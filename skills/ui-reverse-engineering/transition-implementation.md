# Transition Implementation — Step 7 (Bundle → Code Translation)

When implementing scroll/page-load/interaction transitions extracted from JS bundles.

> 🚨 **Bundle → infrastructure components, not just per-component animations.**
> Sites built with Lenis/Locomotive scroll, GSAP timelines, IntersectionObserver
> reveal libraries, or splash-overlay loaders need *shared infrastructure*
> components (`SmoothScroll`, `IntroAnimation`, `ScrollListener`) before any
> section component renders. Read `bundle-map.json` → identify these libraries →
> create the matching infrastructure components FIRST, then wrap `<main>` with
> them. A clone that falls back to a single `useReveal` IntersectionObserver hook
> loses all the scroll-coupled motion the original site has.

## Core principle

**Transitions are part of component generation, not a separate step.** When generating a component, read `transition-spec.json` + the source bundle file, and implement the transition IN the component. A component without its transitions is incomplete.

### Binding mandate (enforced at runtime)

Every `transition-spec.json` entry MUST be implemented with its declared trigger + easing + duration, and is enforced at runtime by the transition-fires gate (post-implement) — a component that imports an animation library but creates no trigger, or whose target does not measurably move at its trigger, FAILS. Per-trigger pattern: IntersectionObserver/whileInView for scroll-into-view reveals; useScroll/scrollYProgress (or rAF + getBoundingClientRect under smooth-scroll) bound to the target's transform/opacity for scroll-scrub; real timeline for splash; autoplay interval for carousel.

**Do not re-derive the scroll/state/swiper parameters — they are already extracted.** For scroll-scrub, scroll-state-machine, and swiper/carousel entries, run `scripts/extract/emit-motion-skeletons.sh <ref-dir> <impl-dir>` to emit `impl/src/generated/motion-skeletons.ts`: one `// spec:<id>`-tagged hook/init per entry with the property list + input range (e.g. `width,height,borderRadius` over `[0,.5,1]` — NOT an approximated `scale`), the state set (`initial → expanded → settled`), and each carousel's EXACT Swiper config including its `breakpoints` object and a `matchMedia` change listener that rebuilds on the mobile query. Fill only the TODO wiring (refs, output stops, thresholds); do not rewrite the parameters or collapse the per-carousel configs into one.

The inverse is also mandatory: do not add hover/page motion that the reference did not show. Header links, logos, cards, and media wrappers must not rotate, disappear, scale, or fade on hover unless `states/hover/manifest.json`, `hover-css-rules.json`, or `transition-spec.json` records that exact target and property delta. Comprehensive verification runs `hover-tree-diff.sh` from the implementation side to catch invented hover motion.

### Spec target absent from the scaffold (reconciliation)

A `transition-spec.json` / `hover-css-rules.json` target whose class/id is absent from `structure.json` is an **interaction-revealed** element the single-state capture never saw (dropdown CTA, tab panel, share/sign-in popover). The mirrored CSS ships its `:hover`/transition rules, but the transpiler has no node to attach them to — so the transition later fails `transition-fires` with 'element not found'. Do not hand-guess the markup. Run:

```
bash scripts/extract/reconcile-spec-targets.sh <ref_url> <ref_dir> [--session S]
```

It drives the live ref with bounded stimulation (scroll, hover nav, click tabs/expanders — never navigating `<a href>`), captures each revealed element's subtree in `structure.json`'s node shape, and splices placeable ones under their observed parent into `structure.merged.json` (never mutating `structure.json`). Then:

1. **Placed targets** (`reconcile-report.json` → `mergedTargets[]`) are already in `structure.merged.json`; transpile from the merged tree so those nodes are emitted.
2. **`missingSpecTargets[]`** are the unresolved remainder — never revealed by stimulation, or revealed only inside an interaction-mounted overlay whose parent is absent from the homepage tree. Each carries its `subtreeHtml` snippet when captured. These are a **Step-7 synthesis obligation**: build the overlay/panel component + its trigger from the snippet, or move the entry to `transition-spec.json` `skipped[]` with a reason if it is genuinely a subpage selector. Do not silently drop them.

## Bundle → Code translation

