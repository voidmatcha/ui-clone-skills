"""
SessionStart + PostCompact reinjection hook.

Why this hook exists
────────────────────
JSONL analysis of an actual UI-cloning session (89a64..., 30 compact_boundary
events across ~31 wallclock hours) showed:

  - Post-compact verification-skip rate: 73.3% of all verification skips
  - Post-compact sub-doc-skip rate:     60.4% of all sub-doc skips
  - Early-session verification-skip rate: 0%
  - Early-session sub-doc-skip rate:     4.4%

i.e., the dominant failure mode is "agent forgets the verification checklist
after context compaction and never re-reads it." Once a session segment passes
its first compact without a sub-doc read, it never recovers (Segments 1-6 of
89a64: 17 hours, 227 edits, 0 sub-doc reads).

This hook runs on SessionStart and PostCompact. When an active WIP marker
(`tmp/ref/<c>/.ui-re-active`) exists, it injects a compact verification
checklist into the model's context via hookSpecificOutput.additionalContext —
giving the agent the gate names, sub-doc names, and an explicit warning that
post-compact skips are the empirically-dominant failure class on this codebase.

If no active WIP marker exists, the hook silently exits 0.

Usage:
    python -m ui_clone.hooks.session_resume

Input: JSON on stdin (the SessionStart/PostCompact payload — most fields
unused; we only care about whether to inject).
Output: JSON on stdout with hookSpecificOutput.additionalContext if a WIP
ref exists; nothing otherwise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ui_clone.goal import build_goal_card
from ui_clone.hooks._common import find_project_root, load_json_safe
from ui_clone.state import PipelineState


def _find_active_refs(search_root: Path) -> list[Path]:
    """Return ref dirs with a .ui-re-active marker that aren't already 'done'.

    Marker stays after section-compare passes (so pre_generate can detect
    post-done edits and demote state). But while state is 'done', there's
    nothing to nag about — skip injection to avoid spamming completed
    projects with the verification checklist on every session resume.
    """
    if not search_root.is_dir():
        return []
    out: list[Path] = []
    for d in sorted(search_root.iterdir()):
        if not d.is_dir():
            continue
        if not (d / ".ui-re-active").is_file():
            continue
        state = PipelineState.load(d)
        if state.current_gate == "done":
            continue
        out.append(d)
    return out


def _trigger_types_in_spec(ref_dir: Path) -> set[str]:
    """Read transition-spec.json and return the set of trigger/type values present.

    Used to scope the "required sub-docs" reminder so we don't tell the agent to
    read transition-implementation.md if there are no transitions in the spec.
    """
    spec = load_json_safe(ref_dir / "transition-spec.json")
    if not spec:
        return set()
    entries = spec.get("transitions") or spec.get("entries") or []
    if not isinstance(entries, list):
        return set()
    triggers: set[str] = set()
    for e in entries:
        if not isinstance(e, dict):
            continue
        t = e.get("trigger") or e.get("type")
        if isinstance(t, str) and t:
            triggers.add(t)
        anim = e.get("animation")
        if isinstance(anim, dict):
            at = anim.get("type")
            if isinstance(at, str) and at:
                triggers.add(at)
    return triggers


def _build_message(ref_dir: Path, event_name: str) -> str:
    """Build the additionalContext string for one active ref."""
    component = ref_dir.name
    triggers = _trigger_types_in_spec(ref_dir)

    has_intersection = any(
        t in triggers
        for t in ("intersection", "inview", "intersection-fade-up", "fade-up", "reveal-rise")
    )
    has_scroll = any(
        t in triggers
        for t in ("scroll", "scroll-driven", "scroll-driven-scale", "scroll-scale", "scroll-parallax")
    )
    has_hover = any(t in triggers for t in ("hover", "css-hover", "mouseenter"))

    lines: list[str] = []
    lines.append(f"⚑ UI-RE WIP detected on {event_name}: tmp/ref/{component}/")
    lines.append("")
    lines.append("Host-neutral goal card for this delegated worker:")
    lines.append(f"Run: python -m ui_clone.goal tmp/ref/{component}")
    lines.append(build_goal_card(ref_dir))
    lines.append("")
    lines.append(
        "Empirical pattern (from JSONL analysis of prior sessions): post-compact and "
        "session-resume are the dominant skip-failure trigger — 73% of past "
        "verification-skip incidents happened within 20 min of a compact_boundary. "
        "Once a session segment passes a compact without re-reading the sub-docs, "
        "the skip pattern persists for the rest of the segment. This reminder is "
        "your re-anchor."
    )
    lines.append("")

    # Verification gates — list ALL required gates, mark which are spec-relevant
    lines.append("Before claiming this clone is 'done' / 'matched' / 'verified':")
    lines.append(f"  1. python -m ui_clone.gate tmp/ref/{component} post-implement")
    lines.append(
        f"  2. bash $SCRIPTS_DIR/section-compare.sh <orig-url> <impl-url> <session> tmp/ref/{component}"
    )
    if has_intersection or has_scroll or has_hover or not triggers:
        lines.append(
            f"  3. bash $SCRIPTS_DIR/transition-spec-coverage.sh tmp/ref/{component} <impl-src-dir>"
        )
    if has_intersection or not triggers:
        lines.append(
            "  4. bash $SCRIPTS_DIR/reveal-trigger-check.sh <session> <impl-url>"
            "      ← intersection/fade-up entries detected in spec — runtime gate REQUIRED"
            if has_intersection
            else "  4. bash $SCRIPTS_DIR/reveal-trigger-check.sh <session> <impl-url>"
        )
    if has_hover or not triggers:
        lines.append("  5. bash $SCRIPTS_DIR/transition-compare.sh <orig> <impl> <session>")
    lines.append("")
    lines.append(
        "$SCRIPTS_DIR resolves from VISUAL_DEBUG_SCRIPTS_DIR, PLUGIN_ROOT, "
        "CODEX_PLUGIN_ROOT, or CLAUDE_PLUGIN_ROOT. If none is set, export "
        "SCRIPTS_DIR=/path/to/ui-clone-skills/skills/visual-debug/scripts."
    )
    lines.append("")

    # Sub-doc reading reminder
    lines.append("Required sub-docs to read BEFORE editing component source under apps/*/src/projects/:")
    if has_intersection:
        lines.append(
            "  • transition-implementation.md → 'IntersectionObserver placement for masked reveals' "
            "(IO+overflow:hidden bug class — most likely failure mode for intersection entries)"
        )
        lines.append("  • diagnosis.md → Root Cause E + Stuck-reveal triage flow")
    lines.append("  • patterns.md (failure-table cross-ref)")
    lines.append("  • generation-pitfalls.md (HTML→JSX conversion failures)")
    if not triggers:
        lines.append(
            "  (transition-spec.json absent or empty — read these only if transitions are in scope)"
        )
    lines.append("")

    lines.append(
        "Do NOT declare a transition category 'matched' after running only "
        "transition-compare.sh — that script verifies hover/idle diffs only. "
        "Intersection-fade-up and scroll-driven entries pass through it as "
        "noise and are NOT verified."
    )

    return "\n".join(lines)


def _emit(message: str, event_name: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": message,
        }
    }
    print(json.dumps(payload, ensure_ascii=False))


def _detect_event_name(stdin_text: str) -> str:
    """Best-effort detection of which event fired this hook.

    Both SessionStart and PostCompact route through the same module. We inspect
    the input JSON for known marker fields. Defaults to "SessionStart" when the
    payload doesn't reveal which event fired (the choice is cosmetic — the
    additionalContext content is identical for both).
    """
    if not stdin_text.strip():
        return "SessionStart"
    try:
        data = json.loads(stdin_text)
    except json.JSONDecodeError:
        return "SessionStart"
    if not isinstance(data, dict):
        return "SessionStart"
    # PostCompact payloads include a "summary" or "trigger" ("manual"/"auto") field.
    if "summary" in data or data.get("trigger") in ("manual", "auto"):
        return "PostCompact"
    if data.get("hook_event_name") == "PostCompact":
        return "PostCompact"
    return "SessionStart"


def main() -> None:
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    event_name = _detect_event_name(raw)

    project_root = find_project_root()
    search_root = project_root / "tmp" / "ref"

    active_refs = _find_active_refs(search_root)
    if not active_refs:
        sys.exit(0)

    # Build context for each active ref. In practice there's almost always
    # exactly one; we still iterate to handle the rare multi-WIP case.
    if len(active_refs) == 1:
        message = _build_message(active_refs[0], event_name)
    else:
        parts = [
            f"⚑ UI-RE WIP detected on {event_name}: {len(active_refs)} active refs.",
            "",
        ]
        for r in active_refs:
            parts.append(_build_message(r, event_name))
            parts.append("─" * 40)
        message = "\n".join(parts).rstrip("─\n")

    _emit(message, event_name)
    sys.exit(0)


if __name__ == "__main__":
    main()
