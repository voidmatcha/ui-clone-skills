"""Unified verification report for pipeline closeout.

`pipeline ... verify` streams the canonical gate output for operators and then
writes this machine + human-readable rollup. The report is intentionally a
summary over existing artifacts; it does not replace the gates or weaken their
exit-code semantics.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from ui_clone.gate import Gate
from ui_clone.gates.base import CheckResult
from ui_clone.gates.dispatch import _gate_method_name
from ui_clone.hooks._common import load_json_safe
from ui_clone.state import PipelineState

JsonObject = dict[str, Any]

UTC = timezone.utc  # noqa: UP017 - macOS /usr/bin/python3 is still 3.9.


@dataclass
class GateReport:
    gate: str
    passed: bool
    pass_count: int
    warn_count: int
    fail_count: int
    checks: list[JsonObject]
    exit_code: int | None = None


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_to_json(result: CheckResult) -> JsonObject:
    return {
        "label": result.label,
        "status": result.status,
        "message": result.message,
        "fix": result.fix,
        "stale": result.stale,
    }


def collect_gate_report(
    ref_dir: Path, gate_name: str, *, exit_code: int | None = None
) -> GateReport:
    """Collect one gate's current checks without mutating pipeline state."""
    gate = Gate(ref_dir)
    prereq = gate._check_pipeline_state_prerequisites(gate_name)
    if prereq is not None:
        results = [prereq]
    else:
        method = getattr(gate, _gate_method_name(gate_name))
        results = list(method())
    fail_count = sum(1 for row in results if row.status == "fail")
    warn_count = sum(1 for row in results if row.status == "warn")
    pass_count = sum(1 for row in results if row.status == "pass")
    return GateReport(
        gate=gate_name,
        passed=fail_count == 0,
        pass_count=pass_count,
        warn_count=warn_count,
        fail_count=fail_count,
        checks=[_check_to_json(row) for row in results],
        exit_code=exit_code,
    )


_SECTION_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|")
_RESULT_RE = re.compile(
    r"\*\*Result:\s*(\d+)\s+PASS,\s*(\d+)\s+FAIL,\s*(\d+)\s+SKIP,\s*(\d+)\s+STRUCTURAL_ONLY"
    r"(?:,\s*(\d+)\s+UNMEASURED)?"
)


def parse_section_result(ref_dir: Path) -> JsonObject | None:
    path = ref_dir / "sections" / "result.txt"
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    rows: list[JsonObject] = []
    summary: JsonObject = {}
    summary_found = False
    for line in lines:
        result_match = _RESULT_RE.search(line)
        if result_match:
            if not summary_found:
                summary = {
                    "pass": 0,
                    "fail": 0,
                    "skip": 0,
                    "structuralOnly": 0,
                    "unmeasured": 0,
                }
                summary_found = True
            summary["pass"] += int(result_match.group(1))
            summary["fail"] += int(result_match.group(2))
            summary["skip"] += int(result_match.group(3))
            summary["structuralOnly"] += int(result_match.group(4))
            # Absent on artifacts written before UNMEASURED became a Result field.
            summary["unmeasured"] += int(result_match.group(5) or 0)
            continue
        if "---" in line or "Section" in line:
            continue
        match = _SECTION_ROW_RE.match(line)
        if not match:
            continue
        section, ae, ae_mpx, severity, status = [part.strip() for part in match.groups()]
        if not section:
            continue
        rows.append(
            {
                "section": section,
                "ae": None if ae in {"—", "-", ""} else _maybe_int(ae),
                "aeMpx": None if ae_mpx in {"—", "-", ""} else _maybe_int(ae_mpx),
                "severity": severity,
                "status": status,
            }
        )
    return {"path": "sections/result.txt", "summary": summary, "rows": rows}


def _maybe_int(value: str) -> int | str:
    try:
        return int(value)
    except ValueError:
        return value


def _verification_plan_summary(ref_dir: Path) -> JsonObject | None:
    plan = load_json_safe(ref_dir / "verification-plan.json")
    if plan is None:
        return None
    raw_required = plan.get("requiredChecks")
    required = cast(list[Any], raw_required) if isinstance(raw_required, list) else []
    return {
        "tier": plan.get("tier", ""),
        "signals": plan.get("signals", {}),
        "requiredCheckCount": len(required),
        "requiredChecks": [
            {
                "id": row.get("id"),
                "produces": row.get("produces"),
                "severity": row.get("severity"),
                "tier": row.get("tier"),
            }
            for row in required
            if isinstance(row, dict)
        ],
    }


