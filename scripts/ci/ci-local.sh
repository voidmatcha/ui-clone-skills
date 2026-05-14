#!/usr/bin/env bash
# ci-local.sh — Mirror of .github/workflows/ci.yml `test` job for local pre-push runs.
# Runs every check that GitHub Actions runs, in the same order, with the same commands.
#
# Why: local push hooks were missing mypy/ruff/pytest, so type errors and lint failures
# only surfaced on GitHub. This is the single source of truth — if you change CI,
# change this; if you change this, change CI.
#
# Usage: bash scripts/ci/ci-local.sh [--quiet]
# Exit:  0 = all pass, non-zero = first failing step
# Bypass (emergency only): UI_RE_SKIP_CI_LOCAL=1

set -uo pipefail

QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)" || { echo "ci-local.sh: cannot resolve repo root" >&2; exit 1; }
cd "$REPO_ROOT" || { echo "ci-local.sh: cannot cd to $REPO_ROOT" >&2; exit 1; }

if [ "${UI_RE_SKIP_CI_LOCAL:-}" = "1" ]; then
  echo "⚠️  ci-local skipped via UI_RE_SKIP_CI_LOCAL=1" >&2
  exit 0
fi

step() { [ "$QUIET" = "1" ] || echo "── $* ──"; }
fail() { echo "❌ ci-local: $1 FAILED" >&2; exit 1; }

# 1. Tests
step "Tests"
if [ "$QUIET" = "1" ]; then
  uv run python -m pytest tests/ -q >/dev/null 2>&1 || fail "tests"
else
  uv run python -m pytest tests/ -q || fail "tests"
fi

# 2. Type check (mypy)
step "Type check"
if [ "$QUIET" = "1" ]; then
  uv run python -m mypy ui_clone/ tests/ >/dev/null 2>&1 || fail "mypy"
else
  uv run python -m mypy ui_clone/ tests/ || fail "mypy"
fi

# 3. Lint check (ruff)
step "Lint check"
if [ "$QUIET" = "1" ]; then
  uv run python -m ruff check ui_clone/ tests/ >/dev/null 2>&1 || fail "ruff"
else
  uv run python -m ruff check ui_clone/ tests/ || fail "ruff"
fi

# 4. Shell syntax check
step "Shell syntax check"
for f in scripts/ci/*.sh scripts/hooks/*.sh scripts/extract/*.sh scripts/verify/*.sh hooks/*.sh skills/visual-debug/scripts/*.sh; do
  bash -n "$f" || fail "shell syntax: $f"
done
[ "$QUIET" = "1" ] || echo "  ✓ all shell scripts parse"

# 5. Review checks
step "Review checks"
if [ "$QUIET" = "1" ]; then
  bash scripts/ci/review.sh --quiet >/dev/null 2>&1 || fail "review.sh"
else
  bash scripts/ci/review.sh || fail "review.sh"
fi

# 6. Drift smoke test — verifies review.sh + pre-push-security.sh still catch
# known-bad mutations. Prevents the guards rotting silently (regex breaking,
# denylist entry getting dropped, language scanner no-opping on a platform).
step "Drift smoke test"
if [ "$QUIET" = "1" ]; then
  bash scripts/ci/test-parity.sh >/dev/null 2>&1 || fail "test-parity.sh"
else
  bash scripts/ci/test-parity.sh || fail "test-parity.sh"
fi

[ "$QUIET" = "1" ] || {
  echo
  echo "════════════════════════════════════════"
  echo "  ci-local: all checks passed"
  echo "════════════════════════════════════════"
}
exit 0
