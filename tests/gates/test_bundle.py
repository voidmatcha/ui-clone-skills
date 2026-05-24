import json
from pathlib import Path

from ui_clone.gate import Gate


def test_gate_bundle_fails_when_no_js_files(tmp_path: Path) -> None:
    """gate_bundle must fail when bundles/ directory has no JS files."""
    ref = tmp_path / "ref"
    ref.mkdir()
    bundles = ref / "bundles"
    bundles.mkdir()
    # No JS files

    gate = Gate(ref)
    results = gate.gate_bundle()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "Empty bundles/ must produce a fail result"



def test_gate_bundle_fails_when_required_json_missing(tmp_path: Path) -> None:
    """gate_bundle must fail when interactions-detected.json is missing."""
    ref = tmp_path / "ref"
    ref.mkdir()
    bundles = ref / "bundles"
    bundles.mkdir()
    (bundles / "chunk-0.js").write_text("// bundle")
    # interactions-detected.json, scroll-engine.json, external-sdks.json intentionally absent

    gate = Gate(ref)
    results = gate.gate_bundle()
    failures = [r for r in results if r.status == "fail"]
    assert any(
        "interactions-detected" in r.label or "interactions-detected" in r.message for r in failures
    ), "Missing interactions-detected.json must produce a fail"



def test_gate_bundle_passes_with_required_files(tmp_path: Path) -> None:
    """gate_bundle must pass when bundles/ has JS files and all required JSON files exist."""
    ref = tmp_path / "ref"
    ref.mkdir()
    bundles = ref / "bundles"
    bundles.mkdir()
    for i in range(3):
        (bundles / f"chunk-{i}.js").write_text("// bundle")
    (ref / "interactions-detected.json").write_text(
        json.dumps({"interactions": [], "hasPreloader": False})
    )
    (ref / "scroll-engine.json").write_text(json.dumps({"engine": "native"}))
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": [], "gsap": False}))

    gate = Gate(ref)
    results = gate.gate_bundle()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"gate_bundle must pass with required files: {failures}"

