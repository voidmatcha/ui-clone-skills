#!/usr/bin/env bash
# frame-align.sh — shared timing analysis + first-change alignment helpers
# for video-transition-compare.sh. Sourced (not executed); unit-tested by
# tests/test_video_recorder_freeze.py with synthetic frame sequences.
#
# analyze_timing <frames-dir> <label> [search-start-frame]
#   Prints change-point summary. Side effect: writes the 1-based index of the
#   first frame whose changed-pixel count exceeds the adaptive threshold to
#   <frames-dir>/.first-change. Full viewports retain the 5000px ceiling;
#   target-local ROI captures use 5% of their frame area so small controls can
#   still produce real change points. search-start-frame defaults to 1; changes
#   before it are ignored while the immediately preceding frame is retained as
#   the comparison baseline.
#   (writes 1 when no change point exists — raw alignment; a side with no
#   visual change cannot self-align, which is the anti-bypass property the
#   codex review required).
_frame_changed_pixel_count() {
  local previous="$1"
  local current="$2"
  local pixel_delta="${FRAME_CHANGE_PIXEL_DELTA_PERCENT:-0.5}"
  local value

  # ImageMagick 7.1.2 Q16-HDRI reports `compare -metric AE` as an error
  # magnitude, not a stable changed-pixel count, for ordinary small color
  # changes. Dividing that magnitude by QuantumRange hides real hover changes
  # (for example, 1,784 changed pixels became 24). Build a binary difference
  # mask instead: keep a pixel when any RGB channel changes by more than the
  # small compression-noise floor, then count the white pixels.
  if command -v magick >/dev/null 2>&1; then
    value=$(magick "$previous" "$current" \
      -compose difference -composite \
      -separate -evaluate-sequence max \
      -threshold "${pixel_delta}%" \
      -format '%[fx:mean*w*h]' info: 2>/dev/null) || value=0
  else
    value=$(convert "$previous" "$current" \
      -compose difference -composite \
      -separate -evaluate-sequence max \
      -threshold "${pixel_delta}%" \
      -format '%[fx:mean*w*h]' info: 2>/dev/null) || value=0
  fi
  awk -v value="$value" 'BEGIN { printf "%.0f\n", value + 0 }'
}