def _capture_confidence_summary(ref_dir: Path) -> JsonObject | None:
    """Surface sections whose capture settle probe timed out mid-animation
    (sections/capture-confidence.json, written by section_capture). AE
    failures on those sections may be harness-caused (frozen frame), not
    impl errors — the report must carry that signal next to the verdict."""
    data = load_json_safe(ref_dir / "sections" / "capture-confidence.json")
    if not isinstance(data, dict):
        return None
    raw = data.get("suspectSections")
    suspects = [str(s) for s in raw] if isinstance(raw, list) else []
    return {"suspectSections": suspects, "captureSuspect": bool(suspects)}


def _motion_parity_summary(ref_dir: Path) -> JsonObject:
    """Roll up motion-verification signals into the closeout report.

    The report previously aggregated only static AE/SSIM (sections/result.txt)
    as first-class results — a green verdict could silently hide unverified
    motion (sub-comprehensive tier, unmeasurable scrubs). This section makes
    that debt machine-readable next to the verdict.
    """
    fires = load_json_safe(ref_dir / "transition-fires.json") or {}
    plan = load_json_safe(ref_dir / "verification-plan.json") or {}
    tier = str(plan.get("tier") or "")
    raw_ids = fires.get("unmeasurableIds")
    unmeasurable_ids = [str(i) for i in raw_ids] if isinstance(raw_ids, list) else []
    fires_summary: JsonObject | None = None
    if fires:
        fires_summary = {
            "total": fires.get("total"),
            "fired": fires.get("fired"),
            "failed": fires.get("failed"),
            "unmeasurable": fires.get("unmeasurable"),
            "unmeasurableIds": unmeasurable_ids,
        }
    return {
        "transitionFires": fires_summary,
        "planTier": tier or None,
        # Motion-arc comparators (video/hover/click state compares) dispatch
        # only at comprehensive tier — below it, motion was NOT verified.
        "motionCheckedAtTier": (tier == "comprehensive") if tier else None,
        "unverifiedMotionDebt": bool(unmeasurable_ids)
        or bool(tier and tier != "comprehensive"),
    }


def build_verify_report(
    ref_dir: Path,
    *,
    gates: tuple[str, ...],
    impl_dir: Path,
    gate_exit_codes: dict[str, int] | None = None,
    generated_at: str | None = None,
) -> JsonObject:
    ref_dir = Path(ref_dir)
    gate_exit_codes = gate_exit_codes or {}
    gate_reports = [
        collect_gate_report(ref_dir, gate, exit_code=gate_exit_codes.get(gate))
        for gate in gates
    ]
    failures = [gate.gate for gate in gate_reports if not gate.passed or gate.exit_code not in (None, 0)]
    state = PipelineState.load(ref_dir)
    stamp = load_json_safe(ref_dir / "verify-stamp.json")
    report: JsonObject = {
        "schemaVersion": 1,
        "source": "ui_clone.verify_report",
        "generatedAt": generated_at or _now_iso(),
        "component": ref_dir.name,
        "refDir": str(ref_dir),
        "implDir": str(impl_dir),
        "verdict": "pass" if not failures else "fail",
        "failures": failures,
        "pipelineState": {
            "currentGate": state.current_gate,
            "completedSteps": state.completed_steps,
            "implRoot": state.impl_root,
            "closeoutPolicy": state.closeout_policy,
        },
        "verificationPlan": _verification_plan_summary(ref_dir),
        "motionParity": _motion_parity_summary(ref_dir),
        "captureConfidence": _capture_confidence_summary(ref_dir),
        "sectionCompare": parse_section_result(ref_dir),
        "verifyStamp": stamp,
        "gates": [asdict(gate) for gate in gate_reports],
        "artifactIndex": {
            "json": "verify-report.json",
            "html": "verify-report.html",
            "sectionResult": "sections/result.txt" if (ref_dir / "sections" / "result.txt").is_file() else None,
        },
    }
    return report


def write_verify_report(ref_dir: Path, report: JsonObject) -> tuple[Path, Path]:
    ref_dir = Path(ref_dir)
    json_path = ref_dir / "verify-report.json"
    html_path = ref_dir / "verify-report.html"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    html_path.write_text(render_verify_report_html(report), encoding="utf-8")
    return json_path, html_path


