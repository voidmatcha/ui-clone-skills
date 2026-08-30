"""Ref-instability dynamic-section calibration for section-compare.

A section is REF-PROVEN-DYNAMIC when the LIVE REFERENCE cannot match its own
frame-to-frame capture over a settle window — two independent captures of the
SAME reference section diverge beyond a noise floor (framer scroll-scrub /
useScroll-useTransform parallax, splash intros, auto-advancing carousels).

Such a section cannot be pixel-AE-compared against ANY impl — not even a
byte-faithful clone — because the reference is non-deterministic at capture
time. It switches from strict pixel-AE to STRUCTURAL / LAYOUT PARITY:

  - the impl must occupy the SAME layout box (within a tight dim tolerance), and
  - be structurally NO LESS similar to the reference than the reference is to
    ITSELF — i.e. within the reference's own measured scrub noise floor.

This is detection-preserving, NOT a hiding place: the classification is driven
ONLY by the reference's own measured instability (a static section can never be
classified dynamic, so its strict AE stays), and a real defect planted in a
calibrated dynamic section — content blanked (near-black), the section box
resized/dropped, or a gross structural change beyond the scrub noise floor —
STILL FAILS. See tests/measure/test_section_dynamic.py.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import TypeGuard


def _is_number(value: object) -> TypeGuard[int | float]:
    """Keep shell-embedded imports compatible with macOS system Python 3.9."""
    return isinstance(value, int) or isinstance(value, float)


# A section whose own two reference frames diverge beyond this AE/Mpx is dynamic.
# Defaults to section-compare's static pass THRESHOLD: if the reference cannot
# even self-match within the band a faithful static clone must hit, it is dynamic.
DYNAMIC_REF_SELF_AE_PER_MPX_DEFAULT = 2000.0

# How far the impl may diverge from the reference RELATIVE to the reference's own
# self-noise. The impl is captured at a third scrub phase, so it can sit up to
# ~2x the single ref-self sample from the reference's captured frame; the additive
# epsilon covers a near-static section whose self-noise rounds to ~0.
DYNAMIC_DSSIM_FLOOR_MULT_DEFAULT = 2.5
DYNAMIC_DSSIM_FLOOR_EPS_DEFAULT = 0.015
# Layout box footprint tolerance (the section must occupy the same geometry).
DYNAMIC_DIM_TOL_DEFAULT = 0.06

# Same-frame strict-AE ceiling for a dynamic section (Task B / specific regression). In the
# frozen-calib flow the impl is captured at the SAME forced scroll frame the ref
# was frozen at, so pixel AE is meaningful again (low for a faithful clone, high
# for a real defect). The dssim floor alone let a same-layout-but-wrong-content
# defect (wrong copy/image/plausible-wrong scrub content) sneak through; the AE
# ceiling restores strict AE as a MUST-ALSO-PASS gate. The ceiling is the
# reference's OWN same-frame AE noise (ref vs impl-path ref-calib) padded by mult
# + eps — a defect exceeding the ref's own jitter STILL FAILS even if dssim<=floor.
DYNAMIC_AE_CEILING_MULT_DEFAULT = 2.5
DYNAMIC_AE_CEILING_EPS_DEFAULT = DYNAMIC_REF_SELF_AE_PER_MPX_DEFAULT  # 2000 AE/Mpx
# Absolute fallback ceiling used when the ref's own same-frame AE noise is not
# available (no ref-calib): generous enough to absorb residual scrub jitter but
# far below a gross content defect.
DYNAMIC_AE_CEILING_ABS_DEFAULT = 8000.0

# ── Motion shift-search (Commit 2) ──────────────────────────────────────────
# A faithful scroll-reveal section can be caught at a different scroll-reveal
# sub-frame than the ref: IDENTICAL content, uniformly translated vertically by
# tens-to-low-hundreds of px (e.g. realfood 'winning': AE/Mpx 104739, the same
# text doubled at ~150px Y-offset). A wide NON-circular vertical shift-search
# realigns it; a BROKEN impl (wrong/scrambled content) does not realign and is
# rejected by the structure + localized-defect + collapse guards below.
MOTION_COLLAPSE_MIN_DEFAULT = 0.15      # min AE drop ratio (1 - AE_shift/AE_zero)
MOTION_SHIFTED_DSSIM_MAX_DEFAULT = 0.015  # shifted pair must be structurally tight
MOTION_MIN_STRUCT_DEFAULT = 0.85        # 1 - shifted_dssim floor (structure kept)
MOTION_AE_ZERO_FLOOR_DEFAULT = 1.0      # below this, AE_zero is noise; no collapse


def is_ref_dynamic(
    self_ae_per_mpx: float | None,
    *,
    threshold: float = DYNAMIC_REF_SELF_AE_PER_MPX_DEFAULT,
) -> bool:
    """True when the reference's own two frames diverge beyond ``threshold``."""
    return _is_number(self_ae_per_mpx) and float(self_ae_per_mpx) > threshold


