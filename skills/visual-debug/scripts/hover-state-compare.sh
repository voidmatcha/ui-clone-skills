#!/usr/bin/env bash
# hover-state-compare.sh — 60fps video compare per hover-driven motion arc.
#
# Why this exists:
#   transition-compare.sh captures idle/hover as two screenshots and AE-diffs
#   the resting frames. The motion arc between them — button-press scale curve,
#   icon-morph easing, color-transition velocity — is invisible to that check.
#   Same bug class as video-motion-compare for scroll/splash motion: an impl
#   with same end state but different easing or duration passes the static
#   check while still feeling wrong to a user.
#
# This script:
#   - Reads regions.json for entries with a hover triggerType.
#   - Caps targets at MAX_HOVER_TARGETS (capture-transitions.md already dedupes
#     before saving, so first-N in document order is a reasonable sample).
#   - Runs scripts/verify/video-transition-compare.sh in `hover:<selector>` mode
#     per target — real-mouse hover via agent-browser, recorded at 60fps,
#     frame-by-frame SSIM compare.
#
# Usage:
#   bash hover-state-compare.sh <orig-url> <impl-url> <session> <ref-dir>
#
# Env:
#   MAX_HOVER_TARGETS=5    — cap on hover targets evaluated (default 5)
#   HOVER_EXIT_CAPTURE=0   — set to 1 to use `hover-and-out:<sel>` mode, which
#                            records entry AND exit arcs in one video (total
#                            duration ≈ 2 × RECORD_DURATION). Off by default
#                            because most hover designs are symmetric (the exit
#                            transition reverses the entry curve), so the extra
#                            recording time rarely surfaces new bugs. Enable
#                            when the site uses Webflow IX2 "On Mouse Leave"
#                            handlers, distinct exit easing in the CSS, or a
#                            group-hover unwind chain that the entry sweep
#                            cannot exercise.
#   VIEWPORTS=""           — comma-separated WxH list (e.g.
#                            "375x812,1280x800,1920x1080"). When set, the
#                            target loop runs once per viewport; results land
#                            in <ref-dir>/transitions/hover-state/<WxH>/<name>/.
#                            Default empty = single-viewport (back-compat); the
#                            inner script's VIEW_W/VIEW_H apply. Comprehensive-
#                            tier callers should pass the four
#                            verification-plan.json viewports to catch
#                            responsive hover regressions (mobile has no
#                            :hover; tablet collapses hover-to-tap; desktop
#                            fires the actual arc).
#
# Output: <ref-dir>/transitions/hover-state-result.txt (❌ on any failure)

set -uo pipefail

# Source the timeout shim so macOS gets a working `timeout` cmd even when
# coreutils isn't installed. See scripts/lib/timeout-shim.sh.
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_SHIM="$_SCRIPT_DIR/../../../scripts/lib/timeout-shim.sh"
[ -f "$_SHIM" ] && . "$_SHIM" || true

MAX_HOVER_TARGETS="${MAX_HOVER_TARGETS:-5}"
HOVER_EXIT_CAPTURE="${HOVER_EXIT_CAPTURE:-0}"
VIEWPORTS="${VIEWPORTS:-}"

# Select hover action mode based on opt-in flag. video-transition-compare.sh
# accepts both `hover:<sel>` and `hover-and-out:<sel>`.
if [ "$HOVER_EXIT_CAPTURE" = "1" ]; then
  HOVER_MODE_PREFIX="hover-and-out"
else
  HOVER_MODE_PREFIX="hover"
fi

ORIG_URL="${1:?Usage: hover-state-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"
IMPL_URL="${2:?Usage: hover-state-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"
SESSION="${3:?Usage: hover-state-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"
REF_DIR="${4:?Usage: hover-state-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"

