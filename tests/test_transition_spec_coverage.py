"""Fixture tests for skills/visual-debug/scripts/transition-spec-coverage.sh.

The load-bearing behavior: coverage is counted from DOM-producing source only.
A target class that exists in the impl tree ONLY inside the verbatim CSS mirror
(src/styles/from-ref/) — or in any pure stylesheet — is dead CSS, not a wired
node, and must count as UNCOVERED (reason `target-not-in-dom`). Bundle-mined
entries without a resolvable target stay on the behavior-text path.
"""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "transition-spec-coverage.sh"


def _run(comp_dir: Path, impl_dir: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    import os
    return subprocess.run(
        ["bash", str(SCRIPT), str(comp_dir), str(impl_dir)],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, **(env or {})},
    )


def _spec(comp_dir: Path, transitions: list[dict]) -> None:
    (comp_dir / "transition-spec.json").write_text(
        json.dumps({"transitions": transitions}), encoding="utf-8"
    )


def _impl(impl_dir: Path) -> Path:
    """Minimal impl tree: one JSX file, a CSS mirror, a pure stylesheet, a hook."""
    src = impl_dir / "src"
    (src / "styles" / "from-ref").mkdir(parents=True)
    # present_class is a real rendered node
    (src / "App.tsx").write_text(
        'export default function App() {\n'
        '  return <><button className="present_class__abc">go</button>\n'
        '    <motion.g id="even" /></>;\n'
        '}\n',
        encoding="utf-8",
    )
    # dead_class exists ONLY in the mirrored ref CSS — dead CSS, no node
    (src / "styles" / "from-ref" / "mirror.css").write_text(
        ".dead_class__xyz:not(:disabled){transition:opacity .2s}\n", encoding="utf-8"
    )
    # purecss_class exists ONLY in a hand-written stylesheet — still not a node
    (src / "styles" / "app.css").write_text(
        ".purecss_class__q{transition:transform .3s}\n", encoding="utf-8"
    )
    # a scroll/intersection hook lives in a real source file
    (src / "hooks.ts").write_text(
        "export const io = new IntersectionObserver(() => {});\n"
        "import { useScroll } from 'framer-motion';\n"
        "export const s = useScroll;\n",
        encoding="utf-8",
    )
    return src


def test_mirror_and_pure_css_targets_count_as_uncovered(tmp_path: Path) -> None:
    comp = tmp_path / "ref"
    comp.mkdir()
    impl = tmp_path / "impl"
    impl.mkdir()
    _impl(impl)
    _spec(comp, [
        # DOM path: target rendered in JSX -> covered
        {"id": "hov-present", "trigger": "hover", "type": "css-hover",
         "target": ".present_class__abc:not(:disabled)"},
        # DOM path: target present ONLY in the CSS mirror -> target-not-in-dom
        {"id": "hov-mirror-only", "trigger": "hover", "type": "css-hover",
         "target": ".dead_class__xyz:not(:disabled)"},
        # DOM path: target present ONLY in a pure stylesheet -> target-not-in-dom
        {"id": "hov-purecss-only", "trigger": "hover", "type": "css-hover",
         "target": ".purecss_class__q"},
        # behavior path: no target -> intersection hook present -> covered
        {"id": "reveal", "trigger": "intersection", "type": "intersection-fade-up",
         "target": ""},
        # behavior path: runtime-injected target -> scroll hook present -> covered
        {"id": "carousel", "trigger": "scroll", "type": "scroll-scrub",
         "target": ".swiper-slide"},
    ])

    proc = _run(comp, impl)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    data = json.loads((comp / "transition-spec-coverage.json").read_text())
    assert data["status"] == "fail"
    assert data["total"] == 5
    assert data["covered"] == 3
    assert data["uncovered"] == 2

    by_id = {e["id"]: e for e in data["entries"]}
    assert by_id["hov-present"]["covered"] is True
    assert by_id["hov-present"]["reason"] == "target-in-dom"
    # the two dead targets are uncovered EVEN THOUGH their class strings exist
    # in the impl tree (mirror CSS / pure CSS) — the exclusion is the whole point
    assert by_id["hov-mirror-only"]["covered"] is False
    assert by_id["hov-mirror-only"]["reason"] == "target-not-in-dom"
    assert by_id["hov-purecss-only"]["covered"] is False
    assert by_id["hov-purecss-only"]["reason"] == "target-not-in-dom"
    # bundle-mined / runtime entries keep the behavior-text path
    assert by_id["reveal"]["path"] == "behavior"
    assert by_id["reveal"]["covered"] is True
    assert by_id["carousel"]["path"] == "behavior"
    assert by_id["carousel"]["covered"] is True


