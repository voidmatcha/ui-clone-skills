#!/usr/bin/env bash
# launch-stage.sh — pure-function translator from stage label → shell commands
# the user copy-pastes to start one staged convergence loop.
#
# This script ONLY prints commands. It does not invoke the terminal
# multiplexer itself. Keeping it side-effect-free makes Stage 0 testable; the
# user (or a future orchestrator) decides when to actually execute the printed
# lines.
#
# Usage:
#   TARGET_URL=https://example.org UI_CLONE_LOOP_MUX=<mux-cli> \
#     UI_CLONE_LOOP_WORKSPACE=<workspace-id> bash scripts/loop/launch-stage.sh <A|B|C|D>
#
# Launcher environment (nothing machine-specific is baked in):
#   UI_CLONE_LOOP_MUX               REQUIRED. Tab-multiplexer CLI that supports
#                                   `<mux> tab create -w <ws> -n <name> -t terminal`
#                                   and `<mux> tab send -w <ws> <tab-id> <text>`.
#   UI_CLONE_LOOP_WORKSPACE         REQUIRED. Workspace id passed to `-w`.
#   UI_CLONE_LOOP_AGENT_CMD         Agent CLI to launch (default: claude).
#   UI_CLONE_LOOP_MODEL             --model value; empty = host default (flag omitted).
#   UI_CLONE_LOOP_PERMISSION_MODE   --permission-mode value; empty = host default.
#   UI_CLONE_LOOP_SHELL_PROMPT_ANSWER
#                                   Keystroke sent to dismiss a shell startup prompt
#                                   (e.g. an update nag) before launching; empty = skip.
#
# Exit codes:
#   0  printed launch commands to stdout
#   2  invalid / missing stage argument, or a required launcher value is unset

set -uo pipefail

if [[ $# -ne 1 ]]; then
  printf 'usage: %s <A|B|C|D>\n' "$0" >&2
  exit 2
fi

stage="$1"
target="${TARGET_URL:-<ref-url>}"
ref_dir="${REF_DIR:-tmp/ref/<component>}"
case "$stage" in
  A|B|C|D) ;;
  *)
    printf 'launch-stage: unknown stage %q (use A, B, C, or D)\n' "$stage" >&2
    exit 2
    ;;
esac

# Per-stage parameters (mirrors plan §Stage A–D).
case "$stage" in
  A)
    sub_command="decode"
    tier="comprehensive"
    sections=""
    ;;
  B)
    sub_command="clone"
    tier="comprehensive"
    sections="hero"
    ;;
  C)
    sub_command="clone"
    # Stage C: hero + 2 follow-on sections; finalize names from Stage A's section-map.
    # Placeholder list — operator should edit to real section ids after Stage A's
    # section-map.json is produced.
    tier="comprehensive"
    sections="hero,<sec2>,<sec3>"
    ;;
  D)
    sub_command="verify"
    tier="comprehensive"
    sections=""
    ;;
esac

prompt_path="scratch/loop-${stage}/prompt.txt"

# Resolve plugin/repo root dynamically (AGENTS.md: prefer env-driven roots
# over hardcoded paths). Order: explicit env > derived-from-this-script.
plugin_root="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}}"
if [[ -z "$plugin_root" ]]; then
  plugin_root="$(cd "$(dirname "$0")/../.." && pwd)"
fi

# Launcher values. The workspace id and the multiplexer command have no
# neutral default (the tab verbs are multiplexer-specific), so fail early with
# a clear message instead of printing an unusable launch.
for required in UI_CLONE_LOOP_MUX UI_CLONE_LOOP_WORKSPACE; do
  if [[ -z "${!required:-}" ]]; then
    printf 'launch-stage: %s is unset — export it (see the header) before launching a stage\n' \
      "$required" >&2
    exit 2
  fi
done
# Every env-sourced value is shell-quoted with printf %q before it is printed,
# so a workspace id, model name, or prompt answer containing spaces or shell
# metacharacters survives the copy-paste as one argument.
mux="$(printf '%q' "$UI_CLONE_LOOP_MUX")"
workspace="$(printf '%q' "$UI_CLONE_LOOP_WORKSPACE")"
agent_cmd="$(printf '%q' "${UI_CLONE_LOOP_AGENT_CMD:-claude}")"
model="${UI_CLONE_LOOP_MODEL:-}"
permission_mode="${UI_CLONE_LOOP_PERMISSION_MODE:-}"
shell_prompt_answer="${UI_CLONE_LOOP_SHELL_PROMPT_ANSWER:-}"

agent_flags="--plugin-dir $(printf '%q' "$plugin_root")"
if [[ -n "$permission_mode" ]]; then
  agent_flags="${agent_flags} --permission-mode $(printf '%q' "$permission_mode")"
fi
if [[ -n "$model" ]]; then
  agent_flags="${agent_flags} --model $(printf '%q' "$model")"
fi

# Compose the env-prefix the user prepends to the agent launch.
env_line="export ENABLE_PROMPT_CACHING_1H=1 UI_CLONE_VERIFY_TIER=${tier}"
if [[ -n "$sections" ]]; then
  env_line="${env_line} UI_CLONE_VERIFY_SECTIONS=$(printf '%q' "$sections")"
fi

# Step numbers stay contiguous whether or not the optional prompt-dismissal
# step is emitted.
step=1
cat <<EOF
# ─── Stage ${stage} launch (${sub_command} ${target}) ───
# Tier: ${tier}
# Sections scope: ${sections:-<full>}
# Sub-command: ${sub_command}
# Prompt: ${prompt_path}

# ${step}) Create a multiplexer tab:
${mux} tab create -w ${workspace} -n loop-claude-conv-${stage} -t terminal
# (capture the returned tabId as TAB_ID)
EOF

if [[ -n "$shell_prompt_answer" ]]; then
  step=$((step + 1))
  cat <<EOF

# ${step}) Dismiss the shell startup prompt:
${mux} tab send -w ${workspace} "\$TAB_ID" $(printf '%q' "$shell_prompt_answer")
sleep 2
EOF
fi

step=$((step + 1))
cat <<EOF

# ${step}) Launch the agent with plugin hooks active:
${mux} tab send -w ${workspace} "\$TAB_ID" "${env_line} && ${agent_cmd} ${agent_flags}"
sleep 8

# $((step + 1))) Send the per-stage prompt:
${mux} tab send -w ${workspace} "\$TAB_ID" "\$(cat ${prompt_path})"

# When the loop reports DONE or stops, run:
#   bash scripts/loop/finalize-stage.sh ${ref_dir} ${stage}
EOF
