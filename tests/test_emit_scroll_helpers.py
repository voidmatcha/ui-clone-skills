from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "emit-scroll-helpers.sh"
HELPER = (
    ROOT
    / "skills"
    / "visual-debug"
    / "scripts"
    / "lib"
    / "emit_scroll_helpers.py"
)


def test_shell_has_no_large_heredoc() -> None:
    """Keep the large Python emitter out of Bash's pipe-backed heredoc path."""
    shell = SCRIPT.read_text(encoding="utf-8")
    assert "<<" not in shell
    assert HELPER.is_file()
    assert 'python3 "$SCRIPTS_DIR/lib/emit_scroll_helpers.py"' in shell


def test_helper_defers_annotations_for_system_python3() -> None:
    """The shell entrypoint may resolve to macOS Python 3.9 outside uv."""
    source = HELPER.read_text(encoding="utf-8")

    assert source.startswith("from __future__ import annotations\n")


def test_completes_on_current_bash_without_compat(tmp_path: Path) -> None:
    """The default Bash must not need inherited heredoc compatibility state."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    impl.mkdir()
    (ref / "generation-plan.json").write_text(
        json.dumps({"smoothScroll": {"required": False, "config": {}}}),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("BASH_COMPAT", None)
    bash = shutil.which("bash")
    assert bash is not None

    proc = subprocess.run(
        [bash, str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        env=env,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "no scroll helpers required" in proc.stdout


def test_emits_smoothscroll_with_real_lenis_config(tmp_path: Path) -> None:
    """When generation-plan.json requires smooth scroll, emit a deterministic
    src/lib/SmoothScroll.tsx wired with the site's REAL Lenis options (Fix 28),
    so the impl uses them instead of hand-rolled defaults."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {
            "required": True,
            "library": "lenis",
            "config": {"lerp": 0.1, "duration": 1.2, "wheelMultiplier": 1, "smoothWheel": True},
        },
    }), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    f = impl / "src" / "lib" / "SmoothScroll.tsx"
    assert f.exists(), "SmoothScroll.tsx must be emitted when smoothScroll.required"
    t = f.read_text(encoding="utf-8")
    assert "new Lenis(" in t
    assert "lerp: 0.1" in t
    assert "duration: 1.2" in t
    assert "wheelMultiplier: 1" in t
    assert "smoothWheel: true" in t, "JSON booleans must render as JS true/false"
    assert "requestAnimationFrame" in t, "must drive Lenis with a raf loop"
    assert "lenis.destroy()" in t, "must clean up on unmount"


def test_smoothscroll_dispatches_ui_clone_scroll_event(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "smoothScroll": {
                    "required": True,
                    "library": "lenis",
                    "config": {"lerp": 0.1},
                },
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    t = (impl / "src" / "lib" / "SmoothScroll.tsx").read_text(encoding="utf-8")
    assert 'const notifyScroll = () => window.dispatchEvent(new Event("ui-clone-scroll"));' in t
    assert 'lenis.on("scroll", notifyScroll);' in t
    assert 'lenis.off("scroll", notifyScroll);' in t
    assert "lenis.destroy()" in t


def test_smoothscroll_honors_capture_flag(tmp_path: Path) -> None:
    """gen-H2: section_capture sets window.__UI_CLONE_CAPTURE__ before forcing
    native scrollTo to crop exact ref frames. If the emitted SmoothScroll drives
    Lenis' raf loop unconditionally, Lenis reverts the forced scroll (actualY->0,
    wrong-frame crops on Lenis sites). The emitted component must honor the flag
    (and prefers-reduced-motion) and NOT run the raf loop while capturing."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": True, "library": "lenis", "config": {"lerp": 0.1}},
    }), encoding="utf-8")
    proc = subprocess.run(["bash", str(SCRIPT), str(ref), str(impl)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    t = (impl / "src" / "lib" / "SmoothScroll.tsx").read_text(encoding="utf-8")
    assert "__UI_CLONE_CAPTURE__" in t, "SmoothScroll must honor the capture flag"
    assert "prefers-reduced-motion" in t, "and prefers-reduced-motion"


def test_smoothscroll_is_a_client_component(tmp_path: Path) -> None:
    """Next App Router: a component using useEffect MUST carry a 'use client'
    directive or the RSC build fails ('You're importing a module that depends on
    useEffect into a React Server Component'). Every other emitted motion helper
    already has it; SmoothScroll must too — surfaced live once gen-H1 made the
    Next entry actually mount SmoothScroll."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": True, "library": "lenis", "config": {"lerp": 0.1}},
    }), encoding="utf-8")
    proc = subprocess.run(["bash", str(SCRIPT), str(ref), str(impl)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    first = (impl / "src" / "lib" / "SmoothScroll.tsx").read_text(encoding="utf-8").lstrip().splitlines()[0]
    assert "use client" in first, f"SmoothScroll.tsx must start with a use-client directive; got {first!r}"


# The lenis/framer-motion dependency injection lives in scaffold-to-jsx.sh (the
# python transpile), not emit-scroll-helpers.sh, so its test lives in
# tests/test_scaffold_stack_motion_drivers.py.


def test_smoothscroll_exposes_lenis_handle_for_reprobe(tmp_path: Path) -> None:
    """F1: the transition-fires scrub re-probe drives the engine's VIRTUAL scroll
    via window.__lenis (transition-fires-check.sh:753,770-772) to tell a DEAD scrub
    (engine advances, transform/opacity flat -> fail) from a genuinely UNMEASURABLE
    one. If the emitter never exposes the real instance, engineDriven stays false on
    our own clones and every dead scrub is misclassified 'unmeasurable' not 'fail'
    (transition_fires.py:861) -> merely mounting Lenis launders FAILs into passes.
    The emitter must publish the instance as window.__lenis and clear it on unmount
    so a stale handle from a prior mount can't drive the re-probe."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": True, "library": "lenis", "config": {"lerp": 0.1}},
    }), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    t = (impl / "src" / "lib" / "SmoothScroll.tsx").read_text(encoding="utf-8")
    assert "(window as any).__lenis = lenis" in t, (
        "must expose the real Lenis instance as window.__lenis so the scrub "
        "re-probe can drive the engine (else dead scrubs pass as 'unmeasurable')"
    )
    assert "(window as any).__lenis = null" in t, (
        "must clear window.__lenis on unmount so a stale handle can't drive the "
        "re-probe of a later mount"
    )


def test_no_helper_when_smoothscroll_not_required(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(
        json.dumps({"smoothScroll": {"required": False, "config": {}}}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (impl / "src" / "lib" / "SmoothScroll.tsx").exists()


def test_emits_scrollscrub_with_grounded_bands(tmp_path: Path) -> None:
    """plan.scrollScrub (the ref's concrete scroll-scrub tables) must emit a
    reusable ScrollScrub.tsx primitive + a scrollScrubSites.ts data file carrying
    the REAL bands. The #3 background zoom is a `scale` band straddling 1.0 with a
    spring; implausible property bindings (opacity output [80,100]) are filtered."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": False, "config": {}},
        "scrollScrub": {
            "required": True,
            "library": "framer-motion",
            "count": 2,
            "sites": [
                {
                    "offset": '["start end","end start"]',
                    "transforms": [
                        {"input": "k?[0,.05,.75,.9]:[0,.1,.75,.9]",
                         "output": "[.9,1,1,1]", "property": "scale"},
                        {"input": "[0,.3]", "output": "[80,100]", "property": "opacity"},
                    ],
                },
                {
                    "offset": '["start end","end start"]',
                    "transforms": [
                        {"input": "[.55,.7]", "output": "[1,0]", "property": "opacity"},
                    ],
                },
            ],
        },
    }), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    comp = impl / "src" / "lib" / "ScrollScrub.tsx"
    data = impl / "src" / "lib" / "scrollScrubSites.ts"
    assert comp.exists() and data.exists()

    ct = comp.read_text(encoding="utf-8")
    assert "useScroll({ target: ref" in ct
    assert "useTransform(scrollYProgress" in ct
    assert "useSpring(" in ct
    assert 'data-scroll-scrub="1"' in ct

    dt = data.read_text(encoding="utf-8")
    sites = json.loads(dt.split("scrollScrubSites: ScrubSite[] = ", 1)[1].rsplit(";", 1)[0])
    # site 0: scale band kept (ternary input -> first bracket), spring flagged;
    # the implausible opacity [80,100] is dropped.
    assert sites[0]["scale"] == [[0.0, 0.05, 0.75, 0.9], [0.9, 1.0, 1.0, 1.0]]
    assert sites[0].get("spring") is True
    assert "opacity" not in sites[0]
    # site 1: a real fade-out opacity band survives.
    assert sites[1]["opacity"] == [[0.55, 0.7], [1.0, 0.0]]
    assert sites[1]["offset"] == ["start end", "end start"]


