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
# Off-pipeline activation (omx postmortem): the external-browse breadcrumb
# detector lives behind this fast-skip, but the off-pipeline flow never
# creates tmp/ref — so the detector depended on a directory its target flow
# never makes. Two extra activation conditions, both cheap:
#   1. an existing crumb dir (tmp/.ui-re-external-browse) at any anchor
#      keeps the stack live for the rest of that session's enforcement;
#   2. a payload that itself opens an external URL via agent-browser must
#      reach pre_bash so the FIRST crumb can be written in a no-tmp/ref
#      tree. Payload is captured here and re-fed to the module unchanged.
_found_crumbs() {
  [[ -d "${CLAUDE_PROJECT_DIR}/tmp/.ui-re-external-browse" ]] && return 0
  local gr; gr="$(git rev-parse --show-toplevel 2>/dev/null)"
  [[ -n "$gr" && -d "$gr/tmp/.ui-re-external-browse" ]] && return 0
  local d="$PWD"
  while [[ "$d" != "/" ]]; do
    [[ -d "$d/tmp/.ui-re-external-browse" ]] && return 0
    d="$(dirname "$d")"
  done
  return 1
}
_payload=""
if ! _found_ref && ! _found_crumbs; then
  _payload="$(cat 2>/dev/null || true)"
  case "${1:-}:$_payload" in
    ui_clone.hooks.claude_continuation:*) : ;;
    *agent-browser*open*http*) : ;;  # external browse — proceed to write the crumb
    *) exit 0 ;;
  esac
fi
if ! command -v uv >/dev/null 2>&1; then
  # shellcheck disable=SC2016
  echo 'ui-clone-skills: uv not found. Install: uv_tmp=$(mktemp) && curl -LsSf -o "$uv_tmp" https://astral.sh/uv/install.sh && sh "$uv_tmp" && rm -f "$uv_tmp"' >&2
  exit 0
fi
script_path="${BASH_SOURCE[0]:-$0}"
if command -v realpath >/dev/null 2>&1; then
  script_path="$(realpath "$script_path" 2>/dev/null || printf '%s' "$script_path")"
fi
project_root="$(cd "$(dirname "$script_path")/.." && pwd)"
# Re-feed a pre-captured payload (external-browse activation path) or pass
# stdin straight through (normal path).
if [[ -n "$_payload" ]]; then
  exec uv run --project "$project_root" python -m "$@" <<< "$_payload"
fi
exec uv run --project "$project_root" python -m "$@"
