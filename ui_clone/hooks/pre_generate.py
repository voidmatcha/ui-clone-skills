"""
Pre-generation check hook for ui-reverse-engineering.
Blocks Write/Edit on component files when extraction pipeline is incomplete.

Usage: python -m ui_clone.hooks.pre_generate
Reads PreToolUse JSON from stdin.
Exit 0 = allow; exit 0 with JSON on stdout = block.

Environment variables:
    UI_RE_COMPONENT_PATHS  — colon-separated list of path substrings to enforce
                             (overrides the built-in defaults)
    Example: UI_RE_COMPONENT_PATHS=/src/components/:/app/components/:/src/pages/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

from ui_clone.hooks._common import find_project_root as _find_project_root
from ui_clone.hooks._common import find_ref_dir as _find_ref_dir
from ui_clone.hooks._common import is_component_file as _is_component_file
from ui_clone.hooks._common import run_gate as _run_gate_common
from ui_clone.state import PipelineState


def _run_gate(ref_dir: Path) -> dict[str, object]:
    return _run_gate_common(ref_dir, "pre-generate")


# ── Emit block JSON ──


def _emit_block(reason: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(payload, ensure_ascii=False))


# ── Main ──


def main() -> None:
    # Read tool input from stdin
    raw_input = sys.stdin.read() if not sys.stdin.isatty() else ""
    file_path = ""
    if raw_input.strip():
        try:
            data = json.loads(raw_input)
            # Support both flat {"file_path": ...} and nested {"tool_input": {"file_path": ...}}
            file_path = data.get("tool_input", {}).get("file_path", "") or data.get("file_path", "")
        except json.JSONDecodeError:
            pass

    # Only enforce on component/page files
    if not _is_component_file(file_path):
        sys.exit(0)

    # Derive project root from the file being edited when possible.
    # This prevents cross-project ref dir pollution (e.g., editing
    # project-a/src/... but hook finds project-b/tmp/ref/).
    project_root = None
    if file_path:
        fp = Path(file_path).resolve()
        # Walk up from file path looking for tmp/ref/
        cur = fp.parent
        while cur != cur.parent:
            if (cur / "tmp" / "ref").is_dir():
                project_root = cur
                break
            cur = cur.parent
    if project_root is None:
        project_root = _find_project_root()

    search_root = project_root / "tmp" / "ref"
    ref_dir = _find_ref_dir(search_root)

    # No ref dir → not a ui-re project
    if ref_dir is None:
        sys.exit(0)

    # The marker is the activation signal for downstream hooks (Stop / Bash /
    # SessionStart / PostCompact). It is *created* on the first passing
    # pre-generate gate run (further down). Until then it doesn't exist — but
    # we still want to run the gate on a component-file edit so the agent
    # gets blocked on missing extraction artifacts. Marker presence is *not*
    # a precondition for blocking; it's a side-effect that activates the
    # rest of the enforcement chain.
    marker = ref_dir / ".ui-re-active"
    state = PipelineState.load(ref_dir)

    # Post-done invalidation only fires when there's an active session
    # (marker exists). Without the marker, no other hook is enforcing, and
    # there's no stale gate state to retract.
    if marker.is_file() and state.current_gate == "done":
        try:
            state.demote_to("section-compare", ref_dir)
            # Move (not delete) result.txt → audit trail of prior PASS state.
            result_file = ref_dir / "sections" / "result.txt"
            if result_file.is_file():
                stale_path = result_file.with_suffix(".txt.stale")
                # If a previous .stale file exists, overwrite — only the most
                # recent stale state is interesting.
                try:
                    if stale_path.exists():
                        stale_path.unlink()
                    result_file.rename(stale_path)
                except OSError:
                    pass
            print(
                "⚑  UI-RE: post-done edit detected — pipeline state demoted to 'section-compare'. "
                "sections/result.txt invalidated. Re-run section-compare before declaring done.",
                file=sys.stderr,
            )
        except OSError:
            pass

    # Run pre-generate gate
    gate_result = _run_gate(ref_dir)

    if not gate_result.get("passed", True):
        failures: list[dict[str, str]] = cast(list[dict[str, str]], gate_result.get("failures", []))
        fail_count = cast(int, gate_result.get("fail_count", len(failures)))
        if failures:
            missing_list = ", ".join(f["label"] for f in failures[:8])
            reason = (
                f"UI Reverse Engineering: extraction incomplete "
                f"({fail_count} artifacts missing). Missing: {missing_list}. "
                f"Complete Phase 2 before writing components."
            )
        else:
            reason = (
                "UI Reverse Engineering: pre-generate gate FAILED. "
                "Run: python -m ui_clone.gate tmp/ref/<component> pre-generate"
            )
        _emit_block(reason)
        sys.exit(0)

    # Gate passed — ensure marker exists. Path.touch() creates the file if
    # absent, refreshes mtime if present. First-time creation here is the
    # documented activation site for the Stop / Bash / SessionStart /
    # PostCompact hooks. Print the activation message only on first creation
    # so subsequent edits don't spam the agent.
    was_new = not marker.is_file()
    try:
        marker.touch()
        if was_new:
            print(
                "⚑  UI-RE Stop gate ACTIVATED: section-compare must pass before finishing.",
                file=sys.stderr,
            )
    except OSError:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