def dynamic_floor(
    ref_self_dssim: float | None,
    *,
    mult: float = DYNAMIC_DSSIM_FLOOR_MULT_DEFAULT,
    eps: float = DYNAMIC_DSSIM_FLOOR_EPS_DEFAULT,
) -> float | None:
    """The structural-divergence ceiling derived from the ref's own scrub noise."""
    if not _is_number(ref_self_dssim):
        return None
    return float(ref_self_dssim) * mult + eps


def dynamic_ae_ceiling(
    ref_self_ae_per_mpx: float | None,
    *,
    mult: float = DYNAMIC_AE_CEILING_MULT_DEFAULT,
    eps: float = DYNAMIC_AE_CEILING_EPS_DEFAULT,
    abs_default: float = DYNAMIC_AE_CEILING_ABS_DEFAULT,
) -> float:
    """Same-frame strict-AE ceiling for a dynamic section.

    Derived from the reference's OWN same-frame AE noise (ref vs impl-path
    ref-calib) when available: ``ref_self_ae_per_mpx * mult + eps``. Falls back to
    ``abs_default`` when the ref's self-AE is unknown (no calib). The impl's
    same-frame AE/Mpx must stay at or below this ceiling — a real defect at the
    same scroll frame exceeds it and FAILS even if the dssim floor passes.
    """
    if _is_number(ref_self_ae_per_mpx) and float(ref_self_ae_per_mpx) >= 0:
        return float(ref_self_ae_per_mpx) * mult + eps
    return abs_default


def _dim_ratio(a: float | None, b: float | None) -> float:
    """max/min ratio of two positive dims, 1.0 when either is missing/zero."""
    if _is_number(a) and _is_number(b) and a > 0 and b > 0:
        return max(float(a), float(b)) / min(float(a), float(b))
    return 1.0