### Scroll-driven animations (GSAP ScrollTrigger / custom)

Original JS libraries use scroll position to drive CSS transforms. Without GSAP, replicate with a **scroll listener** (`{ passive: true }`) in `useEffect`, a **progress calculation** from the section's scroll position, and **direct DOM manipulation** via refs (not React state). If the captured runtime or bundle clips page height, cancels wheel/touch input, or advances a phase after a dwell, read `transition-patterns.md` → "Input-owned scroll sequences" before tuning offsets.

#### Progress formula

```
ScrollTrigger start: 'top 90%'  →  section top at 90% of viewport
ScrollTrigger end: 'bottom top' →  section bottom exits viewport top

const rect = section.getBoundingClientRect()
const vh = window.innerHeight
const scrollStart = vh * 0.9
const progress = clamp((scrollStart - rect.top) / (scrollStart + section.offsetHeight), 0, 1)
```

Adjust `scrollStart` to the original `start` value: `'top top'` → `0`, `'top 80%'` → `vh * 0.8`, `'top center'` → `vh * 0.5`.

#### Common patterns

| Bundle pattern | Implementation |
|---|---|
| `scrub: N` (scroll-driven) | Progress-based transform via ref, no CSS transition |
| `pin: true` | `position: sticky; top: 0` (override parent `overflow: hidden` if needed) |
| `from(el, { y: '100%' })` with scrub | `translateY((1 - progress) * 100%)` |
| `set(el, { y: '20vh' })` + `to(el, { y: '-20vh' })` | `translateY(20 - progress * 40)vh` |
| `to(el, { scale: 1.1 })` with scrub | `scale(1 + progress * 0.1)` |
| Background crossfade overlays | Multiple absolute divs, toggle `opacity: 0/1` by active index |

### Click-triggered content transitions

