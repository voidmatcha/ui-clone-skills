"""
Shared utilities for ui_clone hook modules.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# ── ANSI colors (shared across pipeline/gate/hooks) ──

GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
BOLD = "\033[1m"
NC = "\033[0m"


def _plugin_root() -> Path:
    """Return the ui-clone-skills plugin root (the directory containing pyproject.toml).

    Priority:
    1. Plugin-root env vars set by agent runtimes (generic host, Codex, Claude Code)
    2. Walk up from this file's location looking for pyproject.toml
    """
    for env_name in ("PLUGIN_ROOT", "CODEX_PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"):
        env_root = os.environ.get(env_name, "")
        if env_root and (Path(env_root) / "pyproject.toml").is_file():
            return Path(env_root)
    cur = Path(__file__).resolve()
    while cur != cur.parent:
        if (cur / "pyproject.toml").is_file():
            return cur
        cur = cur.parent
    raise FileNotFoundError(
        "Cannot find ui-clone-skills plugin root. "
        "Set PLUGIN_ROOT, CODEX_PLUGIN_ROOT, or CLAUDE_PLUGIN_ROOT, or run from within the plugin directory."
    )


_cached_project_root: Path | None = None


def find_project_root() -> Path:
    """Discover project root.

    Priority:
    1. $CLAUDE_PROJECT_DIR env var
    2. git rev-parse --show-toplevel (cached per process to avoid repeated subprocess calls)
    3. Walk up from cwd looking for tmp/ref/
    4. cwd fallback
    """
    global _cached_project_root

    env_root = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if env_root and Path(env_root).is_dir():
        return Path(env_root)

    if _cached_project_root is not None:
        return _cached_project_root

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            git_root = Path(result.stdout.strip())
            # Verify this git root actually contains tmp/ref/ — guards nested-repo
            # setups where the monorepo parent is the git root, not the project dir.
            if (git_root / "tmp" / "ref").is_dir():
                _cached_project_root = git_root
                return git_root
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    cwd = Path.cwd()
    cur = cwd
    while cur != cur.parent:
        if (cur / "tmp" / "ref").is_dir():
            _cached_project_root = cur
            return cur
        cur = cur.parent

    _cached_project_root = cwd
    return cwd


def find_ref_dir(search_root: Path) -> Path | None:
    """Find ref dir: prefer WIP marker, fall back to newest extracted.json mtime."""
    if not search_root.is_dir():
        return None

    # 1. WIP marker
    for d in sorted(search_root.iterdir()):
        if not d.is_dir():
            continue
        if (d / ".ui-re-active").is_file():
            return d

    # 2. mtime fallback — only refs with extracted.json
    newest_time = 0.0
    newest_dir: Path | None = None
    for d in sorted(search_root.iterdir()):
        if not d.is_dir():
            continue
        extracted = d / "extracted.json"
        if not extracted.is_file():
            continue
        mtime = extracted.stat().st_mtime
        if mtime > newest_time:
            newest_time = mtime
            newest_dir = d

    return newest_dir


def load_json_safe(path: Path) -> dict[str, Any] | None:
    """Load a JSON file and return it as a dict. Returns None if missing, malformed, or not an object."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data


_DEFAULT_COMPONENT_SUBSTRINGS = ("/src/components/", "/src/projects/")
_DEFAULT_APP_PREFIX = "/src/app/"


def is_component_file(file_path: str) -> bool:
    """Return True for component/page files that pre-generate / pre-bash should enforce.

    Default enforced paths:
    - /src/components/**       — all component files
    - /src/projects/**         — project-scoped component trees (monorepo layouts)
    - /src/app/**/page.*       — Next.js App Router page files only
                                 (layout.tsx, route.ts etc. are excluded)

    Override via UI_RE_COMPONENT_PATHS env var (colon-separated substrings):
        UI_RE_COMPONENT_PATHS=/src/components/:/app/components/
    """
    if not file_path:
        return False
    custom = os.environ.get("UI_RE_COMPONENT_PATHS", "").strip()
    if custom:
        return any(p in file_path for p in custom.split(":") if p)
    if any(sub in file_path for sub in _DEFAULT_COMPONENT_SUBSTRINGS):
        return True
    if _DEFAULT_APP_PREFIX in file_path:
        return any(seg.startswith("page.") for seg in file_path.split("/"))
    return False


def _log_gate_skip(ref_dir: Path, gate_name: str, reason: str) -> None:
    """Append a gate skip event to ref_dir/.gate-skip-log for auditability.

    Best-effort — never raises.
    """
    try:
        from datetime import UTC, datetime

        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        log_path = ref_dir / ".gate-skip-log"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts} gate={gate_name} reason={reason}\n")
    except OSError:
        pass


def run_gate(ref_dir: Path, gate_name: str) -> dict[str, object]:
    """Run `uv run python -m ui_clone.gate <ref_dir> <gate_name> --json` as a subprocess.

    Uses `uv run` to guarantee execution inside the ui-clone-skills virtual environment
    (with scikit-image, Pillow installed). Falls back to sys.executable if uv is
    not available, which will fail-open with a warning if dependencies are missing.

    Returns parsed JSON dict from gate output.
    Falls back to {"passed": True} if gate script not found (fail-open).
    Gate skips are logged to ref_dir/.gate-skip-log for auditability.
    """
    uv = shutil.which("uv")
    if uv:
        cmd = [
            uv,
            "run",
            "--project",
            str(_plugin_root()),
            "python",
            "-m",
            "ui_clone.gate",
            str(ref_dir),
            gate_name,
            "--json",
        ]
    else:
        print(
            "ui-clone-skills: WARNING: uv not found, falling back to sys.executable",
            file=sys.stderr,
        )
        cmd = [sys.executable, "-m", "ui_clone.gate", str(ref_dir), gate_name, "--json"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        raw = result.stdout.strip()
        if raw:
            data: dict[str, object] = json.loads(raw)
            return data
        if result.returncode != 0:
            return {
                "passed": False,
                "fail_count": 1,
                "failures": [
                    {
                        "label": gate_name,
                        "reason": result.stderr.strip() or "gate failed",
                        "fix": "",
                    }
                ],
            }
    except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        reason = f"{type(exc).__name__}: {exc}"
        print(f"ui-clone-skills: WARNING: gate not runnable: {exc}", file=sys.stderr)
        _log_gate_skip(ref_dir, gate_name, reason)
    return {"passed": True, "fail_count": 0, "failures": []}
