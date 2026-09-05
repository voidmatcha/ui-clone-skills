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


def test_parse_bundles_captures_nested_scrolltrigger_tween(tmp_path: Path) -> None:
    """The canonical scroll-driven form nests its config:
    `gsap.to(target, {y, scrollTrigger: {...}})`. A flat-brace pattern cannot
    match it, so these sites were skipped and the spec came out empty."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "anim.js").write_text(
        'gsap.to(e,{y:-100,scrollTrigger:{trigger:e,start:"top bottom",scrub:1}});'
    )
    gsap = mod.parse_bundles(tmp_path)["extractions"].get("gsap")

    assert gsap, "nested scrollTrigger tween must be captured"
    entry = gsap[0]
    assert entry["kind"] == "tween"
    assert entry["scrollLinked"] is True
    assert "scrollTrigger" in entry["config"][0]
    assert "scrub:1" in entry["config"][0]


def test_parse_bundles_captures_fromto_second_object(tmp_path: Path) -> None:
    """fromTo(target, fromVars, toVars) carries scrollTrigger in the TO object,
    so reading only the first object loses the scroll linkage."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "anim.js").write_text(
        "gsap.fromTo(n,{autoAlpha:0},{autoAlpha:1,scrollTrigger:{trigger:n,scrub:!0}});"
    )
    gsap = mod.parse_bundles(tmp_path)["extractions"].get("gsap")

    assert gsap, "fromTo site must be captured"
    entry = gsap[0]
    assert len(entry["config"]) == 2, entry["config"]
    assert "autoAlpha:0" in entry["config"][0]
    assert "scrollTrigger" in entry["config"][1]
    assert entry["scrollLinked"] is True


def test_parse_bundles_captures_timeline_nested_defaults(tmp_path: Path) -> None:
    """`gsap.timeline({defaults:{...}})` previously matched with an EMPTY
    capture group, recording a call site that carried no parameters."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "anim.js").write_text(
        'gsap.timeline({defaults:{ease:"none"},scrollTrigger:{trigger:".hero",scrub:!0}});'
    )
    gsap = mod.parse_bundles(tmp_path)["extractions"].get("gsap")

    assert gsap
    entry = gsap[0]
    assert entry["kind"] == "timeline"
    assert entry["config"], "timeline config must not be empty"
    assert 'ease:"none"' in entry["config"][0]
    assert entry["scrollLinked"] is True


def test_parse_bundles_gsap_object_scan_respects_string_literals(tmp_path: Path) -> None:
    """A brace inside a quoted value must not unbalance the scan."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "anim.js").write_text('gsap.to(e,{ease:"a}b",y:{v:1}});')
    gsap = mod.parse_bundles(tmp_path)["extractions"].get("gsap")

    assert gsap
    assert gsap[0]["config"][0] == '{ease:"a}b",y:{v:1}}'


def test_parse_bundles_gsap_bare_timeline_is_low_confidence(tmp_path: Path) -> None:
    """`gsap.timeline()` with no config carries no parameters, so it must not
    be reported at the same confidence as a site that parsed one."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "anim.js").write_text("var t=gsap.timeline();")
    gsap = mod.parse_bundles(tmp_path)["extractions"].get("gsap")

    assert gsap
    assert gsap[0]["config"] == []
    assert gsap[0]["confidence"] == "low"


def test_parse_bundles_captures_anime_keyframe_arrays(tmp_path: Path) -> None:
    """anime keyframes are arrays of objects, so the config cannot be read with
    a flat brace pattern — the whole construction site was skipped."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "anim.js").write_text(
        'anime({targets:".x",translateY:[{value:0},{value:100}],duration:800});'
    )
    anime = mod.parse_bundles(tmp_path)["extractions"].get("animeJs")

    assert anime, "anime keyframe site must be captured"
    assert "translateY" in anime[0]["config"]
    assert "value:100" in anime[0]["config"]


