#!/usr/bin/env bash
# finalize-stage.sh — post-loop housekeeping for one staged convergence loop.
#
# Runs after a loop tab reports DONE (or otherwise stops). Performs:
#   1. Convergence check via scripts/verify/check-converged.sh
#   2. Receipt build via skills/visual-debug/scripts/build-decode-receipt.sh
#      (always — receipt is useful even on non-convergence as carry-forward
#      context for the next iteration)
#   3. Prints a next-stage hint (or terminal-stage acknowledgment)
#
# Usage:
#   bash scripts/loop/finalize-stage.sh <ref-dir> <A|B|C|D>
#
# Exit codes:
#   0  converged + receipt built
#   1  not converged (receipt still built)
#   2  setup error (bad args / missing ref)

set -uo pipefail

if [[ $# -ne 2 ]]; then
  printf 'usage: %s <ref-dir> <A|B|C|D>\n' "$0" >&2
  exit 2
fi

ref="$1"
stage="$2"

case "$stage" in
  A|B|C|D) ;;
  *)
    printf 'finalize-stage: unknown stage %q (use A, B, C, or D)\n' "$stage" >&2
    exit 2
    ;;
esac

if [[ ! -d "$ref" ]]; then
  printf 'finalize-stage: ref dir not found: %s\n' "$ref" >&2
  exit 2
fi

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
check_script="$repo_root/scripts/verify/check-converged.sh"
receipt_script="$repo_root/skills/visual-debug/scripts/build-decode-receipt.sh"

# ── 1. Convergence check ────────────────────────────────────────────────────
# Stage A is decode-only: it never produces sections/result.txt (no impl to
# compare). Its success criterion is "decode artifacts exist with ≥1 section
# enumerated". Stages B/C/D run section-compare → use the canonical detector.
converged=0
check_out=""
if [[ "$stage" == "A" ]]; then
  section_map="$ref/section-map.json"
  if [[ ! -f "$section_map" ]]; then
    check_out="finalize-stage(A): missing section-map.json — decode did not run or failed"
  else
    section_count="$(python3 -c "
import json, sys
try:
    d = json.load(open('$section_map'))
    print(len(d.get('sections', [])))
except Exception:
    print(0)
" 2>/dev/null || echo 0)"
    if [[ "$section_count" -ge 1 ]]; then
      check_out="converged(A): decode produced $section_count section(s) in section-map.json"
      converged=1
    else
      check_out="finalize-stage(A): section-map.json has empty sections[] — decode extracted nothing"
    fi
  fi
elif [[ -f "$ref/sections/result.txt" ]]; then
  if check_out="$(bash "$check_script" "$ref" 2>&1)"; then
    converged=1
  fi
else
  check_out="finalize-stage: no sections/result.txt yet — loop never ran section-compare"
fi

# ── 2. Receipt build (always, even if not converged) ────────────────────────
receipt_out=""
receipt_rc=0
if [[ -x "$receipt_script" ]] || [[ -f "$receipt_script" ]]; then
  receipt_out="$(bash "$receipt_script" "$ref" 2>&1)" || receipt_rc=$?
else
  receipt_out="finalize-stage: receipt script not found at $receipt_script"
  receipt_rc=1
fi

# Find the receipt path (default-emit puts it under outbox/<date>/<comp>/receipt.html
# relative to PLUGIN_ROOT, which build-decode-receipt.sh resolves itself).
receipt_path="$(find "${PLUGIN_ROOT:-$repo_root}/outbox" -name receipt.html -type f 2>/dev/null | head -1 || true)"

# ── 3. Report ───────────────────────────────────────────────────────────────
printf '─── Stage %s finalize ───\n' "$stage"
printf 'ref-dir: %s\n' "$ref"
printf '%s\n' "$check_out"
if [[ -n "$receipt_path" ]]; then
  printf 'receipt: %s\n' "$receipt_path"
else
  printf 'receipt: (build failed) %s\n' "$receipt_out"
fi

if [[ "$converged" -eq 1 ]]; then
  case "$stage" in
    A) next="B (clone hero only)" ;;
    B) next="C (clone hero + sections 2-3)" ;;
    C) next="D (full integration + canonical verify)" ;;
    D) next="" ;;
  esac
  if [[ -n "$next" ]]; then
    printf '✓ converged. Next stage: %s\n' "$next"
    printf '  Launch with: bash scripts/loop/launch-stage.sh %s\n' "${stage:0:1}" | tr 'ABCD' 'BCDE' | sed 's/E/(plan complete)/'
    # The tr/sed combo bumps A→B, B→C, C→D, D→(plan complete).
  else
    printf '✓ converged. Plan complete — Stage D is terminal.\n'
  fi
  exit 0
fi

printf '✗ not converged. Iterate the same stage, or read the receipt for clues.\n'
exit 1
