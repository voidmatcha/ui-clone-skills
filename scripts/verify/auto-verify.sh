#!/usr/bin/env bash
# auto-verify.sh — Single verification command that runs all checks
#
# Usage: bash scripts/verify/auto-verify.sh <session> <orig-url> <impl-url> <ref-dir>
#
# Runs in order:
#   D0: layout-health-check (structural comparison)
#   C:  batch-scroll + AE comparison (pixel comparison)
#   Gate: post-implement validation
#
# Exit: 0 = all pass, 1 = failures found
#
# DO NOT run individual checks selectively. This script exists to prevent
# cherry-picking passing checks while ignoring failures.

set -euo pipefail

SESSION="${1:?Usage: auto-verify.sh <session> <orig-url> <impl-url> <ref-dir>}"
ORIG_URL="${2:?}"
IMPL_URL="${3:?}"
REF_DIR="${4:?}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VIEW_W="${VIEW_W:-1440}"
VIEW_H="${VIEW_H:-900}"

# Cleanup browser sessions on exit (including errors/signals)
cleanup_browsers() {
  agent-browser --session "${SESSION}-verify" close 2>/dev/null
}
trap cleanup_browsers EXIT

# Resolve visual-debug scripts location.
VISUAL_DEBUG_SCRIPTS="${VISUAL_DEBUG_SCRIPTS_DIR:-}"
if [ -z "$VISUAL_DEBUG_SCRIPTS" ]; then
  # cat may fail under set -e if the marker file is absent; trailing `|| true` is required.
  _marker="$(cat "$HOME/.config/ui-clone-skills/root" 2>/dev/null || true)"
  _marker="${_marker%$'\r'}"
  for root in "$REPO_ROOT" "${PLUGIN_ROOT:-}" "${CODEX_PLUGIN_ROOT:-}" "${CLAUDE_PLUGIN_ROOT:-}" "${UI_CLONE_ROOT:-}" "$_marker" "${INSTALL_DIR:-$HOME/.local/share/ui-clone-skills}" "$HOME"/.claude/plugins/cache/*/ui-clone-skills/*/ "$HOME"/.codex/plugins/cache/*/ui-clone-skills/*/; do
    [ -n "$root" ] && [ -f "$root/skills/visual-debug/scripts/ae-compare.sh" ] && VISUAL_DEBUG_SCRIPTS=$(cd "$root/skills/visual-debug/scripts" && pwd) && break
  done
fi
if [ -z "$VISUAL_DEBUG_SCRIPTS" ]; then
  echo "ERROR: visual-debug scripts not found (ae-compare.sh, batch-compare.sh)"
  echo "Set VISUAL_DEBUG_SCRIPTS_DIR or PLUGIN_ROOT. Default checked: $REPO_ROOT/skills/visual-debug/scripts/"
  exit 2
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
NC='\033[0m'

TOTAL_CHECKS=0
TOTAL_FAIL=0

run_check() {
  local label="$1"
  shift
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  echo -e "\n${BOLD}[$TOTAL_CHECKS] $label${NC}"
  if "$@"; then
    echo -e "  ${GREEN}PASS${NC}"
  else
    echo -e "  ${RED}FAIL${NC}"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
  fi
}

run_with_timeout() {
  local seconds="$1"
  shift

  if command -v timeout >/dev/null 2>&1; then
    timeout "$seconds" "$@"
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout "$seconds" "$@"
  else
    "$@"
  fi
}

echo -e "${BOLD}═══ auto-verify.sh ═══${NC}"
echo "Session: $SESSION"
echo "Original: $ORIG_URL"
echo "Implementation: $IMPL_URL"
echo "Ref dir: $REF_DIR"

# ── Pre-check: ensure both URLs are reachable ──
echo -e "\n${BOLD}Pre-check: URL reachability${NC}"
for url in "$ORIG_URL" "$IMPL_URL"; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")
  if [ "$code" = "200" ]; then
    echo -e "  ${GREEN}✓${NC} $url → $code"
  else
    echo -e "  ${RED}✗${NC} $url → $code"
    echo -e "${RED}BLOCKED: Cannot reach $url. Start the server first.${NC}"
    exit 1
  fi
done

# ── D0: Layout health check ──
if [ -f "$VISUAL_DEBUG_SCRIPTS/layout-health-check.sh" ]; then
  run_check "D0: Layout health check" \
    bash "$VISUAL_DEBUG_SCRIPTS/layout-health-check.sh" "$SESSION" "$ORIG_URL" "$IMPL_URL" "$REF_DIR"
else
  echo -e "\n${YELLOW}SKIP: layout-health-check.sh not found at $VISUAL_DEBUG_SCRIPTS${NC}"
fi

# ── C: Capture impl screenshots + batch comparison ──
echo -e "\n${BOLD}Capturing implementation screenshots...${NC}"
mkdir -p "$REF_DIR/static/impl" "$REF_DIR/static/diff"

# Capture impl at same scroll positions as ref
run_with_timeout 30 agent-browser open "$IMPL_URL" --session "${SESSION}-verify" 2>/dev/null || true
run_with_timeout 10 agent-browser set viewport "$VIEW_W" "$VIEW_H" --session "${SESSION}-verify" 2>/dev/null || true
sleep 5

