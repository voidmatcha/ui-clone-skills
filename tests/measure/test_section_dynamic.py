"""Ref-instability dynamic-section calibration — classification + the HARD
CONSTRAINT that a real defect planted in a calibrated dynamic section STILL FAILS
(the calibration must not become a hiding place for defects). batch-13 ITEM 1."""
from __future__ import annotations

from typing import Any

from ui_clone.section_dynamic import (
    dynamic_section_verdict,
    is_ref_dynamic,
    motion_phase_verdict,
)


def _verdict(**over: Any) -> tuple[str, str]:
    """Faithful-clone baseline for a ref-dynamic section; tests override one
    field at a time. The reference's own two frames already diverge (scrub), so
    ref_self_dssim is non-trivial and the impl sits within its noise floor."""
    kw: dict[str, Any] = dict(
        ref_self_dssim=0.10, impl_dssim=0.12,
        ref_w=1440.0, ref_h=850.0, impl_w=1440.0, impl_h=850.0,
        impl_near_black=False,
    )
    kw.update(over)
    return dynamic_section_verdict(**kw)


# ── classification: driven ONLY by the reference's own instability ──────────


def test_static_section_never_classified_dynamic() -> None:
    # A reference that self-matches (AE/Mpx 0) is static — strict AE stays.
    assert is_ref_dynamic(0.0) is False
    assert is_ref_dynamic(500.0) is False


def test_ref_unstable_section_is_dynamic() -> None:
    # framer scroll-scrub: the reference's two frames diverge far beyond the
    # static pass band -> dynamic.
    assert is_ref_dynamic(40000.0) is True


def test_classification_threshold_tunable() -> None:
    assert is_ref_dynamic(3000.0, threshold=5000.0) is False
    assert is_ref_dynamic(8000.0, threshold=5000.0) is True


# ── faithful clone of a dynamic section self-passes via structural parity ───


def test_faithful_dynamic_section_passes_within_noise_floor() -> None:
    # impl captured at a third scrub phase: dssim ~ the ref's own self-noise.
    assert _verdict(impl_dssim=0.12)[0] == "pass"


def test_self_pass_impl_equal_noise_floor_passes() -> None:
    assert _verdict(impl_dssim=0.20)[0] == "pass"  # 0.20 <= 0.10*2.5 + 0.015


# ── HARD CONSTRAINT: real defects in a calibrated dynamic section STILL FAIL ─


def test_defect_blanked_content_still_fails() -> None:
    status, reason = _verdict(impl_dssim=0.05, impl_near_black=True)
    assert status == "fail", reason
    assert "near-black" in reason


def test_defect_resized_section_box_still_fails() -> None:
    # the impl renders the section at a collapsed height — a real layout defect
    status, reason = _verdict(impl_dssim=0.05, impl_h=500.0)
    assert status == "fail", reason
    assert "layout box" in reason


def test_defect_gross_structural_change_beyond_floor_still_fails() -> None:
    # the impl drops the pyramid / replaces the content: dssim far beyond the
    # reference's own scrub noise floor (0.10*2.5 + 0.015 = 0.265)
    status, reason = _verdict(impl_dssim=0.60)
    assert status == "fail", reason
    assert "noise floor" in reason


def test_missing_dssim_cannot_grant_parity() -> None:
    status, reason = _verdict(impl_dssim=None)
    assert status == "fail", reason
    assert _verdict(impl_dssim=0.05, ref_self_dssim=None)[0] == "fail"


def test_low_noise_floor_keeps_detection_tight() -> None:
    # a barely-dynamic section (small self-noise) gets only a small floor, so a
    # modest structural defect still fails — the floor scales with the proven
    # instability, it is not a blanket pass.
    # floor = 0.02*2.5 + 0.015 = 0.065
    assert _verdict(impl_dssim=0.05, ref_self_dssim=0.02)[0] == "pass"


# ── box-scale variance: the dim check floors on the ref's OWN box variance ───


def test_dynamic_box_scale_variance_within_ref_self_variance_passes() -> None:
    # realfood broken_system: the page-root-scaffold-scale renders the SAME
    # element w1440 on the frozen-ref load and w964 on the calib+impl loads
    # (heights identical). The impl's box divergence equals the reference's OWN
    # cross-load box variance, so structural parity is granted.
    status, reason = _verdict(
        impl_dssim=0.12, ref_w=1440.0, impl_w=964.0, calib_w=964.0,
    )
    assert status == "pass", reason


