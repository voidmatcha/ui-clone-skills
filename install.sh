#!/usr/bin/env bash
# ui-clone-skills installer — bootstraps system deps and registers the Claude Code plugin.
#
# Usage (one of):
#   tmp=$(mktemp) && curl -LsSf -o "$tmp" https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh && bash "$tmp"
#   git clone https://github.com/voidmatcha/ui-clone-skills.git && cd ui-clone-skills && ./install.sh
#
# Idempotent: every step detects existing installs and skips. Safe to re-run.
#
# Flags:
#   --no-deps        skip system dependency installs (uv/ffmpeg/imagemagick/dssim/agent-browser)
#   --no-marketplace skip all marketplace registrations
#   --claude-only    register Claude Code marketplace only (skip Codex)
#   --codex-only     register Codex marketplace only (skip Claude); --codex is an alias
#   --uninstall      remove ui-clone-skills-owned registrations and projections
#   --yes            assume yes for prompts (e.g. apt sudo install)
#
# Default registers BOTH Claude Code and Codex marketplaces. Each registration
# is skipped automatically if the host CLI ('claude' / 'codex') is absent.
#
# Env:
#   UI_CLONE_REPO    git URL to clone (default: https://github.com/voidmatcha/ui-clone-skills.git)
#   UI_CLONE_REF     branch/tag/sha to checkout after clone (default: leave on default branch)
#   INSTALL_DIR      where to clone when running via curl-pipe (default: ~/.local/share/ui-clone-skills)
#   UI_CLONE_SKIP_HOOK_PROBE=1
#                    skip the post-install probe that runs an installed hook out
#                    of the Claude plugin cache. The probe is what proves the
#                    plugin was actually DELIVERED (an install can report success
#                    while caching an empty directory) — set this only if the
#                    probe itself is broken on your host, never to get past a
#                    genuine delivery failure.
set -euo pipefail

# --- curl-pipe bootstrap -----------------------------------------------------
# When piped from curl, BASH_SOURCE[0] is unset or points at a non-file. In that
# case clone the repo to INSTALL_DIR and re-exec the on-disk copy so the rest of
# the script runs against a real working tree (uv sync, marketplace add, etc.).
_self="${BASH_SOURCE[0]:-}"
if [ -z "$_self" ] || [ ! -f "$_self" ]; then
  REPO_URL="${UI_CLONE_REPO:-https://github.com/voidmatcha/ui-clone-skills.git}"
  TARGET="${INSTALL_DIR:-$HOME/.local/share/ui-clone-skills}"
  if ! command -v git >/dev/null 2>&1; then
    echo "git not found — install git and re-run." >&2
    exit 1
  fi
  if [ -d "$TARGET/.claude-plugin" ]; then
    echo "==> Updating existing checkout at $TARGET"
    git -C "$TARGET" fetch --quiet origin
    git -C "$TARGET" pull --ff-only --quiet || {
      echo "  ! local changes in $TARGET prevent fast-forward; leaving as-is" >&2
    }
  elif [ -e "$TARGET" ]; then
    echo "Refusing to clone: $TARGET exists but is not a ui-clone-skills checkout." >&2
    echo "Set INSTALL_DIR=<other path>, or remove/rename $TARGET, then re-run." >&2
    exit 1
  else
    echo "==> Cloning $REPO_URL → $TARGET"
    mkdir -p "$(dirname "$TARGET")"
    git clone --quiet "$REPO_URL" "$TARGET"
  fi
  if [ -n "${UI_CLONE_REF:-}" ]; then
    git -C "$TARGET" checkout --quiet "$UI_CLONE_REF"
  fi
  exec bash "$TARGET/install.sh" "$@"
fi

REPO_ROOT="$(cd "$(dirname "$_self")" && pwd)"
MARKETPLACE_NAME="voidmatcha"
PLUGIN_NAME="ui-clone-skills"
CODEX_MARKETPLACE_NAME="local"
CODEX_PERSONAL_MARKETPLACE="$HOME/.agents/plugins/marketplace.json"
CODEX_PLUGIN_DIR="$HOME/plugins/$PLUGIN_NAME"
CODEX_NATIVE_AGENTS_DIR="${CODEX_HOME:-$HOME/.codex}/agents"
LOCAL_BIN_DIR="${UI_CLONE_LOCAL_BIN_DIR:-$HOME/.local/bin}"
LOCAL_CLI_BIN="$LOCAL_BIN_DIR/ui-clone"
CODEX_PUBLIC_SKILLS="ui-reverse-engineering ui-capture visual-debug"
AGENTS_SKILLS_DIR="${AGENTS_SKILLS_DIR:-$HOME/.agents/skills}"
PUBLIC_SKILLS_OWNERSHIP="$HOME/.config/ui-clone-skills/public-skills.json"
CODEX_PLUGIN_PROJECTION_ITEMS=".claude-plugin .codex-plugin .codex bin hooks scripts ui_clone docs AGENTS.md README.md package.json pyproject.toml uv.lock LICENSE.txt"
CODEX_PLUGIN_PROJECTION_KEEP="$CODEX_PLUGIN_PROJECTION_ITEMS skills"
# Claude and Codex cannot share one source directory. Codex reads its install
# in place, so symlinks keep it live with the checkout; `claude plugin install`
# copies the source into ~/.claude/plugins/cache WITHOUT following symlinks, so
# the same directory caches as an empty shell. Claude gets its own real-file
# staging dir; the symlink projection stays for Codex and the local bin.
# NOT under $HOME/.local/share/$PLUGIN_NAME: that is INSTALL_DIR's default, and
# a curl-pipe install leaves it as a SYMLINK to the checkout — under which this
# path resolves to <repo>/claude-src, copying the repo into itself and aiming
# the staging rm -rf inside the working tree. Sibling path, plus the
# inside-the-checkout assertion in prepare_claude_plugin_source.
CLAUDE_PLUGIN_SRC="${UI_CLONE_CLAUDE_SRC_DIR:-$HOME/.local/share/$PLUGIN_NAME-claude-src}"

