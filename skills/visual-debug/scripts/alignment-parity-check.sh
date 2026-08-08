#!/usr/bin/env bash
# alignment-parity-check.sh — inner-content horizontal alignment gate.
#
# Loop-9 regression class: a full-bleed footer whose SECTION rect is identical
# ref-vs-impl (left=0, full width) while the inner content column is baked to
# 1440-only pixel constants (+64px@1280 / -64px@1600 / -204px@1920 off-center).
# Self-relative AE crops and rect-only geometry checks are blind to this, so
# this gate consumes the per-viewport section enumeration (matches.json, both
# sides' rects + contentBox already recorded by section-compare.sh fan-out —
# zero extra browser time) and asserts:
#
#   (a) section-center: |refCenterOffset - implCenterOffset|
#         > max(16px, 1.25% vpW)            → fail
#       where centerOffset = sectionCenterX - documentElement.clientWidth/2
#   (b) contentbox-asym (ref-relative, never absolute):
#       |(implLeftGap-implRightGap) - (refLeftGap-refRightGap)| / 2
#         > max(12px, 1% refSectionWidth)   → fail
#
# Frozen-ref artifacts lacking contentBox fields on content-bearing sections
# are status=warn (unmeasurable) with an explicit "ref recapture needed"
# remediation — never a silent pass.
#
# Usage: alignment-parity-check.sh <ref-dir>
#
# Reads:
#   <ref-dir>/sections/viewports/*/sections/matches.json   (fan-out runs)
#   <ref-dir>/sections/matches.json                        (single-viewport fallback)
#
# Writes:
#   <ref-dir>/alignment-parity.json
#
# Exit:
#   0 pass/skip/warn, 1 fail, 2 setup error

set -euo pipefail

REF_DIR="${1:?Usage: alignment-parity-check.sh <ref-dir>}"
[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

OUT="$REF_DIR/alignment-parity.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 "$SCRIPT_DIR/alignment_parity_check.py" "$REF_DIR" "$OUT"
