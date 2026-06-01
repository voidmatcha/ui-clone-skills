#!/usr/bin/env bash
# check-universality.sh — block maintainer-bias drift from re-entering the
# tree.
#
# Production code (ui_clone/, skills/, scripts/, hooks/, README*, .claude-plugin/,
# .codex-plugin/) must not carry:
#
#   1. Maintainer loop identifiers — `scratch/loop-codex-<N>`,
#      `scratch/loop-claude-<N>`, `loop-codex-<N>`, `loop-claude-<N>`,
#      `loop-<N>` style attribution.
#   2. Per-loop finding labels — `L33`, `L62`, `Codex L24 Q5`, `codex-1<n>`,
#      `Round 1`/`Round 2` referring to specific benchmark runs.
#   3. Benchmark site names — `realfood.gov`, `realfood-bench`, `tmp/ref/realfood`,
#      `tmp/ref/<benchmark-name>`, or any other concrete site name that ships
#      as an example in a comment when a generic placeholder would do.
#   4. Brand / company leakage — `NAVER`, `naver.com`, `dga_` (NAVER's
#      CSS-module prefix), `kakao`, `coupang`, `nexon`. Site-specific
#      class-prefix examples should use `prefix_*` or `opaque-hashed-class`.
#   5. Personal paths — `/Users/<name>/`, `~/.claude/plans/<filename>.md`,
#      `~/Documents/<personal-folder>/`.
#   6. Hangul (or any non-English natural language) in *production source*
#      comments. Public docs / handover / CHANGELOG are exempt.
#
# Why a gate: the cleanup history shows these creep back in through hook
# closure comments ("Loop-codex-N closure: agent did X") and finding labels
# ("L62 root cause was 0 dga_* refs vs 117"). Each looks harmless in
# isolation. The cumulative effect is that the production source reads like
# the maintainer's lab notebook, not a generic tool.
#
# Scope: this gate scans the same surface the rest of the public-facing
# project ships. It explicitly skips:
#   - tests/         (test fixtures may use concrete sample data)
#   - CHANGELOG.md / CHANGELOG_archive/ (historical record)
#   - research/     (maintainer's research notes)
#   - handover     (gitignored local-only)
#   - tmp/, scratch/, benchmark/  (ephemeral)
#   - .git/, .venv/, node_modules/, .mypy_cache/, .sisyphus/, .claude/
#
# Override: set UI_CLONE_SKIP_UNIVERSALITY=1 to bypass for an emergency
# commit (CI will still catch it).
#
# Exit 0 = clean, 1 = at least one violation found.

set -o pipefail

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$REPO_ROOT"

if [ "${UI_CLONE_SKIP_UNIVERSALITY:-0}" = "1" ]; then
  echo "check-universality: SKIPPED via UI_CLONE_SKIP_UNIVERSALITY=1"
  exit 0
fi

# Common exclusion args for all greps.
EXCL=(
  --exclude-dir=.git
  --exclude-dir=.venv
  --exclude-dir=node_modules
  --exclude-dir=tmp
  --exclude-dir=scratch
  --exclude-dir=benchmark
  --exclude-dir=CHANGELOG_archive
  --exclude-dir=tests
  --exclude-dir=research
  --exclude-dir=.mypy_cache
  --exclude-dir=.sisyphus
  --exclude-dir=.claude
  --exclude-dir=.codex-plugin
  --exclude-dir=.claude-plugin
  # .omx/ — Codex/OMX runtime state (subagent task transcripts, hud-state.json).
  # Gitignored, never committed, but contains absolute paths and finding labels
  # from in-flight rescue sessions. Excluding here keeps the guard focused on
  # the actual source tree.
  --exclude-dir=.omx
  --exclude=CHANGELOG.md
  --exclude=handover
  --exclude=check-universality.sh
)

# File-type filter — only scan source-like files.
INCL=(
  --include='*.py'
  --include='*.sh'
  --include='*.md'
  --include='*.json'
  --include='*.toml'
  --include='*.yml'
  --include='*.yaml'
)

violations=0

