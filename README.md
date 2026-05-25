# ui-clone-skills — Motion forensics for the animated web

**ui-clone-skills** is motion forensics for the animated web: an agent skill for [Claude Code](https://code.claude.com/) and [Codex](https://developers.openai.com/codex/) by [@voidmatcha](https://github.com/voidmatcha) that investigates a live URL the way a forensic analyst investigates a binary — extracts real CSS and real animation parameters from JS bundles (GSAP, Framer Motion, Lenis, anime.js), then either explains them, emits production React + Tailwind code, or scores an existing impl against the original via AE/SSIM pixel diff. No screenshot input, no vision tokens for routine verification.

### Four jobs against a live URL

| Command | Job |
|---|---|
| **`decode <url>`** | Analyse motion + build: which animation library, which scroll engine, what timings. Educational — no code emitted. |
| **`clone <url>`** | Generate React + Tailwind components against the captured DOM + extracted animation params. |
| **`verify <url> <impl>`** | Score an existing impl against the ref with AE/SSIM + motion-parity. Third-party clone audit. |
| **`extract <url>`** | Raw JSON dump (structure, styles, animations, bundles) for downstream tooling. |

[![License](https://img.shields.io/github/license/voidmatcha/ui-clone-skills)](./LICENSE.txt)
[![CI](https://img.shields.io/github/actions/workflow/status/voidmatcha/ui-clone-skills/ci.yml?branch=main&label=CI)](https://github.com/voidmatcha/ui-clone-skills/actions/workflows/ci.yml)

Screenshot-to-code and prompt-driven UI tools generate components that *look* like the original at a glance but ship the wrong transitions, wrong scroll behavior, and broken responsive breakpoints — visible parity, hidden divergence. Input is a live URL, not a screenshot or design file. Supports Next.js, Tailwind v4, Webflow IX2, and scroll-driven animations.

- **Uses the original CSS directly** — downloads stylesheets, keeps original class names. No re-implementing from extracted values.
- **Near-zero vision tokens for verification** — AE/SSIM image diff instead of reading screenshots with the LLM. Vision tokens only used in Phase E (final LLM review) when automated checks pass but semantic verification is needed.
- **Extracts real values from JS bundles** — GSAP timelines, Framer Motion springs, anime.js timelines, Lenis scroll params, scroll-driven keyframes. No guessing.
- **Falls back to `getComputedStyle`** when CSS is obfuscated (Tailwind, CSS-in-JS). Auto-detects site type.

### Contents

- [When to use this](#when-to-use-this--decision-tree) · [Design principles](#design-principles) · [Skills](#skills) · [Install](#install) · [Requirements](#requirements) · [Quickstart](#quickstart)
- Deep dives: [`ui-reverse-engineering`](./README_detail/ui-reverse-engineering.md) · [`ui-capture`](./README_detail/ui-capture.md) · [`visual-debug`](./README_detail/visual-debug.md)
- Operations: [Pipeline hooks, goal card, gates](./README_detail/pipeline.md) · [Token management](./README_detail/token-management.md) · [Security](./README_detail/security.md) · [Responsible use](./README_detail/responsible-use.md) · [FAQ](./README_detail/faq.md) · [Changelog](./CHANGELOG.md)

## When to use this — decision tree

Different inputs need different tools. Pick by what you have:

| What you have | Use |
|---|---|
| **A live URL** and want pixel-faithful React + Tailwind (real CSS, real animation params, scroll/hover behavior) | `ui-clone-skills` ← you are here |
| A **Figma file** | [Builder.io](https://www.builder.io/) / [Anima](https://www.animaapp.com/) / [Plasmic](https://www.plasmic.app/) |
| A **screenshot** (no source available) | [screenshot-to-code](https://github.com/abi/screenshot-to-code) / [v0](https://v0.dev/) |
| A **text description** (no reference) | [Claude Code](https://code.claude.com/) / [v0](https://v0.dev/) / [Lovable](https://lovable.dev/) / [Bolt.new](https://bolt.new/) |
| A live URL and just want **static HTML mirror** (no React) | `wget --mirror` / [HTTrack](https://www.httrack.com/) |

> **Why this exists:** prompt-/screenshot-driven tools approximate what's visible. `ui-clone-skills` downloads the actual stylesheet, runs `getComputedStyle` against the rendered DOM, greps the JS bundle for GSAP/Framer Motion/anime.js/Lenis parameters, and verifies the result against the original via AE/SSIM — so the output matches transitions and responsive behavior, not just the static layout.

**When NOT to use:** general "build me a UI from scratch" tasks (use v0/Lovable or Claude Design), Figma-driven workflows (use Builder/Anima), one-off CSS help (just ask Claude directly).

## Design principles

These are the decisions that shape how the plugin is structured. They aim to keep agent sessions focused and bounded.

- **Real values, not guesses.** Every number — font-size, easing curve, scroll offset, stagger delay — comes from `getComputedStyle`, raw CSS, or a JS bundle grep. The plugin refuses to ship approximations.
- **Near-zero vision tokens for comparison.** AE and SSIM CLI tools handle pixel diff — the LLM never reads ref vs impl screenshots side-by-side. Vision tokens are only used when: (1) reading a single diff image on AE/SSIM failure, (2) Phase E final semantic review (~44K tokens, mandatory).
- **Progressive-disclosure sub-docs.** Each SKILL.md contains only the pipeline and core rules (~5.9K tokens total across 3 skills). Detailed procedures live in 49 focused sub-docs loaded only when that step runs. Common paths stay lean; specialized paths expand on demand.
- **Single source of truth for transitions.** `transition-spec.json` is produced once from bundle analysis. Implementation reads the spec, never re-greps the bundle — avoiding wasted work and the risk of picking the wrong conditional branch.
- **Automation over introspection.** Python gates (`python -m ui_clone.gate`, `python -m ui_clone.pipeline`, `scripts/verify/auto-verify.sh`) decide whether a step is complete. Agents don't self-certify "looks good enough."
- **No judgment, data only.** Every decision must be backed by extracted data, captured screenshots, or script output. "Probably", "close enough", and "just a content difference" are forbidden — each has a documented failure case.

## Skills

| User intent | Skill | Owned responsibility | Non-goal | Handoff/next action |
|---|---|---|---|---|
| Build/route | **`ui-reverse-engineering`** | Run the website-to-React pipeline and route the next phase from pipeline status. | Not a standalone capture utility or mismatch diagnosis tool. | Calls `/ui-capture` for reference artifacts; uses `visual-debug` for visual verification. |
| Capture/reference | **`ui-capture`** | Capture reference screenshots, scroll/transition evidence, and optional implementation clips for the caller. | Not the primary post-implementation mismatch diagnosis tool. | Handoff failing diffs or mismatch investigation to `visual-debug`. |
| Diagnose mismatch | **`visual-debug`** | Compare original vs implementation, run AE/SSIM/computed-style diagnosis, and identify fixes. | Not the build pipeline or baseline capture owner. | Return concrete findings/fixes to `ui-reverse-engineering` or the caller. |

Start with `ui-reverse-engineering` when the request begins with a live URL, when you're unsure which skill fits, or when a run is partial, failed, or already complete. It checks the current state first, then routes to capture, generation, verification, or mismatch diagnosis without discarding usable artifacts.

Call `ui-capture` directly only when you need fresh reference evidence. Call `visual-debug` directly only when reference and implementation evidence already exist and the task is to diagnose a mismatch.

The public surface stays small: Claude Code and Codex expose the same three skills from shared `skills/`; each host adapter points back to the same core scripts, gates, and hooks.

## Install

```bash
curl -LsSf https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh | bash
```

The default install registers **both** Claude Code and Codex marketplaces in one pass; each registration is skipped silently if that host's CLI is not on PATH. Inside Claude Code: `/plugin install ui-clone-skills@voidmatcha`. For Codex: launch with `codex --enable plugin_hooks` after the installer runs.

For one-host installs, the manual git-clone path, the SKILL.md-only no-hooks copy, and the manual system-deps recipe, see [`README_detail/install.md`](./README_detail/install.md).

## Requirements

**Tested on:** macOS 14+ (primary), Ubuntu 22.04+ via WSL2 or native Linux. Windows native is **not supported** — use WSL2.

| Dep | Why |
|---|---|
| `agent-browser` | Browser automation for extraction + comparison |
| `imagemagick` | AE pixel comparison |
| `dssim` | Structural visual similarity (perceptual diff) |
| `ffmpeg` | Video capture + frame extraction |
| `uv` + Python 3.11+ | Gate / hook system (`ui_clone/`) |

Pipeline hooks register automatically through `hooks/hooks.json` (Claude Code) and `hooks/codex-hooks.json` (Codex). For the full hook table, the goal-driven continuation pattern, and the gate-system CLI, see [`README_detail/pipeline.md`](./README_detail/pipeline.md).

## Quickstart

After installing, give the agent a URL and a target. Use `ui-reverse-engineering` as the default entrypoint for live URL work, uncertain routing, partial runs, failed verification, or completed-state follow-up:

```
Clone the hero section from https://stripe.com/payments into React + Tailwind. Output to ./out/
```

The pipeline runs automatically. `python -m ui_clone.pipeline` detects the current phase and prints the next action; you don't invoke phases manually.

**What happens:**

1. Reference capture → `tmp/ref/payments-hero/{full,desktop,tablet,mobile}.png` + scroll video
2. DOM/CSS/JS extraction → `tmp/ref/payments-hero/{structure,styles,assets}.json` + `transition-spec.json`
3. Component generation → `./out/PaymentsHero.tsx` (CSS-first, original class names)
4. Visual verification → `scripts/verify/auto-verify.sh` → D0 layout health + AE/SSIM diff

If verification fails, the pipeline iterates up to 3 rounds (Phase H self-healing loop) before asking for human review.

**Hooks are already registered** on install through the host manifest. Both Claude Code and Codex route through `hooks/shim.sh`, so premature write blocks and unverified completion warnings stay shared. In Codex, also confirm `features.plugin_hooks = true` or launch with `codex --enable plugin_hooks`; without that, validation runs are docs-only.

## Skill deep dives

Each skill ships with a dedicated detail page documenting its pipeline phases, automation scripts, and input modes:

- [`ui-reverse-engineering`](./README_detail/ui-reverse-engineering.md) — full website-to-React pipeline (Phase 0 → 9), repo automation scripts, visual-debug script reference, input modes
- [`ui-capture`](./README_detail/ui-capture.md) — baseline screenshots, per-trigger transition capture, optional implementation evidence handoff
- [`visual-debug`](./README_detail/visual-debug.md) — quick comparison vs full verification, Phase E semantic review

## Operations

- [Pipeline hooks, goal card, gates](./README_detail/pipeline.md) — what every hook does, how the goal card drives continuation, gate CLI reference
- [Token management](./README_detail/token-management.md) — built-in mitigations, `ENABLE_PROMPT_CACHING_1H=1` setup per shell + plan, `rtk` integration
- [Security](./README_detail/security.md) — prompt-injection defense, post-extraction sanitization, content-boundary enforcement
- [Responsible use](./README_detail/responsible-use.md) — copyright, ToS, font licensing, trademarks; intended vs disallowed use cases
- [FAQ](./README_detail/faq.md) — comparisons with v0/Lovable/screenshot-to-code/Anima, framework support, vision-token policy, error/abort behavior

## Evals

All skills include eval suites following [skill-creator](https://github.com/anthropics/skills/tree/main/skills/skill-creator) conventions, at `skills/*/evals/`.

## Changelog

See [CHANGELOG.md](./CHANGELOG.md).

## License

Apache-2.0. See [LICENSE.txt](./LICENSE.txt).
