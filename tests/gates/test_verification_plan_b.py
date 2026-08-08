import json
import os
from pathlib import Path

from ui_clone.gate import Gate

from ._helpers import (
    _fixture_all_signals,
    _project_root,
    _run_verification_plan,
    _stamp_check_input_hash,
    _write_impl_fixture,
)


def test_verification_plan_emits_bundle_impl_coverage_when_bundle_map_present(tmp_path: Path) -> None:
    """Regression — c9b638d's bundle-map.json detected gsap-like + motion-like
    + Lenis, but impl/package.json shipped with only next/react/react-dom →
    dead-wire pattern. New `bundle-impl-coverage` row enforces install parity.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({
        "chunks": {"a.js": {"role": "vendor", "libs": ["gsap-like-strings"]}},
        "notes": "lenis class on <html> is conclusive runtime evidence.",
    }))
    plan = _run_verification_plan(ref)
    rows = {c["id"]: c for c in plan["requiredChecks"]}
    assert "bundle-impl-coverage" in rows
    assert rows["bundle-impl-coverage"]["severity"] == "block"
    assert rows["bundle-impl-coverage"]["tier"] == "quick"



def test_verification_plan_forces_comprehensive_tier_under_benchmark_work(tmp_path: Path) -> None:
    """Regression — the 077d8c3 benchmark exposed a gaming pattern where the
    agent set `UI_CLONE_VERIFY_TIER=quick` and the verification surface
    shrank to 3 checks. Benchmark refs (path contains `benchmark/work/`)
    must force tier=comprehensive regardless of caller-supplied tier, so the
    agent does not get to pick which checks fire.
    """
    bench_root = tmp_path / "benchmark" / "work" / "deadbee"
    ref = bench_root / "ref"
    ref.mkdir(parents=True)
    # Wire enough signals to confirm tier-conditional rows light up.
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": "https://cdn.example.com/x.jpg", "element": "img"},
    ]))
    (ref / "external-sdks.json").write_text(json.dumps({"detected": ["useScroll"]}))
    # Caller asks for quick — script must override to comprehensive.
    plan = _run_verification_plan(ref, tier="quick")
    assert plan["tier"] == "comprehensive", (
        f"benchmark/work/ ref must force tier=comprehensive, got: {plan['tier']}"
    )
    # And the comprehensive-tier rows (e.g. video-motion-compare) must appear.
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "video-motion-compare" in ids, (
        f"comprehensive-tier row missing after benchmark force: {ids}"
    )


def test_verification_plan_blocks_live_parity_and_uses_section_map_for_scroll(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [{"index": i, "top": i * 700, "height": 700} for i in range(8)],
    }))

    plan = _run_verification_plan(ref, tier="standard")
    rows = {c["id"]: c for c in plan["requiredChecks"]}

    assert rows["live-parity-sweep"]["severity"] == "block"
    assert rows["live-parity-sweep"]["tier"] == "standard"
    assert rows["live-parity-sweep"]["dependsOn"] == ["runtime-env"]
    assert rows["impl-url-guard"]["severity"] == "block"
    assert rows["runtime-env"]["dependsOn"] == ["impl-url-guard"]
    assert rows["capacity-probe"]["tier"] == "quick"
    assert rows["scroll-coverage"]["tier"] == "standard"


def test_canonical_boundary_and_font_rows_are_unconditional_at_standard_tier(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()

    plan = _run_verification_plan(ref, tier="standard")
    rows = {check["id"]: check for check in plan["requiredChecks"]}

    assert plan["signals"]["hasCommercialFont"] is False
    assert rows["breakpoint-collision"] == {
        "id": "breakpoint-collision",
        "script": "skills/visual-debug/scripts/breakpoint-collision-check.sh",
        "produces": "responsive/boundary-collisions.json",
        "reason": "canonical boundary gate requires a live breakpoint collision sweep",
        "severity": "block",
        "tier": "standard",
        "dependsOn": ["runtime-env"],
    }
    assert rows["font-parity"] == {
        "id": "font-parity",
        "script": "skills/visual-debug/scripts/font-parity-check.sh",
        "produces": "font-parity.json",
        "reason": "canonical font-parity gate requires measured ref-vs-impl font evidence",
        "severity": "block",
        "tier": "standard",
        "dependsOn": ["runtime-env"],
    }


def test_commercial_font_signal_does_not_duplicate_font_parity_row(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _fixture_all_signals(ref)

    plan = _run_verification_plan(ref, tier="standard")
    font_rows = [
        check for check in plan["requiredChecks"] if check["id"] == "font-parity"
    ]

    assert plan["signals"]["hasCommercialFont"] is True
    assert len(font_rows) == 1


def test_browser_comparison_rows_depend_on_runtime_env(tmp_path: Path) -> None:
    """Browser-backed ref-vs-impl rows must not run before runtime-env.

    This keeps stale/wrong-port failures from producing misleading visual or
    DOM artifacts after impl-url-guard/runtime-env already proved the browser
    target is not trustworthy.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _fixture_all_signals(ref)
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [{"index": i, "top": i * 700, "height": 700} for i in range(3)],
    }))
    css_dir = ref / "css"
    css_dir.mkdir()
    (css_dir / "app.css").write_text("@keyframes fade { from { opacity: 0 } to { opacity: 1 } }")

    plan = _run_verification_plan(ref, tier="comprehensive")
    rows = {c["id"]: c for c in plan["requiredChecks"]}

    for check_id in [
        "live-parity-sweep",
        "transition-compare",
        "video-motion-compare",
        "hover-state-compare",
        "hover-tree-diff",
        "click-state-compare",
        "runtime-dom-parity",
        "svg-dom-parity",
        "tree-diff",
        "scroll-coverage",
        "keyframes-diff",
        "breakpoint-collision",
        "font-parity",
    ]:
        assert rows[check_id]["dependsOn"] == ["runtime-env"], check_id


