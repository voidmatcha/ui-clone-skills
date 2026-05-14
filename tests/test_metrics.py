"""Tests for ui_clone.metrics — multiscale SSIM and viewport-relative severity."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from ui_clone.metrics import defect_severity, multiscale_ssim, severity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_image(arr: np.ndarray, tmp_path: Path) -> Path:
    """Save a float [0,1] 2-D array as a grayscale PNG; return the Path."""
    img = Image.fromarray((arr * 255).astype(np.uint8), mode="L")
    p = tmp_path / f"img_{id(arr)}.png"
    img.save(p)
    return p


def _gradient(h: int = 100, w: int = 100) -> np.ndarray:
    """Return a smooth horizontal gradient as float [0,1]."""
    row = np.linspace(0, 1, w)
    return np.tile(row, (h, 1))


# ---------------------------------------------------------------------------
# multiscale_ssim tests
# ---------------------------------------------------------------------------


def test_multiscale_ssim_identical(tmp_path: Path) -> None:
    """Identical images → score > 0.99."""
    arr = _gradient()
    ref = _make_image(arr, tmp_path)
    impl = _make_image(arr.copy(), tmp_path)
    score = multiscale_ssim(ref, impl)
    assert score > 0.99


def test_multiscale_ssim_different(tmp_path: Path) -> None:
    """Clearly different images → score < 0.90."""
    arr_ref = _gradient()
    arr_impl = 1.0 - _gradient()  # inverted
    ref = _make_image(arr_ref, tmp_path)
    impl = _make_image(arr_impl, tmp_path)
    score = multiscale_ssim(ref, impl)
    assert score < 0.90


def test_multiscale_ssim_small_shift(tmp_path: Path) -> None:
    """1-px shift of same image → score > 0.95 (multiscale reduces false positives)."""
    arr = _gradient()
    shifted = np.roll(arr, 1, axis=1)
    ref = _make_image(arr, tmp_path)
    impl = _make_image(shifted, tmp_path)
    score = multiscale_ssim(ref, impl)
    assert score > 0.95


def test_multiscale_ssim_different_sizes(tmp_path: Path) -> None:
    """ref=800×600, impl=400×300 → should not crash and return a float."""
    arr_ref = _gradient(h=600, w=800)
    arr_impl = _gradient(h=300, w=400)
    ref = _make_image(arr_ref, tmp_path)
    impl = _make_image(arr_impl, tmp_path)
    score = multiscale_ssim(ref, impl)
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# severity tests
# ---------------------------------------------------------------------------


def test_severity_ok() -> None:
    """fontSize: ref=16px, impl=16px → ok."""
    assert severity("fontSize", "16px", "16px") == "ok"


def test_severity_warn_fontsize() -> None:
    """fontSize: ref=16px, impl=18px (2px diff == threshold 2px) → warn."""
    assert severity("fontSize", "16px", "18px") == "warn"


def test_severity_critical_fontsize() -> None:
    """fontSize: ref=16px, impl=22px (6px > critical threshold 4px) → critical."""
    assert severity("fontSize", "16px", "22px") == "critical"


def test_severity_viewport_width() -> None:
    """width: ref=720px, impl=660px (60px diff < 72px threshold, viewport=1440) → ok."""
    assert severity("width", "720px", "660px", viewport=1440) == "ok"


def test_severity_non_numeric() -> None:
    """ref=auto → non-numeric, can't compare → ok."""
    assert severity("fontSize", "auto", "100px") == "ok"


def test_severity_margin() -> None:
    """margin: ref=10px, impl=20px (10px diff > 8px threshold, < 16px critical) → warn."""
    assert severity("margin", "10px", "20px") == "warn"


def test_severity_margin_shorthand() -> None:
    """margin shorthand — all tokens compared, worst severity wins.

    threshold = 100px * 0.08 = 8px; critical = diff > 16px.
    """
    # token 1: 8px vs 8px → ok; token 2: 16px vs 0px (16px diff == critical threshold, not >) → warn
    assert severity("margin", "8px 16px 8px 16px", "8px 0px 8px 0px") == "warn"
    # token 2 diff = 17px > 16px → critical
    assert severity("margin", "8px 17px 8px 16px", "8px 0px 8px 0px") == "critical"
    # all tokens identical → ok
    assert severity("margin", "8px 16px 8px 16px", "8px 16px 8px 16px") == "ok"


