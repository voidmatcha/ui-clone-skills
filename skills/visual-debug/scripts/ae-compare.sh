#!/usr/bin/env bash
# ae-compare.sh — Compare two images using ImageMagick AE metric
# Usage: bash ae-compare.sh <ref.png> <impl.png> [diff-output.png]
#
# Output: AE=<number> STATUS=<PASS|FAIL> REGION=<description>
# Exit code: 0 = PASS, 1 = FAIL
#
# Also identifies which region of the image has the most differences
# (top/middle/bottom thirds) to help targeted debugging.

set -uo pipefail

if ! command -v compare &>/dev/null || ! command -v identify &>/dev/null; then
  echo "ERROR: ImageMagick not installed (need 'compare' and 'identify'). Run: brew install imagemagick"
  exit 2
fi

REF="${1:?Usage: ae-compare.sh <ref.png> <impl.png> [diff-output.png]}"
IMPL="${2:?Usage: ae-compare.sh <ref.png> <impl.png> [diff-output.png]}"
DIFF="${3:-/dev/null}"
THRESHOLD="${AE_THRESHOLD:-500}"

if [ ! -f "$REF" ]; then echo "ERROR: $REF not found"; exit 2; fi
if [ ! -f "$IMPL" ]; then echo "ERROR: $IMPL not found"; exit 2; fi

# Check image dimensions match
REF_SIZE=$(identify -format "%wx%h" "$REF" 2>/dev/null)
IMPL_SIZE=$(identify -format "%wx%h" "$IMPL" 2>/dev/null)

if [ "$REF_SIZE" != "$IMPL_SIZE" ]; then
  echo "WARN: Size mismatch ref=$REF_SIZE impl=$IMPL_SIZE — resizing impl to match"
  W=$(echo "$REF_SIZE" | cut -dx -f1)
  H=$(echo "$REF_SIZE" | cut -dx -f2)
  # L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
  RESIZED="$(mktemp /tmp/ae-compare-XXXXXX)"
  mv "$RESIZED" "${RESIZED}.png"
  RESIZED="${RESIZED}.png"
  trap "rm -f '$RESIZED'" EXIT
  convert "$IMPL" -resize "${W}x${H}!" "$RESIZED"
  IMPL="$RESIZED"
fi

# Full image AE
AE=$(compare -metric AE "$REF" "$IMPL" "$DIFF" 2>&1 || true)
AE=$(echo "$AE" | awk '{printf "%d", $1}')
AE="${AE:-0}"

if [ "$AE" -le "$THRESHOLD" ]; then
  echo "AE=$AE STATUS=PASS"
  exit 0
fi

# FAIL — identify which region differs most
# Split into top/middle/bottom thirds and compare each
H=$(identify -format "%h" "$REF" 2>/dev/null)
THIRD=$((H / 3))

REGION="full"
MAX_AE=0

for PART in top middle bottom; do
  case $PART in
    top)    CROP="${THIRD}+0+0" ;;
    middle) CROP="${THIRD}+0+${THIRD}" ;;
    bottom) CROP="${THIRD}+0+$((THIRD * 2))" ;;
  esac

  W=$(identify -format "%w" "$REF" 2>/dev/null)
  PART_AE=$(compare -metric AE \
    -extract "${W}x${CROP}" "$REF" \
    -extract "${W}x${CROP}" "$IMPL" \
    /dev/null 2>&1 || true)
  PART_AE=$(echo "$PART_AE" | awk '{printf "%d", $1}')
  PART_AE="${PART_AE:-0}"

  if [ "$PART_AE" -gt "$MAX_AE" ]; then
    MAX_AE=$PART_AE
    REGION=$PART
  fi
done

echo "AE=$AE STATUS=FAIL REGION=$REGION (${REGION} third has most differences: $MAX_AE)"
exit 1
