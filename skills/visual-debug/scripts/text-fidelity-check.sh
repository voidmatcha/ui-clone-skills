#!/usr/bin/env bash
# text-fidelity-check.sh — block Phase-4 fabrication of visible text.
#
# Compares JSX text-position strings in `<impl>/src/**/*.{tsx,jsx}` against the
# verbatim text in `<ref>/dom-scaffold.json` (Fix 8), supplemented by
# `<ref>/element-roles.json` when the runtime exposes richer rendered text.
# Any string the impl renders in a JSX text position that is NOT in the ref
# allowlist is flagged as fabrication. Any meaningful non-overlay scaffold
# text that the impl omits is flagged as missing. Either condition fails the
# gate.
#
# This closes the failure mode where Phase 4 invents text like "Eat Real
# Food" / "Dietary Guidelines" when ref says "Real Food Wins" / "America is
# the greatest country on Earth". Pattern is from Design2Code (arXiv:
# 2403.03163) — constrain-then-verify: extract ground truth as an allowlist,
# post-validate generated code against it, fail-fast on drift.
#
# Usage:
#   text-fidelity-check.sh <ref-dir> <impl-dir> [--out <json>]
#
# Exit 0 on pass (no fabrication and no missing source text), 1 on fidelity
# failure, 2 on error.
set -euo pipefail

REF_DIR=""
IMPL_DIR=""
OUT_PATH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)
      OUT_PATH="$2"
      shift 2
      ;;
    -h|--help)
      sed -n '2,18p' "$0"
      exit 0
      ;;
    *)
      if [[ -z "$REF_DIR" ]]; then
        REF_DIR="$1"
      elif [[ -z "$IMPL_DIR" ]]; then
        IMPL_DIR="$1"
      else
        echo "text-fidelity: unexpected arg: $1" >&2
        exit 2
      fi
      shift
      ;;
  esac
done

if [[ -z "$REF_DIR" ]]; then
  echo "usage: text-fidelity-check.sh <ref-dir> [<impl-dir>] [--out <json>]" >&2
  exit 2
fi
if [[ -z "$IMPL_DIR" ]]; then
  # Auto-detect impl-dir from sibling of ref-dir (benchmark/work/<sha>/{ref,impl}).
  IMPL_DIR="$(cd "$(dirname "$REF_DIR")" && pwd)/impl"
fi
SCAFFOLD="$REF_DIR/dom-scaffold.json"
if [[ ! -f "$SCAFFOLD" ]]; then
  echo "text-fidelity: dom-scaffold.json missing — run dom-scaffold.sh first" >&2
  exit 2
fi
if [[ ! -d "$IMPL_DIR" ]]; then
  echo "text-fidelity: impl dir not found: $IMPL_DIR" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/text_fidelity_check.py" \
  "$SCAFFOLD" "$IMPL_DIR" "${OUT_PATH:-}"