if [[ "$REF_DIR" != /* ]]; then
  REF_DIR="$(pwd)/$REF_DIR"
fi

PROJECT_ROOT="${PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${UI_CLONE_ROOT:-}}}}"
if [ -z "$PROJECT_ROOT" ]; then
  PROJECT_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
fi
COMPARE="$PROJECT_ROOT/scripts/verify/video-transition-compare.sh"

if [ ! -f "$COMPARE" ]; then
  echo "ERROR: video-transition-compare.sh not found at $COMPARE" >&2
  exit 2
fi

REGIONS="$REF_DIR/regions.json"
OUT_DIR="$REF_DIR/transitions/hover-state"
mkdir -p "$OUT_DIR"
RESULT="$REF_DIR/transitions/hover-state-result.txt"

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
{
  echo "# hover-state-compare"
  echo "# generated: $NOW"
  echo "# max targets: $MAX_HOVER_TARGETS"
  echo "# exit capture: $HOVER_EXIT_CAPTURE (mode: $HOVER_MODE_PREFIX:<sel>)"
  echo
} > "$RESULT"

if [ ! -f "$REGIONS" ]; then
  echo "✅ no regions.json — hover-state compare skipped (verification-plan should not have required this row)" >> "$RESULT"
  echo "Wrote $RESULT"
  exit 0
fi

# Extract (name, triggerType, selector) tuples where triggerType matches the
# hover family. Lenient regex like the verification-plan hasHover signal —
# matches "hover", "css-hover", "scale-on-hover-target", etc.
TARGETS_FILE="$(mktemp)"
trap 'rm -f "$TARGETS_FILE"' EXIT

if command -v jq >/dev/null 2>&1; then
  jq -r '
    [.. | objects | select(.triggerType? | type == "string") | select(.triggerType | test("[Hh]over"))]
    | unique_by(.selector)
    | .[0:'"$MAX_HOVER_TARGETS"']
    | .[]
    | "\(.name // .triggerType)\t\(.triggerType)\t\(.selector)"
  ' "$REGIONS" > "$TARGETS_FILE" 2>/dev/null || true
fi

if [ ! -s "$TARGETS_FILE" ]; then
  echo "✅ no hover regions found in regions.json — nothing to compare" >> "$RESULT"
  echo "Wrote $RESULT"
  exit 0
fi

FAIL_COUNT=0
RUN_COUNT=0

# Build viewport list. Empty VIEWPORTS = single iteration with no overrides
# (back-compat: VIEW_W/VIEW_H from caller environment, or video-transition-
# compare.sh defaults). Non-empty = comma-separated WxH; one outer-loop pass
# per entry with VIEW_W/VIEW_H exported so the inner compare uses it.
VP_LIST=()
if [ -n "$VIEWPORTS" ]; then
  IFS=',' read -ra VP_LIST <<< "$VIEWPORTS"
else
  VP_LIST=("")
fi

echo "# viewports: ${VIEWPORTS:-<single (caller VIEW_W/VIEW_H)>}" >> "$RESULT"
echo >> "$RESULT"

for VP in "${VP_LIST[@]}"; do
  VP_LABEL="single"
  VP_OUT_DIR="$OUT_DIR"
  VP_SESSION_SUFFIX=""
  if [ -n "$VP" ]; then
    VP_LABEL="$VP"
    VP_W="${VP%x*}"
    VP_H="${VP#*x}"
    if ! [[ "$VP_W" =~ ^[0-9]+$ ]] || ! [[ "$VP_H" =~ ^[0-9]+$ ]]; then
      echo "ERROR: malformed VIEWPORTS entry '$VP' (expected WxH)" >&2
      exit 2
    fi
    export VIEW_W="$VP_W" VIEW_H="$VP_H"
    VP_OUT_DIR="$OUT_DIR/$VP"
    VP_SESSION_SUFFIX="-${VP_W}x${VP_H}"
    mkdir -p "$VP_OUT_DIR"
    {
      echo "### viewport: ${VP_W}x${VP_H}"
      echo
    } >> "$RESULT"
  fi

  while IFS=$'\t' read -r NAME TTYPE SELECTOR; do
    [ -z "$SELECTOR" ] && continue
    RUN_COUNT=$((RUN_COUNT + 1))
    SAFE_NAME="${NAME//[^A-Za-z0-9_-]/_}"
    TARGET_DIR="$VP_OUT_DIR/$SAFE_NAME"
    mkdir -p "$TARGET_DIR"
    {
      echo "## $NAME ($TTYPE) [$VP_LABEL]"
      echo "selector: $SELECTOR"
      echo
    } >> "$RESULT"

    if bash "$COMPARE" "$SESSION-hs${VP_SESSION_SUFFIX}-$RUN_COUNT" "$ORIG_URL" "$IMPL_URL" \
         "$TARGET_DIR" "${HOVER_MODE_PREFIX}:$SELECTOR" >> "$RESULT" 2>&1; then
      echo "✅ $NAME clean [$VP_LABEL]" >> "$RESULT"
    else
      echo "❌ $NAME divergence [$VP_LABEL] — inspect $TARGET_DIR/diff-frames/" >> "$RESULT"
      FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
    echo >> "$RESULT"
  done < "$TARGETS_FILE"
done

{
  echo
  if [ "$FAIL_COUNT" -eq 0 ]; then
    echo "✅ all $RUN_COUNT hover target-run(s) within SSIM threshold"
  else
    echo "❌ $FAIL_COUNT/$RUN_COUNT hover target-run(s) diverged"
  fi
} >> "$RESULT"

echo "Wrote $RESULT"
exit 0
