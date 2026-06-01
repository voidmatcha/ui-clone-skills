#!/usr/bin/env bash
# Universal shim: fast-skip + delegate to Python module.
# Usage: bash shim.sh <python.module.name> [args...]
# Fast-skip: check CLAUDE_PROJECT_DIR, then git root, then walk up from cwd.
_found_ref() {
  [[ -d "${CLAUDE_PROJECT_DIR}/tmp/ref" ]] && return 0
  local gr; gr="$(git rev-parse --show-toplevel 2>/dev/null)"
  [[ -n "$gr" && -d "$gr/tmp/ref" ]] && return 0
  local d="$PWD"
  while [[ "$d" != "/" ]]; do
    [[ -d "$d/tmp/ref" ]] && return 0
    d="$(dirname "$d")"
  done
  return 1
}
_found_ref || exit 0
if ! command -v uv >/dev/null 2>&1; then
  echo 'ui-clone-skills: uv not found. Install: uv_tmp=$(mktemp) && curl -LsSf -o "$uv_tmp" https://astral.sh/uv/install.sh && sh "$uv_tmp" && rm -f "$uv_tmp"' >&2
  exit 0
fi
exec uv run --project "$(cd "$(dirname "$0")/.." && pwd)" python -m "$@"
