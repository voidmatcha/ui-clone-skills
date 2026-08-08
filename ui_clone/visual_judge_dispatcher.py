"""Visual-judge multimodal dispatcher with cache + per-key lock.

Background:
  E1 (ui_clone/gates/post_implement.py:_check_bundle_grep_context_inject)
  injects free ref-source snippets after fail count >= 2. When the cheap
  bundle-grep context still doesn't unstick the loop, an *explicit*
  escape-hatch invokes visual-judge.sh — a real multimodal LLM diff
  between ref and impl section screenshots — and caches the result.

  Visual judge dispatcher design review:
    - module placement: single file (not a new `dispatchers/` package)
    - cache key: ref PNG + impl PNG + label + prompt sha + script sha
    - locking: per-key fcntl.flock (mirrors driver_session.py)
    - error contract: VisualJudgeError(returncode, stderr) — not silent None
    - invocation: driver-trigger only (escape-hatch script + goal.py cache-
      read). post_implement.py never dispatches.

Public API:
  dispatch_visual_judge(ref_dir, label, ref_png, impl_png) -> dict
  load_cached(ref_dir, label, ref_png, impl_png) -> dict | None
  VisualJudgeError(returncode, stderr)

The dispatcher itself is side-effect-aware: lock + subprocess + atomic
write. Callers shouldn't invoke visual-judge.sh directly with --out
pointing at the cache path (that bypasses the lock).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ui_clone.shell import bash_bin

__all__ = [
    "VisualJudgeError",
    "dispatch_visual_judge",
    "load_cached",
]


# ── error type ──────────────────────────────────────────────────────


class VisualJudgeError(Exception):
    """Raised when visual-judge.sh exits non-zero, times out, or returns
    invalid JSON. Carries the underlying returncode + stderr so callers
    can render specific diagnostics instead of catching a bare exception.
    """

    def __init__(self, returncode: int, stderr: str, message: str = ""):
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            message or f"visual-judge failed (rc={returncode}): {stderr[:200]}"
        )


# ── cache key derivation ────────────────────────────────────────────


def _short_sha256_file(path: Path) -> str:
    """First 12 hex chars of sha256 over file content. None-safe via empty."""
    if not path.is_file():
        return "missing"
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:12]


def _short_sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _plugin_root() -> Path | None:
    """Locate the plugin root containing skills/visual-debug/."""
    env_root = os.environ.get("PLUGIN_ROOT") or os.environ.get(
        "CLAUDE_PLUGIN_ROOT"
    )
    if env_root:
        cand = Path(env_root)
        if (cand / "skills" / "visual-debug" / "scripts" / "visual-judge.sh").is_file():
            return cand
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "skills" / "visual-debug" / "scripts" / "visual-judge.sh").is_file():
            return parent
    return None


def _visual_judge_script() -> Path | None:
    root = _plugin_root()
    if root is None:
        return None
    return root / "skills" / "visual-debug" / "scripts" / "visual-judge.sh"


def _prompt_template() -> Path | None:
    root = _plugin_root()
    if root is None:
        return None
    return root / "skills" / "visual-debug" / "prompts" / "visual-judge.md"


def _cache_key(ref_png: Path, impl_png: Path, label: str) -> str:
    """Hash 5 components per review item (b):
        ref PNG bytes + impl PNG bytes + label + prompt content + script content.

    Returns a 24-char hex prefix (sufficient for collision-free path naming
    within a single ref_dir; full sha256 is overkill for filenames).
    """
    ref_h = _short_sha256_file(ref_png)
    impl_h = _short_sha256_file(impl_png)
    prompt_path = _prompt_template()
    script_path = _visual_judge_script()
    prompt_h = _short_sha256_file(prompt_path) if prompt_path else "no-prompt"
    script_h = _short_sha256_file(script_path) if script_path else "no-script"
    composite = f"{ref_h}|{impl_h}|{label}|{prompt_h}|{script_h}"
    return hashlib.sha256(composite.encode("utf-8")).hexdigest()[:24]


def _cache_path(ref_dir: Path, key: str) -> Path:
    return ref_dir / "sections" / "visual-judge-cache" / f"{key}.json"


def _lock_path(ref_dir: Path, key: str) -> Path:
    return ref_dir / "sections" / "visual-judge-cache" / f"{key}.lock"


# ── lock helper ─────────────────────────────────────────────────────


@contextmanager
def _per_key_lock(ref_dir: Path, key: str) -> Iterator[None]:
    """Per-cache-key fcntl.flock. Mirrors driver_session.py:register pattern.

    A NEW fd per call so two threads in the same process (the lock-test
    case) each acquire their own fd — fcntl.flock blocks the second on
    LOCK_EX until the first releases. Cross-process semantics are the
    same advisory exclusive lock.
    """
    lock_path = _lock_path(ref_dir, key)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


# ── public API ──────────────────────────────────────────────────────


def load_cached(
    ref_dir: Path, label: str, ref_png: Path, impl_png: Path
) -> dict | None:
    """Return cached findings if present, else None. No lock, no dispatch."""
    key = _cache_key(ref_png, impl_png, label)
    cache_path = _cache_path(ref_dir, key)
    if not cache_path.is_file():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _validate_payload(text: str) -> dict:
    """Parse + validate the visual-judge.sh stdout/cache content. Raises
    VisualJudgeError on invalid JSON so the caller distinguishes from
    other failure modes."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VisualJudgeError(
            returncode=1,
            stderr=f"invalid JSON response: {exc}",
            message="visual-judge response was not valid JSON",
        ) from exc
    if not isinstance(data, dict):
        raise VisualJudgeError(
            returncode=1,
            stderr="visual-judge response must be a JSON object, got "
            + type(data).__name__,
            message="visual-judge response was not a JSON object",
        )
    return data


