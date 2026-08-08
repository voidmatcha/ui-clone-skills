#!/usr/bin/env bash
# asset-download.sh — download media referenced by capture artifacts.
#
# Input:  tmp/ref/<component>/ — visible-images.json and/or required-media.json
# Output: tmp/ref/<component>/download-log.json + populated impl/public/
#
# Usage: asset-download.sh <ref-dir> <impl-public-dir>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}}}"
REF_DIR="${1:-}"
IMPL_PUBLIC="${2:-}"
if [[ -z "$REF_DIR" || -z "$IMPL_PUBLIC" ]]; then
  echo "Usage: $0 <ref-dir> <impl-public-dir>" >&2
  exit 2
fi
if [[ ! -d "$REF_DIR" ]]; then
  echo "ERROR: ref-dir not found: $REF_DIR" >&2
  exit 2
fi

VIS_IMG="$REF_DIR/visible-images.json"
REQUIRED_MEDIA="$REF_DIR/required-media.json"
if [[ ! -f "$VIS_IMG" && ! -f "$REQUIRED_MEDIA" ]]; then
  echo "▸ asset-download: SKIP — no visible-images.json or required-media.json in $REF_DIR"
  exit 0
fi

if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python3" ]]; then
    PYTHON_BIN="$VIRTUAL_ENV/bin/python3"
  elif [[ -x "$REPO_ROOT/.venv/bin/python3" ]]; then
    PYTHON_BIN="$REPO_ROOT/.venv/bin/python3"
  elif [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
  else
    PYTHON_BIN="$(command -v python3 || true)"
  fi
fi
if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "ERROR: cannot resolve a Python interpreter" >&2
  exit 2
fi
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
  echo "ERROR: asset-download requires Python >=3.11 (selected: $PYTHON_BIN)" >&2
  exit 2
fi

mkdir -p "$IMPL_PUBLIC"
"$PYTHON_BIN" "$SCRIPT_DIR/asset_download.py" \
  "$REF_DIR" "$IMPL_PUBLIC" "$REF_DIR/download-log.json"