def test_parse_bundles_skips_anime_call_without_object(tmp_path: Path) -> None:
    """`anime(x)` passes a variable, not a config — it carries no parameters
    and must not be reported as a construction site."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "anim.js").write_text("var q=anime(x);")
    anime = mod.parse_bundles(tmp_path)["extractions"].get("animeJs")

    assert not anime, anime


def test_parse_bundles_captures_lenis_with_nested_options(tmp_path: Path) -> None:
    """A nested option group (`prevent:{wheel:!0}`) previously made the whole
    Lenis constructor site unmatchable."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "scroll.js").write_text("new Lenis({lerp:.1,prevent:{wheel:!0}});")
    lenis = mod.parse_bundles(tmp_path)["extractions"].get("lenis")

    assert lenis, "nested Lenis config must still be captured"
    assert lenis[0]["options"].get("lerp") == ".1"


def test_parse_bundles_captures_minified_gsap_binding(tmp_path: Path) -> None:
    """Bundlers rename the imported `gsap` binding, so production call sites read
    `o.timeline({...})`. Requiring the literal prefix found only the library's own
    internals and missed every application tween."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "page.js").write_text(
        'i=o.timeline({scrollTrigger:{trigger:e,start:()=>"0% 0%",scrub:!0}});'
        'r.to(m,{y:"10px",duration:.6,ease:"power3.out"});'
    )
    gsap = mod.parse_bundles(tmp_path)["extractions"].get("gsap")

    assert gsap, "aliased GSAP call sites must be captured"
    kinds = sorted(g["kind"] for g in gsap)
    assert kinds == ["timeline", "tween"], kinds
    timeline = next(g for g in gsap if g["kind"] == "timeline")
    assert timeline["binding"] == "o"
    assert timeline["scrollLinked"] is True
    assert "scrub:!0" in timeline["config"][0]


def test_parse_bundles_captures_minified_scrolltrigger_create(tmp_path: Path) -> None:
    """ScrollTrigger.create through a renamed binding still describes a scroll
    range, so the trigger/start/end keys identify it."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "page.js").write_text(
        'f=w.create({trigger:e,start:()=>"0 50%",end:()=>"+=1",onEnter:()=>{}});'
    )
    gsap = mod.parse_bundles(tmp_path)["extractions"].get("gsap")

    assert gsap
    assert gsap[0]["kind"] == "scrollTrigger"
    assert gsap[0]["binding"] == "w"
    assert gsap[0]["scrollLinked"] is True


def test_parse_bundles_ignores_unrelated_to_calls(tmp_path: Path) -> None:
    """A `.to()` on an unrelated object carries no GSAP option keys and must not
    be reported — the alias match is confirmed by config, not by name alone."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "misc.js").write_text(
        'db.to({table:"users",where:{id:1}});'
        'r.to(x,{limit:10,offset:0});'
        "q.timeline();"
    )
    gsap = mod.parse_bundles(tmp_path)["extractions"].get("gsap")

    assert not gsap, gsap


def test_parse_bundles_marks_framer_scroll_hooks_scroll_linked(tmp_path: Path) -> None:
    """useScroll binds element progress to scroll position, so the spec must
    declare it — spec-bundle-site-coverage keys on sourceId + scrollLinked."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "page.js").write_text(
        "const {scrollYProgress: p} = useScroll({target: ref, offset: ['start end']});"
    )
    rows = mod.parse_bundles(tmp_path)["extractions"].get("framerMotion")

    assert rows, "useScroll site must be captured"
    hook = next(r for r in rows if r["kind"] == "useScroll")
    assert hook["scrollLinked"] is True
    assert hook["sourceId"].startswith("framer:page.js:")