def _font_parity_required_check() -> dict[str, str]:
    return {
        "id": "font-parity",
        "script": "skills/visual-debug/scripts/font-parity-check.sh",
        "produces": "font-parity.json",
        "reason": "canonical font parity",
        "severity": "block",
    }


def _write_font_parity_plan(ref: Path) -> None:
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [_font_parity_required_check()],
            }
        ),
        encoding="utf-8",
    )


def test_font_parity_accepts_real_producer_shape_without_generic_status(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = _write_impl_fixture(ref)
    _write_font_parity_plan(ref)
    (ref / "font-parity.json").write_text(
        json.dumps(
            {
                "ref": {"family": "-apple-system", "loaded": True},
                "impl": {"family": "-apple-system", "loaded": True},
                "parity": "match",
                "capturedAt": "2026-07-30T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    _stamp_check_input_hash(ref, "font-parity", impl)

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "pass"
    assert "canonical producer evidence passed" in results[0].message


def test_font_parity_real_producer_shape_reuses_declared_substitution_semantics(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = _write_impl_fixture(ref)
    _write_font_parity_plan(ref)
    (ref / "asset-substitution.json").write_text(
        json.dumps({"fonts": [{"from": "Paid Font", "to": "Inter"}]}),
        encoding="utf-8",
    )
    (ref / "font-parity.json").write_text(
        json.dumps(
            {
                "ref": {"family": "Paid Font", "loaded": True},
                "impl": {"family": "Inter", "loaded": True},
                "parity": "mismatch",
                "capturedAt": "2026-07-30T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    _stamp_check_input_hash(ref, "font-parity", impl)

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "pass"



def test_verification_plan_omits_image_fidelity_when_visible_images_absent(tmp_path: Path) -> None:
    """No visible-images.json → no image-fidelity row.

    Locks in the conditional — this row should NOT fire unconditionally,
    otherwise SVG-only / canvas-only sites would always see a pass-noise row.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    plan = _run_verification_plan(ref)
    ids = {c["id"] for c in plan["requiredChecks"]}
    assert "image-fidelity" not in ids



def test_observed_scroll_motion_via_transition_coverage_sets_scroll_scrub(tmp_path: Path) -> None:
    """Unknown / hand-rolled motion library: scroll-engine.json EMPTY and NO
    bundle GSAP token, but transition-coverage.json recorded a scroll-classified
    animatedElement. The page WAS OBSERVED to move under scroll, so
    hasScrollScrub must be true and video-motion-compare must dispatch —
    library-agnostic, driven off observation not allowlist.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {
                "id": "handrolled-scroll-motion",
                "trigger": "scroll",
                "target": "div.handrolled_sticky",
                "animation": {"type": "scroll-linked transform"},
            }
        ],
    }))
    # Empty scroll engine + no bundle => all allowlist signals are false.
    (ref / "scroll-engine.json").write_text(json.dumps({}))
    (ref / "transition-coverage.json").write_text(json.dumps({
        "url": "https://example.com",
        "animatedElements": [
            {
                "selector": "div.handrolled_sticky",
                "trigger": "scroll-driven",
                "sectionAnchor": "handrolled",
                "decoded": {"position": "sticky", "stickyTop": "0px"},
            }
        ],
        "staticElements": [],
    }))
    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasScrollScrub"] is True, (
        "observed scroll-driven animatedElement must set hasScrollScrub even "
        "with empty scroll-engine + no allowlist token"
    )
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "video-motion-compare" in ids, (
        f"video-motion row missing for observed scroll motion: {ids}"
    )
    assert "transition-compare" not in ids, (
        "transition-compare is a hover/end-state checker; scroll-only motion "
        f"must not dispatch it as generic transition evidence: {ids}"
    )


def test_plain_css_sticky_spec_does_not_dispatch_scrub_trajectory(
    tmp_path: Path,
) -> None:
    """Sticky pinning has a dedicated geometry/runtime proof.

    A plain ``position: sticky`` entry is not a scroll-scrub trajectory, so
    whole-page AE/video checks would measure unrelated static layout drift.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "sticky-header",
                        "trigger": "scroll",
                        "target": "header",
                        "animation": {
                            "type": "css-sticky",
                            "changedProperties": ["position", "top"],
                            "duration": "0s",
                        },
                    }
                ]
            }
        )
    )

    plan = _run_verification_plan(ref)

    assert plan["signals"]["hasScrollScrub"] is False
    ids = {check["id"] for check in plan["requiredChecks"]}
    assert "transition-trajectory" not in ids
    assert "video-motion-compare" not in ids


def test_observed_plain_sticky_coverage_does_not_dispatch_scrub_trajectory(
    tmp_path: Path,
) -> None:
    """Observed sticky geometry alone is not evidence of scrubbed properties."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-coverage.json").write_text(
        json.dumps(
            {
                "animatedElements": [
                    {
                        "selector": "header",
                        "trigger": "scroll",
                        "decoded": {
                            "position": "sticky",
                            "stickyTop": "0px",
                        },
                    }
                ]
            }
        )
    )

    plan = _run_verification_plan(ref)

    assert plan["signals"]["hasScrollScrub"] is False
    ids = {check["id"] for check in plan["requiredChecks"]}
    assert "transition-trajectory" not in ids
    assert "video-motion-compare" not in ids


def test_framer_motion_library_identity_does_not_set_scroll_scrub(tmp_path: Path) -> None:
    """A general animation library is not evidence of scroll-linked motion."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({
        "libraries": ["framer-motion"],
    }))

    plan = _run_verification_plan(ref)

    assert plan["signals"]["hasScrollScrub"] is False
    assert "video-motion-compare" not in {
        check["id"] for check in plan["requiredChecks"]
    }


def test_framer_motion_with_use_scroll_sets_scroll_scrub(tmp_path: Path) -> None:
    """A scroll-specific Framer Motion construction token remains eligible."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({
        "libraries": ["framer-motion"],
        "constructionTokens": ["useScroll"],
    }))

    plan = _run_verification_plan(ref)

    assert plan["signals"]["hasScrollScrub"] is True
    assert "video-motion-compare" in {
        check["id"] for check in plan["requiredChecks"]
    }


