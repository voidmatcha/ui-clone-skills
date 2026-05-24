import json
from pathlib import Path

from ui_clone.gate import Gate

from ._helpers import (
    _write_min_spec_artifacts,
)


def test_gate_paid_features_fails_when_artifact_missing(tmp_path: Path) -> None:
    """gate_paid_features must fail when paid-features.json is absent."""
    ref = tmp_path / "ref"
    ref.mkdir()

    gate = Gate(ref)
    results = gate.gate_paid_features()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "Missing paid-features.json must fail gate_paid_features"
    assert any("paid-features.json" in r.message for r in failures)



def test_gate_paid_features_passes_when_no_findings(tmp_path: Path) -> None:
    """gate_paid_features must pass when paidFonts is empty."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "paid-features.json").write_text(json.dumps({"paidFonts": []}))

    gate = Gate(ref)
    results = gate.gate_paid_features()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"empty findings must pass: {failures}"



def test_gate_paid_features_fails_when_decision_is_null(tmp_path: Path) -> None:
    """gate_paid_features must fail when any paid font has decision=null."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "paid-features.json").write_text(
        json.dumps(
            {
                "paidFonts": [
                    {
                        "family": None,
                        "cdn": "use.typekit.net",
                        "evidence": "css/main.css:1",
                        "decision": None,
                    }
                ],
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_paid_features()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "decision=null must fail"
    assert any("use.typekit.net" in r.message for r in failures)



def test_gate_paid_features_passes_when_decisions_set(tmp_path: Path) -> None:
    """gate_paid_features must pass once every paid font has a valid decision."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "paid-features.json").write_text(
        json.dumps(
            {
                "paidFonts": [
                    {
                        "family": None,
                        "cdn": "use.typekit.net",
                        "evidence": "css/main.css:1",
                        "decision": "substitute",
                    },
                    {
                        "family": None,
                        "cdn": "fast.fonts.net",
                        "evidence": "head.json:1",
                        "decision": "use",
                    },
                ],
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_paid_features()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"valid decisions must pass: {failures}"



def test_gate_paid_features_fails_when_decision_invalid(tmp_path: Path) -> None:
    """gate_paid_features must fail when decision is not in {use, substitute, skip}."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "paid-features.json").write_text(
        json.dumps(
            {
                "paidFonts": [
                    {
                        "family": None,
                        "cdn": "p.typekit.net",
                        "evidence": "css/main.css:7",
                        "decision": "yes",
                    }
                ],
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_paid_features()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "invalid decision value must fail"
    assert any("p.typekit.net" in r.message for r in failures)



def test_gate_paid_features_fails_when_partial_decisions(tmp_path: Path) -> None:
    """gate_paid_features must fail if even one paid font has decision=null among many."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "paid-features.json").write_text(
        json.dumps(
            {
                "paidFonts": [
                    {
                        "family": None,
                        "cdn": "use.typekit.net",
                        "evidence": "css/a.css:1",
                        "decision": "use",
                    },
                    {
                        "family": None,
                        "cdn": "fast.fonts.net",
                        "evidence": "css/b.css:2",
                        "decision": None,
                    },
                ],
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_paid_features()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "any null decision must fail the gate"



def test_gate_spec_skips_substitution_check_when_no_paid_features_json(tmp_path: Path) -> None:
    """No paid-features.json → no substitution check runs (paid-features gate
    would block first; here we just verify spec stays silent)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_min_spec_artifacts(ref)
    # No paid-features.json written

    gate = Gate(ref)
    results = gate.gate_spec()
    sub_results = [r for r in results if "paid-font substitution" in r.label]
    assert sub_results == [], "no paid-features.json → no substitution check"

