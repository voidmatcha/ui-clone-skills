#!/usr/bin/env bash
# asset-placement-check.sh — verify visible assets are referenced by the
# component mapped to the section where the ref renders them.
#
# This is stricter than global asset-utilization: a basename appearing anywhere
# in impl/src is not proof that the asset appears in its original section.
#
# Usage: asset-placement-check.sh <ref-dir> [<impl-root-or-src-dir>]
#
# Reads:
#   <ref-dir>/visible-images.json
#   <ref-dir>/section-map.json
#   <ref-dir>/component-map.json
#
# Writes:
#   <ref-dir>/asset-placement.json

set -euo pipefail

REF_DIR="${1:?Usage: asset-placement-check.sh <ref-dir> [<impl-root-or-src-dir>]}"
[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

IMPL_ARG="${2:-}"
if [ -z "$IMPL_ARG" ]; then
  CANDIDATES=(
    "$(dirname "$REF_DIR")/../impl"
    "$(dirname "$REF_DIR")/impl"
    "apps/$(basename "$REF_DIR")"
    "app"
    "."
  )
  for c in "${CANDIDATES[@]}"; do
    if [ -d "$c/src" ] || [ -d "$c/app" ] || [ -d "$c/pages" ]; then
      IMPL_ARG="$c"
      break
    fi
  done
fi

OUT="$REF_DIR/asset-placement.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 "$SCRIPT_DIR/asset_placement_check.py" "$REF_DIR" "$IMPL_ARG" "$OUT"
