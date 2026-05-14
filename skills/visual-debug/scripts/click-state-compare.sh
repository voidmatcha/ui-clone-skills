#!/usr/bin/env bash
# click-state-compare.sh — 60fps video compare per click-driven state transition.
#
# Why this exists:
#   Static hover-compare and section-compare both verify resting frames.
#   Click-driven UI (tabs, accordions, modals, hamburger menus, content swaps)
#   has its own motion arc — open/close timing, panel entry/exit curves,
#   stacking-order swap. None of the existing verification rows exercise it.
#
# Reads regions.json for entries whose triggerType begins with "click-"
# (click-toggle / click-cycle / click-content-swap), and runs
# scripts/verify/video-transition-compare.sh in "click:<selector>" mode
# for each. Caps at MAX_CLICK_TARGETS to keep CI runs bounded — pick the
# first N selectors in document order; capture-transitions.md already
# dedupes/filters before saving, so first-N is a reasonable sample.
#
# Usage:
#   bash click-state-compare.sh <orig-url> <impl-url> <session> <ref-dir>
#
# Env:
#   MAX_CLICK_TARGETS=5    — cap on click targets evaluated (default 5)
#   VIEWPORTS=""           — comma-separated WxH list (e.g.
#                            "375x812,1920x1080"). When set, the target loop
#                            runs once per viewport; results land in
#                            <ref-dir>/transitions/click-state/<WxH>/<name>/.
#                            Empty = single-viewport (back-compat). Modals
#                            and menus often differ across breakpoints (mobile
#                            full-screen sheet vs desktop floating panel) —
#                            comprehensive-tier callers should fan out to
#                            catch the responsive divergence the inner sweep
#                            otherwise misses.
#
# Output: <ref-dir>/transitions/click-state-result.txt (❌ on any failure)

set -uo pipefail

MAX_CLICK_TARGETS="${MAX_CLICK_TARGETS:-5}"
VIEWPORTS="${VIEWPORTS:-}"

ORIG_URL="${1:?Usage: click-state-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"
IMPL_URL="${2:?Usage: click-state-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"
SESSION="${3:?Usage: click-state-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"
REF_DIR="${4:?Usage: click-state-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"

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
OUT_DIR="$REF_DIR/transitions/click-state"
mkdir -p "$OUT_DIR"
RESULT="$REF_DIR/transitions/click-state-result.txt"

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
{
  echo "# click-state-compare"
  echo "# generated: $NOW"
  echo "# max targets: $MAX_CLICK_TARGETS"
  echo
} > "$RESULT"

if [ ! -f "$REGIONS" ]; then
  echo "✅ no regions.json — click-state compare skipped (verification-plan should not have required this row)" >> "$RESULT"
  echo "Wrote $RESULT"
  exit 0
fi

# Extract (name, selector) pairs whose triggerType starts with "click-".
# Walk both top-level bucket arrays and a generic top-level array — regions.json
# uses typed buckets for hover/scroll/etc. but click captures may land under a
# `click` key or under the merged set, so we use a tolerant jq path.
TARGETS_FILE="$(mktemp)"
trap 'rm -f "$TARGETS_FILE"' EXIT

if command -v jq >/dev/null 2>&1; then
  jq -r '
    [.. | objects | select(.triggerType? | type == "string") | select(.triggerType | startswith("click-"))]
    | unique_by(.selector)
    | .[0:'"$MAX_CLICK_TARGETS"']
    | .[]
    | "\(.name // .triggerType)\t\(.triggerType)\t\(.selector)"
  ' "$REGIONS" > "$TARGETS_FILE" 2>/dev/null || true
fi

if [ ! -s "$TARGETS_FILE" ]; then
  echo "✅ no click-* regions found in regions.json — nothing to compare" >> "$RESULT"
  echo "Wrote $RESULT"
  exit 0
fi

FAIL_COUNT=0
RUN_COUNT=0

# Build viewport list. Empty VIEWPORTS = single iteration (back-compat).
# Non-empty = comma-separated WxH; one outer-loop pass per entry.
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

    if bash "$COMPARE" "$SESSION-cs${VP_SESSION_SUFFIX}-$RUN_COUNT" "$ORIG_URL" "$IMPL_URL" \
         "$TARGET_DIR" "click:$SELECTOR" >> "$RESULT" 2>&1; then
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
    echo "✅ all $RUN_COUNT click target-run(s) within SSIM threshold"
  else
    echo "❌ $FAIL_COUNT/$RUN_COUNT click target-run(s) diverged"
  fi
} >> "$RESULT"

echo "Wrote $RESULT"
exit 0
