"""pre-generate sub-check: clonability-report.json exists, is current, and
every blocker carries a recorded USER decision.

Only a run that predates this requirement is soft: `pre-generate` already in
`completed_steps`, no report on disk, AND no `clonabilityReportAt` record in
pipeline-state.json (set when the producer, or a passing check, first sees a
report). It only warns, so the impl-write hook (which re-runs this gate) and
closeout never newly fail for it. Once a report was recorded it is enforced in
full (missing, stop decision, staleness, open blockers), and Bash
writes/deletes of the report, glob forms included, are denied like other
enforcement state.
"""

from __future__ import annotations

from pathlib import Path

from ui_clone import clonability as _clon
from ui_clone.state import PipelineState

from .base import CheckResult

_LABEL = "clonability-report.json"


def check_clonability_report(ref_dir: Path) -> CheckResult:
    fix = f"python -m ui_clone.clonability {ref_dir}  (then relay its summary to the user)"
    report = _clon.load_report(ref_dir)

    state = PipelineState.load(ref_dir) if (ref_dir / "pipeline-state.json").is_file() else None
    if report is None:
        message = f"{_LABEL} — MISSING or unreadable (Step 5c-d clonability risk report)"
        # Legacy only when the run passed pre-generate AND never recorded a
        # report: a report deleted later does not drop the gate to warn-only.
        legacy = (
            state is not None
            and "pre-generate" in state.completed_steps
            and not state.clonability_report_at
        )
        if legacy:
            return CheckResult(
                _LABEL,
                "warn",
                f"{message} (not enforced: pre-generate passed before this run had the report)",
            )
        return CheckResult(_LABEL, "fail", message, fix=fix)
    provenance = _clon._as_dict(report.get("provenance"))
    if (
        report.get("schemaVersion") != _clon.SCHEMA_VERSION
        or provenance.get("source") != _clon.PRODUCER
        or not isinstance(report.get("risks"), list)
    ):
        return CheckResult(
            _LABEL,
            "fail",
            f"{_LABEL} — not written by {_clon.PRODUCER} schemaVersion {_clon.SCHEMA_VERSION}",
            fix=fix,
        )
    # A user "stop" is final whatever changed since: evaluated before staleness.
    stopped = _clon.stopped_blockers(report)
    if stopped:
        return CheckResult(
            _LABEL,
            "fail",
            f"{_LABEL} — user decided to stop on blocker(s): {', '.join(stopped)}",
            fix="The run is recorded unclonable; do not generate.",
        )
    stale = _clon.stale_inputs(ref_dir, report)
    if stale:
        return CheckResult(
            _LABEL,
            "fail",
            f"{_LABEL} — stale vs its inputs: {', '.join(stale[:6])}; re-run the producer",
            fix=fix,
            stale=True,
        )
    pending = _clon.open_blockers(report)
    if pending:
        from_state = [str(r.get("id")) for r in pending if r.get("fromState")]
        decidable = [str(r.get("id")) for r in pending if not r.get("fromState")]
        fixes = []
        if decidable:
            fixes.append(_clon.decision_instructions(ref_dir, decidable))
        if from_state:
            fixes.append(_clon.recover_instructions(ref_dir, from_state))
        return CheckResult(
            _LABEL,
            "fail",
            f"{_LABEL} — blocker(s) await the user's decision: "
            f"{', '.join(str(r.get('id')) for r in pending)}",
            fix=" ".join(fixes),
        )
    if state is not None and not state.clonability_report_at:
        # A report written before the producer kept the record (in-flight run).
        state.record_clonability_report(ref_dir)
    risks = report.get("risks") or []
    cautions = sum(1 for r in risks if isinstance(r, dict) and r.get("severity") == "caution")
    return CheckResult(
        _LABEL,
        "pass",
        f"{_LABEL} current ({cautions} caution(s), blockers decided by the user)",
    )
