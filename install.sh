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
#   --yes            assume yes for prompts (e.g. apt sudo install)
#
# Default registers BOTH Claude Code and Codex marketplaces. Each registration
# is skipped automatically if the host CLI ('claude' / 'codex') is absent.
#
# Env:
#   UI_CLONE_REPO    git URL to clone (default: https://github.com/voidmatcha/ui-clone-skills.git)
#   UI_CLONE_REF     branch/tag/sha to checkout after clone (default: leave on default branch)
#   INSTALL_DIR      where to clone when running via curl-pipe (default: ~/.local/share/ui-clone-skills)
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
CODEX_PLUGIN_SOURCE_PATH="./plugins/$PLUGIN_NAME"
CODEX_NATIVE_AGENTS_DIR="${CODEX_HOME:-$HOME/.codex}/agents"
CODEX_PUBLIC_SKILLS="ui-reverse-engineering ui-capture visual-debug"

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
      claude --plugin-dir "$REPO_ROOT"
      > Drive the ui-clone-skills pipeline for tmp/ref/<component> until
      > python -m ui_clone.goal tmp/ref/<component> --check-done exits 0.

  Codex (CLI ≥ 0.128.0) — enable the native /goal feature:
      # Append to ~/.codex/config.toml:
      [features]
      goals = true
      # (Gate hooks are installed by install.sh into ~/.codex/hooks.json — the
      #  plugin_hooks feature was removed in codex-cli 0.137 and is not used.)
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
  # `claude plugin marketplace add` is idempotent in recent CLI versions; tolerate either outcome.
  act "Registering local repo as marketplace '$MARKETPLACE_NAME'"
  if claude plugin marketplace add "$REPO_ROOT" >/dev/null 2>&1; then
    ok "marketplace '$MARKETPLACE_NAME' registered"
  else
    skip "marketplace '$MARKETPLACE_NAME' already registered (or CLI declined re-add)"
  fi
}

prepare_codex_plugin_projection() {
  # Codex installs a local plugin by copying its source path into a cache. Point
  # that source at a small projection instead of the development checkout; the
  # repo can contain local scratch runs, screenshots, venvs, and caches.
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
      err "Refusing to use Codex plugin projection at repo root: $plugin_dir"
      return 1
    fi
  elif [ -e "$plugin_dir" ]; then
    err "Codex plugin path exists but is not a directory: $plugin_dir"
    return 1
  fi

  mkdir -p "$plugin_dir"

  local item src dst
  for item in .codex-plugin .codex hooks scripts ui_clone docs AGENTS.md README.md pyproject.toml uv.lock LICENSE.txt; do
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

  ok "Codex plugin projection → $plugin_dir (source: $REPO_ROOT)"
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

  if have python3; then
    MARKETPLACE_PATH="$marketplace" PLUGIN_NAME="$PLUGIN_NAME" PLUGIN_SOURCE_PATH="$CODEX_PLUGIN_SOURCE_PATH" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["MARKETPLACE_PATH"]).expanduser()
plugin = os.environ["PLUGIN_NAME"]
source_path = os.environ["PLUGIN_SOURCE_PATH"]
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

if path.exists():
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
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

path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
  else
    cat > "$marketplace" <<EOF
{
  "name": "local",
  "interface": {
    "displayName": "Local Plugins"
  },
  "plugins": [
    {
      "name": "$PLUGIN_NAME",
      "source": {
        "source": "local",
        "path": "$CODEX_PLUGIN_SOURCE_PATH"
      },
      "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL"
      },
      "category": "Developer Tools"
    }
  ]
}
EOF
  fi

  ok "Codex personal marketplace → $marketplace"
}

register_codex_marketplace() {
  if ! have codex; then
    warn "Codex CLI ('codex') not found on PATH — skipping Codex marketplace registration."
    warn "Install Codex CLI, then re-run: ./install.sh --codex-only"
    return
  fi

  act "Preparing Codex personal plugin source"
  prepare_codex_plugin_projection || return
  install_codex_native_agents || return
  write_codex_personal_marketplace || return

  act "Installing Codex plugin from personal marketplace"
  if codex plugin list 2>/dev/null | grep -qE "^[[:space:]]+$PLUGIN_NAME@$CODEX_MARKETPLACE_NAME \\(installed\\)"; then
    skip "Codex plugin $PLUGIN_NAME@$CODEX_MARKETPLACE_NAME"
  elif codex plugin add "$PLUGIN_NAME@$CODEX_MARKETPLACE_NAME" >/dev/null 2>&1; then
    ok "Codex plugin installed → $PLUGIN_NAME@$CODEX_MARKETPLACE_NAME"
  else
    warn "Codex personal marketplace is ready, but plugin install did not complete."
    warn "Run manually: codex plugin add $PLUGIN_NAME@$CODEX_MARKETPLACE_NAME"
  fi
}

