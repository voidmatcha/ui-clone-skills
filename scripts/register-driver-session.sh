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

# Callers run this from their OWN project directory, not from this plugin's
# checkout, so a bare `uv run python -m ...` (no --project) resolves against
# whatever pyproject.toml `uv` finds walking up from the caller's cwd — the
# caller's own project, not this plugin. Resolve the plugin root from this
# script's own path (same pattern as hooks/shim.sh) and point at the shared
# hook venv so this doesn't rebuild a ~200MB venv inside a version-keyed
# plugin cache copy, or fail outright once `[tool.uv] package = false`
# stopped an editable install from papering over the missing --project.
_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_plugin_root="$(cd "$_script_dir/.." && pwd)"
export UV_PROJECT_ENVIRONMENT="${UI_CLONE_HOOK_VENV:-${XDG_CACHE_HOME:-$HOME/.cache}/ui-clone-skills/hook-venv}"
export PYTHONPATH="$_plugin_root${PYTHONPATH:+:$PYTHONPATH}"

if [[ $# -ge 1 && -n "${1:-}" ]]; then
  exec uv run --project "$_plugin_root" --no-dev --frozen python -m ui_clone.driver_session register "$1"
else
  exec uv run --project "$_plugin_root" --no-dev --frozen python -m ui_clone.driver_session register-from-env
fi