# Codex does NOT load a plugin from the marketplace source path. It copies that
# path into its own versioned cache (~/.codex/plugins/cache/<market>/<plugin>/
# <version>) and loads from there — and its copy skips symlinks. Pointing the
# marketplace at $CODEX_PLUGIN_DIR therefore produced a cache entry holding one
# empty skills/ dir and no plugin.json, while `codex plugin list` still reported
# "installed, enabled": the plugin contributed nothing to any Codex session, and
# a version bump could not fix it because the re-copy skips symlinks too.
# So the marketplace points at the staged real-file tree instead. The projection
# at $CODEX_PLUGIN_DIR stays symlinks on purpose — install_local_cli_bin depends
# on it to keep ~/.local/bin/ui-clone running the live checkout.
case "$CLAUDE_PLUGIN_SRC" in
  "$HOME"/*) CODEX_PLUGIN_SOURCE_PATH="./${CLAUDE_PLUGIN_SRC#"$HOME"/}" ;;
  *) CODEX_PLUGIN_SOURCE_PATH="$CLAUDE_PLUGIN_SRC" ;;
esac
# Entries written before the move above still name the projection. Ownership and
# removal must recognise them, or an upgrade would strand a marketplace entry this
# installer wrote and then refuse to clean it up as "not ours".
CODEX_PLUGIN_SOURCE_PATH_LEGACY="./plugins/$PLUGIN_NAME"
# Build residue that must never reach a published plugin source.
CLAUDE_PLUGIN_SRC_PRUNE="__pycache__ .pytest_cache .mypy_cache .ruff_cache node_modules .venv venv .DS_Store"
# Secondary tripwire. The clean set measures ~6.4MiB; the repo root is ~50GB of
# scratch runs and caches, and .gitignore does not apply to a `cp`. This catches
# an item list that starts sweeping the checkout — it would NOT have caught the
# symlink incident, which is why the symlink assertion is the primary guard.
CLAUDE_PLUGIN_SRC_MAX_KIB="${UI_CLONE_CLAUDE_SRC_MAX_KIB:-40960}"

NO_DEPS=0
NO_MARKETPLACE=0
INSTALL_CLAUDE=1
INSTALL_CODEX=1
ASSUME_YES=0
DO_UNINSTALL=0
for arg in "$@"; do
  case "$arg" in
    --no-deps) NO_DEPS=1 ;;
    --no-marketplace) NO_MARKETPLACE=1 ;;
    --codex|--codex-only) INSTALL_CLAUDE=0 ;;
    --claude|--claude-only) INSTALL_CODEX=0 ;;
    --uninstall) DO_UNINSTALL=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    -h|--help)
      awk 'NR==1{next} /^set -euo/{exit} {sub(/^# ?/,""); print}' "$0"
      exit 0
      ;;
    *) echo "Unknown flag: $arg" >&2; exit 2 ;;
  esac
done

if [ -t 1 ]; then
  C_OK=$'\033[32m'; C_SKIP=$'\033[2m'; C_ACT=$'\033[36m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_RST=$'\033[0m'
else
  C_OK=""; C_SKIP=""; C_ACT=""; C_WARN=""; C_ERR=""; C_RST=""
fi
ok()   { printf "  %s✓%s %s\n" "$C_OK" "$C_RST" "$*"; }
skip() { printf "  %s✓ %s (already present)%s\n" "$C_SKIP" "$*" "$C_RST"; }
act()  { printf "  %s→%s %s\n" "$C_ACT" "$C_RST" "$*"; }
warn() { printf "  %s! %s%s\n" "$C_WARN" "$*" "$C_RST"; }
err()  { printf "  %s✗ %s%s\n" "$C_ERR" "$*" "$C_RST" >&2; }
section() { printf "\n%s== %s ==%s\n" "$C_ACT" "$*" "$C_RST"; }

UNAME="$(uname -s)"
case "$UNAME" in
  Darwin) OS=mac ;;
  Linux)  OS=linux ;;
  *) err "Unsupported OS: $UNAME (only macOS and Linux are supported)"; exit 1 ;;
esac

have() { command -v "$1" >/dev/null 2>&1; }

shell_quote() {
  local quoted
  quoted=$(printf "%s" "$1" | sed "s/'/'\\\\''/g")
  printf "'%s'" "$quoted"
}

# Linux uses sudo for apt; macOS Homebrew does not.
sudo_run() {
  if [ "$OS" = "linux" ] && [ "$(id -u)" -ne 0 ]; then
    if [ "$ASSUME_YES" -eq 0 ]; then
      warn "About to run: sudo $*"
    fi
    sudo "$@"
  else
    "$@"
  fi
}

apt_install() {
  if ! have apt-get; then
    err "apt-get not found. Install '$*' manually for your distro."
    return 1
  fi
  sudo_run apt-get update -y >/dev/null
  sudo_run apt-get install -y "$@"
}

ensure_uv() {
  if have uv; then skip "uv $(uv --version | awk '{print $2}')"; return; fi
  act "Installing uv (Python package manager)"
  local uv_installer
  uv_installer=$(mktemp "${TMPDIR:-/tmp}/uv-installer.XXXXXX.sh")
  curl -LsSf https://astral.sh/uv/install.sh -o "$uv_installer"
  warn "Downloaded uv installer from https://astral.sh/uv/install.sh to $uv_installer"
  sh "$uv_installer"
  rm -f "$uv_installer"
  if ! have uv; then
    # uv installs to ~/.local/bin or ~/.cargo/bin depending on platform; surface the next step.
    warn "uv installed but not on PATH yet. Add ~/.local/bin (or ~/.cargo/bin) to PATH and re-run."
    return 1
  fi
  ok "uv $(uv --version | awk '{print $2}')"
}

ensure_ffmpeg() {
  if have ffmpeg; then skip "ffmpeg"; return; fi
  act "Installing ffmpeg"
  if [ "$OS" = "mac" ]; then
    brew install ffmpeg
  else
    apt_install ffmpeg
  fi
  ok "ffmpeg $(ffmpeg -version | head -1 | awk '{print $3}')"
}

ensure_imagemagick() {
  if have magick || have convert; then skip "imagemagick"; return; fi
  act "Installing imagemagick"
  if [ "$OS" = "mac" ]; then
    brew install imagemagick
  else
    apt_install imagemagick
  fi
  ok "imagemagick"
}

ensure_dssim() {
  if have dssim; then skip "dssim"; return; fi
  act "Installing dssim"
  if [ "$OS" = "mac" ]; then
    brew install dssim
  elif have cargo; then
    cargo install dssim
  else
    err "dssim install requires either Homebrew (mac) or cargo (linux)."
    err "Install Rust toolchain first: https://rustup.rs/  — then re-run this script."
    return 1
  fi
  ok "dssim"
}

ensure_node_npm() {
  if have npm; then skip "npm $(npm --version)"; return; fi
  err "npm not found. Install Node.js 18+ (https://nodejs.org/) and re-run."
  return 1
}

ensure_agent_browser() {
  if have agent-browser; then skip "agent-browser"; return; fi
  if npm list -g --depth=0 agent-browser >/dev/null 2>&1; then skip "agent-browser (via npm -g)"; return; fi
  act "Installing agent-browser globally via npm"
  npm install -g agent-browser
  ok "agent-browser"
}

uv_sync() {
  if [ ! -f "$REPO_ROOT/pyproject.toml" ]; then
    warn "No pyproject.toml at $REPO_ROOT — skipping uv sync."
    return
  fi
  act "Resolving Python deps (uv sync)"
  ( cd "$REPO_ROOT" && uv sync --quiet )
  ok "Python deps resolved"
}

python_candidates() {
  # Resolve each launcher to the interpreter it actually runs, reject unsupported
  # Python versions, and emit each interpreter once. UI_CLONE_PYTHON_CANDIDATES
  # is an internal/test override containing a colon-separated candidate list.
  local candidates="" candidate resolved seen=""
  if [ "${UI_CLONE_PYTHON_CANDIDATES+x}" = x ]; then
    candidates="${UI_CLONE_PYTHON_CANDIDATES//:/$'\n'}"
  else
    if have python3; then
      candidates="$(command -v python3)"
    fi
    if have pyenv; then
      candidate="$(pyenv which python3 2>/dev/null || true)"
      [ -z "$candidate" ] || candidates="${candidates}${candidates:+$'\n'}${candidate}"
    fi
    for candidate in \
      "$HOME/.local/bin/python3" \
      /opt/homebrew/bin/python3 \
      /usr/local/bin/python3 \
      /home/linuxbrew/.linuxbrew/bin/python3
    do
      [ -x "$candidate" ] || continue
      candidates="${candidates}${candidates:+$'\n'}${candidate}"
    done
  fi

  while IFS= read -r candidate; do
    [ -n "$candidate" ] || continue
    if [[ "$candidate" != */* ]]; then
      candidate="$(command -v "$candidate" 2>/dev/null || true)"
    fi
    [ -n "$candidate" ] && [ -x "$candidate" ] || continue
    resolved="$("$candidate" -c '
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    print(Path(sys.executable).resolve())
' 2>/dev/null || true)"
    [ -n "$resolved" ] && [ -x "$resolved" ] || continue
    case $'\n'"$seen"$'\n' in
      *$'\n'"$resolved"$'\n'*) continue ;;
    esac
    seen="${seen}${seen:+$'\n'}${resolved}"
    printf '%s\n' "$resolved"
  done <<< "$candidates"
}

python_resolves_repo() {
  local python="$1"
  (
    cd /
    REPO_ROOT="$REPO_ROOT" "$python" -c '
import os
from pathlib import Path
import ui_clone

repo_package = (Path(os.environ["REPO_ROOT"]) / "ui_clone").resolve()
installed_package = Path(ui_clone.__file__).resolve().parent
raise SystemExit(0 if installed_package == repo_package else 1)
' >/dev/null 2>&1
  )
}

editable_install() {
  # Make `python3 -m ui_clone.*` importable from ANY cwd. The skill scripts run the
  # gates via bare `python3 -m ui_clone` (not the repo .venv), and cold clone loops
  # run OUTSIDE the repo (scratch / loop dirs) — so ui_clone must be installed
  # EDITABLE into every supported local python3, tracking the local checkout.
  # Idempotent; safe to run under --no-deps.
  [ -f "$REPO_ROOT/pyproject.toml" ] || return 0
  local python found=0 pip_log
  while IFS= read -r python; do
    [ -n "$python" ] || continue
    found=1
    if python_resolves_repo "$python"; then
      skip "ui_clone editable ($python resolves to this repo)"
      continue
    fi

    act "Editable-installing ui_clone into $python"
    pip_log="$(mktemp)"
    if "$python" -m pip install --quiet --user -e "$REPO_ROOT" \
         >"$pip_log" 2>&1 &&
       python_resolves_repo "$python"; then
      ok "ui_clone editable-installed in $python"
    elif "$python" -m pip install --quiet --user --break-system-packages \
         -e "$REPO_ROOT" >"$pip_log" 2>&1 &&
         python_resolves_repo "$python"; then
      ok "ui_clone editable-installed in $python"
    else
      warn "ui_clone install in $python did not resolve to $REPO_ROOT from /"
      tail -n 12 "$pip_log" | sed 's/^/    /'
    fi
    rm -f "$pip_log"
  done < <(python_candidates)
  [ "$found" -eq 1 ] || warn "Python >=3.11 not found — skipping ui_clone editable install"
}

cache_ttl_notice() {
  # Print an opt-in notice if ENABLE_PROMPT_CACHING_1H isn't set. Never writes
  # to the user's shell rc — that's their decision. Enterprise/Pro/Max apply
  # 1h cache server-side and can ignore this; the notice still prints because
  # we can't detect plan from the installer.
  if [ -n "${ENABLE_PROMPT_CACHING_1H:-}" ]; then
    return
  fi
  case "${SHELL:-}" in
    *zsh)  rc="~/.zshrc" ;;
    *bash) rc="~/.bashrc" ;;
    *)     rc="your shell rc" ;;
  esac
  printf "\n%sOptional: extend Anthropic prompt cache TTL from 5min → 1h%s\n" "$C_WARN" "$C_RST"
  cat <<EOF

  This plugin's pipeline benefits from the longer TTL during gates and browser
  round-trips. Enterprise/Pro/Max apply 1h automatically; on Team/API-key plans
  the default is 5min. To opt in, add this line to $rc:

      export ENABLE_PROMPT_CACHING_1H=1

  Then **restart Claude Code** — the running process inherits env at launch,
  not on rc-file edit. See README → Token management.
EOF
}

loop_setup_notice() {
  # Print-only. /plugin install runs inside the Claude app and ~/.codex/config.toml
  # may already have a [features] section we shouldn't blindly edit.
  printf "\n%sOptional: enable goal-driven continuation (loop until done)%s\n" "$C_WARN" "$C_RST"
  cat <<EOF

  Claude Code — open a session with the plugin loaded, then describe the goal.
  The ui-reverse-engineering skill is auto-loaded so the prompt can be terse:
      claude --plugin-dir "$CODEX_PLUGIN_DIR"
      > Drive the ui-clone-skills pipeline for tmp/ref/<component> until
      > python -m ui_clone.goal tmp/ref/<component> --check-done exits 0.

  Codex (CLI ≥ 0.128.0) — enable the native /goal feature:
      # Append to ~/.codex/config.toml:
      [features]
      goals = true
      # The ui-reverse-engineering skill configures gate hooks per workspace.
      # Restart Codex, then in the REPL run:
      /goal Drive the ui-clone-skills pipeline for tmp/ref/<component> until python -m ui_clone.goal tmp/ref/<component> --check-done exits 0.

  See README → Goal-driven continuation.
EOF
}

register_marketplace() {
  if ! have claude; then
    warn "Claude Code CLI ('claude') not found on PATH — skipping marketplace registration."
    warn "Install Claude Code, then re-run with --no-deps to register the plugin."
    return
  fi
  # Decide whether we may own this marketplace name BEFORE staging anything or
  # issuing a single host command: a name already claimed by a foreign source
  # must be left exactly as found, with no side effects on the way out.
  local current_source="" marketplace_action="add"
  current_source="$(claude_marketplace_source 2>/dev/null || true)"
  if [ -n "$current_source" ]; then
    if [ ! -d "$current_source" ]; then
      err "Refusing to replace marketplace '$MARKETPLACE_NAME': registered source is unavailable: $current_source"
      return 1
    fi
    local resolved_current resolved_target
    resolved_current="$(cd "$current_source" && pwd -P)"
    resolved_target="$CLAUDE_PLUGIN_SRC"
    if [ -d "$resolved_target" ]; then
      resolved_target="$(cd "$resolved_target" && pwd -P)"
    fi
    if [ "$resolved_current" = "$resolved_target" ]; then
      marketplace_action="skip"
    elif is_ui_clone_plugin_source "$current_source"; then
      marketplace_action="replace"
    else
      err "Refusing to replace marketplace '$MARKETPLACE_NAME': $current_source is not a validated ui-clone-skills source"
      return 1
    fi
  fi

  prepare_plugin_projection || return
  prepare_claude_plugin_source || return
  install_public_agent_skills || return
  install_local_cli_bin || return

  # An unchanged marketplace path still needs the staging above to have run:
  # the registration is a pointer, and the content behind it is what changed.
  if [ "$marketplace_action" = "skip" ]; then
    skip "marketplace '$MARKETPLACE_NAME' already points to Claude source $CLAUDE_PLUGIN_SRC"
    return
  fi
  if [ "$marketplace_action" = "replace" ]; then
    warn "Refreshing Claude marketplace '$MARKETPLACE_NAME' from $current_source to $CLAUDE_PLUGIN_SRC"
    claude plugin marketplace remove "$MARKETPLACE_NAME" >/dev/null 2>&1 || true
  fi

  # `claude plugin marketplace add` is idempotent in recent CLI versions; tolerate either outcome.
  act "Registering Claude plugin source as marketplace '$MARKETPLACE_NAME'"
  if claude plugin marketplace add "$CLAUDE_PLUGIN_SRC" >/dev/null 2>&1; then
    ok "marketplace '$MARKETPLACE_NAME' registered"
  else
    skip "marketplace '$MARKETPLACE_NAME' already registered (or CLI declined re-add)"
  fi
}

