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

# ci-local owns the pytest invocation in step 1 below. Any nested review.sh
# call (step 5 directly, step 6 transitively via test-parity.sh → review.sh)
# would re-run the same pytest sweep. Export the SKIP flag globally so every
# review.sh in this process tree honors it. ci-local's own step 1 pytest is
# unaffected — the flag only gates review.sh's pytest section.
export UI_CLONE_REVIEW_SKIP_TESTS=1

if [ "${UI_RE_SKIP_CI_LOCAL:-}" = "1" ]; then
  echo "⚠️  ci-local skipped via UI_RE_SKIP_CI_LOCAL=1" >&2
  exit 0
fi

step() { [ "$QUIET" = "1" ] || echo "── $* ──"; }
fail() { echo "❌ ci-local: $1 FAILED" >&2; exit 1; }
run_quiet() {
  local label="$1"
  local log_path
  local status
  shift

  log_path=$(mktemp "${TMPDIR:-/tmp}/ui-clone-ci-${label}.XXXXXX") \
    || fail "$label (cannot create failure log)"
  "$@" >"$log_path" 2>&1
  status=$?
  if [ "$status" -eq 0 ]; then
    rm -f "$log_path"
    return 0
  fi

  cat "$log_path" >&2
  rm -f "$log_path"
  fail "$label"
}

# Resolve a bash 4+ binary and put it FIRST on PATH before anything runs.
# macOS ships bash 3.2 as /bin/bash, which cannot parse a heredoc nested inside
# `$(...)` command substitution (a 3.2 limitation). Tests that shell out via
# subprocess.run(["bash", <script>, ...]) would otherwise pick up that 3.2 and
# false-fail on valid scripts. Project policy is bash 4+ minimum (enforced in
# the shell-syntax step below); pin it for the whole run so pytest subprocesses
# inherit it. On Linux CI `bash` is already 4+, so this is a no-op there.
BASH_BIN=$(command -v bash)
if [ "$("$BASH_BIN" -c 'echo ${BASH_VERSION%%.*}')" -lt 4 ] 2>/dev/null; then
  for cand in /opt/homebrew/bin/bash /usr/local/bin/bash; do
    if [ -x "$cand" ]; then BASH_BIN="$cand"; break; fi
  done
fi
# Always prepend (not "add if absent") so bash 4+ wins even when its dir is
# already on PATH but sits behind /bin (where macOS's 3.2 lives).
PATH="$(dirname "$BASH_BIN"):$PATH"; export PATH

# Bash 5.1+ changed large heredocs from tempfile-backed input to pipes. The
# suite exercises legacy internal check scripts directly, outside the
# dispatcher that normally contains this compatibility setting. Scope the
# Bash 5.0 behavior to pytest's child process tree only: do not change later
# CI shell/review semantics, and honor an explicitly inherited BASH_COMPAT.
PYTEST_ENV=()
read -r PYTEST_BASH_MAJOR PYTEST_BASH_MINOR < <(
  "$BASH_BIN" -c 'printf "%s %s\n" "${BASH_VERSINFO[0]}" "${BASH_VERSINFO[1]}"'
)
if [ -z "${BASH_COMPAT:-}" ] \
   && { [ "${PYTEST_BASH_MAJOR:-0}" -gt 5 ] \
        || { [ "${PYTEST_BASH_MAJOR:-0}" -eq 5 ] && [ "${PYTEST_BASH_MINOR:-0}" -ge 1 ]; }; }; then
  PYTEST_ENV=(env "BASH_COMPAT=${UI_CLONE_TEST_BASH_COMPAT:-5.0}")
fi

# 1. Tests
# Parallelised with xdist. `--dist loadfile` keeps every test in a file on ONE
# worker, which removes intra-file ordering and shared-fixture races without
# anyone having to enumerate which files those are. Cap the shared-host default
# at four workers: many tests launch shell/browser subprocesses with deliberate
# wall-clock bounds, and `auto` can oversubscribe developer machines enough to
# turn those bounds into false failures. NOTHING is subset or skipped.
# The drift smoke test (step 6, test-parity.sh) is a separate shell step that
# xdist never sees, so it is unaffected by worker count.
# Override with UI_CLONE_PYTEST_WORKERS=auto on a dedicated machine or 1 to
# bisect a suspected isolation bug.
DEFAULT_PYTEST_WORKERS=$(python3 -c 'import os; print(min(os.cpu_count() or 1, 4))')
PYTEST_WORKERS="${UI_CLONE_PYTEST_WORKERS:-$DEFAULT_PYTEST_WORKERS}"
step "Tests"
if [ "$QUIET" = "1" ]; then
  run_quiet "tests" "${PYTEST_ENV[@]}" uv run python -m pytest tests/ -q \
    -n "$PYTEST_WORKERS" --dist loadfile
