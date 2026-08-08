"""gen-B1 / gen-H1: motion drivers must mount on every React stack, not just vite.

_emit_vite_entry mounts SmoothScroll + ScrollStateDriver + SwiperActivator and
imports the ScrollReveal/ScrollScrub wrappers that section_jsx references. The
next emitter omitted SmoothScroll (Lenis captured but never mounted → scroll
physics diverge), and the remix emitter imported none of the wrappers/drivers
even though it reuses section_jsx (which contains <ScrollReveal>/<ScrollScrub>)
— an unresolved-identifier build failure on any reveal/smooth-scroll site.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"
EMIT_HELPERS = ROOT / "skills" / "visual-debug" / "scripts" / "lib" / "emit_scroll_helpers.py"


def test_emit_scroll_helpers_keeps_python39_runtime_compatibility() -> None:
    source = EMIT_HELPERS.read_text(encoding="utf-8")
    assert "int | float" not in source, (
        "runtime isinstance unions require Python 3.10; use (int, float) so "
        "the installed shell pipeline also works with macOS system Python 3.9"
    )


def _seed_ref(ref: Path) -> None:
    ref.mkdir(parents=True, exist_ok=True)
    ref.joinpath("structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "children": [
                {"tag": "h1", "text": "Real Food Wins"},
            ]},
            {"tag": "section", "class": "reveal-me", "data-scroll-fade": "true",
             "children": [{"tag": "p", "text": "fades in on scroll"}]},
        ],
    }), encoding="utf-8")
    ref.joinpath("section-map.json").write_text(json.dumps({
        "sections": [
            {"index": 0, "tag": "section", "cls": "hero"},
            {"index": 1, "tag": "section", "cls": "reveal-me"},
        ]
    }), encoding="utf-8")
    # smoothScroll + scrollDriven required -> SmoothScroll + ScrollStateDriver
    ref.joinpath("generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": True, "lenis": {"lerp": 0.1}},
        "scrollDriven": {"required": True},
    }), encoding="utf-8")


def _seed_impl(impl: Path, deps: dict, config_file: str | None) -> None:
    (impl / "src").mkdir(parents=True, exist_ok=True)
    impl.joinpath("package.json").write_text(
        json.dumps({"name": "clone", "dependencies": deps,
                    "scripts": {"dev": "", "build": ""}}),
        encoding="utf-8")
    if config_file:
        impl.joinpath(config_file).write_text("export default {};\n", encoding="utf-8")


def _run(tmp_path: Path, deps: dict, config_file: str | None) -> subprocess.CompletedProcess:
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    _seed_ref(ref)
    _seed_impl(impl, deps, config_file)
    return subprocess.run(["bash", str(SCRIPT), str(ref), str(impl)],
                          capture_output=True, text=True, timeout=60)


def test_next_mounts_smoothscroll(tmp_path: Path) -> None:
    proc = _run(tmp_path, {"next": "14.0.0", "react": "18"}, "next.config.js")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    page = (tmp_path / "impl" / "src" / "app" / "page.tsx").read_text(encoding="utf-8")
    assert "import SmoothScroll" in page, "Next page must import SmoothScroll (Lenis)"
    assert "<SmoothScroll>" in page, "Next page must wrap content in <SmoothScroll>"


def test_remix_mounts_smoothscroll(tmp_path: Path) -> None:
    # remix reuses the shared section_jsx and previously mounted NO motion
    # drivers — smoothScroll.required Lenis captured but never mounted.
    proc = _run(tmp_path, {"@remix-run/react": "2", "react": "18"}, None)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    page = (tmp_path / "impl" / "app" / "_index.tsx").read_text(encoding="utf-8")
    assert "import SmoothScroll" in page, "remix entry must import SmoothScroll (Lenis)"
    assert "<SmoothScroll>" in page, "remix entry must wrap content in <SmoothScroll>"


def test_remix_imports_every_identifier_it_renders(tmp_path: Path) -> None:
    """Whatever wrapper/driver identifier appears in the rendered JSX must be
    imported, or the remix build fails on an unresolved reference."""
    proc = _run(tmp_path, {"@remix-run/react": "2", "react": "18"}, None)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    page = (tmp_path / "impl" / "app" / "_index.tsx").read_text(encoding="utf-8")
    for ident in ("ScrollReveal", "ScrollScrub", "ScrollStateDriver",
                  "SwiperActivator", "SmoothScroll"):
        if f"<{ident}" in page:
            assert f"import {ident}" in page, f"{ident} rendered but not imported"


def test_visible_autoplay_video_gets_imperative_kick(tmp_path: Path) -> None:
    """gen-H3: a visible autoplay <video> relies on the JSX `muted` attribute,
    which races React SSR hydration and can freeze the clip at frame 0. The
    transpiler must emit + mount a VideoAutoplayKick that imperatively plays
    every video[autoplay] (the kick the hidden RequiredVideos bridge already
    does for off-page videos)."""
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    ref.mkdir(parents=True)
    ref.joinpath("structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{"tag": "section", "class": "hero", "children": [
            {"tag": "video", "src": "/hero.mp4"},
        ]}],
    }), encoding="utf-8")
    ref.joinpath("section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8")
    ref.joinpath("assets.json").write_text(json.dumps({
        "videos": [{"src": "/hero.mp4", "autoplay": True, "muted": True}]
    }), encoding="utf-8")
    _seed_impl(impl, {"vite": "5", "react": "18"}, "vite.config.js")
    proc = subprocess.run(["bash", str(SCRIPT), str(ref), str(impl)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    kick = impl / "src" / "lib" / "VideoAutoplayKick.tsx"
    assert kick.is_file(), "VideoAutoplayKick.tsx must be emitted for a visible autoplay video"
    assert 'querySelectorAll<HTMLVideoElement>("video[autoplay]")' in kick.read_text(encoding="utf-8")
    app = (impl / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "import VideoAutoplayKick" in app and "<VideoAutoplayKick />" in app


def test_no_video_kick_when_no_autoplay_video(tmp_path: Path) -> None:
    proc = _run(tmp_path, {"vite": "5", "react": "18"}, "vite.config.js")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (tmp_path / "impl" / "src" / "lib" / "VideoAutoplayKick.tsx").exists()
    app = (tmp_path / "impl" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "VideoAutoplayKick" not in app


def test_vite_mounts_spec_class_toggle_and_runtime_scroll_style_drivers(
    tmp_path: Path,
) -> None:
    """Generated helpers are behavior only after the app mounts them."""
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    ref.mkdir(parents=True)
    ref.joinpath("structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {
                "tag": "header",
                "class": "style_header__tjhHk",
                "children": [{"tag": "span", "text": "Playbook"}],
            },
            {
                "tag": "section",
                "class": "style_scrollcontainer__Vup4r",
                "children": [{
                    "tag": "svg",
                    "svg": True,
                    "class": "style_grid__buRkg",
                    "styles": {"opacity": "0"},
                }],
            },
        ],
    }), encoding="utf-8")
    ref.joinpath("section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "header", "cls": "style_header__tjhHk"},
        {
            "index": 1,
            "tag": "section",
            "cls": "style_scrollcontainer__Vup4r",
        },
    ]}), encoding="utf-8")
    ref.joinpath("transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "scroll-header-shadow-threshold",
            "trigger": "scroll",
            "target": "header.style_header__tjhHk",
            "animation": {
                "type": "scroll-state-class-toggle",
                "property": "box-shadow,className",
                "threshold": "window.scrollY > 8",
                "from": {"className": "style_header__tjhHk"},
                "to": {
                    "className": (
                        "style_header__tjhHk style_header__shadow__9G5rH"
                    )
                },
            },
        }],
    }), encoding="utf-8")
    ref.joinpath("generation-plan.json").write_text(json.dumps({
        "scrollDriven": {"required": True, "library": "framer-motion"},
        "scrollScrub": {
            "required": True,
            "sites": [{
                "selector": "svg",
                "selectorIndex": 0,
                "scope": ".style_scrollcontainer__Vup4r",
                "progressSource": "target-offset",
                "offset": '["start start", "end end"]',
                "transforms": [{
                    "property": "opacity",
                    "input": "[0, 0.2, 1]",
                    "output": "[0, 1, 1]",
                }],
            }],
        },
    }), encoding="utf-8")
    _seed_impl(impl, {"vite": "5", "react": "18"}, "vite.config.js")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = (impl / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "import ScrollClassToggleDriver" in app
    assert "<ScrollClassToggleDriver />" in app
    assert "import ScrollLinkedStyleDriver" in app
    assert "<ScrollLinkedStyleDriver />" in app
    assert "<ScrollScrub" not in app
    assert (impl / "src" / "lib" / "ScrollClassToggleDriver.tsx").is_file()
    assert (impl / "src" / "lib" / "ScrollLinkedStyleDriver.tsx").is_file()


def test_non_numeric_scrolltrigger_state_does_not_import_missing_driver(
    tmp_path: Path,
) -> None:
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    ref.mkdir(parents=True)
    ref.joinpath("structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{"tag": "section", "class": "esg-hero"}],
    }), encoding="utf-8")
    ref.joinpath("section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "esg-hero"},
    ]}), encoding="utf-8")
    ref.joinpath("transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "hero-enter-leave-state",
            "trigger": "scroll",
            "target": ".esg-hero",
            "animation": {
                "type": "scrolltrigger-class-toggle",
                "start": "stickyHeight * 0.5 0%",
                "end": "stickyHeight * 0.5 + 1 0%",
                "onEnter": "enter",
                "onLeaveBack": "leave",
            },
        }],
    }), encoding="utf-8")
    ref.joinpath("generation-plan.json").write_text(
        json.dumps({"scrollDriven": {"required": False}}),
        encoding="utf-8",
    )
    _seed_impl(impl, {"vite": "5", "react": "18"}, "vite.config.js")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = (impl / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "ScrollClassToggleDriver" not in app
    assert not (impl / "src" / "lib" / "ScrollClassToggleDriver.tsx").exists()


def test_framer_library_only_does_not_wrap_generic_scroll_reveal(tmp_path: Path) -> None:
    """Library-only Framer evidence must not create arbitrary section wrappers."""
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    ref.mkdir(parents=True)
    ref.joinpath("structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{
            "tag": "section",
            "class": "reveal-me",
            "data-scroll-fade": "true",
            "children": [{"tag": "p", "text": "captured as hidden"}],
        }],
    }), encoding="utf-8")
    ref.joinpath("section-map.json").write_text(json.dumps({
        "sections": [{"index": 0, "tag": "section", "cls": "reveal-me"}]
    }), encoding="utf-8")
    ref.joinpath("generation-plan.json").write_text(json.dumps({
        "scrollDriven": {"required": False, "library": None},
        "libraries": {"required": ["framer-motion"]},
    }), encoding="utf-8")
    _seed_impl(impl, {"vite": "5", "react": "18"}, "vite.config.js")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = (impl / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "<ScrollReveal>" not in app
    assert "import ScrollReveal" not in app


def test_explicit_transition_spec_reveal_still_wraps_scroll_reveal(tmp_path: Path) -> None:
    """A true scroll reveal entry is sufficient even without scrollDriven hooks."""
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    ref.mkdir(parents=True)
    ref.joinpath("structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{
            "tag": "section",
            "class": "reveal-me",
            "data-scroll-fade": "true",
            "styles": {"opacity": "0", "transition-property": "opacity"},
            "children": [{"tag": "p", "text": "reveals on scroll"}],
        }],
    }), encoding="utf-8")
    ref.joinpath("section-map.json").write_text(json.dumps({
        "sections": [{"index": 0, "tag": "section", "cls": "reveal-me"}]
    }), encoding="utf-8")
    ref.joinpath("generation-plan.json").write_text(json.dumps({
        "scrollDriven": {"required": False, "library": None},
    }), encoding="utf-8")
    ref.joinpath("transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "section-opacity-reveal",
            "trigger": "scroll-into-view",
            "target": ".reveal-me",
            "animation": {"type": "fade-reveal", "property": "opacity"},
        }]
    }), encoding="utf-8")
    _seed_impl(impl, {"vite": "5", "react": "18"}, "vite.config.js")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = (impl / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "import ScrollReveal" in app
    assert "<ScrollReveal><RevealMe /></ScrollReveal>" in app


def test_motion_site_on_island_stack_warns(tmp_path: Path) -> None:
    """astro/sveltekit build their own island children list (not section_jsx),
    so stamped motion is silently dropped. At minimum the transpiler must WARN
    rather than emit a motion-dead clone with no signal."""
    proc = _run(tmp_path, {"astro": "4", "react": "18"}, None)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "motion NOT mounted" in proc.stderr and "astro" in proc.stderr, (
        f"expected a motion-not-mounted warning for the island stack; got: {proc.stderr!r}"
    )


def test_scaffold_adds_motion_library_dependencies(tmp_path: Path) -> None:
    """gen-B1/H1 follow-up (found live on a Next build): SmoothScroll hard-imports
    `lenis` and the reveal/scrub/state helpers import `framer-motion`. If those
    deps are undeclared the next/vite build fails ('Module not found: Can't
    resolve lenis / framer-motion'). The scaffold must declare them, as it
    already does for swiper."""
    proc = _run(tmp_path, {"vite": "5", "react": "18"}, "vite.config.js")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    deps = json.loads((tmp_path / "impl" / "package.json").read_text(encoding="utf-8")).get("dependencies", {})
    assert "lenis" in deps, f"lenis must be declared when SmoothScroll is emitted; got {list(deps)}"


def test_boolean_state_attr_deferred_to_reveal_driver(tmp_path: Path) -> None:
    """gen-M4: a [data-in-view=true]-gated reveal must not render its pre-state
    forever. The transpiler must NOT bake the boolean attr, but stamp
    data-ui-clone-state-reveal and mount a StateRevealDriver that sets the attr
    on viewport entry (reproducing the ref's own IO controller)."""
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    ref.mkdir(parents=True)
    ref.joinpath("structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{"tag": "section", "class": "hero", "children": [
            {"tag": "div", "class": "card", "data-in-view": "true",
             "children": [{"tag": "p", "text": "reveals on scroll"}]},
        ]}],
    }), encoding="utf-8")
    ref.joinpath("section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8")
    _seed_impl(impl, {"vite": "5", "react": "18"}, "vite.config.js")
    proc = subprocess.run(["bash", str(SCRIPT), str(ref), str(impl)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    card = (impl / "src" / "components" / "Hero.tsx").read_text(encoding="utf-8")
    assert 'data-ui-clone-state-reveal="data-in-view=true"' in card, (
        "the boolean state attr must be deferred to the reveal driver, not dropped"
    )
    assert 'data-in-view="true"' not in card, "the boolean state must NOT be baked"
    driver = impl / "src" / "lib" / "StateRevealDriver.tsx"
    assert driver.is_file(), "StateRevealDriver.tsx must be emitted"
    assert "IntersectionObserver" in driver.read_text(encoding="utf-8")
    app = (impl / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "import StateRevealDriver" in app and "<StateRevealDriver />" in app


def test_no_state_reveal_driver_without_boolean_state_attrs(tmp_path: Path) -> None:
    proc = _run(tmp_path, {"vite": "5", "react": "18"}, "vite.config.js")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (tmp_path / "impl" / "src" / "lib" / "StateRevealDriver.tsx").exists()
    app = (tmp_path / "impl" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "StateRevealDriver" not in app


def test_vite_mounts_scroll_latch_driver(tmp_path: Path) -> None:
    """An emitted latch driver is dead code until the app mounts it, which is
    how a whole class of generated motion silently went missing before."""
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    ref.mkdir(parents=True)
    ref.joinpath("structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{
            "tag": "nav",
            "class": "site_nav",
            "children": [{"tag": "span", "class": "label", "text": "Overview"}],
        }],
    }), encoding="utf-8")
    ref.joinpath("section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "nav", "cls": "site_nav"},
    ]}), encoding="utf-8")
    ref.joinpath("generation-plan.json").write_text(json.dumps({
        "scrollLatch": {
            "required": True,
            "count": 1,
            "sites": [{
                "selector": "nav .label",
                "selectorIndex": 0,
                "progress": 0.1,
                "endState": {"opacity": "1"},
            }],
        },
    }), encoding="utf-8")
    _seed_impl(impl, {"vite": "5", "react": "18"}, "vite.config.js")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (impl / "src" / "lib" / "ScrollLatchDriver.tsx").is_file()
    app = (impl / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "import ScrollLatchDriver" in app
    assert "<ScrollLatchDriver />" in app
