#!/usr/bin/env bash
# Register the current Claude Code session as a driver session so the Stop
# hook bypasses on it. Append-if-missing semantics under a file lock —
# multiple concurrent driver sessions coexist without stomping each other.
#
# Usage:
#   bash scripts/register-driver-session.sh <session-id>
#   bash scripts/register-driver-session.sh         # reads $CLAUDE_CODE_SESSION_ID
#
# This is a thin shim over `python -m ui_clone.driver_session`. The Python
# module owns the locking + append-if-missing logic so it's pytest-testable
# and portable across macOS / Linux. See ui_clone/driver_session.py for the
# semantics; see ui_clone/hooks/section_gate._is_driver_session for the reader.
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
register-driver-session.sh — add current session to .driver-session.id

Usage:
  $0 <session-id>      # explicit
  $0                   # uses \$CLAUDE_CODE_SESSION_ID

The marker is gitignored local state. The Stop hook treats it as a
newline-delimited set of session IDs and bypasses when the current
session matches any entry. Stale entries are fine — only the live
session's id needs to match.
EOF
  exit 0
fi

if [[ $# -ge 1 && -n "${1:-}" ]]; then
  exec uv run python -m ui_clone.driver_session register "$1"
else
  exec uv run python -m ui_clone.driver_session register-from-env
fi
