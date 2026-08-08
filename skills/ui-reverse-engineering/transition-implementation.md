# Transition Implementation — Step 7 (Bundle → Code Translation)

When implementing scroll/page-load/interaction transitions extracted from JS bundles.

> 🚨 **Bundle → infrastructure components, not just per-component animations.**
> Sites built with Lenis/Locomotive scroll, GSAP timelines, IntersectionObserver
> reveal libraries, or splash-overlay loaders need *shared infrastructure*
> components (`SmoothScroll`, `IntroAnimation`, `ScrollListener`) before any
> section component renders. Read `bundle-map.json` → identify these libraries →
> create the matching infrastructure components FIRST, then wrap `<main>` with
> them. Skipping this step is a high-risk fidelity regression for sites whose
> motion depends on shared scroll/load infrastructure. A successful clone of a
> Lenis/GSAP-style site has `SmoothScroll` + `IntroAnimation` + `ScrollListener`
> wrappers; a failing one has none and falls back to a single `useReveal`
> IntersectionObserver hook, losing all the scroll-coupled motion the original
> site has.

## Core principle

**Transitions are part of component generation, not a separate step.** When generating a component, read `transition-spec.json` + the source bundle file, and implement the transition IN the component — not as a later pass.

If you generate a component without its transitions, it is incomplete.

### Binding mandate (enforced at runtime)

Every `transition-spec.json` entry MUST be implemented with its declared trigger + easing + duration, and is enforced at runtime by the transition-fires gate (post-implement) — a component that imports an animation library but creates no trigger, or whose target does not measurably move at its trigger, FAILS. Per-trigger pattern: IntersectionObserver/whileInView for scroll-into-view reveals; useScroll/scrollYProgress (or rAF + getBoundingClientRect under smooth-scroll) bound to the target's transform/opacity for scroll-scrub; real timeline for splash; autoplay interval for carousel.

**Do not re-derive the scroll/state/swiper parameters — they are already extracted.** For scroll-scrub, scroll-state-machine, and swiper/carousel entries, run `scripts/extract/emit-motion-skeletons.sh <ref-dir> <impl-dir>` to emit `impl/src/generated/motion-skeletons.ts`: one `// spec:<id>`-tagged hook/init per entry with the property list + input range (e.g. `width,height,borderRadius` over `[0,.5,1]` — NOT an approximated `scale`), the state set (`initial → expanded → settled`), and each carousel's EXACT Swiper config including its `breakpoints` object and a `matchMedia` change listener that rebuilds on the mobile query (never a one-shot read). Fill only the TODO wiring (refs, output stops, thresholds); do not rewrite the parameters or collapse the per-carousel configs into one.

The inverse is also mandatory: do not add hover/page motion that the reference did not show. Header links, logos, cards, and media wrappers must not rotate, disappear, scale, or fade on hover unless `states/hover/manifest.json`, `hover-css-rules.json`, or `transition-spec.json` records that exact target and property delta. Comprehensive verification runs `hover-tree-diff.sh` from the implementation side to catch invented hover transforms/opacity changes on otherwise-static reference elements.

### Spec target absent from the scaffold (reconciliation)

A `transition-spec.json` / `hover-css-rules.json` target whose class/id is absent from `structure.json` is an **interaction-revealed** element the single-state capture never saw (dropdown CTA, tab panel, share/sign-in popover). The mirrored CSS ships its `:hover`/transition rules, but the transpiler has no node to attach them to — so the transition later fails `transition-fires` with 'element not found'. Do not hand-guess the markup. Run:

```
bash scripts/extract/reconcile-spec-targets.sh <ref_url> <ref_dir> [--session S]
```

It drives the live ref with bounded stimulation (scroll, hover nav, click tabs/expanders — never navigating `<a href>`), captures each revealed element's subtree in `structure.json`'s node shape, and splices placeable ones under their observed parent into `structure.merged.json` (never mutating the provenance-stamped `structure.json`). Then:

1. **Placed targets** (`reconcile-report.json` → `mergedTargets[]`) are already in `structure.merged.json`; transpile from the merged tree so those nodes are emitted and the mirrored hover CSS has something to act on.
2. **`missingSpecTargets[]`** are the unresolved remainder — either never revealed by stimulation, or revealed only inside an interaction-mounted overlay whose parent is absent from the homepage tree. Each carries its `subtreeHtml` snippet when captured. These are a **Step-7 synthesis obligation**: build the overlay/panel component + its trigger from the snippet, or move the entry to `transition-spec.json` `skipped[]` with a reason if it is genuinely a subpage selector. Do not silently drop them.