else
  "${PYTEST_ENV[@]}" uv run python -m pytest tests/ -q \
    -n "$PYTEST_WORKERS" --dist loadfile || fail "tests"
fi

# 2. Type check (mypy)
step "Type check"
if [ "$QUIET" = "1" ]; then
  run_quiet "mypy" uv run python -m mypy ui_clone/ tests/ \
    skills/visual-debug/scripts/replay-track-compare.py
else
  uv run python -m mypy ui_clone/ tests/ \
    skills/visual-debug/scripts/replay-track-compare.py || fail "mypy"
fi

# 3. Lint check (ruff)
step "Lint check"
if [ "$QUIET" = "1" ]; then
  run_quiet "ruff" uv run python -m ruff check ui_clone/ tests/ \
    skills/visual-debug/scripts/replay-track-compare.py
else
  uv run python -m ruff check ui_clone/ tests/ \
    skills/visual-debug/scripts/replay-track-compare.py || fail "ruff"
fi

# 4. Shell syntax check
# Use bash 4+ explicitly. macOS ships bash 3.2 as /bin/bash, which mis-lexes
# quoted heredocs containing apostrophes (a known 3.2 bug). All target hosts
# (GitHub Actions Ubuntu, Linux installs, macOS users with `brew install bash`)
# run bash 4+, so we enforce that minimum here.
step "Shell syntax check"
BASH_BIN=$(command -v bash)
BASH_MAJOR=$("$BASH_BIN" -c 'echo ${BASH_VERSION%%.*}')
if [ "${BASH_MAJOR:-0}" -lt 4 ]; then
  for cand in /opt/homebrew/bin/bash /usr/local/bin/bash; do
    if [ -x "$cand" ]; then BASH_BIN="$cand"; break; fi
  done
  BASH_MAJOR=$("$BASH_BIN" -c 'echo ${BASH_VERSION%%.*}')
fi
if [ "${BASH_MAJOR:-0}" -lt 4 ]; then
  fail "shell syntax: bash 4+ required (found $($BASH_BIN --version | head -1)). On macOS: brew install bash"
fi
for f in install.sh scripts/ci/*.sh scripts/hooks/*.sh scripts/extract/*.sh scripts/verify/*.sh hooks/*.sh skills/visual-debug/scripts/*.sh; do
  "$BASH_BIN" -n "$f" || fail "shell syntax: $f"
done
[ "$QUIET" = "1" ] || echo "  ✓ all shell scripts parse (bash $BASH_MAJOR.x at $BASH_BIN)"

# 5. Review checks
# UI_CLONE_REVIEW_SKIP_TESTS=1 is exported globally at the top of this file,
# so review.sh here AND review.sh inside test-parity.sh (step 6) both skip
# the duplicate pytest sweep. Security is NOT skipped here because ci-local
# doesn't run pre-push-security directly; pre-push-guard.sh exports
# UI_CLONE_REVIEW_SKIP_SECURITY=1 when IT already ran pre-push-security
# ahead of calling ci-local.
step "Review checks"
if [ "$QUIET" = "1" ]; then
  run_quiet "review.sh" bash scripts/ci/review.sh --quiet
else
  bash scripts/ci/review.sh || fail "review.sh"
fi

# 5b. Universality gate — blocks maintainer-bias drift (loop-N attribution,
# benchmark site names, brand leakage, personal paths, Hangul in production
# source). See scripts/ci/check-universality.sh header for the full set.
step "Universality (no maintainer-bias drift)"
if [ "$QUIET" = "1" ]; then
  run_quiet "check-universality.sh" bash scripts/ci/check-universality.sh
else
  bash scripts/ci/check-universality.sh || fail "check-universality.sh"
fi

# 6. Drift smoke test — verifies review.sh + pre-push-security.sh still catch
# known-bad mutations. Prevents the guards rotting silently (regex breaking,
# denylist entry getting dropped, language scanner no-opping on a platform).
step "Drift smoke test"
if [ "$QUIET" = "1" ]; then
  run_quiet "test-parity.sh" bash scripts/ci/test-parity.sh
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