def test_defect_width_collapse_with_stable_ref_box_still_fails() -> None:
    # the reference's box is STABLE across loads (calib == ref), so a narrow impl
    # box is a real defect, not scrub jitter — must FAIL even on a dynamic section.
    status, reason = _verdict(
        impl_dssim=0.05, ref_w=1440.0, impl_w=964.0, calib_w=1440.0,
    )
    assert status == "fail", reason
    assert "layout box" in reason


def test_defect_box_beyond_ref_self_variance_still_fails() -> None:
    # ref box jitters ~1.06x across loads (calib), but the impl box diverges
    # 1.60x — beyond the proven scrub jitter -> real defect, FAIL.
    status, reason = _verdict(
        impl_dssim=0.05, ref_h=850.0, impl_h=530.0, calib_h=800.0,
    )
    assert status == "fail", reason
    assert "layout box" in reason
    assert _verdict(impl_dssim=0.20, ref_self_dssim=0.02)[0] == "fail"


# ── Task B / loop-16: same-frame strict-AE gate (closes the F1 dssim-only hole) ──


def test_same_frame_content_defect_fails_via_ae_ceiling_despite_low_dssim() -> None:
    # Frozen-calib: impl captured at the SAME scroll frame. A wrong-content defect
    # (wrong copy/image) at the same frame produces HIGH same-frame AE even if it
    # sneaks under the dssim floor. The AE ceiling must FAIL it.
    status, reason = _verdict(
        impl_dssim=0.05, ref_self_dssim=0.10,  # dssim would PASS (0.05 <= 0.265)
        impl_ae_per_mpx=45000.0, ref_self_ae_per_mpx=1200.0,  # AE far over ceiling
    )
    assert status == "fail", reason
    assert "AE/Mpx" in reason and "ceiling" in reason


def test_faithful_same_frame_passes_with_low_ae_and_dssim() -> None:
    # Faithful clone at the same frame: low AE (within ref's own same-frame noise)
    # and low dssim -> PASS, and the pass reason surfaces the AE it cleared.
    status, reason = _verdict(
        impl_dssim=0.12, ref_self_dssim=0.10,
        impl_ae_per_mpx=1800.0, ref_self_ae_per_mpx=1200.0,  # <= 1200*2.5+2000=5000
    )
    assert status == "pass", reason
    assert "AE/Mpx" in reason


def test_ae_gate_skipped_when_no_ae_supplied_preserves_legacy_dssim_behavior() -> None:
    # Legacy callers / cross-frame captures that cannot trust AE: with no AE
    # supplied the gate is skipped and the dssim/box logic alone governs.
    assert _verdict(impl_dssim=0.12, ref_self_dssim=0.10)[0] == "pass"
    assert _verdict(impl_dssim=0.90, ref_self_dssim=0.02)[0] == "fail"  # dssim over floor


def test_ref_self_ae_raises_ceiling_for_genuinely_jittery_section() -> None:
    # A truly-irreproducible section (high ref self-AE) gets a higher ceiling so
    # its own scrub jitter is absorbed; the same impl AE that fails a low-noise
    # section passes a high-noise one.
    low_noise = _verdict(
        impl_dssim=0.05, ref_self_dssim=0.10,
        impl_ae_per_mpx=9000.0, ref_self_ae_per_mpx=500.0,   # ceiling 500*2.5+2000=3250
    )
    high_noise = _verdict(
        impl_dssim=0.05, ref_self_dssim=0.10,
        impl_ae_per_mpx=9000.0, ref_self_ae_per_mpx=4000.0,  # ceiling 4000*2.5+2000=12000
    )
    assert low_noise[0] == "fail", low_noise
    assert high_noise[0] == "pass", high_noise


def test_dynamic_ae_ceiling_helper() -> None:
    from ui_clone.section_dynamic import (
        DYNAMIC_AE_CEILING_ABS_DEFAULT,
        dynamic_ae_ceiling,
    )
    # derived from ref self-AE
    assert dynamic_ae_ceiling(1000.0, mult=2.5, eps=2000.0) == 4500.0
    # absolute fallback when ref self-AE unknown
    assert dynamic_ae_ceiling(None) == DYNAMIC_AE_CEILING_ABS_DEFAULT


