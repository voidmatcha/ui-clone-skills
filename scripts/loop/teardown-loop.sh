#!/usr/bin/env bash
# teardown-loop.sh — reap everything a validation loop leaves behind.
#
# Loops historically leaked: agent-browser sessions (hold Chrome + ports),
# and impl dev servers (vite/next) whose cwd lives under scratch/loop-*/ —
# loop-e2e-1's server outlived its tab and kept serving a stale impl on a
# port later probes could hit. Run this between loops (after closing the
# purplemux tab).
#
# Usage: teardown-loop.sh [<loop-dir-substring>]
#   <loop-dir-substring>  only kill dev servers whose cwd matches this
#                         (default: scratch/loop-)
#
# Kills ONLY processes whose cwd is under the repo's scratch loop dirs.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MATCH="${1:-scratch/loop-}"

echo "▸ agent-browser sessions:"
agent-browser close --all 2>&1 | sed 's/^/  /' || echo "  (agent-browser not available)"

echo "▸ loop dev servers (cwd match: ${MATCH}):"
KILLED=0
for pid in $(lsof -iTCP -sTCP:LISTEN -P 2>/dev/null | awk '$1=="node" {print $2}' | sort -u); do
  cwd=$(lsof -p "$pid" 2>/dev/null | awk '$4=="cwd" {print $NF}')
  case "$cwd" in
    "$REPO_ROOT"/*"$MATCH"*|*"$MATCH"*)
      if [[ "$cwd" == "$REPO_ROOT"* ]]; then
        echo "  kill $pid ($cwd)"
        kill "$pid" 2>/dev/null && KILLED=$((KILLED + 1))
      fi
      ;;
  esac
done
echo "  reaped: $KILLED"
exit 0
