"""Host-neutral goal card for continuing a UI clone run."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

from ui_clone.state import GATE_ORDER, HARD_CAP_GATE_FAILS, PipelineState


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


_STOP_CONDITION = (
    'current_gate == "done", all pipeline prerequisites completed, '
    "and section comparison has no FAIL / MISSING impl lines."
)
_NO_INFINITE_LOOP = (
    "this card describes one bounded next action; "
    "host continuation must stop at the stop condition."
)
# Threshold for the stuck banner. 3 consecutive fails of the same active gate
# is the empirical "you are grinding, not progressing" boundary — fewer is
# noise (one retry after a transient browser glitch), more wastes iterations.
_STUCK_THRESHOLD = 3
# Hard ABORT threshold. The stuck banner is advisory — the agent reads it and
# sometimes keeps retrying anyway (observed in the 3-round benchmark: B bench
# climbed to `gate_fail_counts[post-implement] == 445` while the stuck banner
# was being emitted on every cycle). Beyond this count, the goal card flips
# to abort_banner mode which is terminal: `--check-done` exits 2 and external
# loop drivers stop iterating. The canonical source for this threshold is now
# `state.HARD_CAP_GATE_FAILS` (which also drives the auto-record of an
# `unclonable_reasons` entry from `PipelineState.mark_failed`). The aliased
# name is kept for back-compat with `benchmark_harness.py` which imports it.
_MAX_GATE_FAILS = HARD_CAP_GATE_FAILS


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
    "state-coverage": GoalStep(
        current_goal="Verify multi-snapshot state coverage",
        next_action="Run python -m ui_clone.gate <ref-dir> state-coverage before implementation verification",
        required_evidence="states/splash/trajectory.json, states/scroll/summary.json, states/hover/manifest.json, and matching impl state hooks when present",
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


def _missing_prerequisite_status(state: PipelineState, gate: str) -> str | None:
    missing = state.missing_prerequisites(gate)
    if not missing:
        return None
    return (
        "not satisfied: pipeline-state.json missing completed "
        f"prerequisite gate(s): {', '.join(missing)}"
    )


def _done_stop_evidence_status(ref_dir: Path, state: PipelineState) -> str:
    missing_status = _missing_prerequisite_status(state, "done")
    if missing_status is not None:
        return missing_status
    return _section_compare_status(ref_dir)


def _failing_sections_by_ae(ref_dir: Path, limit: int = 3) -> list[tuple[str, int]]:
    """Return up to `limit` (section-label, AE/Mpx) pairs from the worst-AE
    failing rows of sections/result.txt. Used by the visual-judge routing
    hint when section-compare keeps failing.

    result.txt rows look like:
        | section-1 | 1227100 | 946836 | critical | ❌ |
    columns are: name | AE | AE/Mpx | severity | status.
    """
    result = ref_dir / "sections" / "result.txt"
    if not result.is_file():
        return []
    failing: list[tuple[str, int]] = []
    for line in result.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("|") or "❌" not in line:
            continue
        parts = [c.strip() for c in line.strip("|").split("|")]
        if len(parts) < 4:
            continue
        name = parts[0]
        if name.lower() in {"section", "---------"} or name.startswith("-"):
            continue
        try:
            ae_per_mpx = int(parts[2])
        except (ValueError, IndexError):
            continue
        failing.append((name, ae_per_mpx))
    failing.sort(key=lambda row: row[1], reverse=True)
    return failing[:limit]


def _section_compare_row_counts(ref_dir: Path) -> dict[str, int]:
    """Return table-row counts from sections/result.txt.

    The summary footer contains words like PASS/FAIL, so only markdown table
    rows are counted. Saturated rows are still failing rows, but surfacing the
    saturated count separately helps benchmark drivers avoid false convergence
    stops when AE has no useful gradient.
    """
    result = ref_dir / "sections" / "result.txt"
    counts = {"pass": 0, "fail": 0, "saturated": 0}
    if not result.is_file():
        return counts
    for raw_line in result.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        if "✅" in line:
            counts["pass"] += 1
        if "❌" in line:
            counts["fail"] += 1
        if "saturated" in line or "🌑" in line:
            counts["saturated"] += 1
    return counts


def _section_compare_stop_guard(ref_dir: Path) -> str | None:
    """Return a hard stop-warning when section failures cannot be converged.

    Automated runs may use the graded `INCOMPLETE-CONVERGED` marker, but only
    after the section signal has improved enough to be meaningful. Prior
    failures exposed bad stops where saturated sections or failing tree-diff
    evidence were still reported as converged. This guard makes the
    disqualifiers explicit in every goal card that sees failed section evidence.
    """
    counts = _section_compare_row_counts(ref_dir)
    if counts["fail"] == 0:
        return None

    visual_judge_count = sum(1 for _ in (ref_dir / "sections").glob("visual-judge-*.json"))
    reasons: list[str] = []
    if counts["pass"] == 0:
        reasons.append("0 PASS rows")
    if counts["saturated"] > 0:
        reasons.append(f"{counts['saturated']} saturated row(s)")
    if counts["fail"] >= 2 and counts["fail"] > counts["pass"]:
        reasons.append(f"{counts['fail']} FAIL row(s) vs {counts['pass']} PASS row(s)")
    if visual_judge_count == 0:
        reasons.append("no visual-judge refinement artifact")
    tree_diff = ref_dir / "tree-diff-status.json"
    if tree_diff.is_file():
        try:
            tree_diff_data = json.loads(tree_diff.read_text(encoding="utf-8", errors="replace"))
            status = tree_diff_data.get("status")
        except (json.JSONDecodeError, OSError, AttributeError):
            status = None
            tree_diff_data = {}
        if status and status != "pass":
            reasons.append(f"tree-diff-status.json status={status}")
        if isinstance(tree_diff_data, dict):
            tree_counts = tree_diff_data.get("counts") or {}
            if isinstance(tree_counts, dict):
                unpaired = int(tree_counts.get("unpaired") or 0)
                ok = int(tree_counts.get("ok") or 0)
                if unpaired >= 3 and unpaired > ok:
                    reasons.append(f"tree-diff unpaired={unpaired} ok={ok}")
    if not reasons:
        return None

    return (
        "Do not emit INCOMPLETE-CONVERGED or any terminal clean-stop marker yet: "
        f"{', '.join(reasons)}. Continue the visual-judge refinement loop, "
        "apply concrete component/CSS fixes, restart the dev server, and re-run "
        "section-compare."
    )


def _section_png_pair(ref_dir: Path, section_label: str) -> tuple[Path, Path] | None:
    """Resolve ref/impl PNGs for flat and multi-viewport section-compare rows."""
    sections_dir = ref_dir / "sections"
    candidates: list[tuple[Path, Path]] = []
    m = re.match(r"^\[([0-9]+x[0-9]+)\]\s+(.+)$", section_label)
    if m:
        viewport, plain_label = m.group(1), m.group(2)
        viewport_sections = sections_dir / "viewports" / viewport / "sections"
        candidates.append((
            viewport_sections / "ref" / f"{plain_label}.png",
            viewport_sections / "impl" / f"{plain_label}.png",
        ))
    candidates.append((
        sections_dir / "ref" / f"{section_label}.png",
        sections_dir / "impl" / f"{section_label}.png",
    ))
    for ref_png, impl_png in candidates:
        if ref_png.is_file() and impl_png.is_file():
            return ref_png, impl_png
    return None


def _visual_judge_artifact_name(section_label: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", section_label).strip("-")
    return f"visual-judge-{safe or 'section'}.json"


def _visual_judge_next_action(ref_dir: Path) -> str | None:
    """If section-compare has failing rows AND ref/impl section PNGs exist,
    emit a concrete visual-judge next-action with the worst-AE sections
    pre-filled. Returns None when there's nothing actionable (no result.txt,
    no failing rows, or no PNGs).

    The signal architecture: AE/SSIM is precise but a dead gradient when
    every section is uniformly bad — agents see "all 950k" and quit.
    visual-judge.sh calls a multimodal LLM on the ref-clip vs impl-clip pair
    and emits actionable findings (selector_hint + tailwind suggestions).
    This routes the worker through it instead of leaving discovery to chance.
    """
    failing = _failing_sections_by_ae(ref_dir, limit=3)
    if not failing:
        return None
    command_ref = str(ref_dir)
    examples: list[str] = []
    for name, _ae in failing:
        pair = _section_png_pair(ref_dir, name)
        if pair is None:
            continue
        ref_png, impl_png = pair
        out_json = ref_dir / "sections" / _visual_judge_artifact_name(name)
        examples.append(
            f"bash skills/visual-debug/scripts/visual-judge.sh "
            f"{shlex.quote(str(ref_png))} "
            f"{shlex.quote(str(impl_png))} "
            f"--label {shlex.quote(name)} --out {shlex.quote(str(out_json))}"
        )
    if not examples:
        return None
    commands = " && ".join(examples)
    worst_list = ", ".join(f"{name}(AE/Mpx={ae})" for name, ae in failing)

    # D dispatcher cache-only read: if a prior escape-hatch run cached
    # visual-judge findings for any of the worst sections, inline the
    # priority_fix so the agent sees the LLM verdict immediately instead
    # of having to re-run visual-judge. This is read-only — no dispatch
    # happens here because dispatching expensive visual review from the goal-card path is risky.
    cached_findings: list[str] = []
    try:
        from ui_clone import visual_judge_dispatcher as _vjd
        for name, ae in failing:
            pair = _section_png_pair(ref_dir, name)
            if pair is None:
                continue
            ref_png, impl_png = pair
            cached = _vjd.load_cached(ref_dir, name, ref_png, impl_png)
            if cached:
                priority = cached.get("priority_fix") or cached.get("summary") or "?"
                cached_findings.append(
                    f"  • {name} (AE/Mpx={ae}): {str(priority)[:200]}"
                )
    except Exception:
        # Cache-only read must never break goal-card rendering.
        cached_findings = []

    cache_block = ""
    if cached_findings:
        cache_block = (
            "\n\nCACHED visual-judge findings (no re-dispatch needed):\n"
            + "\n".join(cached_findings)
            + "\nApply these directly; for sections WITHOUT a cached line, "
            "the dispatch command above will populate the cache on first run."
        )

    return (
        "section-compare has failing rows with high AE — AE is uniformly "
        "catastrophic and offers no gradient. Run visual-judge (multimodal "
        f"LLM diff) on the worst sections to get actionable findings: {commands}. "
        f"Worst-AE rows: {worst_list}. After visual-judge writes per-section "
        "JSON, read each visual-judge-<name>.json (priority_fix + findings[] "
        "with selector_hint + tailwind suggestions), apply the changes to "
        "the matching impl/src/components/<Name>.tsx, re-run section-compare "
        "via skills/visual-debug/scripts/section-compare.sh, and re-route via "
        f"python -m ui_clone.goal {command_ref}. Repeat until AE/Mpx drops "
        f"below the section-compare critical threshold.{cache_block}"
    )


def _section_compare_next_action_advisories(ref_dir: Path) -> list[str]:
    """Return non-blocking section-compare warnings that still need operator action."""
    result = ref_dir / "sections" / "result.txt"
    if not result.is_file():
        return []
    try:
        from ui_clone.gate import Gate

        checks = Gate(ref_dir).gate_section_compare()
    except Exception:
        return []
    advisories: list[str] = []
    for check in checks:
        if check.status != "warn" or check.label != "structural-only broad coverage":
            continue
        fix = f" Fix: {check.fix}" if check.fix else ""
        advisories.append(f"{check.label}: {check.message}.{fix}")
    return advisories


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
    stop_evidence_status: str | None = None

    if gate == "done":
        missing = state.missing_prerequisites("done")
        if missing:
            earliest = missing[0]
            missing_s = ", ".join(missing)
            step = GoalStep(
                current_goal="Resolve out-of-order pipeline state",
                next_action=(
                    f"Run python -m ui_clone.gate <ref-dir> {earliest}, "
                    "then refresh the goal card"
                ),
                required_evidence=(
                    "pipeline-state.json completed_steps includes missing "
                    f"prerequisite gate(s): {missing_s}"
                ),
            )
            next_action = step.next_action.replace("<ref-dir>", command_ref)
            evidence = step.required_evidence.replace("<ref-dir>", command_ref)
        stop_evidence_status = _done_stop_evidence_status(ref_dir, state)
    elif gate == "section-compare":
        missing_status = _missing_prerequisite_status(state, gate)
        if (
            missing_status is not None
            and _section_compare_status(ref_dir).startswith("satisfied:")
        ):
            earliest = state.missing_prerequisites(gate)[0]
            step = GoalStep(
                current_goal="Resolve out-of-order pipeline state",
                next_action=(
                    f"Run python -m ui_clone.gate <ref-dir> {earliest}, "
                    "then refresh the goal card"
                ),
                required_evidence=(
                    "pipeline-state.json completed_steps includes missing "
                    f"prerequisite gate(s): {', '.join(state.missing_prerequisites(gate))}"
                ),
            )
            next_action = step.next_action.replace("<ref-dir>", command_ref)
            evidence = step.required_evidence.replace("<ref-dir>", command_ref)
            stop_evidence_status = missing_status

    # Visual-judge routing: when sections/result.txt has FAIL rows, AE alone
    # is a dead gradient signal. Override the generic next-action with a
    # concrete multimodal-LLM diff invocation targeting the worst-AE sections.
    #
    # Fires on BOTH "section-compare" and "post-implement" gates. The latter
    # matters because post-implement's check transitively includes
    # section-compare evidence — when section-compare fails, post-implement
    # also fails, but current_gate stays at "post-implement" and never advances
    # to "section-compare". In repeated automated runs,
    # gate_fail_counts[post-implement] climbed past 400 while the routing
    # override never fired. Broadening the trigger to post-implement
    # cuts the runaway loop.
    if gate in ("section-compare", "post-implement"):
        vj_action = _visual_judge_next_action(ref_dir)
        stop_guard = _section_compare_stop_guard(ref_dir)
        if vj_action is not None:
            next_action = vj_action
        if stop_guard is not None:
            next_action = f"{next_action} {stop_guard}"

    if gate in ("section-compare", "post-implement", "done"):
        advisories = _section_compare_next_action_advisories(ref_dir)
        if advisories:
            next_action = f"{next_action} Advisory: {' '.join(advisories)}"

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
    # substitution, DRM canvas, auth-gated content, etc.) OR when any gate
    # has failed more than _MAX_GATE_FAILS times. This stops runaway loops
    # that keep retrying a terminally failing gate.
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
    elif fail_count >= _MAX_GATE_FAILS:
        abort_banner = (
            f"ABORT: gate '{gate}' failed {fail_count} times "
            f"(hard cap {_MAX_GATE_FAILS}). The pipeline is not converging — "
            "retrying the same gate burns iterations without progress. Halt "
            "the loop, harvest as INCOMPLETE, and report the root cause "
            "(unmapped section enumeration, splash overlay blocking capture, "
            "etc.) instead of continuing. External drivers (Python harness, "
            "LLM-driven session) MUST stop on this banner — `--check-done` "
            "returns exit 2 to make the stop machine-checkable."
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
        stop_evidence_status=stop_evidence_status,
        stuck_banner=stuck_banner,
        abort_banner=abort_banner,
    )


def build_goal_card(ref_dir: Path) -> str:
    """Return a deterministic, host-neutral goal card for a ref directory."""
    card = build_goal_card_data(ref_dir)

    # Abort-active rendering: ONLY emit terminal-state lines. The runnable
    # block (Mission / Current goal / Next action / Stop condition / Required
    # evidence / No infinite loop) is suppressed because LLMs observed
    # prioritizing "Next action" over "ABORT" when both appear in the same
    # card — prior failed runs kept iterating until manually stopped. JSON drivers (to_json) still see
    # the full structured fields; this change is text-rendering only.
    if card.abort_banner is not None:
        lines = [
            f"Goal Card: {card.component}",
            card.abort_banner,
            (
                "Terminal state — this card is terminal. Do not run any "
                "further pipeline commands. pipeline-state.json has recorded "
                "unclonable_reasons; `python -m ui_clone.goal <ref-dir> "
                "--check-done` returns exit 2 so external loop drivers stop."
            ),
            f"Current gate: {card.current_gate}",
            f"Manual refresh: {card.manual_refresh}",
        ]
        return "\n".join(lines)

    lines = [f"Goal Card: {card.component}"]
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
            "exit 0 if current_gate == 'done', pipeline prerequisites are "
            "complete, and section-compare evidence is satisfied; exit 2 if "
            "pipeline-state.json has unclonable_reasons (abort — site cannot "
            "be cloned as-is); exit 1 otherwise. "
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
            print(card.abort_banner, file=sys.stderr)
            return 2
        # The stop condition mirrors `_STOP_CONDITION`: gate is "done" AND the
        # section-compare evidence string starts with "satisfied:". stop_evidence_status
        # is only populated when gate == "done", so the None guard is sufficient.
        if card.current_gate == "done" and (card.stop_evidence_status or "").startswith(
            "satisfied:"
        ):
            return 0
        diagnostic = card.stop_evidence_status or (
            f"not satisfied: current_gate is {card.current_gate!r}, not 'done'"
        )
        print(diagnostic, file=sys.stderr)
        return 1
    if args.json_output:
        print(json.dumps(card.to_json(), ensure_ascii=False, indent=2))
    else:
        print(build_goal_card(args.ref_dir))
    # Abort banner is terminal, not advisory. Text-mode drivers that call
    # `python -m ui_clone.goal <ref-dir>` (not --check-done) would otherwise
    # loop past the hard gate-fail cap (observed runs hit 180+ retries
    # past _MAX_GATE_FAILS=10 because text mode returned 0). Match the
    # --check-done exit code so any host driver reading process exit
    # halts at the cap.
    if card.abort_banner is not None:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
