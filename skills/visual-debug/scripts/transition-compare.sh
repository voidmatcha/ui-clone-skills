#!/usr/bin/env bash
# transition-compare.sh — Compare hover/transition behavior between original and implementation
#
# Usage: bash transition-compare.sh <orig-url> <impl-url> <session> [output-dir]
#
# For each element with CSS transitions on both sites:
# 1. Captures idle state (screenshot + computedStyle)
# 2. Simulates hover (mouseenter dispatch)
# 3. Captures hover state (screenshot + computedStyle)
# 4. Diffs idle/hover computedStyle between ref and impl
# 5. Compares transition timing (duration, easing, delay)
#
# Output: <dir>/transitions/report.json
#         <dir>/transitions/{ref,impl}/{element}-{idle,hover}.png

set -euo pipefail

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
# shellcheck disable=SC1090  # runtime path is resolved from this script's directory
[ -f "$_SHIM" ] && . "$_SHIM" || true

VIEW_W="${VIEW_W:-1440}"
VIEW_H="${VIEW_H:-900}"
NO_IMAGES="${NO_IMAGES:-0}"
WAIT_REF="${WAIT_REF:-8000}"
WAIT_IMPL="${WAIT_IMPL:-6000}"
TRANSITION_WAIT="${TRANSITION_WAIT:-500}"   # ms to wait after hover before screenshot
SWIPER_SETTLE_WAIT="${SWIPER_SETTLE_WAIT:-50}"  # ms after pinning logical slide 0
MAX_TRANSITIONS="${MAX_TRANSITIONS:-30}"    # max semantic candidates to collect
COMPARE_LIMIT="${COMPARE_LIMIT:-20}"        # max top-priority candidates to hard-compare
# The ref list is the comparison shortlist. The impl list is a lookup pool, so
# giving both sides the same cap creates false MISSING results whenever helper
# classes or hydration change candidate ordering. Keep the impl pool bounded,
# but deliberately wider than the ref shortlist.
_DEFAULT_MAX_IMPL_TRANSITIONS=$((MAX_TRANSITIONS * 10))
if [ "$_DEFAULT_MAX_IMPL_TRANSITIONS" -lt 300 ]; then
  _DEFAULT_MAX_IMPL_TRANSITIONS=300
fi
MAX_IMPL_TRANSITIONS="${MAX_IMPL_TRANSITIONS:-$_DEFAULT_MAX_IMPL_TRANSITIONS}"
# CSS selector(s) to exclude from ref detection (e.g. third-party SDK overlays not in the clone).
# Default skips Finsweet Cookie Consent (`.fs-cc_*`) plus the CMP vendors from
# ui_clone.section_capture — the clone never replicates a consent SDK.
#
# The previous default also carried [id*=cookie], [class*=cookie-banner] and
# [class*=consent]. Those are role words, not vendor names: a page's own cookie
# or consent section was excluded from ref detection, so its transitions never
# entered the spec and the clone was never asked to implement them — a silent
# under-population rather than a reported gap.
_CMP_EXCLUDE=$(PYTHONPATH="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}" \
  python3 -m ui_clone.section_capture --print-cmp-selectors 2>/dev/null || echo "")
if [ -z "$_CMP_EXCLUDE" ]; then
  echo "transition-compare: cannot read CMP selectors from ui_clone.section_capture" >&2
  exit 2
fi
EXCLUDE_SELECTORS="${EXCLUDE_SELECTORS:-[class*=fs-cc], $_CMP_EXCLUDE}"

ORIG_URL="${1:?Usage: transition-compare.sh <orig-url> <impl-url> <session> [output-dir]}"
IMPL_URL="${2:?Usage: transition-compare.sh <orig-url> <impl-url> <session> [output-dir]}"
SESSION="${3:?Usage: transition-compare.sh <orig-url> <impl-url> <session> [output-dir]}"
DIR="${4:-tmp/ref/visual-debug}"

# Fail fast on swapped arguments. A URL passed as <session> becomes an invalid
# agent-browser session name whose daemon dies at startup with no stderr —
# "Daemon process exited during startup with no error output" gives the caller
# nothing to act on, so validate here instead.
case "$ORIG_URL" in
  http://*|https://*) ;;
  *) echo "transition-compare: <orig-url> must be http(s)://… (got: $ORIG_URL). Arg order is <orig-url> <impl-url> <session> [output-dir]." >&2; exit 2 ;;
esac
case "$IMPL_URL" in
  http://*|https://*) ;;
  *) echo "transition-compare: <impl-url> must be http(s)://… (got: $IMPL_URL). Arg order is <orig-url> <impl-url> <session> [output-dir]." >&2; exit 2 ;;
esac
case "$SESSION" in
  *[!A-Za-z0-9._-]*|"") echo "transition-compare: <session> must be a slug ([A-Za-z0-9._-]+, got: $SESSION). Arg order is <orig-url> <impl-url> <session> [output-dir]." >&2; exit 2 ;;
esac

