#!/usr/bin/env bash
# Close every agent-browser session whose name starts with the given prefix.
#
# Usage:
#   bash scripts/verify/cleanup-sessions.sh <prefix>          # close sessions matching ^<prefix>
#   bash scripts/verify/cleanup-sessions.sh <prefix> --dry    # list only, don't close
#
# Why this exists: prior projects accumulated 250+ unique --session names
# (e.g. `375-foo-debug`, `375-replay-sc`, `375-meas-cmp`). Each leaves a
# Chrome instance + helper processes alive until explicitly closed. This
# script closes them all in one call at end of run.
#
# Safety: only closes sessions whose names START with <prefix>. Never use a
# 1-char prefix or empty string — that would close every active session
# including ones owned by other Claude tabs.
set -euo pipefail

PREFIX="${1:-}"
DRY=0
[ "${2:-}" = "--dry" ] && DRY=1

if [ -z "$PREFIX" ] || [ "${#PREFIX}" -lt 3 ]; then
  echo "usage: $0 <prefix-of-3+chars> [--dry]" >&2
  echo "  refusing to run with empty or <3-char prefix — too broad" >&2
  exit 2
fi

if ! command -v agent-browser >/dev/null 2>&1; then
  echo "agent-browser not on PATH" >&2
  exit 1
fi

# `agent-browser session list` prints "Active sessions:" then "  <name>" lines.
# Bash 3.2 on macOS lacks `mapfile`, so collect via newline-delimited string.
MATCHES=$(
  agent-browser session list 2>/dev/null \
    | awk -v p="^${PREFIX}" '/^  / { sub(/^  +/, ""); if ($0 ~ p) print }'
)

if [ -z "$MATCHES" ]; then
  echo "no active sessions matching prefix '${PREFIX}'"
  exit 0
fi

COUNT=$(printf '%s\n' "$MATCHES" | wc -l | tr -d ' ')
echo "matched ${COUNT} session(s) with prefix '${PREFIX}':"
printf '%s\n' "$MATCHES" | sed 's/^/  /'

if [ "$DRY" -eq 1 ]; then
  echo "(dry run — no sessions closed)"
  exit 0
fi

FAILED=0
while IFS= read -r name; do
  [ -z "$name" ] && continue
  if agent-browser --session "$name" close >/dev/null 2>&1; then
    echo "  ✓ closed $name"
  else
    echo "  ! failed to close $name" >&2
    FAILED=$((FAILED + 1))
  fi
done <<< "$MATCHES"

if [ "$FAILED" -gt 0 ]; then
  echo "${FAILED} session(s) failed to close — run \`agent-browser session list\` to inspect" >&2
  exit 1
fi
