"""Phase-invariant distribution-level splash SSIM calibration (batch-4 item 1).

The splash frame-SSIM table false-fails the reference site against ITSELF:
a food-arc intro records at ~12fps continuous motion, so two INDEPENDENT
recordings of the same splash land mid-flight food frames at DIFFERENT phases.
Frame-aligned SSIM then compares two random phases and bottoms out at 0.5-0.9
even ref-vs-ref.

A per-frame ref-vs-ref noise floor cannot rescue it: the phase is random per
recording-pair, so ref-vs-refcal[k] is a different random phase than
impl-vs-ref[k] (uncorrelated). The phase-invariant property is the DISTRIBUTION:
an impl is faithful iff its SSIM distribution over the aligned window is no worse
than a second independent recording of the reference.

Which STATISTICS, though, is the crux — and the live data decided it. Over five
ref-vs-ref runs the MEDIAN (p50) and p75 were rock-stable (gaps within ±0.02 and
±0.001), while failRate, p05, p10 and p25 swung by up to ±0.27. The reason is
structural: the median measures STRUCTURAL fidelity (most frames are
well-aligned and near-identical — robust to the phase lottery), while the deep
tail measures PHASE alignment (pure recording-pair noise). So the verdict gates
on the high, stable percentiles (p50 + p75) with TIGHT margins — a faithful
clone keeps a high median/p75 (most frames match), a genuinely different splash
does not. The noisy tail (failRate / p05 / p10 / min) is kept as evidence only.
Anti-cheat: the SSIM threshold (0.90) is never widened, and a wrong TIMELINE is
caught separately by the arc-timing verdict.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ui_clone import splash_distribution as sd

REPO = Path(__file__).resolve().parents[1]


# ── percentile (pure, stdlib) ────────────────────────────────────────────────


def test_percentile_linear_interpolation() -> None:
    vals = [float(i) for i in range(11)]  # 0..10
    assert sd.percentile(vals, 0) == 0.0
    assert sd.percentile(vals, 100) == 10.0
    assert sd.percentile(vals, 50) == 5.0
    assert sd.percentile(vals, 75) == 7.5


def test_percentile_interpolates_between_samples() -> None:
    # rank = 0.25 * (4-1) = 0.75 -> 0*0.25 + 1*0.75 = 0.75
    assert sd.percentile([0.0, 1.0, 2.0, 3.0], 25) == pytest.approx(0.75)


def test_percentile_empty_is_zero() -> None:
    assert sd.percentile([], 50) == 0.0


def test_percentile_single_value() -> None:
    assert sd.percentile([0.93], 50) == 0.93


# ── summarize ────────────────────────────────────────────────────────────────


def test_summarize_failrate_and_stats() -> None:
    series = [0.95] * 80 + [0.70] * 20
    s = sd.summarize(series, 0.90)
    assert s["n"] == 100
    assert s["failRate"] == pytest.approx(0.20)
    assert s["minSsim"] == pytest.approx(0.70)
    assert s["p50"] == pytest.approx(0.95)
    assert s["p75"] == pytest.approx(0.95)
    # tail stats present as evidence
    assert s["p05"] == pytest.approx(0.70)
    assert s["p10"] == pytest.approx(0.70)
    assert s["p25"] == pytest.approx(0.95)


def test_summarize_empty_series() -> None:
    s = sd.summarize([], 0.90)
    assert s["n"] == 0
    assert s["failRate"] == 0.0


# ── compare_series: faithful vs degraded (gated on p50 + p75) ────────────────


def _noisy_ref() -> list[float]:
    # phase-noisy food-arc: ~20% of frames below 0.90, lows ~0.70, but the
    # median is high (most frames well-aligned and near-identical).
    return [0.99] * 60 + [0.97] * 20 + [0.72] * 20


def test_faithful_impl_passes_by_distribution() -> None:
    impl = [0.99] * 58 + [0.97] * 20 + [0.70] * 22  # noisier tail, same median
    out = sd.compare_series(impl, _noisy_ref())
    assert out["engaged"] is True
    assert out["passed"] is True


def test_identical_series_passes() -> None:
    ref = _noisy_ref()
    out = sd.compare_series(list(ref), list(ref))
    assert out["engaged"] is True
    assert out["passed"] is True


def test_worse_tail_only_passes() -> None:
    """The live false-fail class (run 3): the impl matches the ref on the stable
    median + p75 but has a much noisier DEEP TAIL (lower p05/p10). The tail is
    the per-recording-pair phase lottery, not a defect, so the impl must PASS —
    gating on the noisy tail false-fails a faithful clone."""
    # same p50/p75 as ref, but a far deeper/heavier tail
    impl = [0.99] * 60 + [0.97] * 20 + [0.40] * 20
    out = sd.compare_series(impl, _noisy_ref())
    assert out["impl"]["p05"] < out["ref"]["p05"] - 0.10, "tail IS worse"
    assert out["impl"]["failRate"] >= out["ref"]["failRate"], "more fails"
    assert out["checks"]["p50"] is True and out["checks"]["p75"] is True
    assert out["passed"] is True


def test_lower_median_fails() -> None:
    """A genuinely different splash drops the MEDIAN (every frame carries the
    structural difference) — that is the real-defect signal, and it fails."""
    impl = [0.70] * 60 + [0.65] * 40  # median ~0.70, far below ref ~0.99
    out = sd.compare_series(impl, _noisy_ref())
    assert out["checks"]["p50"] is False
    assert out["passed"] is False


def test_bad_impl_catastrophic_fails() -> None:
    impl = [0.10] * 100  # different site entirely
    out = sd.compare_series(impl, _noisy_ref())
    assert out["passed"] is False


def test_lower_p75_fails() -> None:
    """p75 is the rock-stable 'at least a quarter of frames are near-perfect
    matches' check; a different splash has few near-perfect frames and fails."""
    impl = [0.85] * 100  # nothing near-perfect: p75 ~0.85 vs ref ~0.99
    out = sd.compare_series(impl, _noisy_ref())
    assert out["checks"]["p75"] is False
    assert out["passed"] is False


