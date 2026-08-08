#!/usr/bin/env bash
# ae-quantum.sh — normalize ImageMagick `compare -metric AE` back to a raw
# mismatched-pixel COUNT.
#
# ROOT CAUSE (2026-07, Fable/empirical): ImageMagick 7.1.2-27 Q16-HDRI (brew,
# upgraded 2026-07-12) returns `compare -metric AE` as pixel_count * QuantumRange
# (= count * 65535 on Q16), NOT the raw count that every AE parser in this repo
# assumes. The 65535x inflation pushes every nonzero diff past the "saturated"
# severity band, killing the whole AE gradient (every ebpb run since 2026-07-16
# read "all saturated" — the report became uninformative and iteration blind).
#
# This helper detects the scale factor by BEHAVIOR (a synthetic 2x2 white/black
# compare has exactly 4 differing pixels) so it self-corrects if a future IM
# build reverts to raw counts (divisor 1) or changes quantum depth (Q8 -> 255).
# It caches the divisor per shell. `_ae_normalize` divides and FAILS LOUD when a
# normalized AE still exceeds the pixel budget — the tripwire for the NEXT time
# ImageMagick's metric behavior changes.

_AE_QUANTUM_DIVISOR=""

_ae_quantum_divisor() {
  if [ -n "$_AE_QUANTUM_DIVISOR" ]; then
    printf '%s\n' "$_AE_QUANTUM_DIVISOR"
    return 0
  fi
  local w b raw div have_magick=0
  if command -v magick >/dev/null 2>&1; then
    have_magick=1
  elif ! command -v compare >/dev/null 2>&1; then
    _AE_QUANTUM_DIVISOR="1"
    printf '1\n'
    return 0
  fi
  w="$(mktemp -u).png"
  b="$(mktemp -u).png"
  if [ "$have_magick" = "1" ]; then
    magick -size 2x2 xc:white "$w" 2>/dev/null
    magick -size 2x2 xc:black "$b" 2>/dev/null
    raw="$(magick compare -metric AE "$w" "$b" null: 2>&1 | head -1 | awk '{print $1}')"
  else
    convert -size 2x2 xc:white "$w" 2>/dev/null
    convert -size 2x2 xc:black "$b" 2>/dev/null
    raw="$(compare -metric AE "$w" "$b" null: 2>&1 | head -1 | awk '{print $1}')"
  fi
  rm -f "$w" "$b" 2>/dev/null || true
  # 4 differing pixels expected. divisor = round(raw / 4), floored at 1.
  div="$(awk -v r="$raw" 'BEGIN{
    if (r == "" || r+0 <= 4) { print 1 }
    else { printf "%.0f", r/4 }
  }')"
  case "$div" in
    ''|*[!0-9]*) div=1 ;;
  esac
  [ "$div" -lt 1 ] 2>/dev/null && div=1
  _AE_QUANTUM_DIVISOR="$div"
  printf '%s\n' "$div"
}

# _ae_normalize <raw_ae> [<width> <height>]
# Echoes the pixel-count-normalized AE. When width+height are given, asserts the
# normalized value does not exceed the pixel budget (allow 1% slack for fuzz/AA)
# and warns loudly to stderr if it does — a normalized AE above w*h means the
# divisor is wrong (IM behavior changed again).
_ae_normalize() {
  local raw="$1" w="${2:-}" h="${3:-}" div px
  div="$(_ae_quantum_divisor)"
  px="$(awk -v r="$raw" -v d="$div" 'BEGIN{
    if (r == "" || d+0 == 0) { print 0 } else { printf "%.0f", (r+0)/d }
  }')"
  if [ -n "$w" ] && [ -n "$h" ]; then
    awk -v p="$px" -v w="$w" -v h="$h" 'BEGIN{ exit (p > w*h*1.01) ? 1 : 0 }' || \
      printf 'ae-quantum: WARNING normalized AE %s exceeds pixel budget %sx%s — ImageMagick AE unit may have changed again (divisor=%s)\n' \
        "$px" "$w" "$h" "$div" >&2
  fi
  printf '%s\n' "$px"
}
