"""scaffold-to-jsx Swiper activation.

The captured DOM is a snapshot of an already-running Swiper: the container
carries runtime state classes, loop-clone slides, and generated pagination, and
the transpiler froze all of it as a static tree so the carousel renders inert.
These tests pin the transpiler's Swiper handling: strip runtime state classes,
drop loop-clone slides, keep the custom pagination/.bar, stamp a config recovered
from the swiper-* classes, exclude the section from ScrollReveal, and emit + mount
a SwiperActivator that attaches a real Swiper at runtime.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def _swiper_container(extra_class: str = "swiper-fade") -> dict:
    return {
        "tag": "div",
        "class": f"swiper {extra_class} swiper-initialized swiper-horizontal swiper-pointer-events",
        "styles": {"transform": "translate3d(-1440px,0,0)"},
        "children": [
            {"tag": "div", "class": "swiper-wrapper", "styles": {}, "children": [
                {"tag": "div", "class": "swiper-slide swiper-slide-duplicate swiper-slide-prev",
                 "styles": {"opacity": "0"}, "children": [{"tag": "span", "text": "CLONESLIDE"}]},
                {"tag": "div", "class": "swiper-slide swiper-slide-visible swiper-slide-active",
                 "styles": {"opacity": "1"}, "children": [{"tag": "span", "text": "Slide One"}]},
                {"tag": "div", "class": "swiper-slide swiper-slide-next",
                 "styles": {"opacity": "0"}, "children": [{"tag": "span", "text": "Slide Two"}]},
            ]},
            {"tag": "div", "class": "swiper-ui", "styles": {}, "children": [
                {"tag": "div", "class": "swiper-pagination swiper-pagination-bullets", "styles": {},
                 "children": [
                     {"tag": "span", "class": "swiper-pagination-bullet nclick-target", "styles": {},
                      "children": [{"tag": "span", "class": "bar",
                                    "styles": {"width": "9px", "transition": "width 4s linear"},
                                    "text": "1"}]},
                 ]},
            ]},
        ],
    }


def _run(tmp_path: Path, structure: dict) -> Path:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8",
    )
    (impl / "package.json").write_text(
        json.dumps({"name": "impl", "dependencies": {"next": "16.0.0", "react": "19.0.0"}}),
        encoding="utf-8",
    )
    (impl / "next.config.js").write_text("module.exports={};", encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return impl


def _blob(impl: Path) -> str:
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((impl / "src").rglob("*.tsx"))
    )


def test_swiper_carousel_activated(tmp_path: Path) -> None:
    structure = {
        "tag": "body", "class": "antialiased", "styles": {},
        "children": [{"tag": "section", "class": "hero", "styles": {},
                      "children": [_swiper_container()]}],
    }
    impl = _run(tmp_path, structure)
    hero = (impl / "src" / "components" / "Hero.tsx").read_text(encoding="utf-8")
    page = (impl / "src" / "app" / "page.tsx").read_text(encoding="utf-8")

    # (1) container stamped with a config recovered from swiper-fade
    assert "data-swiper-config" in hero, hero
    assert "fade" in hero, "swiper-fade must recover effect:'fade' (drives slide opacity)"
    # (2) loop-clone slide dropped; real slides kept
    assert "CLONESLIDE" not in hero, "loop-clone slide must be dropped before re-init"
    assert "Slide One" in hero and "Slide Two" in hero
    assert "swiper-slide-duplicate" not in hero
    # (3) runtime state classes stripped, base classes kept
    assert "swiper-initialized" not in hero and "swiper-horizontal" not in hero
    assert "swiper" in hero and "swiper-wrapper" in hero and "swiper-fade" in hero
    # (4) custom pagination .bar preserved (progress target #3) and its selector
    # detected from the DOM (width transition) — not hardcoded in the activator.
    assert 'className="bar"' in hero, "the .bar progress fill must survive the strip"
    assert 'data-swiper-progress="span.bar"' in hero, (
        "the progress-fill selector must be detected from the DOM width-transition "
        "and stamped, so the activator is generic (no hardcoded class)"
    )
    activator = (impl / "src" / "lib" / "SwiperActivator.tsx").read_text(encoding="utf-8")
    assert 'querySelector<HTMLElement>(".bar")' not in activator, (
        "the activator must read the stamped selector, never hardcode a site class"
    )
    assert "dataset.swiperProgress" in activator
    # (5) SwiperActivator emitted + imported + mounted; section NOT reveal-wrapped
    assert (impl / "src" / "lib" / "SwiperActivator.tsx").is_file()
    assert "import SwiperActivator" in page and "<SwiperActivator />" in page
    assert "ScrollReveal" not in page, "a swiper section must not be ScrollReveal-wrapped"
    # (6) swiper added to package.json dependencies
    deps = json.loads((impl / "package.json").read_text(encoding="utf-8"))["dependencies"]
    assert "swiper" in deps


def test_progress_fill_outside_container_still_stamped(tmp_path: Path) -> None:
    """Real captures (navercorp hero, T-2 run) put the pagination/fill in a
    sibling `swiper-ui` block OUTSIDE the `.swiper` container, under a shared
    wrapper. Detection must widen one level to the container's parent, and the
    activator searches el.parentElement as the fallback scope."""
    group = {
        "tag": "div", "class": "swiper-group", "styles": {}, "children": [
            {"tag": "div", "class": "swiper swiper-fade swiper-initialized",
             "styles": {}, "children": [
                 {"tag": "div", "class": "swiper-wrapper", "styles": {}, "children": [
                     {"tag": "div", "class": "swiper-slide swiper-slide-active",
                      "styles": {"opacity": "1"}, "children": [{"tag": "span", "text": "H1"}]},
                 ]},
             ]},
            {"tag": "div", "class": "swiper-ui", "styles": {}, "children": [
                {"tag": "div", "class": "swiper-pagination", "styles": {}, "children": [
                    {"tag": "span", "class": "swiper-pagination-bullet", "styles": {},
                     "children": [{"tag": "span", "class": "bar",
                                   "styles": {"transition": "width 4s linear", "width": "9px"},
                                   "text": "1"}]},
                ]},
            ]},
        ],
    }
    structure = {
        "tag": "body", "class": "", "styles": {},
        "children": [{"tag": "section", "class": "hero", "styles": {}, "children": [group]}],
    }
    impl = _run(tmp_path, structure)
    hero = (impl / "src" / "components" / "Hero.tsx").read_text(encoding="utf-8")
    assert 'data-swiper-progress="span.bar"' in hero, (
        "a fill outside the container (sibling swiper-ui) must still be detected "
        f"via the parent scope; got:\n{hero}"
    )
    activator = (impl / "src" / "lib" / "SwiperActivator.tsx").read_text(encoding="utf-8")
    assert "parentElement" in activator, (
        "the activator must widen its query scope one level, mirroring detection"
    )


def test_progress_selector_skips_shared_utility_class(tmp_path: Path) -> None:
    """Pagination bullets carry SHORT width transitions (active-bullet expand)
    and share utility classes with unrelated nodes (nav links) — measured on the
    T-2 run, where the detector wrongly stamped `.nclick-target` and the driver
    wrote width onto a nav link. The fill = the LONGEST width transition, and
    only a class not shared with non-fill nodes is a safe selector."""
    container = {
        "tag": "div", "class": "swiper swiper-initialized", "styles": {}, "children": [
            {"tag": "div", "class": "swiper-wrapper", "styles": {}, "children": [
                {"tag": "div", "class": "swiper-slide swiper-slide-active",
                 "styles": {"opacity": "1"}, "children": [{"tag": "span", "text": "B1"}]},
            ]},
            # a nav link sharing the utility class but with NO width transition
            {"tag": "a", "class": "nclick-target btn-nav", "styles": {"transition": "color 0.3s"},
             "children": [{"tag": "span", "text": "More"}]},
            {"tag": "div", "class": "swiper-ui", "styles": {}, "children": [
                {"tag": "div", "class": "swiper-pagination", "styles": {}, "children": [
                    # bullet: SHORT width transition + the shared utility class
                    {"tag": "span",
                     "class": "swiper-pagination-bullet nclick-target",
                     "styles": {"transition": "width 0.4s cubic-bezier(0.33, 1, 0.68, 1)"},
                     "children": [
                         # the real fill: LONG width transition, own class
                         {"tag": "span", "class": "bar",
                          "styles": {"transition": "width 4s linear", "width": "9px"},
                          "text": "1"},
                     ]},
                ]},
            ]},
        ],
    }
    structure = {
        "tag": "body", "class": "", "styles": {},
        "children": [{"tag": "section", "class": "hero", "styles": {}, "children": [container]}],
    }
    impl = _run(tmp_path, structure)
    hero = (impl / "src" / "components" / "Hero.tsx").read_text(encoding="utf-8")
    assert 'data-swiper-progress="span.bar"' in hero, (
        f"must pick the long-duration fill, never a shared utility class; got:\n{hero}"
    )
    assert 'data-swiper-progress=".nclick-target"' not in hero
    assert 'data-swiper-progress="span.nclick-target"' not in hero


def test_progress_fill_outside_container_despite_inner_decoy(tmp_path: Path) -> None:
    """codex P2 (1a): the real fill is in a sibling swiper-ui OUTSIDE the .swiper
    container, AND a SHORT-duration decoy (active-bullet expand) sits INSIDE the
    container. Widening to the parent scope must happen even though the subtree
    is non-empty, and the longest-duration ranking must pick the sibling fill."""
    group = {
        "tag": "div", "class": "swiper-group", "styles": {}, "children": [
            {"tag": "div", "class": "swiper swiper-initialized", "styles": {}, "children": [
                {"tag": "div", "class": "swiper-wrapper", "styles": {}, "children": [
                    {"tag": "div", "class": "swiper-slide swiper-slide-active",
                     "styles": {"opacity": "1"}, "children": [{"tag": "span", "text": "H1"}]},
                ]},
                # inner decoy: a SHORT width transition inside the container
                {"tag": "div", "class": "inner-progress",
                 "styles": {"transition": "width 0.4s ease"}, "children": []},
            ]},
            {"tag": "div", "class": "swiper-ui", "styles": {}, "children": [
                {"tag": "div", "class": "swiper-pagination", "styles": {}, "children": [
                    {"tag": "span", "class": "swiper-pagination-bullet", "styles": {}, "children": [
                        {"tag": "span", "class": "bar",
                         "styles": {"transition": "width 4s linear", "width": "9px"}, "text": "1"},
                    ]},
                ]},
            ]},
        ],
    }
    structure = {
        "tag": "body", "class": "", "styles": {},
        "children": [{"tag": "section", "class": "hero", "styles": {}, "children": [group]}],
    }
    impl = _run(tmp_path, structure)
    hero = (impl / "src" / "components" / "Hero.tsx").read_text(encoding="utf-8")
    assert 'data-swiper-progress="span.bar"' in hero, (
        f"the sibling 4s fill must beat the inner 0.4s decoy; got:\n{hero}"
    )
    assert 'data-swiper-progress="div.inner-progress"' not in hero


def test_progress_fill_split_transition_duration(tmp_path: Path) -> None:
    """codex P2 (1b): a computed capture may store `transition-property: width`
    plus a separate `transition-duration`. The fill's 4s must beat a decoy's
    0.4s even when the seconds live in transition-duration, not the shorthand."""
    container = {
        "tag": "div", "class": "swiper swiper-initialized", "styles": {}, "children": [
            {"tag": "div", "class": "swiper-wrapper", "styles": {}, "children": [
                {"tag": "div", "class": "swiper-slide swiper-slide-active",
                 "styles": {"opacity": "1"}, "children": [{"tag": "span", "text": "B1"}]},
            ]},
            {"tag": "div", "class": "swiper-ui", "styles": {}, "children": [
                {"tag": "div", "class": "swiper-pagination", "styles": {}, "children": [
                    # decoy bullet: split width transition, SHORT duration, listed FIRST
                    {"tag": "span", "class": "swiper-pagination-bullet",
                     "styles": {"transition-property": "width", "transition-duration": "0.4s"},
                     "children": [
                         # real fill: split width transition, LONG duration
                         {"tag": "span", "class": "bar",
                          "styles": {"transition-property": "width", "transition-duration": "4s",
                                     "width": "9px"}, "text": "1"},
                     ]},
                ]},
            ]},
        ],
    }
    structure = {
        "tag": "body", "class": "", "styles": {},
        "children": [{"tag": "section", "class": "hero", "styles": {}, "children": [container]}],
    }
    impl = _run(tmp_path, structure)
    hero = (impl / "src" / "components" / "Hero.tsx").read_text(encoding="utf-8")
    assert 'data-swiper-progress="span.bar"' in hero, (
        f"split transition-duration 4s must outrank the 0.4s decoy; got:\n{hero}"
    )
    assert 'data-swiper-progress="span.swiper-pagination-bullet"' not in hero


def test_sibling_carousels_do_not_cross_bind_progress(tmp_path: Path) -> None:
    """codex P2 (follow-on): two `.swiper` containers sharing one parent, each
    with its fill in a sibling swiper-ui. Widening to the shared parent would let
    carousel A's detection see carousel B's outside fill and bind the wrong bar.
    A multi-carousel parent must NOT be widened into — each container falls back
    to its own subtree (here: no outside fill in-subtree → no progress stamp,
    which is correct: better no bar than the wrong carousel's bar)."""
    def _swiper_with_outside_fill(fill_cls: str, dur: str) -> list:
        return [
            {"tag": "div", "class": "swiper swiper-initialized", "styles": {}, "children": [
                {"tag": "div", "class": "swiper-wrapper", "styles": {}, "children": [
                    {"tag": "div", "class": "swiper-slide swiper-slide-active",
                     "styles": {"opacity": "1"}, "children": [{"tag": "span", "text": "x"}]},
                ]},
            ]},
            {"tag": "div", "class": "swiper-ui", "styles": {}, "children": [
                {"tag": "span", "class": fill_cls,
                 "styles": {"transition": f"width {dur} linear"}, "children": [{"tag": "span", "text": "1"}]},
            ]},
        ]
    shared = {
        "tag": "div", "class": "carousel-shell", "styles": {},
        # A (fill barA 2s) and B (fill barB 4s) under ONE parent
        "children": _swiper_with_outside_fill("barA", "2s") + _swiper_with_outside_fill("barB", "4s"),
    }
    structure = {
        "tag": "body", "class": "", "styles": {},
        "children": [{"tag": "section", "class": "hero", "styles": {}, "children": [shared]}],
    }
    impl = _run(tmp_path, structure)
    hero = (impl / "src" / "components" / "Hero.tsx").read_text(encoding="utf-8")
    # The concrete cross-bind bug would stamp BOTH containers with the same
    # (longest, barB) fill. A multi-carousel parent is not widened into, so
    # neither container cross-binds the other's outside fill.
    assert hero.count('data-swiper-progress="span.barB"') < 2, (
        f"sibling carousels must not both bind the same fill; got:\n{hero}"
    )


def test_progress_fill_multi_property_transition_ranked_by_width_segment(tmp_path: Path) -> None:
    """codex P2 (3rd round): a decoy with `transition: opacity 8s, width 0.4s`
    must be ranked by its WIDTH segment (0.4s), not the 8s opacity — otherwise it
    outranks the real 4s `span.bar` fill. Duration parsing is per-width-channel."""
    container = {
        "tag": "div", "class": "swiper swiper-initialized", "styles": {}, "children": [
            {"tag": "div", "class": "swiper-wrapper", "styles": {}, "children": [
                {"tag": "div", "class": "swiper-slide swiper-slide-active",
                 "styles": {"opacity": "1"}, "children": [{"tag": "span", "text": "B1"}]},
            ]},
            {"tag": "div", "class": "swiper-ui", "styles": {}, "children": [
                {"tag": "div", "class": "swiper-pagination", "styles": {}, "children": [
                    # decoy: 8s OPACITY + 0.4s width — must score 0.4s, not 8s
                    {"tag": "span", "class": "swiper-pagination-bullet",
                     "styles": {"transition": "opacity 8s ease, width 0.4s ease"}, "children": [
                         {"tag": "span", "class": "bar",
                          "styles": {"transition": "width 4s linear", "width": "9px"}, "text": "1"},
                     ]},
                ]},
            ]},
        ],
    }
    structure = {
        "tag": "body", "class": "", "styles": {},
        "children": [{"tag": "section", "class": "hero", "styles": {}, "children": [container]}],
    }
    impl = _run(tmp_path, structure)
    hero = (impl / "src" / "components" / "Hero.tsx").read_text(encoding="utf-8")
    assert 'data-swiper-progress="span.bar"' in hero, (
        f"the 4s width fill must beat the decoy's 0.4s width (not its 8s opacity); got:\n{hero}"
    )
    assert 'data-swiper-progress="span.swiper-pagination-bullet"' not in hero


def test_vertical_direction_recovered_despite_class_strip(tmp_path: Path) -> None:
    """`swiper-vertical` is both a runtime-state class (stripped) and a config
    signal (direction). The config must be derived from the ORIGINAL captured
    class so a vertical carousel is not silently regenerated as horizontal."""
    container = {
        "tag": "div",
        "class": "swiper swiper-vertical swiper-initialized swiper-pointer-events",
        "styles": {}, "children": [
            {"tag": "div", "class": "swiper-wrapper", "styles": {}, "children": [
                {"tag": "div", "class": "swiper-slide swiper-slide-active",
                 "styles": {"opacity": "1"}, "children": [{"tag": "span", "text": "V1"}]},
                {"tag": "div", "class": "swiper-slide",
                 "styles": {"opacity": "0"}, "children": [{"tag": "span", "text": "V2"}]},
            ]},
        ],
    }
    structure = {
        "tag": "body", "class": "", "styles": {},
        "children": [{"tag": "section", "class": "hero", "styles": {}, "children": [container]}],
    }
    impl = _run(tmp_path, structure)
    hero = (impl / "src" / "components" / "Hero.tsx").read_text(encoding="utf-8")
    # the runtime class is stripped from the DOM...
    assert "swiper-vertical" not in hero
    # ...but the direction survives in the stamped config.
    assert '\\"direction\\": \\"vertical\\"' in hero or '"direction": "vertical"' in hero, hero


def test_autoplay_delay_recovered_from_fill_duration(tmp_path: Path) -> None:
    """Phase A1 fidelity: the autoplay delay is the ref's REAL slide interval,
    recovered from the progress fill's width-transition duration (the fill sweeps
    0→100% over one cycle), not a fixed guess. A 4s fill → delay 4000.

    F4: a swiper with NO autoplay signal (no measurable fill AND no data-autoplay
    attribute) must NOT be given an invented autoplay — a manual (arrows/drag)
    carousel cloned with a 3s self-advance both fabricates motion the ref lacks and
    lets the transition-fires carousel fingerprint read the injected advance as a
    satisfied transition. No signal → no autoplay block (delay absent)."""
    def _hero(fill_dur: str | None) -> dict:
        slides = [{"tag": "div", "class": "swiper-slide swiper-slide-active",
                   "styles": {"opacity": "1"}, "children": [{"tag": "span", "text": "H"}]}]
        ui = ([{"tag": "div", "class": "swiper-ui", "styles": {}, "children": [
                {"tag": "span", "class": "bar",
                 "styles": {"transition": f"width {fill_dur} linear", "width": "9px"}, "text": "1"}]}]
              if fill_dur else [])
        return {"tag": "div", "class": "wrap", "styles": {}, "children": [
            {"tag": "div", "class": "swiper swiper-fade", "styles": {}, "children": [
                {"tag": "div", "class": "swiper-wrapper", "styles": {}, "children": slides}]},
        ] + ui}

    def _delay(fill_dur: str | None) -> int | None:
        structure = {"tag": "body", "class": "", "styles": {}, "children": [
            {"tag": "section", "class": "hero", "styles": {}, "children": [_hero(fill_dur)]}]}
        impl = _run(tmp_path / (fill_dur or "none"), structure)
        hero = (impl / "src" / "components" / "Hero.tsx").read_text(encoding="utf-8")
        m = re.search(r'\\"delay\\":\s*(\d+)', hero)
        return int(m.group(1)) if m else None

    assert _delay("4s") == 4000, "a 4s fill must set autoplay delay 4000 (real ref interval)"
    assert _delay("2.5s") == 2500
    assert _delay(None) is None, (
        "no autoplay signal (no fill, no data-autoplay) → NO invented autoplay; a "
        "manual carousel must clone as manual, not self-advance (F4)"
    )
    # A delay slower than the probe can observe is capped so a faithful-but-slow
    # carousel is never emitted with a delay transition-fires would read as dead.
    assert _delay("8s") == 5000, "delay above the observable ceiling must clamp to 5000"


def test_autoplay_emitted_when_data_attribute_signals_it(tmp_path: Path) -> None:
    """F4: a manual-looking carousel with no progress fill but an explicit
    data-autoplay* attribute DID autoplay on the ref, so autoplay must be emitted
    (Swiper's 3000ms default, since no fill measured the real interval)."""
    def _hero_text(attrs: dict) -> str:
        container = {"tag": "div", "class": "swiper swiper-fade", "styles": {}, "children": [
            {"tag": "div", "class": "swiper-wrapper", "styles": {}, "children": [
                {"tag": "div", "class": "swiper-slide swiper-slide-active",
                 "styles": {"opacity": "1"}, "children": [{"tag": "span", "text": "S"}]}]}]}
        container.update(attrs)
        structure = {"tag": "body", "class": "", "styles": {}, "children": [
            {"tag": "section", "class": "hero", "styles": {}, "children": [
                {"tag": "div", "class": "wrap", "styles": {}, "children": [container]}]}]}
        slug = "".join(c for c in str(sorted(attrs.items())) if c.isalnum())
        impl = _run(tmp_path / slug, structure)
        return (impl / "src" / "components" / "Hero.tsx").read_text(encoding="utf-8")

    # data-autoplay signal, no fill → autoplay at the 3000ms default
    on = _hero_text({"data-autoplay": "true"})
    m = re.search(r'\\"delay\\":\s*(\d+)', on)
    assert m and int(m.group(1)) == 3000, (
        f"a data-autoplay signal must emit autoplay at the 3000ms default; got:\n{on}"
    )
    # data-autoplay="false" is NOT a signal — the ref explicitly disabled it.
    off = _hero_text({"data-autoplay": "false"})
    assert re.search(r'\\"delay\\"', off) is None, (
        f"data-autoplay='false' must not emit autoplay (ref disabled it); got:\n{off}"
    )


def test_v6_swiper_container_activated(tmp_path: Path) -> None:
    """F5: Swiper <=v6 names the root `.swiper-container`; v7+ renamed it to
    `.swiper`. The wrapper/slide class names are unchanged and the activator mounts
    via the stamped data-swiper-config (not the container class), so a v6 container
    must be recognized and stamped — else it never activates and freezes at the
    captured frame (a silent motion drop)."""
    container = {
        "tag": "div",
        "class": "swiper-container swiper-container-horizontal swiper-container-initialized",
        "styles": {}, "children": [
            {"tag": "div", "class": "swiper-wrapper", "styles": {}, "children": [
                {"tag": "div", "class": "swiper-slide swiper-slide-active",
                 "styles": {"opacity": "1"}, "children": [{"tag": "span", "text": "V6 One"}]},
                {"tag": "div", "class": "swiper-slide",
                 "styles": {"opacity": "0"}, "children": [{"tag": "span", "text": "V6 Two"}]},
            ]},
        ],
    }
    structure = {
        "tag": "body", "class": "", "styles": {},
        "children": [{"tag": "section", "class": "hero", "styles": {}, "children": [container]}],
    }
    impl = _run(tmp_path, structure)
    hero = (impl / "src" / "components" / "Hero.tsx").read_text(encoding="utf-8")
    assert "data-swiper-config" in hero, (
        "a legacy v6 .swiper-container must be stamped for activation, not left inert"
    )
    assert (impl / "src" / "lib" / "SwiperActivator.tsx").is_file()
    assert "V6 One" in hero and "V6 Two" in hero


def test_masonry_swiper_not_carouselled(tmp_path: Path) -> None:
    """A masonry/scrollbar (free-scroll grid) Swiper must NOT be stamped — a
    slideshow init would destroy the grid. No data-swiper-config, no activator."""
    grid = {
        "tag": "div", "class": "main-news-list swiper swiper-initialized", "styles": {},
        "children": [
            {"tag": "div", "class": "masonry-list swiper-wrapper", "styles": {}, "children": [
                {"tag": "div", "class": "masonry-grid-item swiper-slide", "styles": {},
                 "children": [{"tag": "span", "text": "News A"}]},
                {"tag": "div", "class": "masonry-grid-item swiper-slide", "styles": {},
                 "children": [{"tag": "span", "text": "News B"}]},
            ]},
            {"tag": "div", "class": "swiper-scrollbar", "styles": {}, "children": []},
        ],
    }
    structure = {
        "tag": "body", "class": "antialiased", "styles": {},
        "children": [{"tag": "section", "class": "hero", "styles": {}, "children": [grid]}],
    }
    impl = _run(tmp_path, structure)
    hero = (impl / "src" / "components" / "Hero.tsx").read_text(encoding="utf-8")
    assert "data-swiper-config" not in hero, "masonry/scrollbar grid must not be stamped"
    assert not (impl / "src" / "lib" / "SwiperActivator.tsx").is_file()
    # content still renders (grid preserved, not destroyed)
    assert "News A" in hero and "News B" in hero


def test_swiper_activator_supports_navigation_for_manual_carousels(tmp_path: Path) -> None:
    """GEN-M1: a manual (arrows) carousel has no autoplay, so without the Swiper
    Navigation module its captured prev/next buttons are inert and the cloned
    click-advance motion can never fire. The emitted SwiperActivator must import
    Navigation and bind the surviving swiper-button-prev/next elements."""
    impl = _run(tmp_path, {
        "tag": "body",
        "children": [{"tag": "section", "class": "hero", "children": [
            _swiper_container(),
            {"tag": "div", "class": "swiper-button-prev", "children": []},
            {"tag": "div", "class": "swiper-button-next", "children": []},
        ]}],
    })
    activator = (impl / "src" / "lib" / "SwiperActivator.tsx").read_text(encoding="utf-8")
    assert "Navigation" in activator, "SwiperActivator must import the Navigation module"
    assert ".swiper-button-prev" in activator and ".swiper-button-next" in activator, (
        "SwiperActivator must locate the captured prev/next buttons"
    )
    assert "navigation:" in activator and "prevEl" in activator and "nextEl" in activator, (
        "SwiperActivator must configure navigation with the bound buttons"
    )
