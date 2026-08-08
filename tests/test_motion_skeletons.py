"""Fixture tests for the motion-hook skeleton emitter.

emit-motion-skeletons.sh turns transition-spec scroll-scrub / state-machine /
swiper entries into impl/src/generated/motion-skeletons.ts with the parameters
(property list, input range, states, Swiper config incl. breakpoints) transcribed
verbatim and `// spec:<id>` tags — the anti-approximation guarantee.
"""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMITTER = ROOT / "scripts" / "extract" / "emit-motion-skeletons.sh"


def _run(ref: Path, impl: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(EMITTER), str(ref), str(impl)],
        capture_output=True, text=True, timeout=60,
    )


def _spec(ref: Path, transitions: list[dict]) -> None:
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": transitions}), encoding="utf-8"
    )


SCRUB = {
    "id": "video-expand", "trigger": "scroll", "target": ".VideoWrapper",
    "bundle_branch": "useScroll offset=['start start','end end']; useTransform([0,.5,1])",
    "animation": {"type": "scroll-scrub", "property": "width,height,borderRadius",
                  "from": "100% / 16px", "to": "fixed px", "input": "[0,0.5,1]",
                  "driver": "framer-motion useScroll + useTransform"},
}
STATE = {
    "id": "progress-machine", "trigger": "scroll", "target": ".playerWrapper",
    "animation": {"type": "scroll state machine",
                  "states": ["initial", "expanded", "settled"],
                  "property": "transform via scrollYProgress",
                  "driver": "framer-motion useScroll"},
}
SWIPER_RAIL = {
    "id": "media-rail", "trigger": "drag", "target": ".media-banner .swiper",
    "animation": {"type": "swiper", "slidesPerView": 3, "spaceBetween": 24,
                  "navigation": True,
                  "breakpoints": {"1024": {"spaceBetween": 24}, "1600": {"spaceBetween": 32}},
                  "mobile": {"slidesPerView": "auto", "freeMode": True, "scrollbar": True}},
}
SWIPER_HERO = {
    "id": "hero-fade", "trigger": "auto", "target": ".hero .swiper",
    "animation": {"type": "swiper", "effect": "fade", "loop": True, "speed": 400,
                  "autoplay": {"delay": 4000}, "slidesPerView": 1},
}


def test_emitter_transcribes_scrub_state_swiper(tmp_path: Path) -> None:
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    ref.mkdir()
    impl.mkdir()
    _spec(ref, [SCRUB, STATE, SWIPER_RAIL])

    proc = _run(ref, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    module = (impl / "src" / "generated" / "motion-skeletons.ts").read_text()

    # spec-id tags on each site
    assert "// spec:video-expand" in module
    assert "// spec:progress-machine" in module
    assert "// spec:media-rail" in module

    # scroll-scrub: EXACT property list + input range, offset from bundle_branch,
    # and crucially NOT approximated as scale
    assert "const input = [0, 0.5, 1] as const;" in module
    assert "const width = useTransform(scrollYProgress, input," in module
    assert "const height = useTransform(scrollYProgress, input," in module
    assert "const borderRadius = useTransform(scrollYProgress, input," in module
    assert "offset: ['start start','end end']" in module
    assert "scale" not in module, "scrub must use spec properties, never re-authored scale"

    # state machine: enum + threshold ladder (TODO thresholds) + event subscription
    assert "export type ProgressMachineState = 'initial' | 'expanded' | 'settled';" in module
    assert "useMotionValueEvent(scrollYProgress, 'change'," in module
    assert "TODO threshold" in module
    assert "setState('settled')" in module

    # swiper: EXACT breakpoints object + mobile matchMedia REBUILD listener (not one-shot)
    assert "export function initMediaRail(): () => void {" in module
    assert '"slidesPerView": 3' in module
    assert '"1024": {' in module and '"1600": {' in module and '"spaceBetween": 32' in module
    assert "mq.addEventListener('change', build)" in module
    assert "swiper.destroy(true, true)" in module

    report = json.loads((ref / "motion-skeletons-emitted.json").read_text())
    kinds = {e["id"]: e["kind"] for e in report["emitted"]}
    assert kinds == {"video-expand": "scroll-scrub", "progress-machine": "state-machine",
                     "media-rail": "swiper"}
    vid = next(e for e in report["emitted"] if e["id"] == "video-expand")
    assert vid["properties"] == ["width", "height", "borderRadius"]
    assert vid["input"] == [0, 0.5, 1] and vid["inputExact"] is True


def test_swiper_without_mobile_is_single_config(tmp_path: Path) -> None:
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    ref.mkdir()
    impl.mkdir()
    _spec(ref, [SWIPER_HERO])

    assert _run(ref, impl).returncode == 0
    module = (impl / "src" / "generated" / "motion-skeletons.ts").read_text()
    assert "export function initHeroFade(): () => void {" in module
    assert '"effect": "fade"' in module
    # no mobile config => Swiper's own resize watch handles it, no matchMedia rebuild
    assert "const swiper = new Swiper(el, desktopConfig);" in module
    assert "addEventListener('change'" not in module
    # framer-motion must NOT be imported when only swiper entries exist
    assert "framer-motion" not in module


def test_emitter_no_motion_is_noop(tmp_path: Path) -> None:
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    ref.mkdir()
    impl.mkdir()
    _spec(ref, [{"id": "hov", "trigger": "hover", "type": "css-hover", "target": ".btn"}])

    proc = _run(ref, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (impl / "src" / "generated" / "motion-skeletons.ts").exists()
    report = json.loads((ref / "motion-skeletons-emitted.json").read_text())
    assert report["emitted"] == []


def test_unclassified_slider_entry_recorded_in_skipped(tmp_path: Path) -> None:
    """F11: a motion entry whose type does not map to scrub/state/swiper (e.g.
    'slider'/'slideshow') is dropped from codegen — but must be RECORDED in the
    emitted report's skipped[], not vanish silently (only missing-id entries were
    logged before, so a whole slider spec could disappear with no accounting)."""
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    ref.mkdir()
    impl.mkdir()
    _spec(ref, [
        {"id": "my-slider", "trigger": "auto", "target": ".s",
         "animation": {"type": "slider"}},
        SCRUB,  # a real scrub so the emitter does not early-exit as no-op
    ])
    _run(ref, impl)
    report = json.loads((ref / "motion-skeletons-emitted.json").read_text())
    dropped = [s for s in report["skipped"] if s.get("id") == "my-slider"]
    assert dropped, f"slider entry must be recorded in skipped; report={report}"
    assert "slider" in dropped[0]["reason"], dropped
