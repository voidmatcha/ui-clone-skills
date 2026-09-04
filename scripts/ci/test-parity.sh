#!/usr/bin/env bash
# Drift smoke test for review.sh + pre-push-security.sh guards.
# Each case applies a known-bad mutation to a tracked file, runs the relevant
# CI guard, asserts the expected error substring appears, then restores from
# a backup. Prevents the guards silently rotting (e.g. regex breaking, denylist
# pattern getting dropped, language scanner no-opping on a platform).
#
# Run: bash scripts/ci/test-parity.sh
# Exit: 0 = all cases caught, 1 = at least one guard failed to fire.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)" || {
  echo "test-parity.sh: cannot resolve repo root" >&2
  exit 1
}
cd "$REPO_ROOT" || {
  echo "test-parity.sh: cannot cd to $REPO_ROOT" >&2
  exit 1
}

# Drift mutations must never touch the caller's shared checkout. The parent
# overlays tracked diffs plus nonignored untracked files into a disposable linked
# worktree; ignored/private files stay out of scope. The child may edit only the
# content of known mutation targets after proving it came from that parent run.
PARITY_CHILD_ROOT="${UI_CLONE_PARITY_CHILD_ROOT:-}"
PARITY_CHILD_TOKEN="${UI_CLONE_PARITY_CHILD_TOKEN:-}"
PARITY_CHILD_TOKEN_FILE="${UI_CLONE_PARITY_CHILD_TOKEN_FILE:-}"
if [ -n "$PARITY_CHILD_ROOT$PARITY_CHILD_TOKEN$PARITY_CHILD_TOKEN_FILE" ]; then
  if [ -z "$PARITY_CHILD_ROOT" ] || [ -z "$PARITY_CHILD_TOKEN" ] || [ -z "$PARITY_CHILD_TOKEN_FILE" ]; then
    echo "test-parity.sh: isolated child capability missing" >&2
    exit 2
  fi
  if [ "${#PARITY_CHILD_TOKEN}" -ne 64 ] || [[ "$PARITY_CHILD_TOKEN" == *[!0123456789abcdef]* ]]; then
    echo "test-parity.sh: isolated child capability invalid" >&2
    exit 2
  fi
  EXPECTED_CHILD_ROOT="$(cd "$PARITY_CHILD_ROOT" 2>/dev/null && pwd -P)" || {
    echo "test-parity.sh: isolated child root unavailable" >&2
    exit 2
  }
  TOKEN_DIR="$(cd "$(dirname "$PARITY_CHILD_TOKEN_FILE")" 2>/dev/null && pwd -P)" || {
    echo "test-parity.sh: isolated child capability unavailable" >&2
    exit 2
  }
  WORKTREE_PARENT="$(cd "$EXPECTED_CHILD_ROOT/.." 2>/dev/null && pwd -P)" || {
    echo "test-parity.sh: isolated child parent unavailable" >&2
    exit 2
  }
  if [ "$TOKEN_DIR" != "$WORKTREE_PARENT" ] || [ ! -f "$PARITY_CHILD_TOKEN_FILE" ] || [ -L "$PARITY_CHILD_TOKEN_FILE" ]; then
    echo "test-parity.sh: isolated child capability invalid" >&2
    exit 2
  fi
  {
    IFS= read -r STORED_CHILD_TOKEN || true
    IFS= read -r STORED_CHILD_ROOT || true
  } < "$PARITY_CHILD_TOKEN_FILE"
  GIT_DIR="$(git -C "$REPO_ROOT" rev-parse --git-dir 2>/dev/null)" || exit 2
  GIT_COMMON_DIR="$(git -C "$REPO_ROOT" rev-parse --git-common-dir 2>/dev/null)" || exit 2
  case "$GIT_DIR" in /*) ;; *) GIT_DIR="$REPO_ROOT/$GIT_DIR" ;; esac
  case "$GIT_COMMON_DIR" in /*) ;; *) GIT_COMMON_DIR="$REPO_ROOT/$GIT_COMMON_DIR" ;; esac
  GIT_DIR="$(cd "$GIT_DIR" 2>/dev/null && pwd -P)" || exit 2
  GIT_COMMON_DIR="$(cd "$GIT_COMMON_DIR" 2>/dev/null && pwd -P)" || exit 2
  if [ "$REPO_ROOT" != "$EXPECTED_CHILD_ROOT" ] \
     || [ "$GIT_DIR" = "$GIT_COMMON_DIR" ] \
     || [[ "$GIT_DIR" != "$GIT_COMMON_DIR"/worktrees/* ]] \
     || [ "$STORED_CHILD_TOKEN" != "$PARITY_CHILD_TOKEN" ] \
     || [ "$STORED_CHILD_ROOT" != "$EXPECTED_CHILD_ROOT" ]; then
    echo "test-parity.sh: isolated child validation failed" >&2
    exit 2
  fi
  rm -f -- "$PARITY_CHILD_TOKEN_FILE"
else
  PARITY_PARENT="$(mktemp -d "${TMPDIR:-/tmp}/ui-re-parity-worktree.XXXXXX")" \
    || { echo "test-parity.sh: cannot create isolated worktree parent" >&2; exit 1; }
  PARITY_WORKTREE="$PARITY_PARENT/worktree"
  PARITY_PATCH="$PARITY_PARENT/tracked.patch"
  PARITY_UNTRACKED="$PARITY_PARENT/untracked.list"
  PARITY_CHILD_TOKEN_FILE="$PARITY_PARENT/child-token"
  PARITY_CHILD_TOKEN=""
  PARITY_CHILD_PID=""

  cleanup_isolated_worktree() {
    local status=$?
    trap - EXIT
    if [ -d "$PARITY_WORKTREE" ]; then
      git -C "$REPO_ROOT" worktree remove --force "$PARITY_WORKTREE" >/dev/null 2>&1 || true
    fi
    rm -f "$PARITY_PATCH" "$PARITY_UNTRACKED" "$PARITY_CHILD_TOKEN_FILE"
    rmdir "$PARITY_PARENT" 2>/dev/null || true
    exit "$status"
  }
  forward_isolation_signal() {
    local signal="$1"
    local code="$2"
    trap - HUP INT TERM
    if [ -n "$PARITY_CHILD_PID" ]; then
      kill -s "$signal" "$PARITY_CHILD_PID" 2>/dev/null || true
      wait "$PARITY_CHILD_PID" 2>/dev/null || true
    fi
    exit "$code"
  }
  trap cleanup_isolated_worktree EXIT
  trap 'forward_isolation_signal HUP 129' HUP
  trap 'forward_isolation_signal INT 130' INT
  trap 'forward_isolation_signal TERM 143' TERM

  if ! git -C "$REPO_ROOT" worktree add --detach --quiet "$PARITY_WORKTREE" HEAD; then
    echo "test-parity.sh: cannot create isolated worktree" >&2
    exit 1
  fi
  PARITY_WORKTREE="$(cd "$PARITY_WORKTREE" 2>/dev/null && pwd -P)" || {
    echo "test-parity.sh: cannot resolve isolated worktree" >&2
    exit 1
  }
  if ! git -C "$REPO_ROOT" diff --binary --no-ext-diff HEAD -- . > "$PARITY_PATCH"; then
    echo "test-parity.sh: cannot snapshot tracked working-tree changes" >&2
    exit 1
  fi
  if [ -s "$PARITY_PATCH" ] \
     && ! git -C "$PARITY_WORKTREE" apply --whitespace=nowarn "$PARITY_PATCH"; then
    echo "test-parity.sh: cannot apply tracked changes to isolated worktree" >&2
    exit 1
  fi
  if ! git -C "$REPO_ROOT" ls-files --others --exclude-standard -z > "$PARITY_UNTRACKED"; then
    echo "test-parity.sh: cannot snapshot untracked working-tree files" >&2
    exit 1
  fi
  if ! python3 - "$REPO_ROOT" "$PARITY_WORKTREE" "$PARITY_UNTRACKED" <<'PY'
import os
import pathlib
import shutil
import sys

source_root = pathlib.Path(sys.argv[1])
target_root = pathlib.Path(sys.argv[2])
for raw in pathlib.Path(sys.argv[3]).read_bytes().split(b"\0"):
    if not raw:
        continue
    relative = pathlib.Path(os.fsdecode(raw))
    if relative.is_absolute() or ".." in relative.parts:
        raise SystemExit(f"unsafe untracked path: {relative}")
    source = source_root / relative
    target = target_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        target.symlink_to(os.readlink(source))
    elif source.is_file():
        shutil.copy2(source, target, follow_symlinks=False)
    else:
        raise SystemExit(f"unsupported untracked path: {relative}")
PY
  then
    echo "test-parity.sh: cannot copy untracked files to isolated worktree" >&2
    exit 1
  fi

  if ! PARITY_CHILD_TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
  )"; then
    echo "test-parity.sh: cannot create isolated child token" >&2
    exit 1
  fi
  if ! { printf '%s\n' "$PARITY_CHILD_TOKEN"; printf '%s\n' "$PARITY_WORKTREE"; } > "$PARITY_CHILD_TOKEN_FILE"; then
    echo "test-parity.sh: cannot write isolated child token" >&2
    exit 1
  fi
  chmod 600 "$PARITY_CHILD_TOKEN_FILE" 2>/dev/null || true

  python3 - "$PARITY_WORKTREE/scripts/ci/test-parity.sh" "$PARITY_WORKTREE" "$PARITY_CHILD_TOKEN" "$PARITY_CHILD_TOKEN_FILE" <<'PY' &
import os
import signal
import subprocess
import sys

script, child_root, child_token, child_token_file = sys.argv[1:]
environment = os.environ.copy()
environment["UI_CLONE_PARITY_CHILD_ROOT"] = child_root
environment["UI_CLONE_PARITY_CHILD_TOKEN"] = child_token
environment["UI_CLONE_PARITY_CHILD_TOKEN_FILE"] = child_token_file
child = None
pending_signals = []

def forward(signum, _frame):
    if child is None:
        pending_signals.append(signum)
        return
    try:
        os.killpg(child.pid, signum)
    except ProcessLookupError:
        pass

for forwarded_signal in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
    signal.signal(forwarded_signal, forward)
child = subprocess.Popen(["bash", script], env=environment, start_new_session=True)
for pending_signal in pending_signals:
    forward(pending_signal, None)
raise SystemExit(child.wait())
PY
  PARITY_CHILD_PID=$!
  wait "$PARITY_CHILD_PID"
  PARITY_RESULT=$?
  PARITY_CHILD_PID=""
  exit "$PARITY_RESULT"
fi

PASS=0
FAIL=0
BACKUP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ui-re-parity.XXXXXX")" || exit 1
TOUCHED=()

cleanup() {
  for f in "${TOUCHED[@]:-}"; do
    if [ -n "$f" ] && [ -f "$BACKUP_DIR/$f" ] && mutation_target_is_safe "$f"; then
      mkdir -p "$(dirname "$f")"
      cp "$BACKUP_DIR/$f" "$f"
    fi
  done
  rm -rf "$BACKUP_DIR"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

mutation_target_is_safe() {
  python3 - "$REPO_ROOT" "$1" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve(strict=True)
path = pathlib.Path(sys.argv[2])
if path.is_symlink():
    raise SystemExit(1)
try:
    resolved = path.resolve(strict=True)
    resolved.relative_to(root)
except (FileNotFoundError, ValueError):
    raise SystemExit(1)
raise SystemExit(0 if resolved.is_file() else 1)
PY
}

require_mutation_target() {
  if ! mutation_target_is_safe "$1"; then
    echo "test-parity.sh: unsafe mutation target: $1" >&2
    return 1
  fi
}

backup() {
  local f="$1"
  require_mutation_target "$f" || exit 2
  mkdir -p "$BACKUP_DIR/$(dirname "$f")"
  if [ ! -f "$BACKUP_DIR/$f" ]; then
    cp "$f" "$BACKUP_DIR/$f"
    TOUCHED+=("$f")
  fi
}

restore() {
  local f="$1"
  require_mutation_target "$f" || exit 2
  [ -f "$BACKUP_DIR/$f" ] && cp "$BACKUP_DIR/$f" "$f"
}

assert_fails() {
  local name="$1"
  local guard="$2"        # path to ci script
  local expected="$3"
  local output
  output=$(bash "$guard" --quiet 2>&1 || true)
  if echo "$output" | grep -qF "$expected"; then
    echo "  [PASS] $name"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] $name — expected substring not found: '$expected'" >&2
    echo "$output" | sed 's/^/         /' >&2
    FAIL=$((FAIL + 1))
  fi
}

mutate() {
  require_mutation_target "$1" || exit 2
  python3 - "$1" "$2" "$3" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
old = sys.argv[2]
new = sys.argv[3]
text = path.read_text()
if old not in text:
    sys.exit(f"mutate: substring not found in {path}: {old!r}")
path.write_text(text.replace(old, new, 1))
PY
}

append() {
  require_mutation_target "$1" || exit 2
  printf '%s' "$2" >> "$1"
}

echo "-- Drift smoke test --"

SEC="scripts/ci/pre-push-security.sh"
REV="scripts/ci/review.sh"

# Case 1: secret scanner — fake-but-shaped AWS access key id.
file="AGENTS.md"
backup "$file"
append "$file" $'\n<!-- drift-test: AKIAIOSFODNN7EXAMPLE -->\n'
assert_fails "Secrets — AKIA AWS key shape" "$SEC" "Potential secret"
restore "$file"

# Cases 1b–1i: the secret formats added alongside AKIA must each stay caught
# (drift guards them against a future regex edit silently dropping coverage).
# Synthetic shaped values, lengths built with printf so they always satisfy the
# pattern's {n,} floor; placed inside a drift-test comment. The isolated
# worktree makes a leaked marker disposable if a restore ever fails.
backup "$file"
append "$file" $'\n<!-- drift-test: sk-ant-api03-'"$(printf 'A%.0s' $(seq 1 40))"$' -->\n'
assert_fails "Secrets — Anthropic sk-ant key shape" "$SEC" "Potential secret"
restore "$file"

backup "$file"
append "$file" $'\n<!-- drift-test: github_pat_'"$(printf 'A%.0s' $(seq 1 82))"$' -->\n'
assert_fails "Secrets — GitHub fine-grained PAT shape" "$SEC" "Potential secret"
restore "$file"

backup "$file"
append "$file" $'\n<!-- drift-test: sk_live_'"$(printf 'A%.0s' $(seq 1 30))"$' -->\n'
assert_fails "Secrets — Stripe sk_live key shape" "$SEC" "Potential secret"
restore "$file"

backup "$file"
append "$file" $'\n<!-- drift-test: gho_'"$(printf 'A%.0s' $(seq 1 36))"$' -->\n'
assert_fails "Secrets — GitHub classic-family token shape" "$SEC" "Potential secret"
restore "$file"

backup "$file"
append "$file" $'\n<!-- drift-test: glpat-'"$(printf 'A%.0s' $(seq 1 20))"$' -->\n'
assert_fails "Secrets — GitLab PAT shape" "$SEC" "Potential secret"
restore "$file"

backup "$file"
append "$file" $'\n<!-- drift-test: xapp-1-'"$(printf 'A%.0s' $(seq 1 16))"$' -->\n'
assert_fails "Secrets — Slack app-level token shape" "$SEC" "Potential secret"
restore "$file"

backup "$file"
append "$file" $'\n<!-- drift-test: _authToken=npm_'"$(printf 'A%.0s' $(seq 1 24))"$' -->\n'
assert_fails "Secrets — npm _authToken assignment shape" "$SEC" "Potential secret"
restore "$file"

backup "$file"
append "$file" $'\n<!-- drift-test: npm_'"$(printf 'A%.0s' $(seq 1 36))"$' -->\n'
assert_fails "Secrets — npm classic token shape" "$SEC" "Potential secret"
restore "$file"

# Case 1j (N2): a real-shaped key co-located with a placeholder WORD in prose
# (# TODO rotate) must still be CAUGHT. The old whole-line suppressor dropped the
# entire line because it contained TODO; the token-scoped suppressor inspects only
# the matched token, which here is a real-shaped key with no placeholder marker.
backup "$file"
append "$file" $'\n<!-- drift-test: sk-ant-api03-'"$(printf 'A%.0s' $(seq 1 40))"$' # TODO rotate -->\n'
assert_fails "Secrets — real-shaped key beside a # TODO comment still caught (N2)" "$SEC" "Potential secret"
restore "$file"

# Case 2: version sync — make .codex-plugin/plugin.json diverge.
file=".codex-plugin/plugin.json"
backup "$file"
current=$(python3 -c "import json; print(json.load(open('$file'))['version'])")
mutate "$file" "\"version\": \"$current\"" "\"version\": \"9.9.9\""
assert_fails "Version sync — codex-plugin drift" "$SEC" "version mismatch"
restore "$file"

# Case 3: manifest validity — break JSON syntax in .claude-plugin/plugin.json.
file=".claude-plugin/plugin.json"
backup "$file"
append "$file" $'{ broken'
assert_fails "Manifest validity — plugin.json broken JSON" "$SEC" "invalid JSON"
restore "$file"

# Case 4: language check — inject Hangul into AGENTS.md.
file="AGENTS.md"
backup "$file"
append "$file" $'\n<!-- drift-test: \xed\x95\x9c\xea\xb5\xad\xec\x96\xb4 -->\n'
assert_fails "Language — Hangul in AGENTS.md" "$REV" "Non-English (Hangul) text found"
restore "$file"

echo ""
echo "========================================"
echo "  Drift smoke: $PASS passed, $FAIL failed"
echo "========================================"

[ "$FAIL" -gt 0 ] && exit 1
exit 0