scan() {
  local label="$1" ; shift
  local pattern="$1" ; shift
  local -a extra_excludes
  extra_excludes=("$@")
  local hits
  if [ "${#extra_excludes[@]}" -gt 0 ]; then
    hits=$(grep -rEn "$pattern" "${INCL[@]}" "${EXCL[@]}" "${extra_excludes[@]}" . 2>/dev/null || true)
  else
    hits=$(grep -rEn "$pattern" "${INCL[@]}" "${EXCL[@]}" . 2>/dev/null || true)
  fi
  if [ -n "$hits" ]; then
    echo "❌ $label"
    echo "$hits" | head -20 | sed 's/^/   /'
    local n
    n=$(printf '%s\n' "$hits" | wc -l | tr -d ' ')
    echo "   ($n match$([ "$n" = 1 ] || echo es))"
    echo ""
    violations=$((violations + 1))
  fi
}

echo "── check-universality ──"
echo ""

scan "Maintainer loop identifiers (loop-codex-N, loop-claude-N, scratch/loop-N)" \
  '(scratch/)?loop-(codex|claude)-[0-9]+|scratch/loop-[0-9N]+'

# L<NN> guard: exclude SVG path syntax (e.g. "M10 5 L20 15") where L is the
# Line-To command, always followed by " <num>". The label form we catch is
# bare "L<NN>" not adjacent to another digit-coord token.
scan "Per-loop finding labels (L33, L62, loop-37, etc.)" \
  '(\bL[0-9]{2,3}\b(?! [0-9])|\bloop-[0-9]+\b)' \
  --exclude='check-universality.sh'

scan "Benchmark site names (realfood.gov / realfood-bench / tmp/ref/realfood)" \
  'realfood\.gov|realfood-bench|tmp/ref/realfood'

scan "Brand / company leakage (NAVER, dga_, kakao, coupang, nexon)" \
  '\bNAVER\b|\bNaver\b|naver\.com|\bdga_|\bkakao\b|\bcoupang\b|\bnexon\b' \
  --exclude='check-universality.sh'

scan "Codex iteration labels (codex-1N / Codex LN QN / Round N)" \
  '\bcodex-(1[0-9]|[2-9][0-9])\b|Codex L[0-9]+ Q[0-9]+|\bRound [12]\b' \
  --exclude='check-universality.sh'

scan "Personal absolute paths (/Users/<name>/)" \
  '/Users/[a-z][a-z0-9_-]+/'

scan "Personal plan files (~/.claude/plans/<name>.md, happy-finding-pelican)" \
  'happy-finding-pelican|~/\.claude/plans/'

# Hangul in production .py/.sh — public docs (.md) excluded since maintainer
# may legitimately keep Korean in handover/research; we excluded handover and
# research at the EXCL level already, so any remaining Hangul in .py/.sh
# under production scope is a leak.
HANGUL_HITS=$(grep -rEn '[가-힣]' --include='*.py' --include='*.sh' "${EXCL[@]}" . 2>/dev/null || true)
if [ -n "$HANGUL_HITS" ]; then
  echo "❌ Hangul (non-English) in production .py/.sh"
  echo "$HANGUL_HITS" | head -20 | sed 's/^/   /'
  echo ""
  violations=$((violations + 1))
fi

if [ "$violations" -eq 0 ]; then
  echo "✅ check-universality: 0 violations"
  exit 0
fi

echo "─────────────────────────────────────"
echo "❌ check-universality: $violations violation class(es) found"
echo ""
echo "How to fix:"
echo "  - Replace concrete site/loop/finding identifiers with generic descriptors"
echo "    (\"observed failure mode\", \"<component>\", \"opaque-hashed-class\", etc.)."
echo "  - Move maintainer-only context to handover (gitignored) or research/."
echo "  - For test fixtures, the test belongs under tests/ — that path is exempt."
echo ""
echo "If you genuinely need to ship one of these (a real example in a doc),"
echo "either justify in a code review or bypass with UI_CLONE_SKIP_UNIVERSALITY=1."
exit 1