def test_parse_bundles_marks_use_in_view_not_scroll_linked(tmp_path: Path) -> None:
    """useInView is an intersection reveal, covered by the IO signal rather than
    by scroll-linked site coverage."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "page.js").write_text("useInView(ref, {margin: '0px', amount: 0.5});")
    rows = mod.parse_bundles(tmp_path)["extractions"].get("framerMotion")

    assert rows
    hook = next(r for r in rows if r["kind"] == "useInView")
    assert hook["scrollLinked"] is False
    assert hook["sourceId"].startswith("framer:page.js:")


def test_parse_bundles_gsap_site_ids_are_stable_across_runs(tmp_path: Path) -> None:
    """sourceId keys the spec citation, so re-parsing identical bundles must
    reproduce it exactly."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "anim.js").write_text(
        'gsap.to(e,{y:-100,scrollTrigger:{trigger:e,scrub:1}});'
        'gsap.to(f,{x:10,scrollTrigger:{trigger:f,scrub:1}});'
    )
    first = [g["sourceId"] for g in mod.parse_bundles(tmp_path)["extractions"]["gsap"]]
    second = [g["sourceId"] for g in mod.parse_bundles(tmp_path)["extractions"]["gsap"]]

    assert first == second
    assert len(set(first)) == len(first), f"ids must be unique per site: {first}"


def test_parse_bundles_marks_ix2_scroll_events_scroll_linked(tmp_path: Path) -> None:
    """IX2 scroll linkage comes from eventTypeId (the trigger), not actionTypeId
    (what changes). SCROLLING_IN_VIEW is scroll-progress driven."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "webflow.js").write_text(
        '{eventTypeId:"SCROLLING_IN_VIEW",actionTypeId:"TRANSFORM_MOVE"}'
    )
    ix2 = mod.parse_bundles(tmp_path)["extractions"].get("webflowIX2")

    assert ix2, "IX2 markers must be captured"
    event = ix2["events"][0]
    assert event["eventType"] == "SCROLLING_IN_VIEW"
    assert event["scrollLinked"] is True
    assert event["sourceId"].startswith("ix2:webflow.js:")


def test_parse_bundles_ix2_click_event_is_not_scroll_linked(tmp_path: Path) -> None:
    """A click trigger drives the same action types but is not scroll motion."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "webflow.js").write_text(
        '{eventTypeId:"MOUSE_CLICK",actionTypeId:"STYLE_OPACITY"}'
    )
    ix2 = mod.parse_bundles(tmp_path)["extractions"].get("webflowIX2")

    assert ix2
    assert ix2["events"][0]["scrollLinked"] is False


def test_parse_bundles_anime_is_not_scroll_linked_by_default(tmp_path: Path) -> None:
    """anime is time-driven; assuming scroll linkage would demand spec entries
    for timed animations and mismap them as scrub motion."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "anim.js").write_text('anime({targets:".x",translateY:100,duration:800});')
    rows = mod.parse_bundles(tmp_path)["extractions"].get("animeJs")

    assert rows
    assert rows[0]["scrollLinked"] is False
    assert rows[0]["sourceId"].startswith("anime:anim.js:")


def test_parse_bundles_anime_onscroll_is_scroll_linked(tmp_path: Path) -> None:
    """anime v4 binds a timeline to scroll through onScroll / ScrollObserver."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "anim.js").write_text(
        'anime({targets:".x",translateY:100,autoplay:onScroll({sync:!0})});'
    )
    rows = mod.parse_bundles(tmp_path)["extractions"].get("animeJs")

    assert rows
    assert rows[0]["scrollLinked"] is True