## Bundle → Code translation

### Scroll-driven animations (GSAP ScrollTrigger / custom)

Original JS libraries use scroll position to drive CSS transforms. Without GSAP, replicate with:

1. **Scroll listener** (`{ passive: true }`) in `useEffect`
2. **Progress calculation** from section's scroll position
3. **Direct DOM manipulation** via refs (not React state — for performance)

#### Progress formula

```
ScrollTrigger start: 'top 90%'  →  section top at 90% of viewport
ScrollTrigger end: 'bottom top' →  section bottom exits viewport top

const rect = section.getBoundingClientRect()
const vh = window.innerHeight
const scrollStart = vh * 0.9
const progress = clamp((scrollStart - rect.top) / (scrollStart + section.offsetHeight), 0, 1)
```

Adjust `scrollStart` based on the original `start` value:
- `'top top'` → `scrollStart = 0`
- `'top 80%'` → `scrollStart = vh * 0.8`
- `'top center'` → `scrollStart = vh * 0.5`

#### Common patterns

| Bundle pattern | Implementation |
|---|---|
| `scrub: N` (scroll-driven) | Progress-based transform via ref, no CSS transition |
| `pin: true` | `position: sticky; top: 0` (override parent `overflow: hidden` if needed) |
| `from(el, { y: '100%' })` with scrub | `translateY((1 - progress) * 100%)` |
| `set(el, { y: '20vh' })` + `to(el, { y: '-20vh' })` | `translateY(20 - progress * 40)vh` |
| `to(el, { scale: 1.1 })` with scrub | `scale(1 + progress * 0.1)` |
| Background crossfade overlays | Multiple absolute divs, toggle `opacity: 0/1` by active index |

### Click-triggered content transitions (view swap / search results)

When clicking an element swaps the visible content (e.g., image grid → search results), the implementation depends on `transition-structure.json` from interaction detection.

**MANDATORY:** Read `transition-structure.json` before implementing. Never guess the pane architecture.

#### Pattern: New-on-top (most common for image grids)

The new pane sits above the old pane. New images load asynchronously with fadein, progressively covering the old pane. Old pane fades out underneath.

```
DOM order: old pane (first) → new pane (last, renders on top)
z-index:   old=1, new=2
background: new pane = transparent (old pane shows through image gaps)
```

Implementation:
1. On click: snapshot current images → `oldImages` state, render old pane with fadegray+fadeout CSS
2. Clear `currentImages` to `[]` → new pane is empty (transparent bg shows old pane)
3. Set new layout (column count, viewMode)
4. Fetch API → set `currentImages` to response
5. Each new `<img>` gets `se_image_fadein` class → loads with 0.15s fadein
6. As images load, they cover the old pane from top to bottom
7. Timer removes old pane after fadeout animation completes (e.g., 4.5s)

CSS:
```css
/* Old pane — below, fades out */
.old_pane {
  animation: fadegray 0.35s forwards, fadeout 4s 0.35s forwards;
  z-index: 1;
}

/* New pane — above, transparent so old shows through gaps */
.new_pane {
  z-index: 2;
  background: transparent;
}

/* Individual images fade in as they load */
.image_fadein {
  animation: fadein 0.15s forwards;
}
```

#### Pattern: Old-on-top (old content fades revealing new)

Old pane sits above with `background: #fff`. Fadegray runs on old pane, then fadeout reveals new pane underneath. **Requires `background: #fff`** on old pane — otherwise both panes' images blend through each other.

#### Pattern: Single pane (class toggle)

One pane element. `old_pane` class is added (triggering fadegray CSS), then removed when new images arrive (canceling animation, showing fresh content).

Simplest implementation but no progressive image loading effect.

#### Anti-patterns (all observed in real failures)

