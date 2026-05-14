# AGENTS.md — ui-clone-skills development guide

Canonical project guide for Claude Code, Codex, and other agent hosts. Claude Code loads `CLAUDE.md`, which imports this file via `@AGENTS.md`; Codex and other `AGENTS.md`-aware tools read this file directly.

## Project structure

```
ui_clone/          Python package (gates, hooks, pipeline, DAG, metrics)
skills/            3 reusable skills; skill-owned primitives live under skills/<skill>/scripts/
scripts/           Repo automation: ci/, hooks/, extract/, verify/
hooks/             Claude/Codex host hook registration and shim plumbing
tests/             pytest suite for gates, hooks, metrics, and pipeline behavior
.claude-plugin/    Claude Code plugin manifest and marketplace metadata
.codex-plugin/     Codex plugin manifest
CLAUDE.md          Thin Claude Code entrypoint that imports this file
```

## Script location policy

- Host lifecycle hook registration stays in `hooks/` (`hooks.json`, `codex-hooks.json`, `shim.sh`). Do not move `shim.sh` into `scripts/`; hook manifests call it as host integration plumbing.
- Repo-level automation lives in `scripts/`: `scripts/ci/` for CI/review/security, `scripts/hooks/` for local git/tool hooks, `scripts/extract/` for shared extraction helpers, and `scripts/verify/` for shared verification wrappers.
- Skill-owned primitives live under `skills/<skill>/scripts/`. `skills/visual-debug/scripts/` owns AE/SSIM, section, transition, and visual-diff primitives.
- Orchestration skills should reference shared/skill-owned scripts by their canonical location instead of duplicating compatibility shims.

## Verification gate (must pass before commit)

```
[] bash scripts/ci/ci-local.sh — 0 failures (mirrors GitHub Actions test job; runs pytest + mypy + ruff + shell + review)
[] bash scripts/ci/pre-push-security.sh — 0 blockers (security + cross-ref + version sync)
```

`scripts/ci/ci-local.sh` is the single source of truth for what CI runs. `scripts/hooks/pre-push-guard.sh` calls it automatically before `git push` when configured as an agent hook (bypass for emergencies: `UI_RE_SKIP_CI_LOCAL=1 git push`). If you change CI, update `ci-local.sh` to match — and vice versa. Run a single test module with `uv run python -m pytest tests/test_<module>.py`; validate gates with `python -m ui_clone.gate tmp/ref/<c> all`.

### Verification tier (cost control)

`skills/visual-debug/scripts/verification-plan.sh` accepts `--tier=quick|standard|comprehensive` (env: `UI_CLONE_VERIFY_TIER`, default `comprehensive`). Each `add_check` row is tagged with a `min_tier`; the plan emits only checks at or below the active tier.

- `quick` (~10s) — static analysis + JSON-comparison rows only (`hydration-check`, `tailwind-transform-conflict`, `transition-spec-coverage`, `runtime-spec-coverage`). Use during inner iteration loops where running the full browser sweep on every change is wasteful.
- `standard` (~1min) — `quick` + one-shot browser interactions (`scroll-end-completion`, `reveal-trigger`, `transition-compare`, `font-parity`). No 60fps video recording.
- `comprehensive` (~5min+) — `standard` + 60fps frame-by-frame motion compares (`video-motion-compare`, `hover-state-compare`, `click-state-compare`). **Default** — preserves the unconditional dispatch from before the tier system was added.

Default stays `comprehensive` so existing callers and CI keep their current safety guarantees. Drop to `quick`/`standard` only when iterating against a single signal class and you want a faster feedback loop.

## Rules

### Language
- All skill docs (`skills/**/*.md`), README, CHANGELOG, eval fixtures (`skills/*/evals/*.json`): **English only**. Enforced by `scripts/ci/review.sh` language check.
- Code comments: English only
- Commit messages: English only

### Identity / example placeholders
- Never embed real company, employer, service, or third-party site names in code, comments, docs, or fixtures. Use generic placeholders: `project-a` / `project-b` / `example`, `example.com` / `target-site.com`, `<cdn-domain-1>` / `<streaming-cdn-host>` / `<image-cdn-host>`, "a partner site" for prose. The plugin is a public-facing marketplace surface; leakage of real names is unprofessional and embeds NDA-adjacent info. Enforced by `scripts/ci/pre-push-security.sh` "Identity leakage" section — add new patterns to its `leak_patterns=()` array.
- **One explicit exception: `realfood.gov` is the canonical benchmark target for the internal `skills/benchmark/` skill.** It is a public US-government site, not NDA-adjacent, and the maintainer regression suite must hit a real production site to measure prompt / sub-doc / external-dep drift (the entire point of `skills/benchmark/SKILL.md` is end-to-end fidelity against an unmocked target). Permitted in: `skills/benchmark/**`, `benchmark/work/**` artifacts, and the prompt strings the agent emits when it acts on the "run benchmark" trigger. Still forbidden elsewhere — do NOT add `realfood.gov` to user-facing examples, public skill docs (`skills/ui-reverse-engineering/**`, `skills/ui-capture/**`, `skills/visual-debug/**`), README, or marketplace metadata.

