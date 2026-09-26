#!/usr/bin/env bash
# Universal shim: fast-skip + delegate to Python module.
# Usage: bash shim.sh <python.module.name> [args...]
#
# Session-scoped activation. The plugin is meant to stay enabled globally so
# the skills are always available, but its hooks are anti-cheat/discipline
# guards that only matter for the session actually running a ui-clone skill.
# Every hook payload is read here and the Python module (~1s via uv) runs only
# when this session may be subject to enforcement. Must stay bash 3.2
# compatible (macOS /bin/bash): no mapfile, ${v,,}, printf %()T, assoc arrays.
#
# Anchors (where state files are looked up): $CLAUDE_PROJECT_DIR, the git root,
# then every ancestor of $PWD — the same anchor set the folder-based fast-skip
# always used.
#
# With a session_id in the payload (Claude, and Codex hook payloads), proceed
# only when one of these holds; any other session exits 0 here, even in a
# folder full of clone leftovers:
#   1. CLAIM: the payload invokes a public ui-clone skill — UserPromptSubmit
#      naming ui-clone-skills:<skill> (or /<skill>, $<skill>), PreToolUse Skill
#      with tool_input.skill = <skill> (prefixed or bare), or a PreToolUse
#      shell (Bash/exec_command/shell) tool_input.command or .cmd, string or
#      argv array, containing a run pattern (Codex has no Skill tool or
#      UserPromptSubmit route): `-m ui_clone[.pipeline|.gate|.goal|.state|
#      .scoped_check|.scoped_diff]`, the node CLI (`bin/ui-clone`,
#      `ui-clone`) with pipeline/gate/goal/state/scoped-check/scoped-diff or
#      the bare `<url>` form, `ui-clone-cli`, or a visual-debug script
#      (`$SCRIPTS_DIR/<x>.sh`, `.../visual-debug/scripts/<x>.sh`) — unless
#      its segment starts with a non-executing command (echo, grep, cat, git
#      log, bash -n, ...; see _cmd_runs). A claim writes the ownership record
#      <root>/tmp/.ui-re-sessions/<sha256(session_id)> at the first of
#      $CLAUDE_PROJECT_DIR, git root, $PWD.
#   2. OWNED RUN: this session's ownership record exists at an anchor AND run
#      state exists at an anchor (tmp/ref/*/ holding pipeline-state.json,
#      .ui-re-active, extracted.json or element-target.json). Ownership with no
#      run state left (run dirs removed) activates nothing.
#   3. REF OWNERSHIP: a hook already recorded this session on a ref dir
#      (tmp/ref/*/.ui-re-sessions/<sha256>.json, _common.mark_ref_session).
#   4. OFF-PIPELINE DETECTOR (omx postmortem): the external-browse crumb
#      detector catches cloning WITHOUT the skill, so it cannot depend on a
#      claim. A payload that opens an external URL via agent-browser always
#      proceeds (pre_bash must be able to write the FIRST crumb), and a session
#      whose OWN crumb (tmp/.ui-re-external-browse/<sha256>.json) exists stays
#      live. Every crumb consumer reads only its own session's crumb, so
#      scoping activation to that file loses no detection.
#   5. RESUME NOTICE: for session_resume only (SessionStart/PostCompact), a
#      WIP run (tmp/ref/*/.ui-re-active) admits an UNCLAIMED session so the
#      "UI-RE WIP detected" notice survives /clear and new sessions. It is
#      not a claim; UI_CLONE_SESSION_UNCLAIMED=1 tells the module to ask the
#      agent to re-invoke the skill (which claims) before continuing.
#   6. CONTINUATION: for claude_continuation only, this session's own receipt
#      <anchor>/.ui-re-continuation/<session_id>.json (receipts are keyed by
#      session id, so a sibling session's armed receipt never activates us).
#
# Without a session_id (older hosts, or a host that omits it) the legacy
# folder-based behaviour is kept so enforcement never silently turns off:
# proceed when tmp/ref or the crumb dir exists at an anchor, for the
# agent-browser external-open payload, for claude_continuation on a
# ui-reverse-engineering invocation, or with any continuation receipt dir.
#
# The payload is always captured here and re-fed to the module unchanged.
_payload="$(cat 2>/dev/null || true)"
_module="${1:-}"
_git_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
_ANCHORS=()
[[ -n "${CLAUDE_PROJECT_DIR:-}" ]] && _ANCHORS+=("$CLAUDE_PROJECT_DIR")
[[ -n "$_git_root" ]] && _ANCHORS+=("$_git_root")
_d="$PWD"
while [[ -n "$_d" && "$_d" != "/" ]]; do
  _ANCHORS+=("$_d")
  _d="${_d%/*}"
