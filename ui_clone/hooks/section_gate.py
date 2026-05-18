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


def _get_stale_seconds() -> float:
    """Return stale threshold in seconds. Overridable via UI_RE_STALE_DAYS env var."""
    try:
        days = float(os.environ.get("UI_RE_STALE_DAYS", _DEFAULT_STALE_DAYS))
    except (ValueError, TypeError):
        days = _DEFAULT_STALE_DAYS
    return days * 24 * 3600


def _find_active_markers(search_root: Path) -> list[Path]:
    """Return ref dirs that should engage the Stop hook.

    Two activation paths:
    1. Explicit `.ui-re-active` marker — written by pre_generate.py on the
       first passing pre-generate gate. This is the canonical "I am in a
       ui-re flow" signal.
    2. impl/ alongside tmp/ref/<c>/ even without the explicit marker —
       loop-6 post-mortem: nested agents that skip Phase 5/6 (spec) never
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
    impl_dir = project_root / "impl"
    impl_present = impl_dir.is_dir()
    for d in sorted(search_root.iterdir()):
        if not d.is_dir():
            continue
        if (d / ".ui-re-active").is_file():
            dirs.append(d)
        elif impl_present and any(d.iterdir()):
            # impl/ exists but the canonical marker is missing — implicit
            # activation. Skip empty ref dirs to avoid false positives on
            # totally cold fresh starts.
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


def _enforce_verify_stamp(ref_dir: Path) -> str | None:
    """Block Stop unless pipeline.execute_verify wrote a fresh stamp.

    Codex Q1 (loop-5 post-mortem): the SKILL.md mandate to run
    `pipeline ... verify` was bypassed because the agent invoked
    individual verification scripts directly. This check closes the
    bypass — Stop blocks unless `verify-stamp.json` exists AND is
    newer than _VERIFY_STAMP_MAX_AGE_S.

    Only fires when impl/ exists (post-generation). Pre-generation
    loops are governed by the regular current_gate enforcement.
    """
    # impl/ is resolved relative to cwd because that's what
    # pipeline.execute_verify uses; for the Stop-hook caller cwd
    # may differ from ref_dir's parent (especially in scratch/loop-N
    # validation runs), so we walk up from ref_dir to find it.
    impl_dir = ref_dir.parent.parent.parent / "impl"  # tmp/ref/<c>/.. = tmp/ref/.. = tmp/.. = loop-N/
    if not impl_dir.is_dir():
        return None  # pre-generation — no stamp required yet

    stamp_path = ref_dir / "verify-stamp.json"
    if not stamp_path.is_file():
        return (
            f"⛔ UI-RE Verify-stamp gate: BLOCKED for {ref_dir}\n\n"
            f"impl/ exists at {impl_dir} but no verify-stamp.json. The Stop hook\n"
            f"requires `python -m ui_clone.pipeline ... verify` to have run and\n"
            f"passed before the response can end.\n\n"
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
    if age_s > _VERIFY_STAMP_MAX_AGE_S:
        return (
            f"⛔ UI-RE Verify-stamp gate: STALE stamp for {ref_dir}\n\n"
            f"verify-stamp.json is {int(age_s)}s old (max {_VERIFY_STAMP_MAX_AGE_S}s).\n"
            f"impl/ was likely modified after the last verify. Re-run:\n\n"
            f"  python -m ui_clone.pipeline <url> <component> <session> verify\n"
        )
    return None


def _enforce_ref_dir(ref_dir: Path) -> str | None:
    # Load current gate from pipeline-state.json.
    # If absent, treat as fresh start at "reference" gate (not legacy section-compare fallback).
    state = PipelineState.load(ref_dir)
    current_gate = state.current_gate

    # Verify-stamp short-circuit (loop-9 post-mortem). When impl/ exists,
    # the Stop hook trusts the verify-stamp + the canonical
    # pipeline.execute_verify entry point as the SOLE release decision and
    # does NOT re-run section-compare itself. Re-running the gate inline
    # caused a fire-storm in loop-9 — 440 Stop-hook injections in 1.5 h —
    # because section-compare permanently reports the same critical FAILs
    # on textually-correct clones with substituted fonts / approximated
    # videos / canvas reveals. The gate is still enforced (pipeline verify
    # runs it on every invocation), but it runs ONCE per agent-initiated
    # cycle, not once per Stop event.
    impl_dir = ref_dir.parent.parent.parent / "impl"
    if impl_dir.is_dir():
        return _enforce_verify_stamp(ref_dir)

    if current_gate in {"section-compare", "done"}:
        gate_result = _run_gate(ref_dir, "section-compare")
        if not gate_result.get("passed", True):
            return _section_compare_block_reason(ref_dir, gate_result)
        # Section-compare PASS but no stamp yet — point the agent at the
        # canonical entry instead of releasing silently.
        return _enforce_verify_stamp(ref_dir)

    if current_gate not in GATE_ORDER:
        return _unknown_gate_block_reason(current_gate, ref_dir)

    gate_result = _run_gate(ref_dir, current_gate)
    if not gate_result.get("passed", True):
        return _block_reason_for_gate(current_gate, ref_dir, gate_result)
    # Per-gate PASS does NOT release the Stop hook on its own — verify
    # stamp is the canonical "agent finished cleanly" signal.
    return _enforce_verify_stamp(ref_dir)


def main() -> None:
    project_root = _find_project_root()
    search_root = project_root / "tmp" / "ref"

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
