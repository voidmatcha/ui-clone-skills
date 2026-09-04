#!/usr/bin/env bash
# review.sh — Automated review checklist for ui-clone-skills
# Runs tests, security checks, and content consistency validation.
# Called by post-push-refresh.sh after successful push, or manually.
#
# Usage: bash scripts/ci/review.sh [--quiet]
# Exit: 0 = all pass, 1 = failures found
#
# Checklist enforced here (kept in sync with AGENTS.md "Review checklist" pointer):
#   [] Tests pass
#   [] Security gate passes
#   [] Sub-doc step numbers match SKILL.md pipeline
#   [] Gate artifact checks match sub-doc output timing
#   [] No stale refs to deleted files (validate-gate.sh, run-pipeline.sh, ui_skills.*)
#   [] README numbers accurate (sub-doc count, token count, FPS)
#   [] Cross-skill refs use correct relative paths (../visual-debug/...)
#   [] Claude/Codex plugin versions match (plugin.json, marketplace.json, codex plugin.json)
#   [] No hardcoded local paths in SKILL.md (use $SCRIPTS_DIR, $PLUGIN_ROOT)
#   [] Claude Code and Codex host files valid (AGENTS.md, .codex-plugin/plugin.json, hooks/codex-hooks.json, skills/*/agents/openai.yaml)
#   [] Section name keyword lists consistent across 3 scripts

set -uo pipefail

QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)" || { echo "review.sh: cannot resolve repo root" >&2; exit 1; }
cd "$REPO_ROOT" || { echo "review.sh: cannot cd to $REPO_ROOT" >&2; exit 1; }

ERRORS=0
WARNINGS=0
PASSED=0

err() { echo "  ❌ $*" >&2; ERRORS=$((ERRORS + 1)); }
warn() { echo "  ⚠️  $*" >&2; WARNINGS=$((WARNINGS + 1)); }
ok() { [ "$QUIET" = "1" ] || echo "  ✓ $*"; PASSED=$((PASSED + 1)); }
section() { [ "$QUIET" = "1" ] || echo ""; [ "$QUIET" = "1" ] || echo "── $* ──"; }

# ── 1. Tests ──
# UI_CLONE_REVIEW_SKIP_TESTS=1 — caller (e.g. ci-local.sh) already ran pytest
# and doesn't want this nested invocation to repeat it. Eliminates the
# duplicate ~1.5–3 min pytest sweep during `git push` (pre-push-guard runs
# ci-local; ci-local ran pytest in step 1 then called review.sh).
section "Tests"
if [ "${UI_CLONE_REVIEW_SKIP_TESTS:-}" = "1" ]; then
  ok "pytest: skipped (caller already ran)"
