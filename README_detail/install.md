# Install — Claude Code & Codex

The default install registers **both** Claude Code and Codex marketplaces in one pass. Each registration is skipped silently if that host's CLI (`claude` / `codex`) is not on PATH, so the same one-liner works on a Claude-only box, a Codex-only box, or a box with both.

```bash
curl -LsSf https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh | bash
```

Inside Claude Code, after the installer finishes:

```
/plugin install ui-clone-skills@voidmatcha
```

For Codex: the installer creates a lightweight personal plugin source at `~/plugins/ui-clone-skills`, writes `~/.agents/plugins/marketplace.json`, and runs `codex plugin add ui-clone-skills@local`. Verify `codex plugin list` shows `ui-clone-skills@local (installed)`, then launch Codex with plugin hooks enabled:

```bash
codex --enable plugin_hooks
```

If your Codex build does not support trusted plugin hooks yet, the skills may load without the hook gate chain. Treat that as docs-only mode for validation: it can guide the agent, but it cannot block bypasses.

The installer is idempotent: it bootstraps shared dependencies, registers the local checkout for whichever host(s) are present, and skips anything already installed.

## Install only one host

```bash
# curl-pipe (flags pass through with -s --)
curl -LsSf https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh | bash -s -- --claude-only
curl -LsSf https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh | bash -s -- --codex-only

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
brew install imagemagick dssim ffmpeg && npm i -g agent-browser && curl -LsSf https://astral.sh/uv/install.sh | sh

# one-liner (Linux / WSL2)
sudo apt install -y ffmpeg imagemagick && cargo install dssim && npm i -g agent-browser && curl -LsSf https://astral.sh/uv/install.sh | sh

# verify
agent-browser --version && magick --version && dssim --help && ffmpeg -version && uv --version && python3 --version
```

`uv` auto-creates a virtualenv and installs `scikit-image` + `Pillow` on first run — no manual `pip install` needed.
