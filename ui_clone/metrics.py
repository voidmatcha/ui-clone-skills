"""
Multiscale SSIM image comparison and viewport-relative CSS severity scoring.

Dependencies: scikit-image, Pillow (declared in pyproject.toml).
"""

import re
import warnings
from itertools import zip_longest
from pathlib import Path

import numpy as np
from PIL import Image
from skimage.color import deltaE_ciede2000, rgb2lab
from skimage.metrics import structural_similarity
from skimage.transform import resize as sk_resize

# Perceptual just-noticeable difference in CIEDE2000 units. A color shift below
# ~2.3 ΔE is generally imperceptible; at/above it the drift is visible to the
# eye even when a fuzz-tolerant pixel AE counts the pixels as "identical".
JND_DELTA_E2000 = 2.3

# ---------------------------------------------------------------------------
# multiscale_ssim
# ---------------------------------------------------------------------------


def _load_gray(path: Path) -> np.ndarray:
    """Load an image as a float32 [0, 1] grayscale array."""
    try:
        img = Image.open(path).convert("L")
    except (OSError, ValueError) as e:
        raise ValueError(f"Cannot load image {path}: {e}") from e
    return np.asarray(img, dtype=np.float32) / 255.0


def _resize_arr(arr: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Resize a 2-D float array to (height, width) without uint8 quantization.

    Uses bilinear (order=1) for downscaling to avoid ringing artifacts,
    bicubic (order=3) for upscaling to preserve detail.
    """
    h, w = size
    is_downscale = h * w < arr.shape[0] * arr.shape[1]
    order = 1 if is_downscale else 3
    resized: np.ndarray = sk_resize(
        arr, (h, w), order=order, mode="reflect", anti_aliasing=True
    ).astype(np.float32)
    return resized


def _ssim_at(arr1: np.ndarray, arr2: np.ndarray, scale: float) -> float | None:
    """Compute SSIM between two grayscale arrays at a given scale factor.

    Resizes both arrays by ``scale``, then computes structural similarity.
    Returns None if the scaled dimensions are smaller than 3x3 (minimum
    for SSIM's sliding window).

    Args:
        arr1: Reference grayscale image, float32 [0, 1].
        arr2: Implementation grayscale image, float32 [0, 1].
        scale: Resize factor (e.g. 0.25 for quarter resolution).
    """
    h, w = arr1.shape
    new_h = max(1, round(h * scale))
    new_w = max(1, round(w * scale))
    # Skip scale if too small for SSIM (minimum 3x3 required)
    if new_h < 3 or new_w < 3:
        return None
    a1 = _resize_arr(arr1, (new_h, new_w))
    a2 = _resize_arr(arr2, (new_h, new_w))
    min_dim = min(new_h, new_w)
    win_size = min(7, min_dim if min_dim % 2 == 1 else min_dim - 1)
    return float(structural_similarity(a1, a2, data_range=1.0, win_size=win_size))


def multiscale_ssim(ref: Path, impl: Path) -> float:
    """
    3-stage multiscale SSIM: 1/4 → 1/2 → original resolution.

    Weighted average: 0.5 * ssim_quarter + 0.3 * ssim_half + 0.2 * ssim_full.
    Returns float in [0.0, 1.0] where 1.0 = identical.

    If images differ in size, impl is resized to match ref before scaling.
    Scales that produce images smaller than 3×3 are skipped; weights are
    redistributed proportionally among the remaining scales.
    """
    ref_arr = _load_gray(ref)
    impl_arr = _load_gray(impl)

    ref_h, ref_w = ref_arr.shape

    # If sizes differ, resize impl to match ref
    if impl_arr.shape != ref_arr.shape:
        impl_arr = _resize_arr(impl_arr, (ref_h, ref_w))

    scales_weights = [(0.25, 0.5), (0.5, 0.3), (1.0, 0.2)]
    total_weight = 0.0
    weighted_sum = 0.0
    skipped_scales: list[float] = []
    for scale, weight in scales_weights:
        s = _ssim_at(ref_arr, impl_arr, scale)
        if s is not None:
            weighted_sum += s * weight
            total_weight += weight
        else:
            skipped_scales.append(scale)
    if skipped_scales:
        warnings.warn(
            f"multiscale_ssim: skipped scales {skipped_scales} (image too small: "
            f"{ref_h}×{ref_w}px). Weights redistributed among remaining scales.",
            stacklevel=2,
        )
    if total_weight == 0.0:
        return 0.0  # degenerate case: all scales too small
    return weighted_sum / total_weight


# ---------------------------------------------------------------------------
# mean_delta_e2000 — perceptual color drift
# ---------------------------------------------------------------------------


def _load_rgb(path: Path) -> np.ndarray:
    """Load an image as a float32 [0, 1] RGB array."""
    try:
        img = Image.open(path).convert("RGB")
    except (OSError, ValueError) as e:
        raise ValueError(f"Cannot load image {path}: {e}") from e
    return np.asarray(img, dtype=np.float32) / 255.0


def mean_delta_e2000(ref: Path, impl: Path) -> float:
    """Mean CIEDE2000 perceptual color distance between two images.

    Catches uniform tint/gamma drift that a fuzz-tolerant pixel AE misses: a
    clone shifted a couple of ΔE across every pixel registers ~0 AE under an 8%
    fuzz yet is perceptibly off-color. The mean over all pixels is a *systematic*
    drift signal — a handful of locally-divergent regions on an otherwise
    faithful clone average out below the JND, so this stays orthogonal to the
    structural (dssim) and pixel (AE) signals rather than double-counting them.

    Impl is resized to the ref grid when sizes differ. Returns the mean ΔE2000
    over all pixels; 0.0 = identical.
    """
    ref_rgb = _load_rgb(ref)
    impl_rgb = _load_rgb(impl)
    if impl_rgb.shape != ref_rgb.shape:
        impl_rgb = sk_resize(
            impl_rgb, ref_rgb.shape, order=1, mode="reflect", anti_aliasing=True
        ).astype(np.float32)
    ref_lab = rgb2lab(ref_rgb)
    impl_lab = rgb2lab(impl_rgb)
    return float(deltaE_ciede2000(ref_lab, impl_lab).mean())


# ---------------------------------------------------------------------------
# severity
# ---------------------------------------------------------------------------

# (context_size_px, tolerance_fraction)
_PROPERTY_CONFIG: dict[str, tuple[str, float]] = {
    "fontSize": ("fixed:100", 0.02),
    "width": ("viewport", 0.05),
    "margin": ("fixed:100", 0.08),
    "padding": ("fixed:100", 0.08),
}
_DEFAULT_CONFIG: tuple[str, float] = ("fixed:100", 0.05)


# Unit group is optional: computed styles can return unitless values (e.g. line-height: 1.5).
_CSS_UNIT_RE = re.compile(r"^([+-]?\d*\.?\d+)\s*(px|em|rem|%|vw|vh|vmin|vmax|ch|ex|cap|lh)?$")


def _parse_px_tokens(val: str) -> list[float]:
    """Extract numeric values from a CSS value string.

    Handles single values ("8px") and shorthand values ("8px 16px 8px 16px").
    Recognises common CSS units (px, em, rem, %, vw, vh, etc.) and bare numbers.
    Raises ValueError if any token is non-numeric (e.g. "auto", "inherit").
    """
    tokens = val.strip().split()
    results = []
    for token in tokens:
        m = _CSS_UNIT_RE.match(token.strip())
        if not m:
            raise ValueError(f"Non-numeric token: {token!r}")
        results.append(float(m.group(1)))
    if not results:
        raise ValueError(f"No numeric tokens in: {val!r}")
    return results


def _severity_for_single(diff: float, threshold: float) -> str:
    if diff > threshold * 2:
        return "critical"
    if diff >= threshold:
        return "warn"
    return "ok"


_SEVERITY_RANK = {"ok": 0, "warn": 1, "critical": 2}


# ---------------------------------------------------------------------------
# defect_severity — section-level defect classification
# ---------------------------------------------------------------------------


def defect_severity(
    *,
    ae: int | None = None,
    threshold: int = 2000,
    height_ratio: float | None = None,
    child_count_ref: int = 0,
    child_count_impl: int = 0,
    structure_issues: list[str] | None = None,
) -> str:
    """Classify a section-level defect as critical, major, or minor.

    Used by section-compare.sh (via Python inline) to prioritize fixes.

    Returns:
        "critical" — section missing, layout broken, or structure fundamentally wrong
        "major"    — visible differences (color, font, spacing) but layout intact
        "minor"    — sub-pixel differences, anti-aliasing, dynamic content variance
    """
    issues = structure_issues or []

    # Critical: section missing or structurally broken
    if height_ratio is not None and (height_ratio < 0.3 or height_ratio > 3.0):
        return "critical"
    if any("SVG_TEXT_MISSING" in i or "LAYOUT_MISMATCH" in i for i in issues):
        return "critical"
    if child_count_ref > 0 and child_count_impl == 0:
        return "critical"
    if ae is not None and ae > threshold * 10:
        return "critical"

    # Major: clearly visible differences
    if ae is not None and ae > threshold:
        return "major"
    if height_ratio is not None and (height_ratio < 0.7 or height_ratio > 1.3):
        return "major"
    if any("HEIGHT_MISMATCH" in i or "CHILD_COUNT_MISMATCH" in i for i in issues):
        return "major"
    if any("DISPLAY_MISMATCH" in i for i in issues):
        return "major"

    # Minor: small differences
    if ae is not None and ae > 500:
        return "minor"

    return "ok"


def severity(prop: str, ref_val: str, impl_val: str, viewport: int = 1440) -> str:
    """
    Return "ok", "warn", or "critical" based on viewport-relative CSS error.

    Thresholds:
        fontSize:  context=100px, tolerance=2%  → threshold=2px
        width:     context=viewport, tolerance=5% → threshold=72px @1440
        margin/padding: context=100px, tolerance=8% → threshold=8px
        default:   context=100px, tolerance=5%  → threshold=5px

    Non-numeric values → "ok".
    For shorthand values (e.g. "8px 16px 8px 16px"), all tokens are compared
    pairwise; the worst severity across all token pairs is returned.
    Token counts that don't match (e.g. "8px" vs "8px 0px 8px 0px") are
    padded with 0.0 so extra tokens in either side are not silently ignored.
    """
    try:
        ref_tokens = _parse_px_tokens(ref_val)
        impl_tokens = _parse_px_tokens(impl_val)
    except (ValueError, AttributeError, IndexError):
        return "ok"

    context_spec, tolerance_pct = _PROPERTY_CONFIG.get(prop, _DEFAULT_CONFIG)

    if context_spec == "viewport":
        context_size = float(viewport)
    else:
        # "fixed:100" → 100.0
        context_size = float(context_spec.split(":")[1])

    threshold = context_size * tolerance_pct

    worst = "ok"
    for ref_f, impl_f in zip_longest(ref_tokens, impl_tokens, fillvalue=0.0):
        s = _severity_for_single(abs(ref_f - impl_f), threshold)
        if _SEVERITY_RANK[s] > _SEVERITY_RANK[worst]:
            worst = s
        if worst == "critical":
            break  # can't get worse

    return worst
