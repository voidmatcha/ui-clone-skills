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
import re
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


def _seed_word_reveal_ref(ref: Path, *, include_highlight_css: bool = True) -> None:
    ref.mkdir(parents=True, exist_ok=True)
    ref.joinpath("structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{
            "tag": "p",
            "class": "mod__text_line",
            "children": [
                {"tag": "span", "class": "mod__line_dimmed", "text": "Real"},
                {"tag": "span", "class": "mod__line_dimmed", "text": "Food"},
            ],
        }],
    }), encoding="utf-8")
    ref.joinpath("section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "p", "cls": "mod__text_line"},
    ]}), encoding="utf-8")
    ref.joinpath("generation-plan.json").write_text(json.dumps({
        "signatureEffects": [{
            "name": "WordRevealText",
            "effectType": "per-word-split",
            "selector": ".mod__text_line, .mod__line_dimmed",
            "wordSelector": ".mod__line_dimmed",
        }],
    }), encoding="utf-8")
    css_dir = ref / "css"
    css_dir.mkdir()
    css = ".mod__line_dimmed{opacity:.35;color:#777}\n"
    if include_highlight_css:
        css += ".mod__line_highlighted{opacity:1;color:#111}\n"
    css_dir.joinpath("ref.css").write_text(css, encoding="utf-8")


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


