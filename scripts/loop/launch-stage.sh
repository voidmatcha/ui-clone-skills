#!/usr/bin/env bash
# launch-stage.sh — pure-function translator from stage label → shell commands
# the user copy-pastes to start one staged convergence loop.
#
# This script ONLY prints commands. It does not invoke purplemux itself.
# Keeping it side-effect-free makes Stage 0 testable; the user (or a future
# orchestrator) decides when to actually execute the printed lines.
#
# Usage:
#   bash scripts/loop/launch-stage.sh <A|B|C|D>
#
# Exit codes:
#   0  printed launch commands to stdout
#   2  invalid / missing stage argument

set -uo pipefail

if [[ $# -ne 1 ]]; then
  printf 'usage: %s <A|B|C|D>\n' "$0" >&2
  exit 2
fi

stage="$1"
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
    target="https://linear.app"
    ;;
  B)
    sub_command="clone"
    tier="comprehensive"
    sections="hero"
    target="https://linear.app"
    ;;
  C)
    sub_command="clone"
    # Stage C: hero + 2 follow-on sections; finalize names from Stage A's section-map.
    # Placeholder list — operator should edit to real section ids after Stage A's
    # section-map.json is produced.
    tier="comprehensive"
    sections="hero,<sec2>,<sec3>"
    target="https://linear.app"
    ;;
  D)
    sub_command="verify"
    tier="comprehensive"
    sections=""
    target="https://linear.app"
    ;;
esac

prompt_path="scratch/loop-${stage}/prompt.txt"

# Resolve plugin/repo root dynamically (AGENTS.md: prefer env-driven roots
# over hardcoded paths). Order: explicit env > derived-from-this-script.
plugin_root="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}}"
if [[ -z "$plugin_root" ]]; then
  plugin_root="$(cd "$(dirname "$0")/../.." && pwd)"
fi

# Compose the env-prefix the user prepends to `claude` launch.
env_line="export ENABLE_PROMPT_CACHING_1H=1 UI_CLONE_VERIFY_TIER=${tier}"
if [[ -n "$sections" ]]; then
  env_line="${env_line} UI_CLONE_VERIFY_SECTIONS=${sections}"
fi

cat <<EOF
# ─── Stage ${stage} launch (${sub_command} ${target}) ───
# Tier: ${tier}
# Sections scope: ${sections:-<full>}
# Sub-command: ${sub_command}
# Prompt: ${prompt_path}

# 1) Create a purplemux tab:
purplemux tab create -w ws-MpcnYf -n loop-claude-conv-${stage} -t terminal
# (capture the returned tabId as TAB_ID)

# 2) Dismiss zsh update prompt:
purplemux tab send -w ws-MpcnYf "\$TAB_ID" "n"
sleep 2

# 3) Launch claude with plugin hooks active:
purplemux tab send -w ws-MpcnYf "\$TAB_ID" "${env_line} && claude --plugin-dir ${plugin_root} --permission-mode auto --model opus"
sleep 8

# 4) Send the per-stage prompt:
purplemux tab send -w ws-MpcnYf "\$TAB_ID" "\$(cat ${prompt_path})"

# When the loop reports DONE or stops, run:
#   bash scripts/loop/finalize-stage.sh tmp/ref/linear-app ${stage}
EOF
