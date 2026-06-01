import json
from pathlib import Path

from ._helpers import (
    _project_root,
    _run_verification_plan,
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
    proc = subprocess.run(
        ["bash", str(script), "--repeat=1", "--json"],
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