def test_parse_bundles_ix2_event_vocabulary_from_real_bundle(tmp_path: Path) -> None:
    """The event vocabulary a production IX2 bundle actually ships.

    Taken from webflow.com's own bundle: SCROLLING_IN_VIEW is continuous
    scroll-progress motion, while SCROLL_INTO_VIEW fires once on entry and is an
    intersection reveal — the IO signal covers that, the same way Framer's
    useInView is excluded from scroll-linked site coverage.
    """
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "webflow.js").write_text(
        '{eventTypeId:"SCROLLING_IN_VIEW"},{eventTypeId:"SCROLL_INTO_VIEW"},'
        '{eventTypeId:"MOUSE_CLICK"},{eventTypeId:"MOUSE_OVER"},'
        '{eventTypeId:"TAB_ACTIVE"},{eventTypeId:"DROPDOWN_OPEN"}'
    )
    ix2 = mod.parse_bundles(tmp_path)["extractions"].get("webflowIX2")

    assert ix2
    linked = {e["eventType"] for e in ix2["events"] if e["scrollLinked"]}
    unlinked = {e["eventType"] for e in ix2["events"] if not e["scrollLinked"]}

    assert linked == {"SCROLLING_IN_VIEW"}, linked
    assert "SCROLL_INTO_VIEW" in unlinked, unlinked
    assert {"MOUSE_CLICK", "MOUSE_OVER", "TAB_ACTIVE", "DROPDOWN_OPEN"} <= unlinked


def test_parse_bundles_captures_anime_v4_through_minified_binding(tmp_path: Path) -> None:
    """v4 renamed the entry points and bundlers rename the namespace, so real
    call sites read `O.animate(target, {...})`. Taken from animejs.com's own
    docs.js, where every app-level animation uses this shape."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "docs.js").write_text(
        'O.animate(m,{opacity:[1,0],duration:225,ease:"outQuad"});'
    )
    rows = mod.parse_bundles(tmp_path)["extractions"].get("animeJs")

    assert rows, "aliased v4 call site must be captured"
    assert rows[0]["api"] == "v4"
    assert rows[0]["binding"] == "O"
    assert 'ease:"outQuad"' in rows[0]["config"]
    assert rows[0]["scrollLinked"] is False


def test_parse_bundles_anime_v4_target_array_holds_a_comma(tmp_path: Path) -> None:
    """`animate(["#a","#b"], {...})` puts a comma inside the target, so scanning
    to the first comma would split the call at the wrong place."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "docs.js").write_text(
        'O.animate(["#docs-info","#path-animation"],{opacity:0,duration:75,ease:"in"});'
    )
    rows = mod.parse_bundles(tmp_path)["extractions"].get("animeJs")

    assert rows
    assert rows[0]["config"] == '{opacity:0,duration:75,ease:"in"}'


def test_parse_bundles_ignores_waapi_element_animate(tmp_path: Path) -> None:
    """Element.animate() shares the name. anime spells the option `ease`, WAAPI
    spells it `easing`, so the config keys keep them apart."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "misc.js").write_text(
        'el.animate([{opacity:0},{opacity:1}],{duration:300,easing:"ease-in",fill:"both"});'
        'node.animate(kf,{duration:200,iterations:2,direction:"alternate"});'
    )
    assert not mod.parse_bundles(tmp_path)["extractions"].get("animeJs")


def test_parse_bundles_anime_v4_onscroll_is_scroll_linked(tmp_path: Path) -> None:
    """Only an onScroll / ScrollObserver binding makes an anime site scroll
    driven; everything else is time driven."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "docs.js").write_text(
        'O.animate(".x",{y:100,ease:"out",autoplay:onScroll({sync:1})});'
    )
    rows = mod.parse_bundles(tmp_path)["extractions"].get("animeJs")

    assert rows
    assert rows[0]["scrollLinked"] is True


