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


_HOVER_ONLY_SPEC = {
    "transitions": [
        {"id": "btn-hover", "trigger": "hover", "type": "color", "target": ".btn"}
    ]
}


def test_scroll_spec_coverage_fails_on_gsap_scrolltrigger_bundle(tmp_path: Path) -> None:
    """sticky-elements.json (top-level list) + a JS bundle that constructs a GSAP
    ScrollTrigger with pin/scrub, while scroll-engine.json is EMPTY and
    transition-spec has only a hover entry → gate must FAIL.

    Before the fix, scroll-engine.json is the only scroll-motion signal source,
    so an empty one makes the gate pass (the GSAP bundle evidence is unseen).
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    # sticky-elements.json is a TOP-LEVEL LIST (list-handling must survive).
    (ref / "sticky-elements.json").write_text(
        json.dumps([{"className": "hero-pin", "tag": "section"}])
    )
    # scroll-engine.json is EMPTY — the framer-motion/IO path finds nothing.
    (ref / "scroll-engine.json").write_text(json.dumps({}))
    # The GSAP scroll evidence lives in a JS bundle, not scroll-engine.json.
    bundles = ref / "bundles"
    bundles.mkdir()
    (bundles / "app.bundle.js").write_text(
        "ScrollTrigger.create({ trigger: '.hero', pin:true, scrub:true });"
    )

    gate = Gate(ref)
    results = gate._check_scroll_spec_coverage(_HOVER_ONLY_SPEC)
    assert any(
        r.status == "fail" and r.label == "scroll-spec-coverage" for r in results
    ), "GSAP ScrollTrigger bundle + sticky + no scroll spec entry must FAIL"


def test_scroll_spec_coverage_passes_when_scroll_spec_entry_present(tmp_path: Path) -> None:
    """Same sticky + GSAP-bundle evidence, but transition-spec HAS a scroll-trigger
    entry → pass (motion will be verified). No regression to the pass path."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "sticky-elements.json").write_text(
        json.dumps([{"className": "hero-pin", "tag": "section"}])
    )
    (ref / "scroll-engine.json").write_text(json.dumps({}))
    bundles = ref / "bundles"
    bundles.mkdir()
    (bundles / "app.bundle.js").write_text(
        "ScrollTrigger.create({ trigger: '.hero', pin:true, scrub:true });"
    )
    spec = {
        "transitions": [
            {"id": "hero-pin", "trigger": "scroll", "mechanism": "scroll-scrub",
             "target": ".hero"}
        ]
    }

    gate = Gate(ref)
    results = gate._check_scroll_spec_coverage(spec)
    assert not any(r.status == "fail" for r in results), (
        "A scroll-trigger spec entry must satisfy the gate"
    )


def test_scroll_spec_coverage_fails_on_nonsticky_observed_parallax(tmp_path: Path) -> None:
    """Non-sticky observed motion: NO sticky-elements.json, but element-tracking.json
    shows an element whose transform changes across scroll positions (parallax),
    while transition-spec has only a hover entry → gate must FAIL.

    Before the fix, the sticky-ONLY precondition (`if not sticky: return []`)
    skips the check entirely for non-sticky parallax/reveal motion, silently
    dropping it from verification.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    # No sticky-elements.json at all.
    (ref / "scroll-engine.json").write_text(json.dumps({}))
    (ref / "element-tracking.json").write_text(json.dumps([
        {"scrollY": 0, "scrollPct": 0, "elements": [
            {"selector": "div.parallax", "inViewport": True, "top": 100,
             "transform": "matrix(1, 0, 0, 1, 0, 0)", "opacity": None,
             "scale": None, "clipPath": None, "position": None},
        ]},
        {"scrollY": 2000, "scrollPct": 50, "elements": [
            {"selector": "div.parallax", "inViewport": True, "top": -300,
             "transform": "matrix(1, 0, 0, 1, 0, -200)", "opacity": None,
             "scale": None, "clipPath": None, "position": None},
        ]},
    ]))

    gate = Gate(ref)
    results = gate._check_scroll_spec_coverage(_HOVER_ONLY_SPEC)
    assert any(
        r.status == "fail" and r.label == "scroll-spec-coverage" for r in results
    ), "Non-sticky observed parallax + no scroll spec entry must FAIL"


def test_scroll_spec_coverage_fails_on_nonsticky_transition_coverage_scroll(tmp_path: Path) -> None:
    """Non-sticky scroll-classified animatedElement in transition-coverage.json,
    no sticky-elements.json, hover-only spec → gate must FAIL."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "scroll-engine.json").write_text(json.dumps({}))
    (ref / "transition-coverage.json").write_text(json.dumps({
        "animatedElements": [
            {"selector": "div.reveal", "trigger": "scroll-driven",
             "decoded": {"position": "relative"}}
        ],
        "staticElements": [],
    }))

    gate = Gate(ref)
    results = gate._check_scroll_spec_coverage(_HOVER_ONLY_SPEC)
    assert any(
        r.status == "fail" and r.label == "scroll-spec-coverage" for r in results
    ), "Non-sticky scroll-classified element + no scroll spec entry must FAIL"


def test_scroll_spec_coverage_passes_fully_static_no_sticky_no_motion(tmp_path: Path) -> None:
    """No-false-dispatch guard: no sticky, no observed motion (element-tracking
    shows no cross-position change, transition-coverage has no animated elements),
    no allowlist token → gate must PASS (return no rows / no fail)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "scroll-engine.json").write_text(json.dumps({}))
    (ref / "transition-coverage.json").write_text(json.dumps({
        "animatedElements": [], "staticElements": [{"selector": "div.static"}],
    }))
    (ref / "element-tracking.json").write_text(json.dumps([
        {"scrollY": 0, "scrollPct": 0, "elements": [
            {"selector": "div.static", "inViewport": True, "top": 100,
             "transform": None, "opacity": None, "scale": None,
             "clipPath": None, "position": None},
        ]},
        {"scrollY": 2000, "scrollPct": 50, "elements": [
            {"selector": "div.static", "inViewport": True, "top": 100,
             "transform": None, "opacity": None, "scale": None,
             "clipPath": None, "position": None},
        ]},
    ]))

    gate = Gate(ref)
    results = gate._check_scroll_spec_coverage(_HOVER_ONLY_SPEC)
    assert not any(r.status == "fail" for r in results), (
        "Fully static page (no sticky, no observed motion) must not fail"
    )


def test_scroll_spec_coverage_passes_static_sticky_no_motion_evidence(tmp_path: Path) -> None:
    """Plain sticky layout with NO scroll-motion evidence anywhere (empty
    scroll-engine, no bundles, no sdk/plan) → must PASS. Don't fail static sticky."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "sticky-elements.json").write_text(
        json.dumps([{"className": "sticky-header", "tag": "header"}])
    )
    (ref / "scroll-engine.json").write_text(json.dumps({}))

    gate = Gate(ref)
    results = gate._check_scroll_spec_coverage(_HOVER_ONLY_SPEC)
    assert not any(r.status == "fail" for r in results), (
        "Static sticky with no scroll-motion evidence must not fail"
    )

