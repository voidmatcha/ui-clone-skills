#!/usr/bin/env bash
# video-transition-compare.sh — Video-based transition comparison
# Records the same interaction on original + implementation, extracts frames at 60fps,
# runs SSIM batch comparison, outputs pass/fail table.
#
# FPS policy: all video frame extraction in this repo uses 60fps. FPS=60 is the
# canonical default below; override only if a target site renders at a lower rate.
#
# Usage:
#   bash video-transition-compare.sh <session> <orig-url> <impl-url> <output-dir> <action-script>
#
# Arguments:
#   session      — agent-browser session name
#   orig-url     — original site URL
#   impl-url     — implementation URL
#   output-dir   — where to save frames and results (e.g., tmp/ref/same-energy/transitions)
#   action-script — path to a shell script that performs the interaction (click, etc.)
#                   OR one of the built-in actions:
#                     "splash"             — record page load (no interaction)
#                     "scroll"             — coordinated scroll over RECORD_DURATION
#                     "click:<selector>"   — click element and record transition
#                     "hover:<selector>"   — real-mouse hover and record entry arc
#                     "hover-and-out:<selector>"
#                                          — hover, record entry arc, move mouse
#                                            away, record exit arc. Total
#                                            recording = 2 * RECORD_DURATION.
#
# Example:
#   bash video-transition-compare.sh same-energy https://same.energy/ http://localhost:4001/same-energy \
#     tmp/ref/same-energy/transitions "click:[class*=image_container]"
#
# Output:
#   <output-dir>/ref-frames/   — 60fps frames from original
#   <output-dir>/impl-frames/  — 60fps frames from implementation
#   <output-dir>/diff-frames/  — diff images for failing frames
#   <output-dir>/result.txt    — SSIM comparison results
#
# Requirements: agent-browser, ffmpeg, imagemagick (compare)

set -euo pipefail

SESSION="${1:?Usage: video-transition-compare.sh <session> <orig> <impl> <outdir> <action>}"
ORIG_URL="${2:?}"
IMPL_URL="${3:?}"
OUT_DIR="${4:?}"
ACTION="${5:?}"

RECORD_DURATION="${RECORD_DURATION:-5}"
SSIM_THRESHOLD="${SSIM_THRESHOLD:-0.90}"
FPS="${FPS:-60}"
PRE_ACTION_WAIT="${PRE_ACTION_WAIT:-3}"
VIEW_W="${VIEW_W:-1440}"
VIEW_H="${VIEW_H:-900}"

# Cleanup browser sessions on exit (including errors/signals)
cleanup_browsers() {
  agent-browser --session "${SESSION}-orig" close 2>/dev/null
  agent-browser --session "${SESSION}-impl" close 2>/dev/null
}
trap cleanup_browsers EXIT

# Optional: skip SSIM comparison, just extract frames for manual review
SKIP_SSIM="${SKIP_SSIM:-false}"
# Optional: only compare timing (detect when frames start changing)
TIMING_ONLY="${TIMING_ONLY:-false}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${BOLD}═══ Transition Compare ═══${NC}"
echo "Original: $ORIG_URL"
echo "Implementation: $IMPL_URL"
echo "Action: $ACTION"
echo "Duration: ${RECORD_DURATION}s, FPS: $FPS, SSIM threshold: $SSIM_THRESHOLD"
echo ""

mkdir -p "$OUT_DIR"/{ref-video,impl-video,ref-frames,impl-frames,diff-frames}