def test_parse_bundles_scans_esm_and_cjs_bundles(tmp_path: Path) -> None:
    """ES-module bundles ship as .mjs. Globbing only "*.js" made them invisible
    to every extractor — measured on godotengine.org, whose app code is a .mjs
    and produced zero extractions while plainly using anime v4."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "app.mjs").write_text(
        'animate(el,{opacity:0,duration:500,ease:"outCirc"});'
    )
    (bundles / "legacy.cjs").write_text(
        'animate(other,{opacity:1,duration:200,ease:"inQuad"});'
    )
    rows = mod.parse_bundles(tmp_path)["extractions"].get("animeJs")

    assert rows, "an .mjs bundle must be scanned"
    sources = {r["source"].rsplit("/", 1)[-1] for r in rows}
    assert sources == {"app.mjs", "legacy.cjs"}, sources


def test_parse_bundles_resolves_anime_autoplay_scroll_observer(tmp_path: Path) -> None:
    """Real code assigns the observer and passes the binding, so the config
    carries only `autoplay:t` and the assignment must be resolved in scope.

    Both shapes are taken from godotengine.org's release page bundle.
    """
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "app.mjs").write_text(
        'const t=onScroll({trigger:e.element,enter:{target:"top"}});'
        'animate(e.element,{y:{from:"+=50px"},duration:500,autoplay:t,ease:"outCirc"});'
        'scrollObserver=onScroll({target:".hdr"});'
        'animate(".hdr",{autoplay:scrollObserver,ease:"outCirc"});'
        'animate("#x",{opacity:{to:0},duration:500,ease:"in"});'
    )
    rows = mod.parse_bundles(tmp_path)["extractions"]["animeJs"]

    linked = [r for r in rows if r["scrollLinked"]]
    unlinked = [r for r in rows if not r["scrollLinked"]]
    assert len(linked) == 2, [r["config"] for r in rows]
    assert len(unlinked) == 1, [r["config"] for r in unlinked]
    assert "opacity" in unlinked[0]["config"]


def test_parse_bundles_autoplay_boolean_is_not_scroll_linked(tmp_path: Path) -> None:
    """`autoplay:false` is a paused timed animation, not a scroll binding."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "app.mjs").write_text(
        'animate(el,{opacity:1,duration:300,ease:"out",autoplay:false});'
    )
    rows = mod.parse_bundles(tmp_path)["extractions"]["animeJs"]
    assert rows[0]["scrollLinked"] is False


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


def test_parse_bundles_flags_unresolved_framer_scroll_gap(tmp_path: Path) -> None:
    """Turbopack can preserve Framer hook names while moving the options object
    away from the hook call. If deterministic extraction resolves zero scroll
    sites despite strong scroll markers, Step 5d must dispatch the
    bundle-analyzer instead of treating the bundle as fully parsed."""
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "page.js").write_text(
        'let r={target:M,offset:["start start","end start"]};'
        'let{scrollYProgress:B}=(0,tK.useScroll)(r);'
        'let G=(0,tW.useTransform)(B,a,n);'
        'return(0,n.jsx)(m.P.div,{style:{width:G}});',
        encoding="utf-8",
    )
    plan = mod.parse_bundles(tmp_path)
    assert "framerMotion" not in plan["extractions"]
    gap = next(u for u in plan["unresolved"] if u["library"] == "framer-motion")
    assert gap["source"] == "bundles/page.js"
    assert "bundle-analyzer" in gap["reason"]


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


def test_active_state_expansion_extracts_dotted_css_module_token(
    tmp_path: Path,
) -> None:
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "nav.js").write_text(
        '(0,n.jsx)(c.P.span,{className:iL.default.label_container,'
        'style:{color:"#111"},layout:!0,transition:{type:"spring"},'
        'initial:{width:0},animate:{width:a?"auto":0}});'
        'let iL={default:{label_container:"nav_label_container__okVKb"}};',
        encoding="utf-8",
    )
    plan = mod.parse_bundles(tmp_path)
    active = plan["extractions"].get("activeStateExpansions")
    assert active, plan["extractions"].keys()
    entry = active[0]
    assert entry["classToken"] == "label_container"
    assert entry["resolvedClassName"] == "nav_label_container__okVKb"


def test_no_expansions_no_key(tmp_path: Path) -> None:
    mod = _load_module()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "plain.js").write_text("console.log(1)", encoding="utf-8")
    plan = mod.parse_bundles(tmp_path)
    assert "hoverSizeExpansions" not in plan["extractions"]
