#!/usr/bin/env bash
# required-media-coverage-check.sh — fail when ref's required video /
# Lottie assets are absent from impl public/ or unreferenced in impl src/.
#
#
# Inputs:
#   <ref-dir>/required-media.json    — produced by extract/required-media.sh
#
# Output: <ref-dir>/required-media-coverage.json
#   { status, implRoot, totals, missing: {video: [...], lottie: [...]} }
#
# Exit: 0 pass, 1 fail (missing transfers or references), 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_ROOT="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: required-media-coverage-check.sh <ref-dir> [<impl-root>]" >&2
  exit 2
fi

OUT_PATH="$REF_DIR/required-media-coverage.json"

if [ -z "$IMPL_ROOT" ]; then
  PLUGIN_ROOT_CAND="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}}"
  for cand_root in "$PLUGIN_ROOT_CAND" "$(cd "$(dirname "$0")/../../.." && pwd)"; do
    [ -z "$cand_root" ] && continue
    RESOLVER="$cand_root/scripts/extract/find-impl-root.sh"
    if [ -f "$RESOLVER" ]; then
      IMPL_ROOT=$(bash "$RESOLVER" "$REF_DIR" 2>/dev/null | head -1)
      [ -n "$IMPL_ROOT" ] && [ -d "$IMPL_ROOT" ] && break
    fi
  done
fi

IMPL_DIR_FIELD=""
IMPL_SRC_DIR_FIELD=""
IMPL_PUBLIC_DIR_FIELD=""
IMPL_PKG_JSON_FIELD=""
if [ -n "$IMPL_ROOT" ] && [ -d "$IMPL_ROOT" ]; then
  IMPL_DIR_FIELD="$IMPL_ROOT"
  if [ -d "$IMPL_ROOT/src" ]; then
    IMPL_SRC_DIR_FIELD="$IMPL_ROOT/src"
  elif [ -d "$IMPL_ROOT/app" ]; then
    IMPL_SRC_DIR_FIELD="$IMPL_ROOT/app"
  elif [ -d "$IMPL_ROOT/pages" ]; then
    IMPL_SRC_DIR_FIELD="$IMPL_ROOT/pages"
  else
    IMPL_SRC_DIR_FIELD="$IMPL_ROOT/src"
  fi
  IMPL_PUBLIC_DIR_FIELD="$IMPL_ROOT/public"
  IMPL_PKG_JSON_FIELD="$IMPL_ROOT/package.json"
fi

REQUIRED="$REF_DIR/required-media.json"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$REQUIRED" ]; then
  python3 "$SCRIPT_DIR/lib/required_media_coverage.py" --missing-required \
    "$OUT_PATH" "${IMPL_ROOT:-}" "$IMPL_DIR_FIELD" "$IMPL_SRC_DIR_FIELD" \
    "$IMPL_PUBLIC_DIR_FIELD" "$IMPL_PKG_JSON_FIELD"
  echo "required-media-coverage: fail (required-media.json missing — run extractor Step 6b-bis)"
  exit 1
fi

if [ -z "$IMPL_ROOT" ] || [ ! -d "$IMPL_ROOT" ]; then
  python3 "$SCRIPT_DIR/lib/required_media_coverage.py" --no-impl "$OUT_PATH"
  echo "required-media-coverage: skip (no impl)"
  exit 0
fi

python3 "$SCRIPT_DIR/lib/required_media_coverage.py" "$REF_DIR" "$IMPL_ROOT" "$OUT_PATH"