def render_verify_report_html(report: JsonObject) -> str:
    verdict = str(report.get("verdict") or "unknown")
    cls = "pass" if verdict == "pass" else "fail"
    raw_gates = report.get("gates")
    gates = cast(list[Any], raw_gates) if isinstance(raw_gates, list) else []
    section = report.get("sectionCompare") if isinstance(report.get("sectionCompare"), dict) else None
    plan = report.get("verificationPlan") if isinstance(report.get("verificationPlan"), dict) else None

    gate_rows = []
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        status = "PASS" if gate.get("passed") and gate.get("exit_code") in (None, 0) else "FAIL"
        gate_rows.append(
            "<tr>"
            f"<td>{html.escape(str(gate.get('gate', '')))}</td>"
            f"<td class='{status.lower()}'>{status}</td>"
            f"<td>{gate.get('pass_count', 0)}</td>"
            f"<td>{gate.get('warn_count', 0)}</td>"
            f"<td>{gate.get('fail_count', 0)}</td>"
            f"<td>{'' if gate.get('exit_code') is None else html.escape(str(gate.get('exit_code')))}</td>"
            "</tr>"
        )

    issue_items: list[str] = []
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        for check in gate.get("checks", []):
            if not isinstance(check, dict) or check.get("status") != "fail":
                continue
            fix = check.get("fix") or ""
            issue_items.append(
                "<li>"
                f"<strong>{html.escape(str(gate.get('gate')))} / {html.escape(str(check.get('label')))}</strong>: "
                f"{html.escape(str(check.get('message')))}"
                + (f"<br><em>Fix:</em> {html.escape(str(fix))}" if fix else "")
                + "</li>"
            )

    section_rows = []
    if section:
        raw_rows = section.get("rows")
        rows = cast(list[Any], raw_rows) if isinstance(raw_rows, list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            section_rows.append(
                "<tr>"
                f"<td>{html.escape(str(row.get('section', '')))}</td>"
                f"<td>{html.escape(str(row.get('ae', '')))}</td>"
                f"<td>{html.escape(str(row.get('aeMpx', '')))}</td>"
                f"<td>{html.escape(str(row.get('severity', '')))}</td>"
                f"<td>{html.escape(str(row.get('status', '')))}</td>"
                "</tr>"
            )

    required_count = plan.get("requiredCheckCount") if plan else "n/a"
    tier = plan.get("tier") if plan else "n/a"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ui-clone verify report — {html.escape(str(report.get('component', '')))}</title>
  <style>
    body {{ font: 14px/1.5 -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; margin: 32px; color: #17202a; }}
    h1, h2 {{ margin-bottom: 8px; }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 999px; font-weight: 700; }}
    .badge.pass, .pass {{ color: #087f23; }}
    .badge.fail, .fail {{ color: #b00020; }}
    table {{ border-collapse: collapse; width: 100%; margin: 12px 0 24px; }}
    th, td {{ border: 1px solid #dde3ea; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f6f8fa; }}
    code {{ background: #f6f8fa; padding: 2px 4px; border-radius: 4px; }}
    .muted {{ color: #5f6b7a; }}
  </style>
</head>
<body>
  <h1>ui-clone verify report</h1>
  <p><span class="badge {cls}">{html.escape(verdict.upper())}</span></p>
  <p class="muted">Generated: {html.escape(str(report.get('generatedAt', '')))}</p>
  <p><strong>Component:</strong> {html.escape(str(report.get('component', '')))}<br>
     <strong>Ref:</strong> <code>{html.escape(str(report.get('refDir', '')))}</code><br>
     <strong>Impl:</strong> <code>{html.escape(str(report.get('implDir', '')))}</code></p>
  <h2>Verification plan</h2>
  <p>Tier: <strong>{html.escape(str(tier))}</strong>; required checks: <strong>{html.escape(str(required_count))}</strong></p>
  <h2>Gate summary</h2>
  <table><thead><tr><th>Gate</th><th>Status</th><th>Pass</th><th>Warn</th><th>Fail</th><th>Exit</th></tr></thead><tbody>
    {''.join(gate_rows)}
  </tbody></table>
  <h2>Failures</h2>
  {('<ul>' + ''.join(issue_items) + '</ul>') if issue_items else '<p>No failing checks.</p>'}
  <h2>Section compare</h2>
  <table><thead><tr><th>Section</th><th>AE</th><th>AE/Mpx</th><th>Severity</th><th>Status</th></tr></thead><tbody>
    {''.join(section_rows) if section_rows else '<tr><td colspan="5">No section rows.</td></tr>'}
  </tbody></table>
</body>
</html>
"""
