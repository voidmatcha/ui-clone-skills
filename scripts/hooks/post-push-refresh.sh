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
#   UI_CLONE_REPO=<git-url>             — repo whose install.sh is fetched
#                                         (default: origin remote, then upstream)
#   UI_CLONE_REPO_BRANCH=<branch>       — branch of install.sh to fetch (default: main)
#   UI_CLONE_INSTALL_SH_URL=<url>       — explicit raw install.sh URL (wins over the above)

input=$(cat)
# JSON-parsed extraction (falls back to the original compact-JSON grep if
# python3/parsing is unavailable) rather than a raw-text grep that assumed
# `"command":"..."` with no space after the colon — Claude Code's payload
# happens to serialize that way, but Codex's PostToolUse/exec_command payload
# shape isn't guaranteed to match byte-for-byte (Codex
# dev-hook parity design). The python step resolves a SINGLE-WORD verdict
# (not the raw command) so a multi-line `command` value (e.g. "git commit ...
# \ngit push ...") can never get mis-split by a line-oriented bash `sed`
# afterwards — an earlier version of this hardening printed the raw command
# on its own line and silently dropped any push hidden past line 1.
_verdict=$(HOOK_INPUT="$input" python3 -c '
import json, sys
import os, shlex
# git global options that take their value as a SEPARATE word. `--exec-path`
# is not one: without `=` git prints its exec path and exits, so a following
# word is never a subcommand. Options written `--opt=value` are one word.
_GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"}
_GIT_SEPS = {";", "&&", "||", "|", "&", "(", ")", ";;", "|&", "&>", ">", "<", ">>"}
def git_push(cmd):
    """(dir, push_args) for the first `git [global-opts] push`, else None.

    A word walker, not a regex: skips each global option (and the separate
    value of a value-taking one) so `git --work-tree X push` and
    `git --git-dir .git push` resolve by rule, not by accident.
    """
    try:
        lex = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""
        toks = list(lex)
    except ValueError:
        toks = cmd.split()
    # A simple leading `cd <dir> &&` moves the shell before git runs: a
    # relative -C resolves against it, not against the hook cwd.
    base = ""
    if len(toks) > 2 and toks[0] == "cd" and toks[2] == "&&" and not toks[1].startswith("-"):
        base = os.path.expandvars(os.path.expanduser(toks[1]))
    for i, t in enumerate(toks):
        if os.path.basename(t) != "git":
            continue
        j, d = i + 1, ""
        while j < len(toks) and toks[j] not in _GIT_SEPS:
            w = toks[j]
            if w in _GIT_VALUE_OPTS:
                if w == "-C" and j + 1 < len(toks):
                    # shlex drops the quotes but not `~`/`$VAR`: expand them
                    # as the shell would, from the hook environment.
                    c = os.path.expandvars(os.path.expanduser(toks[j + 1]))
                    d = c if os.path.isabs(c) or not d else os.path.join(d, c)
                j += 2
                continue
            if w.startswith("-"):
                j += 1
                continue
            if w == "push":
                args = []
                for a in toks[j + 1:]:
                    if a in _GIT_SEPS:
                        break
                    args.append(a)
                if d and base and not os.path.isabs(d):
                    d = os.path.join(base, d)
                return d, args
            break
    return None

try:
    d = json.loads(os.environ.get("HOOK_INPUT", "{}"))
except Exception:
    sys.exit(1)
if not isinstance(d, dict):
    sys.exit(1)
command = d.get("tool_input", {}).get("command", "") if isinstance(d.get("tool_input"), dict) else ""
# `git (global-opts)* push`: also `git -C <dir> push`, `git --no-pager push`,
# `git --work-tree <dir> push` (see git_push above).
if not isinstance(command, str) or git_push(command) is None:
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
  if ! printf '%s\n' "$command" | grep -qE '(^|[^[:alnum:]_-])git([[:space:]]+(-[Cc]|--(git-dir|work-tree|namespace|config-env|super-prefix))[[:space:]]+[^[:space:]]+|[[:space:]]+-[^[:space:]]+)*[[:space:]]+push([^[:alnum:]_-]|$)'; then
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

# Follow-up (a running Claude/Codex session loaded its hooks
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
#
# The installed-plugins key is `<plugin>@<marketplace>`; the marketplace
# suffix is read from this checkout's .claude-plugin/marketplace.json `name`
# so a fork registered under its own marketplace name is compared correctly,
# with the canonical upstream name as the fallback. $1 = installed_plugins.json,
# $2 = repo root.
_read_installed_version() {
  python3 -c "
import json, os, sys
marketplace = 'voidmatcha'
try:
    name = json.load(open(os.path.join(sys.argv[2], '.claude-plugin', 'marketplace.json'))).get('name')
    if isinstance(name, str) and name.strip():
        marketplace = name.strip()
except Exception:
    pass
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
for entry in d.get('plugins', {}).get('ui-clone-skills@' + marketplace) or []:
    v = entry.get('version')
    if v:
        print(v)
        break
" "$1" "$2" 2>/dev/null
}

# repo-slug-helpers: begin
# Install source. Resolution order keeps the repository owner out of hook
# behavior so forks and mirrors dogfood THEIR install.sh:
#   UI_CLONE_INSTALL_SH_URL  explicit raw URL of install.sh
#   UI_CLONE_REPO            git URL (the same variable install.sh honors)
#   origin remote            of the working repo
#   canonical upstream       last resort (announced on stderr)
UI_CLONE_REPO_DEFAULT="https://github.com/voidmatcha/ui-clone-skills.git"  # UI_CLONE_REPO overrides

# Reduce a GitHub git URL to `owner/repo`. Accepts https://github.com/o/r(.git),
# ssh://git@github.com/o/r(.git), and git@github.com:o/r(.git). Anything else
# is echoed stripped of only the .git / trailing-slash suffix, which then fails
# the strict slug validation in _resolve_repo_slug.
_repo_slug_from_url() {
  printf '%s' "$1" \
    | sed -E 's#^(ssh://)?git@github\.com[:/]##; s#^https?://github\.com/##; s#\.git$##; s#/$##'
}

# $1 = candidate git URL (may be empty). Echoes a validated `owner/repo`;
# when none can be derived (non-GitHub remote, odd URL) it prints a one-line
# stderr notice and falls back to the canonical upstream.
_resolve_repo_slug() {
  local candidate="$1" slug
  slug=$(_repo_slug_from_url "${candidate:-$UI_CLONE_REPO_DEFAULT}")
  if ! [[ "$slug" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
    echo "post-push-refresh: cannot derive a GitHub owner/repo from '${candidate:-<empty>}' — falling back to the canonical upstream installer (set UI_CLONE_INSTALL_SH_URL or UI_CLONE_REPO to override)" >&2
    slug=$(_repo_slug_from_url "$UI_CLONE_REPO_DEFAULT")
  fi
  printf '%s\n' "$slug"
}
# repo-slug-helpers: end

# CLAUDE_CONFIG_DIR relocates installed_plugins.json (install.sh honors it
# too; an empty value means the default).
_claude_installed_json="${UI_CLONE_INSTALLED_PLUGINS_JSON:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/installed_plugins.json}"
_version_before=$(_read_installed_version "$_claude_installed_json" "$REPO_ROOT")

_download_ok=0
_install_sh=""
if [ "${UI_CLONE_SKIP_POST_PUSH_REFRESH:-0}" != "1" ]; then
  INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/share/ui-clone-skills}"

  # Wait briefly for the remote to settle so the curl fetch sees the pushed sha.
  # GitHub raw cache TTL is short but non-zero; 2s avoids occasional stale reads.
  sleep 2

  # Install source: see the repo-slug helpers above for the resolution order.
  _repo_url="${UI_CLONE_REPO:-$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || true)}"
  _repo_slug=$(_resolve_repo_slug "$_repo_url")
  INSTALL_SH_URL="${UI_CLONE_INSTALL_SH_URL:-https://raw.githubusercontent.com/${_repo_slug}/${UI_CLONE_REPO_BRANCH:-main}/install.sh}"

  # Download install.sh BEFORE wiping anything: a network/404/truncated fetch
  # must leave the existing install intact instead of an empty INSTALL_DIR.
  _install_sh=$(mktemp "${TMPDIR:-/tmp}/ui-clone-install-XXXXXX") || _install_sh=""
  if [ -n "$_install_sh" ] \
     && curl -LsSf "$INSTALL_SH_URL" -o "$_install_sh" \
     && [ -s "$_install_sh" ] \
     && bash -n "$_install_sh" 2>/dev/null; then
    _download_ok=1
  else
    echo "🔴 post-push-refresh: failed to download a valid install.sh from $INSTALL_SH_URL — existing install at $INSTALL_DIR left untouched (check network / GitHub / UI_CLONE_INSTALL_SH_URL)" >&2
  fi
fi

if [ "${UI_CLONE_SKIP_POST_PUSH_REFRESH:-0}" != "1" ] && [ "$_download_ok" = "1" ]; then
  # Defensive: never wipe the maintainer's working repo even if INSTALL_DIR was
  # mis-set to it. Compare resolved paths to be safe against symlinks.
  RESOLVED_INSTALL=$(cd "$INSTALL_DIR" 2>/dev/null && pwd -P || echo "$INSTALL_DIR")
  RESOLVED_REPO=$(cd "$REPO_ROOT" 2>/dev/null && pwd -P || echo "$REPO_ROOT")
  if [ "$RESOLVED_INSTALL" = "$RESOLVED_REPO" ]; then
    echo "⚠️ post-push-refresh: INSTALL_DIR=$INSTALL_DIR is the working repo — skipping wipe" >&2
  elif [ -d "$INSTALL_DIR" ]; then
    # INSTALL_DIR is a generic name other tools also export (Docker/CI base
    # images, language installers). The working-repo check above guards one
    # mis-set case; this guards an unrelated tool's export reaching this
    # `rm -rf` unmodified — require BOTH a ui-clone-skills sentinel file AND
    # the directory's own basename before wiping it.
    case "$RESOLVED_INSTALL" in
      */ui-clone-skills)
        if [ -f "$INSTALL_DIR/.claude-plugin/plugin.json" ] && [ -f "$INSTALL_DIR/install.sh" ]; then
          rm -rf "$INSTALL_DIR"
        else
          echo "⚠️ post-push-refresh: INSTALL_DIR=$INSTALL_DIR lacks a ui-clone-skills sentinel — skipping wipe" >&2
        fi
        ;;
      *)
        echo "⚠️ post-push-refresh: INSTALL_DIR=$INSTALL_DIR does not look like a ui-clone-skills checkout — skipping wipe" >&2
        ;;
    esac
  fi

  # Feed the script on stdin (not as a file argument) so install.sh takes its
  # curl-pipe bootstrap path (clone INSTALL_DIR, re-exec the on-disk copy)
  # exactly as a real user's `curl | bash` does. Judge the installer by ITS
  # exit status, not the trailing sed's.
  INSTALL_DIR="$INSTALL_DIR" bash -s -- --no-deps < "$_install_sh" 2>&1 \
    | sed 's/^/[post-push-refresh] /'
  _install_rc=${PIPESTATUS[0]}
  if [ "$_install_rc" -ne 0 ]; then
    echo "🔴 post-push-refresh: install.sh FAILED (exit $_install_rc) — $INSTALL_DIR may be missing or partial; rerun: curl -LsSf $INSTALL_SH_URL | bash -s -- --no-deps" >&2
  fi

  _version_after=$(_read_installed_version "$_claude_installed_json" "$REPO_ROOT")
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
[ -n "${_install_sh:-}" ] && rm -f "$_install_sh"

# Run automated review regardless of refresh outcome — catches lint/doc regressions
# in the just-pushed working tree.
bash "$REPO_ROOT/scripts/ci/review.sh" --quiet 2>/dev/null || \
  echo "⚠️ review.sh found issues — run 'bash scripts/ci/review.sh' for details" >&2

exit 0