# ── engagement uses max(impl, ref) failRate ──────────────────────────────────


def test_engages_when_impl_noisy_even_if_refcal_clean() -> None:
    """Live false-fail (run 3): the refcal pair happened to align cleanly
    (failRate 0.049 < floor) while the impl pair was noisy. Engagement must key
    on EITHER pair being noisy, else a fine impl false-fails on the raw verdict."""
    ref = [0.99] * 96 + [0.80] * 4  # failRate 0.04 — below floor on its own
    impl = [0.99] * 80 + [0.80] * 20  # failRate 0.20 — the splash IS phase-noisy
    out = sd.compare_series(impl, ref)
    assert out["engaged"] is True
    assert out["passed"] is True  # p50 matches


def test_engagement_deterministic_across_old_floor_straddle() -> None:
    """batch-12 ITEM 5 (determinism): a phase-noisy splash whose per-run failRate
    STRADDLES the old 0.05 floor must give the SAME verdict run-to-run. Two
    faithful captures of the same impl+ref — one quiet (failRate ~0.04), one
    noisier (~0.06) — must BOTH engage and PASS. Under the old 0.05 floor the
    quiet run did not engage (the strict per-frame FAIL stood) while the noisy run
    passed by distribution — the same impl, opposite verdict (the e2e-12
    intermittency). The floor is now 0: any phase noise engages, so the outcome no
    longer flips at 0.05."""
    impl_quiet = [0.99] * 80 + [0.97] * 16 + [0.80] * 4   # failRate 0.04
    ref_quiet = [0.99] * 80 + [0.97] * 16 + [0.80] * 4
    impl_noisy = [0.99] * 80 + [0.97] * 14 + [0.80] * 6   # failRate 0.06
    ref_noisy = [0.99] * 80 + [0.97] * 14 + [0.80] * 6
    out_q = sd.compare_series(impl_quiet, ref_quiet)
    out_n = sd.compare_series(impl_noisy, ref_noisy)
    assert out_q["engaged"] is True, "a quiet-phase run must still engage (any phase noise)"
    assert out_n["engaged"] is True
    assert out_q["passed"] is True and out_n["passed"] is True
    assert out_q["passed"] == out_n["passed"], "verdict must not flip across the old 0.05 floor"
    # documents the bug: under the OLD 0.05 floor the quiet run did NOT engage.
    assert sd.compare_series(impl_quiet, ref_quiet, ref_failrate_floor=0.05)["engaged"] is False


