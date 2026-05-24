#!/usr/bin/env bash
# bundle-extraction.sh — parse JS bundles for animation/scroll library
# parameters. Deterministic Python bounded parsing, no LLM judgment needed.
#
# Replaces the deleted .claude-plugin/agents/bundle-analyzer.md sub-agent
#
# Thin wrapper around scripts/extract/_bundle_extraction.py — that module
# owns the parsing logic so it can be unit-tested without spinning up the
# shell. See tests/test_bundle_extraction.py.
#
# Input:  tmp/ref/<component>/ — must contain bundles/ directory + bundle-map.json
# Output: tmp/ref/<component>/bundle-extraction.json
#
# Usage: bundle-extraction.sh <ref-dir>
set -euo pipefail

REF_DIR="${1:-}"
if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: $0 <ref-dir>" >&2
  exit 2
fi

BUNDLES_DIR="$REF_DIR/bundles"
if [ ! -d "$BUNDLES_DIR" ]; then
  echo "▸ bundle-extraction: SKIP — no bundles/ directory in $REF_DIR"
  exit 0
fi

OUT="$REF_DIR/bundle-extraction.json"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_MODULE="$SCRIPT_DIR/_bundle_extraction.py"

if [ ! -f "$PYTHON_MODULE" ]; then
  echo "✗ bundle-extraction: missing $PYTHON_MODULE" >&2
  exit 1
fi

python3 "$PYTHON_MODULE" "$REF_DIR" "$OUT"
