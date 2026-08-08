#!/usr/bin/env bash
# sanitize-ref-css.sh — copy captured reference CSS into impl/src/ref-css safely.
#
# Usage:
#   bash scripts/extract/sanitize-ref-css.sh <ref-dir> <impl-root> [copy-to]
#
# Copies <ref-dir>/css/*.css to <impl-root>/<copy-to> (default: src/ref-css),
# preserving filenames, while repairing known browser-tolerated but Vite-strict
# tokens observed in production bundles. Writes a provenance report to
# <ref-dir>/ref-css-sanitize-report.json.
set -euo pipefail

REF_DIR="${1:-}"
IMPL_ROOT="${2:-}"
COPY_TO="${3:-src/ref-css}"

if [ -z "$REF_DIR" ] || [ -z "$IMPL_ROOT" ]; then
  echo "Usage: $0 <ref-dir> <impl-root> [copy-to]" >&2
  exit 2
fi
if [ ! -d "$REF_DIR" ]; then
  echo "sanitize-ref-css: ref-dir not found: $REF_DIR" >&2
  exit 2
fi
if [ ! -d "$REF_DIR/css" ]; then
  echo "sanitize-ref-css: no ref css dir: $REF_DIR/css" >&2
  exit 2
fi

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$_SCRIPT_DIR/sanitize_ref_css.py" "$REF_DIR" "$IMPL_ROOT" "$COPY_TO"
