# Install — Claude Code & Codex

The default install registers **both** Claude Code and Codex marketplaces in one pass. Each registration is skipped silently if that host's CLI (`claude` / `codex`) is not on PATH, so the same one-liner works on a Claude-only box, a Codex-only box, or a box with both.

```bash
tmp=$(mktemp) && curl -LsSf -o "$tmp" https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh && bash "$tmp" && rm -f "$tmp"
```

Inside Claude Code, after the installer finishes:

```
/plugin install ui-clone-skills@voidmatcha
```

For Codex: the installer creates a lightweight personal plugin source at `~/plugins/ui-clone-skills` (skills), writes `~/.agents/plugins/marketplace.json`, runs `codex plugin add ui-clone-skills@local`, **and merges the gate hooks into `~/.codex/hooks.json`**. That projection includes only the three public skills, so maintainer-only skills such as `skills/benchmark` do not appear in Codex. Verify the plugin with `codex plugin list | grep 'ui-clone-skills@local (installed)'` and the hooks with `grep -q ui_clone.hooks ~/.codex/hooks.json && echo gate-hooks OK`.

codex-cli 0.137 removed the `plugin_hooks` feature, so plugin-manifest hooks no longer load — that is why the installer merges the gate entries into the stable `~/.codex/hooks.json` instead (idempotently, preserving your existing hooks). Accept the one-time hook-trust prompt on the next Codex session. If you skip the merge (e.g. a docs-only `npx skills add`), the skills still load but the hook gate chain does not run: it can guide the agent but cannot block bypasses.

The installer is idempotent: it bootstraps shared dependencies, registers the local checkout for whichever host(s) are present, and skips anything already installed.

## Use a development checkout as the Codex plugin source

When you want Codex to use a specific local checkout (for example
`~/Documents/ui-skills`) instead of the curl-installed checkout under
`~/.local/share/ui-clone-skills`, run the installer from that checkout:

```bash
cd ~/Documents/ui-skills
./install.sh --codex-only --no-deps
```

This does **not** clone a second copy. It treats the current directory as the
source of truth, then recreates the Codex projection at
`~/plugins/ui-clone-skills` with symlinks back to the checkout:

```text
~/plugins/ui-clone-skills/.codex-plugin -> ~/Documents/ui-skills/.codex-plugin
~/plugins/ui-clone-skills/hooks         -> ~/Documents/ui-skills/hooks
~/plugins/ui-clone-skills/ui_clone      -> ~/Documents/ui-skills/ui_clone
~/plugins/ui-clone-skills/scripts       -> ~/Documents/ui-skills/scripts
~/plugins/ui-clone-skills/skills/<public-skill>
    -> ~/Documents/ui-skills/skills/<public-skill>
```

Codex still lists the plugin as `ui-clone-skills@local` with path
`~/plugins/ui-clone-skills`; that is expected. The projection is intentionally
smaller than the full checkout so Codex sees only the public skills
(`ui-reverse-engineering`, `ui-capture`, and `visual-debug`) and not
maintainer-only directories such as `skills/benchmark`, scratch runs, caches, or
virtualenvs.

Re-run the same command whenever you want to re-point Codex at a different
checkout or refresh the projection after moving the checkout:

```bash
cd /path/to/the/checkout/Codex/should/use
./install.sh --codex-only --no-deps
```

The installer also writes the fallback marker
`~/.config/ui-clone-skills/root` to the current checkout. Hook commands normally
receive `CODEX_PLUGIN_ROOT` from the plugin host, but standalone scripts and
inline skill snippets use that marker when no host-provided plugin root is
available.

## Install only one host

```bash
# downloaded installer
tmp=$(mktemp) && curl -LsSf -o "$tmp" https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh && bash "$tmp" --claude-only && rm -f "$tmp"
tmp=$(mktemp) && curl -LsSf -o "$tmp" https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh && bash "$tmp" --codex-only && rm -f "$tmp"

# from a local checkout
./install.sh --claude-only       # register Claude marketplace only
./install.sh --codex-only        # register Codex marketplace only
```

## Manual / advanced install paths

```bash
git clone https://github.com/voidmatcha/ui-clone-skills.git
cd ui-clone-skills
./install.sh                    # all flags: ./install.sh --help
./install.sh --no-deps          # skip system deps (already installed)
./install.sh --no-marketplace   # skip all marketplace registrations
./install.sh --claude-only      # Claude marketplace only
./install.sh --codex-only       # Codex marketplace only (alias: --codex)
```

## SKILL.md-only copy (no hooks)

```bash
npx skills add voidmatcha/ui-clone-skills
```

⚠️ `npx skills add voidmatcha/ui-clone-skills` is a no-hooks path only when the receiving host copies the three `SKILL.md` files and does not register bundled hook manifests. In that docs-only mode, the `pre_generate`, `pre_bash`, `section_gate`, and `session_resume` hooks don't run, system deps aren't bootstrapped, and the gate chain can't stop early "done" claims. Use this only when you want the skill docs without enforcement.

## Install system deps manually

```bash
# one-liner (macOS)
brew install imagemagick dssim ffmpeg && npm i -g agent-browser && uv_tmp=$(mktemp) && curl -LsSf -o "$uv_tmp" https://astral.sh/uv/install.sh && sh "$uv_tmp" && rm -f "$uv_tmp"

# one-liner (Linux / WSL2)
sudo apt install -y ffmpeg imagemagick && cargo install dssim && npm i -g agent-browser && uv_tmp=$(mktemp) && curl -LsSf -o "$uv_tmp" https://astral.sh/uv/install.sh && sh "$uv_tmp" && rm -f "$uv_tmp"

# verify
agent-browser --version && magick --version && dssim --help && ffmpeg -version && uv --version && python3 --version
```

`uv` auto-creates a virtualenv and installs `scikit-image` + `Pillow` on first run — no manual `pip install` needed.
