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

# W-4 (loop-ebpb-0): the reference follows prefers-color-scheme — a host
# OS theme flip (macOS auto-dark in the evening) silently captured the ref
# in dark mode and poisoned an entire compare cycle (footer dSSIM
# 0.0000065 -> 0.687 reading as catastrophic regression). Pin light unless
# the caller explicitly overrides.
: "${AGENT_BROWSER_COLOR_SCHEME:=light}"
export AGENT_BROWSER_COLOR_SCHEME

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

# Spec-declared dynamic regions (transition-spec dynamic:true targets) are
# masked identically on both sides during scroll-position capture — the
# position-compare analogue of section-compare's EXCLUDE_DYNAMIC. The
# selector list rides an env var because video-transition-compare.sh only
# receives an out-dir, not the ref dir.
# shellcheck source=../../../scripts/verify/lib/position-compare.sh
. "$PROJECT_ROOT/scripts/verify/lib/position-compare.sh"
VIDEO_COMPARE_DYNAMIC_SELECTORS="$(dynamic_selectors_from_spec "$REF_DIR/transition-spec.json")"
export VIDEO_COMPARE_DYNAMIC_SELECTORS

if [ ! -x "$COMPARE" ] && [ ! -f "$COMPARE" ]; then
  echo "ERROR: video-transition-compare.sh not found at $COMPARE" >&2
  exit 2
fi

PLAN="$REF_DIR/verification-plan.json"
HAS_SCROLL="false"
HAS_SPLASH="false"
HAS_IO="false"
# F13: read the plan signals with python (always present), NOT jq. Gating this on
# `command -v jq` meant that on a host without jq the signals stayed "false", every
# mode loop was skipped, RUN_COUNT hit 0, and the script wrote "no motion signals —
# skipped" + exit 0 EVEN WHEN the plan declared scroll/splash — a silent clean pass
# for an unmeasured motion page. python is already a hard dependency of this script.
if [ -f "$PLAN" ]; then
  read -r HAS_SCROLL HAS_SPLASH HAS_IO < <(python3 - "$PLAN" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    d = {}
s = d.get("signals") if isinstance(d.get("signals"), dict) else {}
out = ["true" if s.get(k) else "false"
       for k in ("hasScrollScrub", "hasSplash", "hasIOReveal")]
print(*out)
PY
)
  HAS_SCROLL="${HAS_SCROLL:-false}"; HAS_SPLASH="${HAS_SPLASH:-false}"; HAS_IO="${HAS_IO:-false}"
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
        echo "# video-motion-compare: COMPLETE"
      } >> "$RESULT"
      echo "Wrote $RESULT (structural trajectory closeout)"
      exit 0
    fi
  else
    {
      echo
      echo "❌ trajectory pre-filter FAILED — gross motion mismatch, skipping expensive video step"
      echo "Re-run with \`PRE_FILTER=0 bash video-motion-compare.sh ...\` to bypass."
      echo "# video-motion-compare: COMPLETE"
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
  local mode_log
  mode_log="$(mktemp "${TMPDIR:-/tmp}/video-motion-mode.XXXXXX")"
  # Chunked-resume aggregation (batch-4 item 2): a scroll sweep captured under
  # UI_CLONE_VMC_SCROLL_CHUNK exits cleanly after one chunk with a
  # scroll-resume.json and no verdict. Re-invoke until the recorder produces a
  # verdict (resume.json gone). Each invocation captures one foreground-safe
  # chunk and resumes from the persisted manifest, so a lost wake-up costs at
  # most one chunk. The default (full-sweep chunk) completes in one pass, so
  # this loop runs exactly once and the verdict is identical to the monolithic
  # run.
  local code attempt=0 max_resume="${UI_CLONE_VMC_MAX_RESUME:-500}"
  while :; do
    bash "$COMPARE" "$SESSION-vm-$mode" "$ORIG_URL" "$IMPL_URL" "$mode_dir" "$mode" > "$mode_log" 2>&1
    code=$?
    if [ "$code" -eq 0 ] && [ -f "$mode_dir/scroll-resume.json" ]; then
      attempt=$((attempt + 1))
      echo "↻ $label resume $attempt — captured a chunk, continuing" >> "$RESULT"
      if [ "$attempt" -ge "$max_resume" ]; then
        echo "❌ $label did not converge after $attempt resume invocations — aborting" >> "$RESULT"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        rm -f "$mode_log"
        echo >> "$RESULT"
        return
      fi
      continue
    fi
    break
  done
  cat "$mode_log" >> "$RESULT"
  if [ "$code" -eq 0 ]; then
    # Loop-10 fix (c): exit 0 must come with measurement evidence — a
    # truncated/empty run that still exits clean is the empty-success class.
    if ! grep -qE "^(Pass|Total frames compared):" "$mode_log"; then
      echo "❌ $label produced no measurement rows despite a clean exit — treating as hard failure (truncation/empty-success class)" >> "$RESULT"
      FAIL_COUNT=$((FAIL_COUNT + 1))
    else
      echo "✅ $label clean" >> "$RESULT"
    fi
  elif [ "$code" -eq 2 ]; then
    echo "❌ $label UNMEASURABLE (setup/extraction/window error, exit 2) — the recording must be re-run; this is never a pass" >> "$RESULT"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  else
    echo "❌ $label divergence — inspect $mode_dir/diff-frames/" >> "$RESULT"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
  rm -f "$mode_log"
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
  # Completion sentinel: a result file WITHOUT this line was truncated by a
  # dispatcher timeout/kill — consumers must treat its absence as
  # "check did not finish", never as a clean run.
  echo "# video-motion-compare: COMPLETE"
} >> "$RESULT"

echo "Wrote $RESULT"
# Exit-code contract: dispatchers (run-required-checks.sh) and exit-code-based
# tooling must see divergence as failure — the text-scan gate already does,
# but `exit 0` on ❌ rows left every exit-code consumer reporting clean.
if [ "$FAIL_COUNT" -gt 0 ]; then
  exit 1
fi
exit 0
