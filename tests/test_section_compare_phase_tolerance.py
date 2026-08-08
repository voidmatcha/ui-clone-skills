"""Commit 2 control test for the motion shift-search primitive.

The OLD `_ae_at` used a CIRCULAR ImageMagick `-roll`, which wraps content that
scrolls off one edge back onto the opposite edge. That lets absolutely-misplaced
content false-collapse to AE=0 at an extreme offset (a wrap, not a real
translate) and so earn fake motion-phase alignment credit. The NEW primitive is
a NON-circular chop/splice translate: content that scrolls off is DISCARDED and
the vacated strip is filled with the ref background — it can never re-introduce
discarded content at the opposite edge.

This test proves the wrap false-collapse the fix removes, and that the new
translate leaves the vacated edge as background, not wrapped content.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ui_clone.section_dynamic import motion_phase_verdict


def _ae(a: str | Path, b: str | Path) -> float:
    out = subprocess.run(
        ["magick", "compare", "-metric", "AE", "-fuzz", "8%", str(a), str(b), "null:"],
        capture_output=True,
        text=True,
    )
    return float((out.stderr or out.stdout).split()[0])


def test_non_circular_translate_does_not_wrap_into_alignment(tmp_path: Path) -> None:
    """The OLD -roll false-collapses bottom-stripe content onto a top-stripe ref
    at dy=-90 (wrap). The NEW non-circular chop/splice translate must NOT
    re-introduce that discarded content at the opposite edge."""
    if not shutil.which("magick"):
        pytest.skip("ImageMagick not installed")
    ref = tmp_path / "ref.png"
    impl = tmp_path / "impl.png"
    roll = tmp_path / "roll.png"
    tr = tmp_path / "tr.png"
    # ref: white stripe at TOP; impl: white stripe at BOTTOM (absolutely misplaced)
    subprocess.run(
        ["magick", "-size", "100x100", "xc:black", "-fill", "white",
         "-draw", "rectangle 0,0 99,9", str(ref)],
        check=True,
    )
    subprocess.run(
        ["magick", "-size", "100x100", "xc:black", "-fill", "white",
         "-draw", "rectangle 0,90 99,99", str(impl)],
        check=True,
    )
    # OLD -roll wraps -> false AE 0 at dy=-90 (documents the bug being removed).
    subprocess.run(
        ["magick", str(impl), "-background", "none", "-virtual-pixel", "none",
         "-roll", "+0-90", str(roll)],
        check=True,
    )
    assert _ae(ref, roll) == 0.0  # the wrap false-collapse the fix eliminates
    # NEW non-circular: shift content UP 90 = chop top 90, splice opaque bottom 90.
    subprocess.run(
        ["magick", str(impl), "-background", "black", "-alpha", "remove",
         "-gravity", "North", "-chop", "0x90",
         "-gravity", "South", "-splice", "0x90", str(tr)],
        check=True,
    )
    # The bottom 10 rows of the translated image must be background (mean ~0),
    # i.e. NOT the wrapped white stripe a circular roll would have placed there.
    botmean = subprocess.run(
        ["magick", str(tr), "-gravity", "South", "-crop", "100x10+0+0", "+repage",
         "-format", "%[fx:mean]", "info:"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert float(botmean) < 0.05  # no wrapped content at the vacated edge


# ── Motion-G4 fix: localized-defect judged at the SHIFTED alignment ──
#
# These mirror section-compare.sh's motion-phase tier helpers (kept in lockstep,
# referencing the script lines), then feed the result into the real
# motion_phase_verdict. The fix recomputes the localized-defect veto on the impl
# shifted by MP_BEST_DY instead of the unshifted pair: a faithful scroll-shifted
# section differs in every band when compared unshifted, which falsely tripped the
# veto and made the tier inert (never granting the uniform-shift pass it exists for).


def _mk_shift_img(base: Path, dy: int, out: Path, bg: str = "white") -> None:
    """Mirror section-compare.sh:1886 _mk_shift — NON-circular vertical translate
    by dy px (dy<0 up, dy>0 down), the vacated edge filled with bg (no wrap)."""
    k = abs(dy)
    if dy < 0:
        edge = ["-gravity", "North", "-chop", f"0x{k}", "-gravity", "South", "-splice", f"0x{k}"]
    else:
        edge = ["-gravity", "South", "-chop", f"0x{k}", "-gravity", "North", "-splice", f"0x{k}"]
    subprocess.run(
        ["magick", str(base), "-background", bg, "-alpha", "remove", *edge, str(out)],
        check=True,
    )


def _dssim_score(a: Path, b: Path) -> float:
    out = subprocess.run(["dssim", str(a), str(b)], capture_output=True, text=True)
    return float(out.stdout.split()[0])


def _localized_defect(
    ref: Path, impl: Path, tmp: Path, *, h: int = 400, w: int = 200, bandpx: int = 200, thr: float = 0.30
) -> bool:
    """Mirror section-compare.sh:1676 _perceptual_localized_defect — crop into
    horizontal bands; True if any band's dssim reaches thr (SECTION_DSSIM_LOCAL_FAIL)."""
    y = 0
    while y < h:
        bh = min(bandpx, h - y)
        if bh < 8:
            break
        r, i = tmp / "_lr.png", tmp / "_li.png"
        subprocess.run(["magick", str(ref), "-crop", f"{w}x{bh}+0+{y}", "+repage", str(r)], check=True)
        subprocess.run(["magick", str(impl), "-crop", f"{w}x{bh}+0+{y}", "+repage", str(i)], check=True)
        if _dssim_score(r, i) >= thr:
            return True
        y += bandpx
    return False


