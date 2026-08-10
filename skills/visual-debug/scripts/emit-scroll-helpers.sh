#!/usr/bin/env bash
# emit-scroll-helpers.sh — deterministic scroll codegen.
#
# Reads <ref-dir>/generation-plan.json and emits ready-to-use scaffold helpers
# into <impl-dir>/src/lib/ so the impl wires smooth scroll with the site's REAL
# parameters instead of hand-rolled defaults:
#
#   smoothScroll.required  → src/lib/SmoothScroll.tsx (Lenis raf loop, config
#                            from smoothScroll.config — Fix 28's threaded options)
#
# Idempotent: re-running overwrites the emitted file. No-op when the plan does
# not require smooth scroll. Existing hand-written helpers in other locations
# are left untouched (this only writes the canonical src/lib/ path).
#
# Usage: emit-scroll-helpers.sh <ref-dir> <impl-dir>
set -euo pipefail

REF_DIR="${1:?Usage: emit-scroll-helpers.sh <ref-dir> <impl-dir>}"
IMPL_DIR="${2:?Usage: emit-scroll-helpers.sh <ref-dir> <impl-dir>}"

PLAN="$REF_DIR/generation-plan.json"
# Deliberately NOT skipping when the plan is absent. A ref can carry a
# transition-spec and no generation-plan; the scaffold still mounts drivers it
# derives from the spec, and skipping here left those mounts with no file
# behind them — the generated tree could not build and nothing reported it.
# The emitter treats a missing plan as empty and emits only what the already-
# emitted tree references.
if [ ! -f "$PLAN" ]; then
  echo "▸ emit-scroll-helpers: no generation-plan.json in $REF_DIR — emitting only scaffold-referenced helpers"
fi

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"

python3 "$SCRIPTS_DIR/lib/emit_scroll_helpers.py" "$PLAN" "$IMPL_DIR"
