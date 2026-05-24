---
name: visual-debug
description: >-
  Post-implementation visual mismatch diagnosis with AE/SSIM diff —
  near-zero vision tokens. Triggers on "doesn't match", "compare with
  original", "diff against ref", "verify the clone". Uses
  auto-diagnose to find mismatched elements from diff images without
  reading them. Routed to from ui-reverse-engineering on gate fail.
metadata:
  filePattern:
    - "**/tmp/ref/**/static/**"
    - "**/tmp/ref/**/frames/**"
    - "**/tmp/ref/**/diff/**"
    - "**/side-by-side/**"
  bashPattern:
    - "compare.*metric"
    - "ffmpeg.*ssim"
    - "ae-compare"
    - "batch-compare"
    - "batch-scroll"
    - "computed-diff"
    - "auto-diagnose"
  priority: 90
---

# Visual Debug

Automated post-implementation visual comparison — original vs implementation. **Zero vision tokens** via AE/SSIM CLI tools.

**Primary trigger:** Diagnose post-implementation mismatch between a reference and an implementation, then provide concrete repair guidance.
**Non-goals:** Do not use this to build or regenerate the React component, orchestrate a full live URL clone, or perform baseline/reference capture; route build/clone work to `ui-reverse-engineering` and capture/reference work to `ui-capture`.

## Boundary and handoff

- **Direct invocation:** Use when reference/implementation evidence already exists and the user asks for mismatch, diff, diagnosis, comparison, or repair guidance.
- **Routed invocation:** `ui-reverse-engineering` may route here after a failed visual diff, failed post-implementation gate, or completed-state mismatch request.
- **Missing evidence:** If baseline/reference capture is missing, return to `ui-capture` first; if implementation or regeneration is needed, return to `ui-reverse-engineering` or the active caller pipeline.
- **Return contract:** Send the caller concrete findings: failing artifact, mismatched selector/region, likely root cause, recommended fix, and verification command. `visual-debug` diagnoses and guides; the caller owns implementation, build, regeneration, and full clone orchestration.

## When to use

- After implementing a section, before declaring "done"
- When user says "it's different", "doesn't match"
- After `ui-reverse-engineering` reports a failed visual diff or post-implementation gate
- **Instead of** `Read`-ing screenshots for comparison

**HARD RULE:** Never `Read` ref/impl images for comparison. For FAIL positions, use `auto-diagnose.sh` first (zero vision tokens). Only `Read` diff images as fallback if auto-diagnose finds nothing. Exception: Phase E reads ref+impl pairs, and Phase E **must run in a delegated subagent context** so its 44K vision tokens stay out of the main context (see Phase E section).

**Browser cleanup rule (MANDATORY at end of every run):** `agent-browser --session <name> close` for each session you opened. **Never** `close --all` because other agent-browser sessions may own active browsers. The detailed section near the end of this file may be clipped after auto-compaction; this one-liner is the survival copy.

## Token rule

Pipe large `eval` output to a file, then `Read` only what you need:
```bash
agent-browser --session <s> eval "<script>" > tmp/ref/<name>.json
```
Never let large JSON print to stdout — it wastes tokens.

## Dependencies — preflight (run once per session)

`npx skills add` installs the SKILL files but skips system tooling. Run this check at session start; if anything is missing, halt and surface the bootstrap one-liner to the user (do **not** auto-execute `curl | bash` on their behalf).

```bash
miss=""
for c in agent-browser ffmpeg dssim; do command -v "$c" >/dev/null 2>&1 || miss+=" $c"; done
{ command -v magick >/dev/null 2>&1 || command -v convert >/dev/null 2>&1; } || miss+=" imagemagick"
if [ -n "$miss" ]; then
  printf 'Missing system deps:%s\n\nFastest fix:\n  curl -LsSf https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh | bash\n\nOr install manually:\n  brew install ffmpeg imagemagick dssim   # macOS  (Linux: apt install ffmpeg imagemagick && cargo install dssim)\n  npm i -g agent-browser\n' "$miss"
  exit 1
fi
```

## Scripts

```bash
SCRIPTS_DIR="${VISUAL_DEBUG_SCRIPTS_DIR:-}"
if [ -z "$SCRIPTS_DIR" ]; then
  for root in "${PLUGIN_ROOT:-}" "${CODEX_PLUGIN_ROOT:-}" "${CLAUDE_PLUGIN_ROOT:-}" "${UI_CLONE_ROOT:-}" "$PWD" "$PWD/.." "$PWD/../.." "${INSTALL_DIR:-$HOME/.local/share/ui-clone-skills}"; do
    [ -n "$root" ] && [ -f "$root/skills/visual-debug/scripts/ae-compare.sh" ] && SCRIPTS_DIR=$(cd "$root/skills/visual-debug/scripts" && pwd) && break
  done
fi
[ -n "$SCRIPTS_DIR" ] || { echo "Set VISUAL_DEBUG_SCRIPTS_DIR or PLUGIN_ROOT" >&2; exit 1; }
```