| Mistake | Symptom | Fix |
|---------|---------|-----|
| Old pane on top WITHOUT `background: #fff` | New pane images bleed through during fadegray | Add `background: #fff` to old pane OR use new-on-top pattern |
| Old pane on top WITH fadeout + same images in both panes | Fadegray desaturates then color returns as old fades | Clear new pane images OR use new-on-top pattern |
| New pane with fadein CSS + old pane also has fadeout | Both layers animate simultaneously, double-ghost effect | Only ONE pane should have opacity animation |
| `setViewMode` before API response with two panes | Column count changes, same images in wrong layout visible | Keep viewMode until API responds, or clear images first |
| Timer removes old pane before images load | White flash | Extend timer OR use API response timing |

### Page-load animations

| Bundle pattern | Implementation |
|---|---|
| `opacity: 0 → 1` with duration | CSS transition + React state toggle in `useEffect` |
| Container expand (width/height change) | CSS transition on dimensions, triggered by state |
| Sequential reveals (A then B then C) | `setTimeout` chain matching original delays |

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

If the bundle says `duration: 1.4`, use `1.4`. If it says `y: 12 * index`, use `12 * index`. Do not round or approximate. These values are tuned by the original designer.

## Pre-implementation sticky check (run BEFORE writing code)

Before implementing any `position: sticky` element, check the original CSS for conflicts:

```bash
# Check if the section has overflow:hidden or display:grid in original CSS
grep '<section-class>' tmp/ref/<component>/css/*.css | grep -E 'overflow|display.*grid|place-items'
```

If found, add inline overrides in the component:
- `overflow: hidden` → `style={{ overflow: 'visible' }}`
- `display: grid; place-items: center` → `style={{ display: 'block' }}`

These are needed because original sites use JS-based pinning (GSAP), not CSS sticky.

**Why this is a pre-check, not a debugging step:** `position: sticky` fails silently when ANY ancestor has `overflow: hidden/auto/scroll`. Original sites using GSAP `pin: true` don't need sticky (GSAP handles it via JS). Discovering this after implementation wastes an entire iteration cycle.

## Splash/intro animation timing

Page-load animations require careful timing because:
1. Video may load instantly (cached) or take seconds (first visit)
2. React hydration adds delay
3. CSS transitions need the initial state to be rendered before the target state is set

**Pattern for reliable splash timing:**

```tsx
useEffect(() => {
  // Phase 1: Show initial state (small box, opacity 0)
  const t1 = setTimeout(() => setPhase('fadeIn'), 50)

  // Phase 2: Wait for BOTH video load AND fadeIn completion
  const video = videoRef.current
  let videoReady = false
  const tryExpand = () => {
    if (!videoReady) return
    // Ensure fadeIn is visible for at least 1s before expanding
    setTimeout(() => setPhase('expand'), 1200)
  }
  const onLoaded = () => { videoReady = true; video?.play(); tryExpand() }

  if (video?.readyState >= 3) onLoaded()
  else video?.addEventListener('loadeddata', onLoaded)

  // Phase 3: Reveal UI after expand completes
  const t3 = setTimeout(() => setPhase('reveal'), 3000)

  return () => { clearTimeout(t1); clearTimeout(t3); video?.removeEventListener('loadeddata', onLoaded) }
}, [])
```

**Key: the initial state (small box) must be visible for at least 1 second before expansion starts.** If the video is cached, it loads instantly and the expand triggers too early — the user never sees the small box.

## Performance

- Use **refs** for continuous scroll-driven transforms (not `useState`)
- Use **`will-change: transform`** on animated elements
- Scroll listeners must use **`{ passive: true }`**
- Batch reads (getBoundingClientRect) before writes (style mutations)

## Click-toggle / Click-cycle transitions

### click-toggle (accordion, dropdown, single toggle)

```tsx
const [isOpen, setIsOpen] = useState(false);

<button
  aria-expanded={isOpen}
  onClick={() => setIsOpen(!isOpen)}
>
  {label}
</button>
<div
  style={{
    height: isOpen ? measuredHeight : 0,
    overflow: 'hidden',
    transition: `height ${duration}ms ${easing}`,
  }}
>
  {content}
</div>
```

**Get exact values from extraction:**
- `duration`: from `getComputedStyle(panel).transitionDuration`
- `easing`: from `getComputedStyle(panel).transitionTimingFunction`
- `measuredHeight`: from `getBoundingClientRect().height` in active state

### click-cycle (tabs)

