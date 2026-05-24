from pathlib import Path

from ui_clone.gate import Gate


def test_gate_reference_pass(ref_dir_with_artifacts: Path) -> None:
    gate = Gate(ref_dir_with_artifacts)
    results = gate.gate_reference()
    failures = [r for r in results if r.status == "fail"]
    assert failures == [], f"Unexpected failures: {failures}"



def test_gate_reference_fail_no_screenshots(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    gate = Gate(ref)
    results = gate.gate_reference()
    failures = [r for r in results if r.status == "fail"]
    assert len(failures) > 0



def test_gate_reference_fail_no_transitions_ref(tmp_path: Path) -> None:
    """gate_reference must fail when transitions/ref/ is missing (SKILL.md Phase 1 gate)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    # Has screenshots but no transitions/ref/
    screenshots = ref / "static" / "ref"
    screenshots.mkdir(parents=True)
    for i in range(5):
        (screenshots / f"scroll_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
    (ref / "regions.json").write_text('{"regions": []}')

    gate = Gate(ref)
    results = gate.gate_reference()
    failures = [r for r in results if r.status == "fail"]
    assert any("transitions" in r.label or "transitions" in r.message for r in failures), (
        "Missing transitions/ref/ must produce a fail result"
    )



def test_gate_reference_pass_with_transitions_ref(tmp_path: Path) -> None:
    """gate_reference must pass when all three Phase 1 artifacts exist."""
    ref = tmp_path / "ref"
    ref.mkdir()
    screenshots = ref / "static" / "ref"
    screenshots.mkdir(parents=True)
    for i in range(5):
        (screenshots / f"scroll_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
    transitions = ref / "transitions" / "ref"
    transitions.mkdir(parents=True)
    (transitions / "scroll.webm").write_bytes(b"\x1aE\xdf\xa3" + b"\x00" * 100)
    (ref / "regions.json").write_text('{"regions": []}')

    gate = Gate(ref)
    results = gate.gate_reference()
    failures = [r for r in results if r.status == "fail"]
    assert failures == [], f"Unexpected failures: {failures}"