# Claude Code keys an installation by marketplace, not by plugin name, so the
# same plugin installed from two marketplaces loads twice: both register their
# hooks, skills, and agents. Nothing in the CLI warns about it. This happens
# whenever a developer installs from a local dev marketplace and then runs this
# installer (or the reverse), and it was observed in practice with
# ui-clone-skills@ui-clone-dev enabled alongside ui-clone-skills@voidmatcha.
# Disable rather than uninstall, and leave the competing MARKETPLACE registered.
# Uninstalling discards a command-source plugin's recorded acceptance, so a
# developer returning to their local marketplace would have to confirm its
# source command again in a real terminal; disabling keeps the installation and
# that record intact, and re-enabling is one command. It is also the less
# destructive act for an installer that runs on other people's machines.
# Call this only AFTER this marketplace's own install or update succeeded:
# switching the other copy off first and then failing leaves nothing enabled.
disable_competing_claude_installs() {
  have claude || return 0
  local listing entry other
  listing="$(claude plugin list 2>/dev/null || true)"
  [ -n "$listing" ] || return 0
  for entry in $(printf '%s\n' "$listing" |
      grep -oE "${PLUGIN_NAME}@[A-Za-z0-9._-]+" | sort -u); do
    other="${entry#*@}"
    [ "$other" = "$MARKETPLACE_NAME" ] && continue
    warn "Disabling competing installation $entry (same plugin, different marketplace)"
    if claude plugin disable "$entry" --scope user >/dev/null 2>&1; then
      ok "disabled $entry"
    else
      warn "could not disable $entry — disable it manually to avoid double-loading"
    fi
  done
}

# The competing installer disables this plugin rather than uninstalling it, so
# a refresh can succeed against an installation that is still switched off —
# leaving the machine with a plugin installed, current, and inert. Re-enabling
# is idempotent and cheap, so assert it on every run rather than tracking who
# turned it off.
ensure_claude_plugin_enabled() {
  have claude || return 0
  claude plugin enable "$PLUGIN_NAME@$MARKETPLACE_NAME" --scope user >/dev/null 2>&1 || true
}

install_claude_plugin() {
  have claude || return
  # Marketplace registration alone never loads the plugin — it must be
  # installed once (user scope). The CLI's install step COPIES the marketplace
  # source into ~/.claude/plugins/cache/<owner>/<plugin>/<version> and the
  # runtime loads from that cache, not from the marketplace path — which is why
  # the source must be real files (prepare_claude_plugin_source) and why an
  # existing install still has to be refreshed: the cache is keyed by version,
  # so 'present in plugin list' means 'cached', not 'current'.
  # Same shape as the Codex listing above, and one plugin-list growth away from the
  # same EPIPE/pipefail failure. Buffer it for the same reason.
  local claude_listing
  claude_listing="$(claude plugin list 2>/dev/null || true)"
  if grep -qF "$PLUGIN_NAME@$MARKETPLACE_NAME" <<<"$claude_listing"; then
    act "Refreshing installed Claude plugin $PLUGIN_NAME@$MARKETPLACE_NAME"
    # `plugin update`, never uninstall+install: uninstall rewrites
    # enabledPlugins in ~/.claude/settings.json, and a failure between the two
    # steps leaves no plugin where a stale one stood.
    if claude plugin update "$PLUGIN_NAME@$MARKETPLACE_NAME" >/dev/null 2>&1; then
      ok "Claude plugin refreshed from $CLAUDE_PLUGIN_SRC"
      ensure_claude_plugin_enabled
      disable_competing_claude_installs
    else
      warn "claude plugin update failed — run inside the app: /plugin update $PLUGIN_NAME@$MARKETPLACE_NAME"
    fi
    return
  fi
  act "Installing Claude plugin $PLUGIN_NAME@$MARKETPLACE_NAME (user scope)"
  if claude plugin install "$PLUGIN_NAME@$MARKETPLACE_NAME" >/dev/null 2>&1; then
    ok "Claude plugin installed — new Claude Code sessions load it automatically"
    ensure_claude_plugin_enabled
    disable_competing_claude_installs
  else
    warn "claude plugin install failed — run inside the app: /plugin install $PLUGIN_NAME@$MARKETPLACE_NAME"
  fi
}

plugin_manifest_version() {
  [ -f "$REPO_ROOT/.claude-plugin/plugin.json" ] || return 1
  sed -n 's/.*"version": "\([^"]*\)".*/\1/p' "$REPO_ROOT/.claude-plugin/plugin.json" | head -1
}

verify_claude_plugin_delivery() {
  # The install step is not the delivery. The host COPIES the marketplace source
  # into its own per-version cache and loads from there, so 'install succeeded'
  # says nothing about whether the plugin has any content. Version 0.7.24 sat
  # installed and enabled for weeks with an empty cache directory: no hooks, no
  # skills, and not one line of output saying so.
  have claude || return 0
  if [ "${UI_CLONE_SKIP_HOOK_PROBE:-0}" = "1" ]; then
    skip "Claude hook delivery probe (UI_CLONE_SKIP_HOOK_PROBE=1)"
    return 0
  fi

  local version cache_dir
  version="$(plugin_manifest_version)" || return 0
  [ -n "$version" ] || return 0
  cache_dir="$HOME/.claude/plugins/cache/$MARKETPLACE_NAME/$PLUGIN_NAME/$version"

  if [ ! -d "$cache_dir" ]; then
    # The cache layout is the host's private detail; a version we cannot find
    # is unverifiable, not proven broken.
    warn "Cannot locate the host plugin cache for $PLUGIN_NAME $version — skipping the delivery probe."
    warn "  looked in: $cache_dir"
    return 0
  fi

  local rel missing=""
  for rel in hooks/hooks.json hooks/shim.sh .claude-plugin/plugin.json ui_clone/__init__.py; do
    [ -f "$cache_dir/$rel" ] || missing="$missing $rel"
  done
  if [ -n "$missing" ]; then
    err "Hook delivery probe FAILED: the host cached $PLUGIN_NAME $version without:$missing"
    err "  cache: $cache_dir"
    err "  The host copies the marketplace source WITHOUT following symlinks, so a"
    err "  symlinked source caches as an empty shell and the plugin loads nothing."
    err "  This is a real delivery failure — fix the source, do not skip the probe."
    err "  (override, for a broken probe only: UI_CLONE_SKIP_HOOK_PROBE=1)"
    return 1
  fi

  # Run a hook the way hooks.json runs it. File counts cannot prove this:
  # hooks are discovered by directory convention with no manifest field to
  # check, and the first execution is also what builds the uv environment —
  # left cold, its first fire inside a real session can time out silently.
  local probe_dir probe_status=0
  probe_dir="$(mktemp -d 2>/dev/null)" || return 0
  mkdir -p "$probe_dir/tmp/ref"
  # stop_hook_active short-circuits section_gate to a clean exit, so the probe
  # exercises loading without evaluating anybody's pipeline state.
  CLAUDE_PROJECT_DIR="$probe_dir" PLUGIN_ROOT="$cache_dir" \
    bash "$cache_dir/hooks/shim.sh" ui_clone.hooks.section_gate \
    <<< '{"hook_event_name":"Stop","stop_hook_active":true}' \
    >/dev/null 2>&1 || probe_status=$?
  rm -rf "$probe_dir"

  if [ "$probe_status" -ne 0 ]; then
    err "Hook delivery probe FAILED: the installed Stop hook did not run from the host cache (exit $probe_status)."
    err "  cache: $cache_dir"
    err "  reproduce: CLAUDE_PROJECT_DIR=<dir containing tmp/ref> bash $cache_dir/hooks/shim.sh ui_clone.hooks.section_gate"
    err "  (override, for a broken probe only: UI_CLONE_SKIP_HOOK_PROBE=1)"
    return 1
  fi
  ok "hook delivery probe: section_gate ran from the host cache"
  warn_if_plugin_disabled
  prune_superseded_cache_versions
}