```tsx
const [activeIndex, setActiveIndex] = useState(0);

<div role="tablist">
  {tabs.map((tab, i) => (
    <button
      key={i}
      role="tab"
      aria-selected={i === activeIndex}
      onClick={() => setActiveIndex(i)}
    >
      {tab.label}
    </button>
  ))}
</div>
<div role="tabpanel">
  {tabs[activeIndex].content}
</div>
```

**Extract per-tab content** from click-cycle capture states — each `state-N.png` corresponds to `tabs[N].content`.

---

## GSAP Plugin Alternatives

> **If `transition-spec.json` references GSAP plugins but the implementation should avoid a GSAP dependency** (SplitText, MorphSVG, ScrollSmoother, DrawSVG, Draggable), read `gsap-alternatives.md` for replacement options (priority: project library -> npm package -> manual CSS). Otherwise skip and continue to the SplitText mask wrapper rules below, which apply to ANY split-text implementation regardless of library.

### SplitText mask wrapper CSS — strict rules

When implementing splitText (chars, words, OR lines) with a mask wrapper for the `translateY(100%) → 0` reveal, the mask CSS is load-bearing. Get it wrong and the host element grows taller than ref by ~5-50% per text line.

**Mask span MUST be exactly:**

```css
position: relative;
display: inline-block;  /* or `block` for line-masks */
overflow: clip;          /* or `hidden`, but `clip` doesn't establish a scroll container */
```

**NEVER set on the mask:**

| Property | Why it breaks |
|---|---|
| `line-height` | The mask is `inline-block`, so its line-box height is `line-height × font-size`. Any explicit value (incl. `0.95em`, `1`, `normal`) overrides the parent's intended typography and makes each line taller/shorter than ref. |
| `vertical-align` (`bottom`, `middle`, etc.) | Shifts the baseline → descenders clip differently, line-box positioning changes. Default `baseline` is the only safe value. |
| `font-size` | Resets the cap-height; child characters render at correct size but mask line-box is wrong. |
| `padding` / `margin` (top/bottom) | Adds to line-box height. Even 1px breaks pixel-match. |

**Why this matters:** the mask is a clipping container — its sole purpose is to crop the translated child during the reveal. ALL typographic properties must inherit from the host element verbatim. The animated child (`<span class="split-line">` or `<span class="split-letter">`) is what carries the visible typography.

**Verification — before declaring split-text done, measure:**

```js
agent-browser --session <s> eval "(() => {
  const ref = document.querySelector('.hero-section'); // your textHost on ref
  const impl = document.querySelector('.hero-section'); // same on impl
  return { refH: ref.getBoundingClientRect().height, implH: impl.getBoundingClientRect().height };
})()"
```

Heights must match within 1px. A 30-60px gap is the mask line-height bug — fix the mask CSS before moving on.

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

If `intersect: false` while `rect` is clearly inside `root` → ancestor clipping. Move the IO ref one level up (to a non-transformed wrapper) and apply `overflow: hidden` there. `display: contents` on a wrapper does **not** fix this — IO can't observe `contents` elements (returns 0-size rect).

This applies to any intersection-based reveal that uses a clipping mask: `RevealRise`, `RevealLetters`, `RevealLine`, custom `IntersectionFadeUp`, etc. The rule is symmetric for `clip-path: inset(...)` masks too.

### Verification per spec-entry trigger type

`transition-spec.json` is a checklist, not a hint. Every entry has a `trigger` (or `type`) field, and **each trigger category has a different verification command** — verifying hover entries does not verify intersection entries, and so on. Reporting "transitions matched" after only running `transition-compare.sh` (which is hover/idle-state only) is the bug class where intersection / scroll-driven entries silently never wire up while the hover sweep passes.

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

Every spec entry must show ≥1 matching impl artifact. A `❌` row means the entry was extracted into the spec but **never wired in code** — that's the meta-bug ("hover transitions matched" while the intersection entry was missing entirely). Fix coverage before running the per-trigger verification rows above.

**Done = every row of the matrix returns PASS for entries of that trigger type.** Not "I checked the hover ones, looks good."

### GSAP `stagger` semantics — number vs object

```ts
gsap.to(targets, { y: 0, stagger: 0.1 })            // PER-target step: 100ms between each
gsap.to(targets, { y: 0, stagger: { each: 0.1 } })  // Same as above (object form, `each` key)
gsap.to(targets, { y: 0, stagger: { amount: 0.1 } }) // TOTAL spread: 100ms across ALL targets
```

