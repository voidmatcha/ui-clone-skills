#!/usr/bin/env bash
# generation-plan.sh — produce a deterministic generation-plan.json from
# detection artifacts. Bridges the Detection → Generation gap that opened
#
# Input:  tmp/ref/<component>/ — must contain Phase 1-5 artifacts
# Output: tmp/ref/<component>/generation-plan.json
#
# The plan is meant to be the SINGLE SOURCE OF TRUTH for Phase 6
# generation: component list, library installs, sticky/hidden strategy,
# initial animation state, asset-substitution mode. Claude Code MAY
# additionally invoke the `generation-planner` sub-agent to enrich the
# plan with qualitative judgments. Codex consumes the deterministic
# plan directly.
#
# Usage: generation-plan.sh <ref-dir>
set -euo pipefail

REF_DIR="${1:-}"
if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: $0 <ref-dir>" >&2
  exit 2
fi

OUT="$REF_DIR/generation-plan.json"

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$_SCRIPT_DIR/generation_plan.py" "$REF_DIR" "$OUT"

# Step 7-pre: the verification plan is minted at Step 5d, BEFORE this script
# derives plan-specific rows (signatureEffects / scrollScrub scale). Amend the
# existing plan now so those block-severity rows (e.g. signature-effects-coverage)
# actually register. Append-only + idempotent; never fatal to generation-plan.sh
# (a skipped/failed amend is backstopped by the pre-generate staleness gate).
if [ -f "$OUT" ] && [ -f "$REF_DIR/verification-plan.json" ]; then
  _GP_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  _GP_ROOT="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-$(cd "$_GP_SCRIPT_DIR/../.." && pwd)}}}"
  _VPLAN="$_GP_ROOT/skills/visual-debug/scripts/verification-plan.sh"
  if [ -f "$_VPLAN" ]; then
    bash "$_VPLAN" "$REF_DIR" --amend \
      || echo "generation-plan: verification-plan --amend failed (non-fatal; pre-generate staleness gate backstops)" >&2
  fi
fi