# ── Helper: perform action ──
perform_action() {
  local session="$1"
  local action="$2"

  if [[ "$action" == "splash" ]]; then
    # Splash: just wait — the recording captures page load
    sleep "$RECORD_DURATION"
  elif [[ "$action" == click:* ]]; then
    local selector="${action#click:}"
    sleep "$PRE_ACTION_WAIT"
    agent-browser eval "(() => {
      var el = document.querySelector('$selector');
      if (el) { el.click(); return 'clicked'; }
      return 'not found';
    })()" --session "$session" 2>&1 | head -1
    sleep "$RECORD_DURATION"
  elif [[ "$action" == hover:* ]]; then
    # Hover motion arc. Uses agent-browser's real mouse hover so the `:hover`
    # pseudo-class fires (synthetic mouseover events don't trigger CSS :hover).
    # PRE_ACTION_WAIT lets the page settle before hover so the baseline frames
    # are clean; RECORD_DURATION after hover captures the full entry transition.
    # We don't un-hover — entry-arc coverage is the primary value here; exit is
    # symmetric in most designs and adding move-away would double the per-target
    # time budget without proportional bug coverage.
    local selector="${action#hover:}"
    sleep "$PRE_ACTION_WAIT"
    agent-browser hover "$selector" --session "$session" 2>&1 | head -1
    sleep "$RECORD_DURATION"
  elif [[ "$action" == hover-and-out:* ]]; then
    # Hover entry + exit arc. Captures the case where the exit transition is
    # NOT symmetric with entry (different easing on close, hover-delay,
    # group-hover chain unwind, scaleY-then-translate panel collapses).
    # Symmetric designs are the common case — entry-only `hover:` is cheaper
    # and the default for hover-state-compare.sh; this mode is opt-in for
    # asymmetric designs (Webflow IX2 "On Mouse Leave" handlers, custom
    # cubic-bezier on exit, etc.) where the entry-only sweep would miss real
    # divergence. Total recording length is 2 × RECORD_DURATION.
    #
    # Mouse-move target (0,0) is chosen because (a) it is always inside the
    # viewport (so the cursor doesn't land outside the captured frame and
    # leave :hover stuck on the previous target via Chrome's "last hover
    # under cursor" behavior), and (b) it is at the top-left corner of the
    # page where the typical hover target is unlikely to overlap a
    # second-level element. Pages with full-bleed top-left elements (sticky
    # header, hero menu) can override via the MOUSE_AWAY_X / MOUSE_AWAY_Y
    # env vars.
    local selector="${action#hover-and-out:}"
    local away_x="${MOUSE_AWAY_X:-0}"
    local away_y="${MOUSE_AWAY_Y:-0}"
    sleep "$PRE_ACTION_WAIT"
    agent-browser hover "$selector" --session "$session" 2>&1 | head -1
    sleep "$RECORD_DURATION"
    agent-browser mouse move "$away_x" "$away_y" --session "$session" 2>&1 | head -1
    sleep "$RECORD_DURATION"
  elif [[ "$action" == "scroll" ]]; then
    # Coordinated full-page scroll over RECORD_DURATION. Scrolls by FRACTION of
    # scrollHeight (not absolute pixels) so a shorter-content impl traverses
    # the same proportional range as ref. Captures scroll-scrub + IO-reveal
    # transitions at the recorder's FPS.
    local steps="${SCROLL_STEPS:-60}"
    local step_ms
    step_ms=$(awk -v d="$RECORD_DURATION" -v s="$steps" 'BEGIN{printf "%.0f", (d*1000)/s}')
    agent-browser eval "(() => {
      let i = 0;
      const total = $steps;
      const interval = $step_ms;
      const tick = () => {
        const max = Math.max(document.documentElement.scrollHeight - window.innerHeight, 0);
        window.scrollTo({top: (i/total)*max, behavior: 'instant'});
        i++;
        if (i <= total) setTimeout(tick, interval);
      };
      tick();
      return 'scroll-started';
    })()" --session "$session" 2>&1 | head -1
    sleep "$RECORD_DURATION"
  elif [[ -f "$action" ]]; then
    # Custom script
    bash "$action" "$session"
  else
    echo "Unknown action: $action"
    exit 1
  fi
}

# ── Phase 1: Record original ──
echo -e "${BOLD}▸ Recording original...${NC}"

if [[ "$ACTION" == "splash" ]]; then
  agent-browser open "$ORIG_URL" --session "${SESSION}-orig" 2>&1 | head -1
  sleep 3
  agent-browser set viewport $VIEW_W $VIEW_H --session "${SESSION}-orig" 2>&1 | head -1
  agent-browser record start "$OUT_DIR/ref-video/raw.webm" --session "${SESSION}-orig" 2>&1 | head -1
  sleep "$RECORD_DURATION"
