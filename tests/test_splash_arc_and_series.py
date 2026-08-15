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

import re
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


def test_arc_calibrated_rejects_extreme_refcal_noise() -> None:
    # Realfood-observed class: ref arc 156, impl arc 48, refcal arc 383.
    # The huge 227-frame ref-vs-refcal noise is not a usable calibration floor.
    r = _arc_cal("1 157 500 1 49 500 1 384 500 18 20")
    assert r.returncode != 0, r.stdout + r.stderr


def test_arc_calibrated_rejects_degenerate_refcal_nearer_false_pass() -> None:
    # Fable-observed degenerate class: ref arc 156, impl arc 8, refcal arc 4.
    # Impl is near the broken refcal arc, but ref/refcal disagree too much for
    # the refcal-nearer branch to be trusted.
    r = _arc_cal("1 157 500 1 9 500 1 5 500 18 20")
    assert r.returncode != 0, r.stdout + r.stderr


def test_arc_calibrated_rejects_short_ref_zero_refcal_false_pass() -> None:
    # Fable-observed short-ref class: ref arc 50, impl arc 5, refcal arc 0.
    # A zero-motion refcal must not become the nearer reference.
    r = _arc_cal("1 51 500 1 6 500 1 1 500 18 20")
    assert r.returncode != 0, r.stdout + r.stderr


def test_arc_calibrated_late_refcal_cannot_clamp_too_long_impl_to_pass() -> None:
    # A late refcal first-change leaves only 50 common frames. The direct
    # ref-vs-impl comparison must still see raw arcs 50 vs 300, not both clamp
    # to the late refcal's remaining budget.
    r = _arc_cal("1 51 500 1 301 500 450 500 500 18 20")
    assert r.returncode != 0, r.stdout + r.stderr


def test_arc_calibrated_extreme_noise_stops_at_ordinary_tolerance_boundary() -> None:
    # Ordinary tolerance is defmax + margin = 38. Extreme refcal noise must not
    # widen that final boundary a second time.
    at_boundary = _arc_cal("1 157 500 1 195 500 1 384 500 18 20")
    beyond_boundary = _arc_cal("1 157 500 1 196 500 1 384 500 18 20")
    assert at_boundary.returncode == 0, at_boundary.stdout + at_boundary.stderr
    assert beyond_boundary.returncode != 0, beyond_boundary.stdout + beyond_boundary.stderr


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


def test_calibratable_arc_mismatch_is_not_emitted_as_a_final_failure() -> None:
    """A strict splash mismatch is provisional until refcal re-verdicts it.

    ``video-motion-compare.sh`` copies the comparator's stdout verbatim into
    ``video-motion-result.txt``.  A calibrated run can therefore end in
    ``ALL PASS`` while an earlier ``❌``/``FAIL`` diagnostic still makes the
    text-artifact gate reject it.  The provisional branch must keep its arc
    metrics without using final-failure tokens; non-calibratable and calibrated
    failures remain authoritative later in the script.
    """
    body = SCRIPT.read_text(encoding="utf-8")
    strict_arc = body.split("  ARC_VERDICT_FAIL=0", 1)[1].split("  # Phase-jitter allowance", 1)[0]
    calibration_entry = body.split('  if [[ "$SPLASH_CAL_ELIGIBLE" -eq 1', 1)[1].split(
        "    _VTC_REPO_ROOT=", 1
    )[0]
    final_verdict = body.split("# ── Phase 5: Output results ──", 1)[1]

    assert "ARC_VERDICT_OUTPUT" in strict_arc
    assert "⚠ provisional arc timing:" in strict_arc
    assert "strict comparison pending calibration" in calibration_entry
    assert "arc=FAIL" not in calibration_entry
    assert "strict verdict failed" not in calibration_entry
    assert 'echo -e "${RED}${FAIL} FAIL' in final_verdict
    assert "exit 1" in final_verdict


def test_calibrated_pass_neutralizes_only_provisional_strict_ssim_rows() -> None:
    """The nested result artifact must agree with its final calibrated tally."""
    body = SCRIPT.read_text(encoding="utf-8")
    calibration_outcome = body.split("    # Recombine FAIL", 1)[1].split(
        "    # Suspect impl recording", 1
    )[0]
    match = re.search(
        r'^    if \[\[ "\$FAIL" -eq 0 \]\]; then\n.*?^    fi$',
        calibration_outcome,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match, "a fully calibrated pass must finalize provisional SSIM rows"
    finalizer = match.group(0)

    provisional_row = r"| f-000001 | 0.876471 | ❌ |\n"
    passed = _bash(
        f"RESULTS='{provisional_row}'; FAIL=0\n"
        f"{finalizer}\n"
        "printf '%b' \"$RESULTS\""
    )
    assert passed.returncode == 0, passed.stdout + passed.stderr
    assert "0.876471" in passed.stdout
    assert "⚠ provisional strict SSIM" in passed.stdout
    assert "❌" not in passed.stdout

    failed = _bash(
        f"RESULTS='{provisional_row}'; FAIL=1\n"
        f"{finalizer}\n"
        "printf '%b' \"$RESULTS\""
    )
    assert failed.returncode == 0, failed.stdout + failed.stderr
    assert "❌" in failed.stdout, "a true final failure must retain its strict row marker"
