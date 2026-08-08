#!/usr/bin/env bash
# dssim-compare.sh — Structural visual similarity comparison using DSSIM
#
# Usage: bash dssim-compare.sh <dir> [threshold]
#
# Complements ae-compare/batch-compare:
#   AE    = "how many pixels differ" (catches pixel-level errors)
#   DSSIM = "how structurally different" (catches layout/composition errors)
#
# DSSIM scale (from testing):
#   0.00       = identical files
#   0.01-0.20  = same content, minor rendering differences
#   0.20-0.50  = similar structure, noticeable content differences
#   0.50-0.80  = significant structural differences
#   0.80+      = completely different pages
#
# Use AFTER AE comparison to catch structural issues that AE misses
# (e.g., AE=1 but wrong section rendered due to matching colors)
#
# GOTCHA (docs/whole-page-dssim-viewport.md): capture the impl at the SAME pixel
# width as the ref. A width mismatch is "fixed" below by a non-uniform horizontal
# stretch (-resize WxH!) that shifts every column boundary and fabricates a
# registration penalty unrelated to fidelity. This script warns on a width
# mismatch; re-capture the impl at the ref width (agent-browser: `set viewport`).

set -uo pipefail

DIR="${1:?Usage: dssim-compare.sh <dir> [threshold]}"
THRESHOLD="${2:-0.50}"

REF_DIR="$DIR/static/ref"
IMPL_DIR="$DIR/static/impl"

if ! command -v dssim &>/dev/null; then
  echo "ERROR: dssim not installed. Run: brew install dssim"
  exit 2
fi

if [ ! -d "$REF_DIR" ]; then echo "ERROR: $REF_DIR not found"; exit 1; fi
if [ ! -d "$IMPL_DIR" ]; then echo "ERROR: $IMPL_DIR not found"; exit 1; fi

# Clean up resized temp files on exit
TMPFILES=()
# Guard the array expansion: under `set -u` on bash 3.2 (macOS default),
# "${TMPFILES[@]}" on an empty array is an "unbound variable" error that fires
# from the EXIT trap after the (valid) table has printed. Only expand when the
# array is non-empty.
cleanup() { [ "${#TMPFILES[@]}" -gt 0 ] && rm -f "${TMPFILES[@]}" 2>/dev/null; return 0; }
trap cleanup EXIT

echo "| Position | DSSIM | Threshold | Status |"
echo "|----------|-------|-----------|--------|"

PASS=0
FAIL=0
TOTAL=0
INVALID=0

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
  POS="${BASENAME%.png}"

  if [ ! -f "$IMPL_FILE" ]; then
    echo "| $POS | — | — | ⚠️ MISSING |"
    FAIL=$((FAIL + 1))
    TOTAL=$((TOTAL + 1))
    continue
  fi

  # Resize impl if dimensions differ
  REF_SIZE=$(identify -format "%wx%h" "$REF_FILE" 2>/dev/null)
  IMPL_SIZE=$(identify -format "%wx%h" "$IMPL_FILE" 2>/dev/null)
  COMPARE_IMPL="$IMPL_FILE"
  WIDTHMM=0  # per-row: 1 when the impl was stretched across a WIDTH mismatch
  if [ "$REF_SIZE" != "$IMPL_SIZE" ]; then
    W=$(echo "$REF_SIZE" | cut -dx -f1)
    H=$(echo "$REF_SIZE" | cut -dx -f2)
    IMPL_W=$(echo "$IMPL_SIZE" | cut -dx -f1)
    # A width mismatch means the impl was captured at a different viewport width
    # than the ref. The exact-dims resize below stretches it horizontally, which
    # distorts column/grid registration and fabricates a structural penalty.
    # See docs/whole-page-dssim-viewport.md. Vertical-only mismatch is expected.
    if [ -n "$IMPL_W" ] && [ "$IMPL_W" != "$W" ]; then
      WIDTHMM=1
      echo "WARNING: $POS impl width ${IMPL_W}px != ref width ${W}px — horizontal" >&2
      echo "  stretch will distort structural comparison. Re-capture impl at ${W}px" >&2
      echo "  wide (agent-browser: set viewport ${W} <h>). See docs/whole-page-dssim-viewport.md" >&2
    fi
    # L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
    RESIZED="$(mktemp /tmp/dssim-resized-XXXXXX)"
    mv "$RESIZED" "${RESIZED}.png"
    RESIZED="${RESIZED}.png"
    convert "$IMPL_FILE" -resize "${W}x${H}!" "$RESIZED" 2>/dev/null
    TMPFILES+=("$RESIZED")
    COMPARE_IMPL="$RESIZED"
  fi

  SCORE=$(dssim "$REF_FILE" "$COMPARE_IMPL" 2>/dev/null | awk '{print $1}')
  SCORE="${SCORE:-999}"

  # A width mismatch means the score came from a horizontally-stretched impl
  # (see the resize block): the number is a registration artifact, not a
  # fidelity signal, and must NOT be presented as an authoritative PASS/FAIL.
  # This is the trap behind the phantom-regression episode — a distorted low
  # score read as a real result. Flag the row invalid instead of scoring it.
  if [ "$WIDTHMM" -eq 1 ]; then
    echo "| $POS | $SCORE | $THRESHOLD | ⚠️ WIDTH-MISMATCH (invalid) |"
    INVALID=$((INVALID + 1))
    TOTAL=$((TOTAL + 1))
    continue
  fi

  # Use awk for float comparison
  STATUS=$(echo "$SCORE $THRESHOLD" | awk '{if ($1 <= $2) print "PASS"; else print "FAIL"}')

  if [ "$STATUS" = "PASS" ]; then
    echo "| $POS | $SCORE | $THRESHOLD | ✅ |"
    PASS=$((PASS + 1))
  else
    echo "| $POS | $SCORE | $THRESHOLD | ❌ |"
    FAIL=$((FAIL + 1))
  fi

  TOTAL=$((TOTAL + 1))
done

echo ""
echo "**Result: $PASS/$TOTAL PASS, $FAIL FAIL, $INVALID INVALID** (threshold=$THRESHOLD)"

if [ "$INVALID" -gt 0 ]; then
  echo ""
  echo "$INVALID comparison(s) INVALID: impl width != ref width, so the score is a"
  echo "horizontal-stretch registration artifact, not a fidelity signal. Re-capture"
  echo "the impl at the ref width (agent-browser: set viewport <ref-w> <h>) — e.g. via"
  echo "batch-scroll.sh with VIEW_W set to the ref width. See docs/whole-page-dssim-viewport.md."
fi

if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "DSSIM FAIL means structural/compositional mismatch — not just pixel noise."
  echo "Investigate: missing sections, wrong layout, content misalignment."
fi

# Non-zero exit on either a real structural FAIL or an INVALID (distorted) row,
# so a run whose numbers can't be trusted never silently reports success.
if [ "$FAIL" -gt 0 ] || [ "$INVALID" -gt 0 ]; then
  exit 1
fi

exit 0
