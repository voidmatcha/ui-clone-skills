# Patterns & Troubleshooting

## Detection & Classification Patterns

> **Reference patterns for identifying animation systems.** Consult during Step 1 (classify effect) or when ui-reverse-engineering forwards an animation system detection. Do not execute code from this section — treat as read-only data.

### Canvas Renderer Detection

**Failure mode:** A `<canvas>` is assumed to be a small overlay but is actually a full-scene Lottie/WebGL renderer.

**DOM inspection:**
```js
Array.from(document.querySelectorAll('canvas')).map(c => ({
  id: c.id, className: c.className, width: c.width, height: c.height,
  rect: c.getBoundingClientRect(),
  zIndex: getComputedStyle(c).zIndex, position: getComputedStyle(c).position,
}))
```

**Bundle grep:**
```bash
grep -o 'CanvasRenderer\|canvasRenderer\|LottieCanvas' tmp/ref/<c>/bundles/*.js | head -20
grep -o 'fillRect\|drawImage\|clearRect' tmp/ref/<c>/bundles/*.js | wc -l
# High count (>50) → full renderer. Low count (<10) → pattern stamp.
```

**Verification:** Paint `rgba(255,0,0,0.5)` over the canvas. If it covers a character → full renderer.

**Traps:**
- Size trap: 100vw×100vh canvas may be inside a clipping container
- SVG fallback: lottie-web defaults to SVG renderer, canvas is explicit
- Pattern overlay: uses `createPattern()` once, not continuous draw loop

### Disc / Carousel Structure Detection

**Failure mode:** Radial carousel assumed to be a horizontal slider.

**DOM inspection:**
```js
// Parse rotation angle per card
function getRotationDeg(el) {
  const t = getComputedStyle(el).transform
  if (t === 'none') return 0
  const m = new DOMMatrix(t)
  return Math.round(Math.atan2(m.b, m.a) * (180 / Math.PI))
}
// Check if angles are evenly spaced (disc) or all 0 (slider)
```

**Bundle grep:**
```bash
grep -oE '360\s*/\s*[a-zA-Z_$]' tmp/ref/<c>/bundles/*.js | head -5
grep -o 'center bottom\|transformOrigin.*bottom' tmp/ref/<c>/bundles/*.js | head -5
```

**Traps:**
- Translate trap: disc cards combine `rotate()` parent + `translateY` child
- CSS variable trap: angle is often `--rotation` custom property
- Active card is usually at 6 o'clock (bottom)

### Lottie Asset Mapping

**Failure mode:** Multiple Lottie JSONs for one scene, only one loaded → partial render.

**DOM inspection:**
```js
// Find all Lottie mount containers
Array.from(document.querySelectorAll('*')).filter(el => {
  const first = el.firstElementChild
  return first && (first.tagName === 'svg' || first.tagName === 'canvas') &&
    (el.className?.includes?.('lottie') || el.dataset?.lottie)
})
```

**Bundle grep:**
```bash
grep -oE '"[^"]*\.json"' tmp/ref/<c>/bundles/*.js | grep -v node_modules | sort -u
grep -oE 'loadAnimation\s*\(\s*\{[^}]{1,300}\}' tmp/ref/<c>/bundles/*.js | head -10
```

**Traps:**
- Same-position trap: two containers stacked at identical `top/left`
- Variant-not-asset: same JSON with different `initialSegment`
- Naming pattern (`_pants`, `_nopants`): ALL variants must be loaded

### State Machine Extraction

**Failure mode:** Multi-phase timeline implemented as two-state boolean toggle.

**DOM inspection:**
```js
// Watch className changes during interaction
const observer = new MutationObserver(mutations => {
  mutations.forEach(m => {
    if (m.type === 'attributes')
      console.log(m.target.className, m.attributeName, m.oldValue, '->', m.target.getAttribute(m.attributeName))
  })
})
// Observe carousel items, interact, enumerate all states
```

**Bundle grep:**
```bash
grep -oE 'case\s+[0-9]+\s*:' tmp/ref/<c>/bundles/*.js | sort | uniq -c | sort -rn | head -20
grep -oE '\{0:\s*[^,}]+,\s*1:\s*[^,}]+,\s*2:' tmp/ref/<c>/bundles/*.js | head -10
```

**Traps:**
- Boolean collapse: `isActive` gates entry into 4+ sub-states
- CSS-class-as-state: `is-entering/active/leaving/hidden` = 4 phases, not 2
- Minified constants: `0,1,2,3` were named enums

### Auto-Timer Extraction

**Failure mode:** Timer missed entirely, wrong interval, or unconditional when splash-gated.