def test_gsap_scrolltrigger_library_identity_sets_scroll_scrub(tmp_path: Path) -> None:
    """The ScrollTrigger-specific GSAP identity is itself scroll evidence."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({
        "libraries": ["gsap-scrolltrigger"],
    }))

    plan = _run_verification_plan(ref)

    assert plan["signals"]["hasScrollScrub"] is True


def test_placeholder_transition_spec_cannot_self_sustain_motion_signals(tmp_path: Path) -> None:
    """An auto placeholder spec and its derived coverage are one stale signal,
    not two independent observations that can keep regenerating each other.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "placeholder": True,
        "transitions": [
            {
                "id": "auto-scroll",
                "trigger": "scroll",
                "target": ".auto-scroll",
                "animation": {
                    "type": "scroll-linked",
                    "mechanism": "useScroll + setTimeout",
                },
            },
            {"id": "auto-io", "trigger": "intersection", "target": ".auto-io"},
            {"id": "auto-hover", "trigger": "hover", "target": ".auto-hover"},
        ],
    }))
    (ref / "transition-coverage.json").write_text(json.dumps({
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "animatedElements": [
            {
                "selector": ".auto-scroll",
                "trigger": "scroll",
                "decoded": {"source": "transition-spec.json"},
            }
        ],
        "derivedFrom": ["transition-spec.json", "section-map.json"],
    }))

    plan = _run_verification_plan(ref)

    assert plan["signals"]["hasScrollScrub"] is False
    assert plan["signals"]["hasScrollStateMachine"] is False
    assert plan["signals"]["hasIOReveal"] is False
    assert plan["signals"]["hasHover"] is False
    assert "video-motion-compare" not in {
        check["id"] for check in plan["requiredChecks"]
    }


