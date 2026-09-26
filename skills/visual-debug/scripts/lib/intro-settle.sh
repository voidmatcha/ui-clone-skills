# shellcheck shell=bash
# intro-settle.sh — post-open wait that honors a measured intro overlay.
#
# SOURCE this file (do not exec it). Live probes that sample the page once
# after `open` + a fixed wait read a reference whose intro overlay is still
# covering it (hover candidates 0, canvas "missing"). The Step 5c-d
# clonability report (python -m ui_clone.clonability) records the measured
# overlay exit plus a margin as `verification.introSettleMs`; this helper
# turns it into the wait, never below the caller's existing fixed floor.
#
#   wait_ms=$(intro_settle_wait_ms "$REF_DIR" 2500)
#
# `UI_CLONE_INTRO_SETTLE_MS` overrides the report. The result is clamped to
# 15000ms (the hover-state-compare settle clamp) so a bad value cannot stall a
# sweep. A missing report or field keeps the floor, i.e. the old behavior.

intro_settle_wait_ms() {
  local ref_dir="${1:-}" floor="${2:-0}" ms=""
  if [ -n "${UI_CLONE_INTRO_SETTLE_MS:-}" ]; then
    ms="$UI_CLONE_INTRO_SETTLE_MS"
  elif [ -n "$ref_dir" ] && [ -f "$ref_dir/clonability-report.json" ]; then
    ms="$(python3 -c '
import json, sys
try:
    v = (json.load(open(sys.argv[1])).get("verification") or {}).get("introSettleMs")
    print(int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else "")
except Exception:
    print("")
' "$ref_dir/clonability-report.json" 2>/dev/null || true)"
  fi
  case "$ms" in
    '' | *[!0-9]*) ms=0 ;;
  esac
  if [ "$ms" -gt 15000 ]; then ms=15000; fi
  if [ "$ms" -lt "$floor" ]; then ms="$floor"; fi
  printf '%s\n' "$ms"
}