def _visual_judge_timeout_seconds() -> int:
    """Match visual-judge.sh's VISUAL_JUDGE_TIMEOUT_SEC default of 300s.
    Operators can override via the same env var."""
    try:
        return max(30, int(os.environ.get("VISUAL_JUDGE_TIMEOUT_SEC", "300")))
    except ValueError:
        return 300


def dispatch_visual_judge(
    ref_dir: Path,
    label: str,
    ref_png: Path,
    impl_png: Path,
) -> dict:
    """Run visual-judge.sh (or return cache) for a (ref, impl, label) triple.

    Returns the parsed JSON findings dict on success.
    Raises VisualJudgeError(returncode, stderr) on any failure mode
    (missing script, non-zero exit, invalid JSON, timeout).

    Concurrent calls with the same cache key are serialized by a per-key
    flock — only one subprocess invocation, the rest get cache hits after.
    """
    script = _visual_judge_script()
    if script is None:
        raise VisualJudgeError(
            returncode=-1,
            stderr="visual-judge.sh script not found in plugin root",
            message="cannot locate visual-judge.sh",
        )

    key = _cache_key(ref_png, impl_png, label)
    cache_path = _cache_path(ref_dir, key)
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    # Fast path: cache hit without lock.
    cached = load_cached(ref_dir, label, ref_png, impl_png)
    if cached is not None:
        return cached

    with _per_key_lock(ref_dir, key):
        # Double-check inside the lock — another caller may have populated
        # the cache while we were waiting.
        cached = load_cached(ref_dir, label, ref_png, impl_png)
        if cached is not None:
            return cached

        tmp = cache_path.with_suffix(f".tmp.{os.getpid()}")
        try:
            proc = subprocess.run(
                [
                    bash_bin(),
                    str(script),
                    str(ref_png),
                    str(impl_png),
                    "--out",
                    str(tmp),
                    "--label",
                    label,
                ],
                capture_output=True,
                text=True,
                timeout=_visual_judge_timeout_seconds(),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            tmp.unlink(missing_ok=True)
            raise VisualJudgeError(
                returncode=124,
                stderr=f"timed out after {exc.timeout}s",
                message=f"visual-judge.sh exceeded {exc.timeout}s timeout",
            ) from exc

        if proc.returncode != 0:
            tmp.unlink(missing_ok=True)
            raise VisualJudgeError(
                returncode=proc.returncode,
                stderr=proc.stderr or proc.stdout,
            )

        if not tmp.is_file():
            raise VisualJudgeError(
                returncode=proc.returncode,
                stderr="subprocess returned 0 but did not write --out file",
                message="visual-judge.sh produced no output file",
            )

        try:
            payload = _validate_payload(tmp.read_text(encoding="utf-8"))
        except VisualJudgeError:
            tmp.unlink(missing_ok=True)
            raise

        os.replace(tmp, cache_path)
        return payload


def _cli_main(argv: list[str] | None = None) -> int:
    """Minimal CLI for ad-hoc operator invocation:
        python -m ui_clone.visual_judge_dispatcher <ref-dir> <label> <ref-png> <impl-png>
    Prints the JSON findings (cache or fresh) to stdout. Exit code 0 success,
    non-zero on VisualJudgeError (matches the wrapped script's exit code).
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m ui_clone.visual_judge_dispatcher",
        description="Dispatch visual-judge.sh with cache + per-key lock.",
    )
    parser.add_argument("ref_dir", type=Path)
    parser.add_argument("label")
    parser.add_argument("ref_png", type=Path)
    parser.add_argument("impl_png", type=Path)
    args = parser.parse_args(argv)

    try:
        result = dispatch_visual_judge(
            args.ref_dir, args.label, args.ref_png, args.impl_png
        )
    except VisualJudgeError as exc:
        print(
            f"visual-judge-dispatcher: {exc} (rc={exc.returncode})",
            file=sys.stderr,
        )
        return exc.returncode or 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli_main())