def test_real_transition_spec_remains_eligible_for_motion_dispatch(tmp_path: Path) -> None:
    """Authored specs remain valid for scroll/IO signals; hover stays live-only."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "schemaVersion": 1,
        "source": "bundle-analysis",
        "placeholder": False,
        "transitions": [
            {
                "id": "authored-scroll",
                "trigger": "scroll",
                "target": ".authored-scroll",
                "animation": {
                    "type": "scroll-linked transform",
                    "mechanism": "scrollYProgress + setTimeout",
                },
            },
            {"id": "authored-io", "trigger": "intersection", "target": ".authored-io"},
            {"id": "authored-hover", "trigger": "hover", "target": ".authored-hover"},
        ],
    }))

    plan = _run_verification_plan(ref)

    assert plan["signals"]["hasScrollScrub"] is True
    assert plan["signals"]["hasScrollStateMachine"] is True
    assert plan["signals"]["hasIOReveal"] is True
    assert plan["signals"]["hasHover"] is False
    assert "video-motion-compare" in {
        check["id"] for check in plan["requiredChecks"]
    }


def test_scroll_state_machine_ignores_null_runtime_scrolltrigger(
    tmp_path: Path,
) -> None:
    """A runtime dump key with null value is not construction evidence.

    Real sites can have animation-runtime-dump.json with `"scrollTrigger": null`
    plus generic framework `setTimeout` calls in bundles. That must not satisfy
    the progress+controller pair for hasScrollStateMachine.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "animation-runtime-dump.json").write_text(json.dumps({
        "scrollTrigger": None,
        "scrollLinkedStyles": None,
    }))
    bundles = ref / "bundles"
    bundles.mkdir()
    (bundles / "framework.js").write_text("setTimeout(function(){}, 0)")

    plan = _run_verification_plan(ref)

    assert plan["signals"]["hasScrollStateMachine"] is False


