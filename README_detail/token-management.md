# Token management

UI cloning sessions are token-intensive — DOM trees, computed styles, and JS bundles can blow through context fast. The plugin includes several built-in mitigations, plus integrates with external tools.

**Built-in:**

| Strategy | How |
|---|---|
| Zero vision tokens for verification | AE/SSIM CLI tools diff screenshots. LLM only reads a single diff image on FAIL |
| Progressive-disclosure sub-docs | SKILL.md ~6K tokens. 48 sub-docs load only when their step runs |
| Pipe-to-file rule | Large `eval` output goes to `tmp/ref/*.json`, then `Read`/`Grep` specific lines |
| Single source of truth | `transition-spec.json` produced once — implementation reads it, never re-greps bundles |
| Bash loop breaker | After 10+ consecutive Bash calls, stop and analyze before continuing |

**Anthropic prompt cache TTL — `ENABLE_PROMPT_CACHING_1H=1`:**

Long cloning sessions re-send the same SKILL.md / extraction context many times. With the default 5-minute cache TTL, any wait longer than 5 min (gate, comparison, browser navigation) evicts that cache and bills the full prompt again on the next turn.

| Plan | Default | Action |
|---|---|---|
| Enterprise / Pro / Max | 1h (auto) | nothing — server-side |
| Team / API key | 5min | Add `export ENABLE_PROMPT_CACHING_1H=1` to your shell rc, then **restart your agent host**. |

Where to put the export depends on shell + how you launch your agent host:

| Shell | Terminal-launched | GUI-launched (Spotlight / Dock / `.app`) |
|---|---|---|
| zsh | `~/.zshrc` | `~/.zshenv` (loads in non-interactive shells too) |
| bash | `~/.bashrc` (Linux) / `~/.bash_profile` (macOS Terminal default) | no clean equivalent — try `~/.bash_profile`; if still unset, use `launchctl setenv` on macOS |

Editing the rc file does not affect an already-running agent host; the env is captured at launch.

The plugin's pipeline assumes 1h TTL when budgeting how aggressively to re-read SKILL.md / sub-docs between gates. With 5min, repeated `python -m ui_clone.gate` calls and `agent-browser` round-trips between turns each pay a cache miss.

**External — [rtk](https://github.com/rtk-ai/rtk) (Rust Token Killer):**

`rtk` is a CLI proxy that intercepts shell commands (`git status`, `ls`, `cat`, etc.) and filters verbose output before it reaches the LLM. Saves 60–90% tokens on dev operations.

```bash
brew install rtk
rtk gain             # show token savings analytics
```

When installed alongside this plugin, `rtk` can reduce token cost for `git`, `ls`, `find`, and other shell commands issued during the pipeline. In Claude Code, no configuration is needed because hooks rewrite commands transparently; other hosts need equivalent hook support.