def test_scrollscrub_drops_pixel_domain_input_bands(tmp_path: Path) -> None:
    """ScrollScrub consumes target scrollYProgress, so serialized input bands must
    already be normalized progress fractions. Pixel-domain ranges like the nav
    ``useTransform(scrollY, [0, 100], ...)`` shape must not survive into the data
    file where they would be replayed against scrollYProgress."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": False, "config": {}},
        "scrollScrub": {
            "required": True,
            "library": "framer-motion",
            "count": 1,
            "sites": [
                {
                    "offset": '["start end","end start"]',
                    "transforms": [
                        {"input": "[37,464]", "output": "[0,1]", "property": "opacity"},
                        {"input": "[0,1]", "output": "[0.9,1]", "property": "scale"},
                    ],
                },
            ],
        },
    }), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    dt = (impl / "src" / "lib" / "scrollScrubSites.ts").read_text(encoding="utf-8")
    sites = json.loads(dt.split("scrollScrubSites: ScrubSite[] = ", 1)[1].rsplit(";", 1)[0])
    assert "opacity" not in sites[0], "pixel-domain input range must be dropped"
    assert sites[0]["scale"] == [[0.0, 1.0], [0.9, 1.0]]


def test_scrollscrub_drops_generic_blur_bands_but_keeps_valid_generic_band(
    tmp_path: Path,
) -> None:
    """Generic ScrollScrub has no blur prop/style, so blur is linked-driver only."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "smoothScroll": {"required": False, "config": {}},
                "scrollScrub": {
                    "required": True,
                    "library": "framer-motion",
                    "sites": [
                        {
                            "offset": '["start end","end start"]',
                            "transforms": [
                                {
                                    "input": "[0,1]",
                                    "output": "[0,20]",
                                    "property": "blur",
                                },
                                {
                                    "input": "[0,1]",
                                    "output": "[0.9,1]",
                                    "property": "scale",
                                },
                            ],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    dt = (impl / "src" / "lib" / "scrollScrubSites.ts").read_text(encoding="utf-8")
    sites = json.loads(
        dt.split("scrollScrubSites: ScrubSite[] = ", 1)[1].rsplit(";", 1)[0]
    )
    assert "blur" not in sites[0]
    assert sites[0]["scale"] == [[0.0, 1.0], [0.9, 1.0]]
    assert not (impl / "src" / "lib" / "scrollLinkedStyleSites.ts").exists()


def test_scrub_site_spring_params_override_invented_constants(tmp_path: Path) -> None:
    """`spring` was inferred from "the site has a scale band" and its stiffness/
    damping were invented constants baked into the driver. When the plan carries
    the ref's decompiled `spring` params they are the evidence: the site must
    emit them as springConfig, and a sprung band on a NON-scale channel (here a
    fade the bundle smoothed) must still be flagged rather than replayed as a
    bare lerp."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": False, "config": {}},
        "scrollScrub": {
            "required": True,
            "library": "framer-motion",
            "count": 1,
            "sites": [
                {
                    "offset": '["start start","end end"]',
                    "spring": {"stiffness": 900, "damping": 60},
                    "transforms": [
                        {"input": "[0,.3]", "output": "[0,1]", "property": "opacity"},
                    ],
                },
            ],
        },
    }), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    dt = (impl / "src" / "lib" / "scrollScrubSites.ts").read_text(encoding="utf-8")
    sites = json.loads(dt.split("scrollScrubSites: ScrubSite[] = ", 1)[1].rsplit(";", 1)[0])
    assert sites[0].get("spring") is True, "bundle-declared spring not honoured on a non-scale band"
    assert sites[0].get("springConfig") == {"stiffness": 900, "damping": 60}


def test_scrollscrub_driver_springs_from_ref_config_not_baked_constants(tmp_path: Path) -> None:
    """The driver hard-coded `useSpring(mv, {stiffness: 120, damping: 30})` and
    sprung only the scale channels, so a site's real decompiled params could not
    change replay. It must take springConfig (falling back to the old constants
    when a site carries none) and be able to spring the non-scale channels the
    bundle sprung."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": False, "config": {}},
        "scrollScrub": {
            "required": True,
            "library": "framer-motion",
            "count": 1,
            "sites": [
                {
                    "offset": '["start start","end end"]',
                    "spring": {"stiffness": 900, "damping": 60},
                    "transforms": [
                        {"input": "[0,.3]", "output": "[0,1]", "property": "opacity"},
                        {"input": "[0,.3]", "output": "[80,0]", "property": "y"},
                    ],
                },
            ],
        },
    }), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    ct = (impl / "src" / "lib" / "ScrollScrub.tsx").read_text(encoding="utf-8")
    assert "springConfig" in ct, "driver cannot receive the ref's spring params"
    # the sprung channels must include the non-scale ones the bundle sprung
    assert "spOpacity" in ct and "spY" in ct


