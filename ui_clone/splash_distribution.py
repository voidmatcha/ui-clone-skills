"""Phase-invariant distribution-level splash SSIM calibration (batch-4 item 1).

The splash branch of video-motion-compare false-fails the reference site against
ITSELF. Root cause: a splash whose effective content frame-rate is ~12fps with
continuous motion (e.g. a food-arc intro where images sweep along an arc)
records mid-flight frames at an uncontrolled phase. Two INDEPENDENT recordings
of the SAME splash land those frames at DIFFERENT phases, so frame-aligned SSIM
compares two random phases of the same motion and bottoms out at 0.5-0.9 even
ref-vs-ref.

A per-frame ref-vs-ref noise floor (the move that works for the phase-free,
position-aligned scroll mode) CANNOT rescue splash: the phase is random per
recording-pair, so at aligned frame k, ref-vs-refcal[k] is a DIFFERENT random
phase than impl-vs-ref[k] — the two are uncorrelated (measured frame 132:
impl-vs-ref 0.748 while ref-vs-refcal 0.82-0.96). Comparing them per-frame is
noise-vs-noise.

The phase-invariant property is the DISTRIBUTION. An impl is faithful iff its
splash SSIM distribution over the aligned window is NO WORSE than a second
independent recording of the reference (refcal). Both impl-vs-ref and
ref-vs-refcal are "two independent recordings", so their distributions MATCH
when the impl is faithful and DIVERGE when it is not — regardless of where any
individual low frame lands.

Anti-cheat (the whole point):
  - The SSIM threshold is NEVER widened. It stays at SSIM_THRESHOLD (0.90).
  - The only allowance is "impl distribution no worse than a live ref-vs-ref
    distribution measured THIS run" — a grounded baseline, not a constant.
  - A genuinely wrong impl has a materially worse distribution (lower
    percentiles, higher fail-rate) and stays FAILED.
  - The calibration engages whenever the splash shows ANY phase noise (the
    ref-vs-ref failRate floor is 0 for run-to-run determinism — batch-12 ITEM 5).
    A perfectly clean splash keeps the strict per-frame verdict; the distribution
    path is for the phase-noisy content class, and the p50/p75 gate (never the
    engagement binary, which a straddling failRate would flip) is the anti-cheat.

This module is the PURE comparator. The bash splash path (video-transition-
compare.sh) records the third reference, builds the two SSIM series, and calls
this via the CLI:

    SSIM_THRESHOLD=0.90 python3 -m ui_clone.splash_distribution <impl.txt> <ref.txt>
        -> prints the verdict JSON; exit 0 iff the calibration ENGAGED and the
           impl distribution PASSED, else exit 1 (strict per-frame FAIL stands).

All tunables are env-overridable and have conservative defaults — there are no
site-specific constants here.
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Any

# Conservative defaults. Tunable per-run via the env vars read in main().
THRESHOLD = 0.90
# The verdict gates on the HIGH, STABLE percentiles — p50 (median) and p75 —
# NOT on failRate / p05 / p10 / min. Measured live over five ref-vs-ref runs:
# the median and p75 gaps stayed within ±0.02 and ±0.001, while failRate and the
# deep tail swung by up to ±0.27. The median measures STRUCTURAL fidelity (most
# frames are well-aligned and near-identical — robust to the phase lottery); the
# deep tail measures PHASE alignment (per-recording-pair noise). A faithful clone
# keeps a high median/p75; a genuinely different splash drops them. Tight margins
# on the stable statistics give strong anti-cheat AND a clean ref-vs-ref pass.
P50_MARGIN = 0.05  # impl median may sit at most this far below ref's median
P75_MARGIN = 0.02  # impl p75 may sit at most this far below ref's p75 (rock-stable)
# batch-12 ITEM 5 (determinism): engage on ANY phase noise (a single sub-threshold
# frame), not above a 0.05 floor. A phase-noisy splash's per-run failRate STRADDLES
# 0.05, so the old floor flipped the verdict run-to-run (a quiet-phase run did not
# engage -> the strict per-frame FAIL stood; a noisier run engaged -> distribution
# PASS). With the floor at 0 a faithful impl resolves to the SAME outcome every run
# -- PASS via strict-clean (failRate exactly 0) OR via the distribution path (any
# noise) -- while the p50/p75 gate stays the anti-cheat (a genuinely different
# splash drops the stable percentiles and still fails). A perfectly clean splash
# (failRate 0) defers to the strict verdict, unchanged.
REF_FAILRATE_FLOOR = 0.0  # any phase noise engages; only a perfectly clean splash defers to strict


def percentile(values: list[float], q: float) -> float:
    """The q-th percentile (q in [0, 100]) with linear interpolation between
    the two nearest ranks — the numpy default. Empty -> 0.0."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (q / 100.0) * (len(ordered) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return ordered[int(rank)]
    frac = rank - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def summarize(series: list[float], threshold: float) -> dict[str, Any]:
    """Reduce an SSIM series to phase-invariant summary statistics: how MANY
    frames fail and how LOW they go — never WHERE they land."""
    n = len(series)
    if n == 0:
        return {
            "n": 0, "failRate": 0.0, "p05": 0.0, "p10": 0.0,
            "p25": 0.0, "p50": 0.0, "p75": 0.0, "minSsim": 0.0,
        }
    fails = sum(1 for v in series if v < threshold)
    return {
        "n": n,
        "failRate": fails / n,  # evidence + engagement signal (NOT a gate)
        "p05": percentile(series, 5),  # evidence: deep tail (phase noise)
        "p10": percentile(series, 10),  # evidence
        "p25": percentile(series, 25),  # evidence
        "p50": percentile(series, 50),  # GATED: structural fidelity (stable)
        "p75": percentile(series, 75),  # GATED: near-perfect-match fraction (stable)
        "minSsim": min(series),  # evidence: single-frame phase lottery
    }


