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
BACKUPS=()

cleanup() {
  for b in "${BACKUPS[@]:-}"; do
    if [ -n "$b" ] && [ -f "$b" ]; then
      local f="${b%.parity-backup}"
      mv "$b" "$f"
    fi
  done
}
trap cleanup EXIT INT TERM

backup() {
  cp "$1" "$1.parity-backup"
  BACKUPS+=("$1.parity-backup")
}

restore() {
  local f="$1"
  local b="$1.parity-backup"
  if [ -f "$b" ]; then
    mv "$b" "$f"
    local new=()
    for x in "${BACKUPS[@]}"; do
      [ "$x" != "$b" ] && new+=("$x")
    done
    BACKUPS=("${new[@]:-}")
  fi
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

# Case 1: identity leakage denylist — inject a denylisted name into a tracked file.
file="AGENTS.md"
backup "$file"
append "$file" $'\n<!-- drift-test: navercorp -->\n'
assert_fails "Identity leakage — navercorp pattern" "$SEC" "identity leak"
restore "$file"

# Case 2: identity leakage denylist — CDN host pattern in a different file type.
file="AGENTS.md"
backup "$file"
append "$file" $'\n<!-- drift-test: livecloud-thumb.akamaized.net -->\n'
assert_fails "Identity leakage — CDN host pattern" "$SEC" "identity leak"
restore "$file"

# Case 3: secret scanner — fake-but-shaped AWS access key id.
file="AGENTS.md"
backup "$file"
append "$file" $'\n<!-- drift-test: AKIAIOSFODNN7EXAMPLE -->\n'
assert_fails "Secrets — AKIA AWS key shape" "$SEC" "Potential secret"
restore "$file"

# Case 4: version sync — make .codex-plugin/plugin.json diverge.
file=".codex-plugin/plugin.json"
backup "$file"
current=$(python3 -c "import json; print(json.load(open('$file'))['version'])")
mutate "$file" "\"version\": \"$current\"" "\"version\": \"9.9.9\""
assert_fails "Version sync — codex-plugin drift" "$SEC" "version mismatch"
restore "$file"

# Case 5: manifest validity — break JSON syntax in .claude-plugin/plugin.json.
file=".claude-plugin/plugin.json"
backup "$file"
append "$file" $'{ broken'
assert_fails "Manifest validity — plugin.json broken JSON" "$SEC" "invalid JSON"
restore "$file"

# Case 6: language check — inject Hangul into AGENTS.md.
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