done
_anchor_has_dir() {
  local a
  for a in "${_ANCHORS[@]}"; do [[ -d "$a/$1" ]] && return 0; done
  return 1
}
_anchor_has_file() {
  local a
  for a in "${_ANCHORS[@]}"; do [[ -f "$a/$1" ]] && return 0; done
  return 1
}
# $1 is a glob relative to each anchor (no spaces; the anchor part is quoted).
_anchor_glob() {
  local a f
  for a in "${_ANCHORS[@]}"; do
    # shellcheck disable=SC2231
    for f in "$a"/$1; do [[ -e "$f" ]] && return 0; done
  done
  return 1
}
_found_ref() { _anchor_has_dir "tmp/ref"; }
_found_crumbs() { _anchor_has_dir "tmp/.ui-re-external-browse"; }
# An armed continuation stores its receipt in <project>/.ui-re-continuation, which
# outlives tmp/ref and is the other state claude_continuation acts on.
_found_receipt() { _anchor_has_dir ".ui-re-continuation"; }
_has_run_state() {
  local n
  for n in pipeline-state.json .ui-re-active extracted.json element-target.json; do
    _anchor_glob "tmp/ref/*/$n" && return 0
  done
  return 1
}
_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$1" | sha256sum 2>/dev/null
  elif command -v shasum >/dev/null 2>&1; then
    printf '%s' "$1" | shasum -a 256 2>/dev/null
  else
    printf '%s' "$1" | openssl dgst -sha256 -r 2>/dev/null
  fi
}
_ws='[[:space:]]*'
_skills='(ui-reverse-engineering|ui-capture|visual-debug)'
# Shell-command claims. A missed claim turns enforcement OFF for a real run;
# a false claim only turns it ON for this session. So any run pattern in the
# command claims, wherever it sits (after `uv run --project ... --frozen`,
# inside `bash -lc '...'`, after `sudo`/`xargs`/`do`/`then`/`{`/`!`, ...),
# UNLESS the segment holding it starts with a clearly non-executing command.
# The input is the JSON-escaped command text (a real newline is the two
# characters `\n`, a literal backslash is `\\`). It is split into segments by
# sed/tr — escaped backslashes are neutralized first, so a shell-literal `\n`
# (e.g. inside a printf string) is not a separator — then awk checks each
# segment line by line. sed/tr/awk stay linear on multi-MB commands (awk
# gsub on one huge string does not) and only run when a keyword is present.
# Run patterns (first occurrence per segment):
#   `-m ui_clone[.pipeline|.gate|.goal|.state|.scoped_check|.scoped_diff]`
#     (__main__ dispatches to the pipeline; ui_clone.hooks.*, .metrics, ...
#     do not match);
#   `[.../]ui-clone[@ver]` + a run subcommand or a URL (`hooks`, `help`,
#     `--help` do not), and any `ui-clone-cli`;
#   a visual-debug script: `$SCRIPTS_DIR/<x>.sh`, `${VISUAL_DEBUG_SCRIPTS_DIR}/
#     <x>.sh`, `.../visual-debug/scripts/<x>.sh`.
# Non-executing leaders (after leading space, `(`, `{`, `!`, quotes, and a
# `bash|sh|zsh -<flags>c` wrapper, so the wrapped command decides): echo,
# printf, grep/rg/ag/egrep/fgrep, cat/less/head/tail/bat, sed/awk, jq, man,
# git commit|log|show|diff|grep|blame, bash|sh|zsh -n; and a `#` comment
# before the pattern.
read -r -d '' _claim_awk <<'AWK'
function run_at(t,  b) {
  b = 0
  if (match(t, /(^|[ \t]|\\t|[\\"'])-m([ \t]|\\t)*ui_clone(\.(pipeline|gate|goal|state|scoped_check|scoped_diff))?([^A-Za-z0-9_.]|$)/)) b = RSTART
  if (match(t, /ui-clone(@[^ \t\\"']*)?[\\"']*([ \t]|\\t)+(pipeline|gate|goal|state|scoped-check|scoped-diff|https?:)|ui-clone-cli/) && (!b || RSTART < b)) b = RSTART
  if (match(t, /(\$\{?(VISUAL_DEBUG_)?SCRIPTS_DIR\}?[\\"']*\/|visual-debug\/scripts\/)[A-Za-z0-9_.-]+\.sh/) && (!b || RSTART < b)) b = RSTART
  return b
}
{
  t = $0
  p = run_at(t)
  if (!p) next
  t = substr(t, 1, p - 1)
  if (t ~ /(^|[ \t]|\\t)#/) next
  sub(/^([ \t]|\\t|[({!'"]|\\"|(bash|sh|zsh)([ \t]|\\t)+-[A-Za-z]*c([ \t]|\\t)+)*/, "", t)
  if (t ~ /^(echo|printf|grep|rg|ag|egrep|fgrep|cat|less|head|tail|bat|sed|awk|jq|man)([ \t]|\\t|[\\"']|$)/) next
  if (t ~ /^git([ \t]|\\t)+(commit|log|show|diff|grep|blame)([ \t]|\\t|$)/) next
  if (t ~ /^(bash|sh|zsh)([ \t]|\\t)+-n([ \t]|\\t|[\\"']|$)/) next
  found = 1
  exit
}
END { exit !found }
AWK
# `\\` -> \001 (JSON escapes pair left to right), then `\n`/`\r` -> newline.
_sed_bs=$'s/\\\\\\\\/\001/g'
_sed_nl=$'s/\\\\[nr]/\\\n/g'
_run_kw='ui_clone|ui-clone|SCRIPTS_DIR|visual-debug/scripts/'
# $1: JSON-escaped command text; $2: optional extra sed expression.
_cmd_runs() {
  [[ "$1" =~ $_run_kw ]] || return 1
  printf '%s\n' "$1" | sed -e "${2:-s/^//}" -e "$_sed_bs" -e "$_sed_nl" |
    tr ';&|' '\n\n\n' | awk "$_claim_awk"
}
_shell_tool_re="\"tool_name\"${_ws}:${_ws}\"(Bash|exec_command|shell)\""
# First string-valued `"command"`/`"cmd"`; escaped quotes stay inside the
# capture. A JSON key cannot occur inside a string value unescaped.
_str_field_re="\"(command|cmd)\"${_ws}:${_ws}\"(([^\"\\\\]|\\\\.)*)\""
# First array-valued `"command"`/`"cmd"` (Codex argv form).
_jstr='"([^"\\]|\\.)*"'
_arr_field_re="\"(command|cmd)\"${_ws}:${_ws}\\[${_ws}(${_jstr}(${_ws},${_ws}${_jstr})*)${_ws}\\]"
_claim_memo=""
# Pre-filters only: the modules still make the exact decision.
_is_claim() {
  [[ -n "$_claim_memo" ]] || { _is_claim_uncached && _claim_memo=1 || _claim_memo=0; }
  [[ "$_claim_memo" == 1 ]]
}
_is_claim_uncached() {
  local re arr
  re="\"hook_event_name\"${_ws}:${_ws}\"UserPromptSubmit\""
  if [[ "$_payload" =~ $re ]]; then
    re="(ui-clone-skills:|(^|[^[:alnum:]_./-])[/\$])${_skills}([^[:alnum:]_-]|\$)"
    [[ "$_payload" =~ $re ]] && return 0
  fi
  re="\"tool_name\"${_ws}:${_ws}\"Skill\""
  if [[ "$_payload" =~ $re ]]; then
    re="\"(skill|name)\"${_ws}:${_ws}\"(ui-clone-skills:)?${_skills}\""
    [[ "$_payload" =~ $re ]] && return 0
  fi
  [[ "$_payload" =~ $_shell_tool_re ]] || return 1
  # Only the command about to run claims: not PostToolUse (its tool_response
  # echoes output), and only tool_input.command/cmd, not e.g. `description`.
  re="\"hook_event_name\"${_ws}:${_ws}\"PreToolUse\""
  [[ "$_payload" =~ $re ]] || return 1
  # Cheap literal pre-check before the field regexes on a multi-MB payload.
  [[ "$_payload" =~ $_run_kw ]] || return 1
  if [[ "$_payload" =~ $_str_field_re ]]; then
    _cmd_runs "${BASH_REMATCH[2]}" && return 0
  fi
  if [[ "$_payload" =~ $_arr_field_re ]]; then
    # Join argv elements with spaces: `"bash", "-lc", "<cmd>"` -> bash -lc <cmd>
    arr="${BASH_REMATCH[2]}"
    arr="${arr#\"}"
    arr="${arr%\"}"
    _cmd_runs "$arr" 's/"[[:space:]]*,[[:space:]]*"/ /g' && return 0
  fi
  return 1
}
# Linear-time pre-filter. The equivalent glob `*agent-browser*open*http*`
# backtracks super-linearly on large payloads (seconds at ~20KB of repeats,
# minutes on a 460KB Write payload); a POSIX regex match stays linear.
_ext_open_re='agent-browser.*open.*http'
_is_external_open() {
  [[ "$_payload" =~ $_ext_open_re ]]
}
_sid=""
_sid_re="\"(session_id|sessionId)\"${_ws}:${_ws}\"([^\"\\\\]+)\""
if [[ "$_payload" =~ $_sid_re ]]; then
  _sid="${BASH_REMATCH[2]}"
fi
_h=""
if [[ -n "$_sid" ]] && { _is_claim || _is_external_open || _found_ref || _found_crumbs ||
                          _anchor_has_dir "tmp/.ui-re-sessions" || _found_receipt; }; then
  _h="$(_sha256 "$_sid")"
  _h="${_h%% *}"
  [[ "$_h" =~ ^[0-9a-f]{64}$ ]] || _h=""
  # Hashing failed: fall through to the legacy folder-based path below.
  [[ -n "$_h" ]] || _sid=""
elif [[ -n "$_sid" ]]; then
  # Session known and no ui-clone state anywhere: nothing to enforce.
  exit 0
fi
if [[ -n "$_sid" ]]; then
  _proceed=0
  if _is_claim; then
    _claim_root="${CLAUDE_PROJECT_DIR:-}"
    [[ -n "$_claim_root" && -d "$_claim_root" ]] || _claim_root="$_git_root"
    [[ -n "$_claim_root" ]] || _claim_root="$PWD"
    mkdir -p "$_claim_root/tmp/.ui-re-sessions" 2>/dev/null &&
      : > "$_claim_root/tmp/.ui-re-sessions/$_h" 2>/dev/null
    _proceed=1
  elif _is_external_open; then
    _proceed=1  # external browse — proceed so pre_bash can write the crumb
  elif _anchor_has_file "tmp/.ui-re-external-browse/$_h.json"; then
    _proceed=1
  elif _anchor_glob "tmp/ref/*/.ui-re-sessions/$_h.json"; then
    _proceed=1
  elif _anchor_has_file "tmp/.ui-re-sessions/$_h" && _has_run_state; then
    _proceed=1
  elif [[ "$_module" == "ui_clone.hooks.session_resume" ]] &&
       _anchor_glob "tmp/ref/*/.ui-re-active"; then
    # Resume notice after /clear, a new session, or a compact on a WIP run
    # this session has not claimed: show it (never auto-claim) and tell the
    # module the session is unclaimed so the notice asks for a re-invoke.
    UI_CLONE_SESSION_UNCLAIMED=1
    export UI_CLONE_SESSION_UNCLAIMED
    _proceed=1
  elif [[ "$_module" == "ui_clone.hooks.claude_continuation" &&
          "$_payload" =~ \"UserPromptSubmit\" &&
          "$_payload" =~ ui-clone[[:space:]]+decide[[:space:]] ]] && _found_ref; then
    # A user's clonability decision line must reach the recorder from any
    # session, not only the one that owns the run.
    _proceed=1
  elif [[ "$_module" == "ui_clone.hooks.claude_continuation" &&
          "$_sid" =~ ^[A-Za-z0-9._-]+$ && "$_sid" != "." && "$_sid" != ".." ]] &&
       _anchor_has_file ".ui-re-continuation/$_sid.json"; then
    _proceed=1
  fi
  (( _proceed )) || exit 0
elif ! _found_ref && ! _found_crumbs; then
  # Legacy folder-based activation (no session id in the payload).
  # claude_continuation used to bypass the fast-skip unconditionally, which spawned
  # uv plus an interpreter on every prompt in every project — ~1.2s to return None.
  # It only acts on two states, so admit exactly those: a bootstrap invocation of the
  # UI-RE skill in a tree that has no tmp/ref yet, or an existing continuation receipt.
  # Match the bare skill name, NOT "/<name>": activation arrives both as a slash prompt
  # on UserPromptSubmit and as PreToolUse Skill with tool_input.skill set, and only the
  # first carries a leading slash. The payload test is deliberately looser than the
  # module's; this is a pre-filter and the module still makes the exact decision.
  # Off-pipeline activation (omx postmortem): a payload that itself opens an
  # external URL via agent-browser must reach pre_bash so the FIRST crumb can be
  # written in a no-tmp/ref tree.
  _proceed=0
  case "$_module:$_payload" in
    ui_clone.hooks.claude_continuation:*"ui-clone-skills:ui-reverse-engineering"*) _proceed=1 ;;
  esac
  # external browse — proceed to write the crumb
  (( _proceed )) || ! _is_external_open || _proceed=1
  if (( ! _proceed )) &&
     [[ "$_module" == "ui_clone.hooks.claude_continuation" ]] &&
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
# uv defaults to a `.venv` inside --project, i.e. inside $project_root. For a
# host-installed plugin that IS a version-keyed cache directory (Claude:
# ~/.claude/plugins/cache/<market>/<plugin>/<version>; Codex has its own
# per-version copy) that a version bump or reinstall deletes and recreates —
# so every bump rebuilds a ~200MB venv from scratch inside a tree meant to be
# disposable, and stale versions strand their venv copy until orphan cleanup.
# Point uv at one persistent location outside every copied/cached tree so it
# is built once and reused; uv resyncs it automatically if the lockfile the
# invoking copy carries ever differs.
#
# Not honoring an inherited UV_PROJECT_ENVIRONMENT: a caller's shell/CI can
# already export that var pointed at some unrelated project's venv, and this
# shim must never sync ITS deps into THAT env.
UV_PROJECT_ENVIRONMENT="${UI_CLONE_HOOK_VENV:-${XDG_CACHE_HOME:-$HOME/.cache}/ui-clone-skills/hook-venv}"
export UV_PROJECT_ENVIRONMENT
# The shared venv must never hold the project itself as an editable install
# (enforced by `[tool.uv] package = false` in pyproject.toml): uv keys an
# editable install's .pth by whichever --project synced last, so with the
# package installed, two hook invocations from different roots (Claude cache
# vs Codex cache vs a version bump vs the dev checkout) racing on the one
# shared venv can have one process import the OTHER root's `ui_clone` mid-run,
# or crash with ModuleNotFoundError if a sync repoints the .pth between the
# other process's env-check and its actual import — empirically reproduced:
# concurrent `uv run --project A` / `--project B` sharing one venv had
# processes cross-import the other root's package. With the project excluded
# from the venv, PYTHONPATH below is what makes `import ui_clone` resolve —
# deterministically, to the invoking root, never to whatever synced last.
#
# PYTHONSAFEPATH keeps the invoking session's cwd off sys.path (it does NOT
# suppress PYTHONPATH entries). Without it, a session whose cwd holds its own
# `ui_clone/` — this repo's dev checkout, or any tree with that package name —
# would shadow the plugin package via cwd instead of via project_root.
#
# Never propagate a non-zero status. The hook modules signal every outcome as
# stdout JSON and only ever call sys.exit(0), so a non-zero status here is always
# an internal failure: a missing module, a broken venv, an interpreter error.
# Claude treats a failing UserPromptSubmit hook as a block, so propagating it
# rejects the user's prompt outright — a far worse outcome than skipping
# enforcement for one turn. Do not restore `exec` here; it would forward the
# status again. If a module ever needs to block, it must say so in its JSON.
# Re-feed the payload captured by the activation check unchanged. An empty
# payload runs the module with the already-drained (EOF) stdin, as before.
if [[ -n "$_payload" ]]; then
  PYTHONSAFEPATH=1 PYTHONPATH="$project_root" \
    uv run --project "$project_root" --no-dev --frozen python -m "$@" <<< "$_payload" || true
  exit 0
fi
PYTHONSAFEPATH=1 PYTHONPATH="$project_root" \
  uv run --project "$project_root" --no-dev --frozen python -m "$@" || true
exit 0
