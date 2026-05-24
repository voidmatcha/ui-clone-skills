import json
from pathlib import Path

from ui_clone.gate import Gate


def test_gate_boundary_fails_when_artifact_missing(tmp_path: Path) -> None:
    """gate_boundary must fail when responsive/boundary-collisions.json is absent."""
    ref = tmp_path / "ref"
    ref.mkdir()

    gate = Gate(ref)
    results = gate.gate_boundary()
    failures = [r for r in results if r.status == "fail"]
    assert any("boundary-collisions.json" in r.message for r in failures), (
        "Missing boundary-collisions.json must produce a fail in gate_boundary"
    )



def test_gate_boundary_passes_when_array_empty(tmp_path: Path) -> None:
    """gate_boundary must pass when the artifact exists and is `[]` (no collisions)."""
    ref = tmp_path / "ref"
    (ref / "responsive").mkdir(parents=True)
    (ref / "responsive" / "boundary-collisions.json").write_text("[]")

    gate = Gate(ref)
    results = gate.gate_boundary()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"empty array must pass gate_boundary: {failures}"
    assert any("No breakpoint collisions" in r.message for r in results)



def test_gate_boundary_fails_when_collisions_present(tmp_path: Path) -> None:
    """gate_boundary must fail when the array has at least one finding."""
    ref = tmp_path / "ref"
    (ref / "responsive").mkdir(parents=True)
    (ref / "responsive" / "boundary-collisions.json").write_text(
        json.dumps([{"bp": 768, "reasons": ["isolated overflow spike"]}])
    )

    gate = Gate(ref)
    results = gate.gate_boundary()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "non-empty boundary-collisions.json must fail gate_boundary"
    assert any("768" in r.message for r in failures)



def test_gate_boundary_fails_when_artifact_invalid_json(tmp_path: Path) -> None:
    """gate_boundary must fail when the artifact is not valid JSON."""
    ref = tmp_path / "ref"
    (ref / "responsive").mkdir(parents=True)
    (ref / "responsive" / "boundary-collisions.json").write_text("{not json")

    gate = Gate(ref)
    results = gate.gate_boundary()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "invalid JSON must fail gate_boundary"



def test_gate_boundary_fails_when_artifact_not_array(tmp_path: Path) -> None:
    """gate_boundary must fail when the artifact is JSON but not an array."""
    ref = tmp_path / "ref"
    (ref / "responsive").mkdir(parents=True)
    (ref / "responsive" / "boundary-collisions.json").write_text('{"bp": 768}')

    gate = Gate(ref)
    results = gate.gate_boundary()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "non-array JSON must fail gate_boundary"