def test_scroll_state_machine_accepts_non_null_runtime_scrolltrigger(
    tmp_path: Path,
) -> None:
    """A populated runtime ScrollTrigger dump remains scroll progress evidence."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "animation-runtime-dump.json").write_text(json.dumps({
        "scrollTrigger": [{"trigger": ".hero", "start": 0, "end": 600}],
    }))
    bundles = ref / "bundles"
    bundles.mkdir()
    (bundles / "controller.js").write_text("setTimeout(function(){}, 0)")

    plan = _run_verification_plan(ref)

    assert plan["signals"]["hasScrollStateMachine"] is True


def test_observed_scroll_motion_via_element_tracking_sets_scroll_scrub(tmp_path: Path) -> None:
    """element-tracking.json shows an element whose transform changes across two
    scroll positions — observed motion, no library token anywhere. hasScrollScrub
    must be true.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
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
    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasScrollScrub"] is True, (
        "cross-position transform change must set hasScrollScrub"
    )


def test_viewport_relative_top_change_does_not_set_scroll_scrub(tmp_path: Path) -> None:
    """A static element's bounding-client-rect top changes as the viewport scrolls.

    That coordinate alone is not motion evidence: its document-relative top
    remains stable after adding scrollY.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "scroll-engine.json").write_text(json.dumps({}))
    (ref / "element-tracking.json").write_text(json.dumps([
        {"scrollY": 0, "scrollPct": 0, "elements": [
            {"selector": "div.static", "inViewport": True, "top": 500,
             "transform": None, "opacity": None, "scale": None,
             "clipPath": None, "position": None},
        ]},
        {"scrollY": 400, "scrollPct": 50, "elements": [
            {"selector": "div.static", "inViewport": True, "top": 100,
             "transform": None, "opacity": None, "scale": None,
             "clipPath": None, "position": None},
        ]},
    ]))

    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasScrollScrub"] is False
    assert "video-motion-compare" not in {
        check["id"] for check in plan["requiredChecks"]
    }


def test_subpixel_document_top_drift_does_not_set_scroll_scrub(tmp_path: Path) -> None:
    """Fractional layout/measurement noise is not positional animation."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "scroll-engine.json").write_text(json.dumps({}))
    (ref / "element-tracking.json").write_text(json.dumps([
        {"scrollY": 0, "scrollPct": 0, "elements": [
            {"selector": "div.static", "inViewport": True, "top": 500.0,
             "transform": None, "opacity": None, "scale": None,
             "clipPath": None, "position": None},
        ]},
        {"scrollY": 400, "scrollPct": 50, "elements": [
            {"selector": "div.static", "inViewport": True, "top": 100.25,
             "transform": None, "opacity": None, "scale": None,
             "clipPath": None, "position": None},
        ]},
    ]))

    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasScrollScrub"] is False


def test_document_relative_top_change_sets_scroll_scrub(tmp_path: Path) -> None:
    """A top change beyond viewport displacement remains positional motion."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "scroll-engine.json").write_text(json.dumps({}))
    (ref / "element-tracking.json").write_text(json.dumps([
        {"scrollY": 0, "scrollPct": 0, "elements": [
            {"selector": "div.animated", "inViewport": True, "top": 500,
             "transform": None, "opacity": None, "scale": None,
             "clipPath": None, "position": None},
        ]},
        {"scrollY": 400, "scrollPct": 50, "elements": [
            {"selector": "div.animated", "inViewport": True, "top": 50,
             "transform": None, "opacity": None, "scale": None,
             "clipPath": None, "position": None},
        ]},
    ]))

    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasScrollScrub"] is True


def test_opacity_change_still_sets_scroll_scrub(tmp_path: Path) -> None:
    """Non-positional property changes remain direct observed-motion evidence."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "scroll-engine.json").write_text(json.dumps({}))
    (ref / "element-tracking.json").write_text(json.dumps([
        {"scrollY": 0, "scrollPct": 0, "elements": [
            {"selector": "div.fade", "inViewport": True, "top": 500,
             "transform": None, "opacity": "0", "scale": None,
             "clipPath": None, "position": None},
        ]},
        {"scrollY": 400, "scrollPct": 50, "elements": [
            {"selector": "div.fade", "inViewport": True, "top": 100,
             "transform": None, "opacity": None, "scale": None,
             "clipPath": None, "position": None},
        ]},
    ]))

    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasScrollScrub"] is True


