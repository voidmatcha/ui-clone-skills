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

exec python3 "$REPO_ROOT/scripts/ci/check_universality.py" "$REPO_ROOT"