def test_emits_scrollwordhighlight_when_declared(tmp_path: Path) -> None:
    """A declared per-word-scroll-highlight signatureEffect emits a reusable
    ScrollWordHighlight.tsx primitive (word split + scroll binding +
    useMotionValueEvent advancing an active index)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": False, "config": {}},
        "signatureEffects": [{
            "name": "ScrollWordHighlight",
            "effectType": "per-word-scroll-highlight",
            "trigger": {"type": "scroll", "scrub": True},
            "animation": {"properties": ["color"], "perWord": True},
        }],
    }), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    f = impl / "src" / "lib" / "ScrollWordHighlight.tsx"
    assert f.exists()
    t = f.read_text(encoding="utf-8")
    assert "useScroll({" in t
    assert "useMotionValueEvent" in t
    assert 'split(" ")' in t
    assert 'data-scroll-word-highlight="1"' in t


def test_emits_wordrevealdriver_for_pre_split_word_spans(tmp_path: Path) -> None:
    """A per-word-split effect whose wordSelector is a bare dim class emits
    WordRevealDriver.tsx, which adopts the transpiled spans in place and only
    toggles the ref stylesheet's own dim/highlight class pair."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "styles.css").write_text(
        ".mod__line_dimmed{color:#999}.mod__line_highlighted{color:#111}",
        encoding="utf-8",
    )
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": False, "config": {}},
        "signatureEffects": [{
            "name": "WordRevealText",
            "effectType": "per-word-split",
            "selector": ".mod__text_line, .mod__line_dimmed",
            "wordSelector": ".mod__line_dimmed",
        }],
    }), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    f = impl / "src" / "lib" / "WordRevealDriver.tsx"
    assert f.exists()
    t = f.read_text(encoding="utf-8")
    # Both class names come from the ref stylesheet — the driver owns neither
    # a colour nor an opacity, so the reveal cannot drift from the reference.
    assert 'DIM_CLASS = "mod__line_dimmed"' in t
    assert 'HIGHLIGHT_CLASS = "mod__line_highlighted"' in t
    assert "color" not in t.split("const DIM_CLASS")[1].lower()
    # Grouping must climb to the paragraph: the transpiler gives every word its
    # own anonymous wrapper span, so parentElement alone yields one word groups.
    assert 'closest("p")' in t


def test_no_wordrevealdriver_when_highlight_class_lacks_css_evidence(tmp_path: Path) -> None:
    """The dimmed -> highlighted name pair is only safe when the reference CSS
    actually defines the highlighted class. Do not invent ``*_highlighted`` from
    a captured dim class alone."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "styles.css").write_text(".foo_dimmed{color:#999}", encoding="utf-8")
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": False, "config": {}},
        "signatureEffects": [{
            "name": "WordRevealText",
            "effectType": "per-word-split",
            "selector": ".foo_text, .foo_dimmed",
            "wordSelector": ".foo_dimmed",
        }],
    }), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (impl / "src" / "lib" / "WordRevealDriver.tsx").exists()


def test_no_wordrevealdriver_for_compound_word_selector(tmp_path: Path) -> None:
    """A wordSelector that is not a bare single class has no derivable highlight
    counterpart, so no driver is emitted rather than one toggling a guess."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": False, "config": {}},
        "signatureEffects": [{
            "effectType": "per-word-split",
            "wordSelector": ".wrap > span.faded",
        }],
    }), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (impl / "src" / "lib" / "WordRevealDriver.tsx").exists()


def test_no_scrollwordhighlight_when_not_declared(tmp_path: Path) -> None:
    """No per-word effect declared → ScrollWordHighlight.tsx not emitted."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": False, "config": {}},
        "signatureEffects": [{"effectType": "per-character-scroll-scrub"}],
    }), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (impl / "src" / "lib" / "ScrollWordHighlight.tsx").exists()


def test_no_scrollscrub_when_not_required(tmp_path: Path) -> None:
    """No scrollScrub contract (or no usable bands) → neither file emitted."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": False, "config": {}},
        "scrollScrub": {"required": False, "count": 0, "sites": []},
    }), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (impl / "src" / "lib" / "ScrollScrub.tsx").exists()
    assert not (impl / "src" / "lib" / "scrollScrubSites.ts").exists()


def test_emits_scrollreveal_when_scroll_driven_required(tmp_path: Path) -> None:
    """When generation-plan.json requires Framer scrollDriven reveals, emit a
    deterministic src/lib/ScrollReveal.tsx using the render-verified canonical
    pattern (Fix 31): useScroll(target, offset) + useTransform(scrollYProgress)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": False, "config": {}},
        "scrollDriven": {
            "required": True,
            "library": "framer-motion",
            "hooks": ["useScroll", "useTransform", "scrollYProgress"],
        },
    }), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    f = impl / "src" / "lib" / "ScrollReveal.tsx"
    assert f.exists(), "ScrollReveal.tsx must be emitted when scrollDriven.required"
    t = f.read_text(encoding="utf-8")
    assert "useScroll({" in t
    assert "useTransform(scrollYProgress" in t
    assert "offset:" in t, "must use a target-relative offset window"
    assert "motion.div" in t
    assert "ref={ref}" in t
    assert "UseScrollOptions" not in t, "ScrollReveal must not emit an unused type import"


def test_scrollreveal_scrub_latches_above_the_fold(tmp_path: Path) -> None:
    """A reveal already inside the first viewport at load never receives the
    scroll delta that would settle it, so the scrub pins it at the from-state
    (opacity 0) forever and every capture of that section renders blank. The
    scrub variant must latch such sections — and every section under the capture
    harness flag — to the to-state."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": False, "config": {}},
        "scrollDriven": {
            "required": True,
            "library": "framer-motion",
            "hooks": ["useScroll", "useTransform", "scrollYProgress"],
        },
    }), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    t = (impl / "src" / "lib" / "ScrollReveal.tsx").read_text(encoding="utf-8")
    assert "__UI_CLONE_CAPTURE__" in t, "capture harness must force the settled state"
    assert "getBoundingClientRect()" in t, "latch must test the live viewport position"
    assert "window.innerHeight" in t
    assert 'data-reveal-settled' in t, "latched state must be observable from a probe"
    assert "settled ? { opacity: 1, y: 0 }" in t, "latch must pin the to-state"
    assert "new IntersectionObserver" not in t, (
        "latch is scroll-driven — a per-element IO froze reveals on earlier runs"
    )


