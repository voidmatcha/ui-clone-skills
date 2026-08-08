"""Tests for scripts/extract/_bundle_extraction.py — the Python parser
behind bundle-extraction.sh."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_module() -> ModuleType:
    """Load scripts/extract/_bundle_extraction.py as a module.

    scripts/extract/ is not a Python package (no __init__.py) so we use
    importlib.util to load the file by path. Caches in sys.modules under
    a unique key.
    """
    key = "_bundle_extraction_test_module"
    if key in sys.modules:
        return sys.modules[key]
    path = _project_root() / "scripts" / "extract" / "_bundle_extraction.py"
    spec = importlib.util.spec_from_file_location(key, str(path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def test_parse_bundles_empty_when_no_bundles_dir(tmp_path: Path) -> None:
    """Missing bundles/ subdirectory → schema-stable empty plan."""
    mod = _load_module()
    plan = mod.parse_bundles(tmp_path)
    assert plan["schemaVersion"] == 1
    assert plan["bundlesScanned"] == 0
    assert plan["totalSizeKB"] == 0
    assert plan["extractions"] == {}
    assert plan["unresolved"] == []


def test_parse_bundles_detects_lenis_options(tmp_path: Path) -> None:
    """A `new Lenis({...})` site is captured with parsed option keys."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "main.js").write_text(
        "const s=new Lenis({ lerp: 0.1, smoothWheel: true, duration: 1.2 });"
    )
    plan = mod.parse_bundles(tmp_path)
    assert plan["bundlesScanned"] == 1
    lenis = plan["extractions"].get("lenis")
    assert lenis, f"expected lenis extractions, got {plan['extractions']}"
    assert lenis[0]["source"] == "bundles/main.js"
    opts = lenis[0]["options"]
    assert "lerp" in opts and opts["lerp"].startswith("0.1")
    assert "smoothWheel" in opts
    assert "duration" in opts
    assert lenis[0]["confidence"] == "high"


def test_parse_bundles_detects_gsap_scrolltrigger(tmp_path: Path) -> None:
    """A `ScrollTrigger.create({...})` site is captured as kind=scrollTrigger."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "anim.js").write_text(
        "ScrollTrigger.create({ trigger: '#hero', start: 'top top', end: '+=500', scrub: true });"
    )
    plan = mod.parse_bundles(tmp_path)
    gsap = plan["extractions"].get("gsap")
    assert gsap, f"expected gsap extractions, got {plan['extractions']}"
    kinds = [c["kind"] for c in gsap]
    assert "scrollTrigger" in kinds, f"expected scrollTrigger in {kinds}"


def test_main_writes_json_artifact(tmp_path: Path) -> None:
    """End-to-end: main(argv) writes a valid JSON file at the requested path."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "a.js").write_text("anime({ targets: '.x', duration: 800 });")
    out_path = tmp_path / "bundle-extraction.json"
    rc = mod.main([str(tmp_path), str(out_path)])
    assert rc == 0
    assert out_path.is_file()
    data = json.loads(out_path.read_text())
    assert data["schemaVersion"] == 1
    assert "animeJs" in data["extractions"]


def test_main_rejects_missing_args() -> None:
    """Insufficient argv → exit code 2 (usage error), prints to stderr."""
    mod = _load_module()
    assert mod.main([]) == 2
    assert mod.main(["only-one"]) == 2


@pytest.fixture
def fixture_ref_dir(tmp_path: Path) -> Path:
    """Realistic bundle fixture with Lenis + GSAP + Framer Motion + Webflow IX2."""
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "vendor.js").write_text(
        "const lenis = new Lenis({ lerp: 0.07, smoothWheel: true });\n"
        "gsap.timeline({ paused: true });\n"
        "ScrollTrigger.create({ trigger: '#sec', start: 'top center', scrub: 1 });\n"
        "function useScroll(opts){} function useTransform(v,a,b){}\n"
        "useScroll({ offset: ['start end', 'end start'] });\n"
        "useTransform(scrollY, [0, 1], [0, 100]);\n"
    )
    (bundles / "webflow.js").write_text(
        "[{actionTypeId: 'TRANSFORM_MOVE', target: '#a'},"
        "{actionTypeId: 'STYLE_OPACITY', target: '#b'}]"
    )
    return tmp_path