warn_if_plugin_disabled() {
  # Installed and ENABLED are different states, and only one of them runs.
  # Observed live: after a `plugin marketplace remove` + reinstall recovery,
  # installed_plugins.json listed the plugin while settings.json enabledPlugins
  # no longer did — hooks stopped firing in every session and nothing said so.
  # The delivery probe above cannot catch this: it runs the hook straight from
  # the cache, so it proves DELIVERY, never ACTIVATION.
  #
  # This warns rather than enabling: a deliberate user disable must be
  # respected, and silently re-enabling would be its own surprise.
  have python3 || return 0
  local state
  state="$(
    SETTINGS="$HOME/.claude/settings.json" PLUGIN_KEY="$PLUGIN_NAME@$MARKETPLACE_NAME" \
      python3 - <<'PY'
import json
import os
from pathlib import Path

try:
    data = json.loads(Path(os.environ["SETTINGS"]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(0)  # no readable settings -> nothing to assert
enabled = data.get("enabledPlugins")
if not isinstance(enabled, dict):
    raise SystemExit(0)
print("enabled" if enabled.get(os.environ["PLUGIN_KEY"]) else "disabled")
PY
  )" || return 0

  if [ "$state" = "disabled" ]; then
    warn "Plugin $PLUGIN_NAME@$MARKETPLACE_NAME is installed but NOT ENABLED — its hooks and skills will not load in any session."
    warn "  Enable it with:  claude plugin enable $PLUGIN_NAME@$MARKETPLACE_NAME"
    warn "  (left as-is on purpose: a deliberate disable is yours to keep)"
  fi
}

prune_superseded_cache_versions() {
  # The host caches per version and never reclaims old ones. Each live version
  # also carries a ~220MB uv venv (materialised by the delivery probe above and
  # by the first real hook fire), so a few releases reach multiple GB.
  # `claude plugin prune` does not cover this — it removes auto-installed
  # dependencies, not superseded versions of a directory-marketplace plugin.
  #
  # Only versions the host itself does not reference are removed, and the live
  # set is READ from installed_plugins.json rather than assumed to be the
  # manifest version: an install that half-failed can leave the host pointing
  # at an older directory, and deleting that would break a working install.
  local cache_root="$HOME/.claude/plugins/cache/$MARKETPLACE_NAME/$PLUGIN_NAME"
  [ -d "$cache_root" ] || return 0
  have python3 || return 0

  local removed
  removed="$(
    CACHE_ROOT="$cache_root" \
    INSTALLED_JSON="$HOME/.claude/plugins/installed_plugins.json" \
    VERIFIED_VERSION="$(plugin_manifest_version || true)" \
    PLUGIN_KEY="$PLUGIN_NAME@$MARKETPLACE_NAME" python3 - <<'PY'
import json
import os
import shutil
from pathlib import Path

cache_root = Path(os.environ["CACHE_ROOT"])
installed_json = Path(os.environ["INSTALLED_JSON"])
key = os.environ["PLUGIN_KEY"]

try:
    data = json.loads(installed_json.read_text(encoding="utf-8"))
    entries = data["plugins"][key]
except (OSError, json.JSONDecodeError, KeyError, TypeError):
    # No readable record of what is installed -> removing anything is a guess.
    raise SystemExit(0)

live = set()
# The version just delivered and probed green is evidence, not a guess, and it
# is protected even when the host record has not caught up. `claude plugin
# update` is warn-and-continue, so a half-applied update reaches here with the
# new directory in cache and the record still naming the previous version —
# reading the live set from the record alone would delete what was just verified.
verified = os.environ.get("VERIFIED_VERSION", "").strip()
if verified:
    live.add(verified)
for entry in entries if isinstance(entries, list) else []:
    if not isinstance(entry, dict):
        continue
    path = entry.get("installPath")
    if isinstance(path, str) and path:
        live.add(Path(path).name)
    version = entry.get("version")
    if isinstance(version, str) and version:
        live.add(version)

if not live:
    raise SystemExit(0)

for child in sorted(cache_root.iterdir()):
    if not child.is_dir() or child.is_symlink() or child.name in live:
        continue
    try:
        shutil.rmtree(child)
        print(child.name)
    except OSError:
        pass
PY
  )" || return 0

  if [ -n "$removed" ]; then
    local count
    count="$(printf '%s\n' "$removed" | grep -c .)"
    ok "reclaimed $count superseded plugin cache version(s): $(printf '%s' "$removed" | tr '\n' ' ')"
  fi
}

