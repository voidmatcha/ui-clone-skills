#!/usr/bin/env bash
# batch-compare.sh — Compare all ref/impl screenshot pairs, output markdown table
# Usage: bash batch-compare.sh <dir> [threshold] [dynamic-regions.json]
#
# dynamic-regions.json format:
#   { "totalHeight": 11000, "regions": [
#     { "name": "hero-carousel", "scrollPct": [0, 9], "threshold": 50000, "reason": "auto-timer" },
#     { "name": "lottie-area", "scrollPct": [0, 100], "threshold": 10000, "reason": "lottie" }
#   ]}
#
# Expects: <dir>/static/ref/*.png and <dir>/static/impl/*.png
# with matching filenames (e.g., 0pct.png, 10pct.png, ...)
#
# Output: Markdown table of AE scores + PASS/FAIL status

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/ae-quantum.sh
. "$SCRIPT_DIR/lib/ae-quantum.sh"

DIR="${1:?Usage: batch-compare.sh <dir> [threshold] [dynamic-regions.json]}"
THRESHOLD="${THRESHOLD:-${2:-500}}"  # env var overrides positional arg
DYNAMIC_REGIONS="${3:-}"

REF_DIR="$DIR/static/ref"
IMPL_DIR="$DIR/static/impl"
DIFF_DIR="$DIR/static/diff"

if ! command -v compare &>/dev/null || ! command -v identify &>/dev/null; then
  echo "ERROR: ImageMagick not installed (need 'compare' and 'identify'). Run: brew install imagemagick"
  exit 2
fi

if [ ! -d "$REF_DIR" ]; then echo "ERROR: $REF_DIR not found"; exit 1; fi
if [ ! -d "$IMPL_DIR" ]; then echo "ERROR: $IMPL_DIR not found"; exit 1; fi

# Clean up resized temp files on exit
TMPFILES=()
cleanup() { rm -f "${TMPFILES[@]+"${TMPFILES[@]}"}" 2>/dev/null; }
trap cleanup EXIT

mkdir -p "$DIFF_DIR"

# Parse dynamic regions if provided
get_threshold_for_position() {
  local pos_pct="$1"
  if [ -z "$DYNAMIC_REGIONS" ] || [ ! -f "$DYNAMIC_REGIONS" ]; then
    echo "$THRESHOLD"
    return
  fi
  # Check if this position falls within any dynamic region
  local best_threshold="$THRESHOLD"
  while IFS= read -r line; do
    local start end thresh
    start=$(echo "$line" | jq -r '.scrollPct[0]' 2>/dev/null)
    end=$(echo "$line" | jq -r '.scrollPct[1]' 2>/dev/null)
    thresh=$(echo "$line" | jq -r '.threshold' 2>/dev/null)
    if [ -n "$start" ] && [ -n "$end" ] && [ -n "$thresh" ]; then
      if [ "$pos_pct" -ge "$start" ] && [ "$pos_pct" -le "$end" ]; then
        if [ "$thresh" -gt "$best_threshold" ]; then
          best_threshold="$thresh"
        fi
      fi
    fi
  done < <(jq -c '.regions[]' "$DYNAMIC_REGIONS" 2>/dev/null)
  echo "$best_threshold"
}

echo "| Position | AE | Threshold | Status | Region |"
echo "|----------|-----|-----------|--------|--------|"

PASS=0
FAIL=0
TOTAL=0