def test_observed_io_reveal_via_animations_detected_sets_io_reveal(tmp_path: Path) -> None:
    """animations-detected.json carries a non-empty textReveals list — observed
    reveal-on-enter motion, no IntersectionObserver token in any allowlist
    source. hasIOReveal must be true so reveal-trigger fires.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "scroll-engine.json").write_text(json.dumps({}))
    (ref / "animations-detected.json").write_text(json.dumps({
        "scrollAnimations": [],
        "textReveals": [
            {"selector": "h2.reveal", "type": "text-reveal"}
        ],
    }))
    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasIOReveal"] is True, (
        "non-empty textReveals must set hasIOReveal"
    )


def test_boolean_data_attr_css_terminal_motion_sets_io_reveal(tmp_path: Path) -> None:
    """Boolean viewport state captured in structure + same-node CSS motion is an
    IO reveal signal even when no JS/bundle token names IntersectionObserver.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "main",
        "children": [
            {
                "tag": "div",
                "class": "card",
                "data-in-view": "false",
                "styles": {"--index": "0"},
                "children": [],
            }
        ],
    }))
    css = ref / "ref-css"
    css.mkdir()
    (css / "motion.css").write_text(
        """
        .card {
          transition: transform .75s ease;
        }
        .card[data-in-view=true] {
          transform: translate(10px, 20px);
        }
        """,
        encoding="utf-8",
    )

    plan = _run_verification_plan(ref)

    assert plan["signals"]["hasIOReveal"] is True
    ids = [check["id"] for check in plan["requiredChecks"]]
    assert "reveal-trigger" in ids


def test_boolean_data_attr_css_ancestor_motion_is_not_same_node_reveal(
    tmp_path: Path,
) -> None:
    """The boolean state must live on the rightmost motion subject. An
    ancestor state that moves a child is not same-node reveal evidence.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "main",
        "children": [{
            "tag": "div",
            "class": "card",
            "data-in-view": "false",
            "children": [{"tag": "span", "class": "child", "children": []}],
        }],
    }))
    css = ref / "ref-css"
    css.mkdir()
    (css / "motion.css").write_text(
        ".card { transition: transform .75s ease; }\n"
        ".card[data-in-view=true] .child { transform: translateY(20px); }\n",
        encoding="utf-8",
    )

    plan = _run_verification_plan(ref)

    assert plan["signals"]["hasIOReveal"] is False


def test_boolean_data_attr_css_requires_all_subject_classes(tmp_path: Path) -> None:
    """A captured `.card` does not satisfy `.card.active`; class intersection
    alone would dispatch a reveal check for a state the node never has.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "main",
        "children": [{
            "tag": "div",
            "class": "card",
            "data-in-view": "false",
            "children": [],
        }],
    }))
    css = ref / "ref-css"
    css.mkdir()
    (css / "motion.css").write_text(
        ".card { transition: transform .75s ease; }\n"
        ".card.active[data-in-view=true] { transform: translateY(20px); }\n",
        encoding="utf-8",
    )

    plan = _run_verification_plan(ref)

    assert plan["signals"]["hasIOReveal"] is False


