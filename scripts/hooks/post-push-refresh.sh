#!/bin/bash
# Agent post-push refresh hook: after a successful `git push`, wipe the local
# install at INSTALL_DIR and re-run the canonical curl-pipe install. This
# dogfoods install.sh end-to-end against the just-pushed state — if the
# installer breaks, the maintainer learns immediately rather than after a real
# user files an issue.
#
# Behavior:
#   - Triggers only when the tool input was a successful `git push`.
#   - INSTALL_DIR defaults to ~/.local/share/ui-clone-skills (matches install.sh).
#     The maintainer's working repo is NEVER touched — INSTALL_DIR is the install
#     location, not the dev clone.
#   - --no-deps is passed so brew/uv don't re-resolve every push (system deps
#     don't change between commits in any meaningful way).
#   - Default install registers BOTH Claude and Codex marketplaces; each is a
#     no-op when that host's CLI is absent.
#   - Then runs review.sh to catch lint/doc regressions.
#
# Override:
#   UI_CLONE_SKIP_POST_PUSH_REFRESH=1  — skip the wipe+reinstall entirely
#   INSTALL_DIR=<path>                  — install elsewhere (e.g. for testing)

input=$(cat)
# JSON-parsed extraction (falls back to the original compact-JSON grep if
# python3/parsing is unavailable) rather than a raw-text grep that assumed
# `"command":"..."` with no space after the colon — Claude Code's payload
# happens to serialize that way, but Codex's PostToolUse/exec_command payload
# shape isn't guaranteed to match byte-for-byte (fable-20260910, Codex
# dev-hook parity design). The python step resolves a SINGLE-WORD verdict
# (not the raw command) so a multi-line `command` value (e.g. "git commit ...
# \ngit push ...") can never get mis-split by a line-oriented bash `sed`
# afterwards — an earlier version of this hardening printed the raw command
# on its own line and silently dropped any push hidden past line 1.
_verdict=$(HOOK_INPUT="$input" python3 -c '
import json, os, re, sys

try:
    d = json.loads(os.environ.get("HOOK_INPUT", "{}"))
except Exception:
    sys.exit(1)
if not isinstance(d, dict):
    sys.exit(1)
command = d.get("tool_input", {}).get("command", "") if isinstance(d.get("tool_input"), dict) else ""
if not isinstance(command, str) or not re.search(r"git\s+push", command):
    print("not-push")
    sys.exit(0)
# exit_code shape is confirmed for Claude Code (top-level "exit_code"); Codex
# is undocumented, so also check a couple of plausible nested spots. Preserve
# the original fail-CLOSED intent ("Triggers only when the tool input was a
# successful git push", this file'\''s own header comment): success must be
# POSITIVELY confirmed (exit_code == 0) or this no-ops, same as before —
# an absent/unrecognized field reads as "not confirmed", not "assume ok".
exit_code = d.get("exit_code")
if exit_code is None and isinstance(d.get("tool_response"), dict):
    exit_code = d["tool_response"].get("exit_code", d["tool_response"].get("exitCode"))
print("push-ok" if exit_code == 0 else "push-unconfirmed")
' 2>/dev/null) || _verdict=""

if [ -z "$_verdict" ]; then
  command=$(echo "$input" | grep -o '"command":"[^"]*"' | head -1 | sed 's/"command":"//;s/"$//')
  if ! echo "$command" | grep -qE 'git\s+push'; then
    _verdict="not-push"
  elif echo "$input" | grep -q '"exit_code":0'; then
    _verdict="push-ok"
  else
    _verdict="push-unconfirmed"
  fi
fi

if [ "$_verdict" != "push-ok" ]; then
  exit 0
fi

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# fable-20260912 follow-up (a running Claude/Codex session loaded its hooks
# at SESSION START from whatever plugin version was cached then; reinstalling
# to a NEW version here does not retroactively refresh an already-running
# session's loaded hook logic — install.sh's own printed guidance says
# "restart after installation" for exactly this reason). A version-bumped
# push landing quietly in this hook's noisy install.sh log made that easy to
# miss, especially for the agent driving the session that just did the push —
# it would keep retrying whatever the OLD hook blocked, forever, with no
# visible reason why the fix "didn't take". Compare the installed version
# before/after and print an unmissable, agent-addressed notice when it
# actually changed.
_read_installed_version() {
  python3 -c "
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
for entry in d.get('plugins', {}).get('ui-clone-skills@voidmatcha') or []:
    v = entry.get('version')
    if v:
        print(v)
        break
" "$1" 2>/dev/null
}

_claude_installed_json="${UI_CLONE_INSTALLED_PLUGINS_JSON:-$HOME/.claude/plugins/installed_plugins.json}"
_version_before=$(_read_installed_version "$_claude_installed_json")

if [ "${UI_CLONE_SKIP_POST_PUSH_REFRESH:-0}" != "1" ]; then
  INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/share/ui-clone-skills}"
  # Defensive: never wipe the maintainer's working repo even if INSTALL_DIR was
  # mis-set to it. Compare resolved paths to be safe against symlinks.
  RESOLVED_INSTALL=$(cd "$INSTALL_DIR" 2>/dev/null && pwd -P || echo "$INSTALL_DIR")
  RESOLVED_REPO=$(cd "$REPO_ROOT" 2>/dev/null && pwd -P || echo "$REPO_ROOT")
  if [ "$RESOLVED_INSTALL" = "$RESOLVED_REPO" ]; then
    echo "⚠️ post-push-refresh: INSTALL_DIR=$INSTALL_DIR is the working repo — skipping wipe" >&2
  elif [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
  fi

  # Wait briefly for the remote to settle so the curl fetch sees the pushed sha.
  # GitHub raw cache TTL is short but non-zero; 2s avoids occasional stale reads.
  sleep 2

  curl -LsSf https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh \
    | INSTALL_DIR="$INSTALL_DIR" bash -s -- --no-deps 2>&1 \
    | sed 's/^/[post-push-refresh] /' || \
    echo "⚠️ post-push-refresh: curl install failed — check network / GitHub" >&2

  _version_after=$(_read_installed_version "$_claude_installed_json")
  if [ -n "$_version_after" ] && [ "$_version_before" != "$_version_after" ]; then
    echo "" >&2
    echo "🔴🔴🔴 PLUGIN VERSION CHANGED: ${_version_before:-none} -> ${_version_after} 🔴🔴🔴" >&2
    echo "Hooks/skills for the CURRENT session were loaded at session start from" >&2
    echo "the OLD cached version. They were just reinstalled to a NEW version, but" >&2
    echo "this session keeps running the OLD hook/skill logic until it is" >&2
    echo "RESTARTED (not /clear) — a stale hook can silently block every" >&2
    echo "subsequent turn with no visible reason why the fix \"didn't take\"." >&2
    echo "IF YOU ARE THE AGENT READING THIS TOOL OUTPUT: tell the user now that" >&2
    echo "this session must be restarted before continuing work that depends on" >&2
    echo "the just-updated hooks/skills, instead of retrying the same blocked" >&2
    echo "action again." >&2
    echo "" >&2
  fi
fi

# Run automated review regardless of refresh outcome — catches lint/doc regressions
# in the just-pushed working tree.
bash "$REPO_ROOT/scripts/ci/review.sh" --quiet 2>/dev/null || \
  echo "⚠️ review.sh found issues — run 'bash scripts/ci/review.sh' for details" >&2

exit 0
