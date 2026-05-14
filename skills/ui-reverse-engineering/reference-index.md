# Reference index

Look up which sub-doc owns a topic. Read SKILL.md first for pipeline flow; read this file when SKILL.md tells you to consult "the sub-doc for step X" and you need to resolve the filename.

## Cross-cutting (read on signal, not by step)

| File | When |
|---|---|
| `agent-environment-rules.md` | **Read once per session** — viewport ordering, zsh word-split, monorepo paths, agent-browser CLI verbs, flat `tmp/ref/<c>/` layout |
| `skip-zones.md` | **Read when gate fails** — 5 zones of commonly skipped steps with per-zone gate checks |
| `diagnosis.md` | **Read when visual mismatch** — Root Cause A–J with diagnosis commands + fix patterns |
| `no-judgment.md` | **Read when "looks right to me"** — decision framework for measurement vs assumption |
| `operational-rules.md` | **Read when request shape matches** — "adding pages to an existing project", "Tailwind class collides with legacy bundle selector", per-request scope adjustments ("clone the hero" / "replicate this card" / "clone the modal") |

## Pipeline sub-docs (ordered by step)

| File | Step | Role |
|---|---|---|
| `site-detection.md` | 1 | Auto-detect stack; pick CSS-First vs Extract-Values |
| `dom-extraction.md` | 1–2 | DOM hierarchy, semantic section enumeration, hidden element extraction |
| `dom-splash-snapshot.md` | 2.6-pre | Dual-snapshot DOM diff for sites with splash/preloader (called from dom-extraction.md when site has timed overlay) |
| `asset-extraction.md` | 2.5 | CSS files, fonts, images, SVGs, videos, head metadata |
| `style-extraction.md` | 3 | Computed styles, design tokens, em-conversion gate |
| `responsive-detection.md` | 4 | Viewport sweep, Step 4-C2 multi-viewport sizing |
| `interaction-detection.md` | 5 | Hover/scroll/click detection, JS timing, hover CSS rules |
| `hover-timing-extraction.md` | 5d-3 | JS-driven hover timing via `getAnimations()` + bundle-grep fallback (called from interaction-detection.md when CSS hover timing is unresolved) |
| `bundle-analysis.md` | 5c-a | JS bundle download, grep, scroll engine detection |
| `bundle-verification.md` | 5c-b | Numerical comparison for auto-rotating / scroll-driven / timer animations |
| `transition-spec-rules.md` | 5d | Transition spec JSON schema and validation |
| `verification-plan.md` | 5d | `verification-plan.json` + `known-artifacts.json` schema — signal-derived required-checks manifest for downstream gates |
| `animation-detection.md` | 6 | Idle/scroll/per-element animation phases |
| `section-audit.md` | 6c | Six-stage audit: element ownership via parentElement chain |
| `transition-coverage.md` | 6d | Multi-position scroll measurement → transition-coverage.json |
| `component-generation.md` | 7 | Generation entry, parallel worktree, verification gates |
| `css-first-generation.md` | 7 | CSS-first assembly strategy for sites with downloadable CSS |
| `generation-pitfalls.md` | 7 | Common implementation errors to avoid |
| `transition-implementation.md` | 7 | Bundle → code translation |
| `gsap-alternatives.md` | 7 | GSAP plugin alternatives for dependency-choice cases (SplitText / MorphSVG / ScrollSmoother / DrawSVG). Read when `transition-spec.json` lists GSAP plugins but the implementation should avoid a GSAP dependency or match a project-native animation stack. |
| `post-gen-verification.md` | 7 | Output validation after component generation |
| `style-audit.md` | 7 | Design token consistency validation |
| `webflow-ix2.md` | W | Webflow IX2 detection + hide-rule extraction + IX2 timeline JSON |

## Transition sub-pipeline (T-* steps)

| File | Step | Role |
|---|---|---|
| `measurement.md` | T-1 | Multi-point animation measurement (11 data points) |
| `element-capture.md` | T0 | Frame extraction protocols |
| `css-extraction.md` | T2a | Pure CSS transition extraction |
| `js-animation-extraction.md` | T2b | GSAP/RAF/scroll-driven JS extraction |
| `canvas-webgl-extraction.md` | T2c | Canvas/Three.js/Rive/Spline/Lottie handling |
| `patterns.md` | T3 | Common transition patterns (CSS/JS) |

## Edge protocols

| File | Trigger | Role |
|---|---|---|
| `splash-extraction.md` | Step 5c-a preloader signal OR Step 6A Tier 1 AE shows changes in first 1–3s | Preloader overlay handling sub-protocol |
| `dynamic-content-protocol.md` | Capture exhibits non-determinism | Handling dynamic/animated UIs during capture |
| `asset-substitution.md` | Impl uses different font/asset than ref by design (license, availability) | Declares substitutions so section-compare switches affected sections to structural-only diff |

## Cross-skill (visual-debug)

| File | Step | Role |
|---|---|---|
| `../visual-debug/verification.md` | 8 | Phase A/B capture + Phase D pixel-perfect gate |
| `../visual-debug/comparison-fix.md` | 8 | Phase C comparison + Phase E LLM review + Phase H self-healing |
| `../visual-debug/scripts/section-compare.sh` | 8b | Section-level crop + AE + structure diff. **Always pass `"$(pwd)/tmp/ref/<component>"` as the 4th arg** — Stop gate reads result.txt from that path |
| `../visual-debug/scripts/transition-compare.sh` | 8c | Idle/hover state comparison + timing diff |
