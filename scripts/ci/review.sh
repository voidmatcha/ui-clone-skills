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
section "Tests"
if command -v uv >/dev/null 2>&1; then
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
section "Security"
if bash scripts/ci/pre-push-security.sh --quiet 2>/dev/null; then
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

if python3 - <<'PY'
import json
import pathlib
import re
import sys

expected = {"ui-reverse-engineering", "ui-capture", "visual-debug"}
# Internal-only skills (maintainer tooling). Allowed on the filesystem under
# skills/ but MUST NOT be registered in .claude-plugin/plugin.json `skills`
# or referenced from .codex-plugin defaultPrompt — that would publish them.
internal_skills = {"benchmark"}
errors = []

claude = json.loads(pathlib.Path(".claude-plugin/plugin.json").read_text())
claude_paths = claude.get("skills")
if not isinstance(claude_paths, list):
    errors.append(".claude-plugin/plugin.json skills must be a list")
else:
    claude_skills = {pathlib.PurePosixPath(p).name for p in claude_paths if isinstance(p, str)}
    if claude_skills != expected:
        errors.append(f"Claude plugin public skills mismatch: {sorted(claude_skills)}")

skill_names = set()
for path in sorted(pathlib.Path("skills").glob("*/SKILL.md")):
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^---\n(.*?)\n---", text, re.S)
    if not match:
        errors.append(f"{path}: missing YAML frontmatter")
        continue
    name = re.search(r"^name:\s*['\"]?([^'\"\n]+)['\"]?\s*$", match.group(1), re.M)
    if not name:
        errors.append(f"{path}: missing frontmatter name")
        continue
    skill_names.add(name.group(1).strip())
extras = skill_names - expected - internal_skills
missing = expected - skill_names
if extras:
    errors.append(f"skills/*/SKILL.md unexpected names (add to internal_skills if internal): {sorted(extras)}")
if missing:
    errors.append(f"skills/*/SKILL.md missing public names: {sorted(missing)}")
# Internal skills must NOT appear in Claude plugin public list
internal_in_public = internal_skills & claude_skills if isinstance(claude_paths, list) else set()
if internal_in_public:
    errors.append(f".claude-plugin/plugin.json leaks internal skills publicly: {sorted(internal_in_public)}")

codex = json.loads(pathlib.Path(".codex-plugin/plugin.json").read_text())
interface = codex.get("interface", {})
prompt = interface.get("defaultPrompt", "")
if isinstance(prompt, list):
    prompt = "\n".join(str(item) for item in prompt)
codex_text = f"{interface.get('longDescription', '')}\n{prompt}"
missing_codex = sorted(skill for skill in expected if skill not in codex_text)
if missing_codex:
    errors.append(f"Codex prompt/description missing public skill mentions: {missing_codex}")

if errors:
    for error in errors:
        print(error, file=sys.stderr)
    sys.exit(1)
PY
then
  ok "public skill set is ui-reverse-engineering, ui-capture, visual-debug"
else
  err "public skill surface parity failed"
fi

# ── 4b. Trigger boundaries ──
section "Trigger boundaries"

if python3 - <<'PY'
import pathlib
import sys

checks = {
    "ui-reverse-engineering": {
        "live URL trigger": ("live", "url"),
        "React build target": ("react",),
        "capture route-out": ("ui-capture",),
        "mismatch route-out": ("visual-debug",),
    },
    "ui-capture": {
        "reference evidence trigger": ("reference", "capture"),
        "screenshot capture": ("screenshot",),
        "transition capture": ("transition",),
        "mismatch diagnosis route-out": ("visual-debug", "mismatch"),
    },
    "visual-debug": {
        "reference implementation comparison": ("reference", "implementation"),
        "comparison/diff trigger": ("compar",),
        "build route-out": ("ui-reverse-engineering", "build"),
        "baseline capture route-out": ("ui-capture", "capture"),
    },
}

errors = []
for skill, groups in checks.items():
    text = pathlib.Path("skills", skill, "SKILL.md").read_text(encoding="utf-8").lower()
    for label, tokens in groups.items():
        missing = [token for token in tokens if token not in text]
        if missing:
            errors.append(f"{skill}: missing {label} token(s): {', '.join(missing)}")

if errors:
    for error in errors:
        print(error, file=sys.stderr)
    sys.exit(1)
PY
then
  ok "public skill trigger boundaries mention required route tokens"
else
  err "public skill trigger boundary drift detected"
fi

# ── 5. Gate-artifact timing ──
section "Gate-artifact timing"

# external-sdks.json must appear in gate_spec, not gate_bundle. Inspect each
# method body via AST so the check stays correct as helper methods are added
# between gate_bundle and gate_spec (string-slicing the file misclassified
# the paid-features cross-validator that scans extraction artifacts).
if uv run python -c "
import ast, sys
with open('ui_clone/gate.py') as f:
    tree = ast.parse(f.read())
gate_cls = next(
    (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == 'Gate'),
    None,
)
if gate_cls is None:
    print('Gate class not found in ui_clone/gate.py', file=sys.stderr)
    sys.exit(1)
methods = {n.name: ast.unparse(n) for n in gate_cls.body if isinstance(n, ast.FunctionDef)}
if 'gate_bundle' not in methods or 'gate_spec' not in methods:
    print('gate_bundle / gate_spec missing from Gate class', file=sys.stderr)
    sys.exit(1)
if 'external-sdks' in methods['gate_bundle']:
    print('external-sdks.json is in gate_bundle (should be in gate_spec)', file=sys.stderr)
    sys.exit(1)
if 'external-sdks' not in methods['gate_spec']:
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

# ── 7. README accuracy ──
section "README accuracy"

# Sub-doc count
ACTUAL_SUBDOCS=$(find skills -name "*.md" ! -name "SKILL.md" | wc -l | tr -d ' ')
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
while IFS= read -r f; do
  [ -z "$f" ] && continue
  bash -n "$f" 2>/dev/null || SH_BAD="$SH_BAD $f"
done <<< "$SH_FILES"
if [ -z "$SH_BAD" ]; then
  SH_COUNT=$(printf "%s\n" "$SH_FILES" | grep -c '\.sh$' || true)
  ok "all $SH_COUNT shell scripts pass bash -n"
else
  err "shell syntax errors in:$SH_BAD"
fi

# ── 10. Language consistency ──
section "Language"

# Skill docs, trigger fixtures, and CHANGELOG must be English only — no exclusions.
# `grep -P` is GNU-only — macOS BSD grep silently no-ops, which made this check
# pass locally while failing on Linux CI. Use python for a portable Unicode scan.
KOREAN_HITS=$(python3 - <<'PY' 2>/dev/null || true
import pathlib, re
hangul = re.compile(r'[\uAC00-\uD7AF]')
roots = ['skills', 'CHANGELOG.md', 'README.md', 'AGENTS.md', 'CLAUDE.md']
hits = []
for root in roots:
    rp = pathlib.Path(root)
    if not rp.exists():
        continue
    paths = [rp] if rp.is_file() else sorted(list(rp.rglob('*.md')) + list(rp.rglob('*.json')))
    for p in paths:
        try:
            if hangul.search(p.read_text(encoding='utf-8', errors='ignore')):
                hits.append(str(p))
        except OSError:
            pass
print('\n'.join(hits))
PY
)
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
