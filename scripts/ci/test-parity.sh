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

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)" || {
  echo "test-parity.sh: cannot resolve repo root" >&2
  exit 1
}
cd "$REPO_ROOT" || {
  echo "test-parity.sh: cannot cd to $REPO_ROOT" >&2
  exit 1
}

PASS=0
FAIL=0
#
# Entry-time recovery: if a prior interrupted run
# left mutations in the tree, this run would mutate-on-mutation and the cleanup
# would only restore the mid-stream state. Hard-reset the known-mutated files
# from git HEAD before doing anything else. Use `git checkout` not `git restore`
# for portability on older git versions in CI.
KNOWN_MUTATED_PATHS=(
  ".codex-plugin/plugin.json"
  ".claude-plugin/plugin.json"
  "AGENTS.md"
)
for p in "${KNOWN_MUTATED_PATHS[@]}"; do
  if [ -f "$p" ] && [[ "$p" == *.json ]]; then
    # Reset on invalid JSON OR the 9.9.9 version-drift sentinel. Case 2 mutates a
    # manifest version to "9.9.9" (VALID JSON), so the JSON-validity check alone
    # missed a leaked mutation from an interrupted run — the backup then captured
    # 9.9.9 and restore re-applied it, a self-perpetuating leak that blocked every
    # later push on "version mismatch". 9.9.9 is never a real version, so resetting
    # any manifest carrying it from HEAD is safe and breaks the cycle.
    if ! python3 -c "import json,sys; json.load(open('$p'))" 2>/dev/null \
       || grep -q '"9\.9\.9"' "$p" 2>/dev/null; then
      git checkout -- "$p" 2>/dev/null || true
    fi
  fi
done
# AGENTS.md is not JSON; check for the canonical drift-test trailing pattern
# (Hangul or `{ broken` token) and reset if present.
if grep -qE 'drift-test|\{ broken' AGENTS.md 2>/dev/null; then
  git checkout -- AGENTS.md 2>/dev/null || true
fi
BACKUP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ui-re-parity.XXXXXX")" || exit 1
TOUCHED=()

cleanup() {
  for f in "${TOUCHED[@]:-}"; do
    if [ -n "$f" ] && [ -f "$BACKUP_DIR/$f" ]; then
      mkdir -p "$(dirname "$f")"
      cp "$BACKUP_DIR/$f" "$f"
    fi
  done
  rm -rf "$BACKUP_DIR"
}
trap cleanup EXIT INT TERM

backup() {
  local f="$1"
  mkdir -p "$BACKUP_DIR/$(dirname "$f")"
  if [ ! -f "$BACKUP_DIR/$f" ]; then
    cp "$f" "$BACKUP_DIR/$f"
    TOUCHED+=("$f")
  fi
}

restore() {
  local f="$1"
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
# pattern's {n,} floor; placed inside a drift-test comment so the entry-time
# recovery above resets AGENTS.md if a marker ever leaks past restore().
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