def compare_series(
    impl: list[float],
    ref: list[float],
    *,
    threshold: float = THRESHOLD,
    p50_margin: float = P50_MARGIN,
    p75_margin: float = P75_MARGIN,
    ref_failrate_floor: float = REF_FAILRATE_FLOOR,
) -> dict[str, Any]:
    """Verdict for the impl SSIM series against a live ref-vs-refcal series.

    Returns a dict with:
      engaged  — whether the distribution calibration applies (ref is noisy
                 enough). When False the strict per-frame verdict stands.
      passed   — True iff engaged AND the impl distribution is no worse than
                 ref's on the stable high percentiles within the margins.
      impl/ref — the summary statistics for each side (failRate / p05 / p10 /
                 p25 / minSsim are evidence only; the gates use p50 + p75).
      checks   — per-criterion booleans (p50 / p75).
      reasons  — human-readable explanation lines.
    """
    impl_s = summarize(impl, threshold)
    ref_s = summarize(ref, threshold)
    base: dict[str, Any] = {
        "threshold": threshold,
        "margins": {
            "p50": p50_margin,
            "p75": p75_margin,
            "refFailRateFloor": ref_failrate_floor,
        },
        "impl": impl_s,
        "ref": ref_s,
        "checks": {"p50": False, "p75": False},
    }

    if impl_s["n"] == 0 or ref_s["n"] == 0:
        base["engaged"] = False
        base["passed"] = False
        base["reasons"] = [
            f"empty SSIM series (impl n={impl_s['n']}, ref n={ref_s['n']}) — "
            "cannot calibrate; strict per-frame verdict stands"
        ]
        return base

    # Engage when EITHER pair is phase-noisy: a noisy-splash refcal pair
    # sometimes draws clean (measured live: failRate 0.049) while the impl pair
    # is noisy — keying engagement on the ref pair alone then false-fails a fine
    # impl. max(impl, ref) keeps a truly clean splash on the strict verdict.
    noisiest = max(impl_s["failRate"], ref_s["failRate"])
    if noisiest <= ref_failrate_floor:
        base["engaged"] = False
        base["passed"] = False
        base["reasons"] = [
            f"splash is not phase-noisy (max failRate {noisiest:.3f} <= floor "
            f"{ref_failrate_floor:.3f}) — strict per-frame verdict stands"
        ]
        return base

    p50_ok = impl_s["p50"] >= ref_s["p50"] - p50_margin
    p75_ok = impl_s["p75"] >= ref_s["p75"] - p75_margin
    passed = bool(p50_ok and p75_ok)

    base["engaged"] = True
    base["passed"] = passed
    base["checks"] = {"p50": p50_ok, "p75": p75_ok}
    base["reasons"] = [
        f"p50 impl {impl_s['p50']:.4f} vs ref {ref_s['p50']:.4f} "
        f"(-{p50_margin:.3f} margin) -> {'ok' if p50_ok else 'WORSE'} [structural]",
        f"p75 impl {impl_s['p75']:.4f} vs ref {ref_s['p75']:.4f} "
        f"(-{p75_margin:.3f} margin) -> {'ok' if p75_ok else 'WORSE'} [near-match fraction]",
        f"evidence (NOT gated — phase-lottery tail): failRate impl "
        f"{impl_s['failRate']:.3f} vs ref {ref_s['failRate']:.3f}; p10 impl "
        f"{impl_s['p10']:.4f} vs ref {ref_s['p10']:.4f}; minSsim impl "
        f"{impl_s['minSsim']:.4f} vs ref {ref_s['minSsim']:.4f}",
    ]
    return base