def test_severity_padding_shorthand_warn() -> None:
    """padding shorthand: ref='10px 20px', impl='20px 20px' — first tokens differ by 10px → warn."""
    assert severity("padding", "10px 20px", "20px 20px") == "warn"


def test_severity_shorthand_non_numeric_first_token() -> None:
    """Shorthand with non-numeric first token (e.g. 'auto 10px') → ok (can't compare)."""
    assert severity("margin", "auto 10px", "0px 10px") == "ok"


def test_multiscale_ssim_tiny_image(tmp_path: Path) -> None:
    """Images smaller than 32x32 must not crash (quarter-scale can produce sub-3px dims)."""
    arr = np.zeros((16, 16), dtype=np.float32)
    ref = _make_image(arr, tmp_path)
    impl = _make_image(arr.copy(), tmp_path)
    score = multiscale_ssim(ref, impl)
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


def test_load_gray_corrupted_image(tmp_path: Path) -> None:
    """Corrupted image bytes must raise ValueError, not an unhandled PIL exception."""
    from ui_clone.metrics import _load_gray

    bad = tmp_path / "corrupt.png"
    bad.write_bytes(b"not a png at all \x00\xff")
    with pytest.raises(ValueError, match="Cannot load image"):
        _load_gray(bad)


def test_severity_shorthand_unequal_token_count() -> None:
    """Shorthand values with different token counts must not silently ignore extra tokens.

    Regression test for: zip() truncating at the shorter side.
    "0px" vs "0px 16px 0px 16px" — extra tokens in impl (16px) must be detected.
    threshold for margin = 100px * 0.08 = 8px; 16px > 8px → warn.
    """
    assert severity("margin", "0px", "0px 16px 0px 16px") == "warn"


def test_severity_shorthand_extra_ref_token() -> None:
    """Extra tokens in ref side must also be caught (fillvalue=0.0 for impl side)."""
    # "16px 0px" vs "0px" → second ref token 0px vs 0.0 → ok for token 2;
    # first token: 16px vs 0px → 16px diff > 8px threshold → warn
    assert severity("margin", "16px 0px", "0px") == "warn"


def test_multiscale_ssim_degenerate_1x1(tmp_path: Path) -> None:
    """1x1 pixel image — all scales too small, returns 0.0 (degenerate case)."""
    arr = np.zeros((1, 1), dtype=np.float32)
    ref = _make_image(arr, tmp_path)
    impl = _make_image(arr.copy(), tmp_path)
    score = multiscale_ssim(ref, impl)
    assert score == 0.0


# ── defect_severity ──


def test_defect_severity_critical_missing_section() -> None:
    """Section with height ratio < 0.3 → critical."""
    assert defect_severity(height_ratio=0.1) == "critical"


def test_defect_severity_critical_svg_text_missing() -> None:
    """SVG_TEXT_MISSING in structure issues → critical."""
    assert defect_severity(structure_issues=["SVG_TEXT_MISSING: ref has SVG text"]) == "critical"


def test_defect_severity_critical_high_ae() -> None:
    """AE > threshold * 10 → critical."""
    assert defect_severity(ae=25000, threshold=2000) == "critical"


def test_defect_severity_critical_no_children() -> None:
    """Ref has children, impl has 0 → critical."""
    assert defect_severity(child_count_ref=5, child_count_impl=0) == "critical"


def test_defect_severity_major_ae_over_threshold() -> None:
    """AE > threshold but < threshold * 10 → major."""
    assert defect_severity(ae=5000, threshold=2000) == "major"


def test_defect_severity_major_height_mismatch() -> None:
    """Height ratio 0.5 (outside 0.7-1.3) → major."""
    assert defect_severity(height_ratio=0.5) == "major"


def test_defect_severity_major_display_mismatch() -> None:
    """DISPLAY_MISMATCH → major."""
    assert defect_severity(structure_issues=["DISPLAY_MISMATCH: ref=grid, impl=flex"]) == "major"


def test_defect_severity_minor_small_ae() -> None:
    """AE between 500 and threshold → minor."""
    assert defect_severity(ae=800, threshold=2000) == "minor"


def test_defect_severity_ok_low_ae() -> None:
    """AE ≤ 500 → ok."""
    assert defect_severity(ae=200) == "ok"


def test_defect_severity_ok_no_issues() -> None:
    """No issues at all → ok."""
    assert defect_severity() == "ok"
