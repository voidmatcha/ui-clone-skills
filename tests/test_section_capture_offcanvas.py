from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ui_clone.section_capture import crop_is_off_canvas, write_transparent_stub


def test_above_canvas_rect_is_off_canvas() -> None:
    """loop-e2e-4 intro overlay: stored rect top -900, h 900 -> rows -900..0,
    fully above the screenshot canvas."""
    assert crop_is_off_canvas(clip_top=-900.0, crop_h=900.0, canvas_h=850.0)


def test_below_canvas_rect_is_off_canvas() -> None:
    assert crop_is_off_canvas(clip_top=900.0, crop_h=300.0, canvas_h=850.0)


def test_partially_visible_rect_is_not_off_canvas() -> None:
    assert not crop_is_off_canvas(clip_top=-100.0, crop_h=300.0, canvas_h=850.0)
    assert not crop_is_off_canvas(clip_top=0.0, crop_h=850.0, canvas_h=850.0)


@pytest.mark.skipif(shutil.which("magick") is None, reason="imagemagick not installed")
def test_transparent_stub_is_1x1_rgba(tmp_path: Path) -> None:
    """Both sides must encode off-canvas crops identically: 1x1 fully
    transparent RGBA (the ref pipeline emits alpha; a no-alpha impl screenshot
    clamps to an edge pixel and guarantees a saturating 1px AE diff)."""
    out = tmp_path / "stub.png"
    write_transparent_stub(out)
    txt = subprocess.run(
        ["magick", str(out), "txt:"], capture_output=True, text=True, check=True
    ).stdout
    assert "1,1," in txt.splitlines()[0]
    assert "#FFFFFF00" in txt or ",0)" in txt.replace(" ", "")