else
  agent-browser open "$ORIG_URL" --session "${SESSION}-orig" 2>&1 | head -1
  sleep "$PRE_ACTION_WAIT"
  agent-browser set viewport $VIEW_W $VIEW_H --session "${SESSION}-orig" 2>&1 | head -1
  sleep 2
  agent-browser record start "$OUT_DIR/ref-video/raw.webm" --session "${SESSION}-orig" 2>&1 | head -1
  perform_action "${SESSION}-orig" "$ACTION"
fi

agent-browser record stop --session "${SESSION}-orig" 2>&1 | head -1
agent-browser --session "${SESSION}-orig" close 2>/dev/null

echo "  ✓ Original recorded"

# ── Phase 2: Record implementation ──
echo -e "${BOLD}▸ Recording implementation...${NC}"

if [[ "$ACTION" == "splash" ]]; then
  agent-browser record start "$OUT_DIR/impl-video/raw.webm" --session "${SESSION}-impl" 2>&1 | head -1
  sleep 0.2
  agent-browser open "$IMPL_URL" --session "${SESSION}-impl" 2>&1 | head -1
  sleep "$RECORD_DURATION"
else
  agent-browser open "$IMPL_URL" --session "${SESSION}-impl" 2>&1 | head -1
  sleep "$PRE_ACTION_WAIT"
  agent-browser set viewport $VIEW_W $VIEW_H --session "${SESSION}-impl" 2>&1 | head -1
  sleep 2
  agent-browser record start "$OUT_DIR/impl-video/raw.webm" --session "${SESSION}-impl" 2>&1 | head -1
  perform_action "${SESSION}-impl" "$ACTION"
fi

agent-browser record stop --session "${SESSION}-impl" 2>&1 | head -1
agent-browser --session "${SESSION}-impl" close 2>/dev/null

echo "  ✓ Implementation recorded"

# ── Phase 3: Extract frames at 60fps ──
echo -e "${BOLD}▸ Extracting frames at ${FPS}fps...${NC}"

ffmpeg -y -i "$OUT_DIR/ref-video/raw.webm" -vf "fps=$FPS" "$OUT_DIR/ref-frames/f-%06d.png" 2>/dev/null
ffmpeg -y -i "$OUT_DIR/impl-video/raw.webm" -vf "fps=$FPS" "$OUT_DIR/impl-frames/f-%06d.png" 2>/dev/null

REF_COUNT=$(ls "$OUT_DIR/ref-frames/"*.png 2>/dev/null | wc -l | tr -d ' ')
IMPL_COUNT=$(ls "$OUT_DIR/impl-frames/"*.png 2>/dev/null | wc -l | tr -d ' ')
MIN_COUNT=$((REF_COUNT < IMPL_COUNT ? REF_COUNT : IMPL_COUNT))

echo "  Ref frames: $REF_COUNT, Impl frames: $IMPL_COUNT, Comparing: $MIN_COUNT"

if [[ "$MIN_COUNT" -eq 0 ]]; then
  echo -e "${RED}ERROR: No frames to compare${NC}"
  exit 1
fi

# ── Phase 3.5: Timing analysis (always runs) ──
echo -e "${BOLD}▸ Analyzing transition timing...${NC}"

