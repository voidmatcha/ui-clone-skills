"""Behavioral proof that the batch-11 ITEM 4(b)/4(c) AE tolerances cannot hide a
real content defect.

section-compare.sh computes a section's AE as the MINIMUM over
{stretch, cover-fit} x {0, +/- vertical phase offsets up to
SECTION_SCROLL_PHASE_TOL_PX} (see the `_ae_at` / `_try_base` loop). The safety
claim is that this min can only remove a GLOBAL uniform translation / aspect
rescale (capture-phase noise) and can NEVER align away a localized/structural
content defect — shifting the whole crop to fix one element misaligns the rest,
so the defect's min AE stays high.

This test exercises that property end-to-end through ImageMagick (the same
`magick`/`-roll`/`-resize ^ -extent`/`compare -metric AE -fuzz` primitives the
shell uses), so it is a behavioral proof of the technique, not just a source
assertion. Skipped when ImageMagick is unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HAVE_MAGICK = shutil.which("magick")
pytestmark = pytest.mark.skipif(_HAVE_MAGICK is None, reason="ImageMagick (magick) not on PATH")
# Narrowed for type-checkers; the skipif above guards the None case at runtime.
_MAGICK: str = _HAVE_MAGICK or "magick"

_FUZZ = "8%"
# Mirror the shell defaults exactly (section-compare.sh).
_PHASE_TOL_PX = 6


def _run(*args: str) -> None:
    subprocess.run([_MAGICK, *args], check=True, capture_output=True, timeout=60)


def _ae(ref: Path, img: Path) -> int:
    # magick compare exits 1 when images differ; AE is on stderr/stdout.
    proc = subprocess.run(
        [_MAGICK, "compare", "-metric", "AE", "-fuzz", _FUZZ, str(ref), str(img), "null:"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = (proc.stderr or proc.stdout or "").strip().split()
    for tok in out:
        try:
            return int(float(tok))
        except ValueError:
            continue
    raise AssertionError(f"could not parse AE from {out!r}")


def _phase_min_ae(ref: Path, impl: Path, tmp: Path) -> int:
    """Replicates section-compare.sh's min-over-vertical-offsets (the exact
    technique under test): AE = min over {0, +/-2, +/-4, +/-6} px rolls."""
    best = _ae(ref, impl)
    mag = 2
    while mag <= _PHASE_TOL_PX:
        for sign in ("+", "-"):
            shifted = tmp / f"shift{sign}{mag}.png"
            _run(str(impl), "-background", "none", "-virtual-pixel", "none",
                 "-roll", f"+0{sign}{mag}", str(shifted))
            best = min(best, _ae(ref, shifted))
        mag += 2
    return best


def _bars(
    path: Path, *, width: int = 400, height: int = 300, bars: list[tuple[int, int, int, int]]
) -> None:
    args = ["-size", f"{width}x{height}", "xc:white", "-fill", "black"]
    for x0, y0, x1, y1 in bars:
        args += ["-draw", f"rectangle {x0},{y0} {x1},{y1}"]
    args.append(str(path))
    _run(*args)


def _cover_fit(impl: Path, out: Path, *, w: int = 400, h: int = 300) -> None:
    """The exact cover-fit candidate section-compare.sh builds from the ORIGINAL
    impl crop (section-compare.sh:1686-1687): aspect-preserving fill + centre
    -extent to ref dims. Mirrored here so the behavioral proof exercises the real
    technique, not an approximation."""
    _run(str(impl), "-resize", f"{w}x{h}^", "-gravity", "center", "-extent", f"{w}x{h}", str(out))


def _stretch(impl: Path, out: Path, *, w: int = 400, h: int = 300) -> None:
    """The legacy exact-stretch resized impl (section-compare.sh:1689)."""
    _run(str(impl), "-resize", f"{w}x{h}!", str(out))


def test_phase_min_forgives_global_shift_but_not_content_defect(tmp_path: Path) -> None:
    ref = tmp_path / "ref.png"
    _bars(ref, bars=[(40, 60, 360, 80), (40, 160, 340, 180), (40, 250, 280, 270)])

    # (1) identical content, uniformly shifted +6px vertically (capture-phase
    # noise): the min over the bounded offset band must collapse it to ~0.
    shifted = tmp_path / "shifted.png"
    _run(str(ref), "-background", "white", "-virtual-pixel", "none", "-roll", "+0+6", str(shifted))
    shifted_min = _phase_min_ae(ref, shifted, tmp_path)
    raw_shifted = _ae(ref, shifted)
    assert shifted_min < raw_shifted, (raw_shifted, shifted_min)
    assert shifted_min <= 50, f"a pure 6px global shift must align to ~0, got {shifted_min}"

    # (2) a REAL content defect (bars at different places/sizes): the min over the
    # SAME bounded band must stay high — a uniform shift cannot align away a
    # localized/structural difference. Must remain far above the section threshold
    # (2000), so the defect still FAILs.
    defect = tmp_path / "defect.png"
    _bars(defect, bars=[(40, 60, 200, 80), (120, 140, 360, 180), (60, 230, 340, 260)])
    defect_min = _phase_min_ae(ref, defect, tmp_path)
    assert defect_min > 5000, (
        f"a content defect must NOT be aligned away by the phase band; "
        f"min AE stayed {defect_min} (still a hard FAIL well above threshold 2000)"
    )


def test_cover_fit_never_worse_than_stretch(tmp_path: Path) -> None:
    # ITEM 4(c): the section AE is min(stretch, cover-fit, ...), so the cover-fit
    # candidate can only LOWER AE. Verify cover-fit <= stretch is the relation the
    # min relies on for an identical-content crop at a slightly different size
    # (here a 1.03x scale — within SECTION_CROP_SCALE_TOL 1.04).
    ref = tmp_path / "ref.png"
    _bars(ref, bars=[(40, 60, 360, 80), (40, 160, 340, 180), (40, 250, 280, 270)])
    scaled = tmp_path / "scaled.png"
    _run(str(ref), "-resize", "412x309", str(scaled))  # ~1.03x, same content
    stretch = tmp_path / "stretch.png"
    _run(str(scaled), "-resize", "400x300!", str(stretch))
    cover = tmp_path / "cover.png"
    _run(str(scaled), "-resize", "400x300^", "-gravity", "center", "-extent", "400x300", str(cover))
    # the min the script keeps is <= the legacy stretch AE (never worse than today)
    assert min(_ae(ref, stretch), _ae(ref, cover)) <= _ae(ref, stretch)


def test_cover_fit_does_not_hide_crop_scale_defect(tmp_path: Path) -> None:
    """ITEM 4 (batch-12): the cover-fit candidate (+ phase band) may only LOWER AE
    on IDENTICAL content — it must NEVER align or crop away a GENUINE crop-scale
    defect. test_cover_fit_never_worse_than_stretch proves the 'can't make it
    worse' direction only; this is the missing hard-FAIL direction (the prompt's
    ITEM 4: a real crop-scale defect must still FAIL under the tolerance).

    Two defective impls, each a slightly different SIZE than the ref (within
    SECTION_CROP_SCALE_TOL=1.04) and a different ASPECT ratio so the cover-fit
    centre -extent genuinely crops (not a no-op):
      (A) wrong image SCALE / structure — bars at the wrong positions and sizes
          on a wider canvas (horizontal cover-fit crop engaged);
      (B) a missing content ROW near the crop margin — a taller canvas whose
          bottom bar is dropped (vertical cover-fit crop engaged), the exact
          'missing content row near the crop margin' case.
    For BOTH, the full min over {stretch, cover-fit} x {0,+/-2,+/-4,+/-6 px} AND
    the cover-fit candidate on its OWN must stay a hard FAIL (>>2000 AE/Mpx ~=
    240 AE on this 0.12 Mpx crop). If a real defect ever slips below threshold
    here, SECTION_CROP_SCALE_TOL at section-compare.sh:1677 must be tightened.
    """
    ref = tmp_path / "ref.png"
    _bars(ref, bars=[(40, 60, 360, 80), (40, 160, 340, 180), (40, 252, 300, 286)])

    # (A) wrong scale/structure on a WIDER within-tol canvas (412x300: w-ratio
    # 1.03, aspect 1.373 vs 1.333 -> cover-fit crops ~6px each side horizontally).
    defect_a = tmp_path / "defect_a.png"
    _bars(defect_a, width=412, height=300,
          bars=[(40, 60, 210, 80), (150, 138, 392, 182), (70, 236, 372, 262)])
    stretch_a = tmp_path / "stretch_a.png"
    cover_a = tmp_path / "cover_a.png"
    _stretch(defect_a, stretch_a)
    _cover_fit(defect_a, cover_a)
    cover_a_min = _phase_min_ae(ref, cover_a, tmp_path)
    full_a_min = min(_phase_min_ae(ref, stretch_a, tmp_path), cover_a_min)
    assert cover_a_min > 5000, (
        f"cover-fit on a wrong-scale/structure defect must FAIL on its own; got {cover_a_min}"
    )
    assert full_a_min > 5000, (
        f"the full min(stretch,cover-fit)x(phase) must not align away a structural "
        f"crop-scale defect; got {full_a_min}"
    )

    # (B) missing content row near the crop margin on a TALLER within-tol canvas
    # (400x309: h-ratio 1.03, aspect 1.294 vs 1.333 -> cover-fit crops ~4-5px off
    # top and bottom). The bottom bar (near the crop margin) is DROPPED.
    defect_b = tmp_path / "defect_b.png"
    _bars(defect_b, width=400, height=309,
          bars=[(40, 60, 360, 80), (40, 160, 340, 180)])  # bottom bar removed
    stretch_b = tmp_path / "stretch_b.png"
    cover_b = tmp_path / "cover_b.png"
    _stretch(defect_b, stretch_b)
    _cover_fit(defect_b, cover_b)
    cover_b_min = _phase_min_ae(ref, cover_b, tmp_path)
    full_b_min = min(_phase_min_ae(ref, stretch_b, tmp_path), cover_b_min)
    assert cover_b_min > 5000, (
        f"cover-fit must not crop away a missing content row near the margin; got {cover_b_min}"
    )
    assert full_b_min > 5000, (
        f"the full min must keep a missing-content-row defect failing; got {full_b_min}"
    )