def test_data_in_view_css_state_without_captured_boolean_motion_is_not_io_reveal(
    tmp_path: Path,
) -> None:
    """A CSS selector alone must not reintroduce the old data-in-view
    false-positive; structure state and same-node motion evidence are required.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "main",
        "children": [
            {"tag": "div", "class": "card", "children": []}
        ],
    }))
    css = ref / "ref-css"
    css.mkdir()
    (css / "static.css").write_text(
        ".card[data-in-view=true]{color:red}",
        encoding="utf-8",
    )

    plan = _run_verification_plan(ref)

    assert plan["signals"]["hasIOReveal"] is False
    ids = [check["id"] for check in plan["requiredChecks"]]
    assert "reveal-trigger" not in ids


def test_no_false_dispatch_on_fully_static_ref(tmp_path: Path) -> None:
    """Fix-not-loosen guard: a genuinely static page — no observed motion in any
    behavioral artifact and no allowlist token — must keep hasScrollScrub and
    hasIOReveal false (no false dispatch of expensive motion checks).
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "scroll-engine.json").write_text(json.dumps({}))
    # Behavioral artifacts present but show NO motion.
    (ref / "transition-coverage.json").write_text(json.dumps({
        "animatedElements": [], "staticElements": [{"selector": "div.static"}],
    }))
    (ref / "animations-detected.json").write_text(json.dumps({
        "scrollAnimations": [], "textReveals": [],
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
    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasScrollScrub"] is False, (
        "static page must not set hasScrollScrub"
    )
    assert plan["signals"]["hasIOReveal"] is False, (
        "static page must not set hasIOReveal"
    )
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "video-motion-compare" not in ids, (
        f"video-motion must not dispatch on static page: {ids}"
    )


def test_observed_carousel_via_auto_timers_sets_swiper(tmp_path: Path) -> None:
    """Fix B — unknown / hand-rolled carousel: NO Swiper name token anywhere,
    but animations-detected.json recorded an auto-rotating timer (Embla/Splide/
    keen-slider/hand-rolled all surface as an observed autoTimer). The periodic
    transform change WAS OBSERVED, so hasSwiper must be true and swiper-runtime
    must dispatch — driven off observation, not the Swiper class grep.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "animations-detected.json").write_text(json.dumps({
        "scrollAnimations": [],
        "textReveals": [],
        "autoTimers": [
            {"selector": ".embla__container", "interval_ms": 4000, "type": "slideshow"}
        ],
    }))
    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasSwiper"] is True, (
        "observed auto-rotating carousel must set hasSwiper even with no Swiper "
        "name token"
    )
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "swiper-runtime" in ids, f"swiper-runtime row missing: {ids}"


def test_observed_canvas_vector_player_sets_lottie(tmp_path: Path) -> None:
    """Fix B — unknown vector player (Rive .riv / custom JSON-on-canvas): NO
    lottie/bodymovin/dotlottie name token anywhere, but a <canvas> surface is
    present AND it was OBSERVED advancing frames (idle auto-timer on the canvas
    region). hasLottie must be true so the runtime gate (real runtime + asset)
    dispatches — a static SVG/canvas freehand fails it, which is the point.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "canvas-webgl-detection.json").write_text(json.dumps({
        "hasCanvas": True, "hasWebGL": False, "primaryRenderType": "canvas",
    }))
    (ref / "animations-detected.json").write_text(json.dumps({
        "scrollAnimations": [],
        "textReveals": [],
        "autoTimers": [
            {"selector": "canvas.hero-vector", "interval_ms": 0, "type": "css-animation"}
        ],
    }))
    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasLottie"] is True, (
        "canvas surface observed advancing frames must set hasLottie even with "
        "no lottie/bodymovin name token"
    )
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "lottie-runtime" in ids, f"lottie-runtime row missing: {ids}"


def test_no_false_swiper_lottie_dispatch_on_static_ref(tmp_path: Path) -> None:
    """Fix-not-loosen guard for Fix B: a genuinely static page — no autoTimers,
    no canvas, no name token — must keep hasSwiper and hasLottie false so the
    swiper/lottie runtime gates do not false-dispatch.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "canvas-webgl-detection.json").write_text(json.dumps({
        "hasCanvas": False, "hasWebGL": False,
    }))
    (ref / "animations-detected.json").write_text(json.dumps({
        "scrollAnimations": [], "textReveals": [], "autoTimers": [],
    }))
    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasSwiper"] is False, "static page must not set hasSwiper"
    assert plan["signals"]["hasLottie"] is False, "static page must not set hasLottie"
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "swiper-runtime" not in ids, f"swiper-runtime must not dispatch: {ids}"
    assert "lottie-runtime" not in ids, f"lottie-runtime must not dispatch: {ids}"


def test_verification_plan_does_not_use_bash_heredocs() -> None:
    """Homebrew Bash can deadlock while delivering heredocs to child processes.

    verification-plan.sh runs before many tests and gates, so keep embedded
    Python, JavaScript, and JSON bodies in helper files or printf blocks.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    text = script.read_text(encoding="utf-8")
    assert "<<" not in text, "verification-plan.sh must not use Bash heredocs"