| Script | Purpose |
|---|---|
| `computed-diff.sh <session> <orig> <impl> <sel...>` | **Run first** — getComputedStyle comparison. Catches fontWeight/display/height root causes before pixel diff |
| `batch-scroll.sh <orig> <impl> <session> [dir]` | Captures both at 0–100% scroll positions |
| `ae-compare.sh <ref.png> <impl.png> [diff.png]` | AE comparison → `AE=<n> STATUS=PASS|FAIL` |
| `batch-compare.sh <dir> [threshold]` | Compare all pairs. Supports dynamic thresholds |
| `dssim-compare.sh <dir> [threshold]` | Structural similarity (catches what AE misses) |
| `layout-diff.sh <session> <orig> <impl>` | Section bounding box comparison |
| `section-compare.sh <orig> <impl> <session> <dir>` | **Section-level comparison** — crops each section, AE + structure diff. Catches SVG-as-text, layout mismatches. **`<dir>` is required** — pass `"$(pwd)/tmp/ref/<component>"` |
| `auto-diagnose.sh <session> <orig> <impl> <diff.png>` | **Auto-find mismatched elements** from AE diff image → elementFromPoint → computed-diff with severity |
| `layout-health-check.sh <session> <orig> <impl> <dir>` | Section height/total height structural check before pixel diff |
| `stray-absolute-check.sh <session> <impl-url> [w] [h]` | **Catches the "footer disappeared" bug class** — flags `position: absolute` elements with no positioned ancestor (offset resolves against `<body>`). Single URL, no ref needed. See `diagnosis.md` → Root Cause H. |
| `tailwind-transform-conflict-check.sh <session> <impl-url> [w] [h] [scope]` | **Catches the "transform stacked twice" bug class** — flags elements where computed style has both a non-identity `transform:` (Tailwind v3 shorthand) AND a non-`none` `translate:`/`rotate:`/`scale:` (Tailwind v4 individual properties), which compose on top of each other and double the rendered offset. Set `REF_DIR=...` to write `tailwind-conflict.json` — `verification-plan.sh` includes this as a universal `post-implement` row. See `diagnosis.md` → Root Cause I. |
| `breakpoint-collision-check.sh <session> <impl-url> [bps]` | **Catches the "broken at exactly 768" bug class** — captures impl at every Tailwind boundary ±1 (default 640/768/1024/1280/1536) and flags widths where `matchMedia(max-width)` and `matchMedia(min-width)` both match, body overflows in isolation, or root font-size jitters. Single URL, no ref needed. Set `REF_DIR=...` env to write `responsive/boundary-collisions.json` for the `boundary` gate. See `diagnosis.md` → Root Cause J. |
| `font-parity-check.sh <session> <ref-url> <impl-url> <ref-dir>` | **Gates the asset-substitution decision** — extracts primary `font-family` from both ref and impl, writes `<ref-dir>/font-parity.json`. The `font-parity` gate enforces: parity:`match` → PASS; parity:`mismatch` → must be declared in `asset-substitution.json`. Catches the "100% sections FAIL forever" bug when commercial fonts are silently substituted. See `ui-reverse-engineering/asset-substitution.md`. |
| `paid-features-detect.sh <ref-dir>` | **Early-detects paid font dependencies BEFORE generation** — static-greps `<ref-dir>/bundles/`, `css/`, `fonts.json`, `head.json`, `external-sdks.json` for paid font CDN hosts (Adobe Typekit, Monotype, Hoefler, Linotype, FONTPLUS / TypeSquare in Japan). Writes `<ref-dir>/paid-features.json` with `decision: null` for each finding. The `paid-features` gate (between `bundle` and `spec`) refuses to pass until every entry has `decision` set to one of `use` / `substitute` / `skip`. Catches the "100% sections FAIL forever" bug class when a paid web font silently falls back to the default sans-serif at impl time. **Note:** GSAP plugins are no longer flagged — GSAP became 100% free (including all previously-paid Club plugins) following the Webflow acquisition. |
| `reveal-trigger-check.sh <session> <impl-url> [w] [h]` | **Catches the "stuck reveal" bug class** — enumerates initially-hidden elements (opacity 0 / non-identity transform), scrolls each into view, fails any whose style never advances. Reports parent-chain with `overflow: hidden` ancestors so the IO+overflow:hidden bug class is named on first run instead of after many iterations of pixel-diffing. See `ui-reverse-engineering/transition-implementation.md` → IntersectionObserver placement for masked reveals. |
| `hidden-children-check.sh <session> <impl-url> <ref-dir>` | **Catches the "ref screenshot painted as background while DOM hidden" cheat** — for each major section, scrolls into view, dispatches scroll, finishes all in-flight animations, then enumerates non-trivial direct children (with text OR visual descendants like img/svg/canvas/video). Fails when a section with area > 20000 has >= 2 such children AND every one of them is permanently hidden (display:none / visibility:hidden / opacity<=0.01 / rect<2x2). Distinct from `reveal-trigger-check.sh` — that targets "wired but never fires"; this targets "stays hidden forever because the background is doing the rendering". |
| `runtime-dom-parity-check.sh <session> <ref-url> <impl-url> <ref-dir>` | **Positive-parity gate** — runs analysis JS on both ref and impl pages, fails if impl deviates from ref along: (a) node count outside ±30%, (b) visible text nodes < max(10, sectionCount*2), (c) any single image / picture / video / background-image element covers > 90% of viewport, (d) ref has Lottie evidence but impl has zero Lottie containers mounted. Catches screenshot-overlay and single-canvas-paint cheats that all negative gates miss. |
| `capture-artifact-inventory-check.sh <ref-dir>` | **ui-capture artifact contract gate** — every `regions.json` entry with `triggerType` must enumerate existing `clip/ref/` or `transitions/ref/` artifacts, so generation cannot infer transition evidence from names alone. |
| `asset-placement-check.sh <ref-dir> [impl-root]` | **Static section asset placement gate** — maps `visible-images.json.top` through `section-map.json` + `component-map.json` and fails when an asset is only referenced globally instead of by the component that renders its original section. |
| `ref-screenshot-asset-check.sh <ref-dir> [impl-root]` | **Static screenshot-substitution anti-cheat** — scans impl tree for path substrings of the ref's capture dirs (`tmp/ref/`, `/sections/{ref,impl,diff}/`, `/static/{ref,impl}/`, `/scroll-video/`) AND for byte-identical (sha256) copies of any file under those dirs. Blocks the agent from placing captured ref screenshots as impl backgrounds / assets. |
| `entry-coherence-check.sh <ref-dir> [impl-root]` | **Static stack-consolidation gate** — infers stack from `impl/package.json`. Vite+React requires `src/main.{jsx,tsx}`; Next App requires `app/page.{jsx,tsx}`. Fails on coexisting entries (src/main + app/page), mixed Vite+Next deps, raw ref markup pasted into index.html (>=5 layout content tags OR >=3 + 800 chars body text). |
| `scaffold-residue-check.sh <ref-dir> [impl-root]` | **Orphan-component gate** — exported PascalCase components under `impl/src/` (excluding entry files main/App/index) must appear as JSX `<Name>` or `createElement(Name)` somewhere. >=3 orphans OR >=40% orphan ratio = scaffold residue cheat. |
| `html-paste-check.sh <ref-dir> [impl-root]` | **Static entry-HTML theft gate** — three orthogonal signals on impl entry HTML (`index.html`, `src/index.html`, `app/page.tsx`, etc): (1) tag-multiset Jaccard >= 70% similarity to `dom-scaffold.json` (paste of ref body), (2) `<script src="...">` filename matches any `bundle-map.json` entry (hot-loading ref JS), (3) inline `<style>` block byte-similar (>= 70% difflib quick_ratio) to any `<ref>/bundles/*.css`. |
| `css-mirror-check.sh <ref-dir> [impl-root]` | **Static CSS theft gate** — scans impl CSS for `@import url(...)` targeting a host/filename in `bundle-map.json`, byte-identical copies of `<ref>/bundles/*.css`, or impl CSS with >= 70% quick_ratio to a ref bundle. Per-section snippet reuse allowed under `impl/src/styles/from-ref/`. |
| `required-media-coverage-check.sh <ref-dir> [impl-root]` | **Required video/Lottie coverage gate** — consumes `required-media.json` (produced by `scripts/extract/required-media.sh` at Step 6b-bis). Every video URL and every Lottie path must (a) be downloaded to `impl/public/` AND (b) be referenced in impl source. If ref has Lottie URLs, `impl/package.json` must declare a Lottie runtime package (`lottie-web` / `lottie-react` / `@lottiefiles/*` / `@dotlottie/*` / `bodymovin`). Closes the div-soup-site blind spot where `visible-images.json` only catalogues `<img>`. |
| `scaffold-warn-check.sh <ref-dir> [impl-root]` | **Subtree-not-found placeholder gate** — `scaffold-to-jsx.sh` emits `<section data-scaffold-warn="subtree-not-found-for-<name>" />` when it cannot resolve a section's subtree. Any of those placeholders shipping to impl = block-severity FAIL. |
| `invalidation-check.sh <ref-dir>` | **Operator invalidation stamp** — `touch <ref-dir>/.invalidated` with a JSON `{reason, markedAt, markedBy}` body marks a past loop result as known-bad. Post-implement refuses to pass until the stamp is removed AND the underlying issue is fixed. Use this when a prior loop's ref must be retired but kept on disk for diagnosis. |
| `svg-dom-parity-check.sh <session> <ref-url> <impl-url> <ref-dir>` | **Runtime SVG inventory parity gate** — walks ref + impl pages, enumerates per-section SVG inventory (inline SVG count, with-path count, `<img src$=".svg">`, `<use href>`, CSS bg url(...svg), pseudo-element bg url(...svg)). Fails when impl drops >=50% of ref's SVG inventory OR ships empty `<svg>` stubs where ref had geometry OR any per-section SVG presence is dropped. Closes the heavy-icon-site blind spot where logo / search / dropdown / footer SNS icons are CSS-background SVGs that `visible-images.json` never catalogued. |
| `motion-coverage-check.sh <ref-dir> [impl-root]` | **Motion implementation presence gate** — scores ref-side motion evidence (bundle-map.json libs: gsap/framer/lenis/anime/lottie/popmotion/react-spring/motion; transition-spec.json transitions count; external-sdks motion SDKs) vs impl-side motion code (motion library imports, `useScroll`/`useTransform`/`useSpring`/`useInView` hooks, `IntersectionObserver` / `ScrollTimeline` / `requestAnimationFrame` calls, `gsap.to`/`gsap.timeline` calls, CSS `@keyframes` / `animation` / `transition` / `scroll-timeline` declarations). `motion` substring excludes `emotion` (CSS-in-JS) via package-boundary matching. Fails when ref score >= 3 and impl score == 0, or ref score >= 5 and impl score < 2. Catches the "ref has gsap, impl has plain CSS only" gap. |
| `scroll-engine-parity-check.sh <ref-dir> [impl-root]` | **Scroll-engine CLASS parity gate** — distinct from motion-coverage which counts SOME motion code. This gate enforces that the SPECIFIC motion ENGINE class matches. Ref classes detected: `gsap-scrolltrigger`, `lenis-smooth-scroll`, `scroll-pin`, `scroll-scrub`, `framer-motion`, `native-scroll-timeline`. Impl must satisfy each ref class with an equivalent (e.g. ref `scroll-pin` requires impl `gsap-scrolltrigger` OR `native-scroll-timeline` — `css-sticky` alone cannot scrub). Closes the "look-and-feel similar but transitions guessed" gap where impl uses bare IntersectionObserver + CSS transitions but ref uses gsap.scrollTrigger({pin, scrub}) — fundamentally different motion class. |
| `monolithic-impl-check.sh <ref-dir> [impl-root]` | **Componentization gate** — flags when entry file (App.{jsx,tsx} / page.{jsx,tsx}) is >= 8KB AND component count < max(3, section-map.totalCount // 3). Catches the agent packing the entire UI into one file (defeats per-section iteration). Excludes entry files (main/App/index) from component count. Allows barrel re-exports under impl/src/styles/from-ref/. |
| `transition-spec-coverage.sh <component-dir> <impl-src-dir>` | **Static gate: every spec entry has an impl artifact.** Parses `transition-spec.json`, greps the impl source for each entry's id / selector / type-derived hooks (RevealRise, useScrollTrigger, useScroll, etc.), FAILs if any entry has zero hits. Catches the "hover transitions matched while intersection entries were never wired" failure class. |
| `transition-compare.sh <orig> <impl> <session> [dir]` | **Transition comparison** — idle/hover screenshots + computedStyle + timing diff per element |
| `tree-diff.sh <session> <orig> <impl> [dir]` | **Exhaustive per-element CSS diff** — walks every visible impl element (≥ MIN_SIZE px), pairs with ref via `elementFromPoint`, runs computed-style diff per pair. Catches mismatches AE misses (wrong font that renders identically, same-box different-style). |
| `layout-tree-diff.sh <session> <orig> <impl> [dir]` | **Geometry diff via signature-based pairing** — pairs impl ↔ ref by stable signature (text + tag + class hash + size class), reports geometry deltas (top/left/w/h) regardless of where elements moved. Catches what tree-diff misses (right element, wrong position). |
| `hover-tree-diff.sh <session> <orig> <impl> [dir]` | **Per-element hover/transition diff** — for each hover-capable element pair, captures idle → CDP `:hover` → settled style. Diffs timing (property/duration/easing/delay) + idle→hover delta. Catches missing hover rules, wrong easing, different deltas. |
| `keyframes-diff.sh <session> <orig> <impl> [dir]` | **`@keyframes` declaration diff** — extracts all keyframe rules from both pages, reports keyframes only on one side and same-name rules with different steps. Catches missing entrance animations, wrong timing curves baked into keyframes. |
| `scroll-anim-temporal-diff.sh <session> <ref> <impl> <selector> [dir]` | **Phase/frequency diff for scroll-driven repeating animations** — samples each matched element's position at N scroll progress steps on both sides, classifies as single-frequency (traveling wave) vs per-row-frequency vs mixed. Catches the "wave family wrong" bug class that AE/SSIM can't see (animation pixels match in any frozen frame, perceived motion is completely different). **Advisory only — no gate.** Run manually when the impl "feels off" on scroll for repeating elements; the selector arg is required so it can't be auto-invoked. |

**Reference selectors:** `common-selectors.md` — ready-to-use selector sets (typography, CSS reset canaries, Tailwind preflight issues, news/portal patterns, general e-commerce)

## Cost ladder — cheapest detection first

The diff tools below have a 100x cost spread (sub-second to multi-minute, plus token cost when their output gets read). The most common avoidable waste is jumping to L3/L4 when an L1/L2 check would have answered the same question. **Always start at L1 and stop as soon as a tier gives you the answer.**

| Tier | Cost | What | Use when |
|---|---|---|---|
| **L1** | ≤1s, ~free | Read existing summary files: `tmp/ref/<c>/sections/result.txt`, `pipeline-state.json`, `_summary.json`, `pixel-perfect-diff.json` | A prior run already produced these — they answer "what FAILed" in 2KB instead of you re-reading 50KB per section |
| **L2** | ≤5s, 1 page load | Structural checks: `stray-absolute-check.sh`, `transition-spec-coverage.sh`, `reveal-trigger-check.sh`, `layout-health-check.sh`, `computed-diff.sh` | Verifying transitions, checking for whole-class bugs (footer disappeared, reveal stuck, spec entry never wired) |
| **L3** | 30–120s | Targeted runs: `auto-diagnose.sh` on a single FAIL `diff.png`, `computed-diff.sh` on a narrow selector list | Bug class is suspected universal — sample one before sweeping all |
| **L4** | multi-minute | Full sweeps: full-page `section-compare.sh`, `transition-compare.sh`, `tree-diff.sh`, `hover-tree-diff.sh` | L1–L3 came back clean and you need exhaustive coverage |
| **L5** | minutes + tokens | Subagent visual review (Phase E LLM gate) | All metric tools agree but you need semantic verification |

**Read-summary-first rule (L1 specifics):** scripts that write per-section/per-element detail also write a summary. Read the summary, *then* drill into detail files only for entries marked FAIL.

| Script | Summary (read first) | Detail (drill on FAIL only) |
|---|---|---|
| `section-compare.sh` | `<dir>/sections/result.txt` (~2KB) | `<dir>/sections/<name>.json` (~50KB each) |
| `transition-compare.sh` | `<dir>/transitions/report.json` (per-element verdicts) | `<dir>/transitions/{ref,impl}-elements.json`, `hover-states.json` |
| `tree-diff.sh` / `layout-tree-diff.sh` / `hover-tree-diff.sh` / `keyframes-diff.sh` | severity-sorted markdown (`<dir>/<script-name>.md`) | paired `<dir>/<script-name>.json` raw diff list |
| Pipeline gates | `python -m ui_clone.pipeline ... status` | individual gate artifacts |

Reading a 50KB per-section JSON when result.txt would have answered the question is the single most common token waste in this workflow. If you don't see a summary file, that's a sign no run has been done yet — go to L2/L3, don't manually grep raw artifacts.

## Pick the right diff tool

Five computed-style/geometry diff tools exist; each answers a different question. Run the targeted tool first, then escalate if the answer is "nothing wrong" but AE still fails.

| Question | Tool | Scope | Cost |
|---|---|---|---|
| Are CSS resets / structural canaries OK? (entry-point sanity) | `computed-diff.sh` | Selector list you provide | Cheap — first call always |
| Did every transition-spec entry get wired into impl code at all? | `transition-spec-coverage.sh` | All entries in `transition-spec.json` vs grep of impl source | Cheap — first call when verifying transitions |
| Hidden-init elements (opacity 0, transform offset) — do they ever trigger? | `reveal-trigger-check.sh` | Every initially-hidden element on the impl page | Cheap — second call when verifying transitions |
| AE failed; which element on the diff image is wrong? | `auto-diagnose.sh` | Hotspots in the AE diff image | Cheap — second call |
| AE keeps failing but auto-diagnose found nothing — wrong style on visually-similar render | `tree-diff.sh` | Every visible element (≥ MIN_SIZE), paired by `elementFromPoint` | Med |
| Element is in the right place style-wise but at the wrong position | `layout-tree-diff.sh` | Every element, paired by signature (text+tag+class hash+size class) — robust to reflow | Med |
| Hover / transition feels off (wrong easing, missing rule, different delta) | `hover-tree-diff.sh` | Every hover-capable pair, idle → CDP `:hover` → settled | High — many state captures |
| Entrance / scroll animation timing is subtly off | `keyframes-diff.sh` | All `@keyframes` declarations from both pages | Low — declarations only |
| Scroll-driven repeating animation "feels off" — irregular gaps where ref shows smooth interlock, or vice versa | `scroll-anim-temporal-diff.sh` | Per-element trajectory across N scroll progress samples (you provide the selector for the repeating set) | Med — N viewport scrolls × 2 sites |

**Heuristics:**
- `tree-diff` and `layout-tree-diff` are siblings, not redundant — first asks "is the style right on this element?", second asks "is this element in the right place?". Run `tree-diff` first; if it's clean and AE still fails, run `layout-tree-diff`.
- `transition-compare.sh` is the predefined-set hover gate (Step 8c of `ui-reverse-engineering`); `hover-tree-diff.sh` is the exhaustive escalation. Use `hover-tree-diff` only when `transition-compare` reports PASS but the impl still feels wrong.
- `transition-spec-coverage.sh` and `reveal-trigger-check.sh` are the **first two** transition gates, not escalations — run them before `transition-compare.sh`. Coverage catches "entry never wired", reveal-trigger catches "wired but stuck". `transition-compare.sh` only verifies idle→hover diffs, so it can pass while intersection/scroll-driven entries are completely broken.
- Don't run all five by default — they are slower and noisier than the standard `auto-diagnose` workflow.

## Workflow

### Step 0-pre: pass the chrome-hidden impl URL to every script

Before running ANY compare script (`stray-absolute-check`, `section-compare`, `transition-compare`, `batch-compare`, `auto-diagnose`), confirm the impl shell is not painting fixed-position chrome that the ref doesn't have — dev banners, attribution / "made with X" badges, Vite/Next dev-overlay buttons, devtools widgets, locale switchers, env labels. Any element with `position: fixed` and a non-trivial bounding box will dominate AE in its corner *every frame*, turning a passing clone into FAIL while the diff image points at the chrome — not at any code you wrote.

**Quick scan:**
```bash
agent-browser --session <s> open <impl-url>
agent-browser --session <s> wait 1500   # let chrome (dev banners, badges) mount
agent-browser --session <s> eval "
(() => {
  const fixed = [...document.querySelectorAll('*')].filter(el => {
    const st = getComputedStyle(el);
    if (st.position !== 'fixed') return false;
    const r = el.getBoundingClientRect();
    return r.width >= 40 && r.height >= 20;
  }).map(el => ({ tag: el.tagName.toLowerCase(), id: el.id, cls: (el.className && el.className.toString) ? el.className.toString().slice(0,80) : '', w: el.getBoundingClientRect().width|0, h: el.getBoundingClientRect().height|0 }));
  return JSON.stringify(fixed);
})()
" > tmp/fixed-scan.json
```
Pipe to a file (per the token rule) — chrome scans are small but writing through `Read` keeps the pattern uniform.

If the scan returns elements that don't exist on the ref, the impl needs a hide-mechanism (query flag like `?embed=true`, env var like `NEXT_PUBLIC_HIDE_CHROME=1`, dev-only `NODE_ENV` guard, CSS `display:none` injected via a fixture stylesheet). Standardize on one and pass the *hidden* URL to every script. Document the flag in the impl repo's CLAUDE.md so it survives compaction.

**Failure signature when you forget:** AE delta image shows a clean rectangular hotspot in one corner (top-right, bottom-right, etc.); `auto-diagnose.sh` reports the badge element as the only mismatch; section-compare passes for every section *except* the one that includes the chrome's vertical band. Fix is a query-flag swap, not a code change — verify before opening any source file.

### Step 0: structural checks FIRST (before AE)

**Always run structural checks before pixel comparison.** AE catches *that* something is wrong; structural checks catch *why* — and fix the root cause immediately without hunting through diff images.

```bash
SCRIPTS="$SCRIPTS_DIR"

# 0a. Stray absolute positioning — catches the "footer disappeared" bug class.
#     Run on EVERY viewport you care about; the bug often only manifests on shorter pages.
bash "$SCRIPTS/stray-absolute-check.sh" <session>-stray <impl> 375 812
bash "$SCRIPTS/stray-absolute-check.sh" <session>-stray <impl> 1280 800

# 0a-bis. Stuck reveals — catches the IO+overflow:hidden bug class. Mandatory if
#         the spec has any `intersection`/`inview` trigger entries.
bash "$SCRIPTS/reveal-trigger-check.sh" <session>-reveal <impl> 1280 800

# 0a-quater. Breakpoint collision — catches the "broken at exactly 768" bug class.
#            Mandatory whenever impl mixes Tailwind responsive utilities AND a
#            project-scoped @media (max-width: <bp>px) rule (root font-size,
#            container padding, mobile-only stack). One run sweeps every Tailwind
#            boundary ±1; cheap (single page load, ~15 viewport sets).
bash "$SCRIPTS/breakpoint-collision-check.sh" <session>-bp <impl>

# 0a-ter. Spec coverage — every transition-spec entry must have an impl artifact.
#         Mandatory before per-trigger verification (transition-compare etc.) so
#         entirely-missing entries are caught BEFORE you waste a hover sweep.
bash "$SCRIPTS/transition-spec-coverage.sh" tmp/ref/<component> <impl-src-dir>

# 0b. Broad sweep: CSS reset canaries + page structure
bash "$SCRIPTS/computed-diff.sh" <session> <orig> <impl> \
  "h1" "h2" "h3" "h4" \
  "img" "button" "a" \
  "body" "header" "main" "footer"

# 0c. Domain-specific selectors from common-selectors.md
# IGNORE_FONT_SIZE=1 to skip OS text-scaling false positives
IGNORE_FONT_SIZE=1 bash "$SCRIPTS/computed-diff.sh" <session> <orig> <impl> \
  "[class*=title]" "[class*=logo]" "[class*=search]" "[class*=nav]"
```

See `common-selectors.md` for ready-to-use selector sets by domain.

### Full-page comparison (broad sweep)
```
0. Structural    stray-absolute-check.sh + computed-diff.sh (CSS reset canaries + page structure)
1. Capture        batch-scroll.sh <orig> <impl> <session>
2. AE diff        batch-compare.sh <dir>
3. DSSIM          dssim-compare.sh <dir>
4. Diagnose       auto-diagnose.sh <session> <orig> <impl> <diff.png>
                  → auto-finds mismatched elements, runs computed-diff with severity
                  → zero vision tokens. Only Read diff image if auto-diagnose finds nothing.
5. Fix            Targeted code change (critical severity first)
6. Re-compare     Repeat 0–3
7. LLM review     Read ref+impl pairs for ALL positions (Phase E)
8. Gate           All axes PASS → DONE
```

### Section-level comparison (precise — preferred for post-gen verification)
```
0. Structural    stray-absolute-check.sh + computed-diff.sh (CSS reset + section selectors)
1. Section compare  section-compare.sh <orig> <impl> <session> "$(pwd)/tmp/ref/<component>"
   → Per-section AE + severity (critical/major/minor) + structure diff
   ⚠️  The 4th argument (ref dir path) is MANDATORY — the Stop gate reads result.txt from that
       exact path. Omitting it writes result.txt to the wrong location and the gate never clears.
2. Transition compare  transition-compare.sh <orig> <impl> <session>
   → Per-element idle/hover style + timing diff
3. Diagnose     For FAIL sections: auto-diagnose.sh <session> <orig> <impl> <diff.png>
                → auto-finds mismatched elements within that section (zero vision tokens)
4. Fix          Targeted code change (critical severity first, then major, skip minor until Phase E)
5. Re-compare   Repeat 0–2
6. Gate         All sections PASS + all transitions PASS → DONE
```

**Use section-level for ui-reverse-engineering Step 8b/8c.** Use full-page for standalone `/visual-debug` invocations.

**`ONLY_IF_CHANGED=1` (skip if impl unchanged):** when re-running section-compare during iteration, set `ONLY_IF_CHANGED=1` + `IMPL_SRC_DIR=<path-to-impl-source>` to short-circuit if no `*.tsx`/`*.jsx`/`*.ts`/`*.js`/`*.css`/`*.scss` file has changed since the last run. The prior `sections/result.txt` stays in place (Stop gate passes against it).

```bash
ONLY_IF_CHANGED=1 IMPL_SRC_DIR=~/projects/foo/src \
  bash $SCRIPTS/section-compare.sh <orig> <impl> <session> "$(pwd)/tmp/ref/<c>"
```

Hash is SHA-256 of (sorted paths + content) — mtime-resilient. Delete `<ref-dir>/sections/.last-impl-hash` to force a full run. Use this for the second/third/Nth re-run after a fix; skip it on the *first* run after extraction (no prior result.txt to reuse).

## Escalation diagnostics (when the standard workflow misses the bug)

The standard workflow (AE + DSSIM + `auto-diagnose.sh` + `computed-diff.sh`) catches most mismatches. When AE keeps reporting failures but `auto-diagnose` returns clean — escalate to the **tree-diff family**. These walk *every* element on the page rather than a fixed selector list, so they catch what targeted diagnostics miss.

| Symptom | Escalate to | Why |
|---|---|---|
| AE fails repeatedly but `auto-diagnose` finds nothing | `tree-diff.sh` | Exhaustive computed-style diff — pairs every visible impl element with ref via `elementFromPoint`. Catches wrong fonts that render identically, same-box different-style overrides. |
| Element appears at wrong position but `tree-diff` says style matches | `layout-tree-diff.sh` | Geometry diff via signature-based pairing — pairs by stable signature (text + tag + class hash + size class), reports `top/left/w/h` deltas regardless of where the element moved on screen. |
| Hover/transition feels off but `transition-compare.sh` reports PASS | `hover-tree-diff.sh` | Per-element CDP `:hover` capture for *every* hover-capable pair (not just the predefined set). Diffs idle→hover delta + timing. |
| Entrance/scroll animation runs but timing or curve is subtly different | `keyframes-diff.sh` | Diffs `@keyframes` declarations directly. Catches missing rules, wrong steps, wrong easing baked into the keyframe definition rather than the animation shorthand. |

```bash
bash "$SCRIPTS/tree-diff.sh"        <session> <orig> <impl>   # full-element style diff
bash "$SCRIPTS/layout-tree-diff.sh" <session> <orig> <impl>   # geometry deltas
bash "$SCRIPTS/hover-tree-diff.sh"  <session> <orig> <impl>   # hover style + timing
bash "$SCRIPTS/keyframes-diff.sh"   <session> <orig> <impl>   # @keyframes declarations
```

These are diagnostic, not gate-blocking. Use them when `section-compare` / `transition-compare` keep failing without a clear cause — they produce a markdown report (severity-sorted) that names the culprit elements and properties. **Do not run all four by default** — they are slower and more expensive than the standard workflow.

## Three-axis verification (ALL required)

| Axis | Tool | Catches | Blind spot |
|------|------|---------|------------|
| **Pixel** | AE | Exact rendering diff | Lottie frame differences (false positive) |
| **Perceptual** | DSSIM | Color/tone mismatch | Missing content on same-color bg |
| **Semantic** | LLM (Phase E) | Missing sections, wrong content | Slow, costs tokens |

A position is PASS only when **all three agree** (or LLM explicitly approves a known difference).

### Phase E: LLM Review (MANDATORY)

NOTE: Quick comparison (Phases A-D) uses zero vision tokens via AE/SSIM diff. Phase E (LLM verification) is mandatory for full verification workflow and DOES use vision tokens for the final review.

After AE + DSSIM, read every position's ref+impl pair. Judge PASS / PARTIAL / FAIL. Not optional — automated metrics can silently pass wrong results. ~44K tokens.

**Always delegate Phase E to a subagent context.** In Claude Code-style hosts, prefer the plugin's `subagent_type: "ui-clone-skills:visual-debug-reviewer"` — it pins `model: opus` so vision verdict quality is consistent regardless of the parent agent's model. As a fallback (e.g. Codex inline), `subagent_type: "general-purpose"` works but inherits the parent's model (sonnet defaults can degrade Phase E judgment). Other hosts should use their equivalent delegated-worker mechanism with an opus-equivalent model. The 44K vision tokens stay in the subagent context, and only the verdict table (~500 tokens) returns. See `comparison-fix.md` Phase E section for the example invocation.

## Thresholds

| Metric | Pass | Fail |
|---|---|---|
| AE per image | ≤ 500 | > 500 |
| SSIM per frame | ≥ 0.995 | < 0.995 |
| Computed style diff | 0 mismatches | > 0 |

AE=500 allows anti-aliasing variance. Bump to 2000 for dynamic content.

## Dynamic content (canvas/video) — `EXCLUDE_DYNAMIC=1`

RAF-driven canvases (Three.js shaders, particle fields) and `<video>` elements run on independent clocks in ref vs impl, so their per-frame pixel diff is unmatchable — they dominate AE without indicating a real defect.

`section-compare.sh` accepts an opt-in mask:

```bash
EXCLUDE_DYNAMIC=1 bash section-compare.sh <orig> <impl> <session> "$(pwd)/tmp/ref/<component>"
```

When set, the script injects `visibility: hidden !important` for the masked selectors into both ref and impl screenshots — identical hide rule on both sides, so layout is preserved while the noisy region drops out of AE.

| Var | Default | Effect |
|---|---|---|
| `EXCLUDE_DYNAMIC` | `0` | `1` enables masking |
| `DYNAMIC_SELECTORS` | `canvas, video` | Override the default mask list |
| `transition-spec.json` entries with `"dynamic": true` | — | Auto-augment the mask list with each entry's `target` selector |

**Spec-driven (recommended):** add `"dynamic": true` to every `transition-spec.json` entry whose visual is RAF-driven (auto-timer canvas, looping shader, `<video>` autoplay). Then `EXCLUDE_DYNAMIC=1` masks them all without enumerating selectors at the call site.

**Selector caveat:** Selectors must not contain quote characters of either kind. `"` would close the injected JS string and `'` would close the surrounding Python r-string. Use bare attribute matchers (e.g. `[data-canvas=hero]`) or class/id selectors (e.g. `.canvas-hero`, `#hero-canvas`). The script aborts if it sees `"` or `'`.

## Full verification

- `verification.md` — Phase A/B (capture) + D (pixel-perfect gate) + auxiliary checks
- `comparison-fix.md` — Phase C (AE+DSSIM comparison, computed-style diagnosis, Phase E LLM review, Phase H self-healing loop)

## Browser cleanup (MANDATORY)

**Every skill run MUST end with browser cleanup — success, failure, or interruption.**

```bash
# Always close your own session(s) by name
agent-browser --session <session-name> close
```

- Close every `--session <name>` you opened during the comparison
- Run cleanup **before returning control to the user**, even on error/early exit
- Unclosed sessions spawn Chrome Helper processes (GPU + Renderer) that persist indefinitely
- **Never use `close --all`** because other agent-browser sessions may have active browsers. Only close sessions you own.

**Bulk cleanup helper (multi-session sweeps):** if a long verification run accumulated many ad-hoc sessions under a common prefix, use `scripts/verify/cleanup-sessions.sh <prefix>` (dry-run with `--dry`) to close them all in one pass. Refuses prefixes shorter than 3 chars; never matches across other agent-browser sessions.

## Integration

`visual-debug` owns comparison and diagnosis only. After it identifies a fix target or a PASS/FAIL result, resume the caller's pipeline (`ui-reverse-engineering`, `ui-capture`, or standalone task) for implementation, regeneration, and the next gate.

| Skill | Where |
|---|---|
| `ui-reverse-engineering` Step 8+9 | Full verification procedure |
| `ui-reverse-engineering` Step T4 | Phase D for transition resting states |
| `ui-capture` Phase 4A | Phase D before compare.html |
| Standalone | batch-scroll + batch-compare on any two URLs |
