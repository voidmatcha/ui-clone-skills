"""Regression tests for skills/visual-debug/scripts/swiper-runtime-check.sh.

F5 (part 2): the gate exists to block an impl that COPIED swiper-wrapper/swiper-
slide classes from the captured DOM without initializing a real Swiper runtime.
Its `INLINE_SIZE_RE` "explicit sizing logic" escape hatch listed `translate3d`
and `swiper-slide-active` — but those are exactly the BAKED-CAPTURE residue the
gate is meant to catch (the transpiler froze the running Swiper's inline
transform and its runtime active-slide class). A dead, runtime-less baked
carousel therefore always contained them, `class_only` stayed False, and the gate
passed the corpse. Genuine extracted sizing logic is Swiper config
(spaceBetween / slidesPerView / marginRight), not runtime residue.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "swiper-runtime-check.sh"


def _run(tmp_path: Path, impl_files: dict[str, str]) -> dict:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    # ref clearly requires Swiper
    (ref / "structure.html").write_text(
        '<div class="swiper-wrapper"><div class="swiper-slide">x</div></div>',
        encoding="utf-8",
    )
    for rel, content in impl_files.items():
        p = impl / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    result = json.loads((ref / "swiper-runtime.json").read_text(encoding="utf-8"))
    assert isinstance(result, dict)
    return result


def test_baked_translate3d_and_active_class_without_runtime_still_fails(tmp_path: Path) -> None:
    """Impl copied the classes AND carries baked translate3d + swiper-slide-active
    residue but has NO Swiper runtime -> the class-copy defect must be flagged, not
    excused as 'explicit sizing logic'."""
    baked = (
        '<div className="swiper">'
        '  <div className="swiper-wrapper">'
        '    <div className="swiper-slide swiper-slide-active" '
        '         style={{ transform: "translate3d(-1440px, 0px, 0px)" }}>Slide</div>'
        '  </div>'
        '</div>'
    )
    art = _run(tmp_path, {"src/Hero.tsx": baked, "package.json": "{}"})
    assert art["status"] == "fail", art
    assert any(i["kind"] == "copied-swiper-classes" for i in art["issues"]), art


def test_real_swiper_runtime_passes(tmp_path: Path) -> None:
    """An impl that imports and initializes Swiper is a real runtime -> pass."""
    real = (
        'import Swiper from "swiper";\n'
        'const s = new Swiper(el, { loop: false });\n'
        '<div className="swiper-wrapper"><div className="swiper-slide">x</div></div>'
    )
    art = _run(tmp_path, {"src/Hero.tsx": real, "package.json": '{"dependencies":{"swiper":"11"}}'})
    assert art["status"] == "pass", art


def test_genuine_extracted_sizing_config_still_passes(tmp_path: Path) -> None:
    """Real Swiper CONFIG (spaceBetween/slidesPerView) is legitimate extracted
    sizing logic and must remain an accepted non-class-only signal."""
    sized = (
        'const opts = { spaceBetween: 24, slidesPerView: 3 };\n'
        '<div className="swiper-wrapper"><div className="swiper-slide">x</div></div>'
    )
    art = _run(tmp_path, {"src/Hero.tsx": sized, "package.json": "{}"})
    assert art["classOnly"] is False, art