# ── Commit 2: motion shift-search parity verdict ────────────────────────────


def _mp(**over: Any) -> tuple[str, str]:
    """Faithful scroll-reveal baseline: AE collapses hard under shift, the
    shifted pair is pixel-tight, no localized defect, ref has variance."""
    kw: dict[str, Any] = dict(
        ae_zero=22000.0, ae_shift_min=900.0, shifted_dssim=0.008,
        ref_has_variance=True, localized_defect=False,
    )
    kw.update(over)
    return motion_phase_verdict(**kw)


# ── faithful phase-shift collapses AE under shift -> passes ──────────────────
def test_motion_identical_content_at_offset_passes() -> None:
    status, reason = _mp()
    assert status == "pass", reason
    assert "collapsed" in reason


def test_motion_collapse_just_over_floor_passes() -> None:
    # collapse = 1 - 0.84 = 0.16 >= 0.15 default
    assert _mp(ae_zero=10000.0, ae_shift_min=8400.0, shifted_dssim=0.010)[0] == "pass"


# ── HARD CONSTRAINT: wrong content at EVERY offset fails (no collapse) ───────
def test_motion_wrong_content_every_offset_fails() -> None:
    # best offset barely moves AE: collapse 0.0 -> not a phase, wrong content.
    status, reason = _mp(ae_zero=6342.0, ae_shift_min=6342.0, shifted_dssim=0.257)
    assert status == "fail"
    assert "did not collapse" in reason


def test_motion_partial_collapse_below_floor_fails() -> None:
    # collapse = 1 - 0.90 = 0.10 < 0.15 -> insufficient, fails.
    assert _mp(ae_zero=10000.0, ae_shift_min=9000.0, shifted_dssim=0.005)[0] == "fail"


# ── shifted-but-localized-defect fails even with a global collapse ──────────
def test_motion_shifted_but_localized_defect_fails() -> None:
    status, reason = _mp(localized_defect=True)
    assert status == "fail"
    assert "localized" in reason


# ── low structure fails even if raw AE collapsed at an extreme offset ───────
def test_motion_low_structure_fails() -> None:
    # AE collapsed (some lucky offset) but shifted structure 1-0.20=0.80 < 0.85.
    status, reason = _mp(ae_shift_min=500.0, shifted_dssim=0.20)
    assert status == "fail"
    # G2 (tightness 0.015) trips first; both are fails, assert it is structural-class.
    assert "dssim" in reason or "structure" in reason


def test_motion_shifted_dssim_not_tight_fails() -> None:
    # collapsed AND structure>=0.85 (1-0.05=0.95) but dssim 0.05 > 0.015 tightness.
    status, reason = _mp(ae_shift_min=500.0, shifted_dssim=0.05)
    assert status == "fail"
    assert "pixel-tight" in reason or "dssim" in reason


def test_motion_structure_floor_g3_is_the_binding_guard() -> None:
    # G3 (structure floor) is unreachable under default thresholds because G2
    # (tightness 0.015) trips first. Loosen G2 so G3 binds: sd=0.20 passes G2
    # (<= 0.30) but structure 1-0.20=0.80 < 0.85 -> G3 fails. Proves G3 is a real
    # independent backstop, not dead code.
    status, reason = _mp(ae_shift_min=500.0, shifted_dssim=0.20, shifted_dssim_max=0.30)
    assert status == "fail"
    assert "structure" in reason or "scrambled" in reason


# ── blank / no-variance ref fails ───────────────────────────────────────────
def test_motion_blank_ref_fails() -> None:
    status, reason = _mp(ref_has_variance=False)
    assert status == "fail"
    assert "variance" in reason


def test_motion_zero_ae_baseline_fails() -> None:
    # AE@0 below the noise floor: nothing to realign, not a motion phase.
    status, reason = _mp(ae_zero=0.5, ae_shift_min=0.0)
    assert status == "fail"
    assert "noise floor" in reason


def test_motion_missing_measurements_fail() -> None:
    assert _mp(ae_zero=None)[0] == "fail"
    assert _mp(ae_shift_min=None)[0] == "fail"
    assert _mp(shifted_dssim=None)[0] == "fail"