analyze_timing() {
  local dir="$1"
  local label="$2"
  local search_start="${3:-1}"
  local frames=("$dir"/*.png)
  local prev=""
  local changes=()
  local first_idx=""
  local last_idx=""
  local threshold="${FRAME_CHANGE_AE_THRESHOLD:-}"
  local cluster_gap="${FRAME_CHANGE_CLUSTER_GAP_FRAMES:-0}"
  local cluster_closed=0
  local ignored_late=0
  local f fname frame_idx

  if [[ ! "$search_start" =~ ^[0-9]+$ ]] || [[ "$search_start" -lt 1 ]]; then
    search_start=1
  fi

  if [[ ${#frames[@]} -eq 1 && ! -f "${frames[0]}" ]]; then
    frames=()
  fi

  if [[ ! "$threshold" =~ ^[0-9]+$ ]] || [[ "$threshold" -lt 1 ]]; then
    local first_frame dimensions width height pixels
    first_frame="${frames[0]:-}"
    dimensions=$(identify -format '%w %h' "$first_frame" 2>/dev/null || echo "0 0")
    read -r width height <<< "$dimensions"
    if [[ "$width" =~ ^[0-9]+$ ]] && [[ "$height" =~ ^[0-9]+$ ]]; then
      pixels=$((width * height))
    else
      pixels=0
    fi
    threshold=$((pixels / 20))
    [[ "$threshold" -lt 1 ]] && threshold=1
    [[ "$threshold" -gt 5000 ]] && threshold=5000
  fi

  for f in "${frames[@]}"; do
    fname="${f##*/}"
    frame_idx=0
    if [[ "$fname" =~ ([0-9]+) ]]; then
      frame_idx=$((10#${BASH_REMATCH[1]}))
    fi
    if [[ -n "$prev" && "$frame_idx" -ge "$search_start" ]]; then
      CHANGED_PIXELS=$(_frame_changed_pixel_count "$prev" "$f")
      CHANGED_PIXELS="${CHANGED_PIXELS:-0}"
      if [[ "$CHANGED_PIXELS" -gt "$threshold" ]]; then
        changes+=("$fname:changed=$CHANGED_PIXELS")
        if [[ -z "$first_idx" ]]; then
          first_idx="$frame_idx"
          last_idx="$frame_idx"
        elif [[ "$cluster_gap" =~ ^[0-9]+$ ]] \
          && [[ "$cluster_gap" -gt 0 ]] \
          && [[ "$cluster_closed" -eq 0 ]] \
          && [[ $((frame_idx - last_idx)) -gt "$cluster_gap" ]]; then
          cluster_closed=1
          ignored_late=$((ignored_late + 1))
        elif [[ "$cluster_closed" -eq 1 ]]; then
          ignored_late=$((ignored_late + 1))
        else
          last_idx="$frame_idx"
        fi
      fi
    fi
    prev="$f"
  done

  printf '%s\n' "${first_idx:-1}" > "$dir/.first-change"
  printf '%s\n' "${last_idx:-1}" > "$dir/.last-change"

  echo "  $label: ${#changes[@]} change points detected (changed-pixel threshold: $threshold)"
  if [[ ${#changes[@]} -gt 0 ]]; then
    echo "    First change: ${changes[0]}"
    echo "    Last change: ${changes[${#changes[@]}-1]}"
    if [[ "$ignored_late" -gt 0 ]]; then
      echo "    Timing cluster: ignored $ignored_late late isolated change point(s) after a ${cluster_gap}-frame settled gap"
    fi
  fi
}

# has_looping_video <media-freeze-sidecar.json>
#   True (exit 0) when the freeze sidecar written by freeze_videos() reports
#   any <video loop> attribute. The sidecar may be double-encoded (agent-browser
#   eval returns JSON.stringify output, stored verbatim — a JSON string holding
#   JSON). Missing or unparseable sidecar -> false: the looping-video arc bound
#   must only engage on positive evidence from BOTH sides.
has_looping_video() {
  local sidecar="$1"
  [[ -f "$sidecar" ]] || return 1
  python3 - "$sidecar" <<'PY'
import json, sys
try:
    v = json.loads(open(sys.argv[1]).read().strip())
    if isinstance(v, str):
        v = json.loads(v)
except Exception:
    sys.exit(1)
if isinstance(v, list) and any(isinstance(a, dict) and a.get("loop") for a in v):
    sys.exit(0)
sys.exit(1)
PY
}

# clamp_arc_last <first> <last> <cutoff>
#   Prints <last> bounded into [first, cutoff]. Used to bound splash arc
#   measurement to the common recording window when a looping bg video keeps
#   whole-frame change detection alive to the end of each clip (e2e-9: arc
#   delta 96 == recording-length delta 96 — the verdict was measuring
#   recorder-stop jitter, not the splash timeline). Motion starting after the
#   cutoff collapses to arc 0, which the one-side-no-motion anti-bypass in
#   arc_timing_verdict treats as a FAIL against a real arc — conservative.
clamp_arc_last() {
  local first="$1" last="$2" cutoff="$3"
  local out="$last"
  [[ "$out" -gt "$cutoff" ]] && out="$cutoff"
  [[ "$out" -lt "$first" ]] && out="$first"
  printf '%s\n' "$out"
}

# arc_common_budget <ref_first> <ref_total> <impl_first> <impl_total>
#   Prints the common per-side arc budget: min(ref_total-ref_first,
#   impl_total-impl_first). The symmetric arc clamp (batch-4 item 1) bounds
#   EACH side's last-change to its_own_first_change + this budget. The prior
#   looping-video clamp bounded both last-changes to ONE absolute cutoff
#   (min total frames), which truncated the side with the LATER first-change
#   more — equal real arcs with different load-latency lead-ins then false-
#   failed. Measuring the budget from each side's own first-change removes the
#   asymmetry. Negative remainders (first beyond total, impossible in practice)
#   collapse to 0.
arc_common_budget() {
  local ref_first="$1" ref_total="$2" impl_first="$3" impl_total="$4"
  local ref_rem=$((ref_total - ref_first))
  local impl_rem=$((impl_total - impl_first))
  local budget=$((ref_rem < impl_rem ? ref_rem : impl_rem))
  [[ "$budget" -lt 0 ]] && budget=0
  printf '%s\n' "$budget"
}

# _best_frame_ssim <ref_frame> <cmp_dir> <cmp_base_index> <jitter> <threshold>
#   Prints the best SSIM for ref_frame vs cmp_dir/f-<base>.png, scanning the
#   ±jitter neighbor frames ONLY when the primary pair is below threshold
#   (phase-jitter compensation between two independent recordings — NOT
#   threshold widening: the threshold passed in is unchanged and a real
#   easing/duration defect diverges for many consecutive frames beyond ±jitter).
#   Prints empty when the primary cmp frame is missing. Factored out of the
#   splash SSIM loop so the ref-vs-refcal calibration series is built with
#   byte-identical logic.
_best_frame_ssim() {
  local ref_frame="$1" cmp_dir="$2" base="$3" jitter="$4" threshold="$5"
  local cmp_frame ssim is_pass dj sign alt_k alt_frame alt_ssim
  cmp_frame=$(printf "%s/f-%06d.png" "$cmp_dir" "$base")
  [[ -f "$cmp_frame" ]] || { printf ''; return 0; }
  ssim=$(ffmpeg -i "$ref_frame" -i "$cmp_frame" -lavfi "ssim" -f null - 2>&1 | grep -oE 'All:[0-9.]+' | cut -d: -f2 || echo "0")
  [[ -z "$ssim" ]] && ssim="0"
  is_pass=$(awk -v a="$ssim" -v b="$threshold" 'BEGIN{print (a+0 >= b+0) ? 1 : 0}')
  if [[ "$is_pass" -ne 1 && "$jitter" -gt 0 ]]; then
    for dj in $(seq 1 "$jitter"); do
      for sign in -1 1; do
        alt_k=$((base + sign * dj))
        [[ "$alt_k" -ge 1 ]] || continue
        alt_frame=$(printf "%s/f-%06d.png" "$cmp_dir" "$alt_k")
        [[ -f "$alt_frame" ]] || continue
        alt_ssim=$(ffmpeg -i "$ref_frame" -i "$alt_frame" -lavfi "ssim" -f null - 2>&1 | grep -oE 'All:[0-9.]+' | cut -d: -f2 || echo "0")
        [[ -z "$alt_ssim" ]] && alt_ssim="0"
        if awk -v a="$alt_ssim" -v b="$ssim" 'BEGIN{exit !(a+0 > b+0)}'; then
          ssim="$alt_ssim"
        fi
      done
    done
  fi
  printf '%s' "$ssim"
}

# compute_ssim_series <ref_dir> <ref_off> <cmp_dir> <cmp_off> <aligned> <jitter> <threshold>
#   Prints one best-SSIM per aligned frame (ref[k+ref_off] vs cmp[k+cmp_off]),
#   k in 1..aligned. Missing ref frames are skipped (no row). Builds the
#   S_impl / S_ref SSIM series that the pure distribution comparator
#   (ui_clone.splash_distribution) consumes.
compute_ssim_series() {
  local ref_dir="$1" ref_off="$2" cmp_dir="$3" cmp_off="$4" aligned="$5" jitter="$6" threshold="$7"
  local k ref_frame s
  for k in $(seq 1 "$aligned"); do
    ref_frame=$(printf "%s/f-%06d.png" "$ref_dir" $((k + ref_off)))
    [[ -f "$ref_frame" ]] || continue
    s=$(_best_frame_ssim "$ref_frame" "$cmp_dir" $((k + cmp_off)) "$jitter" "$threshold")
    [[ -n "$s" ]] && printf '%s\n' "$s"
  done
}

# arc_timing_verdict <ref_first> <ref_last> <impl_first> <impl_last> <max_delta>
#   Compares ARC-INTERNAL timing (first-to-last-change duration) instead of
#   absolute first-change offsets: the live-network ref's first paint jitters
#   18-108 frames run-to-run (e2e-8 brief), so an absolute-offset delta fails
#   honest runs, while a wrong impl TIMELINE (too-long splash, missing
#   dismissal) shows up as a different arc length regardless of when paint
#   started. Anti-bypass: a side with no change points carries first==last==1
#   (arc 0) from analyze_timing — against a side with a real arc, the delta
#   fails, so a missing transition can never pass shape-free.
arc_timing_verdict() {
  local ref_first="$1" ref_last="$2" impl_first="$3" impl_last="$4" max_delta="${5:-18}"
  local ref_arc=$((ref_last - ref_first))
  local impl_arc=$((impl_last - impl_first))
  local delta=$((ref_arc - impl_arc))
  [[ "$delta" -lt 0 ]] && delta=$((-delta))
  echo "  arc timing: ref ${ref_arc} frames, impl ${impl_arc} frames, delta ${delta} (max ${max_delta})"
  if [[ "$ref_arc" -eq 0 && "$impl_arc" -eq 0 ]]; then
    # neither side detected motion — nothing to verdict here; the SSIM pass
    # and the vacuous-capture guards own that case.
    return 0
  fi
  if [[ "$ref_arc" -eq 0 || "$impl_arc" -eq 0 ]]; then
    echo "  ❌ arc timing: one side has no detected motion (missing transition)"
    return 1
  fi
  if [[ "$delta" -gt "$max_delta" ]]; then
    echo "  ❌ arc timing: first-to-last-change duration differs by ${delta} frames (>${max_delta})"
    return 1
  fi
  return 0
}

# arc_calibrated_verdict <ref_fc> <ref_lc> <ref_total> <impl_fc> <impl_lc> \
#                        <impl_total> <refcal_fc> <refcal_lc> <refcal_total> \
#                        <default_max_delta> <cal_margin>
#   Splash arc verdict CALIBRATED against a live ref-vs-refcal arc-noise floor
#   (batch-4 item 1). The static arc max_delta false-fails ref-vs-ref on the
#   phase-noisy splash class: the cold reference recording (loaded 1st)
#   over-detects its last change by up to ~40 frames vs the warm impl/refcal
#   recordings (loaded 2nd/3rd off a shared cache) — measured ref-vs-ref arc
#   deltas 0,0,5,29,41 across 5 runs vs the static max 18. This grounds the
#   tolerance in the SAME ref-vs-ref measurement the SSIM distribution uses (a
#   refcal recorded this run), NOT a constant:
#     - the direct ref-vs-impl arc is bounded to the 2-side ref/impl budget
#       before refcal is considered, so a late refcal cannot shrink/equalize a
#       bad impl timeline;
#     - one-side-no-motion (arc 0 against a real arc) stays a HARD fail — the
#       live noise floor must never rescue a missing/blank splash;
#     - the impl arc is compared to the NEARER of {ref, refcal} (the impl
#       matches whichever ref recording shares its cold/warm load state), with
#       the refcal-nearer branch only trusted while ref/refcal divergence stays
#       inside one ordinary tolerance plus one default detector delta, refcal
#       has real motion, and the noise is not more than half the ref arc; with
#       usable ref-vs-refcal noise capped by the ordinary default tolerance
#       policy so a broken calibration run cannot mask a wrong impl timeline.
#   A genuinely wrong timeline (too-long/too-short splash) matches NEITHER ref
#   recording and exceeds the tolerance by far, so it still fails.
#   Returns 0 (arc OK) / 1 (arc FAIL).
arc_calibrated_verdict() {
  local rfc="$1" rlc="$2" rtot="$3" ifc="$4" ilc="$5" itot="$6" \
        cfc="$7" clc="$8" ctot="$9" defmax="${10:-18}" margin="${11:-20}"
  local rrem=$((rtot - rfc)) irem=$((itot - ifc)) crem=$((ctot - cfc))
  local ref_impl_budget=$rrem
  [[ "$irem" -lt "$ref_impl_budget" ]] && ref_impl_budget=$irem
  [[ "$ref_impl_budget" -lt 0 ]] && ref_impl_budget=0
  local cal_budget=$ref_impl_budget
  [[ "$crem" -lt "$cal_budget" ]] && cal_budget=$crem
  [[ "$cal_budget" -lt 0 ]] && cal_budget=0
  local ra=$((rlc - rfc)) ia=$((ilc - ifc)) ca=$((clc - cfc))
  [[ "$ra" -gt "$ref_impl_budget" ]] && ra=$ref_impl_budget; [[ "$ra" -lt 0 ]] && ra=0
  [[ "$ia" -gt "$ref_impl_budget" ]] && ia=$ref_impl_budget; [[ "$ia" -lt 0 ]] && ia=0
  [[ "$ca" -gt "$cal_budget" ]] && ca=$cal_budget; [[ "$ca" -lt 0 ]] && ca=0
  if [[ "$ra" -eq 0 && "$ia" -eq 0 ]]; then
    echo "  arc(cal): neither side detected motion — SSIM/vacuous guards own this"
    return 0
  fi
  if [[ "$ia" -eq 0 || "$ra" -eq 0 ]]; then
    echo "  ❌ arc(cal): one side has no detected motion (missing transition) — not calibratable"
    return 1
  fi
  local noise=$((ra - ca)); [[ "$noise" -lt 0 ]] && noise=$((-noise))
  local d_ref=$((ia - ra)); [[ "$d_ref" -lt 0 ]] && d_ref=$((-d_ref))
  local d_cal=$((ia - ca)); [[ "$d_cal" -lt 0 ]] && d_cal=$((-d_cal))
  local impl_delta=$d_ref
  local refcal_nearer_limit=$((defmax + margin + defmax))
  local refcal_eligible=0
  if [[ "$ca" -gt 0 && "$noise" -le "$refcal_nearer_limit" && $((2 * noise)) -le "$ra" ]]; then
    refcal_eligible=1
  fi
  [[ "$refcal_eligible" -eq 1 && "$d_cal" -lt "$impl_delta" ]] && impl_delta=$d_cal
  # The margin is added once below. Cap the calibrated contribution at the
  # default delta so extreme refcal noise cannot widen the final boundary twice.
  local noise_cap=$defmax
  local usable_noise=$noise
  [[ "$usable_noise" -gt "$noise_cap" ]] && usable_noise=$noise_cap
  local tol=$defmax
  [[ "$usable_noise" -gt "$tol" ]] && tol=$usable_noise
  tol=$((tol + margin))
  echo "  arc(cal): ref ${ra} impl ${ia} refcal ${ca} frames (ref/impl budget ${ref_impl_budget}, cal budget ${cal_budget}); impl-vs-nearer-ref delta ${impl_delta}, live ref-vs-ref noise ${noise}, refcal-nearer limit ${refcal_nearer_limit}, refcal eligible ${refcal_eligible}, usable noise ${usable_noise}, tol ${tol}"
  if [[ "$impl_delta" -le "$tol" ]]; then
    return 0
  fi
  echo "  ❌ arc(cal): impl arc differs from both ref recordings by ${impl_delta} frames (> live-calibrated tol ${tol}) — wrong splash timeline"
  return 1
}
