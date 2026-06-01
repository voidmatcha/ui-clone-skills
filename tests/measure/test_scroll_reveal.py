from __future__ import annotations

import json
from pathlib import Path

from ._helpers import (
    _run_script,
)


def test_scroll_engine_parity_fail_on_gsap_ref_vs_bare_io_impl(tmp_path: Path) -> None:
    """Ref has gsap-scrolltrigger + lenis + scroll-pin + scroll-scrub
    but impl has only IntersectionObserver → all 4 classes missing."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({
        "libraries": [{"name": "gsap"}, {"name": "@studio-freight/lenis"}],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "hero", "trigger": "scroll-scrub"},
            {"id": "pin", "trigger": "sticky-scrub"},
        ],
    }))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps(
        {"dependencies": {"react": "19"}},
    ))
    (impl / "src" / "App.jsx").write_text(
        "import {useEffect} from 'react'\n"
        "export default function App(){\n"
        "  useEffect(() => { new IntersectionObserver(() => {}, {}); }, [])\n"
        "  return <div/>;\n"
        "}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/scroll-engine-parity-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "scroll-engine-parity.json").read_text())
    assert art["status"] == "fail"
    missing = {v["refClass"] for v in art["violations"]}
    assert "gsap-scrolltrigger" in missing
    assert "lenis-smooth-scroll" in missing
    assert "scroll-pin" in missing
    assert "scroll-scrub" in missing



def test_scroll_engine_parity_pass_on_matching_impl(tmp_path: Path) -> None:
    """Ref has gsap + scroll-scrub; impl imports gsap + uses
    ScrollTrigger → pass."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({
        "libraries": [{"name": "gsap"}],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero", "trigger": "scroll-scrub"}],
    }))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({
        "dependencies": {"react": "19", "gsap": "3"},
    }))
    (impl / "src" / "App.jsx").write_text(
        "import {gsap} from 'gsap'\n"
        "import {ScrollTrigger} from 'gsap/ScrollTrigger'\n"
        "gsap.registerPlugin(ScrollTrigger)\n"
        "ScrollTrigger.create({trigger: '.hero', scrub: 1})\n"
        "export default function App(){return <div className='hero'/>}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/scroll-engine-parity-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "scroll-engine-parity.json").read_text())
    assert art["status"] == "pass"


def test_scroll_engine_parity_reads_scroll_engine_json_for_scrolltrigger_pin_scrub(tmp_path: Path) -> None:
    """scroll-engine.json is enough ref evidence to reject native handlers
    for ScrollTrigger pin/scrub pages.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "scroll-engine.json").write_text(json.dumps({
        "library": "ScrollTrigger",
        "smoothScroll": {"library": "Lenis", "matches": 1},
        "features": {"scrub": True, "pin": True},
    }))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps(
        {"dependencies": {"react": "19"}},
    ))
    (impl / "src" / "App.jsx").write_text(
        "window.addEventListener('scroll', () => { "
        "document.querySelector('.rail').style.transform = `translateX(${window.scrollY}px)`;"
        "});\n",
    )

    proc = _run_script(
        "skills/visual-debug/scripts/scroll-engine-parity-check.sh",
        str(ref), str(impl),
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "scroll-engine-parity.json").read_text())
    missing = {v["refClass"] for v in art["violations"]}
    assert {"gsap-scrolltrigger", "lenis-smooth-scroll", "scroll-pin", "scroll-scrub"} <= missing
