#!/usr/bin/env bash
# scaffold-to-jsx.sh — deterministic transpiler: structure.json → JSX components.
#
# Reads <ref-dir>/structure.json (with Fix 13 per-node `styles` and Fix 6 v1
# `text` fields) and <ref-dir>/section-map.json, emits one component file per
# section into <impl-dir>/src/components/<Name>.tsx with verbatim text,
# verbatim inline styles, and tag-preserving JSX.
#
# Usage:
#   scaffold-to-jsx.sh <ref-dir> <impl-dir> [--out-dir <impl/src/components>]
#
# Writes one .tsx per ref section. Idempotent — re-running overwrites.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REF_DIR=""
IMPL_DIR=""
OUT_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out-dir) OUT_DIR="$2"; shift 2;;
    -h|--help) sed -n '2,12p' "$0"; exit 0;;
    *)
      if [[ -z "$REF_DIR" ]]; then REF_DIR="$1"
      elif [[ -z "$IMPL_DIR" ]]; then IMPL_DIR="$1"
      else echo "scaffold-to-jsx: unexpected arg: $1" >&2; exit 2
      fi
      shift
      ;;
  esac
done

if [[ -z "$REF_DIR" || -z "$IMPL_DIR" ]]; then
  echo "usage: scaffold-to-jsx.sh <ref-dir> <impl-dir> [--out-dir <path>]" >&2
  exit 2
fi
if [[ ! -d "$REF_DIR" ]]; then
  echo "scaffold-to-jsx: ref dir not found: $REF_DIR" >&2; exit 2
fi
if [[ -z "$OUT_DIR" ]]; then
  OUT_DIR="$IMPL_DIR/src/components"
fi
mkdir -p "$OUT_DIR"

# Prefer the reconciled structure.merged.json (spec-target reconciliation)
# when present; fall back to the raw structure.json.
if [[ -f "$REF_DIR/structure.merged.json" ]]; then
  STRUCT="$REF_DIR/structure.merged.json"
else
  STRUCT="$REF_DIR/structure.json"
fi
SECMAP="$REF_DIR/section-map.json"
if [[ ! -f "$STRUCT" ]]; then
  echo "scaffold-to-jsx: structure.json missing — run Phase 2 first" >&2; exit 2
fi

python3 "$SCRIPT_DIR/lib/scaffold_to_jsx.py" "$STRUCT" "$SECMAP" "$OUT_DIR"

# Emit deterministic scroll helpers. Runs unconditionally: the emitter also
# writes helpers the scaffold above already committed to MOUNTING (derived from
# transition-spec), which a ref with no generation-plan.json still produces.
# Gating this on the plan's existence is what left those mounts unresolvable.
bash "$SCRIPT_DIR/emit-scroll-helpers.sh" "$REF_DIR" "$IMPL_DIR"

# Re-gate emitted imports against what was ACTUALLY written. Must run here:
# after helper emission, so file existence is ground truth rather than a
# prediction. Each driver is decided by two independent predicates (scaffold
# imports it / emitter writes it) and a disagreement produces a tree that
# cannot build — which is how a 35/35-PASS reference shipped an unresolvable
# ./lib/ScrollLatchDriver with nothing in the pipeline noticing. Fails the run
# on any unresolved import that is not a known unwritten driver.
python3 "$SCRIPT_DIR/lib/regate_unresolved_imports.py" "$IMPL_DIR"

# Download referenced assets into impl/public when a manifest is available.
_REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." 2>/dev/null && pwd || true)"
_ASSET_DL="${_REPO_ROOT:-}/scripts/extract/asset-download.sh"
if [[ "${UI_CLONE_SKIP_ASSET_DOWNLOAD:-0}" != "1" &&
      -f "$REF_DIR/visible-images.json" && -f "$_ASSET_DL" ]]; then
  bash "$_ASSET_DL" "$REF_DIR" "$IMPL_DIR/public" >/dev/null 2>&1 || true
fi