def dynamic_section_verdict(
    *,
    ref_self_dssim: float | None,
    impl_dssim: float | None,
    ref_w: float,
    ref_h: float,
    impl_w: float,
    impl_h: float,
    impl_near_black: bool,
    calib_w: float | None = None,
    calib_h: float | None = None,
    impl_ae_per_mpx: float | None = None,
    ref_self_ae_per_mpx: float | None = None,
    dssim_floor_mult: float = DYNAMIC_DSSIM_FLOOR_MULT_DEFAULT,
    dssim_floor_eps: float = DYNAMIC_DSSIM_FLOOR_EPS_DEFAULT,
    ae_ceiling_mult: float = DYNAMIC_AE_CEILING_MULT_DEFAULT,
    ae_ceiling_eps: float = DYNAMIC_AE_CEILING_EPS_DEFAULT,
    ae_ceiling_abs: float = DYNAMIC_AE_CEILING_ABS_DEFAULT,
    dim_tol: float = DYNAMIC_DIM_TOL_DEFAULT,
) -> tuple[str, str]:
    """Structural/layout parity verdict for a ref-dynamic section.

    Returns ``(status, reason)`` with status in {"pass", "fail"}. Detection
    classes that still FAIL: blanked content (near-black), a resized/dropped
    section box BEYOND the reference's own cross-load box variance, and a
    structural change beyond the reference's own scrub noise floor.

    A scroll-scrub / scaffold-scale section's bounding box itself varies across
    loads (a page-root scaffold-scale animation can render the SAME element at
    w1440 on one load and w964 on another). The dim check therefore allows the
    impl to diverge from the reference up to the REFERENCE'S OWN box variance
    (ref crop vs calib crop), not a flat tolerance — measured on the reference,
    so a genuinely resized/dropped impl box (beyond the ref's own scrub jitter,
    e.g. a half-height section whose ref height is stable) STILL FAILs.
    """
    if impl_near_black:
        return "fail", "impl dynamic section near-black — content missing, not a scrub phase"
    if ref_w <= 0 or ref_h <= 0 or impl_w <= 0 or impl_h <= 0:
        return "fail", "degenerate section dimensions"
    # Allowed divergence = the reference's own cross-load box variance, padded by
    # the base tolerance (>= the flat tolerance so static-box dynamic sections are
    # unaffected).
    allow_w = max(1.0 + dim_tol, _dim_ratio(ref_w, calib_w) * (1.0 + dim_tol))
    allow_h = max(1.0 + dim_tol, _dim_ratio(ref_h, calib_h) * (1.0 + dim_tol))
    w_ratio = max(ref_w, impl_w) / min(ref_w, impl_w)
    h_ratio = max(ref_h, impl_h) / min(ref_h, impl_h)
    if w_ratio > allow_w or h_ratio > allow_h:
        return (
            "fail",
            f"layout box diverged (w {w_ratio:.2f}x>{allow_w:.2f}x, "
            f"h {h_ratio:.2f}x>{allow_h:.2f}x; ref self-variance "
            f"w {_dim_ratio(ref_w, calib_w):.2f}x h {_dim_ratio(ref_h, calib_h):.2f}x)",
        )
    # Same-frame strict-AE gate (Task B / specific regression — closes the F1 dssim-only
    # defect-hiding hole). When the impl was captured at the SAME forced scroll
    # frame as the frozen ref, pixel AE is meaningful: a faithful clone is LOW, a
    # wrong-content defect is HIGH. The dssim floor below cannot be the only gate
    # (a same-layout/wrong-copy defect can sit under it), so strict AE must ALSO
    # pass. Skipped only when no AE was supplied (legacy callers / cross-frame
    # captures that cannot trust AE) — the real verify path always supplies it.
    if _is_number(impl_ae_per_mpx):
        ae_ceiling = dynamic_ae_ceiling(
            ref_self_ae_per_mpx,
            mult=ae_ceiling_mult,
            eps=ae_ceiling_eps,
            abs_default=ae_ceiling_abs,
        )
        if float(impl_ae_per_mpx) > ae_ceiling:
            return (
                "fail",
                f"same-frame AE/Mpx {float(impl_ae_per_mpx):.0f} exceeds dynamic AE "
                f"ceiling {ae_ceiling:.0f} (ref self-AE "
                f"{ref_self_ae_per_mpx if ref_self_ae_per_mpx is not None else 'n/a'}) "
                f"— content defect, not scrub jitter",
            )
    floor = dynamic_floor(ref_self_dssim, mult=dssim_floor_mult, eps=dssim_floor_eps)
    if impl_dssim is None or floor is None:
        return "fail", "missing dssim measurement for a dynamic section — cannot grant parity"
    if float(impl_dssim) > floor:
        return (
            "fail",
            f"structural divergence dssim={float(impl_dssim):.4f} exceeds ref scrub "
            f"noise floor {floor:.4f}",
        )
    _ae_note = (
        f", AE/Mpx={float(impl_ae_per_mpx):.0f}"
        if _is_number(impl_ae_per_mpx)
        else ""
    )
    return (
        "pass",
        f"structural/layout parity within ref scrub noise floor "
        f"(dssim={float(impl_dssim):.4f} <= {floor:.4f}, box w {w_ratio:.2f}x h "
        f"{h_ratio:.2f}x{_ae_note})",
    )


