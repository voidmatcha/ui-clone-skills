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
    """Return list of ref dirs that have a .ui-re-active marker."""
    if not search_root.is_dir():
        return []
    return [
        d for d in sorted(search_root.iterdir()) if d.is_dir() and (d / ".ui-re-active").is_file()
    ]


def _fresh_active_dirs(active_dirs: list[Path]) -> list[Path]:
    fresh_dirs = []
    for ref_dir in active_dirs:
        marker = ref_dir / ".ui-re-active"
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


def _enforce_ref_dir(ref_dir: Path) -> str | None:
    # Load current gate from pipeline-state.json.
    # If absent, treat as fresh start at "reference" gate (not legacy section-compare fallback).
    state = PipelineState.load(ref_dir)
    current_gate = state.current_gate

    if current_gate in {"section-compare", "done"}:
        gate_result = _run_gate(ref_dir, "section-compare")
        if not gate_result.get("passed", True):
            return _section_compare_block_reason(ref_dir, gate_result)
        # Section-compare PASS at current_gate "done" → the goal-card stop
        # condition is satisfied. The Python benchmark harness owns the
        # iter loop and checks STRICT v2 itself; this hook just confirms
        # the gate is genuinely clean for interactive sessions.
        return None

    if current_gate not in GATE_ORDER:
        return _unknown_gate_block_reason(current_gate, ref_dir)

    gate_result = _run_gate(ref_dir, current_gate)
    if not gate_result.get("passed", True):
        return _block_reason_for_gate(current_gate, ref_dir, gate_result)
    return None


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