### Naming
- Python package: `ui_clone`
- npm package: `agent-browser`
- GitHub: `vercel-labs/agent-browser`, `rtk-ai/rtk`
- Owner: `voidmatcha`

### Pipeline step numbering and gate → artifact mapping

Reference tables moved to `docs/gates.md` to keep `AGENTS.md` thin (re-injected every turn). Read `docs/gates.md` when adding/changing a gate, a sub-doc, or `ui_clone/gate.py` `VALID_GATES`. The dispatch keys (`reference`, `extraction`, `bundle`, `paid-features`, `spec`, `pre-generate`, `post-implement`, `boundary`, `font-parity`, `section-compare`) and the step → sub-doc mapping live there.

### Sub-doc conventions
- Title format: `# <Name> — Step <N>` matching SKILL.md pipeline
- "After this step" links must point to the correct next step per SKILL.md
- `animations-detected.json` is merged into `extracted.json` at Step 6b — not checked by gates directly
- All agent-browser commands must use `--session <name>`
- All JS evals must use IIFE: `(() => { ... })()`

### Version sync
- `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, and `.codex-plugin/plugin.json` versions must match
- `pyproject.toml` version must be updated on release
- `scripts/hooks/pre-push-guard.sh` enforces this automatically

### Token management
- Large eval output → pipe to file, then Read/Grep specific lines
- Never let DOM/style JSON print to stdout
- Ref screenshots: AE/SSIM diff only, never Read for comparison (except Phase E)
- Large source files (`ui_clone/pipeline.py`, `skills/ui-reverse-engineering/SKILL.md`, sub-docs >200 lines): use `Read` with `offset` + `limit` or `Grep` — never read whole-file when only a section is needed. Long command output (`ci-local.sh` without `--quiet`, full test logs, agent-browser eval dumps): redirect to `/tmp/<name>.log` and `tail`/`grep` only the part you need. Reading whole files and full command output is the dominant cause of autocompact thrashing on this repo.
- **Cache TTL assumption: 1h.** Pipeline pacing assumes the Anthropic 1h prompt cache survives between gates / browser round-trips. See README → "Anthropic prompt cache TTL — `ENABLE_PROMPT_CACHING_1H=1`" for plan-level defaults and shell-rc placement.

## Claude Code and Codex

- Claude Code reads `CLAUDE.md`, which imports this canonical `AGENTS.md`; Codex reads `AGENTS.md` and can load `.codex-plugin/plugin.json`.
- Shared skill logic lives in `skills/`. Keep `CLAUDE.md`, `.claude-plugin/*`, `.codex-plugin/*`, `hooks/*.json`, and install guidance as thin host adapters; do not duplicate skill docs or put host-specific behavior in shared skills.
- Public surface parity: both hosts must expose the same three public skills, with equivalent names, descriptions/responsibilities, and default prompts or prompt guidance: `ui-reverse-engineering`, `ui-capture`, `visual-debug`.
- Internal-only skills: additional skills under `skills/` are allowed for maintainer tooling (e.g. `skills/benchmark/`) but MUST NOT appear in `.claude-plugin/plugin.json` `skills`, the Claude marketplace listing, or `.codex-plugin/plugin.json` `defaultPrompt`. `scripts/ci/review.sh`'s `internal_skills` set is the enforcement allowlist — add the directory name there when introducing one.
- Version parity: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, and `.codex-plugin/plugin.json` `version` fields must stay synchronized.
- Hook parity: `hooks/hooks.json` and `hooks/codex-hooks.json` may differ by host manifest shape, matcher vocabulary, status messages, and timeouts, but every command that calls `shim.sh` must call `hooks/shim.sh` and route to the same `ui_clone.hooks.*` modules unless a host lacks that lifecycle event.
- Legitimate host-specific differences are limited to manifest schema shape, hook manifest file (`hooks/hooks.json` vs `hooks/codex-hooks.json`), hook status messages/timeouts, install command details such as `install.sh --codex`, and Claude marketplace metadata.
- Runtime paths must be resolved from environment/plugin roots, not hard-coded local paths. Prefer `PLUGIN_ROOT` / `CODEX_PLUGIN_ROOT` / `CLAUDE_PLUGIN_ROOT` fallbacks in commands and scripts.
- Shared push hook logic lives in `scripts/hooks/pre-push-guard.sh` and `scripts/hooks/post-push-refresh.sh`.

## Review checklist

Full checklist lives in `scripts/ci/review.sh` header (automated). Run `bash scripts/ci/review.sh` before push.
