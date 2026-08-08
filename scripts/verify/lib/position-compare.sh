#!/usr/bin/env bash
# position-compare.sh — scroll-position-aligned SSIM comparison for the
# scroll mode of video-transition-compare.sh. Sourced (not executed);
# unit-tested by tests/test_scroll_position_compare.py with synthetic frames.
#
# Why position-aligned instead of time-indexed video frames: the time-indexed
# scroll sweep quantizes into instant scroll steps scheduled per-side with
# in-page setTimeout; main-thread contention slides step execution by ±1 step
# independently per side, so frame-aligned SSIM measures jitter phase, not
# fidelity. Control experiment: the live reference compared against ITSELF
# failed 210/324 frames (65%) — see the e2e closeout tool-gap brief
# (video-motion-scroll-tool-gap.md). Position-aligned stills remove the time
# axis: both sides screenshot at the same proportional scroll fraction after
# a settle wait, so identical pages compare identical pixels by construction
# (the ref-vs-ref self-test property is structural).
#
# compare_position_frames <ref-dir> <impl-dir> <diff-dir> [threshold]
#   Compares same-named *.png pairs (sorted). Per-pair SSIM via ffmpeg; pairs
#   under threshold get an AE diff image in <diff-dir> and a table row.
#   Prints "Total positions compared: N" and "Pass: X, Fail: Y" (the tally
#   shape transition-proof-rollup.sh and the post-implement gate parse).
#   Returns 0 only when N > 0 and Y == 0. Anti-bypass: 0 compared pairs is a
#   vacuous run and returns 1 — an empty capture must never pass. A ref
#   position with no same-named impl frame is a FAIL row, never a skip.
compare_position_frames() {
  local ref_dir="$1"
  local impl_dir="$2"
  local diff_dir="$3"
  local threshold="${4:-0.90}"
  local pass=0 fail=0 total=0
  local results=""
  mkdir -p "$diff_dir"

  # Per-position SSIM sidecar (name<TAB>ssim<TAB>verdict): the noise-floor
  # calibration pass needs exact scores for failing rows, and diagnosis
  # benefits from pass-row scores too.
  local sidecar="$diff_dir/position-ssim.tsv"
  : > "$sidecar"

  local f name impl_f ssim is_pass
  for f in $(ls "$ref_dir"/*.png 2>/dev/null | sort); do
    name=$(basename "$f")
    impl_f="$impl_dir/$name"
    total=$((total + 1))
    if [[ ! -f "$impl_f" ]]; then
      fail=$((fail + 1))
      results="${results}| ${name} | missing-impl-frame | ❌ |\n"
      printf '%s\t%s\t%s\n' "$name" "0" "missing-impl-frame" >> "$sidecar"
      continue
    fi
    ssim=$(ffmpeg -i "$f" -i "$impl_f" -lavfi "ssim" -f null - 2>&1 | grep -oE 'All:[0-9.]+' | cut -d: -f2 || echo "0")
    [[ -z "$ssim" ]] && ssim="0"
    is_pass=$(awk -v a="$ssim" -v b="$threshold" 'BEGIN{print (a+0 >= b+0) ? 1 : 0}')
    if [[ "$is_pass" -eq 1 ]]; then
      pass=$((pass + 1))
      printf '%s\t%s\t%s\n' "$name" "$ssim" "pass" >> "$sidecar"
    else
      fail=$((fail + 1))
      compare -metric AE "$f" "$impl_f" "$diff_dir/$name" 2>/dev/null || true
      results="${results}| ${name} | ${ssim} | ❌ |\n"
      printf '%s\t%s\t%s\n' "$name" "$ssim" "fail" >> "$sidecar"
    fi
  done

  POSITION_PASS=$pass
  POSITION_FAIL=$fail
  POSITION_TOTAL=$total
  POSITION_RESULTS="$results"

  echo "Total positions compared: $total"
  echo "Pass: $pass, Fail: $fail"
  if [[ "$fail" -gt 0 ]]; then
    echo ""
    echo "| Position | SSIM | Status |"
    echo "|----------|------|--------|"
    echo -e "$results"
  fi
  if [[ "$total" -eq 0 ]]; then
    echo "❌ vacuous: 0 position pairs captured — capture stage produced nothing"
    return 1
  fi
  [[ "$fail" -eq 0 ]]
}

# noise_floor_allows <impl_ssim> <refref_ssim> <threshold> [margin] [floor] [band]
#   Ref-vs-ref noise-floor calibration for BORDERLINE positions only.
#   A live reference compared against a fresh capture of ITSELF measures the
#   capture environment's noise (CDN re-encode variants, media-presentation
#   state) — the only honest baseline when an impl deterministically lands a
#   hair under the fixed threshold while live DOM parity is exact (e2e-8
#   pos-013: 0.89957 vs 0.90). Codex-reviewed bounds:
#     - band: only scores within [threshold-band, threshold) are eligible
#       (default band 0.02 -> 0.88..0.90 at the 0.90 threshold); anything
#       lower is a genuine defect, never calibrated away.
#     - margin: impl must score >= refref - margin (default 0.015).
#     - floor: absolute minimum 0.87 — a catastrophically noisy ref-vs-ref
#       must not whitelist a bad impl.
noise_floor_allows() {
  local impl_ssim="$1" refref_ssim="$2" threshold="$3"
  local margin="${4:-0.015}" floor="${5:-0.87}" band="${6:-0.02}"
  awk -v i="$impl_ssim" -v r="$refref_ssim" -v t="$threshold" \
      -v m="$margin" -v f="$floor" -v b="$band" 'BEGIN {
    if (i + 0 >= t + 0) exit 0;            # already passing — nothing to do
    if (i + 0 < t - b) exit 1;             # below the borderline band
    if (i + 0 < f + 0) exit 1;             # absolute floor
    if (i + 0 >= r - m) exit 0;            # within the measured noise floor
    exit 1;
  }'
}

# dynamic_selectors_from_spec <transition-spec.json>
#   Prints the `target` selectors of every transitions[] entry marked
#   dynamic:true, joined by `||` (selectors may contain commas). When the
#   grounded animation metadata explicitly describes canvas pixels/physics,
#   include the rendered `canvas` surface too: extraction can legitimately
#   name a sibling content wrapper as the semantic target while the changing
#   pixels live on the canvas element. Empty output when the spec is
#   missing/unreadable — masking is then a no-op.
dynamic_selectors_from_spec() {
  local spec="$1"
  [[ -f "$spec" ]] || { echo ""; return 0; }
  python3 - "$spec" <<'PY' 2>/dev/null || echo ""
import json, sys
try:
    spec = json.load(open(sys.argv[1]))
except Exception:
    print("")
    raise SystemExit(0)
sels = []
for t in spec.get("transitions", []):
    if isinstance(t, dict) and t.get("dynamic") and t.get("target"):
        sels.append(str(t["target"]))
        animation = t.get("animation")
        if isinstance(animation, dict):
            motion_text = " ".join(
                str(animation.get(key, "")) for key in ("type", "property")
            ).lower()
            if "canvas" in motion_text:
                sels.append("canvas")
print("||".join(dict.fromkeys(sels)))
PY
}
