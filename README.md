<h1 align="center">UI Clone Skills</h1>

<p align="center">
  <strong>Clone how a website moves, not only how it looks.</strong>
</p>

<p align="center">
  <a href="#skills"><img alt="Agent Skills" src="https://img.shields.io/badge/Agent_Skills-3-1FC07C?style=flat-square&amp;labelColor=black"></a>
  <a href="https://claude.com/product/claude-code"><img alt="Claude Code" src="https://img.shields.io/badge/Claude_Code-compatible-D97757?style=flat-square&amp;labelColor=black&amp;logo=anthropic&amp;logoColor=white"></a>
  <a href="https://github.com/openai/codex"><img alt="Codex" src="https://img.shields.io/badge/Codex-compatible-412991?style=flat-square&amp;labelColor=black&amp;logo=openai&amp;logoColor=white"></a>
  <a href="#what-it-recovers"><img alt="Input" src="https://img.shields.io/badge/input-live_URL-2EAD33?style=flat-square&amp;labelColor=black"></a>
  <a href="https://github.com/voidmatcha/ui-clone-skills/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/voidmatcha/ui-clone-skills/ci.yml?branch=main&amp;label=CI&amp;style=flat-square"></a>
  <a href="./LICENSE.txt"><img alt="License" src="https://img.shields.io/github/license/voidmatcha/ui-clone-skills?style=flat-square"></a>
</p>

<p align="center">
  <strong>🇺🇸 English</strong> | <a href="README.ko.md">🇰🇷 한국어</a> | <a href="README.ja.md">🇯🇵 日本語</a> | <a href="README.zh-cn.md">🇨🇳 简体中文</a>
</p>

`ui-clone-skills` turns a live website into an evidence-backed React + Tailwind implementation. It captures the rendered page, downloads the real CSS and assets, reads responsive and computed styles, recovers animation parameters from JavaScript bundles, and verifies the result across viewports and interaction states.

This is motion forensics for the animated web. It is built for pages where a screenshot-to-code model misses what matters: GSAP timelines, Framer Motion springs, Webflow IX2 interactions, Lenis smooth scrolling, Lottie playback, hover states, scroll reveals, sticky sections, and responsive transitions.

| One live URL in | What the pipeline does | What comes out |
| --- | --- | --- |
| **Capture** | Records desktop, tablet, mobile, scroll, hover, click, and transition evidence | Reference frames, videos, DOM and section maps |
| **Decode** | Extracts stylesheets, computed values, assets, fonts, bundles, and motion parameters | `transition-spec.json`, runtime evidence, measured layout data |
| **Recreate** | Builds from observed structure and values instead of inventing a look | React/TSX, Tailwind, preserved CSS, local assets |
| **Verify** | Compares reference and implementation with layout gates, absolute error (AE), structural similarity (SSIM), and motion checks | Reproducible pass/fail evidence and scoped fixes |

## Try it

Install the plugin, then give your coding agent a live URL, a target, and an output directory:

```text
Clone the hero and pricing sections from https://example.com into React + Tailwind.
Preserve the responsive layout, scroll reveals, and hover motion. Output to ./out/.
```

Start with `ui-reverse-engineering`. It detects an existing run, resumes from the last proven pipeline state, and routes capture, extraction, generation, verification, or mismatch diagnosis without throwing away usable evidence.

## Why it is different

Screenshot-to-code tools infer an implementation from pixels in one or more frames. `ui-clone-skills` can inspect the live source of truth behind those pixels, then test whether the recreated page behaves the same way.

| Typical visual generator | `ui-clone-skills` |
| --- | --- |
| Approximates layout from screenshots | Downloads CSS and measures the rendered DOM |
| Guesses easing, duration, and trigger timing | Extracts values from CSS and JavaScript bundles |
| Recreates the visible desktop frame | Captures desktop, tablet, mobile, and scroll positions |
| Treats motion as polish added later | Produces a shared motion specification before implementation |
| Stops when the page builds or looks plausible | Requires rendered, structural, asset, and motion evidence |

The goal is not a plausible imitation. The goal is a clone whose visible assets, DOM structure, responsive behavior, and motion can be compared against the reference.

