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
# An armed continuation stores its receipt in <project>/.ui-re-continuation, which
# outlives tmp/ref and is the other state claude_continuation acts on.
_found_receipt() {
  [[ -d "${CLAUDE_PROJECT_DIR}/.ui-re-continuation" ]] && return 0
  local gr; gr="$(git rev-parse --show-toplevel 2>/dev/null)"
  [[ -n "$gr" && -d "$gr/.ui-re-continuation" ]] && return 0
  local d="$PWD"
  while [[ "$d" != "/" ]]; do
    [[ -d "$d/.ui-re-continuation" ]] && return 0
    d="$(dirname "$d")"
  done
  return 1
}
_payload=""
if ! _found_ref && ! _found_crumbs; then
  _payload="$(cat 2>/dev/null || true)"
  # claude_continuation used to bypass the fast-skip unconditionally, which spawned
  # uv plus an interpreter on every prompt in every project — ~1.2s to return None.
  # It only acts on two states, so admit exactly those: a bootstrap invocation of the
  # UI-RE skill in a tree that has no tmp/ref yet, or an existing continuation receipt.
  # Match the bare skill name, NOT "/<name>": activation arrives both as a slash prompt
  # on UserPromptSubmit and as PreToolUse Skill with tool_input.skill set, and only the
  # first carries a leading slash. The payload test is deliberately looser than the
  # module's; this is a pre-filter and the module still makes the exact decision.
  _proceed=0
  case "${1:-}:$_payload" in
    ui_clone.hooks.claude_continuation:*"ui-clone-skills:ui-reverse-engineering"*) _proceed=1 ;;
    *agent-browser*open*http*) _proceed=1 ;;  # external browse — proceed to write the crumb
  esac
  if (( ! _proceed )) &&
     [[ "${1:-}" == "ui_clone.hooks.claude_continuation" ]] &&
     _found_receipt; then
    _proceed=1
  fi
  (( _proceed )) || exit 0
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
# PYTHONSAFEPATH keeps the invoking session's cwd off sys.path. Without it a
# session whose cwd holds its own `ui_clone/` — this repo's dev checkout, or any
# tree with that package name — shadows the installed plugin package, and every
# hook dies with ModuleNotFoundError against a version the checkout predates.
#
# Never propagate a non-zero status. The hook modules signal every outcome as
# stdout JSON and only ever call sys.exit(0), so a non-zero status here is always
# an internal failure: a missing module, a broken venv, an interpreter error.
# Claude treats a failing UserPromptSubmit hook as a block, so propagating it
# rejects the user's prompt outright — a far worse outcome than skipping
# enforcement for one turn. Do not restore `exec` here; it would forward the
# status again. If a module ever needs to block, it must say so in its JSON.
# Re-feed a pre-captured payload (external-browse activation path) or pass
# stdin straight through (normal path).
if [[ -n "$_payload" ]]; then
  PYTHONSAFEPATH=1 uv run --project "$project_root" python -m "$@" <<< "$_payload" || true
  exit 0
fi
PYTHONSAFEPATH=1 uv run --project "$project_root" python -m "$@" || true
exit 0
