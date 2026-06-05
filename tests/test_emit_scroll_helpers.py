from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "emit-scroll-helpers.sh"


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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    f = impl / "src" / "lib" / "ScrollWordHighlight.tsx"
    assert f.exists()
    t = f.read_text(encoding="utf-8")
    assert "useScroll({" in t
    assert "useMotionValueEvent" in t
    assert 'split(" ")' in t
    assert 'data-scroll-word-highlight="1"' in t


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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    t = (impl / "src" / "lib" / "SmoothScroll.tsx").read_text(encoding="utf-8")
    assert "lerp: 0.08" in t
    assert "bogus" not in t
    assert "drop me" not in t