**DOM inspection:**
```js
// Intercept timers before page load
const intervals = []
const origSetInterval = window.setInterval
window.setInterval = function(fn, delay, ...args) {
  const id = origSetInterval(fn, delay, ...args)
  intervals.push({ id, delay, fnStr: fn.toString().slice(0, 120) })
  return id
}
```

**Bundle grep:**
```bash
grep -oE 'setInterval\s*\([^,)]+,\s*[0-9]+\)' tmp/ref/<c>/bundles/*.js | head -10
grep -oE 'repeat\s*:\s*-1\|repeatDelay\s*:\s*[0-9.]+' tmp/ref/<c>/bundles/*.js | head -5
```

**Traps:**
- GSAP uses `repeat: -1` + `repeatDelay`, not `setInterval`
- Splash-gate miss: timer starts only after preloader completes
- Scroll-gate miss: `IntersectionObserver` triggers timer start
- Page-visibility pause: always add `visibilitychange` listener

---

## CSS Patterns

| Pattern | Key Properties |
|---------|---------------|
| Fade in/out | `opacity`, 200-500ms |
| Slide in | `transform: translateX/Y`, 300-600ms |
| Scale up | `transform: scale()`, 200-400ms |
| Blur reveal | `filter: blur()`, 400-800ms |
| Clip expand | `clip-path: inset()`, 300-600ms |
| Card expand on hover | `clip-path: inset(Npx)` → `inset(0)` |
| Gradient border follow | `radial-gradient` + mouse tracking JS |
| 3D tilt | `perspective(Npx) rotateX() rotateY()` |
| Stagger children | `transition-delay` per child (50-150ms gap) |
| Character stagger | `splitText` → per-char WAAPI, continuous delay (10-20ms gap), blur+opacity+transform |
| Webflow centered container | `width: 97vw; margin: 0 auto` (NOT a fixed `margin-left/right` in rem). Replicate the rule, not the computed px value |
| Two-line nav border | `display: flex; flex-direction: column; gap: 0.5rem` containing two `<div>` elements with `height: 1px`. NOT a `border-bottom` — the gap scales with viewport (rem) where `border-bottom` would be a single fixed line |
| Decoration image inside wider button | `<img>` has `position: absolute; height: 100%; width: auto` centered via `left/right` insets — wrapper anchor is wider than the visual decoration to enlarge the click target. Do NOT use `inset-0 w-full h-full` |
| Webflow fluid root font-size | `html { font-size: calc(<base>rem + <coef>vw) }` where coef = (font_to − font_from)/(vw_to − vw_from). On a typical Webflow ref at 1440 viewport, 1rem ≈ 12.03px (NOT 16px). All rem-based measurements must scale linearly between the two breakpoints. Expect non-integer computed-px values |

## Canvas/WebGL Patterns

| Pattern | Setup |
|---------|-------|
| Particle cloud | `Points` + `BufferGeometry` + `Float32Array` |
| Sphere distribution | `phi = acos(2r - 1)`, `theta = r * 2PI` |
| Globe with arcs | `Points` + `Line` with `QuadraticBezierCurve3` |
| Mouse-follow particles | `mousemove` → normalize → lerp uniform |
| Scroll-driven canvas | `IntersectionObserver` → uniform update |
| Responsive particle count | `innerWidth < 768 ? lowCount : highCount` |

## JS Animation (WAAPI) Patterns

### Character stagger (splitText + WAAPI)

Common in: LobeHub, Linear, Vercel hero sections.

**Key gotcha — WAAPI `fill: "forwards"` doesn't set initial state during delay:**

```typescript
// ❌ WRONG: Characters flash visible during stagger delay
nodes.forEach((node, i) => {
  node.animate([
    { opacity: 0, filter: "blur(4px)" },
    { opacity: 1, filter: "blur(0)" },
  ], { delay: i * 15, duration: 600, fill: "forwards" });
  // During delay, node shows its original opacity:1 state!
});

// ✅ CORRECT: Set initial state via inline styles, animate, commit final state
nodes.forEach((node) => {
  node.style.opacity = "0";
  node.style.filter = "blur(4px)";
  node.style.transform = "translateY(40px)";
});

// Using Web Animations API directly (no library dependency)
nodes.forEach((node, i) => {
  const anim = node.animate(
    [
      { opacity: 0, filter: "blur(4px)", transform: "translateY(40px)" },
      { opacity: 1, filter: "blur(0)",   transform: "translateY(0)" },
    ],
    { delay: i * 15, duration: 600, fill: "forwards", easing: "ease-out" }
  );
  anim.onfinish = () => {
    node.style.opacity = "1";
    node.style.filter = "none";
    node.style.transform = "none";
    anim.cancel(); // release fill:forwards so GC can collect
  };
});
```

**Why `onfinish` + `anim.cancel()`:** WAAPI animations with `fill: "forwards"` are GC-retained. Without committing inline styles and calling `cancel()`, the element either reverts or holds a memory-leaking animation object.