# Guard: no ref images
shopt -s nullglob
_ref_check=("$REF_DIR"/*.png)
shopt -u nullglob
if [ ${#_ref_check[@]} -eq 0 ]; then
  echo "ERROR: No PNG files found in $REF_DIR"
  echo "  Run batch-scroll.sh first to capture screenshots."
  exit 1
fi

for REF_FILE in "${_ref_check[@]}"; do
  BASENAME=$(basename "$REF_FILE")
  IMPL_FILE="$IMPL_DIR/$BASENAME"
  DIFF_FILE="$DIFF_DIR/$BASENAME"
  POS="${BASENAME%.png}"

  # Extract numeric percentage from position name
  POS_PCT=$(echo "$POS" | grep -oE '[0-9]+' | head -1)
  POS_PCT="${POS_PCT:-0}"

  if [ ! -f "$IMPL_FILE" ]; then
    echo "| $POS | — | — | ⚠️ MISSING | impl not found |"
    FAIL=$((FAIL + 1))
    TOTAL=$((TOTAL + 1))
    continue
  fi

  # Get position-specific threshold
  POS_THRESHOLD=$(get_threshold_for_position "$POS_PCT")

  # Check size match, resize if needed
  REF_SIZE=$(identify -format "%wx%h" "$REF_FILE" 2>/dev/null)
  IMPL_SIZE=$(identify -format "%wx%h" "$IMPL_FILE" 2>/dev/null)
  W_PX="${REF_SIZE%x*}"
  H_PX="${REF_SIZE#*x}"

  COMPARE_IMPL="$IMPL_FILE"
  if [ "$REF_SIZE" != "$IMPL_SIZE" ]; then
    W=$(echo "$REF_SIZE" | cut -dx -f1)
    H=$(echo "$REF_SIZE" | cut -dx -f2)
    # L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
    RESIZED="$(mktemp /tmp/batch-compare-XXXXXX)"
    mv "$RESIZED" "${RESIZED}.png"
    RESIZED="${RESIZED}.png"
    convert "$IMPL_FILE" -resize "${W}x${H}!" "$RESIZED" 2>/dev/null
    TMPFILES+=("$RESIZED")
    COMPARE_IMPL="$RESIZED"
  fi

  # AE diff
  RAW_AE=$(compare -metric AE "$REF_FILE" "$COMPARE_IMPL" "$DIFF_FILE" 2>&1 || true)
  RAW_AE=$(echo "$RAW_AE" | awk '{print $1}')
  AE=$(_ae_normalize "${RAW_AE:-0}" "$W_PX" "$H_PX")

  THRESH_LABEL="$POS_THRESHOLD"
  if [ "$POS_THRESHOLD" -gt "$THRESHOLD" ]; then
    THRESH_LABEL="${POS_THRESHOLD}*"  # asterisk = dynamic threshold
  fi

  if [ "$AE" -le "$POS_THRESHOLD" ]; then
    echo "| $POS | $AE | $THRESH_LABEL | ✅ | — |"
    PASS=$((PASS + 1))
  else
    # Identify worst region
    THIRD=$((H_PX / 3))
    WORST=""
    WORST_AE=0
    for PART in top mid bot; do
      case $PART in
        top) OFF=0 ;;
        mid) OFF=$THIRD ;;
        bot) OFF=$((THIRD * 2)) ;;
      esac
      RAW_PAE=$(compare -metric AE \
        -extract "${W_PX}x${THIRD}+0+${OFF}" "$REF_FILE" \
        -extract "${W_PX}x${THIRD}+0+${OFF}" "$COMPARE_IMPL" \
        /dev/null 2>&1 || true)
      RAW_PAE=$(echo "$RAW_PAE" | awk '{print $1}')
      PAE=$(_ae_normalize "${RAW_PAE:-0}" "$W_PX" "$THIRD")
      if [ "$PAE" -gt "$WORST_AE" ]; then WORST_AE=$PAE; WORST=$PART; fi
    done
    echo "| $POS | $AE | $THRESH_LABEL | ❌ | $WORST ($WORST_AE) |"
    FAIL=$((FAIL + 1))
  fi

  TOTAL=$((TOTAL + 1))
done

echo ""
echo "**Result: $PASS/$TOTAL PASS, $FAIL FAIL** (base threshold=$THRESHOLD)"
if [ -n "$DYNAMIC_REGIONS" ] && [ -f "$DYNAMIC_REGIONS" ]; then
  echo "Dynamic regions applied from: $DYNAMIC_REGIONS (* = adjusted threshold)"
fi

if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "Investigate FAIL positions by reading diff images:"
  echo "\`\`\`"
  shopt -s nullglob
  for DIFF_FILE in "$DIFF_DIR"/*.png; do
    echo "Read $DIFF_FILE   # only if ❌ above"
  done
  shopt -u nullglob
  echo "\`\`\`"

  echo ""
  echo "═══ MANDATORY DIAGNOSIS ═══"
  echo "⛔ $FAIL position(s) FAILED. Each FAIL must be diagnosed before proceeding."
  echo ""
  echo "DO NOT rationalize FAILs as 'content differences' or 'expected mismatch'."
  echo "DO NOT proceed to declare any section 'done' until all FAILs are resolved or diagnosed."
  echo ""
  echo "For each FAIL position:"
  echo "  1. Read the diff image (listed above)"
  echo "  2. Run layout health check:"
  echo "     bash \"\$(dirname \"\$0\")/layout-health-check.sh\" <session> <orig-url> <impl-url>"
  echo "  3. Run computed-diff on elements in the failing region:"
  echo "     bash \"\$(dirname \"\$0\")/computed-diff.sh\" <session> <orig-url> <impl-url> <selectors>"
  echo ""
  echo "Only after all FAILs have documented root causes can verification proceed."

  exit 1
fi

exit 0