# A consistent ref-vs-ref baseline this much above the better impl pairing means
# the impl RECORDING is the outlier (live-site load variance), not a real defect.
SUSPECT_GAP = 0.10
SUSPECT_BASELINE_MIN = 0.95  # baseline must itself be consistent to blame the impl


def evaluate_three(
    impl_ref: list[float],
    impl_refcal: list[float],
    ref_refcal: list[float],
    *,
    threshold: float = THRESHOLD,
    p50_margin: float = P50_MARGIN,
    p75_margin: float = P75_MARGIN,
    ref_failrate_floor: float = REF_FAILRATE_FLOOR,
    suspect_gap: float = SUSPECT_GAP,
    suspect_baseline_min: float = SUSPECT_BASELINE_MIN,
) -> dict[str, Any]:
    """Verdict using all three live recordings (ref, impl, refcal).

    The impl is faithful if it matches EITHER reference recording (ref and refcal
    are two valid captures of a phase-noisy splash; the impl aligning with one is
    enough). So S_impl is the better-aligned of {impl-vs-ref, impl-vs-refcal} by
    median, compared against the ref-vs-refcal baseline.

    suspect: when the baseline is itself CONSISTENT (ref ~= refcal, high median)
    but the impl matches NEITHER reference, the impl RECORDING is the outlier —
    live-site load variance, an unreliable capture. The caller re-records it
    (bounded), exactly like the truncation/anchor retries; a genuinely different
    impl stays divergent across re-records and fails.
    """
    m_ir = percentile(impl_ref, 50) if impl_ref else 0.0
    m_ic = percentile(impl_refcal, 50) if impl_refcal else 0.0
    s_impl = impl_ref if m_ir >= m_ic else impl_refcal
    base = compare_series(
        s_impl,
        ref_refcal,
        threshold=threshold,
        p50_margin=p50_margin,
        p75_margin=p75_margin,
        ref_failrate_floor=ref_failrate_floor,
    )
    base_median = percentile(ref_refcal, 50) if ref_refcal else 0.0
    impl_best_median = max(m_ir, m_ic)
    suspect = bool(
        not base["passed"]
        and base_median >= suspect_baseline_min
        and impl_best_median < base_median - suspect_gap
    )
    base["suspect"] = suspect
    base["pairings"] = {
        "implVsRefMedian": round(m_ir, 4),
        "implVsRefcalMedian": round(m_ic, 4),
        "refVsRefcalMedian": round(base_median, 4),
        "chosen": "impl-vs-ref" if s_impl is impl_ref else "impl-vs-refcal",
    }
    if suspect:
        base["reasons"].append(
            f"SUSPECT impl recording: consistent ref-vs-ref baseline "
            f"(median {base_median:.3f}) but impl matches neither reference "
            f"(best median {impl_best_median:.3f}) — unreliable capture, re-record"
        )
    return base


def _read_series(path: str) -> list[float]:
    out: list[float] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(float(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return out


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # 2 args: impl-vs-ref + ref-vs-refcal (compare_series).
    # 3 args: impl-vs-ref + ref-vs-refcal + impl-vs-refcal (evaluate_three:
    #         best-of-two references + suspect-recording flag).
    if len(args) not in (2, 3):
        print(
            "usage: python -m ui_clone.splash_distribution "
            "<impl-vs-ref> <ref-vs-refcal> [impl-vs-refcal]",
            file=sys.stderr,
        )
        return 2
    threshold = _env_float("SSIM_THRESHOLD", THRESHOLD)
    p50_margin = _env_float("UI_CLONE_VMC_SPLASH_P50_MARGIN", P50_MARGIN)
    p75_margin = _env_float("UI_CLONE_VMC_SPLASH_P75_MARGIN", P75_MARGIN)
    floor = _env_float("UI_CLONE_VMC_SPLASH_REF_FAILRATE_FLOOR", REF_FAILRATE_FLOOR)
    impl_ref = _read_series(args[0])
    ref_refcal = _read_series(args[1])
    if len(args) == 3:
        out = evaluate_three(
            impl_ref,
            _read_series(args[2]),
            ref_refcal,
            threshold=threshold,
            p50_margin=p50_margin,
            p75_margin=p75_margin,
            ref_failrate_floor=floor,
            suspect_gap=_env_float("UI_CLONE_VMC_SPLASH_SUSPECT_GAP", SUSPECT_GAP),
        )
    else:
        out = compare_series(
            impl_ref,
            ref_refcal,
            threshold=threshold,
            p50_margin=p50_margin,
            p75_margin=p75_margin,
            ref_failrate_floor=floor,
        )
    print(json.dumps(out, indent=2))
    return 0 if (out.get("engaged") and out.get("passed")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