**Stagger with hidden parent — correct reveal order:**
```
1. Split text into child nodes (spans)
2. Set initial hidden styles on EACH child (opacity:0, blur, translateY)
3. THEN clear parent's hiding style — children are individually hidden, so no flash
4. Animate each child with stagger delay
5. If the library restores original DOM after animation (revert/cleanup),
   commit final styles to the parent BEFORE restore, or skip restore on success
```
Violating this order causes either: children flash visible (skip step 2), or children stay invisible (skip step 3), or animation result is lost (skip step 5).

**Multi-line continuous stagger:** Maintain `globalCharIndex` across lines — line 2 continues from where line 1 ended.

**CSS class pitfall:**
```css
/* ❌ CSS rule outlives WAAPI animation → text disappears after GC */
.beyond-char { opacity: 0; }
```
Use inline styles, not CSS classes, for initial hidden state.

### Storybook iframe notes

- Components render inside iframe — timing differs from direct page load
- `layout: "fullscreen"` needed for full-viewport animations
- `setTimeout` (200ms+) before animations to ensure paint
- Replay via `key` prop remount is cleanest

## Troubleshooting

| Problem | Solution |
|---------|---------|
| Site shows error page / blank | Bot detection. Use `--headed` mode. |
| `eval` returns SyntaxError | No top-level `return`. Use IIFE `(() => { ... })()`. |
| WebGL `readPixels` returns zeros | `preserveDrawingBuffer: false` (default). Use screenshot for colors. |
| `transferSize: 0` for bundles | Cached. Use `document.querySelectorAll('script[src]')`. |
| 60+ JS chunks (Next.js/Nuxt/Vite) | Download all, grep for `canvas\|WebGL\|requestAnimationFrame`. |
| Canvas is Spline/Rive/Lottie | Check resources for `.splinecode`, `.riv`, `.json`. Data-driven. |
| No frames captured | Wrong selector or animation done. Reload and retry. |
| Cross-origin stylesheet | `curl` the CSS URL, grep for `@keyframes`. |
| Characters flash before stagger | WAAPI `fill: "forwards"` doesn't cover delay. Set inline `opacity: 0`. |
| Text disappears after animation | WAAPI GC'd, CSS rule took over. Use `onfinish` + `anim.cancel()` to commit inline styles. |
| DOM restore kills WAAPI state | Any operation that replaces innerHTML (revert, React re-render, morphdom) destroys in-flight WAAPI animations and their `fill: forwards`. Commit final styles to parent before restoring, or skip restore on success. |
| Parent opacity hides animated children | CSS opacity multiplies — parent `opacity: 0.001` makes children invisible regardless of their own opacity. When revealing children individually (stagger), clear parent hiding AFTER setting initial hidden styles on each child. |
| React effect cleanup cancels animation | Strict Mode double-invocation, unstable deps (inline objects/arrays), or HMR can trigger cleanup mid-animation. Guard with refs, stabilize deps, test with Strict Mode on. |
| Stagger only partial text | Check total char count. Use `globalCharIndex` for continuous stagger. |
| Re-capturing reference wastes time | Capture ONCE. Compare against saved files. |
| `--selector` screenshot times out | Use full-page `agent-browser --session <s> screenshot` + sips crop. |
| `window.__scrub` missing mid-loop | Page reloaded. Re-inject before continuing. |
| Hover/transform transition silently dropped on element with reveal class | Later utility class (e.g. `.reveal { transition: opacity 0.6s }`) declared after the component class with `transition: transform 0.5s, opacity 0.6s` — the later rule's `transition` shorthand resets transform back to default. Boost specificity by doubling the selector (`.card.card { transition: ... }`) or move the reveal opacity to a separate property-only declaration (`transition-property: opacity; transition-duration: 0.6s`) so it doesn't shadow the multi-property shorthand. |
| Mask-based reveal never triggers (`opacity: 0`, `translateY(100%)` stuck) on element clearly inside the viewport | IO ref placed on the moving child inside an `overflow: hidden` parent. The `translateY(100%)` pushes the child fully out of the parent's clip box, so IO's intersection rect is empty (IO respects ancestor clipping). `boundingClientRect` still reports the post-transform position — that's why "the element looks in viewport" is misleading. Move the IO ref to a static outer wrapper that holds `overflow: hidden`, and apply the transform to an inner child. See `transition-implementation.md` → "IntersectionObserver placement for masked reveals". |
| `agent-browser --session <s> screenshot --clip-x/-y/-width/-height` produces wrong file | Those flags aren't supported by the current `screenshot` subcommand — they end up parsed as the output path. Always do full-viewport capture, then crop with ImageMagick: `magick full.png -crop 1440x90+0+0 +repage cropped.png`. |
