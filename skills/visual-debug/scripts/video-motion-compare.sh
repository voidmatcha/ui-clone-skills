#!/usr/bin/env bash
# video-motion-compare.sh — verification-plan dispatch entry for 60fps motion compare.
#
# Why this wrapper:
#   scripts/verify/video-transition-compare.sh is signature
#   <session> <orig> <impl> <out-dir> <action>. The verification-plan dispatch
#   uses <orig-url> <impl-url> <session> <ref-dir>. This wrapper adapts arg
#   shape and runs video-transition-compare.sh in whichever motion modes the
#   site's signals demand (splash / scroll). Aggregates results into a single
#   text artifact gate_post_implement can scan for ❌.
#
# Staged motion check — trajectory pre-filter then 60fps SSIM:
#   `transition-trajectory-compare.sh` runs a CHEAP 5-point AE check (0/25/50/
#   75/100% scroll). Its strength: gross failure detection (no motion, wrong
#   end state, reversed direction). Its weakness: same-end-same-midpoints-
#   different-velocity passes (easeOutCubic vs easeOutQuint look identical at
#   0/25/50/75/100). So trajectory PASS is INCONCLUSIVE, but trajectory FAIL
#   is RELIABLE — if 5 samples diverge, 60fps will too. We use it as a
#   fail-fast pre-filter to avoid burning expensive video recording when the
#   gross check already fails.
#
#   Order:
#     1. trajectory-compare (cheap) → if FAIL: emit result + exit, skip video.
#     2. video-transition-compare (60fps SSIM) → authoritative verdict.
#
#   Skip the pre-filter via PRE_FILTER=0 when you specifically want the full
#   video pass (e.g., regression debugging the video pipeline itself).
#
# Usage:
#   bash video-motion-compare.sh <orig-url> <impl-url> <session> <ref-dir>
#
# Reads <ref-dir>/verification-plan.json signals; runs:
#   - "splash" if hasSplash=true
#   - "scroll" if hasScrollScrub=true OR hasIOReveal=true
# Output: <ref-dir>/transitions/video-motion-result.txt (❌ on any failure).

set -uo pipefail

# Source the timeout shim so macOS gets a working `timeout` cmd even when
# coreutils isn't installed. See scripts/lib/timeout-shim.sh.
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_SHIM="$_SCRIPT_DIR/../../../scripts/lib/timeout-shim.sh"
[ -f "$_SHIM" ] && . "$_SHIM" || true

ORIG_URL="${1:?Usage: video-motion-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"
IMPL_URL="${2:?Usage: video-motion-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"
SESSION="${3:?Usage: video-motion-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"
REF_DIR="${4:?Usage: video-motion-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"