def test_clean_splash_still_defers_to_strict() -> None:
    """The floor-0 change must NOT make a PERFECTLY clean splash engage: failRate
    exactly 0 on both sides defers to the strict per-frame verdict (unchanged)."""
    out = sd.compare_series([0.99] * 100, [0.99] * 100)
    assert out["engaged"] is False
    assert out["passed"] is False


# ── 3-series evaluation: best-of-two references + suspect detection ──────────


def test_evaluate_three_picks_better_aligned_reference() -> None:
    """Phase lottery: the impl aligns badly with ref but well with refcal (both
    are valid reference captures). The impl matches a reference recording, so it
    is faithful — pick the better-aligned pair."""
    s_ir = [0.70] * 100  # impl vs ref: badly aligned
    s_ic = [0.99] * 100  # impl vs refcal: well aligned
    s_rc = [0.99] * 80 + [0.80] * 20  # ref vs refcal: consistent baseline
    out = sd.evaluate_three(s_ir, s_ic, s_rc)
    assert out["passed"] is True
    assert out["suspect"] is False


def test_evaluate_three_flags_suspect_impl_recording() -> None:
    """Run-2 class: the baseline is consistent (ref ~= refcal, high median) but
    the impl recording matches NEITHER reference — the impl capture is
    unreliable (live-site load variance), flagged suspect for a bounded
    re-record rather than passed."""
    s_ir = [0.75] * 100  # impl vs ref: low
    s_ic = [0.75] * 100  # impl vs refcal: also low
    s_rc = [0.99] * 90 + [0.80] * 10  # ref vs refcal: consistent, high median
    out = sd.evaluate_three(s_ir, s_ic, s_rc)
    assert out["passed"] is False
    assert out["suspect"] is True


def test_evaluate_three_real_defect_not_suspect_when_baseline_inconsistent() -> None:
    """If the baseline itself is noisy (ref != refcal), a low impl is judged
    against the lower baseline, not blamed as a suspect capture."""
    s_ir = [0.99] * 100
    s_ic = [0.99] * 100
    s_rc = [0.70] * 100  # baseline inconsistent (low) — not a clean ref-vs-ref
    out = sd.evaluate_three(s_ir, s_ic, s_rc)
    assert out["suspect"] is False
    assert out["passed"] is True  # impl beats the noisy baseline


def test_evaluate_three_faithful_passes_not_suspect() -> None:
    s_ir = [0.99] * 80 + [0.80] * 20
    s_ic = [0.99] * 78 + [0.80] * 22
    s_rc = [0.99] * 80 + [0.80] * 20
    out = sd.evaluate_three(s_ir, s_ic, s_rc)
    assert out["passed"] is True
    assert out["suspect"] is False


# ── anti-cheat: clean ref-vs-ref must NOT engage the calibration ─────────────


def test_clean_splash_faithful_impl_does_not_engage() -> None:
    """A clean-splash site with a FAITHFUL impl (both pairs pristine) keeps the
    strict per-frame verdict — the distribution path engages only for the
    phase-noisy content class."""
    clean_ref = [0.99] * 100  # failRate 0
    faithful_impl = [0.99] * 100  # failRate 0 — not phase-noisy
    out = sd.compare_series(faithful_impl, clean_ref)
    assert out["engaged"] is False
    assert out["passed"] is False