def test_scrollreveal_parametrized_from_transition_spec(tmp_path: Path) -> None:
    """P6 core (transition-fires): the ref's reveal is a Framer whileInView
    one-shot (opacity 0->1, y 80->0, 0.8s, cubic-bezier) per transition-spec —
    but the emitter hardcoded a generic scrub (y 60, no ease/duration), so the
    measured runtime trajectory mismatched the ref's. When transition-spec.json
    has an into-view reveal entry, emit ScrollReveal as a spec-parametrized
    whileInView one-shot grounded in the ref's real values."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": False, "config": {}},
        "scrollDriven": {"required": True, "library": "framer-motion",
                         "hooks": ["useScroll", "useTransform", "scrollYProgress"]},
    }), encoding="utf-8")
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "scroll-reveal-fade-up",
             "trigger": "scroll into view (IntersectionObserver via Framer whileInView)",
             "bundle_branch": "whileInView (default, once viewport)",
             "animation": {"property": "opacity, y",
                           "from": {"opacity": 0, "y": 80},
                           "to": {"opacity": 1, "y": 0},
                           "duration": 0.8,
                           "ease": "[0.165, 0.84, 0.44, 1]"}},
        ],
    }), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    t = (impl / "src" / "lib" / "ScrollReveal.tsx").read_text(encoding="utf-8")
    assert "whileInView" in t, f"spec reveal must emit a whileInView one-shot:\n{t}"
    assert '"y": 80' in t.replace("y: 80", '"y": 80') or "y: 80" in t, "spec from.y must be used"
    assert "0.8" in t, "spec duration must be used"
    assert "0.165" in t and "0.84" in t, "spec cubic-bezier ease must be used"
    assert "once: true" in t, "ref reveals once per viewport entry"
    # Fix 79 — the wrapper is the fires-probe's deterministic fallback target
    # for prose-target fade-up entries.
    assert 'data-scroll-reveal="1"' in t, "wrapper must carry the probe marker"
    # spec says whileInView DEFAULT viewport: no amount threshold — a section
    # taller than the viewport can never reach an invented ratio like 0.2
    # (633px viewport / 1192px section = 0.16 max at entry), so the reveal
    # would never fire on tall sections.
    assert "amount" not in t, "must use framer's default 'some' intersection"
    # the generic scrub must NOT be emitted when the spec grounds the reveal
    assert "useTransform(scrollYProgress, [0, 1], [60, 0])" not in t


def test_scroll_state_driver_emitted_from_spec(tmp_path: Path) -> None:
    """transition-fires (P6): the ref's scroll-position-state fade
    (animate:{opacity: a?1:.5, y:80*!a}) is JS-driven — no CSS transition
    marker — so captured elements freeze at the inactive opacity (0.5) and the
    clone shows no runtime delta when the trigger is driven. When
    transition-spec declares the state-driven entry, emit a ScrollStateDriver
    (IntersectionObserver + WAAPI) that animates [data-scroll-fade] elements to
    the active state with the spec's real duration/ease — a measured
    computed-opacity delta."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": False, "config": {}},
        "scrollDriven": {"required": True, "library": "framer-motion",
                         "hooks": ["useScroll", "useTransform", "scrollYProgress"]},
    }), encoding="utf-8")
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "scroll-progress-fade",
             "trigger": "scroll position state (a ? active : inactive)",
             "bundle_branch": "animate:{opacity:a?1:.5,y:80*!a}",
             "animation": {"property": "opacity, y",
                           "from": {"opacity": 0.5, "y": 80},
                           "to": {"opacity": 1, "y": 0},
                           "duration": 0.8,
                           "ease": "[0.16, 1, 0.3, 1]"}},
        ],
    }), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    f = impl / "src" / "lib" / "ScrollStateDriver.tsx"
    assert f.exists(), "ScrollStateDriver must be emitted for a state-driven spec entry"
    t = f.read_text(encoding="utf-8")
    assert "data-scroll-fade" in t, "driver must target the transpiler's stamp"
    assert "IntersectionObserver" in t
    assert "0.5" in t and "800" in t, "spec from-opacity and duration(ms) must be used"
    assert "cubic-bezier(0.16, 1, 0.3, 1)" in t, "spec ease must be used"
    assert ".animate(" in t, "must use WAAPI so the delta is measurable in computed style"


def test_stroke_draw_handling_emitted_from_spec(tmp_path: Path) -> None:
    """transition-fires (P6): the ref draws decorative SVG strokes in via
    strokeDashoffset (initial:{strokeDashoffset:len} animate:{...:0}). Captured
    paths freeze with a stroke-dasharray and never animate. When
    transition-spec declares the stroke-draw entry, the emitted driver must
    animate [data-stroke-draw] paths' strokeDashoffset getTotalLength->0 with
    the spec's duration/ease."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": False, "config": {}},
        "scrollDriven": {"required": True, "library": "framer-motion", "hooks": []},
    }), encoding="utf-8")
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "svg-stroke-draw",
             "trigger": "in-view / scroll state (t ? drawn : hidden)",
             "bundle_branch": "initial:{strokeDashoffset:o} animate:{strokeDashoffset:t?0:o}",
             "animation": {"property": "strokeDashoffset",
                           "from": "dashLength", "to": 0,
                           "duration": 1.0, "ease": "[0.25, 1, 0.5, 1]"}},
        ],
    }), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    f = impl / "src" / "lib" / "ScrollStateDriver.tsx"
    assert f.exists(), "driver must be emitted for a stroke-draw spec entry"
    t = f.read_text(encoding="utf-8")
    assert "data-stroke-draw" in t
    assert "getTotalLength" in t, "dash length must come from the real path geometry"
    assert "strokeDashoffset" in t
    assert "1000" in t, "spec duration (ms) must be used"
    assert "cubic-bezier(0.25, 1, 0.5, 1)" in t, "spec ease must be used"


def test_no_scroll_state_driver_without_spec_entry(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": False, "config": {}},
        "scrollDriven": {"required": True, "library": "framer-motion", "hooks": []},
    }), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (impl / "src" / "lib" / "ScrollStateDriver.tsx").exists()


def test_no_scrollreveal_when_not_required(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": False, "config": {}},
        "scrollDriven": {"required": False, "hooks": []},
    }), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (impl / "src" / "lib" / "ScrollReveal.tsx").exists()


def test_smoothscroll_omits_unknown_config_keys(tmp_path: Path) -> None:
    """Only known Lenis options are emitted; non-numeric/bool junk is skipped
    so the generated TSX never contains an unparseable option."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {
            "required": True,
            "config": {"lerp": 0.08, "bogus": "drop me", "easing": "expoOut"},
        },
    }), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    t = (impl / "src" / "lib" / "SmoothScroll.tsx").read_text(encoding="utf-8")
    assert "lerp: 0.08" in t
    assert "bogus" not in t
    assert "drop me" not in t


