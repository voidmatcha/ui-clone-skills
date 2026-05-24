import json
from pathlib import Path

from ui_clone.gate import Gate

from ._helpers import (
    _write_pre_generate_baseline,
    _write_valid_artifact_provenance,
)


def test_gate_pre_generate_blocks_when_footer_missing_from_component_map(tmp_path: Path) -> None:
    """section-map has hasFooter=True but component-map has no footer entry → fail."""
    ref = tmp_path / "ref"
    ref.mkdir()

    # Minimal artifacts required by gate_pre_generate
    (ref / "extracted.json").write_text(json.dumps({"sections": [], "url": "https://example.com"}))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "fixture-reveal-on-scroll",
            "trigger": "intersection",
            "source_chunk": "fixture.js",
            "bundle_branch": "main",
            "target": ".fixture",
            "animation": "opacity-translateY",
            "reference_frames": ["frame_00.png"],
        }]
    }))
    (ref / "animation-init-styles.json").write_text(json.dumps({}))
    (ref / "svg-text-elements.json").write_text(json.dumps([]))
    responsive = ref / "responsive"
    responsive.mkdir()
    (responsive / "sizing-expressions.json").write_text(json.dumps({}))
    (ref / "interactions-detected.json").write_text(
        json.dumps({"interactions": [], "hasPreloader": False})
    )
    (ref / "hover-css-rules.json").write_text(json.dumps([]))
    (ref / "transition-coverage.json").write_text(
        json.dumps({"animatedElements": [], "staticElements": []})
    )
    (ref / "element-roles.json").write_text(json.dumps({}))
    (ref / "element-groups.json").write_text(json.dumps({}))
    (ref / "layout-decisions.json").write_text(json.dumps({}))

    # section-map has a <footer>
    (ref / "section-map.json").write_text(
        json.dumps(
            {
                "sections": [{"tag": "main"}],
                "totalCount": 1,
                "hasFooter": True,
            }
        )
    )
    # component-map has NO footer entry
    (ref / "component-map.json").write_text(
        json.dumps(
            {
                "sections": [{"componentName": "HeroSection", "sourceTag": "main"}],
                "sectionCount": 1,
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_pre_generate()
    footer_failures = [r for r in results if r.status == "fail" and "footer" in r.message.lower()]
    assert footer_failures, "Missing footer in component-map must produce a fail result"



def test_gate_pre_generate_fails_when_hover_timing_unknown(tmp_path: Path) -> None:
    """interactions with timingSource='unknown' must cause gate failure."""
    ref = tmp_path / "ref"
    ref.mkdir()

    (ref / "extracted.json").write_text(json.dumps({"sections": [], "url": "https://example.com"}))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "fixture-reveal-on-scroll",
            "trigger": "intersection",
            "source_chunk": "fixture.js",
            "bundle_branch": "main",
            "target": ".fixture",
            "animation": "opacity-translateY",
            "reference_frames": ["frame_00.png"],
        }]
    }))
    (ref / "animation-init-styles.json").write_text(json.dumps({}))
    (ref / "svg-text-elements.json").write_text(json.dumps([]))
    responsive = ref / "responsive"
    responsive.mkdir()
    (responsive / "sizing-expressions.json").write_text(json.dumps({}))
    (ref / "hover-css-rules.json").write_text(json.dumps([]))
    (ref / "transition-coverage.json").write_text(
        json.dumps({"animatedElements": [], "staticElements": []})
    )
    (ref / "element-roles.json").write_text(json.dumps({}))
    (ref / "element-groups.json").write_text(json.dumps({}))
    (ref / "layout-decisions.json").write_text(json.dumps({}))
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [], "totalCount": 0, "hasFooter": False})
    )
    (ref / "component-map.json").write_text(json.dumps({"sections": [], "sectionCount": 0}))
    # interactions with timingSource='unknown'
    (ref / "interactions-detected.json").write_text(
        json.dumps(
            {
                "interactions": [
                    {"trigger": "hover", "timingSource": "unknown", "selector": ".btn"},
                ],
                "hasPreloader": False,
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_pre_generate()
    timing_failures = [r for r in results if r.status == "fail" and "unknown" in r.message.lower()]
    assert timing_failures, "timingSource='unknown' must produce a fail result"



def test_gate_pre_generate_blocks_without_artifact_provenance(tmp_path: Path) -> None:
    """Pre-generation must fail when extraction artifacts have no evidence trail."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)

    gate = Gate(ref)
    results = gate.gate_pre_generate()

    assert any(
        r.status == "fail" and "artifact-provenance.json" in r.message
        for r in results
    ), "Missing artifact provenance must block pre-generation"



def test_gate_pre_generate_blocks_manual_artifact_provenance(tmp_path: Path) -> None:
    """Critical artifacts cannot be declared as hand-written/manual evidence."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)
    provenance = json.loads((ref / "artifact-provenance.json").read_text())
    provenance["artifacts"][0]["source"] = "manual"
    (ref / "artifact-provenance.json").write_text(json.dumps(provenance))

    gate = Gate(ref)
    results = gate.gate_pre_generate()

    assert any(
        r.status == "fail" and "manual" in r.message.lower()
        for r in results
    ), "Manual provenance for critical extraction artifacts must block pre-generation"



def test_gate_pre_generate_accepts_evidence_backed_artifact_provenance(tmp_path: Path) -> None:
    """Valid provenance should not add failures to an otherwise complete extraction."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)

    gate = Gate(ref)
    results = gate.gate_pre_generate()

    provenance_failures = [
        r for r in results
        if r.status == "fail" and "provenance" in r.message.lower()
    ]
    assert not provenance_failures

