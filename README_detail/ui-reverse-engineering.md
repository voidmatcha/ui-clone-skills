# `ui-reverse-engineering` — Website → React Component

Turns any live website into a React + Tailwind component. For URL input, extracts real values. Screenshot and video inputs fall back to host vision-model approximation.

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
| Screenshot | Approximation (host vision model) | Design mockup, inaccessible site |
| Video / recording | Approximation (host vision model) | Interactions visible in recording |
| Multiple screenshots | Approximation (host vision model) | Different pages or breakpoints |
