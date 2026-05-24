"""
Stop hook — blocks Claude response based on current pipeline gate.

Reads pipeline-state.json to determine which gate to enforce.
If pipeline-state.json is absent, defaults to reference gate (fresh start).

Activation: only fires when a .ui-re-active marker exists in tmp/ref/*/.

Usage: python -m ui_clone.hooks.section_gate
Outputs {"decision": "block", "reason": "..."} to stdout to block, or exits 0 to allow.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import cast

from ui_clone.goal import build_goal_card
from ui_clone.hooks._common import find_project_root as _find_project_root
from ui_clone.hooks._common import run_gate as _run_gate
from ui_clone.state import GATE_ORDER, PipelineState

_DEFAULT_STALE_DAYS = 3


def _is_driver_session(project_root: Path, session_id_from_payload: str = "") -> bool:
    """Driver-session bypass — release Stop unconditionally when the current
    Claude Code session is registered as a loop driver for this repo.

    Why this exists:
      A maintainer driver session in this repo monitors parallel
      benchmark / sub-workspace sessions (each in their own tab with
      their own session id). When a sub-workspace creates impl/ at
      `<repo>/scratch/<dir>/impl/`, the Stop hook's search_root walk
      finds that ref dir as "active" and fires on the driver's response.
      The driver itself never claims to clone anything, so verify-stamp
      enforcement is a category mismatch.

    Activation:
      1. Marker file <project_root>/.driver-session.id exists, AND
      2. Its content matches the active session id. We check, in order:
           (a) `session_id_from_payload` — the `session_id` field on the
               Claude Code hook JSON stdin payload (canonical),
           (b) `os.environ.get("CLAUDE_CODE_SESSION_ID")` — fallback for
               hosts that propagate session-id via env instead of stdin
               (e.g. direct CLI invocation in tests).

    Production users never create the marker, so the gate works as before.
    Child sessions spawned by the driver have their own session_id; even
    if they could read the marker, no match → gate fires for them as
    expected.

    The marker is local-only state (gitignored). Register via
    `bash scripts/register-driver-session.sh` or
    `python -m ui_clone.driver_session register <id>` — both use the
    append-if-missing writer in `ui_clone.driver_session` which holds
    `fcntl.flock` across the read-modify-write so concurrent driver
    sessions don't stomp each other. Manual `echo > .driver-session.id`
    is single-writer and was the source of the 2026-05-24 multi-driver
    stomping incident.
    """
    marker = project_root / ".driver-session.id"
    if not marker.is_file():
        return False
    try:
        recorded_ids = {
            line.strip()
            for line in marker.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    except OSError:
        return False
    if not recorded_ids:
        return False
    candidates = [
        session_id_from_payload.strip(),
        os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip(),
    ]
    return any(c in recorded_ids for c in candidates if c)


def _get_stale_seconds() -> float:
    """Return stale threshold in seconds. Overridable via UI_RE_STALE_DAYS env var."""
    try:
        days = float(os.environ.get("UI_RE_STALE_DAYS", _DEFAULT_STALE_DAYS))
    except (ValueError, TypeError):
        days = _DEFAULT_STALE_DAYS
    return days * 24 * 3600


def _resolve_impl_dir(ref_dir: Path, fallback_root: Path | None = None) -> Path | None:
    """Resolve the impl/ directory for `ref_dir`, preferring the per-ref-dir
    `impl_root` recorded in pipeline-state.json.

    False-positive cascade closure:
    `ref_dir.parent.parent.parent / "impl"` and `project_root / "impl"` both
    assume one canonical impl/ per project. When a stray `<repo>/impl`
    symlink lives at the repo root (rogue subagent state, a hand-symlink, a
    leftover convention), every prior `tmp/ref/<c>/` false-positives as
    "active" and the verify-stamp gate fires against unrelated months-old
    clones. Now that `PipelineState.impl_root` and the `.impl-root` marker
    are written at Phase 1 start, the resolver can be precise per ref dir.

    Priority order:
    1. UI_CLONE_IMPL_ROOT env (operator override / sub-workspace testing)
    2. `pipeline-state.json` → `impl_root` (set by Phase 1 of the pipeline)
    3. `<ref_dir>/.impl-root` marker file (set alongside the state field)
    4. Fallback to the convention path (`<fallback_root or repo>/impl`)
       — preserves legacy behavior for ref dirs that predate the impl_root
       field; the fallback still produces the same single-impl assumption
       failures as before, but only when the per-ref-dir resolution found
       nothing.

    Returns None when no resolution exists at all, so callers can branch
    on "no impl is wired up to this ref" instead of grabbing a rogue symlink.
    """
    env_root = os.environ.get("UI_CLONE_IMPL_ROOT", "").strip()
    if env_root:
        p = Path(env_root)
        if p.is_dir():
            return p

    try:
        state = PipelineState.load(ref_dir)
        if state.impl_root:
            p = Path(state.impl_root)
            if p.is_dir():
                return p
    except OSError:
        pass

    marker = ref_dir / ".impl-root"
    if marker.is_file():
        try:
            marker_value = marker.read_text(encoding="utf-8").strip()
            if marker_value:
                p = Path(marker_value)
                if p.is_dir():
                    return p
        except OSError:
            pass

    candidates: list[Path] = []
    if fallback_root is not None:
        candidates.append(fallback_root / "impl")
    # ref_dir.parent.parent.parent matches the legacy derivation
    # (tmp/ref/<c>/.. = tmp/ref/.. = tmp/.. = <project-root>).
    try:
        candidates.append(ref_dir.parent.parent.parent / "impl")
    except (IndexError, OSError):
        pass
    # Sub-workspace fallback: an agent may write `<repo>/scratch/<name>/impl/`
    # and `<repo>/tmp/ref/<name>/` as siblings but never set `.impl-root`
    # or `pipeline-state.impl_root`. The legacy `<repo>/impl` fallback
    # doesn't exist in that layout, so the resolver returns None and the
    # Stop hook's verify-stamp gate silently skips this ref dir. The
    # agent then declares success on build/render alone.
    # Heuristic: when ref dir lives at `<repo>/tmp/ref/<name>/`, also
    # try `<repo>/scratch/<name>/impl/`. This recovers the linkage
    # without trusting the agent to write the marker.
    try:
        repo = ref_dir.parent.parent.parent  # tmp/ref/<c>/ → <repo>
        candidates.append(repo / "scratch" / ref_dir.name / "impl")
    except (IndexError, OSError):
        pass
    for cand in candidates:
        if cand.is_dir():
            return cand
    return None


def _find_active_markers(search_root: Path) -> list[Path]:
    """Return ref dirs that should engage the Stop hook.

    Two activation paths:
    1. Explicit `.ui-re-active` marker — written by pre_generate.py on the
       first passing pre-generate gate. This is the canonical "I am in a
       ui-re flow" signal.
    2. impl/ alongside tmp/ref/<c>/ even without the explicit marker —
       audit incident post-mortem: nested agents that skip Phase 5/6 (spec) never
       pass pre-generate, the marker never gets written, and the Stop hook
       used to release silently. Treat the bare presence of impl/ as a
       sufficient signal that an agent is mid-clone, so the verify-stamp
       gate gets a chance to enforce.
    """
    if not search_root.is_dir():
        return []
    dirs: list[Path] = []
    # project_root is the parent of tmp/, which is the parent of search_root.
    project_root = search_root.parent.parent
    for d in sorted(search_root.iterdir()):
        if not d.is_dir():
            continue
        if (d / ".ui-re-active").is_file():
            dirs.append(d)
            continue
        # Implicit activation: impl/ exists but the canonical marker is missing.
        # Loop-61 false-positive fix: resolve impl per ref dir (impl_root /
        # .impl-root marker / env), not from a single repo-root convention.
        # A rogue <project_root>/impl symlink no longer false-positives every
        # prior tmp/ref/<c>/ — only ref dirs whose recorded impl_root exists
        # implicit-activate.
        impl_dir = _resolve_impl_dir(d, fallback_root=project_root)
        if impl_dir is not None and any(d.iterdir()):
            # Skip empty ref dirs to avoid false positives on totally cold
            # fresh starts.
            dirs.append(d)
    return dirs


def _fresh_active_dirs(active_dirs: list[Path]) -> list[Path]:
    fresh_dirs = []
    for ref_dir in active_dirs:
        marker = ref_dir / ".ui-re-active"
        if not marker.is_file():
            # Implicit activation (impl/ present, no marker) — never goes
            # stale because there's nothing to age. The verify-stamp check
            # owns its own freshness window.
            fresh_dirs.append(ref_dir)
            continue
        try:
            age = time.time() - marker.stat().st_mtime
        except OSError:
            continue
        if age >= _get_stale_seconds():
            age_days = int(age // 86400)
            print(
                f"ui-clone-skills: Stale WIP marker ({age_days}d) at {marker} — removing.",
                file=sys.stderr,
            )
            try:
                marker.unlink()
            except OSError:
                pass
            continue
        fresh_dirs.append(ref_dir)
    return fresh_dirs


def _emit_block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


def _block_reason_for_gate(gate_name: str, ref_dir: Path, gate_result: dict[str, object]) -> str:
    failures: list[dict[str, str]] = cast(list[dict[str, str]], gate_result.get("failures", []))
    fail_count = cast(int, gate_result.get("fail_count", len(failures)))
    missing_list = "\n  - ".join(f["label"] for f in failures[:10])
    return (
        f"⛔ UI-RE Gate: {gate_name} BLOCKED\n\n"
        f"Incomplete items ({fail_count}):\n  - {missing_list}\n\n"
        f"Run:\n"
        f"  python -m ui_clone.gate {ref_dir} {gate_name}\n"
        f"  → After passing, run python -m ui_clone.goal {ref_dir} for the next bounded goal\n\n"
        f"{build_goal_card(ref_dir)}"
    )


def _section_compare_block_reason(ref_dir: Path, gate_result: dict[str, object]) -> str:
    failures: list[dict[str, str]] = cast(list[dict[str, str]], gate_result.get("failures", []))
    fail_count = cast(int, gate_result.get("fail_count", len(failures)))
    parts = [f"⛔ UI-RE Gate: section-compare FAILED for {ref_dir} ({fail_count} issue(s))."]
    for f in failures[:5]:
        parts.append(f"  • {f['label']}: {f['reason']}")
        if f.get("fix"):
            parts.append(f"    → {f['fix']}")
    parts.append("\nAll sections must PASS before finishing.")
    parts.append(f"\nRun: python -m ui_clone.goal {ref_dir}")
    parts.append(build_goal_card(ref_dir))
    return "\n".join(parts)


def _unknown_gate_block_reason(current_gate: str, ref_dir: Path) -> str:
    valid_gates = ", ".join([*GATE_ORDER, "done"])
    return (
        f"⛔ UI-RE Gate: unknown current_gate BLOCKED for {ref_dir}\n\n"
        f"pipeline-state.json has unknown current_gate {current_gate!r}.\n"
        f"Valid current_gate values: {valid_gates}.\n\n"
        f"Run:\n"
        f"  python -m ui_clone.goal {ref_dir}\n\n"
        f"{build_goal_card(ref_dir)}"
    )


_VERIFY_STAMP_MAX_AGE_S = 1800  # 30 min — generous so the agent has time to
# finish the response after running verify, but short enough that stale
# stamps from a previous run don't satisfy the gate.
_VERIFY_STAMP_WATCH_EXTS = {
    ".css",
    ".gif",
    ".html",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".jsx",
    ".mp4",
    ".png",
    ".scss",
    ".svg",
    ".ts",
    ".tsx",
    ".webm",
    ".webp",
}
_VERIFY_STAMP_SKIP_DIRS = {
    ".git",
    ".next",
    "build",
    "dist",
    "node_modules",
}


def _newer_impl_files(impl_dir: Path, stamp_path: Path, limit: int = 5) -> list[Path]:
    """Return impl files modified after verify-stamp.json.

    A fresh timestamp alone is not enough: agents can run `pipeline ... verify`,
    then patch JSX/CSS/assets and stop within the 30-minute stamp window. Scan
    the implementation surface and force a new verify when source changed.
    """
    try:
        stamp_mtime = stamp_path.stat().st_mtime
    except OSError:
        return []

    changed: list[Path] = []
    for path in impl_dir.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _VERIFY_STAMP_SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in _VERIFY_STAMP_WATCH_EXTS:
            continue
        try:
            if path.stat().st_mtime > stamp_mtime + 0.001:
                changed.append(path)
        except OSError:
            continue
        if len(changed) >= limit:
            break
    return changed


def _enforce_verify_stamp(ref_dir: Path) -> str | None:
    """Block Stop unless pipeline.execute_verify wrote a fresh stamp.

    Codex Q1 (audit incident post-mortem): the SKILL.md mandate to run
    `pipeline ... verify` was bypassed because the agent invoked
    individual verification scripts directly. This check closes the
    bypass — Stop blocks unless `verify-stamp.json` exists AND is
    newer than _VERIFY_STAMP_MAX_AGE_S.

    Only fires when impl/ exists (post-generation). Pre-generation
    loops are governed by the regular current_gate enforcement.
    """
    # impl/ is resolved via the per-ref-dir impl_root field. The legacy
    # ref_dir.parent.parent.parent / "impl" walk is kept as a fallback
    # inside _resolve_impl_dir for ref dirs that predate the field, but
    # the prior single-impl-per-project assumption no longer false-
    # positives every ref dir when a rogue <repo>/impl symlink exists.
    impl_dir = _resolve_impl_dir(ref_dir)
    if impl_dir is None or not impl_dir.is_dir():
        return None  # pre-generation — no stamp required yet

    stamp_path = ref_dir / "verify-stamp.json"
    if not stamp_path.is_file():
        return (
            f"⛔ UI-RE Verify-stamp gate: BLOCKED for {ref_dir}\n\n"
            f"impl/ exists at {impl_dir} but no verify-stamp.json. The Stop hook\n"
            f"requires `python -m ui_clone.pipeline ... verify` to have run and\n"
            f"passed before the response can end.\n\n"
            f"Build success, HTTP 200/title checks, local render, and visual spot checks\n"
            f"are not completion evidence. Missing artifacts are hard failures, not\n"
            f"substitutes for canonical verification.\n\n"
            f"Fix:\n"
            f"  python -m ui_clone.pipeline <url> <component> <session> verify\n\n"
            f"Verify drives the post-impl gates in GATE_ORDER (post-implement,\n"
            f"boundary, font-parity, section-compare) and stamps {stamp_path.name}\n"
            f"on success.\n"
        )

    try:
        stamp = json.loads(stamp_path.read_text())
        import datetime
        stamped_at = datetime.datetime.strptime(
            stamp["verifiedAt"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=datetime.UTC)
        age_s = (datetime.datetime.now(datetime.UTC) - stamped_at).total_seconds()
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        return (
            f"⛔ UI-RE Verify-stamp gate: malformed stamp {stamp_path}\n\n"
            f"{exc}\n\n"
            f"Re-run `python -m ui_clone.pipeline ... verify` to regenerate.\n"
        )
    required_gates = {"spec", "post-implement", "boundary", "font-parity", "section-compare"}
    stamped_by = stamp.get("stampedBy")
    gates_passed = stamp.get("gatesPassed")
    missing_gates: list[str] = []
    if isinstance(gates_passed, list):
        passed_set = {str(g) for g in gates_passed}
        missing_gates = sorted(required_gates - passed_set)
    else:
        missing_gates = sorted(required_gates)
    if stamped_by != "pipeline.execute_verify" or missing_gates:
        missing = ", ".join(missing_gates) if missing_gates else "none"
        return (
            f"⛔ UI-RE Verify-stamp gate: non-canonical stamp for {ref_dir}\n\n"
            f"verify-stamp.json must be written by pipeline.execute_verify after the\n"
            f"canonical post-implementation gate suite passes. Current stampedBy={stamped_by!r};\n"
            f"missing required gate evidence: {missing}.\n\n"
            f"Build success, HTTP 200/title checks, local render, and visual spot checks\n"
            f"are not completion evidence.\n\n"
            f"Re-run:\n"
            f"  python -m ui_clone.pipeline <url> <component> <session> verify\n"
        )
    if age_s > _VERIFY_STAMP_MAX_AGE_S:
        return (
            f"⛔ UI-RE Verify-stamp gate: STALE stamp for {ref_dir}\n\n"
            f"verify-stamp.json is {int(age_s)}s old (max {_VERIFY_STAMP_MAX_AGE_S}s).\n"
            f"impl/ was likely modified after the last verify. Re-run:\n\n"
            f"  python -m ui_clone.pipeline <url> <component> <session> verify\n"
        )

    changed = _newer_impl_files(impl_dir, stamp_path)
    if changed:
        sample = "\n".join(f"  - {p}" for p in changed)
        return (
            f"⛔ UI-RE Verify-stamp gate: impl changed after verify for {ref_dir}\n\n"
            f"These implementation files are newer than verify-stamp.json:\n"
            f"{sample}\n\n"
            f"Re-run the canonical closeout after the last code/asset edit:\n\n"
            f"  python -m ui_clone.pipeline <url> <component> <session> verify\n"
        )
    return None


def _enforce_structural_convergence_stamp(ref_dir: Path) -> str | None:
    """Block Stop unless check-converged.sh wrote a fresh structural stamp.

    Parallel to _enforce_verify_stamp but for plans that opted into the
    structural closeout policy (codex Task #11 review). The two stamps are
    distinct artifacts with distinct writers — section-staged plans satisfy
    Stop via structural-convergence-stamp.json from scripts/verify/check-
    converged.sh; canonical plans satisfy Stop via verify-stamp.json from
    pipeline.execute_verify. Mixing them is forbidden by closeoutPolicy
    routing in _enforce_ref_dir.

    Same anti-cheat invariants as canonical: fresh stamp, canonical writer,
    impl files no newer than the stamp, AND the underlying sections/result.txt
    must hash-match what was stamped (analogue of impl-freshness for the
    convergence evidence itself).
    """
    impl_dir = _resolve_impl_dir(ref_dir)
    if impl_dir is None or not impl_dir.is_dir():
        return None  # pre-generation — no stamp required yet

    stamp_path = ref_dir / "structural-convergence-stamp.json"
    if not stamp_path.is_file():
        return (
            f"⛔ UI-RE Structural-stamp gate: BLOCKED for {ref_dir}\n\n"
            f"closeoutPolicy=structural but no structural-convergence-stamp.json.\n"
            f"This run satisfies Stop via the convergence detector's stamp; it\n"
            f"is written on 0-FAIL by:\n\n"
            f"  bash scripts/verify/check-converged.sh {ref_dir} --write-stamp [--stage <A|B|C|D>]\n\n"
            f"Convergence definition: sections/result.txt's last `**Result: ...**` line\n"
            f"shows 0 FAIL (STRUCTURAL_ONLY counted as PASS, SKIP doesn't gate).\n"
        )

    try:
        stamp = json.loads(stamp_path.read_text())
        import datetime
        stamped_at = datetime.datetime.strptime(
            stamp["verifiedAt"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=datetime.UTC)
        age_s = (datetime.datetime.now(datetime.UTC) - stamped_at).total_seconds()
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        return (
            f"⛔ UI-RE Structural-stamp gate: malformed stamp {stamp_path}\n\n"
            f"{exc}\n\n"
            f"Re-run `bash scripts/verify/check-converged.sh {ref_dir} --write-stamp`.\n"
        )

    stamped_by = stamp.get("stampedBy")
    closeout_kind = stamp.get("closeoutKind")
    if stamped_by != "scripts/verify/check-converged.sh" or closeout_kind != "structural":
        return (
            f"⛔ UI-RE Structural-stamp gate: non-canonical stamp for {ref_dir}\n\n"
            f"structural-convergence-stamp.json must be written by\n"
            f"scripts/verify/check-converged.sh with closeoutKind=structural.\n"
            f"Current stampedBy={stamped_by!r}, closeoutKind={closeout_kind!r}.\n\n"
            f"Build success, HTTP 200/title checks, and hand-written stamps are\n"
            f"not completion evidence — only the convergence detector's stamp\n"
            f"counts.\n\n"
            f"Re-run:\n"
            f"  bash scripts/verify/check-converged.sh {ref_dir} --write-stamp\n"
        )

    if age_s > _VERIFY_STAMP_MAX_AGE_S:
        return (
            f"⛔ UI-RE Structural-stamp gate: STALE stamp for {ref_dir}\n\n"
            f"structural-convergence-stamp.json is {int(age_s)}s old "
            f"(max {_VERIFY_STAMP_MAX_AGE_S}s).\n"
            f"Re-run:\n\n"
            f"  bash scripts/verify/check-converged.sh {ref_dir} --write-stamp\n"
        )

    # Re-validate the sections/result.txt hash — detects the cheat of stamping
    # while converged then editing result.txt to claim more PASS rows.
    result_file = ref_dir / "sections" / "result.txt"
    stamped_sha = stamp.get("sectionsResultSha256")
    if stamped_sha and result_file.is_file():
        import hashlib
        current_sha = hashlib.sha256(result_file.read_bytes()).hexdigest()
        if current_sha != stamped_sha:
            return (
                f"⛔ UI-RE Structural-stamp gate: result.txt tampered after stamp for {ref_dir}\n\n"
                f"sections/result.txt sha256 mismatch: stamp recorded {stamped_sha[:12]}…\n"
                f"but the file now hashes to {current_sha[:12]}…\n\n"
                f"The convergence evidence the stamp attests to has changed.\n"
                f"Re-run the convergence detector:\n\n"
                f"  bash scripts/verify/check-converged.sh {ref_dir} --write-stamp\n"
            )

    changed = _newer_impl_files(impl_dir, stamp_path)
    if changed:
        sample = "\n".join(f"  - {p}" for p in changed)
        return (
            f"⛔ UI-RE Structural-stamp gate: impl changed after stamp for {ref_dir}\n\n"
            f"These implementation files are newer than structural-convergence-stamp.json:\n"
            f"{sample}\n\n"
            f"Re-converge after the last code/asset edit:\n\n"
            f"  bash scripts/verify/check-converged.sh {ref_dir} --write-stamp\n"
        )
    return None


def _enforce_ref_dir(ref_dir: Path) -> str | None:
    # Load current gate from pipeline-state.json.
    # If absent, treat as fresh start at "reference" gate (not legacy section-compare fallback).
    state = PipelineState.load(ref_dir)
    current_gate = state.current_gate

    # Unclonable short-circuit (Common cheat pattern): when pipeline-state.json
    # records unclonable_reasons (paid font with no substitution, DRM canvas,
    # auth-gated content, or — per ui_clone.goal abort_banner — a hard-cap
    # gate-fail count), release Stop instead of re-enforcing the gate that
    # produced the abort. Without this, Stop fires every turn while
    # `python -m ui_clone.goal --check-done` returns exit 2 — the two signals
    # disagree and external loops can't terminate cleanly. The orchestrator
    # halts loop spawning on --check-done exit 2; this hook stops firing
    # blocking-reason text symmetrically.
    if state.unclonable_reasons:
        # Loop-23 slip path: synthetic / forensic markers (any gate name
        # NOT in GATE_ORDER) preserved from a prior loop must NOT
        # release Stop on a fresh state (`completed_steps == []`).
        # Codex-23 inherited such a marker (`gate="session-cleanup"`)
        # and exited "done" without exercising a single gate. Real hard
        # blockers (hard-cap fail-count, paid-font, DRM canvas,
        # auth-gated) still release — they carry a canonical gate name
        # produced by a gate that DID run. Filtering by `gate in
        # GATE_ORDER` keeps this robust against codex renaming the
        # synthetic marker (e.g. "session-cleanup" → "forensic-preserve").
        canonical_reasons = [
            r for r in state.unclonable_reasons
            if r.get("gate") in GATE_ORDER
        ]
        if canonical_reasons or state.completed_steps:
            return None

    # Closeout policy routing (Task #11): structural plans (section-staged
    # convergence loops) satisfy Stop via structural-convergence-stamp.json
    # from check-converged.sh. Canonical plans (default) satisfy Stop via
    # verify-stamp.json from pipeline.execute_verify. The two stamps are
    # never interchangeable — the policy decides which writer counts.
    if state.closeout_policy == "structural":
        stamp_enforcer = _enforce_structural_convergence_stamp
    else:
        stamp_enforcer = _enforce_verify_stamp

    impl_dir = _resolve_impl_dir(ref_dir)
    if impl_dir is not None and impl_dir.is_dir():
        return stamp_enforcer(ref_dir)

    if current_gate in {"section-compare", "done"}:
        gate_result = _run_gate(ref_dir, "section-compare")
        if not gate_result.get("passed", True):
            return _section_compare_block_reason(ref_dir, gate_result)
        # Section-compare PASS but no stamp yet — point the agent at the
        # canonical entry instead of releasing silently.
        return stamp_enforcer(ref_dir)

    if current_gate not in GATE_ORDER:
        return _unknown_gate_block_reason(current_gate, ref_dir)

    gate_result = _run_gate(ref_dir, current_gate)
    if not gate_result.get("passed", True):
        return _block_reason_for_gate(current_gate, ref_dir, gate_result)
    # Per-gate PASS does NOT release the Stop hook on its own — verify
    # stamp is the canonical "agent finished cleanly" signal.
    return stamp_enforcer(ref_dir)


def main() -> None:
    project_root = _find_project_root()
    search_root = project_root / "tmp" / "ref"

    # Claude Code Stop hooks receive a JSON payload on stdin with
    # `session_id`, `hook_event_name`, etc. We read it (best-effort, never
    # block on parse error) to extract the active session id for the
    # driver-session bypass check.
    session_id_from_payload = ""
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw.strip():
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    sid = payload.get("session_id")
                    if isinstance(sid, str):
                        session_id_from_payload = sid
        except (json.JSONDecodeError, OSError):
            pass

    # Driver-session bypass — maintainer running parallel loops as an
    # observer/orchestrator, not a clone agent. Production users never
    # write the marker, so this is a no-op for them.
    if _is_driver_session(project_root, session_id_from_payload):
        sys.exit(0)

    active_dirs = _fresh_active_dirs(_find_active_markers(search_root))
    if not active_dirs:
        sys.exit(0)

    if len(active_dirs) > 1:
        print(
            f"ui-clone-skills: WARNING: {len(active_dirs)} concurrent WIP markers. Enforcing all.",
            file=sys.stderr,
        )

    for ref_dir in active_dirs:
        block_reason = _enforce_ref_dir(ref_dir)
        if block_reason:
            _emit_block(block_reason)
            sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