def _build_motion_fixtures(tmp: Path) -> dict[str, Path]:
    """REF: high-variance content rows [0,300), white rows [300,400) so a ±50px
    non-circular shift realigns EXACTLY (no edge-fill mismatch). impl = REF shifted
    DOWN 50px (a faithful scroll sub-frame; best_dy=-50 realigns it). The *_defect
    variants add a catastrophic local band that SURVIVES realignment."""
    content, white, ref = tmp / "content.png", tmp / "white.png", tmp / "ref.png"
    # -seed makes plasma deterministic (verified: same seed → dssim 0.0), so the
    # band-dssim margins below are stable run-to-run, not flaky.
    subprocess.run(["magick", "-size", "200x300", "-seed", "7", "plasma:fractal", str(content)], check=True)
    subprocess.run(["magick", "-size", "200x100", "xc:white", str(white)], check=True)
    subprocess.run(["magick", str(content), str(white), "-append", str(ref)], check=True)
    impl, realigned = tmp / "impl.png", tmp / "realigned.png"
    _mk_shift_img(ref, 50, impl)
    _mk_shift_img(impl, -50, realigned)
    impl_defect, realigned_defect = tmp / "impl_defect.png", tmp / "realigned_defect.png"
    # A large, high-contrast defect band: realigned-defect max-band dssim ≈ 0.66
    # (>> the 0.30 threshold), while the clean realigned image stays at 0.0.
    subprocess.run(
        ["magick", str(impl), "-fill", "black", "-draw", "rectangle 0,250 199,390", str(impl_defect)],
        check=True,
    )
    _mk_shift_img(impl_defect, -50, realigned_defect)
    return {"ref": ref, "impl": impl, "realigned": realigned, "realigned_defect": realigned_defect}


def test_motion_phase_uniform_shift_passes(tmp_path: Path) -> None:
    """A uniform global scroll-shift PASSES: localized-defect judged on the
    realigned (shifted) impl finds no band defect, so motion_phase_verdict grants
    motion-phase parity. Also documents the inert bug the fix removes — the same
    check on the UNSHIFTED pair falsely reports a defect."""
    if not (shutil.which("magick") and shutil.which("dssim")):
        pytest.skip("ImageMagick + dssim required")
    f = _build_motion_fixtures(tmp_path)
    # The fix: veto judged at the winning alignment → uniform shift has no defect.
    localized_shifted = _localized_defect(f["ref"], f["realigned"], tmp_path)
    assert localized_shifted is False
    # The bug the fix removes: the unshifted pair is offset in every band → false defect.
    assert _localized_defect(f["ref"], f["impl"], tmp_path) is True
    status, reason = motion_phase_verdict(
        ae_zero=100000.0,
        ae_shift_min=500.0,
        shifted_dssim=_dssim_score(f["ref"], f["realigned"]),
        ref_has_variance=True,
        localized_defect=localized_shifted,
    )
    assert status == "pass", reason


def test_motion_phase_shift_plus_local_defect_fails(tmp_path: Path) -> None:
    """A global shift WITH a real local defect FAILS: the defect band survives
    realignment, so the localized veto fires and motion_phase_verdict rejects it —
    the motion tier did not become a blanket pass for anything that slides."""
    if not (shutil.which("magick") and shutil.which("dssim")):
        pytest.skip("ImageMagick + dssim required")
    f = _build_motion_fixtures(tmp_path)
    localized_shifted = _localized_defect(f["ref"], f["realigned_defect"], tmp_path)
    assert localized_shifted is True  # the local defect is not aligned away
    status, reason = motion_phase_verdict(
        ae_zero=100000.0,
        ae_shift_min=500.0,
        shifted_dssim=_dssim_score(f["ref"], f["realigned_defect"]),
        ref_has_variance=True,
        localized_defect=localized_shifted,
    )
    assert status == "fail"
    assert "localized" in reason
