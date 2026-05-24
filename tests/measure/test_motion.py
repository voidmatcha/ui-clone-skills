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

