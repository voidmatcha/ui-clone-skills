#!/usr/bin/env bash
# ae-compare.sh — Compare two images using ImageMagick AE metric
# Usage: bash ae-compare.sh <ref.png> <impl.png> [diff-output.png]
#
# Output: AE=<number> STATUS=<PASS|FAIL|ERROR> REGION=<description>
# Exit code: 0 = PASS, 1 = FAIL, 2 = ERROR (missing tool/file, unreadable or
# corrupt image, non-numeric compare output). A compare error never PASSes.
#
# Also identifies which region of the image has the most differences
# (top/middle/bottom thirds) to help targeted debugging.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ImageMagick 7 HDRI builds report AE as count*QuantumRange; normalize to a raw
# pixel count exactly like batch-compare.sh / section-compare.sh do.
# shellcheck source=lib/ae-quantum.sh
. "$SCRIPT_DIR/lib/ae-quantum.sh"

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

# _ae_raw <compare args...> — run `compare -metric AE`, echo the raw numeric AE.
# Returns 2 when compare errored (exit >= 2) or printed a non-numeric first
# token, so a corrupt/unreadable image can never be read as AE=0.
_ae_raw() {
  local out rc first
  out=$(compare -metric AE "$@" 2>&1)
  rc=$?
  first=$(printf '%s\n' "$out" | head -1 | awk '{print $1}')
  if [ "$rc" -ge 2 ] || ! [[ "$first" =~ ^[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$ ]]; then
    printf 'ae-compare: compare failed (exit %s): %s\n' "$rc" "$(printf '%s\n' "$out" | head -3)" >&2
    return 2
  fi
  printf '%s\n' "$first"
}

# Check image dimensions match (and that both images are readable at all)
REF_SIZE=$(identify -format "%wx%h" "$REF" 2>/dev/null)
IMPL_SIZE=$(identify -format "%wx%h" "$IMPL" 2>/dev/null)
if ! [[ "$REF_SIZE" =~ ^[0-9]+x[0-9]+$ ]]; then
  echo "AE=NA STATUS=ERROR REASON=unreadable-ref-image"
  exit 2
fi
if ! [[ "$IMPL_SIZE" =~ ^[0-9]+x[0-9]+$ ]]; then
  echo "AE=NA STATUS=ERROR REASON=unreadable-impl-image"
  exit 2
fi

if [ "$REF_SIZE" != "$IMPL_SIZE" ]; then
  echo "WARN: Size mismatch ref=$REF_SIZE impl=$IMPL_SIZE — resizing impl to match"
  W=$(echo "$REF_SIZE" | cut -dx -f1)
  H=$(echo "$REF_SIZE" | cut -dx -f2)
  # L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
  RESIZED="$(mktemp /tmp/ae-compare-XXXXXX)"
  mv "$RESIZED" "${RESIZED}.png"
  RESIZED="${RESIZED}.png"
  # shellcheck disable=SC2064
  trap "rm -f '$RESIZED'" EXIT
  if ! convert "$IMPL" -resize "${W}x${H}!" "$RESIZED"; then
    echo "AE=NA STATUS=ERROR REASON=resize-failed"
    exit 2
  fi
  IMPL="$RESIZED"
fi

W_PX="${REF_SIZE%x*}"
H_PX="${REF_SIZE#*x}"

# Full image AE
if ! RAW_AE=$(_ae_raw "$REF" "$IMPL" "$DIFF"); then
  echo "AE=NA STATUS=ERROR REASON=compare-failed"
  exit 2
fi
AE=$(_ae_normalize "$RAW_AE" "$W_PX" "$H_PX")

if [ "$AE" -le "$THRESHOLD" ]; then
  echo "AE=$AE STATUS=PASS"
  exit 0
fi

# FAIL — identify which region differs most
# Split into top/middle/bottom thirds and compare each
THIRD=$((H_PX / 3))

REGION="full"
MAX_AE=0

for PART in top middle bottom; do
  case $PART in
    top)    CROP="${THIRD}+0+0" ;;
    middle) CROP="${THIRD}+0+${THIRD}" ;;
    bottom) CROP="${THIRD}+0+$((THIRD * 2))" ;;
  esac

  # Region attribution is advisory; a failed region compare counts as 0 but the
  # overall verdict is already FAIL.
  if RAW_PART_AE=$(_ae_raw \
    -extract "${W_PX}x${CROP}" "$REF" \
    -extract "${W_PX}x${CROP}" "$IMPL" \
    /dev/null); then
    PART_AE=$(_ae_normalize "$RAW_PART_AE" "$W_PX" "$THIRD")
  else
    PART_AE=0
  fi

  if [ "$PART_AE" -gt "$MAX_AE" ]; then
    MAX_AE=$PART_AE
    REGION=$PART
  fi
done

echo "AE=$AE STATUS=FAIL REGION=$REGION (${REGION} third has most differences: $MAX_AE)"
exit 1