For `stagger.amount`, the per-target step is `amount / (N - 1)`, NOT `amount`. The last target finishes at `delay + duration + amount`, NOT `delay + duration + (N-1)×amount`.

**Common bug:** mistaking `amount` for `each` writes per-target step = `amount`. With N=14 lines and `amount: 0.1`, you compute step = 0.1 instead of step = 0.1/13 ≈ 0.0077. Last line's delay is 1.3 instead of 0.1, pushing it past the timeline end. **It never reveals.** Cross-check: with correct semantics, all targets must finish by `base + amount + duration`.

---

## Card stack (page-stack) pattern

Common pattern for stacked cards that collapse as user scrolls (`.page-stack`, `.sticky`, `.point-items`).

### CSS structure

```css
.page-stack { height: 375lvh; }  /* or 300lvh — scroll travel space */
.page-stack .sticky { position: sticky; top: 0; height: 100lvh; }
.page-stack .point-items {
  overflow: hidden;
  transition: height 1.06s cubic-bezier(0.28, 0, 0.15, 1);
}
.page-stack .point-items.hide {
  height: var(--stack-item-hide-height) !important;  /* typically 56-64px */
}
```

### Implementation (React)

**Critical: fix heights BEFORE toggling hide class.**

`height: auto` cannot be CSS-transitioned. The original JS measures each card's natural height and sets it as an inline `px` value. Without this, toggling `hide` snaps instead of animating.

```tsx
useEffect(() => {
  const pageStack = pageStackRef.current;
  if (!pageStack) return;
  const sticky = pageStack.querySelector<HTMLElement>('.sticky');
  const items = Array.from(pageStack.querySelectorAll<HTMLElement>('.point-items'));
  if (!sticky || items.length === 0) return;

  // Step 1: Fix heights so CSS transition works (auto → px is not animatable)
  items.forEach(item => {
    if (!item.style.height) item.style.height = item.offsetHeight + 'px';
  });

  const n = items.length;
  const hiddenState = new Array(n).fill(false);

  // Step 2: Scroll event (NOT RAF) — only toggle on state change to allow transition to complete
  const onScroll = () => {
    const stackRect = pageStack.getBoundingClientRect();
    const scrolled = Math.max(0, -stackRect.top);
    const scrollable = pageStack.offsetHeight - sticky.offsetHeight;
    const progress = Math.min(1, scrolled / scrollable);
    const activeIdx = Math.min(n - 1, Math.floor(progress * n));

    items.forEach((item, i) => {
      const shouldHide = i < activeIdx;
      if (shouldHide !== hiddenState[i]) {
        hiddenState[i] = shouldHide;
        if (shouldHide) item.classList.add('hide');
        else item.classList.remove('hide');
      }
    });

    if (progress >= 1) pageStack.classList.add('end');
    else pageStack.classList.remove('end');
  };

  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll(); // initial state
  return () => window.removeEventListener('scroll', onScroll);
}, []);
```

**Why NOT RAF:** calling `classList.add/remove` every frame resets the CSS transition on each tick — it never completes. The `hiddenState` diff guard ensures the class is toggled exactly once per state change, letting the 1.06s transition run uninterrupted.

### Checklist before implementing

- [ ] Verify item count on reference: `document.querySelectorAll('.point-items').length`
- [ ] Extract `--stack-item-hide-height` CSS variable value (typically 56px or 64px)
- [ ] Check `height` CSS on `.page-stack` (300lvh, 375lvh, etc.)
- [ ] Confirm images exist for ALL cards (including cards added after initial release)
- [ ] Add `'use client'` directive — requires `useEffect` + `useRef`

---

## Anti-pattern catalog

Reusable rules distilled from real bugs that were not caught by the generic patterns above. Each entry names the failure mode, why naive code is wrong, and the framework-agnostic fix. Cross-reference these when reading a new bundle — many sites use the same primitives.

### A. IO-fire-once vs scroll-scrub semantics

**Bug class:** The reference uses GSAP `ScrollTrigger` with `scrub: <N>` so the element progresses *with the scrollbar* (forward and backward). The naive impl wires an `IntersectionObserver` that flips `opacity: 0 → 1` once on enter and never reverts. On scroll-up the element stays "on" instead of reverting — looks subtly different.