merge_codex_hooks() {
  # codex-cli >= 0.137 REMOVED the `plugin_hooks` feature, so the plugin's
  # hooks/codex-hooks.json gates no longer load from the plugin manifest. Merge
  # them into the stable, OMX-shared ~/.codex/hooks.json instead — idempotent,
  # touches only `.hooks` (OMX's trust `state` and native wrappers are preserved).
  # The merged commands resolve the checkout via the install marker, so this is
  # host- and source-agnostic (local working tree or github clone).
  have codex || { warn "codex CLI absent — skipping gate-hook merge"; return; }
  local hooks_file plugin_hooks
  hooks_file="${CODEX_HOME:-$HOME/.codex}/hooks.json"
  plugin_hooks="$REPO_ROOT/hooks/codex-hooks.json"
  if [ ! -f "$plugin_hooks" ]; then
    warn "missing $plugin_hooks — skipping gate-hook merge"; return
  fi
  act "Merging ui-clone gate hooks → $hooks_file"
  if uv run --project "$REPO_ROOT" python -m ui_clone.codex_hooks_install merge \
       --hooks-file "$hooks_file" --plugin "$plugin_hooks"; then
    ok "Codex gate hooks installed (idempotent)"
    warn "First Codex session prompts once to trust the new hooks — accept it."
  else
    err "Gate-hook merge failed; $hooks_file left untouched"
  fi
}

uninstall_all() {
  section "Uninstall ui-clone-skills"
  if have codex; then
    local hooks_file
    hooks_file="${CODEX_HOME:-$HOME/.codex}/hooks.json"
    if [ -f "$hooks_file" ]; then
      if uv run --project "$REPO_ROOT" python -m ui_clone.codex_hooks_install remove \
           --hooks-file "$hooks_file"; then
        ok "stripped ui-clone gate hooks from $hooks_file"
      else
        warn "could not strip gate hooks from $hooks_file"
      fi
    fi
    if codex plugin remove "$PLUGIN_NAME@$CODEX_MARKETPLACE_NAME" >/dev/null 2>&1; then
      ok "removed Codex plugin $PLUGIN_NAME@$CODEX_MARKETPLACE_NAME"
    else
      skip "Codex plugin $PLUGIN_NAME@$CODEX_MARKETPLACE_NAME"
    fi
  fi
  if [ -e "$CODEX_PLUGIN_DIR" ]; then
    rm -rf "$CODEX_PLUGIN_DIR" && ok "removed projection $CODEX_PLUGIN_DIR"
  fi
  if have claude; then
    if claude plugin uninstall "$PLUGIN_NAME@$MARKETPLACE_NAME" >/dev/null 2>&1; then
      ok "uninstalled Claude plugin $PLUGIN_NAME@$MARKETPLACE_NAME"
    else
      skip "Claude plugin $PLUGIN_NAME@$MARKETPLACE_NAME"
    fi
    if claude plugin marketplace remove "$MARKETPLACE_NAME" >/dev/null 2>&1; then
      ok "removed Claude marketplace $MARKETPLACE_NAME"
    else
      skip "Claude marketplace $MARKETPLACE_NAME"
    fi
  fi
  rm -f "$HOME/.config/ui-clone-skills/root" 2>/dev/null && ok "removed install marker" || true
  warn "Left in place: Codex native agents (~/.codex/agents/*.toml) and any config.toml [plugins] block — remove manually if desired."
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

  if [ "$NO_MARKETPLACE" -eq 0 ] && [ "$INSTALL_CLAUDE" -eq 1 ]; then
    section "Claude Code plugin"
    register_marketplace
  fi

  if [ "$NO_MARKETPLACE" -eq 0 ] && [ "$INSTALL_CODEX" -eq 1 ]; then
    section "Codex plugin"
    register_codex_marketplace
  fi

  # Codex gate hooks are a SEPARATE concern from the plugin/marketplace (the
  # plugin only carries skills now that codex removed plugin_hooks), so install
  # them whenever Codex is targeted — even under --no-marketplace.
  if [ "$INSTALL_CODEX" -eq 1 ]; then
    section "Codex gate hooks"
    merge_codex_hooks
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
      Claude Code: run install.sh without --no-marketplace, then /plugin install ${PLUGIN_NAME}@${MARKETPLACE_NAME}
      Codex:       run install.sh --codex-only, then verify codex plugin list shows ${PLUGIN_NAME}@${CODEX_MARKETPLACE_NAME} installed

  Verify deps:
      agent-browser --version && uv --version && ffmpeg -version | head -1
EOF
  else
    echo "  Next step:"
    if [ "$INSTALL_CLAUDE" -eq 1 ]; then
      cat <<EOF

      Claude Code (inside the app):
          /plugin install ${PLUGIN_NAME}@${MARKETPLACE_NAME}
EOF
    fi
    if [ "$INSTALL_CODEX" -eq 1 ]; then
      cat <<EOF

      Codex: restart the CLI to pick up the registered marketplace + gate hooks.
             Verify plugin: codex plugin list | grep '${PLUGIN_NAME}@${CODEX_MARKETPLACE_NAME} (installed)'
             Verify hooks:  grep -q ui_clone.hooks "${CODEX_HOME:-\$HOME/.codex}/hooks.json" && echo gate-hooks OK
             Source: ${CODEX_PLUGIN_DIR} is a public-skill projection symlinked to ${REPO_ROOT}
             (Accept the one-time hook-trust prompt on first Codex session.)
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
