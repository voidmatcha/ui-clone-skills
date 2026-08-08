from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from ui_clone.gates.base import CheckResult
from ui_clone.state import GATE_ORDER
from ui_clone.verify_report import build_verify_report, parse_section_result, write_verify_report


def test_parse_section_result_extracts_summary_and_rows(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    sections = ref / "sections"
    sections.mkdir(parents=True)
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---------|-----|--------|----------|--------|\n"
        "| header | 225 | 1563 | minor | ✅ |\n"
        "| main-tech | — | — | substituted | 🔁 STRUCTURAL_ONLY |\n"
        "\n**Result: 1 PASS, 0 FAIL, 0 SKIP, 1 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )

    parsed = parse_section_result(ref)

    assert parsed is not None
    assert parsed["summary"] == {
        "pass": 1,
        "fail": 0,
        "skip": 0,
        "structuralOnly": 1,
        "unmeasured": 0,
    }
    assert parsed["rows"][0]["section"] == "header"
    assert parsed["rows"][0]["ae"] == 225
    assert parsed["rows"][1]["ae"] is None


def test_parse_section_result_reads_the_unmeasured_field(tmp_path: Path) -> None:
    """`report --for-llm` reads this summary. Before UNMEASURED became a Result
    field it showed `20 PASS, 0 FAIL` for a run that never measured 5 sections."""
    ref = tmp_path / "ref"
    sections = ref / "sections"
    sections.mkdir(parents=True)
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---------|-----|--------|----------|--------|\n"
        "| header | 0 | 0 | ok | ✅ |\n"
        "| slideshow | — | — | unmeasured | ⚠️ UNMEASURED (blank-ref) |\n"
        "\n**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY, 1 UNMEASURED**\n",
        encoding="utf-8",
    )

    parsed = parse_section_result(ref)

    assert parsed is not None
    assert parsed["summary"]["unmeasured"] == 1
    assert parsed["rows"][1]["severity"] == "unmeasured"


def test_parse_section_result_sums_multi_viewport_result_blocks(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    sections = ref / "sections"
    sections.mkdir(parents=True)
    (sections / "result.txt").write_text(
        "# section-compare multi-viewport result\n"
        "viewport: 375x812\n"
        "[375x812] **Result: 8 PASS, 1 FAIL, 2 SKIP, 3 STRUCTURAL_ONLY**\n"
        "viewport: 1280x800\n"
        "[1280x800] **Result: 7 PASS, 2 FAIL, 1 SKIP, 4 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )

    parsed = parse_section_result(ref)

    assert parsed is not None
    assert parsed["summary"] == {
        "pass": 15,
        "fail": 3,
        "skip": 3,
        "structuralOnly": 7,
        "unmeasured": 0,
    }


def test_build_and_write_verify_report(tmp_path: Path) -> None:
    ref = tmp_path / "tmp" / "ref" / "comp"
    ref.mkdir(parents=True)
    (ref / "pipeline-state.json").write_text(
        json.dumps({"component": "comp", "current_gate": "done", "completed_steps": GATE_ORDER}),
        encoding="utf-8",
    )
    (ref / "verification-plan.json").write_text(
        json.dumps({"tier": "standard", "signals": {"hasHover": True}, "requiredChecks": []}),
        encoding="utf-8",
    )
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "| header | 0 | 0 | ok | ✅ |\n"
        "**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )

    with patch("ui_clone.gates.base.Gate.gate_spec") as gate_spec:
        gate_spec.return_value = [CheckResult("spec-ok", "pass", "spec passed")]
        report = build_verify_report(
            ref,
            gates=("spec",),
            impl_dir=tmp_path / "impl",
            gate_exit_codes={"spec": 0},
            generated_at="2026-06-08T00:00:00Z",
        )

    assert report["verdict"] == "pass"
    assert report["verificationPlan"]["tier"] == "standard"
    assert report["gates"][0]["gate"] == "spec"
    assert report["gates"][0]["pass_count"] == 1

    json_path, html_path = write_verify_report(ref, report)

    written = json.loads(json_path.read_text(encoding="utf-8"))
    assert written["component"] == "comp"
    assert html_path.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_report_verdict_fails_when_check_fails(tmp_path: Path) -> None:
    ref = tmp_path / "tmp" / "ref" / "comp"
    ref.mkdir(parents=True)

    with patch("ui_clone.gates.base.Gate.gate_spec") as gate_spec:
        gate_spec.return_value = [CheckResult("missing", "fail", "missing artifact", fix="write it")]
        report = build_verify_report(
            ref,
            gates=("spec",),
            impl_dir=tmp_path / "impl",
            gate_exit_codes={"spec": 1},
        )

    assert report["verdict"] == "fail"
    assert report["failures"] == ["spec"]
    check = report["gates"][0]["checks"][0]
    assert check["status"] == "fail"
    assert check["fix"] == "write it"