def motion_phase_verdict(
    *,
    ae_zero: float | None,
    ae_shift_min: float | None,
    shifted_dssim: float | None,
    ref_has_variance: bool,
    localized_defect: bool,
    collapse_min: float = MOTION_COLLAPSE_MIN_DEFAULT,
    shifted_dssim_max: float = MOTION_SHIFTED_DSSIM_MAX_DEFAULT,
    min_struct: float = MOTION_MIN_STRUCT_DEFAULT,
    ae_zero_floor: float = MOTION_AE_ZERO_FLOOR_DEFAULT,
) -> tuple[str, str]:
    """Motion shift-search parity verdict for a vertically phase-shifted section.

    Returns ``(status, reason)`` with status in {"pass", "fail"}. PASS means the
    section is a faithful scroll-reveal caught at a different sub-frame: a wide
    NON-circular vertical shift collapses its AE AND the shifted pair stays
    structurally tight AND no localized defect band survives. Any broken impl
    (wrong content that only aligns at an extreme offset, a low-structure
    scramble, a localized misplacement, or a blank/no-variance ref) FAILS.

    All four guards must hold:
      G1 collapse:   ae_zero is real (>= floor & has variance) and the shift
                     search dropped AE by >= collapse_min fraction. A wrong-
                     content section does not collapse (best offset is ~dy=0).
      G2 tightness:  the shifted pair's dssim is <= shifted_dssim_max — the
                     realigned content is near-pixel-identical, not merely
                     'roughly similar after sliding'.
      G3 structure:  1 - shifted_dssim >= min_struct — a hard structural floor
                     that a scrambled/low-detail impl cannot reach even if some
                     extreme offset happens to lower raw AE.
      G4 no defect:  the localized-defect band check (computed by the caller at
                     the SHIFTED alignment) found no catastrophic local band — a
                     single misplaced/missing element that survives realignment,
                     which a global shift cannot mask.
    """
    if not ref_has_variance:
        return "fail", "ref crop has no variance — shift-search cannot grant motion-phase parity"
    if localized_defect:
        return "fail", "localized structural defect band present — not a uniform scroll-phase shift"
    if not _is_number(ae_zero) or not _is_number(ae_shift_min):
        return "fail", "missing AE measurement for motion shift-search"
    if not _is_number(shifted_dssim):
        return "fail", "missing shifted-pair dssim for motion shift-search"
    az = float(ae_zero)
    asm = float(ae_shift_min)
    sd = float(shifted_dssim)
    if az < ae_zero_floor or az <= 0:
        return "fail", f"AE@0 {az:.0f} below noise floor — nothing to realign, not a motion phase"
    collapse = 1.0 - (asm / az) if az > 0 else 0.0
    if collapse < collapse_min:
        return (
            "fail",
            f"AE did not collapse under shift-search (drop {collapse:.3f} < {collapse_min:.3f}; "
            f"AE@0={az:.0f} AE_shiftmin={asm:.0f}) — wrong content, not a phase offset",
        )
    if sd > shifted_dssim_max:
        return (
            "fail",
            f"shifted-pair dssim {sd:.4f} > {shifted_dssim_max:.4f} — realigned content "
            f"not pixel-tight, only loosely slid into place",
        )
    structure = 1.0 - sd
    if structure < min_struct:
        return (
            "fail",
            f"shifted structure {structure:.3f} < {min_struct:.3f} — scrambled/low-detail "
            f"impl cannot earn motion-phase parity",
        )
    return (
        "pass",
        f"motion-phase parity: AE collapsed {collapse:.3f} under non-circular shift "
        f"(AE@0={az:.0f}->{asm:.0f}), shifted structure {structure:.3f} "
        f"(dssim={sd:.4f}<= {shifted_dssim_max:.4f}), no localized defect",
    )