elif command -v uv >/dev/null 2>&1; then
  TEST_OUT=$(uv run python -m pytest tests/ -q 2>&1)
  if echo "$TEST_OUT" | grep -q "passed"; then
    PASS_COUNT=$(echo "$TEST_OUT" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+')
    ok "pytest: $PASS_COUNT passed"
  else
    err "pytest failures detected"
    [ "$QUIET" = "0" ] && echo "$TEST_OUT" | tail -10 >&2
  fi
else
  warn "uv not found — skipping tests"
fi

# ── 2. Security gate ──
# UI_CLONE_REVIEW_SKIP_SECURITY=1 — caller already ran pre-push-security.sh
# (pre-push-guard.sh runs it directly before ci-local). Avoids the
# duplicate scan when chained through ci-local → review.sh.
section "Security"
if [ "${UI_CLONE_REVIEW_SKIP_SECURITY:-}" = "1" ]; then
  ok "pre-push-security: skipped (caller already ran)"
elif bash scripts/ci/pre-push-security.sh --quiet 2>/dev/null; then
  ok "pre-push-security: clean"
else
  err "pre-push-security: blockers found"
fi

# ── 3. Step numbering consistency ──
section "Step numbering"

# bundle-analysis.md must say Step 5c-a (split from 5c in v0.4.3)
if head -1 skills/ui-reverse-engineering/bundle-analysis.md | grep -q "Step 5c-a"; then
  ok "bundle-analysis.md: Step 5c-a"
else
  err "bundle-analysis.md: title should say Step 5c-a (not Step 5c, not Step 6)"
fi

# bundle-verification.md must say Step 5c-b (split from 5c in v0.4.3)
if head -1 skills/ui-reverse-engineering/bundle-verification.md | grep -q "Step 5c-b"; then
  ok "bundle-verification.md: Step 5c-b"
else
  err "bundle-verification.md: title should say Step 5c-b"
fi

# animation-detection.md must say Step 6
if head -1 skills/ui-reverse-engineering/animation-detection.md | grep -q "Step 6"; then
  ok "animation-detection.md: Step 6"
else
  err "animation-detection.md: title should say Step 6"
fi

# ── 4. Stale references ──
section "Stale references"

# Old package name
OLD_PKG=$(grep -rl 'ui_skills\.' skills/ ui_clone/ tests/ 2>/dev/null | grep -v CHANGELOG | grep -v __pycache__ || true)
if [ -z "$OLD_PKG" ]; then
  ok "no ui_skills.* references"
else
  err "old package name ui_skills.* found in: $OLD_PKG"
fi

# Old plugin name in code (not CHANGELOG)
OLD_NAME=$(grep -rlw 'ui-skills' ui_clone/ skills/ hooks/ 2>/dev/null | grep -v CHANGELOG | grep -v __pycache__ || true)
if [ -z "$OLD_NAME" ]; then
  ok "no 'ui-skills' references in code (correct: ui-clone-skills)"
else
  err "old plugin name 'ui-skills' found in: $OLD_NAME"
fi

# Deleted files
for STALE in "validate-gate.sh" "run-pipeline.sh" "waapi-scrub-inject.js" "capture-frames.sh"; do
  REFS=$(grep -rl "$STALE" skills/ scripts/ 2>/dev/null | grep -v CHANGELOG | grep -v review.sh || true)
  if [ -z "$REFS" ]; then
    ok "no refs to deleted $STALE"
  else
    err "$STALE referenced in: $REFS"
  fi
done

# Old owner name
DIDIDY=$(grep -rl 'dididy' README.md skills/ .claude-plugin/ .codex-plugin/ 2>/dev/null | grep -v review.sh || true)
if [ -z "$DIDIDY" ]; then
  ok "no dididy references (owner is voidmatcha)"
else
  err "old owner 'dididy' found in: $DIDIDY"
fi

# Wrong npm package name
WRONG_NPM=$(grep -rl '@anthropic-ai/agent-browser' skills/ ui_clone/ 2>/dev/null | grep -v review.sh || true)
if [ -z "$WRONG_NPM" ]; then
  ok "no @anthropic-ai/agent-browser refs (correct: agent-browser)"
else
  err "wrong npm name @anthropic-ai/agent-browser in: $WRONG_NPM"
fi

# Removed consolidation-local script directory
STALE_RE_SCRIPT_PREFIX=$(grep -rl 'skills/ui-reverse-engineering/scripts' README.md AGENTS.md skills/ scripts/ hooks/ ui_clone/ .claude-plugin/ .codex-plugin/ 2>/dev/null \
  | grep -v CHANGELOG | grep -v review.sh || true)
if [ -z "$STALE_RE_SCRIPT_PREFIX" ]; then
  ok "no stale skills/ui-reverse-engineering/scripts prefix"
else
  err "stale skills/ui-reverse-engineering/scripts prefix found in: $STALE_RE_SCRIPT_PREFIX"
fi

# ── 4a. Public skill surface ──
section "Public skill surface"

if python3 scripts/ci/review_checks.py public-skills; then
  ok "public skill set is ui-reverse-engineering, ui-capture, visual-debug"
else
  err "public skill surface parity failed"
fi

# ── 4b. Trigger boundaries ──
section "Trigger boundaries"

if python3 scripts/ci/review_checks.py trigger-boundaries; then
  ok "public skill trigger boundaries mention required route tokens"
else
  err "public skill trigger boundary drift detected"
fi

# ── 5. Gate-artifact timing ──
section "Gate-artifact timing"

# external-sdks.json must appear in gate_spec, not gate_bundle. Post-Item-5
# refactor: Gate methods live in ui_clone/gates/<area>.py (one module per
# gate). Inspect each gate file's AST so the check stays correct as helper
# methods are added near the gate function (string-slicing misclassified the
# paid-features cross-validator that scans extraction artifacts).
if uv run python -c "
import ast, pathlib, sys
def gate_body(path: str, fn_name: str) -> str | None:
    if not pathlib.Path(path).is_file():
        return None
    tree = ast.parse(open(path).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            return ast.unparse(node)
    return None

bundle_body = gate_body('ui_clone/gates/bundle.py', 'gate_bundle')
spec_body = gate_body('ui_clone/gates/spec.py', 'gate_spec')
if bundle_body is None or spec_body is None:
    print('gate_bundle / gate_spec missing from ui_clone/gates/', file=sys.stderr)
    sys.exit(1)
if 'external-sdks' in bundle_body:
    print('external-sdks.json is in gate_bundle (should be in gate_spec)', file=sys.stderr)
    sys.exit(1)
if 'external-sdks' not in spec_body:
    print('external-sdks.json missing from gate_spec', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null; then
  ok "external-sdks.json in gate_spec (not gate_bundle)"
else
  err "external-sdks.json gate placement wrong"
fi

# ── 6. Section name keyword sync ──
section "Section keyword sync"

extract_keywords() {
  grep -oE "'\w+','\w+'" "$1" 2>/dev/null | tr -d "'" | sort -u
}

KW1=$(grep -c "kwMap" scripts/extract/extract-assets.sh 2>/dev/null || echo "0")
KW2=$(grep -c "kwMap" scripts/extract/extract-section-html.sh 2>/dev/null || echo "0")
KW3=$(grep -c "kwMap" scripts/extract/section-clips.sh 2>/dev/null || echo "0")
if [ "$KW1" -gt 0 ] && [ "$KW2" -gt 0 ] && [ "$KW3" -gt 0 ]; then
  ok "all 3 scripts use kwMap pattern"
else
  err "section name detection not using kwMap in all 3 scripts"
fi

# ── 6a. Trigger eval fixture boundaries ──
section "Trigger fixtures"

if python3 scripts/ci/review_checks.py trigger-fixtures; then
  ok "trigger fixtures preserve public skill boundaries"
else
  err "trigger fixture boundary drift detected"
fi

# ── 7. README accuracy ──
section "README accuracy"

# Sub-doc count
ACTUAL_SUBDOCS=$(find skills -name "*.md" ! -name "SKILL.md" ! -path '*/prompts/*' | wc -l | tr -d ' ')
README_COUNT=$(grep -oE '[0-9]+ focused sub-docs' README.md | grep -oE '[0-9]+')
if [ "$ACTUAL_SUBDOCS" = "$README_COUNT" ]; then
  ok "sub-doc count matches README ($ACTUAL_SUBDOCS)"
else
  err "sub-doc count: actual=$ACTUAL_SUBDOCS, README=$README_COUNT"
fi

# FPS default
FPS_DEFAULT=$(grep -oE 'FPS="\$\{FPS:-[0-9]+\}"' scripts/verify/video-transition-compare.sh | grep -oE '[0-9]+')
if [ "$FPS_DEFAULT" = "60" ]; then
  ok "video-transition-compare.sh FPS default is 60"
else
  err "video-transition-compare.sh FPS default is $FPS_DEFAULT (should be 60)"
fi

if python3 scripts/ci/check-readme-i18n.py; then
  ok "localized READMEs match the canonical README contract"
else
  err "localized README parity failed"
fi

# ── 8. Hardcoded paths ──
section "Hardcoded paths"
# Detect absolute user-home paths (/Users/<name>/ or /home/<name>/)
HARDCODED=$(grep -rlE '/Users/[a-zA-Z]+/|/home/[a-zA-Z]+/' skills/ scripts/ hooks/ ui_clone/ .claude-plugin/ .codex-plugin/ 2>/dev/null \
  | grep -v review.sh | grep -v CHANGELOG | grep -v __pycache__ || true)
if [ -z "$HARDCODED" ]; then
  ok "no hardcoded absolute paths"
else
  err "hardcoded absolute paths found in: $HARDCODED"
fi

# ── 9. Shell syntax ──
section "Shell syntax"

SH_BAD=""
SH_FILES=$(find scripts skills hooks -name '*.sh' -type f 2>/dev/null)
[ -f install.sh ] && SH_FILES="$SH_FILES"$'\n'"install.sh"
# Use bash 4+; macOS /bin/bash (3.2) mis-lexes quoted heredocs with apostrophes.
SH_BIN=$(command -v bash)
SH_MAJOR=$("$SH_BIN" -c 'echo ${BASH_VERSION%%.*}')
if [ "${SH_MAJOR:-0}" -lt 4 ]; then
  for cand in /opt/homebrew/bin/bash /usr/local/bin/bash; do
    if [ -x "$cand" ]; then SH_BIN="$cand"; break; fi
  done
fi
while IFS= read -r f; do
  [ -z "$f" ] && continue
  "$SH_BIN" -n "$f" 2>/dev/null || SH_BAD="$SH_BAD $f"
done < <(printf "%s\n" "$SH_FILES")
if [ -z "$SH_BAD" ]; then
  SH_COUNT=$(printf "%s\n" "$SH_FILES" | grep -c '\.sh$' || true)
  ok "all $SH_COUNT shell scripts pass bash -n"
else
  err "shell syntax errors in:$SH_BAD"
fi

# Ratchet: agent-browser `open` silently ignores --viewport/--wait. The dead
# flags shipped for months and broke motion probes at the default window
# size; use scripts/lib/viewport.sh ab_open_at_viewport (or open + `set
# viewport` + sleep). Mirrors tests/test_viewport_regression.py at review
# time so the pattern cannot reappear via copy-paste.
DEAD_OPEN=$(printf "%s\n" "$SH_FILES" | while IFS= read -r f; do
  [ -z "$f" ] && continue
  sed -e 's/^[[:space:]]*#.*$//' -e 's/ #.*$//' "$f" 2>/dev/null | grep -nE '\bopen\b[^|&;]*--(viewport|wait)\b' >/dev/null 2>&1 && echo "$f"
done)
if [ -z "$DEAD_OPEN" ]; then
  ok "no dead-flag agent-browser opens (--viewport/--wait)"
else
  err "dead-flag agent-browser opens (--viewport/--wait are silently ignored; use ab_open_at_viewport): $DEAD_OPEN"
fi

# Ratchet: subprocess.run in tests without timeout=. pytest-timeout (pyproject
# timeout=300, thread method) is the hard net, but a per-call timeout= fails
# faster and surfaces the offending call. Legacy call-sites predate the net;
# this ratchet forbids new un-timed calls without forcing a one-shot cleanup.
# Lower SUBPROCESS_NO_TIMEOUT_BASELINE as call-sites are fixed. Opt-in
# integration tests carry their own generous timeouts and are excluded.
section "Subprocess timeouts"
SUBPROCESS_NO_TIMEOUT_BASELINE=223
NO_TIMEOUT_COUNT=$(python3 scripts/ci/review_checks.py count-subprocess-without-timeout 2>/dev/null || echo "ERR")
if [ "$NO_TIMEOUT_COUNT" = "ERR" ]; then
  warn "could not scan tests for un-timed subprocess.run (python3 failed)"
elif [ "$NO_TIMEOUT_COUNT" -gt "$SUBPROCESS_NO_TIMEOUT_BASELINE" ]; then
  err "subprocess.run without timeout= in tests rose to $NO_TIMEOUT_COUNT (baseline $SUBPROCESS_NO_TIMEOUT_BASELINE) — add timeout= to new calls"
elif [ "$NO_TIMEOUT_COUNT" -lt "$SUBPROCESS_NO_TIMEOUT_BASELINE" ]; then
  ok "subprocess.run without timeout= dropped to $NO_TIMEOUT_COUNT — lower SUBPROCESS_NO_TIMEOUT_BASELINE to $NO_TIMEOUT_COUNT"
else
  ok "subprocess.run without timeout= at baseline ($NO_TIMEOUT_COUNT)"
fi

# ── 9a. gates.md thinness guard ──
# docs/gates.md is re-read whenever an agent touches a gate (AGENTS.md points at
# it), so it MUST stay a thin per-gate lookup. Round-by-round narrative belongs
# in docs/gate-hardening-history.md.
section "gates.md thinness"
GATES_MD="docs/gates.md"
GATES_MAXLINE=1500
GATES_MAXBYTES=20000
if [ -f "$GATES_MD" ]; then
  GATES_MAXLEN=$(awk '{ if (length($0) > m) m = length($0) } END { print m + 0 }' "$GATES_MD")
  GATES_SIZE=$(wc -c < "$GATES_MD" | tr -d ' ')
  if [ "$GATES_MAXLEN" -gt "$GATES_MAXLINE" ]; then
    err "docs/gates.md has a ${GATES_MAXLEN}-char line (limit ${GATES_MAXLINE}) — relocate narrative prose to docs/gate-hardening-history.md"
  else
    ok "docs/gates.md longest line ${GATES_MAXLEN} ≤ ${GATES_MAXLINE}"
  fi
  if [ "$GATES_SIZE" -gt "$GATES_MAXBYTES" ]; then
    err "docs/gates.md is ${GATES_SIZE}B (limit ${GATES_MAXBYTES}B) — relocate history to docs/gate-hardening-history.md"
  else
    ok "docs/gates.md size ${GATES_SIZE}B ≤ ${GATES_MAXBYTES}"
  fi
else
  warn "docs/gates.md not found — skipping thinness guard"
fi

# ── 10. Language consistency ──
section "Language"

# Skill docs, trigger fixtures, and CHANGELOG must be English only — no exclusions.
# `grep -P` is GNU-only — macOS BSD grep silently no-ops, which made this check
# pass locally while failing on Linux CI. Use python for a portable Unicode scan.
KOREAN_HITS=$(python3 scripts/ci/review_checks.py find-hangul 2>/dev/null || true)
if [ -z "$KOREAN_HITS" ]; then
  ok "skill docs, trigger fixtures, and changelog are English only"
else
  err "Non-English (Hangul) text found: $KOREAN_HITS"
fi

# ── Summary ──
echo ""
echo "════════════════════════════════════════"
echo "  Review: $PASSED passed, $WARNINGS warnings, $ERRORS errors"
echo "════════════════════════════════════════"

[ "$ERRORS" -gt 0 ] && exit 1
exit 0