If a click swaps the visible content (image grid → search results), read
`transition-structure.json` first and then
[transition-patterns.md](transition-patterns.md#click-triggered-content-transitions-view-swap--search-results)
for the new-on-top / old-on-top / single-pane recipes. Never guess the pane architecture.

### Page-load animations

| Bundle pattern | Implementation |
|---|---|
| `opacity: 0 → 1` with duration | CSS transition + React state toggle in `useEffect` |
| Container expand (width/height change) | CSS transition on dimensions, triggered by state |
| Sequential reveals (A then B then C) | `setTimeout` chain matching original delays |

When a page-load splash waits on video load, use the timing pattern in `transition-patterns.md` → "Splash/intro animation timing".

### Easing conversion

Convert animation library easings to CSS `cubic-bezier`:

| Library easing | CSS equivalent |
|---|---|
| `power1.out` | `cubic-bezier(0, 0, 0.58, 1)` |
| `power2.out` | `cubic-bezier(0.215, 0.61, 0.355, 1)` |
| `power2.inOut` | `cubic-bezier(0.645, 0.045, 0.355, 1)` |
| `power3.out` | `cubic-bezier(0.165, 0.84, 0.44, 1)` |
| `none` / `linear` | `linear` (or no CSS transition — direct progress mapping) |

Use `scripts/extract/gsap-to-css.sh convert "<easing>"` for automated conversion.

## Bundle parameters are EXACT

If the bundle says `duration: 1.4`, use `1.4`. If it says `y: 12 * index`, use `12 * index`. Do not round or approximate. GSAP `stagger` has number vs object semantics (`each` vs `amount`) — see `transition-patterns.md` → "GSAP `stagger` semantics" when the bundle uses the object form.

## Pre-implementation sticky check (run BEFORE writing code)

Before implementing any `position: sticky` element, check the original CSS for conflicts:

```bash
# Check if the section has overflow:hidden or display:grid in original CSS
grep '<section-class>' tmp/ref/<component>/css/*.css | grep -E 'overflow|display.*grid|place-items'
```

If found, add inline overrides in the component: `overflow: hidden` → `style={{ overflow: 'visible' }}`; `display: grid; place-items: center` → `style={{ display: 'block' }}`. `position: sticky` fails silently when ANY ancestor has `overflow: hidden/auto/scroll`; original sites using GSAP `pin: true` never needed sticky, so discovering this after implementation wastes an iteration cycle.

## Performance

- Use **refs** for continuous scroll-driven transforms (not `useState`)
- Use **`will-change: transform`** on animated elements
- Scroll listeners must use **`{ passive: true }`**
- Batch reads (getBoundingClientRect) before writes (style mutations)

## Click-toggle / Click-cycle transitions

For `click-toggle` (accordion, dropdown) and `click-cycle` (tabs) entries, use the state recipes in `transition-patterns.md` → "Click-toggle / Click-cycle transitions"; `duration`/`easing`/`measuredHeight` come from the captured active state, and each click-cycle `state-N.png` corresponds to `tabs[N].content`.

---

## GSAP Plugin Alternatives

> **If `transition-spec.json` references GSAP plugins but the implementation should avoid a GSAP dependency** (SplitText, MorphSVG, ScrollSmoother, DrawSVG, Draggable), read `gsap-alternatives.md` for replacement options (priority: project library -> npm package -> manual CSS). Otherwise skip. For any split-text implementation (chars, words, or lines) with a mask wrapper, regardless of library, apply the strict mask CSS rules in `transition-patterns.md` → "SplitText mask wrapper CSS".

### IntersectionObserver placement for masked reveals

When the reveal pattern is `overflow: hidden` parent + `transform: translate(0, 100%)` child (the standard "rise from below mask"), the **IO observer ref MUST be on the non-moving outer wrapper, never on the moving child.**

**Why:** `IntersectionObserver` computes the visible (post-clipping) intersection rect — it respects every ancestor's `overflow: hidden`/`clip`. When the child is translated 100% of its own height, it sits exactly outside the parent's box. The parent clips it out, so IO reports `intersect: false, ratio: 0` even when `getBoundingClientRect()` says the child is inside the viewport. The reveal **never triggers**, the element stays at `opacity: 0` forever, and there is no console error.

**Wrong:**
```tsx
function RevealRise({ children }) {
  const ref = useRef<HTMLDivElement>(null)
  const inView = useScrollTrigger(ref)  // ❌ ref on the moving element
  return (
    <div
      ref={ref}
      style={{ transform: inView ? 'translateY(0)' : 'translateY(100%)' }}
    >
      {children}
    </div>
  )
}
// Caller wraps it in <li className="overflow-hidden"> — IO clipped, never fires
```

**Right:**
```tsx
function RevealRise({ children }) {
  const ref = useRef<HTMLDivElement>(null)
  const inView = useScrollTrigger(ref)  // ✅ ref on the static outer wrapper
  return (
    <div ref={ref} style={{ overflow: 'hidden' }}>
      <div style={{ transform: inView ? 'translateY(0)' : 'translateY(100%)' }}>
        {children}
      </div>
    </div>
  )
}
```

**Verification — first thing to check when a reveal "doesn't trigger":**

```js
agent-browser --session <s> eval "(async () => {
  const el = document.querySelector('<the moving child selector>')
  return new Promise(r => {
    const o = new IntersectionObserver(([e]) => {
      r({intersect: e.isIntersecting, ratio: e.intersectionRatio,
         rect: e.boundingClientRect.toJSON(), root: e.rootBounds && e.rootBounds.toJSON()})
      o.disconnect()
    }, {threshold: 0.2})
    o.observe(el)
  })
})()"
```

If `intersect: false` while `rect` is clearly inside `root` → ancestor clipping. Move the IO ref one level up (to a non-transformed wrapper) and apply `overflow: hidden` there. `display: contents` on a wrapper does **not** fix this — IO can't observe `contents` elements (returns 0-size rect). This applies to any intersection-based reveal that uses a clipping mask (`RevealRise`, `RevealLetters`, `RevealLine`, custom `IntersectionFadeUp`, …) and symmetrically to `clip-path: inset(...)` masks.

### Verification per spec-entry trigger type

`transition-spec.json` is a checklist, not a hint. Every entry has a `trigger` (or `type`) field, and **each trigger category has a different verification command** — verifying hover entries does not verify intersection entries. Reporting "transitions matched" after only running `transition-compare.sh` (hover/idle-state only) is the bug class where intersection / scroll-driven entries silently never wire up while the hover sweep passes.

Run the matrix in this exact order. Skipping a row = silent omission of that whole category.

| Spec `trigger` / `type` | Examples | Required verification | Tool |
|---|---|---|---|
| `hover` / `css-hover` / `mouseenter` | nav-link underline, button color, image scale | Idle vs hover computed style + timing per element | `transition-compare.sh <orig> <impl> <session>` |
| `intersection` / `inview` / `intersection-fade-up` | RevealRise, RevealLetters, fade-up sections, reveal masks | Scroll each hidden-init element into view; verify opacity/transform actually advance past initial values | `reveal-trigger-check.sh <session> <impl-url> <w> <h>` |
| `scroll` / `scroll-driven` / `scroll-scale` | parallax, scroll-driven scale on Works cards, sticky header threshold | Scroll-position sweep — capture computed style at 0/25/50/75/100% and check the value progresses monotonically | `batch-scroll.sh` + `auto-diagnose.sh` on diff hotspots |
| `auto-timer` / `loop` / `cycle` / `raf` | WebGL canvas cycle, cursor follower, infinite marquee | Numerical comparison against bundle-extracted parameters (NOT screenshots — frames are async) | See `bundle-verification.md` |
| `click` / `click-toggle` / `click-cycle` | accordion, tab swap, modal open | Dispatch click + capture state delta; ensure both directions tested | `transition-compare.sh` with explicit click event in `--actions` |

**Coverage gate (run before any of the above):**

```bash
SCRIPTS="${VISUAL_DEBUG_SCRIPTS_DIR:-${PLUGIN_ROOT:+$PLUGIN_ROOT/skills/visual-debug/scripts}}"
SCRIPTS="${SCRIPTS:-${CODEX_PLUGIN_ROOT:+$CODEX_PLUGIN_ROOT/skills/visual-debug/scripts}}"
SCRIPTS="${SCRIPTS:-${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/visual-debug/scripts}}"
[ -n "$SCRIPTS" ] || { echo "Set VISUAL_DEBUG_SCRIPTS_DIR or PLUGIN_ROOT" >&2; exit 1; }
bash "$SCRIPTS/transition-spec-coverage.sh" \
  tmp/ref/<component> \
  apps/<app>/src/projects/<component>
```

Every spec entry must show ≥1 matching impl artifact. A `❌` row means the entry was extracted into the spec but **never wired in code**. Fix coverage before running the per-trigger verification rows above.

**Done = every row of the matrix returns PASS for entries of that trigger type.** Not "I checked the hover ones, looks good."

## Pattern recipes and anti-pattern catalog

Read [transition-patterns.md](transition-patterns.md) only when a spec entry or a
failing check matches one of these rows:

| Pattern | Read when |
|---|---|
| Card stack (page-stack) | stacked cards collapse on scroll; heights must be fixed to px before toggling `hide` |
| Input-owned scroll sequences | the runtime clips page height, cancels wheel/touch input, or advances a phase after a dwell |
| Splash/intro animation timing | a page-load splash waits on video load before expanding |
| Click-toggle / click-cycle | `click-toggle` (accordion, dropdown) or `click-cycle` (tabs) spec entries |
| SplitText mask wrapper CSS | any masked split-text reveal (host grows taller than ref per line) |
| GSAP `stagger` semantics | the bundle uses `stagger: { amount }` or a staggered target never reveals |
| A. IO-fire-once vs scroll-scrub | bundle has `scrub:` / `useScroll`+`useTransform`; style must reverse on scroll-up |
| B. Viewport-aware scrub offsets | `scroll-end-completion-check.sh` reports an element stuck near maxScroll |
| C. `completeAt` headroom | stagger/shuffle tails never reach their final value at progress=1 |
| D. Seeded shuffle | `hydration-check.sh` fails on randomized order (`Math.random()` in render) |
| E. WAAPI reveal semantics | IO-driven `element.animate()` reveals snap back after the harness scrolls up |
| F. IO + `overflow:hidden` clipping | `reveal-trigger-check.sh` lists a clipped initially-hidden child |
| G. Footer-disappeared | `stray-absolute-check.sh` flags `position:absolute` without a positioned ancestor |