for pct in 0 10 20 30 40 50 60 70 80 90 100; do
  run_with_timeout 15 agent-browser eval "(()=>{const h=document.documentElement.scrollHeight-window.innerHeight;window.scrollTo(0,h*$pct/100);return $pct})()" --session "${SESSION}-verify" 2>/dev/null || true
  sleep 1
  run_with_timeout 15 agent-browser screenshot "$REF_DIR/static/impl/${pct}pct.png" --session "${SESSION}-verify" 2>/dev/null || true
done

run_with_timeout 10 agent-browser --session "${SESSION}-verify" close 2>/dev/null || true

# Run batch comparison
if [ -f "$VISUAL_DEBUG_SCRIPTS/batch-compare.sh" ]; then
  run_check "C: Batch AE comparison (ref vs impl)" \
    bash "$VISUAL_DEBUG_SCRIPTS/batch-compare.sh" "$REF_DIR"
else
  echo -e "\n${YELLOW}SKIP: batch-compare.sh not found${NC}"
  # Fallback: manual AE comparison
  if [ -f "$VISUAL_DEBUG_SCRIPTS/ae-compare.sh" ]; then
    echo -e "\n${BOLD}Fallback: individual AE comparisons${NC}"
    PASS_COUNT=0
    FAIL_COUNT=0
    for ref_img in "$REF_DIR"/static/ref/*.png; do
      fname=$(basename "$ref_img")
      impl_img="$REF_DIR/static/impl/$fname"
      if [ -f "$impl_img" ]; then
        result=$(bash "$VISUAL_DEBUG_SCRIPTS/ae-compare.sh" "$ref_img" "$impl_img" "$REF_DIR/static/diff/$fname" 2>/dev/null)
        status=$(echo "$result" | grep -o 'STATUS=[A-Z]*' | cut -d= -f2)
        ae=$(echo "$result" | grep -o 'AE=[0-9]*' | cut -d= -f2)
        if [ "$status" = "PASS" ]; then
          echo -e "  ${GREEN}✓${NC} $fname AE=$ae"
          PASS_COUNT=$((PASS_COUNT + 1))
        else
          echo -e "  ${RED}✗${NC} $fname AE=$ae"
          FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
      fi
    done
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if [ "$FAIL_COUNT" -gt 0 ]; then
      echo -e "  ${RED}$FAIL_COUNT/$((PASS_COUNT + FAIL_COUNT)) screenshots FAIL${NC}"
      TOTAL_FAIL=$((TOTAL_FAIL + 1))
    else
      echo -e "  ${GREEN}All $PASS_COUNT screenshots PASS${NC}"
    fi
  fi
fi

# ── Post-implement gate ──
run_check "Gate: post-implement" \
  uv run --project "$REPO_ROOT" python -m ui_clone.gate "$REF_DIR" post-implement

# ── Visual-debug stamp ──
# Emit visual-debug-stamp.json so downstream gates can prove this canonical
# entry was used (not bare section-compare.sh / transition-compare.sh). The
# HTML-paste + screenshot-substitution cheats slipped when an agent ran the
# leaf scripts directly and never invoked the visual-debug umbrella that
# bundles anti-cheat baseline checks. Gate now requires the stamp when
# sections/result.txt has ≥1 PASS. Phase E LLM review is OPTIONAL but, when
# run, writes phase-e-result.json which downstream gates consume.
STAMP_PATH="$REF_DIR/visual-debug-stamp.json"
PHASE_E_PATH="$REF_DIR/phase-e-result.json"
PHASE_E_PRESENT="false"
[ -f "$PHASE_E_PATH" ] && PHASE_E_PRESENT="true"
STAMP_VERIFIED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [ "$TOTAL_FAIL" -gt 0 ]; then
  STAMP_PASSED="false"
  STAMP_EXIT_CODE=1
else
  STAMP_PASSED="true"
  STAMP_EXIT_CODE=0
fi
python3 - "$STAMP_PATH" "$STAMP_VERIFIED_AT" "$STAMP_PASSED" "$STAMP_EXIT_CODE" "$PHASE_E_PRESENT" "$TOTAL_CHECKS" "$TOTAL_FAIL" <<'PY'
import json, sys
path, verified_at, passed, exit_code, phase_e, total_checks, total_fail = sys.argv[1:]
stamp = {
    "schemaVersion": 1,
    "stampedBy": "scripts/verify/auto-verify.sh",
    "verifiedAt": verified_at,
    "passed": passed == "true",
    "exitCode": int(exit_code),
    "totalChecks": int(total_checks),
    "totalFail": int(total_fail),
    "phaseE": phase_e == "true",
}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(stamp, fh, indent=2)
    fh.write("\n")
PY

# ── Summary ──
echo -e "\n${BOLD}═══ RESULT ═══${NC}"
if [ "$TOTAL_FAIL" -gt 0 ]; then
  echo -e "${RED}FAIL: $TOTAL_FAIL/$TOTAL_CHECKS checks failed.${NC}"
  echo -e "${YELLOW}DO NOT declare done. Diagnose each failure, fix, and re-run.${NC}"
  exit 1
else
  echo -e "${GREEN}PASS: $TOTAL_CHECKS/$TOTAL_CHECKS checks passed.${NC}"
  echo -e "Proceed to Phase D (pixel-perfect visual gate) for final sign-off."
  exit 0
fi
