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
from dataclasses import dataclass
from pathlib import Path

from ui_clone.goal import build_goal_card
from ui_clone.hooks._common import find_project_root, load_json_safe
from ui_clone.hooks._stop_repeat import forget_session
from ui_clone.state import PipelineState

SUBDOC_DIR = "skills/ui-reverse-engineering"


@dataclass(frozen=True)
class SubdocPointer:
    """One targeted section pointer; tests assert `heading` exists in `doc`."""

    doc: str
    heading: str
    when: str
    intersection_only: bool = False


SUBDOC_POINTERS: tuple[SubdocPointer, ...] = (
    SubdocPointer(
        "transition-implementation.md",
        "IntersectionObserver placement for masked reveals",
        "IO+overflow:hidden bug class — most likely failure for intersection entries",
        intersection_only=True,
    ),
    SubdocPointer(
        "diagnosis.md",
        "Stuck-reveal triage flow (3 steps, in order)",
        "Root Cause E — reveal stuck or never fires",
        intersection_only=True,
    ),
    SubdocPointer(
        "diagnosis.md",
        "How to pick the right category",
        "gate or section-compare failure → root-cause class",
    ),
    SubdocPointer(
        "generation-pitfalls.md",
        "CSS-to-React translation — 3 categories",
        "HTML→JSX conversion failures",
    ),
    SubdocPointer(
        "generation-pitfalls.md",
        "Failure-based diagnosis",
        "large symptom table — grep -n your symptom, never read it whole",
    ),
    SubdocPointer(
        "patterns.md",
        "Troubleshooting",
        "failure-table cross-ref",
    ),
)


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
    lines.append(
        f"⚑ UI-RE WIP detected on {event_name}: tmp/ref/{component}/ — re-anchor now "
        "(post-compact/resume is when past runs skipped verification)."
    )
    lines.append(
        "Goal card for this delegated worker "
        f"(refresh: python -m ui_clone.goal tmp/ref/{component}):"
    )
    lines.append(build_goal_card(ref_dir))
    lines.append("")

    # Verification gates — list ALL required gates, mark which are spec-relevant
    lines.append(
        "Before claiming 'done' / 'matched' / 'verified' ($SCRIPTS_DIR = "
        "skills/visual-debug/scripts under VISUAL_DEBUG_SCRIPTS_DIR / PLUGIN_ROOT / "
        "CODEX_PLUGIN_ROOT / CLAUDE_PLUGIN_ROOT):"
    )
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
    lines.append(
        "transition-compare.sh verifies hover/idle diffs only — never evidence "
        "that intersection or scroll-driven entries are 'matched'."
    )
    lines.append("")

    # Targeted section pointers instead of whole-doc reads: the four sub-docs
    # total ~16k words, and re-reading them after every compact dominated the
    # context. What is ENFORCED lives in the goal card and the gates above.
    lines.append(
        f"Before editing component source, read only the matching section of "
        f"{SUBDOC_DIR}/<doc> (`grep -n '^#' <doc>`, then Read with offset/limit):"
    )
    for pointer in SUBDOC_POINTERS:
        if pointer.intersection_only and not has_intersection:
            continue
        lines.append(f"  • {pointer.doc} → '{pointer.heading}' ({pointer.when})")
    if not triggers:
        lines.append(
            "  (transition-spec.json absent or empty — transition sections only "
            "if transitions are in scope)"
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


def _session_id(stdin_text: str) -> str:
    try:
        data = json.loads(stdin_text) if stdin_text.strip() else {}
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    sid = data.get("session_id") or data.get("sessionId")
    return sid.strip() if isinstance(sid, str) else ""


def main() -> None:
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    event_name = _detect_event_name(raw)

    project_root = find_project_root()
    search_root = project_root / "tmp" / "ref"

    # The compact dropped any full Stop-block text from context; make the next
    # Stop show it in full again. Never touches the retry-budget ledger.
    forget_session(project_root, _session_id(raw))

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