def test_all_present_targets_pass(tmp_path: Path) -> None:
    comp = tmp_path / "ref"
    comp.mkdir()
    impl = tmp_path / "impl"
    impl.mkdir()
    _impl(impl)
    _spec(comp, [
        {"id": "hov-present", "trigger": "hover", "type": "css-hover",
         "target": ".present_class__abc"},
        {"id": "reveal", "trigger": "intersection", "type": "intersection-fade-up",
         "target": ""},
    ])

    proc = _run(comp, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads((comp / "transition-spec-coverage.json").read_text())
    assert data["status"] == "pass"
    assert data["total"] == 2
    assert data["uncovered"] == 0
    assert all(e["covered"] for e in data["entries"])


def test_motion_svg_tag_id_selector_counts_as_dom_target(tmp_path: Path) -> None:
    """Framer Motion JSX primitives still produce the tag/id DOM selector."""
    comp = tmp_path / "ref"
    comp.mkdir()
    impl = tmp_path / "impl"
    impl.mkdir()
    _impl(impl)
    _spec(comp, [
        {
            "id": "discovery-ring",
            "trigger": "scroll",
            "type": "scroll-driven-scale",
            "target": "g#even",
        },
    ])

    proc = _run(comp, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads((comp / "transition-spec-coverage.json").read_text())
    assert data["status"] == "pass"
    assert data["entries"][0]["path"] == "dom"
    assert data["entries"][0]["matched"] == "even"


def test_id_selector_does_not_match_unrelated_source_text(tmp_path: Path) -> None:
    comp = tmp_path / "ref"
    comp.mkdir()
    impl = tmp_path / "impl"
    src = impl / "src"
    src.mkdir(parents=True)
    (src / "App.tsx").write_text(
        'export default function App() { return <p>even rings</p>; }\n',
        encoding="utf-8",
    )
    _spec(comp, [
        {
            "id": "missing-ring",
            "trigger": "scroll",
            "type": "scroll-driven-scale",
            "target": "g#even",
        },
    ])

    proc = _run(comp, impl)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    data = json.loads((comp / "transition-spec-coverage.json").read_text())
    assert data["status"] == "fail"
    assert data["entries"][0]["reason"] == "target-not-in-dom"


def test_runtime_captured_swiper_accepts_real_constructor_hook(tmp_path: Path) -> None:
    comp = tmp_path / "ref"
    comp.mkdir()
    impl = tmp_path / "impl"
    src = impl / "src"
    src.mkdir(parents=True)
    (src / "SwiperActivator.tsx").write_text(
        'import Swiper from "swiper";\n'
        'export const activate = (el: HTMLElement) => new Swiper(el, { loop: true });\n',
        encoding="utf-8",
    )
    _spec(comp, [
        {
            "id": "live-swiper-0",
            "trigger": "swiper-next",
            "target": '.swiper[data-ui-clone-swiper="0"]',
            "runtime_hook": "el.swiper.slideNext()",
            "runtime_type": "Swiper",
            "animation": {"type": "swiper", "action": "slideNext"},
        },
    ])

    proc = _run(comp, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads((comp / "transition-spec-coverage.json").read_text())
    assert data["status"] == "pass"
    assert data["covered"] == 1
    assert data["entries"][0]["path"] == "behavior"
    assert data["entries"][0]["reason"] == "behavior-hit"
    assert data["entries"][0]["matched"] == "new Swiper("


def test_env_override_excludes_custom_mirror_dir(tmp_path: Path) -> None:
    """UI_CLONE_GENERATED_EVIDENCE_DIRS mirrors the sibling coverage scripts:
    a class present only in the named dir must not count as DOM presence."""
    comp = tmp_path / "ref"
    comp.mkdir()
    impl = tmp_path / "impl"
    (impl / "src" / "reference-css").mkdir(parents=True)
    (impl / "src" / "reference-css" / "m.css").write_text(
        ".only_in_custom_mirror__z{transition:opacity .2s}\n", encoding="utf-8"
    )
    _spec(comp, [
        {"id": "hov", "trigger": "hover", "type": "css-hover",
         "target": ".only_in_custom_mirror__z"},
    ])

    proc = _run(comp, impl, env={"UI_CLONE_GENERATED_EVIDENCE_DIRS": "reference-css"})
    assert proc.returncode == 1, proc.stdout + proc.stderr
    data = json.loads((comp / "transition-spec-coverage.json").read_text())
    assert data["uncovered"] == 1
    assert data["entries"][0]["reason"] == "target-not-in-dom"
