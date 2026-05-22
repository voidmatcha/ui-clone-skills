# ui-clone-skills — Clone any website into React + Tailwind with Claude Code or Codex

**ui-clone-skills** is an agent skill for [Claude Code](https://code.claude.com/) and [Codex](https://developers.openai.com/codex/) by [@voidmatcha](https://github.com/voidmatcha) that clones any live website URL into production-ready React + Tailwind code. It extracts real CSS and real animation parameters from JS bundles (GSAP, Framer Motion, Lenis, anime.js), then verifies the result against the original via AE/SSIM pixel diff — no screenshot input, no vision tokens for routine verification.

[![License](https://img.shields.io/github/license/voidmatcha/ui-clone-skills)](./LICENSE.txt)
[![CI](https://img.shields.io/github/actions/workflow/status/voidmatcha/ui-clone-skills/ci.yml?branch=main&label=CI)](https://github.com/voidmatcha/ui-clone-skills/actions/workflows/ci.yml)

Screenshot-to-code and prompt-driven UI tools generate components that *look* like the original at a glance but ship the wrong transitions, wrong scroll behavior, and broken responsive breakpoints — visible parity, hidden divergence. Input is a live URL, not a screenshot or design file. Supports Next.js, Tailwind v4, Webflow IX2, and scroll-driven animations.

- **Uses the original CSS directly** — downloads stylesheets, keeps original class names. No re-implementing from extracted values.
- **Near-zero vision tokens for verification** — AE/SSIM image diff instead of reading screenshots with the LLM. Vision tokens only used in Phase E (final LLM review) when automated checks pass but semantic verification is needed.
- **Extracts real values from JS bundles** — GSAP timelines, Framer Motion springs, anime.js timelines, Lenis scroll params, scroll-driven keyframes. No guessing.
- **Falls back to `getComputedStyle`** when CSS is obfuscated (Tailwind, CSS-in-JS). Auto-detects site type.

### Contents

- [When to use this](#when-to-use-this--decision-tree) · [Design principles](#design-principles) · [Skills](#skills) · [Install](#install-for-claude-code-or-codex) · [Quickstart](#quickstart)
- Skills detail: [`ui-reverse-engineering`](#ui-reverse-engineering--website--react-component) · [`ui-capture`](#ui-capture---visual-capture--reference) · [`visual-debug`](#visual-debug--all-visual-verification-in-one-skill)
- [Token management](#token-management) · [Security](#security) · [Responsible use](#responsible-use) · [FAQ](#faq) · [Changelog](./CHANGELOG.md)

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
- **Progressive-disclosure sub-docs.** Each SKILL.md contains only the pipeline and core rules (~5.9K tokens total across 3 skills). Detailed procedures live in 48 focused sub-docs loaded only when that step runs. Common paths stay lean; specialized paths expand on demand.
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

## Install for Claude Code or Codex

```bash
curl -LsSf https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh | bash
```

The default install registers **both** Claude Code and Codex marketplaces in one pass. Each registration is skipped silently if that host's CLI (`claude` / `codex`) is not on PATH, so the same one-liner works on a Claude-only box, a Codex-only box, or a box with both.

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

<details>
<summary>Install only one host</summary>

```bash
# curl-pipe (flags pass through with -s --)
curl -LsSf https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh | bash -s -- --claude-only
curl -LsSf https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh | bash -s -- --codex-only

# from a local checkout
./install.sh --claude-only       # register Claude marketplace only
./install.sh --codex-only        # register Codex marketplace only
```

</details>

<details>
<summary>Manual / advanced install paths</summary>

```bash
git clone https://github.com/voidmatcha/ui-clone-skills.git
cd ui-clone-skills
./install.sh                    # all flags: ./install.sh --help
./install.sh --no-deps          # skip system deps (already installed)
./install.sh --no-marketplace   # skip all marketplace registrations
./install.sh --claude-only      # Claude marketplace only
./install.sh --codex-only       # Codex marketplace only (alias: --codex)
```

</details>

<details>
<summary>SKILL.md-only copy (no hooks)</summary>

```bash
npx skills add voidmatcha/ui-clone-skills
```

⚠️ `npx skills add voidmatcha/ui-clone-skills` is a no-hooks path only when the receiving host copies the three `SKILL.md` files and does not register bundled hook manifests. In that docs-only mode, the `pre_generate`, `pre_bash`, `section_gate`, and `session_resume` hooks don't run, system deps aren't bootstrapped, and the gate chain can't stop early "done" claims. Use this only when you want the skill docs without enforcement.

</details>

<details>
<summary>Install system deps manually</summary>

```bash
# one-liner (macOS)
brew install imagemagick dssim ffmpeg && npm i -g agent-browser && curl -LsSf https://astral.sh/uv/install.sh | sh

# one-liner (Linux / WSL2)
sudo apt install -y ffmpeg imagemagick && cargo install dssim && npm i -g agent-browser && curl -LsSf https://astral.sh/uv/install.sh | sh

# verify
agent-browser --version && magick --version && dssim --help && ffmpeg -version && uv --version && python3 --version
```

`uv` auto-creates a virtualenv and installs `scikit-image` + `Pillow` on first run — no manual `pip install` needed.

</details>

## Requirements

**Tested on:** macOS 14+ (primary), Ubuntu 22.04+ via WSL2 or native Linux. Windows native is **not supported** — use WSL2.

| Dep | Why |
|---|---|
| `agent-browser` | Browser automation for extraction + comparison |
| `imagemagick` | AE pixel comparison |
| `dssim` | Structural visual similarity (perceptual diff) |
| `ffmpeg` | Video capture + frame extraction |
| `uv` + Python 3.11+ | Gate / hook system (`ui_clone/`) |

### Pipeline hooks (automatic)

Hooks register automatically through the host manifest when supported: `hooks/hooks.json` for Claude Code and `hooks/codex-hooks.json` for Codex. All hooks route through a single `hooks/shim.sh` that fast-skips when no `tmp/ref/` directory exists.

| Hook module | Event | Purpose |
|------|-------|---------|
| `ui_clone.hooks.pre_generate` | `PreToolUse` (Write/Edit) | Blocks component writes until extraction completes. Creates `.ui-re-active` on first passing gate (activation site for the rest of the chain). Demotes state + invalidates `sections/result.txt` on post-`done` component edits |
| `ui_clone.hooks.pre_bash` | `PreToolUse` (Bash) | Two checks. (1) Blocks declaration-of-done bash commands (`git commit`, `git push`, `gh pr create/merge/close`) when verification is incomplete. (2) Blocks Bash redirects/streams that write to component files (`cat > Foo.tsx`, `tee Foo.tsx`, `sed -i ... Foo.tsx`) when extraction is incomplete — symmetrical with the `Edit/Write` gate so shell-redirect bypass is closed. Read-only commands pass through. Bypass: `UI_RE_SKIP_BASH_GATE=1` |
| `ui_clone.hooks.post_verify` | `PostToolUse` (Bash) | Warns on completion signals if verification hasn't run |
| `ui_clone.hooks.devtools_errors` | `PostToolUse` (Bash) | Checks browser devtools for console errors after each Bash call |
| `ui_clone.hooks.section_gate` | `Stop` | Blocks finishing if the current gate hasn't passed. Marker persists past section-compare; `current_gate == "done"` is the canonical complete signal |
| `ui_clone.hooks.session_resume` | `SessionStart`, `PostCompact` | Reinjects the verification checklist into context after a session resume or context compact (empirical: 73% of past verification skips happened within 20 min of a `compact_boundary`). Skipped when state is `done` |

### Goal-driven continuation

`ui-clone-skills` supports Ralph-Wiggum-style continuation through a host-neutral **goal card** instead of an infinite loop. The card is derived from `tmp/ref/<component>/pipeline-state.json` / `PipelineState` and states the mission, current goal, next action, stop condition, and required evidence for the current gate.

```bash
python -m ui_clone.goal <ref-dir>
```

Automatic continuation: the agent drives the loop inside a single Claude Code or Codex session — there is no external scheduler / background daemon. Open the session with the plugin loaded, give it the goal, and let the agent iterate against `python -m ui_clone.goal <ref-dir> --check-done` until that exits 0. `ui_clone/hooks/section_gate.py` (Stop hook) emits gate-specific failure diagnostics so the agent sees exactly which artifact / check is still blocking on every exit attempt.

**Claude Code (recommended):**

```text
claude --plugin-dir "$(pwd)"
> Drive the ui-clone-skills pipeline for tmp/ref/<component>. Each
> iteration: run `python -m ui_clone.goal tmp/ref/<component>`, execute
> exactly the Next action, then re-check with
> `python -m ui_clone.goal tmp/ref/<component> --check-done`. Stop
> only when --check-done exits 0.
```

The `ui-reverse-engineering` skill is auto-loaded so the prompt does not need to re-embed the full pipeline briefing — just declare the ref dir and the stop condition. For unattended headless / CI runs see `ui_clone/benchmark_harness.py` (Python wrapper around `claude --print`).

**Codex (recommended, interactive):** Codex CLI ≥ 0.128.0 ships a native [Goal](https://developers.openai.com/codex/use-cases/follow-goals) feature that drives plan → execute → verify → repeat against the same `python -m ui_clone.goal <ref-dir> --check-done` stop condition — no external loop needed.

```toml
# ~/.codex/config.toml — enable once, restart Codex
[features]
goals = true
plugin_hooks = true
```

In the Codex REPL, run a one-line `/goal` invocation (the `ui-reverse-engineering` skill ships an `AGENTS.md` block that Codex auto-loads, so the goal prompt doesn't re-embed the full pipeline briefing): `/goal Drive the ui-clone-skills pipeline for tmp/ref/<component> until python -m ui_clone.goal tmp/ref/<component> --check-done exits 0. Never declare completion until the exit code is 0.` Use `/goal pause` to narrow scope mid-run, `/goal resume` to continue.

The stop condition is bounded: stop when `current_gate == "done"` and `sections/result.txt` has no `FAIL` or `MISSING impl` lines. SessionStart/PostCompact hooks inject the active goal card, and the Stop gate includes the same card when blocking so the next action is explicit.

### Gate system (Python)

The `ui_clone/` package (Python 3.11+, managed by `uv`) provides pipeline gates, dependency tracking (DAG-based staleness detection), multiscale SSIM comparison, and viewport-relative CSS severity scoring.

```bash
# Gate validation
python -m ui_clone.gate <ref-dir> <gate> [--json]
# Gates: reference | extraction | bundle | paid-features | spec | pre-generate | post-implement | boundary | font-parity | section-compare | all
# Exit:  0=PASS  1=BLOCKED  2=usage error

# Pipeline status
python -m ui_clone.pipeline <url> <component> <session> status [--json]

# Current bounded goal card
python -m ui_clone.goal <ref-dir>

# Loop-exit signal: exit 0 only if current_gate == "done" AND section-compare is clean.
# Use as the while-loop predicate for external drivers (Ralph loop, codex exec, etc.).
python -m ui_clone.goal <ref-dir> --check-done
```

---

## Quickstart

After installing (see [Install for Claude Code or Codex](#install-for-claude-code-or-codex)), give the agent a URL and a target. Use `ui-reverse-engineering` as the default entrypoint for live URL work, uncertain routing, partial runs, failed verification, or completed-state follow-up:

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

---

## `ui-reverse-engineering` — Website → React Component

Turns any live website into a React + Tailwind component. For URL input, extracts real values. Screenshot and video inputs fall back to Claude Vision approximation.

**Usage:**

```
Clone this site: https://example.com
Copy the hero section from https://example.com
Replicate this UI (attach screenshot)
Turn this screen recording into a working component
```

**Pipeline:**

```
0.   Load existing analysis     — re-invoked? load transition-spec.json + bundle-map.json
R.   Capture reference         — static screenshots + scroll video
1.   Open & snapshot           — DOM tree, full-page screenshot. Session reuse for splash sites
W.   Webflow IX2 detection     — MANDATORY if <meta name=generator> contains "Webflow".
                                 Extract hide-rule selector list + IX2 timeline JSON.
                                 ⛔ gate: webflow-detection.json, webflow-hide-rule.json, webflow-ix2.json
2.   Extract structure         — HTML hierarchy, component boundaries, hidden elements
2.5  Extract assets            — CSS files, fonts, images, SVGs, videos, head metadata
2.5b SVG-as-text detection     — find headings rendered as SVG <path> not fonts → svg-text-elements.json
2.6p Dual-snapshot (splash)    — pre/post-splash DOM state → dom-state-diff.json.
                                 Auto-detects splash completion (no hardcoded waits)
2.6  Catalog init styles       — GSAP-baked inline styles, state coupling
3.   Extract styles            — computed CSS, design tokens, em-conversion (viewport-scaled).
                                 Merge runtime-injected transitions from dual-snapshot diff
4.   Detect responsive         — 2-pass viewport sweep + multi-viewport sizing → sizing-expressions.json
5.   Detect interactions       — hover/click/scroll. Extract ALL :hover CSS from live stylesheets
                                 (incl. inline <style>). data-text attribute scan. Hover video recording.
                                 JS hover timing + child cascade
5b.  Capture C3 (deferred)     — interaction/transition videos using selectors from Step 5
5c.  Bundle analysis           — ALL loaded chunks, scroll engine, hover event listeners. ⛔ gate: bundle
5d.  Transition spec           — transition-spec.json + bundle-map.json. ⛔ gate: spec
5e.  Capture verification      — record original, extract frames, verify spec spatial values
6.   Detect animations         — Phase A idle / B scroll (wheel events for smooth scroll) / C per-element
6b.  Assemble extracted.json
6c.  Pre-generation audit      — 6-stage design audit
6d.  Transition coverage       — multi-position scroll measurement → transition-coverage.json.
                                 Samples 10 scroll positions, decodes every transform matrix,
                                 classifies scroll-driven vs enter-reveal vs static. ⛔ gate: pre-generate
                                 (requires transition-coverage.json with animatedElements.length > 0)
7.   Generate component        — CSS-First + body scoping + CSS value diff verification.
                                 SVG-as-text verbatim, RAF parallax for smooth scroll
8.   Visual verification       — scripts/verify/auto-verify.sh. ⛔ gate: post-implement
                                 (checks hover rule count, px fontSize leaks, scroll listeners)
8b.  Section comparison        — skills/visual-debug/scripts/section-compare.sh crops each section independently → AE + structure diff.
                                 MANDATORY — replaces noisy full-page scroll comparison
8c.  Transition comparison     — skills/visual-debug/scripts/transition-compare.sh idle/hover state + timing + computedStyle diff
9.   Interaction verification  — dispatch mouseenter for JS hovers, verify hover-css-rules match
```

**Repo automation scripts** (`scripts/`):

| Script | Purpose |
|---|---|
| `scripts/verify/auto-verify.sh` | Single-command verification: D0 layout health → Phase C scroll AE → post-implement gate |
| `scripts/extract/extract-assets.sh` | Downloads video backgrounds, Typekit fonts, CDN fonts. Extracts video poster frames |
| `scripts/extract/extract-section-html.sh` | Per-section HTML + computed CSS + media element extraction |
| `scripts/extract/download-chunks.sh` | Downloads ALL loaded chunks, detects animation libs, produces skeleton bundle-map.json |
| `scripts/extract/gsap-to-css.sh` | GSAP easing → CSS cubic-bezier (lookup, full table, or bundle scan) |
| `scripts/extract/extract-dynamic-styles.sh` | Classifies GSAP inline styles: layout (keep) vs animation (remove) |
| `scripts/verify/freeze-animations.sh` | Freeze CSS animations, JS timers, canvas, Lottie before screenshot capture |
| `scripts/verify/video-transition-compare.sh` | Video-based transition comparison: records same interaction on orig + impl, extracts frames at 60fps, runs SSIM batch diff |

**Visual comparison scripts** (`skills/visual-debug/scripts/`):

| Script | Purpose |
|---|---|
| `stray-absolute-check.sh` | **Run first (Step 0 Structural)** — single-URL detector for stray `position: absolute` elements with no positioned ancestor (Root Cause H — "footer disappeared" bug class). Often manifests only on shorter viewports |
| `computed-diff.sh` | **Run first** — per-selector `getComputedStyle` diff. Finds fontWeight/display/height root causes before pixel diff. `IGNORE_FONT_SIZE=1` skips fontSize/lineHeight/width/height (use on macOS with 105% system text scaling) |
| `auto-diagnose.sh` | **Second call** — locates which element on the AE diff image is wrong by clustering hotspot pixels and resolving each cluster to the impl element underneath. Detects and hides full-viewport preloader overlays (heuristic: fixed, z-index ≥ 1000, ≥ 80% viewport coverage) before probing. For section-crop diffs, also hides fixed/sticky overlays so the probe sees the section content. Cheaper than `tree-diff.sh` |
| `ae-compare.sh` | Single-pair AE pixel comparison primitive (used by other scripts; can be invoked directly for one-off ref/impl pairs) |
| `batch-scroll.sh` | Captures scroll-position screenshots on both ref and impl at fixed percentages. Auto-detects Lenis / locomotive-scroll / `body { overflow: hidden }` inner-wrapper sites and falls back to `wrapper.scrollTop` + dispatched `scroll` event |
| `tree-diff.sh` | Exhaustive per-element computed-style diff. Walks every visible impl element ≥ MIN_SIZE px, pairs with ref via `elementFromPoint`. Catches mismatches AE misses (wrong font rendering identically, same-box different overrides) |
| `layout-health-check.sh` | D0: section height/total height comparison before pixel-level diff |
| `layout-diff.sh` | Structural section bounding-box comparison between two URLs |
| `layout-tree-diff.sh` | Geometry diff via signature-based pairing (text + tag + class hash + size class). Reports top/left/w/h deltas regardless of where elements moved. Catches "right element, wrong position" bugs |
| `batch-compare.sh` | Batch AE comparison with dynamic-region threshold support |
| `dssim-compare.sh` | Structural visual similarity (DSSIM) — catches layout issues AE misses |
| `section-compare.sh` | Section-level visual + structural comparison (lazy pre-scroll for IntersectionObserver content, text fingerprint matching, per-section AE diff, DOM structure diff). Inner-scroll-container detection for Lenis/locomotive sites. `NO_CANVAS=1` opt-in to hide `<canvas>` elements (WebGL/Three.js dynamic content drowns out structural diffs) |
| `reveal-trigger-check.sh` | **Run before transition-compare** — runtime gate for the "stuck reveal" bug class. Enumerates initially-hidden elements (opacity 0 / non-identity transform), scrolls each into view, fails any whose style never advances. Reports the parent-chain `overflow: hidden` ancestor that's most likely clipping the IntersectionObserver |
| `transition-spec-coverage.sh` | **Static gate for spec-vs-impl coverage** — parses `transition-spec.json`, greps the impl source for each entry's id / selector / type-derived hooks (RevealRise, useScrollTrigger, useScroll, etc.), FAILs if any entry has zero hits. Catches the failure class where hover transitions match while intersection/scroll-driven entries were never wired |
| `transition-compare.sh` | Hover/transition behavior comparison (idle/hover state capture, computedStyle diff, timing validation). `EXCLUDE_SELECTORS` env var to skip third-party SDK overlays (default: cookie/consent banners). `NO_CANVAS=1` opt-in to hide `<canvas>` elements during capture |
| `hover-tree-diff.sh` | Per-element hover/transition diff. Captures idle → CDP `:hover` → settled style. Diffs timing (property/duration/easing/delay) + idle→hover delta. Uses CDP-level `:hover` (synthetic events do not fire `:hover`) |
| `keyframes-diff.sh` | `@keyframes` declaration diff. Extracts keyframe rules from both pages; reports keyframes only on one side or same-name rules with different steps. Catches missing entrance animations and wrong timing curves baked into keyframes |

Visual-debug scripts that open browser sessions support `VIEW_W`/`VIEW_H` env vars (default 1440x900) for custom viewport sizes.

**Input modes:**

| Mode | Quality | When to use |
|---|---|---|
| URL (primary) | Exact values | Live site — `getComputedStyle`, real DOM, JS bundle |
| Screenshot | Approximation (Claude Vision) | Design mockup, inaccessible site |
| Video / recording | Approximation (Claude Vision) | Interactions visible in recording |
| Multiple screenshots | Approximation (Claude Vision) | Different pages or breakpoints |

---

## `ui-capture` - Visual Capture & Reference

Captures baseline screenshots and transition videos. With a local URL, it captures matching implementation evidence for downstream verification, then hands mismatch diagnosis to `visual-debug`.

Classifies each effect by trigger type before recording — prevents blank videos from wrong activation methods.

**Usage:**

```
Capture the transitions from https://example.com
Record the hover effects on https://example.com
Capture matching implementation evidence for https://example.com and http://localhost:3000
Take a baseline of https://example.com before I start cloning
```

**Pipeline:**

```
Phase 1:  Full page capture        — section screenshots + full scroll video
                                     auto-detects custom scroll (Lenis, Locomotive)
Phase 2:  Transition detection     — classify all effects by trigger type → regions.json
Phase 2B-2E: Capture per trigger type:
  2B scroll-driven   — exploration video → clip screenshot before/mid/after
  2C css-hover       — eval + clip screenshot: idle + active
     js-class        — eval classList.add + clip screenshot: idle + active
     intersection    — eval classList.add + clip screenshot: before + after
  2D mousemove       — raster-path sweep video
  2E auto-timer      — passive recording for 2-3 cycles

local-url provided?
├── YES → Phase 3: Implementation evidence capture
│         Phase 4A: Handoff to `visual-debug` Phase D pixel-perfect gate
│         Phase 4B: compare.html human review artifact
│         Phase 5:  User review
└── NO  → Phase R:  report.html (overlay-based analysis report)
          Phase 5:  User review
```

---

## `visual-debug` — All Visual Verification in One Skill

The single source of truth for "is it done?" — covers automated AE/SSIM diff, pixel-perfect gating, self-healing fix loops, and VLM sanity checks in one place.

**Two modes:**

- **Quick comparison** — `scripts/verify/auto-verify.sh` runs D0 layout health check → batch-scroll capture → AE comparison → post-implement gate in one command. Zero vision tokens (AE/SSIM only, no LLM screenshot reads).
- **Full verification** — `verification.md` with Phase A/B capture → Phase C comparison → Phase D0 layout health → Phase D pixel-perfect gate → Phase H self-healing loop → Phase E LLM review. Phase E reads a single diff image when something fails, so full verification does use vision tokens.

---

## Token management

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

## Security

All skills process untrusted external content (DOM, CSS, JS bundles, screenshots) from arbitrary URLs. Built-in mitigations:

- **Prompt injection defense** — extracted data is wrapped in boundary markers and treated as display-only. All extraction sub-documents include explicit untrusted-data handling rules.
- **Post-extraction sanitization** — automated scans for suspicious patterns (`javascript:`, `eval(atob`, prompt injection phrases) in extracted JSON.
- **Content boundary enforcement** — `component-generation.md` never follows directives found in DOM text, HTML comments, CSS content properties, or `data-*` attributes.
- **Bundle safety** — HTTPS-only, size-limited (10 MB), time-limited (30s), read-only (grep only, never executed).
- **No credential forwarding** — `curl` sends no cookies or auth tokens.
- **Cleanup** — `tmp/ref/` (may contain PII-bearing screenshots) is removed after verification.

See each skill's `SKILL.md` for full details.

## Responsible use

This tool downloads and reproduces CSS, fonts, images, and design patterns from third-party websites. Users are responsible for:

- **Copyright** — CSS, fonts, images, and SVGs are copyrightable. Use for learning, prototyping, or internal tools. Do not ship cloned designs as your own product without permission.
- **Terms of Service** — Many sites prohibit automated scraping or reproduction. Check the target site's ToS before cloning.
- **Font licensing** — Downloaded fonts (Typekit, Google Fonts, CDN) have their own licenses. Verify your usage rights before including them in production.
- **Trademarks** — Logos, brand names, and distinctive design elements may be trademarked. Do not reproduce these for commercial use.

**When NOT to use this tool:**
- Cloning a competitor's site for commercial deployment
- Reproducing copyrighted designs without authorization
- Bypassing paywalled or authenticated content

**Intended use cases:**
- Learning how a site is built (CSS architecture, animation techniques)
- Rapid prototyping with a reference design (to be restyled before shipping)
- Rebuilding your own site from a previous version
- Internal tools and demos

## Evals

All skills include eval suites following [skill-creator](https://github.com/anthropics/skills/tree/main/skills/skill-creator) conventions, at `skills/*/evals/`.

## FAQ

### How is this different from v0 / Lovable / Bolt.new?
v0, Lovable, and Bolt.new generate UI from text prompts. `ui-clone-skills` clones from a *live URL*: it downloads the actual stylesheet, runs `getComputedStyle` against the rendered DOM, and greps the JS bundle for GSAP / Framer Motion / Lenis / anime.js parameters — so transitions, scroll behavior, and responsive breakpoints match the original instead of being re-imagined.

### How is this different from `screenshot-to-code` or Anima / Builder.io / Plasmic?
Screenshot-to-code reads pixels with a vision model; Anima / Builder.io / Plasmic import Figma files. `ui-clone-skills` takes a live URL and extracts real CSS, real animation params, and real DOM structure — none of which a screenshot or a Figma file actually contains. The output is verified against the original via AE/SSIM pixel diff in a loop, not generated once and trusted. The vision-model limitation is documented by [Design2Code (NAACL 2025)](https://aclanthology.org/2025.naacl-long.199/): even GPT-4V output was rated acceptable to replace the original in only ~49% of cases on a human-evaluated benchmark.

### Does it work with Webflow / Framer / GSAP-built sites?
Yes — `ui-clone-skills` supports Webflow, Framer, and GSAP-built sites end-to-end. Webflow IX2 timelines, GSAP / ScrollTrigger / SplitText / DrawSVG, Framer Motion springs + scroll handlers, anime.js timelines, and Lenis / Locomotive smooth-scroll parameters are extracted from the live JS bundle into `transition-spec.json` and reproduced in the React + Tailwind output. Webflow premium plugins fall back to OSS equivalents (SplitText → `splitting`, ScrollSmoother → `lenis`, DrawSVG → `stroke-dashoffset`).

### Does it support Next.js and Tailwind v4?
Yes — `ui-clone-skills` supports both Next.js and Tailwind v4. Output is framework-agnostic JSX/TSX usable from any React host; [Tailwind v4](https://tailwindcss.com/blog/tailwindcss-v4)'s individual `translate:` / `rotate:` / `scale:` properties are detected and reconciled against legacy `transform:` shorthand to prevent the doubled-translate bug class (per the [MDN `translate` spec](https://developer.mozilla.org/en-US/docs/Web/CSS/translate), individual transform properties compose *before* the `transform` shorthand, so the same translation can be applied twice if both are emitted). Hydration mismatches in [Next.js](https://nextjs.org/docs/messages/react-hydration-error) / React Server Components / SolidStart / [Astro Islands](https://docs.astro.build/en/concepts/islands/) are caught by a dedicated `hydration-check.sh` gate.

### Does it need OpenAI API or anything besides Claude / Codex?
No — `ui-clone-skills` doesn't need an OpenAI API or any third-party LLM beyond Claude or Codex. System dependencies are local CLI tools (`agent-browser`, `imagemagick`, `dssim`, `ffmpeg`, `uv` + Python 3.11) — see [Requirements](#requirements). The same skills run unchanged on Claude Code or Codex from a single `install.sh` one-liner.

### How does the "no vision tokens" claim work — when *do* you use vision tokens?
Routine verification uses CLI tools — [ImageMagick AE (Absolute Error)](https://usage.imagemagick.org/compare/) for pixel diff and `dssim` for [SSIM-based](https://ece.uwaterloo.ca/~z70wang/research/ssim/) structural similarity (the original Wang/Bovik 2004 metric, [IEEE TIP](https://ieeexplore.ieee.org/document/1284395/)) — and never sends screenshots to the LLM. Vision tokens are used in exactly two places: (1) reading a single diff image on AE/SSIM failure to diagnose what's wrong, (2) Phase E final semantic review (~44K tokens, mandatory before declaring done). All other comparison is image-bytes-to-CLI, not image-bytes-to-LLM.

### Is the output production-ready?
Production-ready as React + Tailwind code (typed JSX, original class names, real animation values), not as a production *site*. Cloned designs are subject to copyright, trademark, and font licensing — see [Responsible use](#responsible-use). Intended cases are learning, prototyping, internal tools, and rebuilding your own previous site; commercial deployment of cloned third-party designs is not.

### What happens when extraction fails or the site uses paid fonts / DRM canvas?
The pipeline records an `unclonable_reasons` entry, surfaces an `ABORT` banner in the goal card, and exits with code `2` from `python -m ui_clone.goal --check-done` — distinct from code `1` (not-yet-done). External loop drivers (Ralph loop, `codex exec`) recognize the difference and stop instead of grinding to max-iterations.

### Why does `ENABLE_PROMPT_CACHING_1H` matter for long cloning sessions?
The pipeline re-sends the same SKILL.md + extraction context across many turns separated by browser navigation, gate calls, and AE/SSIM comparisons. Anthropic's [prompt caching docs](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching) document a default 5-minute cache TTL with an opt-in 1-hour TTL via `cache_control: { "ttl": "1h" }` (priced at 2× write / 0.1× read vs base tokens). A typical gate or browser round-trip exceeds 5 min, so the default TTL evicts the cache between turns and re-bills the full prompt on the next message. Setting `ENABLE_PROMPT_CACHING_1H=1` opts the Claude Code process into the 1h TTL on Team / API-key plans; Enterprise/Pro/Max apply 1h server-side.

## Changelog

See [CHANGELOG.md](./CHANGELOG.md).

## License

Apache-2.0. See [LICENSE.txt](./LICENSE.txt).
