#!/usr/bin/env bash
# dssim-cap.sh — AE ceiling for the dssim/perceptual leniency paths.
#
# pass-by-dssim and pass-by-perceptual exist to absorb font anti-aliasing,
# idle drift, and codec noise — divergences that score high on AE but read
# identical to a human. They were NEVER meant to wave through sections whose
# AE/Mpx is an order of magnitude past the threshold (observed: a section at
# AE/Mpx 83593 = ~42x threshold passed pass-by-perceptual). Above
# threshold x SECTION_DSSIM_AE_CAP_MULT (default 10), the leniency paths
# close unless an explicit visual-judge confirmation exists:
#
#   <dir>/sections/<name>-judge.json   {"verdict": "PASS", ...}
#
# written by the visual-debug-reviewer subagent (Phase E protocol) AFTER
# looking at the actual ref/impl pair. A judge file older than the current
# impl crop is stale (the crop changed after the verdict) and is ignored.

# dssim_cap_allows <ae_per_mpx> <threshold> <cap_mult> <judge_file> <impl_crop>
# returns 0 when a dssim/perceptual pass is permitted at this AE level.
dssim_cap_allows() {
  local ae="${1:?ae_per_mpx}" thr="${2:?threshold}" mult="${3:?cap_mult}"
  local judge="${4:?judge_file}" crop="${5:?impl_crop}"
  local cap=$((thr * mult))
  if [ "$ae" -le "$cap" ]; then
    return 0
  fi
  # Above the cap: only a fresh PASS visual-judge confirmation re-opens the
  # leniency path.
  [ -f "$judge" ] || return 1
  grep -q '"verdict"[[:space:]]*:[[:space:]]*"PASS"' "$judge" || return 1
  if [ -f "$crop" ] && [ "$judge" -ot "$crop" ]; then
    return 1
  fi
  return 0
}