prepare_plugin_projection() {
  # Local plugin hosts install by copying/scanning the source path. Point that
  # source at a small projection instead of the development checkout; the repo
  # can contain local scratch runs, screenshots, venvs, and caches.
  local plugin_dir="$CODEX_PLUGIN_DIR"
  local resolved_repo
  resolved_repo="$(cd "$REPO_ROOT" && pwd -P)"

  if [ -L "$plugin_dir" ]; then
    rm -f "$plugin_dir"
  fi

  if [ -d "$plugin_dir" ]; then
    local resolved_plugin
    resolved_plugin="$(cd "$plugin_dir" && pwd -P)"
    if [ "$resolved_plugin" = "$resolved_repo" ]; then
      err "Refusing to use plugin projection at repo root: $plugin_dir"
      return 1
    fi
  elif [ -e "$plugin_dir" ]; then
    err "Codex plugin path exists but is not a directory: $plugin_dir"
    return 1
  fi

  mkdir -p "$plugin_dir"

  local existing name
  for existing in "$plugin_dir"/* "$plugin_dir"/.[!.]* "$plugin_dir"/..?*; do
    # `-e` follows the link, so a symlink left dangling by a source rename reads
    # as absent and is skipped — exempting from the prune exactly the stale
    # entries it exists to remove. Match on the link itself as well.
    [ -e "$existing" ] || [ -L "$existing" ] || continue
    name="$(basename "$existing")"
    case " $CODEX_PLUGIN_PROJECTION_KEEP " in
      *" $name "*) ;;
      *) rm -rf "$existing" ;;
    esac
  done

  local item src dst
  for item in $CODEX_PLUGIN_PROJECTION_ITEMS; do
    src="$REPO_ROOT/$item"
    dst="$plugin_dir/$item"
    if [ ! -e "$src" ]; then
      continue
    fi
    mkdir -p "$(dirname "$dst")"
    rm -rf "$dst"
    ln -s "$src" "$dst"
  done

  # Codex skill discovery points at ./skills/. Keep the projection public-only
  # so maintainer-only skills in the development checkout (for example
  # skills/benchmark) never appear in the installed Codex plugin surface.
  rm -rf "$plugin_dir/skills"
  mkdir -p "$plugin_dir/skills"
  local skill
  for skill in $CODEX_PUBLIC_SKILLS; do
    src="$REPO_ROOT/skills/$skill"
    dst="$plugin_dir/skills/$skill"
    if [ ! -d "$src" ]; then
      err "Missing public Codex skill directory: $src"
      return 1
    fi
    ln -s "$src" "$dst"
  done

  ok "plugin projection → $plugin_dir (source: $REPO_ROOT)"
}

prepare_claude_plugin_source() {
  # Real-file staging for Claude. `cp -RL` dereferences: the checkout carries no
  # symlinks inside the projected items today, but a skill that adds one must
  # not be able to reintroduce the empty-cache failure.
  local dst="$CLAUDE_PLUGIN_SRC"
  local resolved_repo
  resolved_repo="$(cd "$REPO_ROOT" && pwd -P)"

  if [ -L "$dst" ]; then
    rm -f "$dst"
  fi
  if [ -e "$dst" ] && [ ! -d "$dst" ]; then
    err "Claude plugin source path exists but is not a directory: $dst"
    return 1
  fi

  # Resolve the PARENT: $dst itself may not exist yet, and the collision that
  # matters comes from a symlinked ancestor, not from $dst's own name.
  local resolved_parent resolved_dst
  mkdir -p "$(dirname "$dst")" 2>/dev/null || true
  resolved_parent="$(cd "$(dirname "$dst")" 2>/dev/null && pwd -P)" || {
    err "Cannot resolve the Claude plugin source parent directory: $(dirname "$dst")"
    return 1
  }
  resolved_dst="$resolved_parent/$(basename "$dst")"
  # Equal to the repo, or anywhere beneath it. A staged copy inside the working
  # tree pollutes the checkout and points this function's rm -rf at the repo.
  case "$resolved_dst" in
    "$resolved_repo" | "$resolved_repo"/*)
      err "Refusing to stage the Claude plugin source inside the checkout: $dst"
      err "  resolves to: $resolved_dst"
      err "  repo:        $resolved_repo"
      err "  A symlinked ancestor is the usual cause (INSTALL_DIR defaults to a path"
      err "  that a curl-pipe install may leave symlinked at the checkout)."
      err "  Set UI_CLONE_CLAUDE_SRC_DIR to a directory outside the repo."
      return 1
      ;;
  esac

  # Rebuild from scratch: a stale file left by a previous item list would ship
  # forever otherwise, and the whole point of this directory is that its
  # contents are exactly what the host will cache.
  rm -rf "$dst"
  mkdir -p "$dst"

  local item src
  for item in $CODEX_PLUGIN_PROJECTION_ITEMS; do
    src="$REPO_ROOT/$item"
    [ -e "$src" ] || continue
    mkdir -p "$(dirname "$dst/$item")"
    if ! cp -RL "$src" "$dst/$item"; then
      err "Failed to stage $item into the Claude plugin source"
      return 1
    fi
  done

  local skill
  mkdir -p "$dst/skills"
  for skill in $CODEX_PUBLIC_SKILLS; do
    src="$REPO_ROOT/skills/$skill"
    if [ ! -d "$src" ]; then
      err "Missing public skill directory: $src"
      return 1
    fi
    if ! cp -RL "$src" "$dst/skills/$skill"; then
      err "Failed to stage skill $skill into the Claude plugin source"
      return 1
    fi
  done

  local prune
  for prune in $CLAUDE_PLUGIN_SRC_PRUNE; do
    find "$dst" -name "$prune" -prune -exec rm -rf {} + 2>/dev/null || true
  done
  find "$dst" \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true

  assert_claude_plugin_source_sane || return 1

  ok "Claude plugin source → $dst (real files, source: $REPO_ROOT)"
}

assert_claude_plugin_source_sane() {
  # Runs BEFORE the marketplace is registered. Everything below is a property
  # the host silently depends on: it copies without following symlinks, it
  # loads hooks by directory convention, and it exposes whatever skills/ holds.
  local dst="$CLAUDE_PLUGIN_SRC"

  local links
  links="$(find "$dst" -type l 2>/dev/null | head -5)"
  if [ -n "$links" ]; then
    err "Claude plugin source contains symlinks — the host caches it without following them, producing an empty plugin:"
    printf '  %s\n' $links >&2
    return 1
  fi

  if [ ! -f "$dst/.claude-plugin/plugin.json" ]; then
    err "Claude plugin source is missing .claude-plugin/plugin.json: $dst"
    return 1
  fi
  if [ ! -f "$dst/hooks/hooks.json" ]; then
    err "Claude plugin source is missing hooks/hooks.json — hooks load by directory convention, so this installs a silently hook-less plugin: $dst"
    return 1
  fi

  local staged expected skill
  staged="$(cd "$dst/skills" 2>/dev/null && ls -1 | sort | tr '\n' ' ')"
  expected="$(printf '%s\n' $CODEX_PUBLIC_SKILLS | sort | tr '\n' ' ')"
  if [ "$staged" != "$expected" ]; then
    err "Claude plugin source skills/ must hold exactly the public skills."
    err "  expected: $expected"
    err "  staged:   $staged"
    return 1
  fi
  for skill in $CODEX_PUBLIC_SKILLS; do
    if [ ! -f "$dst/skills/$skill/SKILL.md" ]; then
      err "Staged skill $skill has no SKILL.md: $dst/skills/$skill"
      return 1
    fi
  done

  local kib
  kib="$(du -sk "$dst" 2>/dev/null | awk '{print $1}')"
  if [ -n "$kib" ] && [ "$kib" -gt "$CLAUDE_PLUGIN_SRC_MAX_KIB" ]; then
    err "Claude plugin source is ${kib}KiB, over the ${CLAUDE_PLUGIN_SRC_MAX_KIB}KiB tripwire — the item list is sweeping the checkout: $dst"
    return 1
  fi

  if have claude && ! claude plugin validate "$dst" >/dev/null 2>&1; then
    warn "claude plugin validate declined $dst — continuing, but the host may reject this plugin"
  fi
}



record_public_skill_ownership() {
  local skill="$1"
  local installed_path="$2"
  mkdir -p "$(dirname "$PUBLIC_SKILLS_OWNERSHIP")"

  OWNERSHIP_PATH="$PUBLIC_SKILLS_OWNERSHIP" SKILL_NAME="$skill" \
    INSTALLED_PATH="$installed_path" SOURCE_ROOT="$REPO_ROOT" \
    PLUGIN_MANIFEST="$REPO_ROOT/.claude-plugin/plugin.json" python3 - <<'PY'
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        if path.is_symlink():
            kind, content = b"L", os.readlink(path).encode()
        elif path.is_dir():
            kind, content = b"D", b""
        else:
            kind = b"X" if path.stat().st_mode & 0o111 else b"F"
            content = path.read_bytes()
        digest.update(kind + b"\0" + relative + b"\0")
        digest.update(str(len(content)).encode() + b"\0" + content + b"\0")
    return digest.hexdigest()


def atomic_write_json(path: Path, data: object) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", delete=False,
        ) as handle:
            temp_name = handle.name
            os.fchmod(handle.fileno(), mode)
            json.dump(data, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


ownership_path = Path(os.environ["OWNERSHIP_PATH"])
try:
    data = json.loads(ownership_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    data = {}
if not isinstance(data, dict):
    data = {}
data["schemaVersion"] = 1
skills = data.get("skills")
if not isinstance(skills, dict):
    skills = {}
    data["skills"] = skills

installed = Path(os.environ["INSTALLED_PATH"]).resolve()
source_root = Path(os.environ["SOURCE_ROOT"]).resolve()
try:
    plugin = json.loads(Path(os.environ["PLUGIN_MANIFEST"]).read_text(encoding="utf-8"))
    version = plugin.get("version") if isinstance(plugin, dict) else None
except (OSError, json.JSONDecodeError):
    version = None

skills[os.environ["SKILL_NAME"]] = {
    "path": str(installed),
    "sha256": tree_hash(installed),
    "source": str(source_root),
    "version": version,
}
atomic_write_json(ownership_path, data)
PY
}

install_public_agent_skills() {
  mkdir -p "$AGENTS_SKILLS_DIR"

  local skill src dst
  for skill in $CODEX_PUBLIC_SKILLS; do
    src="$REPO_ROOT/skills/$skill"
    dst="$AGENTS_SKILLS_DIR/$skill"
    if [ ! -d "$src" ]; then
      err "Missing public Codex skill directory: $src"
      return 1
    fi

    rm -rf "$dst"
    cp -R "$src" "$dst"
    if ! record_public_skill_ownership "$skill" "$dst"; then
      rm -rf "$dst"
      return 1
    fi
    ok "Codex public skill $skill → $dst"
  done
}

install_local_cli_bin() {
  # Deliberately points into the Codex projection, NOT at $REPO_ROOT/bin.
  # ~/.local/bin/ui-clone -> $CODEX_PLUGIN_DIR/bin/ui-clone -> $REPO_ROOT/bin/ui-clone:
  # every hop is a symlink, so the CLI always runs the live checkout and cannot
  # serve a stale snapshot. Reviewed and left alone deliberately — do not
  # "fix" it by repointing at REPO_ROOT.
  #
  # This holds ONLY while the Codex projection stays symlinks. If anyone ever
  # converts $CODEX_PLUGIN_DIR to real files (as the Claude source at
  # $CLAUDE_PLUGIN_SRC is), this silently starts running a frozen copy and the
  # repoint to "$REPO_ROOT/bin/ui-clone" becomes required — along with the
  # uninstall bookkeeping in remove_owned_symlink for LOCAL_CLI_BIN.
  local src="$CODEX_PLUGIN_DIR/bin/ui-clone"
  local dst="$LOCAL_CLI_BIN"

  if [ ! -x "$src" ]; then
    warn "ui-clone local bin source missing or not executable: $src"
    return 0
  fi

  mkdir -p "$LOCAL_BIN_DIR"

  if [ -L "$dst" ]; then
    local existing
    existing="$(readlink "$dst")"
    if [ "$existing" = "$src" ]; then
      skip "local ui-clone bin $dst"
    else
      rm -f "$dst"
      ln -s "$src" "$dst"
      ok "local ui-clone bin → $dst"
    fi
  elif [ -e "$dst" ]; then
    warn "local ui-clone bin exists and is not a symlink — leaving in place: $dst"
    return 0
  else
    ln -s "$src" "$dst"
    ok "local ui-clone bin → $dst"
  fi

  if "$dst" --help >/dev/null 2>&1; then
    ok "local ui-clone bin smoke"
  else
    warn "local ui-clone bin smoke failed: $dst --help"
  fi

  case ":$PATH:" in
    *":$LOCAL_BIN_DIR:"*) ;;
    *) warn "$LOCAL_BIN_DIR is not on PATH; run directly as $dst or add it to PATH." ;;
  esac
}

install_codex_native_agents() {
  # Codex native subagents are discovered from CODEX_HOME agents, not from the
  # plugin manifest. Symlink this plugin's role adapters so installed Codex
  # sessions can dispatch agent_type names such as generation-planner.
  local src_dir="$REPO_ROOT/.codex/agents"
  if [ ! -d "$src_dir" ]; then
    return
  fi

  mkdir -p "$CODEX_NATIVE_AGENTS_DIR"

  local src dst existing
  for src in "$src_dir"/*.toml; do
    [ -e "$src" ] || continue
    dst="$CODEX_NATIVE_AGENTS_DIR/$(basename "$src")"
    if [ -L "$dst" ]; then
      existing="$(readlink "$dst")"
      if [ "$existing" = "$src" ]; then
        skip "Codex native agent $(basename "$dst")"
        continue
      fi
    fi
    # A rename of the checkout leaves this link dangling at the old path.
    # `-e` follows the link, so a broken one reads as absent and slips past the
    # user-copy guard below straight into `ln -s`, which then fails because the
    # path does exist. Clear it first so the reinstall can repair it.
    if [ -L "$dst" ] && [ ! -e "$dst" ]; then
      rm -f "$dst"
    fi
    if [ -e "$dst" ]; then
      warn "Codex native agent $(basename "$dst") already exists — leaving user copy"
      continue
    fi
    ln -s "$src" "$dst"
    ok "Codex native agent → $dst"
  done
}

write_codex_personal_marketplace() {
  local marketplace="$CODEX_PERSONAL_MARKETPLACE"
  mkdir -p "$(dirname "$marketplace")"

  if ! have python3; then
    warn "python3 absent — cannot atomically update $marketplace"
    return 1
  fi

  if ! MARKETPLACE_PATH="$marketplace" PLUGIN_NAME="$PLUGIN_NAME" \
       PLUGIN_SOURCE_PATH="$CODEX_PLUGIN_SOURCE_PATH" \
       PLUGIN_SOURCE_PATH_LEGACY="$CODEX_PLUGIN_SOURCE_PATH_LEGACY" python3 - <<'PY'
import json
import os
import stat
import tempfile
from pathlib import Path

path = Path(os.environ["MARKETPLACE_PATH"]).expanduser()
plugin = os.environ["PLUGIN_NAME"]
source_path = os.environ["PLUGIN_SOURCE_PATH"]
legacy_source_path = os.environ.get("PLUGIN_SOURCE_PATH_LEGACY", "")
entry = {
    "name": plugin,
    "source": {
        "source": "local",
        "path": source_path,
    },
    "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    },
    "category": "Developer Tools",
}

if path.is_symlink():
    try:
        write_path = path.resolve(strict=True)
    except OSError as error:
        raise SystemExit(f"refusing invalid marketplace symlink {path}: {error}")
    if not stat.S_ISREG(write_path.stat().st_mode):
        raise SystemExit(f"refusing non-regular marketplace target: {write_path}")
elif path.exists():
    write_path = path
    if not stat.S_ISREG(write_path.stat().st_mode):
        raise SystemExit(f"refusing non-regular marketplace file: {write_path}")
else:
    write_path = path

if write_path.exists():
    try:
        data = json.loads(write_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise SystemExit(f"refusing to replace invalid marketplace JSON: {write_path}")
else:
    data = {}

if not isinstance(data, dict):
    data = {}
data.setdefault("name", "local")
interface = data.setdefault("interface", {})
if isinstance(interface, dict):
    interface.setdefault("displayName", "Local Plugins")
else:
    data["interface"] = {"displayName": "Local Plugins"}

plugins = data.get("plugins")
if not isinstance(plugins, list):
    plugins = []
plugins = [item for item in plugins if not (isinstance(item, dict) and item.get("name") == plugin)]
plugins.append(entry)
data["plugins"] = plugins

mode = stat.S_IMODE(write_path.stat().st_mode) if write_path.exists() else 0o600
temp_name = None
try:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=write_path.parent,
        prefix=f".{write_path.name}.", delete=False,
    ) as handle:
        temp_name = handle.name
        os.fchmod(handle.fileno(), mode)
        json.dump(data, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_name, write_path)
finally:
    if temp_name and os.path.exists(temp_name):
        os.unlink(temp_name)
PY
  then
    warn "could not atomically update $marketplace"
    return 1
  fi

  ok "Codex personal marketplace → $marketplace"
}

# `codex plugin add` reports success from the presence of a version directory in
# the cache, not from its contents, so an install that copied nothing still reads
# as "installed, enabled". That is exactly how the symlink-skipping copy stayed
# invisible for months. Assert the copy actually carries a manifest.
codex_source_version() {
  local manifest="$CLAUDE_PLUGIN_SRC/.codex-plugin/plugin.json"
  [ -f "$manifest" ] || return 1
  sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$manifest" | head -1
}

# Assert the version being installed, not "some version". A previous release can
# leave a populated directory behind, so a scan that accepts any version dir with
# a manifest reports success while the CURRENT copy is the empty one — the exact
# failure this assertion exists to catch.
verify_codex_plugin_delivered() {
  have codex || return 0
  local cache_root version dir
  cache_root="${CODEX_HOME:-$HOME/.codex}/plugins/cache/$CODEX_MARKETPLACE_NAME/$PLUGIN_NAME"
  [ -d "$cache_root" ] || { warn "Codex plugin cache missing at $cache_root"; return 1; }
  version="$(codex_source_version || true)"
  if [ -z "$version" ]; then
    warn "Cannot read the source plugin version — delivery not asserted."
    return 1
  fi
  dir="$cache_root/$version"
  if [ -f "$dir/.codex-plugin/plugin.json" ]; then
    ok "Codex plugin delivered ($version)"
    return 0
  fi
  err "Codex plugin cache $version has no .codex-plugin/plugin.json — the copy is empty."
  err "Codex skips symlinks when copying; the marketplace source must be real files."
  return 1
}

register_codex_marketplace() {
  if ! have codex; then
    warn "Codex CLI ('codex') not found on PATH — skipping Codex marketplace registration."
    warn "Install Codex CLI, then re-run: ./install.sh --codex-only"
    return
  fi

  act "Preparing Codex personal plugin source"
  # The marketplace entry names the staged real-file tree, so --codex-only has to
  # build it too. Without this a Codex-only install publishes an entry pointing at
  # a directory nothing ever created, and a Codex-only re-run after pulling serves
  # whatever the last Claude-side run happened to stage.
  prepare_claude_plugin_source || return
  prepare_plugin_projection || return
  install_public_agent_skills || return
  install_local_cli_bin || return
  install_codex_native_agents || return
  write_codex_personal_marketplace || return

  act "Installing Codex plugin from personal marketplace"
  # codex 0.147 prints a table row ("<plugin>@<market>  installed, enabled  <ver>  <path>"),
  # not the "(installed)" form this once matched. The stale pattern never hit, so every
  # run re-added the plugin — which is part of why the empty cache went unnoticed.
  # codex has no `plugin update`; `plugin add` is the only refresh. Skipping merely
  # because a row says "installed" would pin the cache forever — the stale
  # "(installed)" pattern never matched, so the unconditional re-add WAS the
  # refresh. Skip only once the current version is actually delivered.
  # Buffer the listing instead of piping it. `grep -q` exits at the first match and
  # closes the pipe; Rust ignores SIGPIPE, so codex panics on the next write and
  # exits 101 rather than dying at 141, and under `set -o pipefail` that 101 becomes
  # the pipeline's status — the condition read false on every run and this branch
  # never once executed. The old "(installed)" pattern was accidentally immune
  # because it never matched, so grep drained the output and no EPIPE occurred:
  # fixing the pattern is what made the latent bug live. A herestring has no
  # pipeline, so pipefail has nothing to observe.
  local codex_listing
  codex_listing="$(codex plugin list 2>/dev/null || true)"
  if grep -qE "(^|[[:space:]])$PLUGIN_NAME@$CODEX_MARKETPLACE_NAME[[:space:]]+installed" <<<"$codex_listing" &&
     verify_codex_plugin_delivered; then
    skip "Codex plugin $PLUGIN_NAME@$CODEX_MARKETPLACE_NAME"
  elif codex plugin add "$PLUGIN_NAME@$CODEX_MARKETPLACE_NAME" >/dev/null 2>&1; then
    ok "Codex plugin installed → $PLUGIN_NAME@$CODEX_MARKETPLACE_NAME"
    verify_codex_plugin_delivered || true
  else
    warn "Codex personal marketplace is ready, but plugin install did not complete."
    warn "Run manually: codex plugin add $PLUGIN_NAME@$CODEX_MARKETPLACE_NAME"
  fi
}

run_codex_hooks_manager() {
  local python="" candidate
  while IFS= read -r candidate; do
    [ -n "$candidate" ] || continue
    python="$candidate"
    break
  done < <(python_candidates)
  if [ -z "$python" ]; then
    warn "Python >=3.11 not found — cannot manage Codex hooks"
    return 1
  fi
  PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    "$python" -m ui_clone.codex_hooks_install "$@"
}

cleanup_legacy_codex_hooks() {
  # Releases through 0.7.36 merged ui-clone routes into the user-global hooks
  # file. Current Codex plugins support bundled hooks again, but bundling the
  # enforcement template would still make it run in unrelated sessions. Remove
  # only ui-clone-owned legacy entries; project-local activation is handled by
  # `ui-clone hooks enable` inside an actual clone workspace.
  local hooks_file
  hooks_file="${CODEX_HOME:-$HOME/.codex}/hooks.json"
  if [ ! -f "$hooks_file" ] || ! grep -qF "ui_clone.hooks" "$hooks_file"; then
    skip "no legacy global ui-clone hooks"
    return 0
  fi
  act "Removing legacy global ui-clone hooks → $hooks_file"
  if run_codex_hooks_manager remove --hooks-file "$hooks_file"; then
    ok "Legacy global ui-clone hooks removed"
  else
    err "Legacy hook cleanup failed; $hooks_file left untouched"
    return 1
  fi
}

path_present() {
  [ -e "$1" ] || [ -L "$1" ]
}

mark_uninstall_incomplete() {
  UNINSTALL_INCOMPLETE=1
  warn "$*"
}

claude_marketplace_source() {
  have python3 || return 1

  MARKETPLACE_NAME="$MARKETPLACE_NAME" python3 - <<'PY'
import json
import os
from pathlib import Path

name = os.environ["MARKETPLACE_NAME"]
known = Path.home() / ".claude" / "plugins" / "known_marketplaces.json"
try:
    data = json.loads(known.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    data = None

if isinstance(data, dict) and name in data:
    entry = data.get(name)
    source = entry.get("source") if isinstance(entry, dict) else None
    path = source.get("path") if isinstance(source, dict) and source.get("source") == "directory" else None
    if not isinstance(path, str) and isinstance(entry, dict):
        path = entry.get("installLocation")
    if isinstance(path, str):
        print(path)
    raise SystemExit(0)

settings = Path.home() / ".claude" / "settings.json"
try:
    data = json.loads(settings.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(0)

entry = data.get("extraKnownMarketplaces", {}).get(name, {}) if isinstance(data, dict) else {}
source = entry.get("source") if isinstance(entry, dict) else None
path = source.get("path") if isinstance(source, dict) and source.get("source") == "directory" else None
if isinstance(path, str):
    print(path)
PY
}

is_ui_clone_plugin_source() {
  local source_path="$1"
  [ -d "$source_path" ] || return 1
  have python3 || return 1

  SOURCE_PATH="$source_path" PLUGIN_NAME="$PLUGIN_NAME" \
    MARKETPLACE_NAME="$MARKETPLACE_NAME" python3 - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["SOURCE_PATH"]).expanduser()
try:
    plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    marketplace = json.loads((root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)

plugin_name = os.environ["PLUGIN_NAME"]
plugins = marketplace.get("plugins") if isinstance(marketplace, dict) else None
valid = (
    isinstance(plugin, dict)
    and plugin.get("name") == plugin_name
    and isinstance(marketplace, dict)
    and marketplace.get("name") == os.environ["MARKETPLACE_NAME"]
    and isinstance(plugins, list)
    and any(isinstance(item, dict) and item.get("name") == plugin_name for item in plugins)
)
raise SystemExit(0 if valid else 1)
PY
}

remove_owned_symlink() {
  local path="$1"
  local expected_target="$2"
  local label="$3"

  if [ -L "$path" ] && [ "$(readlink "$path")" = "$expected_target" ]; then
    rm -f "$path"
    ok "removed $label $path"
  elif path_present "$path"; then
    warn "preserving user-owned path: $path"
  fi
}

public_skill_ownership_status() {
  local skill="$1"
  local dst="$AGENTS_SKILLS_DIR/$skill"
  local src="$REPO_ROOT/skills/$skill"
  local marketplace_source="${CLAUDE_UNINSTALL_SOURCE:-}"
  if [ -z "$marketplace_source" ]; then
    marketplace_source="$(claude_marketplace_source 2>/dev/null || true)"
  fi

  if ! path_present "$dst"; then
    printf '%s\n' "absent"
    return 0
  fi
  if [ -L "$dst" ]; then
    if [ "$(readlink "$dst")" = "$src" ]; then
      printf '%s\n' "removable:symlink"
    else
      printf '%s\n' "unproven"
    fi
    return 0
  fi
  [ -d "$dst" ] || { printf '%s\n' "unproven"; return 0; }

  local validated_marketplace=""
  if is_ui_clone_plugin_source "$marketplace_source"; then
    validated_marketplace="$marketplace_source"
  fi

  OWNERSHIP_PATH="$PUBLIC_SKILLS_OWNERSHIP" SKILL_NAME="$skill" \
    INSTALLED_PATH="$dst" CURRENT_SOURCE="$src" \
    MARKETPLACE_SOURCE="$validated_marketplace/skills/$skill" \
    REPO_ROOT="$REPO_ROOT" python3 - <<'PY'
import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path


def tree_hash(root: Path) -> str | None:
    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        if path.is_symlink():
            kind, content = b"L", os.readlink(path).encode()
        elif path.is_dir():
            kind, content = b"D", b""
        else:
            kind = b"X" if path.stat().st_mode & 0o111 else b"F"
            content = path.read_bytes()
        digest.update(kind + b"\0" + relative + b"\0")
        digest.update(str(len(content)).encode() + b"\0" + content + b"\0")
    return digest.hexdigest()


def archive_hash(repo: Path, tree_oid: str) -> str | None:
    try:
        archive = subprocess.check_output(
            ["git", "-C", str(repo), "archive", "--format=tar", tree_oid],
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    entries = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        for member in tar.getmembers():
            relative = member.name.rstrip("/")
            if not relative:
                continue
            if member.issym():
                kind, content = b"L", member.linkname.encode()
            elif member.isdir():
                kind, content = b"D", b""
            elif member.isfile():
                kind = b"X" if member.mode & 0o111 else b"F"
                extracted = tar.extractfile(member)
                content = extracted.read() if extracted else b""
            else:
                continue
            entries.append((relative, kind, content))
    digest = hashlib.sha256()
    for relative, kind, content in sorted(entries):
        digest.update(kind + b"\0" + relative.encode() + b"\0")
        digest.update(str(len(content)).encode() + b"\0" + content + b"\0")
    return digest.hexdigest()


def matches_repo_history(repo: Path, skill: str, expected_hash: str | None) -> bool:
    if expected_hash is None or not (repo / ".git").exists():
        return False
    skill_path = f"skills/{skill}"
    try:
        commits = subprocess.check_output(
            ["git", "-C", str(repo), "rev-list", "--all", "--", skill_path],
            text=True,
            stderr=subprocess.DEVNULL,
        ).splitlines()
    except (OSError, subprocess.CalledProcessError):
        return False
    tree_oids = set()
    for commit in commits:
        try:
            tree_oids.add(
                subprocess.check_output(
                    ["git", "-C", str(repo), "rev-parse", f"{commit}:{skill_path}"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
            )
        except subprocess.CalledProcessError:
            continue
    return any(archive_hash(repo, oid) == expected_hash for oid in tree_oids)


skill = os.environ["SKILL_NAME"]
installed = Path(os.environ["INSTALLED_PATH"])
installed_hash = tree_hash(installed)
ownership_path = Path(os.environ["OWNERSHIP_PATH"])
try:
    ownership = json.loads(ownership_path.read_text(encoding="utf-8"))
except FileNotFoundError:
    ownership = {}
except (OSError, json.JSONDecodeError):
    print("unproven")
    raise SystemExit(0)

entry = ownership.get("skills", {}).get(skill) if isinstance(ownership, dict) else None
if isinstance(entry, dict) and entry.get("path") == str(installed.resolve()):
    print("removable:receipt" if entry.get("sha256") == installed_hash else "customized")
    raise SystemExit(0)

for source_name in ("CURRENT_SOURCE", "MARKETPLACE_SOURCE"):
    source = Path(os.environ[source_name])
    source_hash = tree_hash(source)
    if source_hash and source_hash == installed_hash:
        print("removable:source")
        raise SystemExit(0)

repo = Path(os.environ["REPO_ROOT"])
print("removable:history" if matches_repo_history(repo, skill, installed_hash) else "unproven")
PY
}

verify_public_skills_removable() {
  local skill status
  for skill in $CODEX_PUBLIC_SKILLS; do
    status="$(public_skill_ownership_status "$skill")"
    case "$status" in
      absent|removable:*) ;;
      customized)
        mark_uninstall_incomplete "preserving post-install edits in $AGENTS_SKILLS_DIR/$skill"
        ;;
      *) mark_uninstall_incomplete "cannot prove ownership of $AGENTS_SKILLS_DIR/$skill" ;;
    esac
  done
}

clear_public_skill_ownership() {
  local skill="$1"
  [ -f "$PUBLIC_SKILLS_OWNERSHIP" ] || return 0

  OWNERSHIP_PATH="$PUBLIC_SKILLS_OWNERSHIP" SKILL_NAME="$skill" python3 - <<'PY'
import json
import os
import stat
import tempfile
from pathlib import Path

path = Path(os.environ["OWNERSHIP_PATH"])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)
skills = data.get("skills") if isinstance(data, dict) else None
if not isinstance(skills, dict) or os.environ["SKILL_NAME"] not in skills:
    raise SystemExit(0)
skills.pop(os.environ["SKILL_NAME"])
if not skills:
    path.unlink()
    raise SystemExit(0)

mode = stat.S_IMODE(path.stat().st_mode)
temp_name = None
try:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=f".{path.name}.", delete=False,
    ) as handle:
        temp_name = handle.name
        os.fchmod(handle.fileno(), mode)
        json.dump(data, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_name, path)
finally:
    if temp_name and os.path.exists(temp_name):
        os.unlink(temp_name)
PY
}

remove_installed_public_skills() {
  local skill dst status
  for skill in $CODEX_PUBLIC_SKILLS; do
    dst="$AGENTS_SKILLS_DIR/$skill"
    status="$(public_skill_ownership_status "$skill")"
    case "$status" in
      absent)
        if ! clear_public_skill_ownership "$skill"; then
          mark_uninstall_incomplete "could not clear stale ownership receipt for $skill"
        fi
        ;;
      removable:*)
        rm -rf "$dst"
        if clear_public_skill_ownership "$skill"; then
          ok "removed Codex public skill $dst"
        else
          mark_uninstall_incomplete "removed $dst but could not update its ownership receipt"
        fi
        ;;
    esac
  done
}

remove_codex_native_agents() {
  local src_dir="$REPO_ROOT/.codex/agents"
  local src dst
  [ -d "$src_dir" ] || return 0

  for src in "$src_dir"/*.toml; do
    [ -e "$src" ] || continue
    dst="$CODEX_NATIVE_AGENTS_DIR/$(basename "$src")"
    remove_owned_symlink "$dst" "$src" "Codex native agent"
  done
}

remove_owned_editable_install() {
  local python ownership found=0
  while IFS= read -r python; do
    [ -n "$python" ] || continue
    found=1
    if ! ownership="$(
      REPO_ROOT="$REPO_ROOT" DISTRIBUTION_NAME="$PLUGIN_NAME" "$python" - <<'PY'
import importlib.metadata
import json
import os
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

try:
    distribution = importlib.metadata.distribution(os.environ["DISTRIBUTION_NAME"])
except importlib.metadata.PackageNotFoundError:
    print("absent")
    raise SystemExit(0)

try:
    direct_url = json.loads(distribution.read_text("direct_url.json") or "")
except (TypeError, json.JSONDecodeError):
    print("preserved: missing or invalid direct_url.json")
    raise SystemExit(0)

dir_info = direct_url.get("dir_info") if isinstance(direct_url, dict) else None
url = direct_url.get("url") if isinstance(direct_url, dict) else None
if not isinstance(dir_info, dict) or dir_info.get("editable") is not True:
    print("preserved: distribution is not editable")
    raise SystemExit(0)
if not isinstance(url, str):
    print("preserved: editable distribution has no source URL")
    raise SystemExit(0)

parsed = urlparse(url)
if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
    print("preserved: editable source is not a local file URL")
    raise SystemExit(0)

source = Path(url2pathname(unquote(parsed.path))).resolve()
repo = Path(os.environ["REPO_ROOT"]).resolve()
if source != repo:
    print(f"preserved: editable source is {source}")
    raise SystemExit(0)

print("owned")
PY
    )"; then
      mark_uninstall_incomplete "could not inspect the ui-clone-skills editable install in $python"
      continue
    fi

    case "$ownership" in
      owned)
        if "$python" -m pip uninstall -y "$PLUGIN_NAME" >/dev/null 2>&1; then
          ok "removed editable install $PLUGIN_NAME from $python"
        elif "$python" -m pip uninstall -y --break-system-packages \
            "$PLUGIN_NAME" >/dev/null 2>&1; then
          ok "removed editable install $PLUGIN_NAME from $python"
        else
          mark_uninstall_incomplete "could not uninstall editable install $PLUGIN_NAME from $python"
        fi
        ;;
      absent) ;;
      preserved:*) warn "$python: $ownership" ;;
      *) warn "could not determine ui-clone-skills editable install ownership in $python" ;;
    esac
  done < <(python_candidates)

  if [ "$found" -eq 0 ]; then
    mark_uninstall_incomplete "Python >=3.11 absent — could not inspect ui-clone-skills editable installs"
  fi
}

remove_codex_personal_marketplace_entry() {
  local marketplace="$CODEX_PERSONAL_MARKETPLACE"
  path_present "$marketplace" || return 0

  if ! have python3; then
    warn "python3 absent — could not remove $PLUGIN_NAME from $marketplace"
    return 1
  fi

  local result
  if ! result="$(
    MARKETPLACE_PATH="$marketplace" PLUGIN_NAME="$PLUGIN_NAME" \
      PLUGIN_SOURCE_PATH="$CODEX_PLUGIN_SOURCE_PATH" \
       PLUGIN_SOURCE_PATH_LEGACY="$CODEX_PLUGIN_SOURCE_PATH_LEGACY" python3 - <<'PY'
import json
import os
import stat
import tempfile
from pathlib import Path

path = Path(os.environ["MARKETPLACE_PATH"]).expanduser()
plugin = os.environ["PLUGIN_NAME"]
source_path = os.environ["PLUGIN_SOURCE_PATH"]
legacy_source_path = os.environ.get("PLUGIN_SOURCE_PATH_LEGACY", "")

if path.is_symlink():
    try:
        write_path = path.resolve(strict=True)
    except OSError as error:
        raise SystemExit(f"refusing invalid marketplace symlink {path}: {error}")
    if not stat.S_ISREG(write_path.stat().st_mode):
        raise SystemExit(f"refusing non-regular marketplace target: {write_path}")
elif path.exists():
    write_path = path
    if not stat.S_ISREG(write_path.stat().st_mode):
        raise SystemExit(f"refusing non-regular marketplace file: {write_path}")
else:
    raise SystemExit(f"marketplace disappeared before update: {path}")

try:
    data = json.loads(write_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    print("preserved")
    raise SystemExit(0)

if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
    print("preserved")
    raise SystemExit(0)

plugins = data["plugins"]
kept = []
removed = 0
for item in plugins:
    source = item.get("source") if isinstance(item, dict) else None
    if (
        isinstance(item, dict)
        and item.get("name") == plugin
        and isinstance(source, dict)
        and source.get("source") == "local"
        and source.get("path") in {source_path, legacy_source_path}
    ):
        removed += 1
    else:
        kept.append(item)

if not removed:
    print("unchanged")
    raise SystemExit(0)

data["plugins"] = kept
mode = stat.S_IMODE(write_path.stat().st_mode)
temp_name = None
try:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=write_path.parent,
        prefix=f".{write_path.name}.", delete=False,
    ) as handle:
        temp_name = handle.name
        os.fchmod(handle.fileno(), mode)
        json.dump(data, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_name, write_path)
finally:
    if temp_name and os.path.exists(temp_name):
        os.unlink(temp_name)
print("removed")
PY
  )"; then
    warn "could not atomically update $marketplace"
    return 1
  fi

  case "$result" in
    removed) ok "removed Codex personal marketplace entry from $marketplace" ;;
    preserved) warn "preserving user-owned or invalid marketplace file: $marketplace" ;;
  esac
}

codex_personal_marketplace_entry_owned() {
  local marketplace="$CODEX_PERSONAL_MARKETPLACE"
  [ -f "$marketplace" ] || return 1
  have python3 || return 1

  MARKETPLACE_PATH="$marketplace" PLUGIN_NAME="$PLUGIN_NAME" \
    PLUGIN_SOURCE_PATH="$CODEX_PLUGIN_SOURCE_PATH" \
       PLUGIN_SOURCE_PATH_LEGACY="$CODEX_PLUGIN_SOURCE_PATH_LEGACY" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["MARKETPLACE_PATH"]).expanduser()
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)

for item in data.get("plugins", []) if isinstance(data, dict) else []:
    source = item.get("source") if isinstance(item, dict) else None
    if (
        isinstance(item, dict)
        and item.get("name") == os.environ["PLUGIN_NAME"]
        and isinstance(source, dict)
        and source.get("source") == "local"
        and source.get("path") in {os.environ["PLUGIN_SOURCE_PATH"],
                                   os.environ.get("PLUGIN_SOURCE_PATH_LEGACY", "")}
    ):
        raise SystemExit(0)
raise SystemExit(1)
PY
}

claude_marketplace_owned() {
  local source_path="${CLAUDE_UNINSTALL_SOURCE:-}"
  if [ -z "$source_path" ]; then
    source_path="$(claude_marketplace_source 2>/dev/null || true)"
  fi
  [ -n "$source_path" ] || return 1
  if [ -d "$source_path" ]; then
    is_ui_clone_plugin_source "$source_path"
  else
    [ "$(basename "$source_path")" = "$PLUGIN_NAME" ]
  fi
}

remove_plugin_projection() {
  local item src dst skill

  if [ -L "$CODEX_PLUGIN_DIR" ]; then
    local projection_target
    projection_target="$(readlink "$CODEX_PLUGIN_DIR")"
    if [ "$projection_target" = "$REPO_ROOT" ] ||
       is_ui_clone_plugin_source "$CODEX_PLUGIN_DIR"; then
      rm -f "$CODEX_PLUGIN_DIR"
      ok "removed legacy projection symlink $CODEX_PLUGIN_DIR"
    else
      warn "preserving user-owned path: $CODEX_PLUGIN_DIR"
    fi
    return 0
  fi

  for item in $CODEX_PLUGIN_PROJECTION_ITEMS; do
    src="$REPO_ROOT/$item"
    dst="$CODEX_PLUGIN_DIR/$item"
    remove_owned_symlink "$dst" "$src" "projection item"
  done

  for skill in $CODEX_PUBLIC_SKILLS; do
    src="$REPO_ROOT/skills/$skill"
    dst="$CODEX_PLUGIN_DIR/skills/$skill"
    remove_owned_symlink "$dst" "$src" "projection skill"
  done

  rmdir "$CODEX_PLUGIN_DIR/skills" 2>/dev/null || true
  if ! rmdir "$CODEX_PLUGIN_DIR" 2>/dev/null && path_present "$CODEX_PLUGIN_DIR"; then
    warn "preserving user-owned path: $CODEX_PLUGIN_DIR"
  fi
}

remove_install_marker() {
  local marker="$HOME/.config/ui-clone-skills/root"
  local installed_root=""

  if [ -f "$marker" ] && [ ! -L "$marker" ]; then
    IFS= read -r installed_root < "$marker" || true
    if [ "$installed_root" = "$REPO_ROOT" ]; then
      rm -f "$marker"
      ok "removed install marker"
      rmdir "$HOME/.config/ui-clone-skills" 2>/dev/null || true
      return 0
    fi
  fi
  if path_present "$marker"; then
    warn "preserving user-owned path: $marker"
  fi
}

uninstall_all() {
  section "Uninstall ui-clone-skills"
  local hooks_file codex_entry_owned=0 claude_plugin_removed=0
  local CLAUDE_UNINSTALL_SOURCE=""
  local UNINSTALL_INCOMPLETE=0
  hooks_file="${CODEX_HOME:-$HOME/.codex}/hooks.json"
  CLAUDE_UNINSTALL_SOURCE="$(claude_marketplace_source 2>/dev/null || true)"
  verify_public_skills_removable
  if [ -f "$hooks_file" ] && grep -qF "ui_clone.hooks" "$hooks_file"; then
    if run_codex_hooks_manager remove --hooks-file "$hooks_file"; then
      ok "stripped ui-clone gate hooks from $hooks_file"
    else
      mark_uninstall_incomplete "could not strip gate hooks from $hooks_file"
    fi
  fi
  remove_owned_editable_install
  if codex_personal_marketplace_entry_owned; then
    codex_entry_owned=1
    if ! have codex; then
      mark_uninstall_incomplete "codex absent — could not remove plugin $PLUGIN_NAME@$CODEX_MARKETPLACE_NAME"
    elif codex plugin remove "$PLUGIN_NAME@$CODEX_MARKETPLACE_NAME" >/dev/null 2>&1; then
      ok "removed Codex plugin $PLUGIN_NAME@$CODEX_MARKETPLACE_NAME"
    else
      mark_uninstall_incomplete "could not remove Codex plugin $PLUGIN_NAME@$CODEX_MARKETPLACE_NAME"
    fi
  fi
  if claude_marketplace_owned; then
    if ! have claude; then
      mark_uninstall_incomplete "claude absent — could not remove plugin $PLUGIN_NAME@$MARKETPLACE_NAME"
    elif claude plugin uninstall "$PLUGIN_NAME@$MARKETPLACE_NAME" >/dev/null 2>&1; then
      ok "uninstalled Claude plugin $PLUGIN_NAME@$MARKETPLACE_NAME"
      claude_plugin_removed=1
    else
      mark_uninstall_incomplete "could not uninstall Claude plugin $PLUGIN_NAME@$MARKETPLACE_NAME"
    fi
    if [ "$claude_plugin_removed" -eq 1 ] &&
       claude plugin marketplace remove "$MARKETPLACE_NAME" >/dev/null 2>&1; then
      ok "removed Claude marketplace $MARKETPLACE_NAME"
    elif [ "$claude_plugin_removed" -eq 1 ]; then
      mark_uninstall_incomplete "could not remove Claude marketplace $MARKETPLACE_NAME"
    fi
  fi

  if [ "$UNINSTALL_INCOMPLETE" -ne 0 ]; then
    warn "Uninstall incomplete; retained local artifacts and marketplace metadata for a safe retry."
    return 1
  fi

  if [ "$codex_entry_owned" -eq 1 ]; then
    if ! remove_codex_personal_marketplace_entry; then
      mark_uninstall_incomplete "could not remove Codex personal marketplace entry"
      return 1
    fi
  fi
  remove_owned_symlink "$LOCAL_CLI_BIN" "$CODEX_PLUGIN_DIR/bin/ui-clone" "local ui-clone bin"
  remove_installed_public_skills
  if [ "$UNINSTALL_INCOMPLETE" -ne 0 ]; then
    warn "Uninstall incomplete; retained remaining local artifacts for a safe retry."
    return 1
  fi
  remove_codex_native_agents
  remove_plugin_projection
  remove_install_marker
  warn "System dependencies and user-managed Codex config.toml settings were not installed by this script and were left in place."
  ok "Uninstall complete."
}

main() {
  if [ "$DO_UNINSTALL" -eq 1 ]; then
    uninstall_all
    return 0
  fi
  section "ui-clone-skills installer (OS: $OS, repo: $REPO_ROOT)"

  if [ "$NO_DEPS" -eq 0 ]; then
    section "System dependencies"
    if [ "$OS" = "mac" ] && ! have brew; then
      err "Homebrew not found. Install from https://brew.sh/ and re-run."
      exit 1
    fi
    ensure_uv
    ensure_ffmpeg
    ensure_imagemagick
    ensure_dssim
    ensure_node_npm
    ensure_agent_browser

    section "Python deps"
    uv_sync
  else
    warn "--no-deps: skipping system dependency bootstrap"
  fi

  # ui_clone must import from ANY cwd — cold clone loops + gates run outside the
  # repo. Runs even under --no-deps (cheap + idempotent once installed).
  section "ui_clone (editable, importable anywhere)"
  editable_install

  if [ "$NO_MARKETPLACE" -eq 0 ] && [ "$INSTALL_CLAUDE" -eq 1 ]; then
    section "Claude Code plugin"
    register_marketplace
    install_claude_plugin
    verify_claude_plugin_delivery || return 1
  fi

  if [ "$NO_MARKETPLACE" -eq 0 ] && [ "$INSTALL_CODEX" -eq 1 ]; then
    section "Codex plugin"
    register_codex_marketplace
  fi

  # Older releases registered ui-clone hooks globally. Clean those entries on
  # every Codex-targeted install, including --no-marketplace updates.
  if [ "$INSTALL_CODEX" -eq 1 ]; then
    section "Codex hook cleanup"
    cleanup_legacy_codex_hooks
  fi

  # Install marker — lets inline preflight bash and shared scripts resolve the
  # checkout when no PLUGIN_ROOT/CLAUDE_PLUGIN_ROOT/CODEX_PLUGIN_ROOT env var is
  # set (e.g., SKILL.md ran outside a plugin host). Cheap and idempotent.
  section "Install marker"
  if mkdir -p "$HOME/.config/ui-clone-skills" 2>/dev/null && printf '%s\n' "$REPO_ROOT" > "$HOME/.config/ui-clone-skills/root"; then
    ok "marker → $HOME/.config/ui-clone-skills/root"
  else
    warn "Could not write $HOME/.config/ui-clone-skills/root — inline preflight will fall back to glob search."
  fi

  section "Done"
  if [ "$NO_MARKETPLACE" -eq 1 ]; then
    cat <<EOF
  Next step:

      Add this checkout as a plugin source for your agent host.
      Claude Code: run install.sh without --no-marketplace (registers the marketplace and installs the plugin)
      Codex:       run install.sh --codex-only, then verify codex plugin list shows ${PLUGIN_NAME}@${CODEX_MARKETPLACE_NAME} installed

  Verify deps:
      agent-browser --version && uv --version && ffmpeg -version | head -1
EOF
  else
    echo "  Next step:"
    if [ "$INSTALL_CLAUDE" -eq 1 ]; then
      cat <<EOF

      Claude Code: plugin installed (user scope) — restart sessions to load it.
          Verify: claude plugin list | grep ${PLUGIN_NAME}@${MARKETPLACE_NAME}
          Source: ${CODEX_PLUGIN_DIR} is a lean projection symlinked to ${REPO_ROOT}
EOF
    fi
    if [ "$INSTALL_CODEX" -eq 1 ]; then
      cat <<EOF

      Codex: restart the CLI to pick up the registered marketplace plugin.
             Verify plugin: codex plugin list | grep '${PLUGIN_NAME}@${CODEX_MARKETPLACE_NAME}'
             Source: ${CODEX_PLUGIN_DIR} is a public-skill projection symlinked to ${REPO_ROOT}
             Clone workspaces configure hooks automatically through the ui-reverse-engineering skill.
             Manual setup: ui-clone hooks enable --project-root <clone-project>
             Review /hooks if prompted, then start a fresh Codex session.
EOF
    fi
    cat <<EOF

  Verify deps:
      agent-browser --version && uv --version && ffmpeg -version | head -1
EOF
  fi

  cache_ttl_notice
  loop_setup_notice
}

main "$@"