# Convert relative path to absolute (Stop gate uses absolute paths, result.txt lookup breaks otherwise)
if [[ "$DIR" != /* ]]; then
  DIR="$(pwd)/$DIR"
fi

SESSION_REF="${SESSION}-tc-ref"
SESSION_IMPL="${SESSION}-tc-impl"

cleanup_all() {
  agent-browser --session "$SESSION_REF" close 2>/dev/null || true
  agent-browser --session "$SESSION_IMPL" close 2>/dev/null || true
  return 0
}
trap cleanup_all EXIT

mkdir -p "$DIR/transitions/ref" "$DIR/transitions/impl"

echo "═══ Transition Comparison ═══"
echo "Original: $ORIG_URL"
echo "Implementation: $IMPL_URL"
echo ""

# ── Open both sites ──
# `open` may report a navigation timeout on slow third-party sites even when the
# page eventually loads. Tolerate that — the explicit `wait` below settles state.
echo "▸ Opening both sites..."
ref_open_out="$(agent-browser --session "$SESSION_REF" open "$ORIG_URL" 2>&1 || true)"
printf '%s\n' "$ref_open_out" | head -1
impl_open_out="$(agent-browser --session "$SESSION_IMPL" open "$IMPL_URL" 2>&1 || true)"
printf '%s\n' "$impl_open_out" | head -1

# Navigation timeouts are tolerated (wait below settles state), but a session
# whose daemon never came up means every later eval silently no-ops — probe
# liveness once and surface the captured open output instead of cascading.
if ! agent-browser --session "$SESSION_REF" eval '(() => 1)()' > /dev/null 2>&1; then
  echo "transition-compare: ref session '$SESSION_REF' failed to start: $ref_open_out" >&2
  exit 1
fi
if ! agent-browser --session "$SESSION_IMPL" eval '(() => 1)()' > /dev/null 2>&1; then
  echo "transition-compare: impl session '$SESSION_IMPL' failed to start: $impl_open_out" >&2
  exit 1
fi

agent-browser --session "$SESSION_REF" set viewport "$VIEW_W" "$VIEW_H" > /dev/null 2>&1
agent-browser --session "$SESSION_IMPL" set viewport "$VIEW_W" "$VIEW_H" > /dev/null 2>&1

agent-browser --session "$SESSION_REF" wait "$WAIT_REF" > /dev/null 2>&1
agent-browser --session "$SESSION_IMPL" wait "$WAIT_IMPL" > /dev/null 2>&1

# Remove overlays
DISMISS='(() => {
  document.querySelectorAll("[class*=popup], [class*=modal], [class*=signup]").forEach(el => {
    const s = getComputedStyle(el);
    if (s.position === "fixed" || s.position === "absolute") el.remove();
  });
  document.body.style.overflow = "";
  document.documentElement.style.overflow = "";
  return "ok";
})()'
agent-browser --session "$SESSION_REF" eval "$DISMISS" > /dev/null 2>&1
agent-browser --session "$SESSION_IMPL" eval "$DISMISS" > /dev/null 2>&1

# Hide images to reduce AE noise from dynamic thumbnails
HIDE_IMAGES_JS='(() => {
  const style = document.createElement("style");
  style.id = "__no_images__";
  style.textContent = "img, picture, video, iframe { visibility: hidden !important; }";
  document.head.appendChild(style);
  document.querySelectorAll("*").forEach(el => {
    if (el.style && el.style.backgroundImage) el.style.backgroundImage = "none";
  });
})()'

# Hide <canvas> elements (WebGL/Three.js/etc.) — their content is dynamic per-frame.
HIDE_CANVAS_JS='(() => {
  const style = document.createElement("style");
  style.id = "__no_canvas__";
  style.textContent = "canvas { visibility: hidden !important; }";
  document.head.appendChild(style);
})()'

if [ "$NO_IMAGES" = "1" ]; then
  echo "▸ Hiding images (NO_IMAGES=1)..."
  agent-browser --session "$SESSION_REF" eval "$HIDE_IMAGES_JS" 2>/dev/null || true
  agent-browser --session "$SESSION_IMPL" eval "$HIDE_IMAGES_JS" 2>/dev/null || true
fi

if [ "${NO_CANVAS:-0}" = "1" ]; then
  echo "▸ Hiding canvases (NO_CANVAS=1)..."
  agent-browser --session "$SESSION_REF" eval "$HIDE_CANVAS_JS" 2>/dev/null || true
  agent-browser --session "$SESSION_IMPL" eval "$HIDE_CANVAS_JS" 2>/dev/null || true
fi

# Stop every initialized Swiper and align both pages to the same logical slide
# before transition candidates or idle styles are sampled. Carousel motion is
# verified by the transition-fires/video gates; this static comparison must not
# pair unrelated autoplay frames after its sequential ref/impl capture.
PIN_SWIPERS_JS='(() => {
  const seen = new Set();
  let pinned = 0;
  document.querySelectorAll("*").forEach(el => {
    const swiper = el.swiper;
    if (!swiper || seen.has(swiper)) return;
    seen.add(swiper);
    try {
      if (swiper.autoplay && typeof swiper.autoplay.stop === "function") {
        swiper.autoplay.stop();
      }
    } catch (_) {}
    try {
      if (typeof swiper.slideToLoop === "function") {
        swiper.slideToLoop(0, 0, false);
      } else if (typeof swiper.slideTo === "function") {
        swiper.slideTo(0, 0, false);
      }
      pinned++;
    } catch (_) {}
  });
  return pinned;
})()'

echo "▸ Pinning Swiper carousels..."
agent-browser --session "$SESSION_REF" eval "$PIN_SWIPERS_JS" > /dev/null
agent-browser --session "$SESSION_IMPL" eval "$PIN_SWIPERS_JS" > /dev/null
agent-browser --session "$SESSION_REF" wait "$SWIPER_SETTLE_WAIT" > /dev/null 2>&1
agent-browser --session "$SESSION_IMPL" wait "$SWIPER_SETTLE_WAIT" > /dev/null 2>&1

# ── Step 1: Find elements with transitions on the original ──
echo "▸ Detecting transition elements..."

DETECT_HELPER="$_SCRIPT_DIR/lib/transition-detect.js"
DETECT_TRANSITIONS_TEMPLATE=$(< "$DETECT_HELPER")

# Substitute runtime values into the standalone JS helper.
# EXCLUDE_SELECTORS is JSON-encoded so it embeds as a JS string literal safely —
# matches the v0.4.2 hardening discipline that JSON-encodes selectors before eval.
EXCLUDE_SELECTORS_JSON=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$EXCLUDE_SELECTORS")
DETECT_TRANSITIONS_TEMPLATE="${DETECT_TRANSITIONS_TEMPLATE/__EXCLUDE_SELECTORS_JSON__/$EXCLUDE_SELECTORS_JSON}"
DETECT_TRANSITIONS_REF="${DETECT_TRANSITIONS_TEMPLATE/__MAX_TRANSITIONS__/$MAX_TRANSITIONS}"
DETECT_TRANSITIONS_IMPL="${DETECT_TRANSITIONS_TEMPLATE/__MAX_TRANSITIONS__/$MAX_IMPL_TRANSITIONS}"

agent-browser --session "$SESSION_REF" eval "$DETECT_TRANSITIONS_REF" \
  > "$DIR/transitions/ref-elements.json" \
  2> "$DIR/transitions/ref-elements.stderr.log"
agent-browser --session "$SESSION_IMPL" eval "$DETECT_TRANSITIONS_IMPL" \
  > "$DIR/transitions/impl-elements.json" \
  2> "$DIR/transitions/impl-elements.stderr.log"

REF_TRANS=$(python3 -c "import json; print(len(json.loads(open('$DIR/transitions/ref-elements.json').read())))" 2>/dev/null || echo "0")
IMPL_TRANS=$(python3 -c "import json; print(len(json.loads(open('$DIR/transitions/impl-elements.json').read())))" 2>/dev/null || echo "0")

echo "  Ref:  $REF_TRANS transition elements"
echo "  Impl: $IMPL_TRANS transition elements"

if [ "$REF_TRANS" -eq 0 ]; then
  echo ""
  echo "  ℹ No transition elements detected on the original site."
  echo "  Possible causes:"
  echo "    1. All transitions are JS-driven (GSAP) — not in getComputedStyle at rest"
  echo "    2. Page not scrolled — transitions may be off-screen"
  echo "    3. Transitions only exist on hover (GSAP mouseenter), not in base CSS"
  echo "  If transitions exist, add custom selectors: bash transition-compare.sh ... then edit DETECT_TRANSITIONS"
  echo ""
  printf '%s\n' '[]' > "$DIR/transitions/report.json"
  printf '%s\n' \
    'Transition compare: 0 PASS, 0 FAIL' \
    'SKIP no transition elements detected on the original site.' \
    > "$DIR/transitions/result.txt"
  echo "═══ Transition Compare Complete ═══"
  echo "  0 elements — skipped"
  echo "  Summary: $DIR/transitions/result.txt"
  exit 0
fi

# ── Step 2: For each ref transition element, capture idle + hover states ──
echo "▸ Capturing idle + hover states..."

_TC_SESSION_REF="$SESSION_REF" _TC_SESSION_IMPL="$SESSION_IMPL" _TC_DIR="$DIR" \
  TRANSITION_WAIT="$TRANSITION_WAIT" COMPARE_LIMIT="$COMPARE_LIMIT" \
  python3 "$_SCRIPT_DIR/transition_capture_hover.py" 2>&1

# ── Step 3: Diff transitions ──
echo ""
echo "▸ Comparing transitions..."

python3 "$_SCRIPT_DIR/transition_compare_report.py" "$DIR" "$COMPARE_LIMIT" 2>&1

echo ""
echo "═══ Transition Compare Complete ═══"
echo "  Report:  $DIR/transitions/report.json"
echo "  Summary: $DIR/transitions/result.txt"
echo "  States:  $DIR/transitions/{ref,impl}/"

if grep -Eq '([1-9][0-9]*) FAIL|❌ FAIL' "$DIR/transitions/result.txt"; then
  exit 1
fi