def test_bench_verification_smoke_markdown() -> None:
    """bench-verification.sh --repeat=1 must exit 0 and emit the expected
    markdown header + the three named fixtures.

    Locks in that fixture writes + run_*_bench callers + median calc stay in
    sync. A bash-3.2-incompatible construct (e.g. associative arrays) regresses
    this test on macOS default bash.
    """
    import subprocess

    script = _project_root() / "scripts" / "ci" / "bench-verification.sh"
    proc = subprocess.run(
        ["bash", str(script), "--repeat=1"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"bench failed: {proc.stdout}\n{proc.stderr}"
    out = proc.stdout
    assert "# verification dispatch bench" in out
    assert "verification-plan.sh" in out
    for fixture in ("empty", "hover-only", "all-signals"):
        assert fixture in out, f"missing fixture '{fixture}' in output:\n{out}"
    for gate in ("spec-implementation-coverage", "runtime-spec-coverage"):
        assert gate in out, f"missing gate '{gate}' in output:\n{out}"



def test_bench_verification_json_mode_is_valid_json() -> None:
    """--json mode must produce a JSON object with the documented top-level
    keys (verificationPlan, specImplementationCoverage, runtimeSpecCoverage).

    Locks in the JSON contract for any CI consumer that wants to assert on
    a regression threshold against a stored baseline.
    """
    import subprocess

    script = _project_root() / "scripts" / "ci" / "bench-verification.sh"
    env = os.environ.copy()
    env.pop("BASH_COMPAT", None)
    proc = subprocess.run(
        ["bash", str(script), "--repeat=1", "--json"],
        env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"bench --json failed: {proc.stdout}\n{proc.stderr}"
    data = json.loads(proc.stdout)
    assert set(data.keys()) >= {"repeat", "verificationPlan", "specImplementationCoverage", "runtimeSpecCoverage"}
    assert set(data["verificationPlan"].keys()) == {"empty", "hoverOnly", "allSignals"}
    for fixture_block in data["verificationPlan"].values():
        assert set(fixture_block.keys()) == {"quick", "standard", "comprehensive"}
        for tier_block in fixture_block.values():
            assert "medianMs" in tier_block and "checkCount" in tier_block
    # accuracy column should report "ok" on a clean tree — the unit tests
    # already lock in the pass/fail exit codes the bench fixtures depend on.
    assert data["specImplementationCoverage"]["accuracy"] == "ok"
    assert data["runtimeSpecCoverage"]["accuracy"] == "ok"


def test_bench_verification_self_sets_bash5_compat_guard() -> None:
    """Regression — Bash 5.1+ can deadlock nested heredoc writers in this
    parallel bench unless it self-selects Bash 5.0 heredoc behavior. The runtime
    JSON smoke above clears inherited BASH_COMPAT; this locks in the guard.
    """
    script = _project_root() / "scripts" / "ci" / "bench-verification.sh"
    text = script.read_text(encoding="utf-8")

    assert "BASH_VERSINFO" in text
    assert "${BASH_COMPAT+x}" in text
    assert "BASH_COMPAT=5.0" in text
    assert "export BASH_COMPAT" in text



def test_bench_verification_rejects_bad_repeat() -> None:
    """--repeat must be a positive odd integer (median picks middle element).
    Even or non-numeric values must exit 2 with a clear message — otherwise
    a typo silently coerces to whatever bash's arithmetic returns and the
    median calc breaks in non-obvious ways.
    """
    import subprocess

    script = _project_root() / "scripts" / "ci" / "bench-verification.sh"
    for bad in ("2", "0", "abc"):
        proc = subprocess.run(
            ["bash", str(script), f"--repeat={bad}"],
            capture_output=True, text=True, timeout=15,
        )
        assert proc.returncode == 2, f"--repeat={bad} should exit 2, got {proc.returncode}: {proc.stderr}"
        assert "repeat" in proc.stderr.lower()