def test_emits_scroll_class_toggle_driver_from_transition_spec(
    tmp_path: Path,
) -> None:
    """A scroll threshold that changes className must become executable code.

    Treating this as a numeric scroll scrub produces a dead placeholder because
    class names cannot be interpolated. The emitted singleton must toggle the
    exact class on the exact target and remove it when scroll returns above the
    threshold.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(
        json.dumps({"scrollDriven": {"required": False}}),
        encoding="utf-8",
    )
    (ref / "transition-spec.json").write_text(json.dumps({
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

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    driver = impl / "src" / "lib" / "ScrollClassToggleDriver.tsx"
    assert driver.is_file(), "className scroll entries need a runtime driver"
    source = driver.read_text(encoding="utf-8")
    assert "header.style_header__tjhHk" in source
    assert "style_header__shadow__9G5rH" in source
    assert "window.scrollY > 8" in source
    assert "classList.toggle" in source
    assert "addEventListener(\"scroll\"" in source


def test_emits_document_progress_driver_for_runtime_sampled_sites(
    tmp_path: Path,
) -> None:
    """Runtime-sampled descendant curves need target-specific replay.

    A generic section-level ScrollScrub cannot reproduce an SVG opacity curve,
    two distinct ``g#even`` scale curves, and a descendant width/radius curve.
    Emit a singleton that scopes selectors, preserves duplicate indexes, and
    interpolates against document scroll progress from the captured samples.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "scrollDriven": {"required": True, "library": "framer-motion"},
        "scrollScrub": {
            "required": True,
            "sites": [
                {
                    "selector": "svg",
                    "selectorIndex": 0,
                    "scope": ".style_scrollcontainer__Vup4r",
                    "progressSource": "document",
                    "transforms": [{
                        "property": "opacity",
                        "input": "[0, 0.15, 0.2, 1]",
                        "output": "[0, 0.22, 1, 1]",
                    }],
                },
                {
                    "selector": "g#even",
                    "selectorIndex": 1,
                    "scope": ".style_scrollcontainer__Vup4r",
                    "progressSource": "document",
                    "transforms": [{
                        "property": "scale",
                        "input": "[0, 0.1, 0.15]",
                        "output": "[0.575, 0.575, 0.6685]",
                    }],
                },
                {
                    "selector": "div.style_imgWrapper__AFuB_",
                    "selectorIndex": 0,
                    "scope": ".style_scrollcontainer__Vup4r",
                    "progressSource": "document",
                    "transforms": [
                        {
                            "property": "width",
                            "input": "[0, 0.15, 0.2, 1]",
                            "output": "[1376, 349.897, 196.571, 196.571]",
                        },
                        {
                            "property": "borderRadius",
                            "input": "[0, 0.15, 0.2, 1]",
                            "output": "[16, 28.8, 28.8, 28.8]",
                        },
                    ],
                },
            ],
        },
    }), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    driver = impl / "src" / "lib" / "ScrollLinkedStyleDriver.tsx"
    data = impl / "src" / "lib" / "scrollLinkedStyleSites.ts"
    assert driver.is_file() and data.is_file()
    source = driver.read_text(encoding="utf-8")
    assert "document.documentElement.scrollHeight - window.innerHeight" in source
    assert "requestAnimationFrame" in source
    assert "querySelectorAll" in source
    assert "selectorIndex" in source
    # written via setProperty(..., "important") so an authored pin cannot
    # outrank the measured band (see the important-priority test below)
    assert 'set("transform"' in source
    assert 'set("width"' in source
    assert 'set("border-radius"' in source
    payload = data.read_text(encoding="utf-8")
    assert '"scope": ".style_scrollcontainer__Vup4r"' in payload
    assert '"selectorIndex": 1' in payload