## How it compares with open-source alternatives

Open-source website recreation tools start from different evidence and stop at different outputs. Choose by the result you need:

| Project | Best fit | Boundary compared with `ui-clone-skills` |
| --- | --- | --- |
| [Screenshot to Code](https://github.com/abi/screenshot-to-code) | Turn screenshots, mockups, Figma designs, or screen recordings into HTML, React, or Vue | Generates from visual input; `ui-clone-skills` starts from a live URL and inspects CSS, bundles, runtime state, and interaction evidence |
| [AI Website Cloner Template](https://github.com/JCodesMore/ai-website-cloner-template) | Build a Next.js clone with computed-style research, interaction sweeps, real assets, and parallel builder agents | The closest overlap in this set; `ui-clone-skills` adds reusable capture, diagnosis, and audit workflows, bundle-derived motion specs, resumable gates, and deterministic visual and motion checks |
| [Open Lovable](https://github.com/firecrawl/open-lovable) | Use a chat application and Firecrawl to recreate a website as a React app | Focuses on the app-generation experience; `ui-clone-skills` focuses on forensic artifacts and measured parity across an agent pipeline |
| [GoClone](https://github.com/goclone-dev/goclone) | Download HTML, CSS, JavaScript, images, and links as a browsable static mirror | Preserves site files for offline browsing; `ui-clone-skills` produces a React + Tailwind implementation and tests responsive and interactive behavior |

Choose `ui-clone-skills` when animation parameters hidden in JavaScript bundles matter, when you need to audit an existing implementation, or when completion must be demonstrated by reproducible gates instead of a build and visual spot check.

<a id="what-it-recovers"></a>

## What it recovers

- **Real visual values:** typography, spacing, colors, borders, transforms, breakpoints, CSS custom properties, and original class names
- **Responsive structure:** viewport-dependent layout, fluid `vw`/`rem` behavior, sticky positioning, grid placement, and mobile reflow
- **Motion parameters:** GSAP and ScrollTrigger timelines, Framer Motion springs, anime.js timing, Webflow IX2 interactions, Lenis and Locomotive scroll settings, CSS keyframes, and Web Animations API state
- **Interactive states:** scroll reveals and scrubs, hover and click transitions, preloaders, page transitions, sliders, tabs, menus, and timed sequences
- **Media and scenes:** images, fonts, video, Lottie, Rive, Spline, canvas, and WebGL references with playback or interaction evidence where available
- **Obfuscated output:** computed-style extraction when Tailwind, CSS Modules, CSS-in-JS, or minified bundles hide authored values

The extraction engine writes shared artifacts, especially `transition-spec.json`, so implementation and verification use the same observed contract instead of independently guessing.

## Verification that can fail

A successful build, HTTP 200, matching page title, or convincing screenshot is not completion. The pipeline checks the rendered result with the evidence appropriate to the page:

- Layout health and DOM/section structure
- Text, font, visible asset, and responsive parity
- Absolute error (AE), SSIM, and section-level visual comparison
- Scroll-end, reveal-trigger, hover, click, and transition-state comparison
- 60 fps frame-by-frame motion comparison for comprehensive verification
- Static coverage of extracted motion entries against implementation hooks

Fast iteration can use `quick` or `standard` verification tiers. The default `comprehensive` tier keeps the full browser and motion sweep.

Routine comparison uses deterministic scripts instead of asking a model to judge every screenshot. Vision is reserved for the final semantic review and scoped diagnosis when metrics alone cannot explain a mismatch.

<a id="skills"></a>

## Skills

| You need to | Use | Owned result |
| --- | --- | --- |
| Recreate a live site or resume a run | **`ui-reverse-engineering`** | An evidence-routed website-to-React pipeline through capture, extraction, generation, and verification |
| Capture reference behavior | **`ui-capture`** | Screenshots and scroll, hover, click, transition, and optional implementation evidence |
| Diagnose why a clone differs | **`visual-debug`** | AE/SSIM, computed-style, structure, and motion findings with concrete fixes |

Use `ui-reverse-engineering` as the default entry point. Call `ui-capture` directly when you only need fresh reference evidence. Call `visual-debug` when reference and implementation artifacts already exist and the task is to explain a mismatch.

Claude Code and Codex expose the same three public skills. The host adapters share the same scripts, gates, artifacts, and hook behavior.

## When to use it

| Your source | Best fit |
| --- | --- |
| A **live URL** with real CSS, assets, responsive behavior, and motion | **`ui-clone-skills`** |
| A **Figma file** | Builder.io, Anima, Plasmic, or another Figma implementation tool |
| A **screenshot only** | A screenshot-to-code tool such as screenshot-to-code or v0 |
| A **text description only** | A design generator such as v0, Lovable, or Bolt |
| A live URL that only needs a **static mirror** | `wget --mirror` or HTTrack |

Do not use it to invent a new design, bypass access controls, or publish a third party's protected design without permission. It works best when the page is reachable in a real browser and the goal is learning, prototyping, internal tooling, or rebuilding a site you are authorized to reproduce.

## Install

Run the installer once. It registers the plugin for every supported host CLI found on your `PATH`:

```bash
tmp=$(mktemp) && curl -LsSf -o "$tmp" https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh && bash "$tmp" && rm -f "$tmp"
```

Use `--claude-only` or `--codex-only` to target one host. Claude Code receives the plugin and lifecycle hooks. Codex receives the three public skills and enables project-local hooks when `ui-reverse-engineering` first runs in a workspace.

See the [installation guide](./README_detail/install.md) for checkout installs, manual dependency setup, host-specific flags, and the skill-only path.

## Requirements

**Tested on:** macOS 14+ (primary) and Ubuntu 22.04+ natively or through WSL2. Windows native is not supported.

| Dependency | Purpose |
| --- | --- |
| `agent-browser` | Browser capture, extraction, and interaction comparison |
| ImageMagick | AE pixel comparison |
| `dssim` | Structural visual similarity |
| `ffmpeg` | Video capture and frame extraction |
| `uv` + Python 3.11+ | Pipeline state, gates, hooks, and metrics |

## How the pipeline works

1. **Capture the reference** at desktop, tablet, mobile, and relevant interaction states.
2. **Extract the page** into DOM, CSS, asset, font, section, bundle, and runtime evidence.
3. **Decode motion** into a source-backed transition specification with triggers and measured parameters.
4. **Generate the implementation** from captured structure and values, preserving source CSS when freehand reconstruction would lose fidelity.
5. **Verify the rendered result** with structural, visual, responsive, and motion gates.
6. **Iterate on measured mismatches** and stop only when the requested completion contract is satisfied or a real blocker is reported.

From a checkout, inspect state with `python -m ui_clone.pipeline live_url component_name session_name status --json` or `node bin/ui-clone pipeline live_url component_name session_name status --json`. npm publishing is paused, so prefer the in-checkout commands unless `ui-clone-cli` is npm-linked to this repository.

## Documentation

The three routing skills stay compact and load 51 focused sub-docs only when a pipeline step needs them. Start with the task-level pages, then open operational contracts when you need exact commands or gate behavior.

- [Installation and host setup](./README_detail/install.md)
- [Full reverse-engineering pipeline](./README_detail/ui-reverse-engineering.md)
- [Reference and transition capture](./README_detail/ui-capture.md)
- [Visual and motion debugging](./README_detail/visual-debug.md)
- [Pipeline hooks, state, and gates](./README_detail/pipeline.md)
- [Agent-readable CLI contract](./docs/agent-cli.md)
- [Token and prompt-cache management](./README_detail/token-management.md)
- [Security model](./README_detail/security.md)
- [Responsible use](./README_detail/responsible-use.md)
- [FAQ and framework support](./README_detail/faq.md)

## Scope

The generated result is production-oriented React + Tailwind code, not an automatic guarantee that a cloned third-party site is licensed or ready for public deployment. Dynamic or protected assets, authentication, anti-bot systems, randomized scenes, and inaccessible source bundles can limit extraction. The pipeline records these gaps rather than silently treating them as matched.

All three skills include eval fixtures following the [Agent Skills](https://agentskills.io/) format. See [CHANGELOG.md](./CHANGELOG.md) for release history.

## License

Apache-2.0. See [LICENSE.txt](./LICENSE.txt).