def test_vite_mounts_state_machine_pixel_style_driver_without_scrollscrub(
    tmp_path: Path,
) -> None:
    """A pixel-domain state machine emits the linked style driver by itself."""
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    ref.mkdir(parents=True)
    ref.joinpath("structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{
            "tag": "header",
            "class": "nav",
            "styles": {"position": "fixed", "top": "80px"},
            "children": [{"tag": "span", "text": "Navigation"}],
        }],
    }), encoding="utf-8")
    ref.joinpath("section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "header", "cls": "nav"},
    ]}), encoding="utf-8")
    ref.joinpath("generation-plan.json").write_text(json.dumps({
        "scrollStateMachine": {
            "required": True,
            "sites": [{
                "selector": "header.nav",
                "selectorIndex": 0,
                "inputDomain": "scroll-y-px",
                "transforms": [{
                    "property": "top",
                    "input": "[0, 260]",
                    "output": "[80, 0]",
                    "unit": "px",
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
    assert "import ScrollLinkedStyleDriver" in app
    assert "<ScrollLinkedStyleDriver />" in app
    assert "import ScrollScrub" not in app
    assert "<ScrollScrub" not in app
    for helper in re.findall(r"import\s+\w+\s+from\s+['\"]\./lib/([^'\"]+)['\"]", app):
        assert (impl / "src" / "lib" / f"{helper}.tsx").is_file(), (
            f"{helper} imported but not emitted"
        )


def test_state_machine_required_false_does_not_mount_or_emit_style_driver(
    tmp_path: Path,
) -> None:
    """A valid-looking disabled state machine must not rely on re-gate cleanup."""
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    ref.mkdir(parents=True)
    ref.joinpath("structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{
            "tag": "header",
            "class": "nav",
            "styles": {"position": "fixed", "top": "80px"},
            "children": [{"tag": "span", "text": "Navigation"}],
        }],
    }), encoding="utf-8")
    ref.joinpath("section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "header", "cls": "nav"},
    ]}), encoding="utf-8")
    ref.joinpath("generation-plan.json").write_text(json.dumps({
        "scrollStateMachine": {
            "required": False,
            "sites": [{
                "selector": "header.nav",
                "selectorIndex": 0,
                "inputDomain": "scroll-y-px",
                "transforms": [{
                    "property": "top",
                    "input": "[0, 260]",
                    "output": "[80, 0]",
                    "unit": "px",
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
    assert "import ScrollLinkedStyleDriver" not in app
    assert "<ScrollLinkedStyleDriver" not in app
    assert not (impl / "src" / "lib" / "ScrollLinkedStyleDriver.tsx").exists()
    assert "re-gate removed unwritten driver mount" not in proc.stderr
    assert "ScrollLinkedStyleDriver" not in proc.stderr


def test_invalid_scrub_autowrap_ranges_do_not_mount_scrollscrub(tmp_path: Path) -> None:
    """Section-level auto-wrap only accepts normalized monotonic progress input."""
    for name, input_range in {
        "percent": "[0, 100]",
        "negative": "[-0.1, 1]",
        "over_one": "[0, 1.2]",
        "non_monotonic": "[0, 0.8, 0.7, 1]",
    }.items():
        ref, impl = tmp_path / name / "ref", tmp_path / name / "impl"
        ref.mkdir(parents=True)
        ref.joinpath("structure.json").write_text(json.dumps({
            "tag": "body",
            "children": [{
                "tag": "section",
                "class": "zoom",
                "styles": {"transform": "scale(0.9)"},
                "children": [{"tag": "p", "text": "zoom"}],
            }],
        }), encoding="utf-8")
        ref.joinpath("section-map.json").write_text(json.dumps({"sections": [
            {"index": 0, "tag": "section", "cls": "zoom"},
        ]}), encoding="utf-8")
        ref.joinpath("generation-plan.json").write_text(json.dumps({
            "scrollScrub": {
                "required": True,
                "sites": [{
                    "selector": ".zoom",
                    "offset": '["start end", "end start"]',
                    "transforms": [{
                        "property": "scale",
                        "input": input_range,
                        "output": "[0.9, 1]",
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
        assert "<ScrollScrub" not in app, f"{input_range} should not auto-wrap"
        assert "import ScrollScrub" not in app


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


def test_vite_mounts_word_reveal_driver(tmp_path: Path) -> None:
    """The ref ships the text pre-split, so there is nothing for the scaffold to
    wrap — without a mounted driver every transpiled word span keeps the dim
    class forever and the reveal renders as a uniformly dim paragraph."""
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    _seed_word_reveal_ref(ref)
    _seed_impl(impl, {"vite": "5", "react": "18"}, "vite.config.js")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    # The mount predicate and the emitter predicate must agree: a mount without
    # the file is an unresolvable import, a file without the mount is dead code.
    assert (impl / "src" / "lib" / "WordRevealDriver.tsx").is_file()
    app = (impl / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "import WordRevealDriver" in app
    assert "<WordRevealDriver />" in app


def test_remix_mounts_word_reveal_driver(tmp_path: Path) -> None:
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    _seed_word_reveal_ref(ref)
    _seed_impl(impl, {"@remix-run/react": "2", "react": "18"}, None)

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (impl / "src" / "lib" / "WordRevealDriver.tsx").is_file()
    page = (impl / "app" / "_index.tsx").read_text(encoding="utf-8")
    assert "import WordRevealDriver" in page
    assert "<WordRevealDriver />" in page


def test_island_stack_warning_mentions_word_reveal_when_required(tmp_path: Path) -> None:
    for stack, deps in {
        "astro": {"astro": "4", "react": "18"},
        "sveltekit": {"@sveltejs/kit": "2", "react": "18"},
    }.items():
        ref, impl = tmp_path / stack / "ref", tmp_path / stack / "impl"
        _seed_word_reveal_ref(ref)
        _seed_impl(impl, deps, None)

        proc = subprocess.run(
            ["bash", str(SCRIPT), str(ref), str(impl)],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "motion NOT mounted" in proc.stderr
        assert "word-reveal" in proc.stderr


def test_word_reveal_does_not_mount_when_derived_highlight_class_is_absent(
    tmp_path: Path,
) -> None:
    """A dim class alone is not enough evidence to invent a highlight toggle."""
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    _seed_word_reveal_ref(ref, include_highlight_css=False)
    _seed_impl(impl, {"vite": "5", "react": "18"}, "vite.config.js")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = (impl / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "WordRevealDriver" not in app


def test_latch_sites_that_all_fail_validation_emit_no_dangling_import(
    tmp_path: Path,
) -> None:
    """App.tsx must never import a driver the emitter did not write.

    The scaffold gates the import on `scrollLatch.sites` being a non-empty
    list; the emitter additionally validates each site, requiring a selector,
    a non-empty endState and a numeric progress. A plan can satisfy the first
    and none of the second — realfood-v2 ships `required: true, count: 3`
    where all three sites are IntersectionObserver descriptions with no
    endState or progress. Every site is dropped, ScrollLatchDriver.tsx is
    never written, and the emitted App.tsx imports it anyway: the generated
    project does not build ("Could not resolve ./lib/ScrollLatchDriver").
    """
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
            "count": 2,
            "sites": [
                # Observer-gated description: no endState, no progress.
                {
                    "id": "rfw-checkmark-draw",
                    "selector": ".rfw_item",
                    "observer": "new IntersectionObserver(...)",
                },
                # Has a selector and progress but an empty endState.
                {"selector": ".other", "progress": 0.4, "endState": {}},
            ],
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
    driver = impl / "src" / "lib" / "ScrollLatchDriver.tsx"
    if "import ScrollLatchDriver" in app:
        assert driver.is_file(), (
            "App.tsx imports ScrollLatchDriver but the emitter wrote no such "
            "file — the generated project cannot build"
        )
    assert "<ScrollLatchDriver />" not in app or driver.is_file()


def test_app_imports_no_lib_module_the_emitter_did_not_write(tmp_path: Path) -> None:
    """Generic build-integrity invariant across ALL emitted drivers.

    Each driver has two independent predicates: one in scaffold_to_jsx.py
    deciding whether App.tsx imports it, one in emit_scroll_helpers.py deciding
    whether the file is written. Whenever those disagree the generated project
    does not compile. Rather than audit every gate pair, assert the property
    they must jointly satisfy — every `./lib/X` App.tsx imports exists on disk.
    The plan below deliberately mixes well-formed and malformed feature blocks.
    """
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
        # required/count asserted, every site unusable (realfood-v2's shape)
        "scrollLatch": {
            "required": True,
            "count": 2,
            "sites": [
                {"id": "a", "selector": ".x", "observer": "new IntersectionObserver(...)"},
                {"selector": ".y", "progress": 0.5, "endState": {}},
            ],
        },
        # required asserted with no usable site payload at all
        "scrollScrub": {"required": True, "sites": []},
        "scrollLinkedStyle": {"required": True, "sites": [{"selector": ".z"}]},
        "smoothScroll": {"required": True},
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
    referenced = set(re.findall(r"""from\s+['"](?:\./|@/)lib/([A-Za-z0-9_]+)['"]""", app))
    missing = sorted(
        name for name in referenced
        if not (impl / "src" / "lib" / f"{name}.tsx").is_file()
        and not (impl / "src" / "lib" / f"{name}.ts").is_file()
    )
    assert not missing, (
        "App.tsx imports lib modules the emitter never wrote, so the generated "
        f"project cannot build: {missing}"
    )