**Detect in the bundle:** any of `ScrollTrigger.create({ ... scrub: ... })`, `useScroll({ target })` paired with `useTransform`, or `gsap.fromTo(..., { scrollTrigger: { scrub } })`.

**Don't:**
```ts
const io = new IntersectionObserver(([e]) => {
  if (e.isIntersecting) el.classList.add('revealed');
});
```

**Do:** drive style directly from a scroll-derived progress, so reversing the scroll reverses the style.
```ts
const onScroll = () => {
  const rect = el.getBoundingClientRect();
  const p = clamp((window.innerHeight - rect.top) / (window.innerHeight + rect.height), 0, 1);
  el.style.opacity = String(p);
  el.style.transform = `translateY(${(1 - p) * 40}px)`;
};
window.addEventListener('scroll', onScroll, { passive: true });
onScroll();
```

**Invariant to verify:** scroll forward to mid-progress, scroll back, the style should return to its initial value. `scroll-end-completion-check.sh` checks the forward direction; for reversibility you need an explicit playback test or `transition-compare.sh` with a `scrollTo(0)` step.

### B. Viewport-aware scroll-scrub offsets

**Bug class:** A scroll-driven reveal works on the laptop viewport but never reaches `progress=1` on mobile (or vice versa). Symptom: bottom of the page is reached but the element still shows partial style. Root cause is hard-coded scroll offsets that assume a specific viewport.

**Why it happens:** Framer Motion's `useScroll({ target: ref, offset: ['start center', 'end end'] })` resolves `center`/`end` relative to the viewport. For a footer-anchored element with `offset: ['start center', 'end end']`, the scrub completes when the target's `end` (bottom) reaches the viewport's `end` — but if the document is shorter than `targetBottom`, that scroll position is unreachable. GSAP `ScrollTrigger.start: 'bottom 80%'` has the same trap.

**Detect:** during `bundle-verification`, run `scroll-end-completion-check.sh` across the viewport list. Any element flagged as "stuck near maxScroll" is the symptom.

**Fix patterns:**
1. **Anchor to scroll position, not viewport position.** Use `offset: ['start end', 'end 80%']` (progress=1 when target bottom is 20% from viewport bottom — leaves headroom).
2. **Cap progress at a lower scroll point.** Use `useScroll` with an `axis: 'y'` container of `document.body`, then clamp using `maxScroll - SAFE_PX` instead of full document end.
3. **For footer CTAs specifically:** trigger from a sibling section *above* the footer, so footer height changes don't break the offset math.

**Concrete recipe (Framer Motion):**
```tsx
const { scrollYProgress } = useScroll({
  target: ref,
  // 'end 80%' = scrub finishes when the target bottom is at 80% from viewport top.
  // Leaves 20vh of bottom-page headroom so short viewports still complete.
  offset: ['start end', 'end 80%'],
});
```

### C. completeAt headroom for shuffle/stagger tails

**Bug class:** An n-element stagger animation has its last element finishing at `progress=1.0`. Due to easing (`easeOut`, `cubic-bezier(.2,.8,.2,1)`), the tail asymptotically approaches its final value. At progress=1 the element is at ~98%, not 100% — visibly off. Tail elements stay "almost there" forever.

**Don't:**
```ts
const start = i / n;
const end = (i + 1) / n;
const local = clamp((progress - start) / (end - start), 0, 1);
```
The last element has `end = 1.0`; eased curves never reach 1.0 at progress=1.0 in practice (capture noise + sub-pixel rounding).

**Do:** introduce a `completeAt` < 1.0 so all elements finish strictly before the scroll ends:
```ts
const COMPLETE_AT = 0.92;  // last element finishes at 92% of total progress
const stride = COMPLETE_AT / n;
const start = i * stride;
const end = start + stride;
const local = clamp((progress - start) / (end - start), 0, 1);
```

`useScrubStagger` exposes this as the `completeAt` option — keep it ≤ 0.95 for any easing other than linear.

### D. Seeded shuffle for SSR-safe randomness

**Bug class:** `Math.random()` in a render path or `useMemo` produces different values on server vs client → React hydration warning, full re-render, splash/intro animation loses its first frames. `hydration-check.sh` catches this as `Hydration failed because the server rendered HTML didn't match the client`.