def test_parse_bundles_integration(fixture_ref_dir: Path) -> None:
    """Realistic two-bundle fixture: each library family detected."""
    mod = _load_module()
    plan = mod.parse_bundles(fixture_ref_dir)
    assert plan["bundlesScanned"] == 2
    libs = set(plan["extractions"].keys())
    assert "lenis" in libs
    assert "gsap" in libs
    assert "framerMotion" in libs
    assert "webflowIX2" in libs
    ix2 = plan["extractions"]["webflowIX2"]
    assert ix2["totalActions"] == 2
    # Lenis/GSAP/Framer/Webflow are fully regex-parsed → no unresolved gap flag.
    assert plan["unresolved"] == []


def test_parse_bundles_flags_swiper_as_unresolved(tmp_path: Path) -> None:
    """A1 dispatch-on-gap: Swiper carousel config (nested breakpoint maps) is not
    regex-extractable, so the parser flags its PRESENCE under `unresolved` for the
    bundle-analyzer LLM instead of silently shipping a config-less carousel."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "slider.js").write_text(
        'var s=new Swiper(".swiper",'
        "{slidesPerView:1,breakpoints:{768:{slidesPerView:3}},loop:true});"
        'e.createElement("div",{className:"swiper-slide"});'
    )
    plan = mod.parse_bundles(tmp_path)
    # Presence flagged, but no construction-site config was guessed.
    assert "swiper" not in plan["extractions"]
    swiper = next(u for u in plan["unresolved"] if u["library"] == "swiper")
    assert swiper["source"] == "bundles/slider.js"
    assert "bundle-analyzer" in swiper["reason"]


def test_parse_bundles_flags_splide_via_dom_class_marker(tmp_path: Path) -> None:
    """Splide presence flagged from the stable `splide__track` DOM class literal
    even when the minified constructor name is mangled away."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "carousel.js").write_text(
        'r.createElement("div",{className:"splide__track"},'
        'r.createElement("ul",{className:"splide__list"}))'
    )
    plan = mod.parse_bundles(tmp_path)
    libs = [u["library"] for u in plan["unresolved"]]
    assert libs == ["splide"]
    assert plan["unresolved"][0]["source"] == "bundles/carousel.js"