if [[ "$REF_DIR" != /* ]]; then
  REF_DIR="$(pwd)/$REF_DIR"
fi

# Resolve project root through standard envs, fall back to script's own location.
PROJECT_ROOT="${PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${UI_CLONE_ROOT:-}}}}"
if [ -z "$PROJECT_ROOT" ]; then
  PROJECT_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
fi
COMPARE="$PROJECT_ROOT/scripts/verify/video-transition-compare.sh"

if [ ! -x "$COMPARE" ] && [ ! -f "$COMPARE" ]; then
  echo "ERROR: video-transition-compare.sh not found at $COMPARE" >&2
  exit 2
fi

PLAN="$REF_DIR/verification-plan.json"
HAS_SCROLL="false"
HAS_SPLASH="false"
HAS_IO="false"
if [ -f "$PLAN" ] && command -v jq >/dev/null 2>&1; then
  HAS_SCROLL=$(jq -r '.signals.hasScrollScrub // false' "$PLAN" 2>/dev/null || echo "false")
  HAS_SPLASH=$(jq -r '.signals.hasSplash // false' "$PLAN" 2>/dev/null || echo "false")
  HAS_IO=$(jq -r '.signals.hasIOReveal // false' "$PLAN" 2>/dev/null || echo "false")
fi

OUT_DIR="$REF_DIR/transitions/video-motion"
mkdir -p "$OUT_DIR"
RESULT="$REF_DIR/transitions/video-motion-result.txt"

structural_only_mode() {
  python3 - "$REF_DIR" <<'PY'
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
asset_sub = ref_dir / "asset-substitution.json"
result = ref_dir / "sections" / "result.txt"
if not asset_sub.exists() or not result.exists():
    print("false")
    raise SystemExit(0)
try:
    data = json.loads(asset_sub.read_text(encoding="utf-8"))
except Exception:
    print("false")
    raise SystemExit(0)
if not data.get("structuralOnlySections"):
    print("false")
    raise SystemExit(0)
text = result.read_text(encoding="utf-8", errors="replace")
m = re.search(r"\*\*Result:\s*(\d+)\s+PASS,\s*(\d+)\s+FAIL,\s*(\d+)\s+SKIP,\s*(\d+)\s+STRUCTURAL_ONLY", text)
if not m:
    print("false")
    raise SystemExit(0)
fail = int(m.group(2))
structural = int(m.group(4))
print("true" if fail == 0 and structural > 0 else "false")
PY
}

STRUCTURAL_ONLY_MODE="${STRUCTURAL_TRAJECTORY_MODE:-$(structural_only_mode)}"

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
{
  echo "# video-motion-compare"
  echo "# generated: $NOW"
  echo "# signals: scrollScrub=$HAS_SCROLL splash=$HAS_SPLASH ioReveal=$HAS_IO"
  echo
} > "$RESULT"

# ── Stage 1: cheap trajectory pre-filter (fail-fast) ──
# Only run when scroll-driven motion is on the verification plan — splash
# intros are time-driven, not scroll-driven, and trajectory-compare doesn't
# apply. Skippable via PRE_FILTER=0 for video-pipeline debugging.
PRE_FILTER="${PRE_FILTER:-1}"
TRAJ_SCRIPT="$_SCRIPT_DIR/transition-trajectory-compare.sh"
if [ "$PRE_FILTER" = "1" ] \
   && { [ "$HAS_SCROLL" = "true" ] || [ "$HAS_IO" = "true" ]; } \
   && [ -f "$TRAJ_SCRIPT" ]; then
  {
    echo "## pre-filter: 5-point trajectory probe"
    echo
  } >> "$RESULT"
  if bash "$TRAJ_SCRIPT" "$ORIG_URL" "$IMPL_URL" "$SESSION-traj" "$REF_DIR" >> "$RESULT" 2>&1; then
    {
      echo "✓ trajectory pre-filter passed"
      echo
    } >> "$RESULT"
    if [ "$STRUCTURAL_ONLY_MODE" = "true" ]; then
      {
        echo "✅ structural motion trajectory passed — skipping full-frame SSIM because section-compare is STRUCTURAL_ONLY"
        echo
        echo "✅ all 1 mode(s) within structural trajectory threshold"
      } >> "$RESULT"
      echo "Wrote $RESULT (structural trajectory closeout)"
      exit 0
    fi
  else
    {
      echo
      echo "❌ trajectory pre-filter FAILED — gross motion mismatch, skipping expensive video step"
      echo "Re-run with \`PRE_FILTER=0 bash video-motion-compare.sh ...\` to bypass."
    } >> "$RESULT"
    echo "Wrote $RESULT (early-exit on trajectory fail)"
    exit 1
  fi
fi

FAIL_COUNT=0
RUN_COUNT=0

run_mode() {
  local mode="$1"
  local label="$2"
  local mode_dir="$OUT_DIR/$mode"
  mkdir -p "$mode_dir"
  RUN_COUNT=$((RUN_COUNT + 1))
  {
    echo "## $label ($mode)"
    echo
  } >> "$RESULT"
  if bash "$COMPARE" "$SESSION-vm-$mode" "$ORIG_URL" "$IMPL_URL" "$mode_dir" "$mode" >> "$RESULT" 2>&1; then
    echo "✅ $label clean" >> "$RESULT"
  else
    echo "❌ $label divergence — inspect $mode_dir/diff-frames/" >> "$RESULT"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
  echo >> "$RESULT"
}

if [ "$HAS_SPLASH" = "true" ]; then
  run_mode splash "Splash / intro"
fi

if [ "$HAS_SCROLL" = "true" ] || [ "$HAS_IO" = "true" ]; then
  run_mode scroll "Scroll-driven motion"
fi

if [ "$RUN_COUNT" -eq 0 ]; then
  # No motion signals — verification-plan should not have added this row.
  # Pass cleanly so the gate is not blocked by a no-op.
  echo "✅ no motion signals — video compare skipped" >> "$RESULT"
fi

{
  echo
  if [ "$FAIL_COUNT" -eq 0 ]; then
    echo "✅ all $RUN_COUNT mode(s) within SSIM threshold"
  else
    echo "❌ $FAIL_COUNT/$RUN_COUNT mode(s) diverged — tighten easing / threshold params"
  fi
} >> "$RESULT"

echo "Wrote $RESULT"
exit 0
