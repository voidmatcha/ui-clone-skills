"""Host-neutral goal card for continuing a UI clone run."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from ui_clone.state import GATE_ORDER, PipelineState


@dataclass(frozen=True)
class GoalStep:
    current_goal: str
    next_action: str
    required_evidence: str


@dataclass(frozen=True)
class GoalCard:
    component: str
    current_gate: str
    current_goal: str
    next_action: str
    stop_condition: str
    required_evidence: str
    manual_refresh: str
    no_infinite_loop: str
    stop_evidence_status: str | None = None
    # Surfaced banners — non-empty strings render at the top of the text
    # rendering and as JSON fields. Drive external loop drivers to abort
    # (abort_banner) or enter diagnosis mode (stuck_banner).
    stuck_banner: str | None = None
    abort_banner: str | None = None

    def to_json(self) -> dict[str, object]:
        data: dict[str, object] = {
            "component": self.component,
            "current_gate": self.current_gate,
            "current_goal": self.current_goal,
            "next_action": self.next_action,
            "stop_condition": self.stop_condition,
            "required_evidence": self.required_evidence,
            "manual_refresh": self.manual_refresh,
            "no_infinite_loop": self.no_infinite_loop,
        }
        if self.stop_evidence_status is not None:
            data["stop_evidence_status"] = self.stop_evidence_status
        if self.stuck_banner is not None:
            data["stuck_banner"] = self.stuck_banner
        if self.abort_banner is not None:
            data["abort_banner"] = self.abort_banner
        return data


_STOP_CONDITION = 'current_gate == "done" and section comparison has no FAIL / MISSING impl lines.'
_NO_INFINITE_LOOP = (
    "this card describes one bounded next action; "
    "host continuation must stop at the stop condition."
)
# Threshold for the stuck banner. 3 consecutive fails of the same active gate
# is the empirical "you are grinding, not progressing" boundary — fewer is
# noise (one retry after a transient browser glitch), more wastes iterations.
_STUCK_THRESHOLD = 3


_GOAL_BY_GATE: dict[str, GoalStep] = {
    "reference": GoalStep(
        current_goal="Capture reference evidence",
        next_action="Run /ui-capture for the target URL, then run python -m ui_clone.gate <ref-dir> reference",
        required_evidence="static/ref/ >=5 PNGs, transitions/ref/ >=1 file, regions.json",
    ),
    "extraction": GoalStep(
        current_goal="Extract DOM, assets, styles, and body state",
        next_action="Run the extraction steps, then run python -m ui_clone.gate <ref-dir> extraction",
        required_evidence="structure.json, head.json, styles.json, fonts.json, visible-images.json, inline-svgs.json, body-state.json, css/variables.txt",
    ),
    "bundle": GoalStep(
        current_goal="Analyze JS bundles and interaction runtime",
        next_action="Run Step 5c-a bundle analysis, then run python -m ui_clone.gate <ref-dir> bundle",
        required_evidence="bundles/, interactions-detected.json, scroll-engine.json",
    ),
    "paid-features": GoalStep(
        current_goal="Decide paid font feature handling",
        next_action="Run paid feature detection, then run python -m ui_clone.gate <ref-dir> paid-features",
        required_evidence="paid-features.json with each paid font decision set to use, substitute, or skip",
    ),
    "spec": GoalStep(
        current_goal="Finalize transition spec and verification plan",
        next_action="Run Step 5d spec production, then run python -m ui_clone.gate <ref-dir> spec",
        required_evidence="bundle-map.json, external-sdks.json, transition-spec.json, verification-plan.json, verify/ >=5 frames",
    ),
    "pre-generate": GoalStep(
        current_goal="Resolve pre-generation readiness",
        next_action="Finish required audit artifacts, then run python -m ui_clone.gate <ref-dir> pre-generate",
        required_evidence="extracted.json, transition-coverage.json, section-map.json, hover timing, dom-state-diff.json when needed, component-map",
    ),
    "post-implement": GoalStep(
        current_goal="Verify the implemented component against required artifacts",
        next_action="Run python -m ui_clone.gate <ref-dir> post-implement before visual comparison",
        required_evidence="extracted.json, transition-spec.json, static/ref/ >=5 PNGs",
    ),
    "boundary": GoalStep(
        current_goal="Prove responsive breakpoint boundaries are clean",
        next_action="Run breakpoint collision check, then run python -m ui_clone.gate <ref-dir> boundary",
        required_evidence="responsive/boundary-collisions.json must be []",
    ),
    "font-parity": GoalStep(
        current_goal="Prove font parity or document substitutions",
        next_action="Run font parity check, then run python -m ui_clone.gate <ref-dir> font-parity",
        required_evidence="font-parity.json with parity match, or asset-substitution.json fonts[] when mismatched",
    ),
    "section-compare": GoalStep(
        current_goal="Prove every captured section matches",
        next_action="Run bash skills/visual-debug/scripts/section-compare.sh <orig-url> <impl-url> <session> <ref-dir>",
        required_evidence="sections/result.txt with 0 FAIL lines and 0 MISSING impl lines",
    ),
    "done": GoalStep(
        current_goal="Stop",
        next_action="Do not continue automatically; report the verified evidence to the host/user",
        required_evidence='sections/result.txt and current_gate == "done"',
    ),
}


def _section_compare_status(ref_dir: Path) -> str:
    result = ref_dir / "sections" / "result.txt"
    if not result.is_file():
        return "not satisfied: sections/result.txt is missing"
    text = result.read_text(encoding="utf-8", errors="replace")
    # Only count actual FAIL rows in the section-compare table. Lines like
    # "**Result: 2 PASS, 0 FAIL, 0 SKIP, 2 STRUCTURAL_ONLY**" contain "FAIL"
    # as a literal substring and must NOT trigger the failure count — they're
    # the summary footer, not a failing section row. Failed rows are markdown
    # table rows starting with "|" that contain "❌" in their status cell.
    fail_count = sum(
        1
        for line in text.splitlines()
        if line.startswith("|") and "❌" in line
    )
    missing_count = sum(1 for line in text.splitlines() if "MISSING impl" in line)
    if fail_count == 0 and missing_count == 0:
        return "satisfied: sections/result.txt has 0 FAIL lines and 0 MISSING impl lines"
    return f"not satisfied: sections/result.txt has {fail_count} FAIL line(s) and {missing_count} MISSING impl line(s)"


def build_goal_card_data(ref_dir: Path) -> GoalCard:
    """Return deterministic, host-neutral goal card data for a ref directory."""
    state = PipelineState.load(ref_dir)
    gate = state.current_gate
    step = _GOAL_BY_GATE.get(gate)
    if step is None:
        valid_gates = ", ".join([*GATE_ORDER, "done"])
        step = GoalStep(
            current_goal="Resolve invalid pipeline state",
            next_action=f"Inspect {ref_dir / 'pipeline-state.json'} and set current_gate to a valid value before continuing",
            required_evidence=f"pipeline-state.json has a valid current_gate ({valid_gates})",
        )
    command_ref = str(ref_dir)
    next_action = step.next_action.replace("<ref-dir>", command_ref)
    evidence = step.required_evidence.replace("<ref-dir>", command_ref)

    # Stuck banner: bumped by gate.py CLI on consecutive BLOCKED runs of the
    # active gate. Once we cross the threshold, the worker is grinding the same
    # failure — route it into diagnosis primitives before another iteration.
    stuck_banner: str | None = None
    fail_count = state.gate_fail_counts.get(gate, 0)
    if fail_count >= _STUCK_THRESHOLD:
        stuck_banner = (
            f"STUCK: gate '{gate}' has failed {fail_count} consecutive runs. "
            "Read skills/ui-reverse-engineering/diagnosis.md (root-cause routing), "
            "patterns.md (failure-table cross-ref), and skills/visual-debug/SKILL.md "
            "(AE/SSIM mismatch triage) BEFORE the next iteration. Do not retry "
            "the same action — find the upstream cause first."
        )

    # Abort banner: hard-blocker reasons recorded by gates/scripts when they
    # detect a condition the pipeline cannot resolve (paid font with no
    # substitution, DRM canvas, auth-gated content, etc.).
    abort_banner: str | None = None
    if state.unclonable_reasons:
        summary = "; ".join(
            f"[{r.get('gate', '?')}] {r.get('reason', '?')}"
            for r in state.unclonable_reasons
        )
        abort_banner = (
            f"ABORT: this site cannot be cloned as-is. Reasons: {summary}. "
            "External loops should stop iterating. Resolve the underlying "
            "constraint (license, auth, content gating) or report unclonable."
        )

    return GoalCard(
        component=state.component or ref_dir.name,
        current_gate=gate,
        current_goal=step.current_goal,
        next_action=next_action,
        stop_condition=_STOP_CONDITION,
        required_evidence=evidence,
        manual_refresh=f"python -m ui_clone.goal {command_ref}",
        no_infinite_loop=_NO_INFINITE_LOOP,
        stop_evidence_status=_section_compare_status(ref_dir) if gate == "done" else None,
        stuck_banner=stuck_banner,
        abort_banner=abort_banner,
    )


def build_goal_card(ref_dir: Path) -> str:
    """Return a deterministic, host-neutral goal card for a ref directory."""
    card = build_goal_card_data(ref_dir)

    lines = [f"Goal Card: {card.component}"]
    # Abort takes precedence — if the site is unclonable, the worker shouldn't
    # waste a turn on the next bounded action.
    if card.abort_banner is not None:
        lines.append(card.abort_banner)
    if card.stuck_banner is not None:
        lines.append(card.stuck_banner)
    lines.extend([
        "Mission: Continue the UI clone as a delegated worker until the current gate has evidence, then stop and report.",
        f"Current gate: {card.current_gate}",
        f"Current goal: {card.current_goal}",
        f"Next action: {card.next_action}",
        f"Stop condition: {card.stop_condition}",
        f"Required evidence: {card.required_evidence}",
        f"Manual refresh: {card.manual_refresh}",
        f"No infinite loop: {card.no_infinite_loop}",
    ])
    if card.stop_evidence_status is not None:
        lines.append(f"Stop evidence status: {card.stop_evidence_status}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print the ui-clone-skills goal card for a ref dir"
    )
    parser.add_argument("ref_dir", type=Path, help="tmp/ref/<component> directory")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="print the goal card as structured JSON",
    )
    parser.add_argument(
        "--check-done",
        action="store_true",
        dest="check_done",
        help=(
            "exit 0 if current_gate == 'done' AND section-compare evidence is "
            "satisfied; exit 2 if pipeline-state.json has unclonable_reasons "
            "(abort — site cannot be cloned as-is); exit 1 otherwise. "
            "Suppresses normal output. Use as the loop-exit signal for "
            "external drivers (codex /goal, codex exec, headless `claude -p`)."
        ),
    )
    args = parser.parse_args(argv)
    card = build_goal_card_data(args.ref_dir)
    if args.check_done:
        # Order matters: an unclonable target should abort the loop even if
        # the pipeline somehow advanced to "done" (it shouldn't, but defend
        # against partial-state runs).
        if card.abort_banner is not None:
            return 2
        # The stop condition mirrors `_STOP_CONDITION`: gate is "done" AND the
        # section-compare evidence string starts with "satisfied:". stop_evidence_status
        # is only populated when gate == "done", so the None guard is sufficient.
        if card.current_gate == "done" and (card.stop_evidence_status or "").startswith(
            "satisfied:"
        ):
            return 0
        return 1
    if args.json_output:
        print(json.dumps(card.to_json(), ensure_ascii=False, indent=2))
    else:
        print(build_goal_card(args.ref_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