def test_extract_framer_minified_scroll_scrub(tmp_path: Path) -> None:
    """Minified Framer useScroll/useTransform (mangled hook names) must still be
    extracted by anchoring on stable API literals, and the bound property must be
    resolved through a useSpring hop — the signature of a scroll-scrubbed scale
    (background zoom). Mirrors the real realfood minified shape."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    # Mangled identifiers: useScroll=(0,o.L), useTransform=(0,s.G), useSpring=(0,l.z)
    (bundles / "page.js").write_text(
        'function eZ(){let F=(0,i.useRef)(null),'
        '{scrollYProgress:C}=(0,o.L)({target:F,offset:["start end","end start"]}),'
        'E=(0,s.G)(C,k?[0,.05,.75,.9]:[0,.1,.75,.9],[.9,1,1,1]),'
        'S=(0,l.z)(E,{stiffness:120,damping:30}),'
        'h=(0,s.G)(C,[0,.3],[.4,1]);'
        'return(0,n.jsx)(d.P.div,{style:{scale:S,opacity:h}})}'
    )
    plan = mod.parse_bundles(tmp_path)
    fm = plan["extractions"]["framerMotion"]
    scroll_sites = [x for x in fm if x["kind"] == "useScroll"]
    assert len(scroll_sites) == 1
    site = scroll_sites[0]
    assert site["target"] == "F"
    assert site["offset"] == '["start end","end start"]'
    props = {t["property"] for t in site["transforms"]}
    # scale resolved through the useSpring hop; opacity resolved directly
    assert "scale" in props
    assert "opacity" in props
    scale_t = next(t for t in site["transforms"] if t["property"] == "scale")
    assert scale_t["output"] == "[.9,1,1,1]"  # band straddling 1.0 = the zoom


def test_hover_size_expansion_extracted(tmp_path: Path) -> None:
    """Loop-9 regression class (item 6b): the nav pill label expansion
    (initial:{width:0} → animate:{width:active?"auto":0}, spring) lived only
    in the JS bundle and never reached a spec entry — so its absence in the
    impl (labels baked width:0, no hover expansion) was never verified.
    The extractor must surface size-expansion components."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "page-x.js").write_text(
        '(0,n.jsx)(c.P.span,{className:O().label_container,layout:!0,'
        'initial:{width:0},animate:{width:a?"auto":0},'
        'transition:{type:"spring",stiffness:120,damping:20},'
        'children:(0,n.jsx)(c.P.span,{className:O().label})});'
        'let m={pill:"nav_pill__LWSDc",dot_button:"nav_dot_button__kZB4V",'
        'label_container:"nav_label_container__okVKb"};',
        encoding="utf-8",
    )
    plan = mod.parse_bundles(tmp_path)
    expansions = plan["extractions"].get("hoverSizeExpansions")
    assert expansions, plan["extractions"].keys()
    entry = expansions[0]
    assert entry["classToken"] == "label_container"
    assert entry["property"] == "width"
    assert entry["from"] == "0"
    assert "auto" in entry["to"]
    assert "spring" in str(entry.get("transition", ""))
    # the class-map resolution turns the token into the concrete class name
    assert entry.get("resolvedClassName") == "nav_label_container__okVKb"


def test_hover_size_expansion_max_width_variant(tmp_path: Path) -> None:
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "chunk.js").write_text(
        'x.jsx(M.div,{className:s().menu_label,initial:{maxWidth:0},'
        'animate:{maxWidth:h?160:0},transition:{duration:.3}})',
        encoding="utf-8",
    )
    plan = mod.parse_bundles(tmp_path)
    expansions = plan["extractions"].get("hoverSizeExpansions")
    assert expansions and expansions[0]["property"] == "maxWidth"


def test_active_state_expansion_extracted(tmp_path: Path) -> None:
    """Loop-10/11 (item 7): the nav active-section label reveal lives in the
    bundle as initial:{width:0} -> animate:{width:<activeFlag>?"auto":0}. It is
    gated on a state flag (active section), not a hover, so the hover gate cannot
    verify it — and real minified state-machine code interleaves layout props
    between className and initial, which the hover extractor's adjacency regex
    misses. The active-state extractor must still surface it."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    # className is separated from initial by an interleaved object literal, so
    # the hover adjacency regex would miss it.
    (bundles / "nav.js").write_text(
        '(0,n.jsx)(c.P.span,{className:O().label_container,'
        'style:{color:"#111"},layout:!0,transition:{type:"spring"},'
        'initial:{width:0},animate:{width:a?"auto":0}});'
        'let m={label_container:"nav_label_container__okVKb"};',
        encoding="utf-8",
    )
    plan = mod.parse_bundles(tmp_path)
    active = plan["extractions"].get("activeStateExpansions")
    assert active, plan["extractions"].keys()
    entry = active[0]
    assert entry["property"] == "width"
    assert entry["stateFlag"] == "a"
    assert entry["to"] == "auto"
    assert entry["resolvedClassName"] == "nav_label_container__okVKb"


def test_no_expansions_no_key(tmp_path: Path) -> None:
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "plain.js").write_text("console.log(1)", encoding="utf-8")
    plan = mod.parse_bundles(tmp_path)
    assert "hoverSizeExpansions" not in plan["extractions"]
