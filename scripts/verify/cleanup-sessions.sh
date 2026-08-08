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

_matching_sessions() {
  # `agent-browser session list` prints "Active sessions:" then "  <name>"
  # lines. Bash 3.2 on macOS lacks `mapfile`, so return newline-delimited text.
  agent-browser session list 2>/dev/null \
    | awk -v p="${PREFIX}" '/^  / { sub(/^  +/, ""); if (index($0, p) == 1) print }'
}

MATCHES=$(_matching_sessions)

if [ -z "$MATCHES" ]; then
  echo "no active sessions matching prefix '${PREFIX}'"
  # Session unregister and Chrome teardown are asynchronous on some releases.
  # A tiny settle prevents the next open from racing the previous daemon exit
  # even when the registry is already empty.
  sleep "${UI_CLONE_SESSION_SETTLE_SEC:-0.2}"
  exit 0
fi

COUNT=$(printf '%s\n' "$MATCHES" | wc -l | tr -d ' ')
echo "matched ${COUNT} session(s) with prefix '${PREFIX}':"
printf '%s\n' "$MATCHES" | sed 's/^/  /'

if [ "$DRY" -eq 1 ]; then
  echo "(dry run — no sessions closed)"
  exit 0
fi

CLOSE_FAILURES=""
while IFS= read -r name; do
  [ -z "$name" ] && continue
  if agent-browser --session "$name" close >/dev/null 2>&1; then
    echo "  ✓ closed $name"
  else
    echo "  ! close returned nonzero for $name; waiting for registry settle" >&2
    CLOSE_FAILURES="${CLOSE_FAILURES}${name}
"
  fi
done <<< "$MATCHES"

# A successful close response can precede registry removal by a few hundred
# milliseconds. Wait until this exact run prefix is truly absent before the
# caller launches another browser family. A nonzero close response is also
# provisional: agent-browser can return before an already-started unregister is
# visible to `session list`.
REMAINING=""
WAIT_ATTEMPT=0
while [ "$WAIT_ATTEMPT" -lt 20 ]; do
  REMAINING=$(_matching_sessions)
  [ -z "$REMAINING" ] && break
  WAIT_ATTEMPT=$((WAIT_ATTEMPT + 1))
  sleep 0.1
done
if [ -n "$REMAINING" ]; then
  echo "session cleanup did not settle for prefix '${PREFIX}':" >&2
  printf '%s\n' "$REMAINING" | sed 's/^/  /' >&2
  if [ -n "$CLOSE_FAILURES" ]; then
    echo "close returned nonzero for:" >&2
    printf '%s\n' "$CLOSE_FAILURES" | sed '/^$/d; s/^/  /' >&2
  fi
  exit 1
fi
if [ -n "$CLOSE_FAILURES" ]; then
  echo "nonzero close response settled after registry removal"
fi
sleep "${UI_CLONE_SESSION_SETTLE_SEC:-0.2}"
