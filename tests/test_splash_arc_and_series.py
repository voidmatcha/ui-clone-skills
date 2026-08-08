"""Splash distribution-calibration bash helpers (batch-4 item 1).

Three new sourceable helpers in scripts/verify/lib/frame-align.sh support the
distribution-level splash redesign:

  arc_common_budget  — symmetric per-side arc clamp. The old looping-video
    clamp bounded BOTH last-changes to one absolute cutoff (min total frames),
    which truncated the side with the LATER first-change more. Equal real arcs
    with different lead-ins then false-failed. The fix bounds each side's arc
    to a COMMON budget measured from ITS OWN first-change.

  _best_frame_ssim   — per-frame SSIM with ±jitter neighbor compensation,
    factored out of the main loop so the ref-vs-refcal series reuses identical
    logic.

  compute_ssim_series — emits one best-SSIM per aligned frame; builds the
    S_impl / S_ref series the pure distribution comparator consumes.

Plus script-wiring locks for the media mask + distribution path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ALIGN_LIB = REPO / "scripts" / "verify" / "lib" / "frame-align.sh"
SCRIPT = REPO / "scripts" / "verify" / "video-transition-compare.sh"


def _bash(snippet: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", snippet], capture_output=True, text=True, timeout=120
    )


def _make_frames(d: Path, colors: list[str]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(colors, start=1):
        subprocess.run(
            ["magick", "-size", "128x128", f"xc:{c}", str(d / f"f-{i:06d}.png")],
            check=True,
            capture_output=True,
        )


# ── arc_common_budget (symmetric per-side clamp) ─────────────────────────────


def test_arc_common_budget_is_min_per_side_remaining() -> None:
    # ref remaining 486-61=425, impl remaining 390-60=330 -> 330
    r = _bash(f'source "{ALIGN_LIB}"; arc_common_budget 61 486 60 390')
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == "330"


def test_symmetric_clamp_passes_equal_arcs_with_different_leadins() -> None:
    """ref fc130/lc440 (arc 310) and impl fc40/lc350 (arc 310) have EQUAL real
    arcs but very different lead-ins. The old absolute clamp to
    min(total)=360 would truncate ref to arc 230 vs impl 310 (delta 80 FAIL);
    the symmetric per-side budget clamps each from its own first-change and the
    arcs stay equal."""
    r = _bash(
        f'source "{ALIGN_LIB}"; '
        f"B=$(arc_common_budget 130 450 40 360); "
        f'RL=$(clamp_arc_last 130 440 $((130 + B))); '
        f'IL=$(clamp_arc_last 40 350 $((40 + B))); '
        f'arc_timing_verdict 130 "$RL" 40 "$IL" 18'
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_symmetric_clamp_still_fails_wrong_timeline() -> None:
    """A genuinely-different timeline (impl arc much shorter) still fails after
    symmetric clamping — the clamp normalizes lead-in, not arc shape."""
    # ref fc60/lc460 (arc 400), impl fc60/lc200 (arc 140)
    r = _bash(
        f'source "{ALIGN_LIB}"; '
        f"B=$(arc_common_budget 60 480 60 480); "
        f'RL=$(clamp_arc_last 60 460 $((60 + B))); '
        f'IL=$(clamp_arc_last 60 200 $((60 + B))); '
        f'arc_timing_verdict 60 "$RL" 60 "$IL" 18'
    )
    assert r.returncode != 0, r.stdout + r.stderr


# ── arc_calibrated_verdict (live ref-vs-ref arc noise floor) ─────────────────
#
# Measured live: the cold ref recording (loaded 1st) over-detects its last
# change by up to ~40 frames vs the warm impl/refcal recordings (loaded 2nd/3rd
# off a shared cache) on a ~12fps continuous-motion splash. ref-vs-ref arc
# deltas across 5 runs: 0,0,5,29,41 — the static max 18 false-fails 2/5. The
# verdict grounds the tolerance in the live |ref-arc − refcal-arc| noise and
# compares the impl arc to the NEARER of {ref, refcal} (impl matches whichever
# ref recording shares its load state). One-side-no-motion stays a HARD fail.


def _arc_cal(args: str) -> subprocess.CompletedProcess[str]:
    return _bash(f'source "{ALIGN_LIB}"; arc_calibrated_verdict {args}')


def test_arc_calibrated_passes_cold_ref_warm_impl(tmp_path: Path) -> None:
    # run-4 class: ref arc 371 (cold), impl 330 (warm), refcal 330 (warm).
    # impl matches the warm refcal -> pass despite |impl-ref|=41 > static 18.
    # args: ref_fc ref_lc ref_total impl_fc impl_lc impl_total cal_fc cal_lc cal_total defmax margin
    r = _arc_cal("1 372 500 1 331 500 1 331 500 18 20")
    assert r.returncode == 0, r.stdout + r.stderr


def test_arc_calibrated_fails_wrong_timeline(tmp_path: Path) -> None:
    # impl splash 2x too long (arc 660) vs ref/refcal 330 -> matches neither.
    r = _arc_cal("1 331 800 1 661 800 1 331 800 18 20")
    assert r.returncode != 0, r.stdout + r.stderr


def test_arc_calibrated_fails_impl_no_motion(tmp_path: Path) -> None:
    # bad-impl (blank/different page): impl has no detected arc -> hard fail,
    # the live noise floor must never rescue a missing splash.
    r = _arc_cal("1 331 500 1 1 500 1 331 500 18 20")
    assert r.returncode != 0, r.stdout + r.stderr


def test_arc_calibrated_passes_small_real_jitter(tmp_path: Path) -> None:
    r = _arc_cal("1 331 500 1 333 500 1 330 500 18 20")
    assert r.returncode == 0, r.stdout + r.stderr


# ── _best_frame_ssim ─────────────────────────────────────────────────────────


def test_best_frame_ssim_identical_is_high(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    _make_frames(a, ["red"])
    _make_frames(b, ["red"])
    r = _bash(f'source "{ALIGN_LIB}"; _best_frame_ssim "{a}/f-000001.png" "{b}" 1 0 0.90')
    assert r.returncode == 0, r.stdout + r.stderr
    assert float(r.stdout.strip()) >= 0.99


def test_best_frame_ssim_missing_cmp_frame_is_empty(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    _make_frames(a, ["red"])
    b.mkdir()
    r = _bash(f'source "{ALIGN_LIB}"; _best_frame_ssim "{a}/f-000001.png" "{b}" 1 0 0.90')
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == ""


def test_best_frame_ssim_jitter_finds_better_neighbor(tmp_path: Path) -> None:
    """A 1-frame phase offset: the primary pair mismatches but the ±1 neighbor
    matches. With jitter=1 the helper returns the high neighbor SSIM."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    _make_frames(a, ["red"])  # ref frame 1 = red
    _make_frames(b, ["blue", "red"])  # cmp frame 1 = blue (mismatch), frame 2 = red
    # base index 1 mismatches (blue vs red); jitter 1 scans index 2 (red) -> high
    r = _bash(f'source "{ALIGN_LIB}"; _best_frame_ssim "{a}/f-000001.png" "{b}" 1 1 0.90')
    assert r.returncode == 0, r.stdout + r.stderr
    assert float(r.stdout.strip()) >= 0.99


