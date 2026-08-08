from __future__ import annotations

import json
from pathlib import Path

from ._helpers import (
    _run_script,
)


def test_motion_coverage_fail_on_ref_motion_impl_static(tmp_path: Path) -> None:
    """Ref bundle has gsap; impl has no motion code → fail."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({
        "libraries": [{"name": "gsap"}, {"name": "framer-motion"}],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "hero", "trigger": "scroll"},
            {"id": "fade", "trigger": "intersection"},
        ],
    }))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps(
        {"dependencies": {"react": "19"}},
    ))
    (impl / "src" / "App.jsx").write_text(
        "export default function App(){return <div>static</div>}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/motion-coverage-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "motion-coverage.json").read_text())
    assert art["status"] == "fail"
    assert art["refSignalStrength"] >= 3
    assert art["implMotionStrength"] == 0



def test_motion_coverage_pass_when_impl_uses_gsap(tmp_path: Path) -> None:
    """Ref motion + impl uses gsap.timeline → pass."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({
        "libraries": [{"name": "gsap"}],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero"}],
    }))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({
        "dependencies": {"react": "19", "gsap": "3"},
    }))
    (impl / "src" / "App.jsx").write_text(
        "import { gsap } from 'gsap'\n"
        "import { useScroll } from 'framer-motion'\n"
        "export default function App(){\n"
        "  gsap.to('.hero', {opacity: 1});\n"
        "  const {scrollY} = useScroll();\n"
        "  return <div/>;\n"
        "}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/motion-coverage-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "motion-coverage.json").read_text())
    assert art["status"] == "pass"



def test_motion_coverage_emotion_does_not_trigger_motion_match(tmp_path: Path) -> None:
    """@emotion/react in bundle-map must NOT count as motion."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({
        "libraries": [
            {"name": "@emotion/react"},
            {"name": "@emotion/styled"},
        ],
    }))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps(
        {"dependencies": {"react": "19"}},
    ))
    (impl / "src" / "App.jsx").write_text(
        "export default function App(){return <div/>}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/motion-coverage-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "motion-coverage.json").read_text())
    assert art["status"] == "pass"
    assert art["refSignalStrength"] == 0, (
        "emotion must not match motion library set"
    )



def test_motion_coverage_css_keyframes_count_as_motion(tmp_path: Path) -> None:
    """CSS @keyframes + animation: + scroll-timeline: count as impl motion."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero"}, {"id": "fade"}],
    }))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps(
        {"dependencies": {"react": "19"}},
    ))
    (impl / "src" / "App.jsx").write_text(
        "export default function App(){return <div className='hero'/>}\n",
    )
    (impl / "src" / "styles.css").write_text(
        "@keyframes fadeIn { from { opacity: 0 } to { opacity: 1 } }\n"
        ".hero { animation: fadeIn 1s ease-out; }\n"
        ".scroll { scroll-timeline: --page block; }\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/motion-coverage-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "motion-coverage.json").read_text())
    assert art["status"] == "pass"
    assert art["implMotionStrength"] >= 3, (
        "CSS motion declarations must contribute to impl strength"
    )


def test_library_driven_ref_raf_shim_and_mirror_keyframes_uncovered(tmp_path: Path) -> None:
    """Library-driven ref (gsap + lenis) whose impl fakes motion with a bare
    requestAnimationFrame loop and carries @keyframes only in mirrored ref CSS
    (src/styles/from-ref/) → both signals are discounted, impl strength 0, FAIL.
    This is the rAF-shim loophole the fix closes."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps(
        {"libraries": {"gsap": True}, "notes": "lenis smooth scroll on <html>"}
    ))
    (ref / "transition-spec.json").write_text(json.dumps(
        {"transitions": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
    ))
    impl = tmp_path / "impl"
    (impl / "src" / "styles" / "from-ref").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({"dependencies": {"react": "19"}}))
    (impl / "src" / "App.jsx").write_text(
        "export default function App(){\n"
        "  const raf=()=>requestAnimationFrame(raf); raf();\n"
        "  return <div/>;\n}\n"
    )
    (impl / "src" / "styles" / "from-ref" / "site.css").write_text(
        "@keyframes spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}\n"
        ".x{animation:spin 2s linear infinite}\n"
    )
    proc = _run_script(
        "skills/visual-debug/scripts/motion-coverage-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "motion-coverage.json").read_text())
    assert art["status"] == "fail"
    assert art["refIsLibraryDriven"] is True
    assert set(art["refMotionLibraries"]) >= {"gsap", "lenis"}
    assert art["implMotionStrength"] == 0
    rejected = " ".join(r["signal"] for r in art["rejectedImplSignals"])
    assert "requestAnimationFrame" in rejected
    assert "keyframes" in rejected


def test_vanilla_ref_raf_still_counts(tmp_path: Path) -> None:
    """A ref with NO library evidence (transition-spec only) is a legitimate
    vanilla-JS site: a requestAnimationFrame loop in impl still counts as motion,
    so the impl is not flagged uncovered."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps(
        {"transitions": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
    ))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({"dependencies": {"react": "19"}}))
    (impl / "src" / "App.jsx").write_text(
        "export default function App(){\n"
        "  function loop(){ requestAnimationFrame(loop); } loop();\n"
        "  return <div/>;\n}\n"
    )
    proc = _run_script(
        "skills/visual-debug/scripts/motion-coverage-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "motion-coverage.json").read_text())
    assert art["refIsLibraryDriven"] is False
    assert art["implMotionStrength"] >= 1
    assert not art["rejectedImplSignals"]


def test_library_driven_ref_own_css_keyframes_still_count(tmp_path: Path) -> None:
    """Under a library-driven ref, @keyframes in the impl's OWN (non-mirror) CSS
    still count — only the mirrored ref-CSS copy is discounted."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({"libraries": {"gsap": True}}))
    (ref / "transition-spec.json").write_text(json.dumps(
        {"transitions": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
    ))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({"dependencies": {"react": "19"}}))
    (impl / "src" / "App.jsx").write_text(
        "export default function App(){return <div className='y'/>}\n"
    )
    (impl / "src" / "app.css").write_text(
        "@keyframes a{from{opacity:0}to{opacity:1}}\n"
        "@keyframes b{}@keyframes c{}\n"
        ".y{animation:a 1s}\n"
    )
    proc = _run_script(
        "skills/visual-debug/scripts/motion-coverage-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "motion-coverage.json").read_text())
    assert art["refIsLibraryDriven"] is True
    assert art["implMotionStrength"] >= 3, "own-CSS keyframes must still count"
    assert not art["rejectedImplSignals"]