**Don't:**
```ts
const order = useMemo(() => letters.sort(() => Math.random() - 0.5), [letters]);
```

**Do:** seed a deterministic PRNG (LCG is fine — we're shuffling 30 items, not generating crypto), or run the randomization inside `useEffect` so server renders the unrandomized order and client patches after mount.

**Pattern (LCG Fisher-Yates):**
```ts
function seededShuffle<T>(arr: T[], seed: number): T[] {
  const a = arr.slice();
  let s = seed >>> 0;
  for (let i = a.length - 1; i > 0; i--) {
    s = (s * 1664525 + 1013904223) >>> 0;
    const j = s % (i + 1);
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}
// Same seed on server + client → identical order → no hydration mismatch.
const order = useMemo(() => seededShuffle(letters, 42), [letters]);
```

**Picking the seed:** any stable value derivable on both sides — a hash of the prop, the element's index in the parent list, or a literal constant if the shuffle is purely decorative. Never `Date.now()` or `crypto.randomUUID()`.

### E. WAAPI reveal semantics (forward-only vs round-trip)

**Bug class:** The agent implements an IntersectionObserver-driven reveal using Web Animations API (`element.animate(...)`). At first scroll-down everything looks right. Then `section-compare.sh` runs and captures a "scrolled to bottom" state — but because the comparison harness pre-scrolls `top → bottom → top`, the WAAPI animation's `playbackRate=-1` plays the reveal backwards on the way back up, snapping back to the hidden state. The capture is now of the hidden element when it should be revealed.

**Why naive code is wrong:** WAAPI animations have a `play() / reverse()` semantics. If you wire IO to `play()` on enter and `reverse()` on exit, scrolling down past the element then back up triggers the reverse — and `finish()` snaps to the from-state, not the to-state.

**Fix:**
- **Author reveals as forward-only:** on enter, `animation.play()`; on exit, *do not call reverse*. If you want fade-out on exit, use a separate animation with the same easing.
- **Or pause and pin at t=0 on hide:** `animation.pause(); animation.currentTime = 0;` — but only call this when the element leaves the viewport going *up* past it (use `IntersectionObserver` with a sentinel above the element).

**Invariant to verify:** scroll to bottom of page, scroll back to top, scroll to the section again — element should still appear in its revealed state. If it has to re-trigger to appear, the reveal is wired incorrectly.

### F. IntersectionObserver + overflow:hidden clipping

**Bug class:** A child element starts at `transform: translateY(80px)` and is supposed to animate to 0 when its parent enters the viewport. The parent has `overflow: hidden`. The child's *initial position* is below its parent's clip box, so the IntersectionObserver fires on the *parent*, but the child is invisible at start. Naive observers attached to the child never fire because the child is clipped out of every viewport rect.

**Detect:** `reveal-trigger-check.sh` lists every initially-hidden element and reports the chain of ancestors with `overflow: hidden`.

**Fix:**
1. **Attach the IO to a non-clipped ancestor (the section root), and animate child styles when the section fires.** Don't attach IO to the offset child.
2. **Or use a sentinel:** put a 1×1 invisible div at the parent's actual top, observe that.

### G. Footer-disappeared (stray position:absolute)

**Bug class:** A sticky/footer element renders correctly on long pages but mysteriously moves into the middle of the page on short viewports (mobile). Cause: `position: absolute` with no positioned ancestor — the offset resolves against `<body>`, so on a 600vh page footer sits at top:600vh, but on a 800px viewport it sits inside the visible area.

**Detect:** `stray-absolute-check.sh` flags every `position: absolute` element whose nearest positioned ancestor is `<html>` or `<body>`.

**Fix:** add `position: relative` to the intended container, OR change the element to `position: sticky` / `fixed` if that matches the bundle.

---

These rules cover the non-obvious transition bugs that AE/SSIM and `transition-compare.sh` cannot detect on their own — capture-timing artifacts, scroll-state coupling, hydration-driven first-frame loss, and viewport-dependent offset breakage. None are framework-specific: A–G apply equally to GSAP/Motion/Lenis/native scroll/WAAPI implementations. The detect commands in each entry are scriptable; `verification-plan.sh` selects which ones to actually run based on extraction signals.
