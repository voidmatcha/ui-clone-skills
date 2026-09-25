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
#   3. Benchmark site names — `realfood` in any form (`realfood.gov`,
#      `realfood-v2`, `tmp/ref/realfood`, "realfood's card_bg"), or any other
#      concrete site name that ships as an example in a comment when a
#      generic placeholder ("one observed site", "a Lenis-driven site") would do.
#   3b. Maintainer end-to-end run identifiers — `loop-e2e-<N>` and bare
#      `e2e-<N>` / `<site>-e2e-<N>` corpus labels. Say "an end-to-end run".
#      Generic E2E vocabulary (`tests/e2e-3/`, `e2e-2024 runner`) passes.
#   3c. Benchmark corpus labels — `ebay-playbook` (folded into rule 3).
#   4. Brand / company leakage — `NAVER`, `navercorp`, `naver.com`, `dga_`
#      (NAVER's CSS-module prefix), `kakao`, `coupang`, `nexon`. Site-specific
#      class-prefix examples should use `prefix_*` or `opaque-hashed-class`.
#   4b. Dated lab notes and session labels — `<N>-site loop`,
#      `review|analysis|audit YYYY-MM-DD`, `fable-YYYYMMDD`. State the
#      finding, not which run or day produced it.
#   5. Personal paths — `/Users/<name>/`, `~/.claude/plans/<filename>.md`,
#      `~/Documents/<personal-folder>/`, `~/.codex/<anything but host config>`.
#   6. Hangul (or any non-English natural language) in *production source*
#      comments. Public docs / handover / CHANGELOG are exempt.
#   7. Maintainer terminal setup — `purplemux`, `cmux`, workspace ids
#      (`ws-XXXXXX`: six alphanumerics with at least one digit and one
#      uppercase letter, so `ws-client` / `ws-server` pass). Launchers take
#      these from UI_CLONE_LOOP_* env vars.
#   8. Personal project names — `onpixel` (lives under internal/, never in
#      the shipped package).
#   9. Lab batch labels — `batch-N item N`, `tools-batch-N`.
#  10. Repository owner used as behavior — a hard-coded
#      `github.com/voidmatcha/...` fetch URL inside hooks/, scripts/, or
#      ui_clone/. Derive it from UI_CLONE_REPO / `git remote get-url origin`;
#      README, manifests, and install docs (attribution) are out of scope.
#
# Allowlists are explicit per rule in check_universality.py (`allow=`), never
# a weakened pattern: Codex host-config paths (~/.codex/config.toml,
# hooks.json, plugins/, skills), the `${UI_CLONE_REPO:-<url>}` /
# `UI_CLONE_REPO_DEFAULT="<url>"` env-default forms, and the
# `internal/onpixel` path reference that keeps its tests collected. An allow
# only covers a hit that sits INSIDE the allowed span — appending an allowed
# token as a trailing comment does not launder the rest of the line.
#
# Why a gate: the cleanup history shows these creep back in through hook
# closure comments ("Loop-codex-N closure: agent did X") and finding labels
# ("L62 root cause was 0 dga_* refs vs 117"). Each looks harmless in
# isolation. The cumulative effect is that the production source reads like
# the maintainer's lab notebook, not a generic tool.
#
# Scope: this gate scans the same surface the rest of the public-facing
# project ships, INCLUDING host-integration surfaces that ship to users:
# .claude-plugin/ (agent definitions, manifests), .codex-plugin/, .codex/agents/,
# and docs/. It explicitly skips:
#   - tests/         (test fixtures may use concrete sample data)
#   - CHANGELOG.md / CHANGELOG_archive/ (historical record)
#   - research/     (maintainer's research notes)
#   - handover, outbox/, .worktrees/, .ui-re-continuation/ (gitignored local-only)
#   - internal/    (maintainer-only automation, not packaged)
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
