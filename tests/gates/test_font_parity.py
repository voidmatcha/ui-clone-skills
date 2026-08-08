import json
from pathlib import Path

from ui_clone.gate import Gate


def test_gate_font_parity_fails_when_artifact_missing(tmp_path: Path) -> None:
    """gate_font_parity must fail when font-parity.json is absent."""
    ref = tmp_path / "ref"
    ref.mkdir()

    gate = Gate(ref)
    results = gate.gate_font_parity()
    failures = [r for r in results if r.status == "fail"]
    assert any("font-parity.json" in r.message for r in failures), (
        "Missing font-parity.json must fail gate_font_parity"
    )



def test_gate_font_parity_passes_when_match(tmp_path: Path) -> None:
    """gate_font_parity must pass when parity is 'match'."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "font-parity.json").write_text(
        json.dumps(
            {"ref": {"family": "Inter"}, "impl": {"family": "Inter"}, "parity": "match"}
        )
    )

    gate = Gate(ref)
    results = gate.gate_font_parity()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"match must pass: {failures}"



def test_gate_font_parity_fails_when_mismatch_undeclared(tmp_path: Path) -> None:
    """gate_font_parity must fail when parity is 'mismatch' and asset-substitution.json is absent."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "font-parity.json").write_text(
        json.dumps(
            {"ref": {"family": "Exat"}, "impl": {"family": "Roboto Flex"}, "parity": "mismatch"}
        )
    )

    gate = Gate(ref)
    results = gate.gate_font_parity()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "undeclared mismatch must fail"
    assert any("Exat" in r.message and "Roboto Flex" in r.message for r in failures)



def test_gate_font_parity_passes_when_mismatch_declared(tmp_path: Path) -> None:
    """gate_font_parity must pass when mismatch is declared in asset-substitution.json."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "font-parity.json").write_text(
        json.dumps(
            {"ref": {"family": "Exat"}, "impl": {"family": "Roboto Flex"}, "parity": "mismatch"}
        )
    )
    (ref / "asset-substitution.json").write_text(
        json.dumps(
            {
                "fonts": [
                    {"original": "Exat", "replacement": "Roboto Flex", "reason": "license"}
                ],
                "structuralOnlySections": ["*"],
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_font_parity()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"declared mismatch must pass: {failures}"



def test_gate_font_parity_fails_when_substitution_has_empty_fonts(tmp_path: Path) -> None:
    """gate_font_parity must fail when asset-substitution.json exists but fonts[] is empty."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "font-parity.json").write_text(
        json.dumps(
            {"ref": {"family": "Exat"}, "impl": {"family": "Roboto Flex"}, "parity": "mismatch"}
        )
    )
    (ref / "asset-substitution.json").write_text(json.dumps({"fonts": [], "images": []}))

    gate = Gate(ref)
    results = gate.gate_font_parity()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "empty fonts[] must still fail"



def test_gate_font_parity_fails_when_impl_declared_but_not_loaded(tmp_path: Path) -> None:
    """gate_font_parity must catch the silent-fallback case: same family declared but impl FontFace failed to load."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "font-parity.json").write_text(
        json.dumps(
            {
                "ref": {"family": "Exat", "loaded": True},
                "impl": {"family": "Exat", "loaded": False},
                "parity": "match",
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_font_parity()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "match parity but impl unloaded must fail"
    assert any("NOT actually loaded" in r.message or "not actually loaded" in r.message.lower() for r in failures)



def test_gate_font_parity_passes_when_both_loaded(tmp_path: Path) -> None:
    """gate_font_parity must pass when both ref and impl have loaded:true."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "font-parity.json").write_text(
        json.dumps(
            {
                "ref": {"family": "Inter", "loaded": True},
                "impl": {"family": "Inter", "loaded": True},
                "parity": "match",
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_font_parity()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"both loaded must pass: {failures}"



def test_gate_font_parity_passes_when_loaded_field_missing(tmp_path: Path) -> None:
    """Backward compat: older font-parity.json without `loaded` field still passes on match."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "font-parity.json").write_text(
        json.dumps(
            {"ref": {"family": "Inter"}, "impl": {"family": "Inter"}, "parity": "match"}
        )
    )

    gate = Gate(ref)
    results = gate.gate_font_parity()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, "missing loaded field defaults to True (backward compat)"



def test_gate_font_parity_fails_when_invalid_parity_value(tmp_path: Path) -> None:
    """gate_font_parity must fail when `parity` is not 'match' or 'mismatch'."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "font-parity.json").write_text(json.dumps({"parity": "unknown"}))

    gate = Gate(ref)
    results = gate.gate_font_parity()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "unknown parity value must fail"



def test_gate_font_parity_fails_when_a_secondary_face_loads_only_on_the_ref(
    tmp_path: Path,
) -> None:
    """Sampling one element's primary family cannot see a second declared face.

    A page whose body text is Die Grotesk and whose banner is Geist Mono passes
    a primary-family probe while Geist Mono silently renders as Arial. Once the
    artifact enumerates every declared @font-face, a face the ref loads and the
    impl does not must fail — otherwise the gate certifies typography it never
    looked at.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "font-parity.json").write_text(
        json.dumps(
            {
                "ref": {
                    "family": "Die Grotesk A",
                    "loaded": True,
                    "families": [
                        {"family": "Die Grotesk A", "loaded": True},
                        {"family": "Geist Mono", "loaded": True},
                    ],
                },
                "impl": {
                    "family": "Die Grotesk A",
                    "loaded": True,
                    "families": [
                        {"family": "Die Grotesk A", "loaded": True},
                        {"family": "Geist Mono", "loaded": False},
                    ],
                },
                "parity": "match",
            }
        )
    )

    gate = Gate(ref)
    failures = [r for r in gate.gate_font_parity() if r.status == "fail"]
    assert any("Geist Mono" in r.message for r in failures), (
        f"a declared face the impl never loaded must fail: {failures}"
    )
