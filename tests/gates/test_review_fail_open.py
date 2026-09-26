"""Gate fail-open regressions: list/malformed roots, missing `produces`,
non-numeric counts, bundle file types, and state-corruption surfacing."""

from __future__ import annotations

import json
from pathlib import Path

from ui_clone.gates.base import CheckResult, Gate
from ui_clone.gates.pre_generate_checks import (
    _check_regions_generation_readiness,
    _check_transition_coverage,
)
from ui_clone.gates.reference import _check_regions_not_placeholder
from ui_clone.state import PipelineState

from ._helpers import _post_implement_baseline

_FORGED_REGION = {"triggerType": "scroll", "x": -99, "y": -99, "width": 0, "height": 0}


def _fails(results: list[CheckResult], needle: str) -> bool:
    return any(r.status == "fail" and needle in r.label for r in results)


def test_list_root_regions_still_validated(tmp_path: Path) -> None:
    (tmp_path / "regions.json").write_text(json.dumps([_FORGED_REGION]))
    result = _check_regions_not_placeholder(Gate(tmp_path))
    assert result is not None and result.status == "fail"
    readiness = _check_regions_generation_readiness(Gate(tmp_path))
    assert readiness and readiness[0].status == "fail"


def test_malformed_regions_fails(tmp_path: Path) -> None:
    (tmp_path / "regions.json").write_text("{ not json")
    result = _check_regions_not_placeholder(Gate(tmp_path))
    assert result is not None and result.status == "fail"
    assert _fails(_check_regions_generation_readiness(Gate(tmp_path)), "regions.json")


def test_malformed_transition_spec_fails_gate_spec(tmp_path: Path) -> None:
    for raw in ("{ not json", "[]"):
        (tmp_path / "transition-spec.json").write_text(raw)
        assert _fails(Gate(tmp_path).gate_spec(), "transition-spec.json parses"), raw


def test_null_transition_entry_fails_without_crash(tmp_path: Path) -> None:
    (tmp_path / "transition-spec.json").write_text(json.dumps({"transitions": [None]}))
    assert _fails(Gate(tmp_path).gate_spec(), "transitions[0]")


def test_malformed_transition_coverage_fails(tmp_path: Path) -> None:
    (tmp_path / "transition-coverage.json").write_text("[]")
    results = _check_transition_coverage(Gate(tmp_path), {"transitions": []})
    assert _fails(results, "transition-coverage animated")


def test_block_row_without_produces_fails(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [{"id": "junk-token", "severity": "block"}],
    }))
    results = Gate(ref)._check_verification_plan()
    assert _fails(results, "required: junk-token")


def test_tree_diff_non_numeric_counts_fail(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "tree-diff", "produces": "tree-diff-status.json", "severity": "block"},
        ],
    }))
    (ref / "tree-diff-status.json").write_text(
        json.dumps({"status": "pass", "elements_walked": "many"})
    )
    results = Gate(ref)._check_verification_plan()
    assert _fails(results, "required: tree-diff")


def test_list_root_plans_do_not_crash_pre_generate(tmp_path: Path) -> None:
    (tmp_path / "generation-plan.json").write_text("[]")
    (tmp_path / "verification-plan.json").write_text("[]")
    results = Gate(tmp_path).gate_pre_generate()
    assert _fails(results, "generation-plan schema")


def test_list_root_verification_plan_does_not_crash_section_compare(tmp_path: Path) -> None:
    (tmp_path / "sections").mkdir()
    (tmp_path / "sections" / "result.txt").write_text(
        "| hero | 0 | 0 | ok | ✅ |\n\n**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n"
    )
    (tmp_path / "detected-breakpoints.json").write_text("{}")
    (tmp_path / "verification-plan.json").write_text("[]")
    assert _fails(Gate(tmp_path).gate_section_compare(), "sections/viewport-coverage")


def test_bundle_dir_requires_js(tmp_path: Path) -> None:
    (tmp_path / "bundles").mkdir()
    (tmp_path / "bundles" / "notes.txt").write_text("x")
    assert _fails(Gate(tmp_path).gate_bundle(), "bundles/")
    (tmp_path / "bundles" / "main.js").write_text("x")
    assert not _fails(Gate(tmp_path).gate_bundle(), "bundles/")


def test_gate_run_surfaces_state_corruption(tmp_path: Path) -> None:
    (tmp_path / "pipeline-state.json").write_text("[]")
    assert Gate(tmp_path).run("extraction", json_output=True) == 1
    state = PipelineState.load(tmp_path)
    assert any(r.get("category") == "state-corruption" for r in state.unclonable_reasons)
    assert list(tmp_path.glob("pipeline-state.json.corrupt.*"))