# Detect when frames start changing (AE between consecutive frames)
analyze_timing() {
  local dir="$1"
  local label="$2"
  local count=$(ls "$dir/"*.png 2>/dev/null | wc -l | tr -d ' ')
  local prev=""
  local changes=()

  for f in $(ls "$dir/"*.png | sort | head -"$count"); do
    if [[ -n "$prev" ]]; then
      AE_RAW=$(compare -metric AE "$prev" "$f" /dev/null 2>&1 || echo "0")
      AE=$(echo "$AE_RAW" | grep -oE '^[0-9]+' | head -1)
      AE="${AE:-0}"
      if [[ "$AE" -gt 5000 ]]; then
        local fname=$(basename "$f")
        changes+=("$fname:AE=$AE")
      fi
    fi
    prev="$f"
  done

  echo "  $label: ${#changes[@]} change points detected"
  if [[ ${#changes[@]} -gt 0 ]]; then
    echo "    First change: ${changes[0]}"
    echo "    Last change: ${changes[${#changes[@]}-1]}"
  fi
}

analyze_timing "$OUT_DIR/ref-frames" "Original"
analyze_timing "$OUT_DIR/impl-frames" "Implementation"

# ── Phase 4: SSIM batch comparison (optional) ──
PASS=0
FAIL=0
RESULTS=""

if [[ "$SKIP_SSIM" == "true" ]]; then
  echo -e "${YELLOW}▸ SSIM comparison skipped (SKIP_SSIM=true)${NC}"
  echo "  Frames extracted for manual review at:"
  echo "    Ref:  $OUT_DIR/ref-frames/"
  echo "    Impl: $OUT_DIR/impl-frames/"
elif [[ "$TIMING_ONLY" == "true" ]]; then
  echo -e "${YELLOW}▸ Timing-only mode — no pixel comparison${NC}"
else
  echo -e "${BOLD}▸ Running SSIM comparison (threshold=$SSIM_THRESHOLD)...${NC}"

  for i in $(seq -f "%06g" 1 "$MIN_COUNT"); do
    REF_FRAME="$OUT_DIR/ref-frames/f-${i}.png"
    IMPL_FRAME="$OUT_DIR/impl-frames/f-${i}.png"

    if [[ ! -f "$REF_FRAME" ]] || [[ ! -f "$IMPL_FRAME" ]]; then
      continue
    fi

    SSIM=$(ffmpeg -i "$REF_FRAME" -i "$IMPL_FRAME" -lavfi "ssim" -f null - 2>&1 | grep -oE 'All:[0-9.]+' | cut -d: -f2 || echo "0")
    [[ -z "$SSIM" ]] && SSIM="0"

    IS_PASS=$(awk -v a="$SSIM" -v b="$SSIM_THRESHOLD" 'BEGIN{print (a+0 >= b+0) ? 1 : 0}')

    if [[ "$IS_PASS" -eq 1 ]]; then
      PASS=$((PASS + 1))
    else
      FAIL=$((FAIL + 1))
      compare -metric AE "$REF_FRAME" "$IMPL_FRAME" "$OUT_DIR/diff-frames/f-${i}.png" 2>/dev/null || true
      RESULTS="${RESULTS}| f-${i} | ${SSIM} | ❌ |\n"
    fi
  done
fi

# ── Phase 5: Output results ──
echo ""
echo -e "${BOLD}═══ Results ═══${NC}"
echo "Total frames compared: $MIN_COUNT"
echo -e "Pass: ${GREEN}${PASS}${NC}, Fail: ${RED}${FAIL}${NC}"
echo ""

if [[ "$FAIL" -gt 0 ]]; then
  echo "| Frame | SSIM | Status |"
  echo "|-------|------|--------|"
  echo -e "$RESULTS"
  echo ""
  echo "Diff images saved to: $OUT_DIR/diff-frames/"
  echo -e "${YELLOW}Investigate FAIL frames by reading diff images.${NC}"
fi

# Save results
cat > "$OUT_DIR/result.txt" << ENDRESULT
Transition Compare Results
==========================
Original: $ORIG_URL
Implementation: $IMPL_URL
Action: $ACTION
Total frames: $MIN_COUNT
Pass: $PASS
Fail: $FAIL
Threshold: $SSIM_THRESHOLD

$(echo -e "$RESULTS")
ENDRESULT

if [[ "$FAIL" -eq 0 ]]; then
  echo -e "${GREEN}ALL PASS${NC} — transition matches original"
  exit 0
else
  echo -e "${RED}${FAIL} FAIL${NC} — transition differs from original"
  exit 1
fi