# ── compute_ssim_series ──────────────────────────────────────────────────────


def test_compute_ssim_series_one_per_frame(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    cmp = tmp_path / "cmp"
    _make_frames(ref, ["red", "green", "blue"])
    _make_frames(cmp, ["red", "green", "blue"])
    r = _bash(f'source "{ALIGN_LIB}"; compute_ssim_series "{ref}" 0 "{cmp}" 0 3 0 0.90')
    assert r.returncode == 0, r.stdout + r.stderr
    lines = [ln for ln in r.stdout.strip().splitlines() if ln]
    assert len(lines) == 3
    assert all(float(x) >= 0.99 for x in lines)


# ── script wiring locks ──────────────────────────────────────────────────────


def test_script_wires_distribution_calibration() -> None:
    body = SCRIPT.read_text(encoding="utf-8")
    assert "splash_distribution" in body, "splash mode must call the distribution comparator"
    assert "UI_CLONE_VMC_SPLASH_CALIBRATE" in body, "distribution path must be opt-out"
    # the threshold is never widened by this change
    assert 'SSIM_THRESHOLD="${SSIM_THRESHOLD:-0.90}"' in body


def test_script_masks_media_in_splash_record() -> None:
    body = SCRIPT.read_text(encoding="utf-8")
    assert "_splash_mask_media" in body, "splash recording must mask playing video/canvas"
    assert "UI_CLONE_VMC_SPLASH_MASK_SELECTORS" in body, "mask selectors must be env-tunable"
    assert "visibility" in body.lower()


def test_script_gates_distribution_on_arc_timing() -> None:
    """A wrong timeline must fail on arc and never reach the SSIM calibration."""
    body = SCRIPT.read_text(encoding="utf-8")
    assert "ARC_VERDICT_FAIL" in body, "distribution path must be gated on arc timing passing"