def test_document_progress_driver_drops_pixel_domain_input_bands(
    tmp_path: Path,
) -> None:
    """The selector-scoped linked driver also consumes normalized progress, not
    raw scrollY pixels. A pixel-domain input band must be filtered before it can
    reach scrollLinkedStyleSites.ts."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(json.dumps({
        "scrollDriven": {"required": True, "library": "framer-motion"},
        "scrollScrub": {
            "required": True,
            "sites": [
                {
                    "selector": "nav",
                    "selectorIndex": 0,
                    "progressSource": "document",
                    "transforms": [
                        {"property": "y", "input": "[0,100]", "output": "[56,20]"},
                        {"property": "opacity", "input": "[0,1]", "output": "[0,1]"},
                    ],
                },
            ],
        },
    }), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = (impl / "src" / "lib" / "scrollLinkedStyleSites.ts").read_text(
        encoding="utf-8"
    )
    sites = json.loads(
        payload.split("scrollLinkedStyleSites: ScrollLinkedStyleSite[] = ", 1)[1]
        .rsplit(";", 1)[0]
    )
    assert "y" not in sites[0]["bands"], "pixel-domain input range must be dropped"
    assert sites[0]["bands"]["opacity"] == [[0.0, 1.0], [0.0, 1.0]]
    helper = HELPER.read_text(encoding="utf-8")
    scrub_keys = helper.split("_SCRUB_PROP_KEYS = (", 1)[1].split(")", 1)[0]
    assert '"top"' not in scrub_keys


def test_document_progress_driver_emits_supported_blur_band(tmp_path: Path) -> None:
    """Selector-scoped runtime blur bands must serialize and set filter only as blur(px)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "scrollDriven": {"required": True, "library": "framer-motion"},
                "scrollScrub": {
                    "required": True,
                    "sites": [
                        {
                            "selector": ".hero-art",
                            "selectorIndex": 0,
                            "progressSource": "document-progress",
                            "transforms": [
                                {
                                    "property": "blur",
                                    "input": "[0, 0.5, 1]",
                                    "output": "[20, 8.5, 0]",
                                }
                            ],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = (impl / "src" / "lib" / "scrollLinkedStyleSites.ts").read_text(
        encoding="utf-8"
    )
    sites = json.loads(
        payload.split("scrollLinkedStyleSites: ScrollLinkedStyleSite[] = ", 1)[1]
        .rsplit(";", 1)[0]
    )
    assert sites[0]["bands"]["blur"] == [[0.0, 0.5, 1.0], [20.0, 8.5, 0.0]]
    assert "brightness" not in sites[0]["bands"]

    source = (impl / "src" / "lib" / "ScrollLinkedStyleDriver.tsx").read_text(
        encoding="utf-8"
    )
    assert 'if (bands.blur) set("filter", `blur(${value("blur")}px)`);' in source
    assert 'set("filter"' in source


def test_document_progress_driver_emits_blur_brightness_filter(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "scrollDriven": {"required": True, "library": "framer-motion"},
                "scrollScrub": {
                    "required": True,
                    "sites": [
                        {
                            "selector": ".hero-art",
                            "selectorIndex": 0,
                            "progressSource": "document-progress",
                            "transforms": [
                                {
                                    "property": "blur",
                                    "input": "[0, 1]",
                                    "output": "[0, 12]",
                                },
                                {
                                    "property": "brightness",
                                    "input": "[0, 1]",
                                    "output": "[1, 0.25]",
                                },
                            ],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    source = (impl / "src" / "lib" / "ScrollLinkedStyleDriver.tsx").read_text(
        encoding="utf-8"
    )
    payload = (impl / "src" / "lib" / "scrollLinkedStyleSites.ts").read_text(
        encoding="utf-8"
    )
    sites = json.loads(
        payload.split("scrollLinkedStyleSites: ScrollLinkedStyleSite[] = ", 1)[1]
        .rsplit(";", 1)[0]
    )

    assert sites[0]["bands"]["blur"] == [[0.0, 1.0], [0.0, 12.0]]
    assert sites[0]["bands"]["brightness"] == [[0.0, 1.0], [1.0, 0.25]]
    assert (
        'set("filter", `blur(${value("blur")}px) brightness(${value("brightness")})`)'
        in source
    )


def test_document_progress_driver_drops_implausible_blur_bands(tmp_path: Path) -> None:
    """Negative and huge blur bands are not plausible runtime replay targets."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "scrollDriven": {"required": True, "library": "framer-motion"},
                "scrollScrub": {
                    "required": True,
                    "sites": [
                        {
                            "selector": ".negative",
                            "progressSource": "document-progress",
                            "transforms": [
                                {
                                    "property": "blur",
                                    "input": "[0, 1]",
                                    "output": "[0, -1]",
                                }
                            ],
                        },
                        {
                            "selector": ".huge",
                            "progressSource": "document-progress",
                            "transforms": [
                                {
                                    "property": "blur",
                                    "input": "[0, 1]",
                                    "output": "[0, 240]",
                                }
                            ],
                        },
                        {
                            "selector": ".opacity",
                            "progressSource": "document-progress",
                            "transforms": [
                                {
                                    "property": "opacity",
                                    "input": "[0, 1]",
                                    "output": "[0, 1]",
                                }
                            ],
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = (impl / "src" / "lib" / "scrollLinkedStyleSites.ts").read_text(
        encoding="utf-8"
    )
    sites = json.loads(
        payload.split("scrollLinkedStyleSites: ScrollLinkedStyleSite[] = ", 1)[1]
        .rsplit(";", 1)[0]
    )
    assert [site["selector"] for site in sites] == [".opacity"]
    assert sites[0]["bands"]["opacity"] == [[0.0, 1.0], [0.0, 1.0]]


def test_emits_raw_pixel_scroll_state_machine_driver(tmp_path: Path) -> None:
    """scrollStateMachine sites are real-target raw scrollY style bands.

    They are not progress-domain ScrollScrub wrapper sites, so a state-only plan
    should emit the selector-scoped linked driver and data without emitting the
    wrapper primitive or scrollScrub data.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "scrollStateMachine": {
                    "required": True,
                    "sites": [
                        {
                            "specId": "header.compact.top",
                            "selector": "header.site-nav",
                            "inputDomain": "scroll-y-px",
                            "transforms": [
                                {
                                    "property": "top",
                                    "input": "[0, 100]",
                                    "output": "[56, 20]",
                                    "unit": "px",
                                }
                            ],
                            "source": "runtime-style-diff",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    driver = impl / "src" / "lib" / "ScrollLinkedStyleDriver.tsx"
    data = impl / "src" / "lib" / "scrollLinkedStyleSites.ts"
    assert driver.is_file() and data.is_file()
    emitted_helpers = {path.name for path in (impl / "src" / "lib").iterdir()}
    assert emitted_helpers == {
        "ScrollLinkedStyleDriver.tsx",
        "scrollLinkedStyleSites.ts",
    }

    payload = data.read_text(encoding="utf-8")
    assert 'inputDomain: "progress" | "scroll-y-px";' in payload
    assert 'progressSource?: "document-progress" | "target-offset";' in payload
    sites = json.loads(
        payload.split("scrollLinkedStyleSites: ScrollLinkedStyleSite[] = ", 1)[1]
        .rsplit(";", 1)[0]
    )
    assert sites == [
        {
            "selector": "header.site-nav",
            "selectorIndex": 0,
            "inputDomain": "scroll-y-px",
            "bands": {"top": [[0.0, 100.0], [56.0, 20.0]]},
            "units": {"top": "px"},
        }
    ]

    source = driver.read_text(encoding="utf-8")
    assert 'set("top", length("top"))' in source
    assert 'style.setProperty(property, next, "important")' in source
    assert 'case "scroll-y-px":' in source
    assert "Math.max(0, window.scrollY)" in source
    scroll_y_case = source.split('case "scroll-y-px":', 1)[1].split("default:", 1)[0]
    assert "scrollHeight" not in scroll_y_case
    assert "targetOffsetProgress" not in scroll_y_case
    assert "documentProgress" not in scroll_y_case


def test_invalid_raw_pixel_scroll_state_machine_sites_do_not_emit_linked_driver(
    tmp_path: Path,
) -> None:
    """Only top/px finite ascending raw scrollY sites are replayable."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "scrollStateMachine": {
                    "required": True,
                    "sites": [
                        {
                            "selector": "header",
                            "transforms": [{
                                "property": "top",
                                "input": "[0, 100]",
                                "output": "[56, 20]",
                                "unit": "px",
                            }],
                        },
                        {
                            "selector": "header",
                            "inputDomain": "progress",
                            "transforms": [{
                                "property": "top",
                                "input": "[0, 100]",
                                "output": "[56, 20]",
                                "unit": "px",
                            }],
                        },
                        {
                            "selector": "header",
                            "inputDomain": "scroll-y-px",
                            "transforms": [{
                                "property": "y",
                                "input": "[0, 100]",
                                "output": "[56, 20]",
                                "unit": "px",
                            }],
                        },
                        {
                            "selector": "header",
                            "inputDomain": "scroll-y-px",
                            "transforms": [{
                                "property": "top",
                                "input": "[0, 100]",
                                "output": "[56, 20]",
                                "unit": "%",
                            }],
                        },
                        {
                            "selector": "header",
                            "inputDomain": "scroll-y-px",
                            "transforms": [{
                                "property": "top",
                                "input": "[100, 0]",
                                "output": "[56, 20]",
                                "unit": "px",
                            }],
                        },
                        {
                            "selector": "header",
                            "inputDomain": "scroll-y-px",
                            "transforms": [{
                                "property": "top",
                                "input": "[0, 100]",
                                "output": "[56, Infinity]",
                                "unit": "px",
                            }],
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (impl / "src" / "lib" / "ScrollLinkedStyleDriver.tsx").exists()
    assert not (impl / "src" / "lib" / "scrollLinkedStyleSites.ts").exists()


def test_emits_target_offset_progress_for_runtime_sampled_sites(
    tmp_path: Path,
) -> None:
    """Target-scoped runtime curves must preserve Framer offset semantics."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "scrollDriven": {"required": True, "library": "framer-motion"},
                "scrollScrub": {
                    "required": True,
                    "sites": [
                        {
                            "selector": "div.style_imgWrapper__AFuB_",
                            "selectorIndex": 0,
                            "scope": ".style_scrollcontainer__Vup4r",
                            "target": ".style_scrollcontainer__Vup4r",
                            "progressSource": "target-offset",
                            "offset": "[\"start start\", \"end end\"]",
                            "transforms": [
                                {
                                    "property": "width",
                                    "input": "[0, 0.5, 1]",
                                    "output": "[100, 75, 50]",
                                    "unit": "%",
                                }
                            ],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    source = (impl / "src" / "lib" / "ScrollLinkedStyleDriver.tsx").read_text(
        encoding="utf-8"
    )
    payload = (impl / "src" / "lib" / "scrollLinkedStyleSites.ts").read_text(
        encoding="utf-8"
    )
    assert "function targetOffsetProgress" in source
    assert "getBoundingClientRect" in source
    assert "site.offset" in source
    assert (
        'import type { LinkedBand, ScrollLinkedStyleSite } from "./scrollLinkedStyleSites";'
        in source
    )
    assert 'offset: ScrollLinkedStyleSite["offset"]' in source
    assert "Array.isArray(offset)" in source
    assert "offset.length >= 2" in source
    assert "const startOffset = offset[0];" in source
    assert "const endOffset = offset[1];" in source
    assert 'typeof startOffset !== "string"' in source
    assert 'typeof endOffset !== "string"' in source
    assert "const targetOffset: [string, string] = [startOffset, endOffset];" in source
    assert "targetOffsetProgress(root, targetOffset)" in source
    assert "as [string, string]" not in source
    assert '"progressSource": "target-offset"' in payload
    assert '"offset": ["start start", "end end"]' in payload
    assert '"units": {"width": "%"}' in payload
    assert "site.units" in source


def test_emits_vw_width_unit_for_runtime_sampled_sites(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "scrollDriven": {"required": False},
                "scrollScrub": {
                    "required": True,
                    "sites": [
                        {
                            "selector": ".n28-panel",
                            "selectorIndex": 0,
                            "progressSource": "document-progress",
                            "transforms": [
                                {
                                    "property": "width",
                                    "input": "[0, 0.5, 1]",
                                    "output": "[80, 90, 100]",
                                    "unit": "vw",
                                }
                            ],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    source = (impl / "src" / "lib" / "ScrollLinkedStyleDriver.tsx").read_text(
        encoding="utf-8"
    )
    payload = (impl / "src" / "lib" / "scrollLinkedStyleSites.ts").read_text(
        encoding="utf-8"
    )
    assert '"units": {"width": "vw"}' in payload
    assert 'const length = (name: string) => `${value(name)}${units[name] ?? "px"}`;' in source
    assert 'set("width", length("width"))' in source


def test_scroll_linked_style_driver_indexes_matching_scope_before_descendants(
    tmp_path: Path,
) -> None:
    """A scope root that matches the runtime selector is candidate index 0.

    Runtime extraction can report the scroll container itself plus descendants
    under the same selector. Element.querySelectorAll only returns descendants,
    so the generated replay driver must include a matching scope root before
    descendant candidates while keeping selectorIndex ordering intact.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    selector = ".style_scrollcontainer__Vup4r"
    (ref / "generation-plan.json").write_text(json.dumps({
        "scrollDriven": {"required": True, "library": "framer-motion"},
        "scrollScrub": {
            "required": True,
            "sites": [
                {
                    "selector": selector,
                    "selectorIndex": 0,
                    "scope": selector,
                    "progressSource": "document-progress",
                    "transforms": [{
                        "property": "opacity",
                        "input": "[0, 1]",
                        "output": "[0.5, 1]",
                    }],
                },
                {
                    "selector": selector,
                    "selectorIndex": 1,
                    "scope": selector,
                    "progressSource": "document-progress",
                    "transforms": [{
                        "property": "scale",
                        "input": "[0, 1]",
                        "output": "[0.8, 1]",
                    }],
                },
            ],
        },
    }), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    source = (impl / "src" / "lib" / "ScrollLinkedStyleDriver.tsx").read_text(
        encoding="utf-8"
    )
    payload = (impl / "src" / "lib" / "scrollLinkedStyleSites.ts").read_text(
        encoding="utf-8"
    )
    assert "root instanceof Element && root.matches(selector)" in source
    assert "const candidates =" in source
    assert "? [root, ...descendants]" in source
    assert "selectScopedCandidates(root, site.selector, site.selectorIndex, site.replay)" in source
    assert f'"selector": "{selector}", "selectorIndex": 0' in payload
    assert f'"selector": "{selector}", "selectorIndex": 1' in payload


def test_scroll_linked_driver_replays_all_matching_elements_and_custom_event(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "scrollDriven": {"required": True, "library": "framer-motion"},
                "scrollScrub": {
                    "required": True,
                    "sites": [
                        {
                            "selector": "span.disintegrating_char",
                            "replay": "all-matches",
                            "sourceIds": ["char-0", "char-1", "char-2"],
                            "progressSource": "document-progress",
                            "transforms": [
                                {
                                    "property": "opacity",
                                    "input": "[0, 1]",
                                    "output": "[1, 0]",
                                }
                            ],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    source = (impl / "src" / "lib" / "ScrollLinkedStyleDriver.tsx").read_text(
        encoding="utf-8"
    )
    payload = (impl / "src" / "lib" / "scrollLinkedStyleSites.ts").read_text(
        encoding="utf-8"
    )
    sites = json.loads(
        payload.split("scrollLinkedStyleSites: ScrollLinkedStyleSite[] = ", 1)[1]
        .rsplit(";", 1)[0]
    )

    assert sites[0]["replay"] == "all-matches"
    assert sites[0]["sourceIds"] == ["char-0", "char-1", "char-2"]
    assert "selectScopedCandidates(root, site.selector, site.selectorIndex, site.replay)" in source
    assert "for (const target of targets)" in source
    assert 'window.addEventListener("ui-clone-scroll", schedule as EventListener)' in source
    assert 'window.removeEventListener("ui-clone-scroll", schedule as EventListener)' in source


def test_scroll_linked_driver_respects_media_with_property_ownership(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "scrollDriven": {"required": True, "library": "framer-motion"},
                "scrollScrub": {
                    "required": True,
                    "sites": [
                        {
                            "selector": ".hero-video",
                            "selectorIndex": 0,
                            "media": "(min-width: 581px)",
                            "progressSource": "document-progress",
                            "transforms": [
                                {
                                    "property": "width",
                                    "unit": "vw",
                                    "input": "[0, 1]",
                                    "output": "[80, 100]",
                                }
                            ],
                        },
                        {
                            "selector": ".hero-video",
                            "selectorIndex": 0,
                            "media": "(max-width: 580px)",
                            "progressSource": "document-progress",
                            "transforms": [
                                {
                                    "property": "width",
                                    "unit": "vw",
                                    "input": "[0, 1]",
                                    "output": "[100, 90]",
                                }
                            ],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    source = (impl / "src" / "lib" / "ScrollLinkedStyleDriver.tsx").read_text(
        encoding="utf-8"
    )
    payload = (impl / "src" / "lib" / "scrollLinkedStyleSites.ts").read_text(
        encoding="utf-8"
    )
    sites = json.loads(
        payload.split("scrollLinkedStyleSites: ScrollLinkedStyleSite[] = ", 1)[1]
        .rsplit(";", 1)[0]
    )

    assert sites[0]["media"] == "(min-width: 581px)"
    assert sites[1]["media"] == "(max-width: 580px)"
    assert "media?: string;" in payload
    assert "window.matchMedia(media).matches" in source
    assert "rememberBandStyles" in source
    assert "noteActiveBandProperties" in source
    assert "restoreInactiveBandStyles" in source
    assert "style.removeProperty(property)" in source
    assert "restoreAllBandStyles(originalStyles)" in source
    assert "if (!mediaMatches(site.media)) continue;" in source
    assert source.index("restoreInactiveBandStyles(originalStyles, activeProperties)") < source.index(
        "for (const application of applications)"
    )


def test_emits_scroll_latch_driver_from_plan_latch_sites(tmp_path: Path) -> None:
    """A latched row proved not to reverse must be applied as a discrete state
    at its progress fraction. Interpolating it is what renders every state
    permanently half-applied, and keying it to capture-session pixels would
    desync the moment the document height changes."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "scrollDriven": {"required": False},
                "scrollLatch": {
                    "required": True,
                    "count": 1,
                    "sites": [
                        {
                            "selector": "nav .label",
                            "selectorIndex": 0,
                            "progress": 0.1,
                            "endState": {"opacity": "1"},
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    driver = impl / "src" / "lib" / "ScrollLatchDriver.tsx"
    assert driver.is_file(), "latch sites need a discrete-state runtime driver"
    source = driver.read_text(encoding="utf-8")
    assert "nav .label" in source
    assert "0.1" in source
    assert "opacity" in source
    # progress fraction resolved against the live scroll range, not baked px
    assert "scrollHeight" in source
    assert 'addEventListener("scroll"' in source


def test_scroll_linked_driver_writes_bands_with_important_priority(
    tmp_path: Path,
) -> None:
    """A stylesheet !important rule beats a plain inline write, so one authored
    pin silently defeats a whole measured band — the driver keeps writing and
    nothing moves. The band is the ref's own measured value for that property,
    so it has to be written at a priority a pin cannot outrank."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "scrollDriven": {"required": False},
                "scrollScrub": {
                    "required": True,
                    "sites": [
                        {
                            "selector": "span.label_container",
                            "selectorIndex": 0,
                            "progressSource": "document-progress",
                            "transforms": [
                                {
                                    "property": "width",
                                    "input": "[0, 1]",
                                    "output": "[0, 74]",
                                }
                            ],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    source = (impl / "src" / "lib" / "ScrollLinkedStyleDriver.tsx").read_text(
        encoding="utf-8"
    )
    assert '"important"' in source, "band writes must outrank a stylesheet pin"
    assert "style.width =" not in source, "plain inline write loses to !important"
    assert "style.opacity =" not in source


def test_emitted_offset_props_are_typed_from_framer_motion() -> None:
    """framer-motion types useScroll's `offset` as ScrollOffset — an array of
    Edge / Intersection / ProgressIntersection template-literal unions. Restating
    it as `[string, string]` is not assignable, so every emitted helper that
    forwards an `offset` prop into useScroll broke `tsc --noEmit` (and therefore
    `next build`) on a real Next.js impl. Derive the type from the library."""
    helper = HELPER.read_text(encoding="utf-8")
    assert "offset?: [string, string]" not in helper, (
        "emitted offset props must not restate framer-motion's ScrollOffset type"
    )
    # Two templates are plain text, two are nested inside Python string literals
    # where the quotes are backslash-escaped — count both spellings.
    typed = helper.count('UseScrollOptions["offset"]') + helper.count(
        'UseScrollOptions[\\"offset\\"]'
    )
    assert typed >= 4, (
        f"every emitted offset prop should be typed as UseScrollOptions['offset'] (found {typed})"
    )
    # Each emitted module that names the type must also import it, or the
    # generated file fails to compile on its own.
    assert helper.count('import type { UseScrollOptions } from "framer-motion";') >= 4