def test_clean_splash_structural_defect_engages_but_fails() -> None:
    """A clean-splash site with a STRUCTURAL impl defect engages (the impl is
    noisy) and fails the tight p75 gate — the defect is caught, not masked."""
    clean_ref = [0.99] * 100
    impl = [0.96] * 80 + [0.50] * 20  # 20% deeply divergent — structural
    out = sd.compare_series(impl, clean_ref)
    assert out["engaged"] is True
    assert out["passed"] is False
    assert out["checks"]["p75"] is False


def test_empty_impl_series_no_pass() -> None:
    out = sd.compare_series([], _noisy_ref())
    assert out["engaged"] is False
    assert out["passed"] is False


def test_empty_ref_series_no_pass() -> None:
    out = sd.compare_series(_noisy_ref(), [])
    assert out["engaged"] is False
    assert out["passed"] is False


# ── margins are tunable (no site-specific constant baked in) ─────────────────


def test_p50_margin_is_tunable() -> None:
    # impl median 0.90 vs ref ~0.99 -> gap ~0.09
    impl = [0.90] * 60 + [0.97] * 20 + [0.72] * 20
    assert sd.compare_series(impl, _noisy_ref())["checks"]["p50"] is False  # default 0.05
    out = sd.compare_series(impl, _noisy_ref(), p50_margin=0.15)
    assert out["checks"]["p50"] is True


def test_ref_failrate_floor_is_tunable() -> None:
    ref = [0.99] * 92 + [0.72] * 8  # failRate 0.08
    impl = [0.99] * 92 + [0.72] * 8
    assert sd.compare_series(impl, ref)["engaged"] is True
    assert sd.compare_series(impl, ref, ref_failrate_floor=0.10)["engaged"] is False


# ── CLI: reads two series files, exit 0 iff engaged AND passed ───────────────


def _write_series(path: Path, vals: list[float]) -> None:
    path.write_text("\n".join(f"{v}" for v in vals) + "\n", encoding="utf-8")


def _run_cli(
    impl_file: Path, ref_file: Path, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "ui_clone.splash_distribution", str(impl_file), str(ref_file)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_cli_faithful_exit_zero(tmp_path: Path) -> None:
    impl = tmp_path / "impl.txt"
    ref = tmp_path / "ref.txt"
    _write_series(impl, [0.99] * 60 + [0.97] * 20 + [0.40] * 20)  # worse tail only
    _write_series(ref, _noisy_ref())
    r = _run_cli(impl, ref)
    assert r.returncode == 0, r.stdout + r.stderr
    payload = json.loads(r.stdout)
    assert payload["engaged"] is True and payload["passed"] is True


def test_cli_degraded_exit_nonzero(tmp_path: Path) -> None:
    impl = tmp_path / "impl.txt"
    ref = tmp_path / "ref.txt"
    _write_series(impl, [0.70] * 60 + [0.65] * 40)  # lower median
    _write_series(ref, _noisy_ref())
    r = _run_cli(impl, ref)
    assert r.returncode != 0, r.stdout + r.stderr
    payload = json.loads(r.stdout)
    assert payload["passed"] is False


def test_cli_clean_faithful_exit_nonzero(tmp_path: Path) -> None:
    # clean splash + faithful impl: not phase-noisy -> not engaged -> strict
    # verdict stands (exit nonzero; the caller keeps its per-frame result).
    impl = tmp_path / "impl.txt"
    ref = tmp_path / "ref.txt"
    _write_series(impl, [0.99] * 100)
    _write_series(ref, [0.99] * 100)
    r = _run_cli(impl, ref)
    assert r.returncode != 0
    payload = json.loads(r.stdout)
    assert payload["engaged"] is False
