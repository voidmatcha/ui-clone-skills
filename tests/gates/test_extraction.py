import json
from pathlib import Path

from ui_clone.gate import Gate


def test_gate_extraction_does_not_require_transition_coverage(tmp_path: Path) -> None:
    """gate_extraction must pass without transition-coverage.json.

    transition-coverage.json is produced at Step 6d, after bundle (5c) and spec (5d).
    Requiring it at the extraction gate (which runs after Step 2-3) would deadlock
    the pipeline — extraction can never advance until 6d, but 6d depends on bundle,
    which depends on extraction having passed. Coverage of transition-coverage.json
    belongs to gate_pre_generate (see test_gate_pre_generate_*).
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    for fname in [
        "structure.json",
        "head.json",
        "styles.json",
        "fonts.json",
        "visible-images.json",
        "inline-svgs.json",
        "body-state.json",
        "design-bundles.json",
    ]:
        (ref / fname).write_text(json.dumps({}))
    css_dir = ref / "css"
    css_dir.mkdir()
    (css_dir / "variables.txt").write_text(":root {}")
    # transition-coverage.json intentionally omitted

    gate = Gate(ref)
    results = gate.gate_extraction()
    failures = [r for r in results if r.status == "fail"]
    labels = [r.label for r in failures]
    assert not any("transition-coverage" in lbl for lbl in labels), (
        "gate_extraction must not require transition-coverage.json (Step 6d artifact)"
    )

