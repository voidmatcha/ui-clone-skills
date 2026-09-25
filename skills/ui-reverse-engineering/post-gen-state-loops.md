# Post-Generation State Loops — Step 7 Reference

Conditional verification loops split out of `post-gen-verification.md`. Read
only the loop whose artifact or signal is present; Loop 0 (60fps A/B), the
silent-failure checks, and Loops 5/6 stay in `post-gen-verification.md`.

| Section | Read when |
|---|---|
| Loop 0.5: State coupling | the impl has carousels, tabs, or accordions (`state-coupling.json` exists or should) |
| Loop 1: Section bounds | a section carries a fixed `style={{ height: N }}` and its height is under suspicion |
| Loop 2: Sticky lock point | `sticky-elements.json` has entries |
| Loop 3: Body state transition | `body-state.json` has body class rules |
| Loop 4: Hover transition | `hover-deltas.json` exists |
| [Animation library wiring](#animation-library--wiring-pattern-mapping) | Step 6 bundle analysis detected an animation library and you need the per-library wiring pattern |

## Loop 0.5: State Coupling Verification (MANDATORY for carousels/tabs)

Verify that ALL coupled elements update when shared state changes.

### Step 1: Load `state-coupling.json`
If it doesn't exist, create it now by clicking through each state on the ref and noting what changes.

### Step 2: For each state transition, verify ALL coupled elements update

```bash
# On impl: click carousel arrow, then immediately check all coupled elements
agent-browser --session <s> eval "
(() => {
  // Click arrow
  document.querySelectorAll('[class*=\"carousel-control\"] button')[1]?.click();

  // Wait 1s for animations
  return new Promise(resolve => setTimeout(() => {
    // Check each coupled element
    const results = {
      sectionBg: document.querySelector('section[class*=\"carousel\"]')?.style.backgroundColor,
      cardText: document.querySelector('.card [style*=\"opacity: 1\"] h3')?.textContent,
      serviceBg: document.querySelector('[class*=\"programs-\"][class*=\"-bg\"]')?.style.backgroundColor,
      illustRotation: /* rotation wrapper transform */,
    };
    resolve(results);
  }, 1000));
})()
"
```

### Step 3: Compare against ref at same state
If ANY coupled element didn't update → fix the `goTo()` function. Typical misses: carousel rotates but card background stays; card text updates but illustration doesn't rotate; background color changes but a secondary section bg stays stale; Lottie SVG swapped to the wrong asset.

### Step 4: Verify auto-timer doesn't conflict with splash
```bash
# Record first 8 seconds. If carousel rotates during splash overlay → bug.
agent-browser --session <s> record start tmp/ref/<c>/splash-timer-check.webm
sleep 8
agent-browser --session <s> record stop
# Extract frames and check: is splash visible in any frame where illustration has rotated?
```

---

## Loop 1: Section bounds diagnosis

For every section with fixed height (e.g., `style={{ height: N }}`):

```bash
agent-browser --session <s> eval "(() => {
  const sections = document.querySelectorAll('section[style*=height], [style*=height]');
  const results = [];
  for (const s of sections) {
    const sr = s.getBoundingClientRect();
    const imgs = [...s.querySelectorAll('img')];
    const last = imgs.length ? imgs[imgs.length-1] : null;
    const lr = last?.getBoundingClientRect();
    if (lr) results.push({
      id: s.id || s.className.slice(0,40),
      sectionH: Math.round(sr.height),
      lastContentBottom: Math.round(lr.bottom - sr.top),
      waste: Math.round(sr.height - (lr.bottom - sr.top)),
    });
  }
  return JSON.stringify(results, null, 2);
})()"
```

This probe is diagnostic only: the last image is not necessarily the last content,
and remaining space may be authored padding or scroll distance. Compare against
the same reference section/state, inspect missing nodes and pin lifecycle, and
restore the source sizing rule. There is no universal whitespace threshold and no
fixed trailing-space correction.

## Loop 2: Sticky lock point verification

For every sticky element in `sticky-elements.json`:

```bash
agent-browser --session <s> eval "(() => {
  const results = [];
  for (let y = 0; y <= document.documentElement.scrollHeight; y += 200) {
    window.scrollTo(0, y);
    const title = document.querySelector('<sticky-selector>');
    if (!title) continue;
    const tr = title.getBoundingClientRect();
    const tc = tr.top + tr.height / 2;
    const lastImg = document.querySelector('<last-content-selector>');
    if (!lastImg) continue;
    const lr = lastImg.getBoundingClientRect();
    const lc = lr.top + lr.height / 2;
    const sticky = tr.top > 50 && tr.top < 500;
    if (!sticky && results.length > 0 && results[results.length-1].sticky) {
      results.push({ y, diff: Math.round(lc - tc), sticky, note: 'UNSTICK POINT' });
    } else if (sticky) {
      results.push({ y, diff: Math.round(lc - tc), sticky });
    }
  }
  return JSON.stringify(results.slice(-5), null, 2);
})()"
```

**Gate:** at unstick, `|diff| < 15px`. Adjust wrapper height:
- `diff > 0` → wrapper too short, increase by `diff`
- `diff < 0` → wrapper too long, decrease by `|diff|`

Re-run until `|diff| < 15`.

## Loop 3: Body state transition verification

If `body-state.json` has body class rules:

1. Scroll to position where class should be active
2. Check `document.body.className` contains expected class
3. Check CSS cascade produces expected values (nav color, logo filter, bg-color)
4. Scroll back → verify class removed + values reverted

### Body-state implementation pattern

When `body-state.json` has rules, implement this exact pattern:

**globals.css:**
```css
body { transition: background-color 0.8s; }
body.<active-class> { background-color: <extracted-value>; }
body.<active-class> #main-nav { background-color: <extracted-value>; }
body.<active-class> .nav-logo { filter: brightness(0) invert(1); }
body.<active-class> .nav-link { color: <extracted-value>; }
```

**Scroll handler (component owning the transition):**
```tsx
useEffect(() => {
  const handleScroll = () => {
    const isActive = /* scroll condition from extracted data */;
    document.body.classList.toggle('<active-class>', isActive);
  };
  window.addEventListener('scroll', handleScroll, { passive: true });
  return () => {
    window.removeEventListener('scroll', handleScroll);
    document.body.classList.remove('<active-class>');
  };
}, []);
```

**Why CSS cascade, not React state:** a single body-class toggle coordinated by CSS rules is simpler, avoids prop drilling, and matches the original site's architecture. Do NOT replicate with per-component `isDark` state + conditional classNames on every element.

## Loop 4: Hover transition verification (MANDATORY if hover-deltas.json exists)

Hover effects are the most commonly "approximately close but wrong" part of clones. Hover on both original and implementation, measure the same properties, compare.

### Step 1: For each element in hover-deltas.json

```bash
# On ORIGINAL site:
agent-browser --session <s> open <original-url>
# scroll to element
agent-browser --session <s> hover "<selector>"
# wait for transition
agent-browser --session <s> eval "(() => {
  const el = document.querySelector('<selector>');
  const s = getComputedStyle(el);
  // Capture all visual properties
  return JSON.stringify({
    transform: s.transform,
    opacity: s.opacity,
    scale: s.scale,
    backgroundColor: s.backgroundColor,
    color: s.color,
    boxShadow: s.boxShadow,
    borderColor: s.borderColor,
    filter: s.filter,
    clipPath: s.clipPath,
  });
})()"
```

### Step 2: Same measurement on implementation

```bash
agent-browser --session <s> open <impl-url>
agent-browser --session <s> hover "<selector>"
agent-browser --session <s> eval "/* same property extraction */"
```

### Step 3: Compare

For each property in the delta:
```
□ Property changes in SAME direction (scale up vs scale down)
□ End value matches within 2% tolerance
□ Duration is within ±100ms
□ Easing curve SHAPE matches (bounce vs linear vs ease-out)
□ Child elements that should ALSO change are changing
```

**Common hover mismatches:**

| Symptom | Root cause |
|---|---|
| Hover works but feels "flat" | Missing easing — using `ease` instead of `cubic-bezier(0.625, 0.05, 0, 1)` |
| Hover effect instant (no transition) | CSS `transition` property missing or overridden by Tailwind reset |
| Hover shows wrong element | `display: none → block` controlled by JS, not CSS `:hover` |
| Image zooms differently | Original uses GSAP `scale: 1.05` with custom ease, impl uses CSS `hover:scale-105` with default ease |
| Text split-hover broken | Original uses GSAP SplitText per-character stagger on hover, impl does whole-block transition |
| Hover doesn't revert smoothly | `mouseleave` transition missing — GSAP has separate `leave` tween |
| Hover-OUT snaps instead of animating | CSS animated properties (`clip-path`, `scale`, `transform`) have no initial value in idle state — browser can't interpolate from `none` to `inset(...)`. Fix: add explicit initial values. Check `::after` pseudo-elements too |

### Step 4: Fix and re-verify

After fixing, re-hover on both and confirm the delta matches. Maximum 3 iterations.

## Animation library → wiring pattern mapping

When Step 6 bundle analysis detects an animation library, use these patterns:

### Scroll-driven parallax

| Library | Pattern |
|---|---|
| GSAP + ScrollTrigger | `gsap.to(el, { y: offset, scrollTrigger: { trigger, scrub: true } })` |
| Framer Motion | `useScroll({ target }) + useTransform(scrollYProgress, [0,1], [startY, endY])` → `style={{ y: transformValue }}` |
| Lenis / custom lerp | Subscribe to scroll position callback → compute offset in rAF → set `el.style.transform` directly |
| No library (CSS-only) | `IntersectionObserver` + CSS custom property `--scroll-progress` |

### Scroll-trigger reveal

| Library | Pattern |
|---|---|
| GSAP | `ScrollTrigger.create({ trigger, onEnter: () => gsap.to(el, { opacity:1, y:0 }) })` |
| Framer Motion | `useInView(ref) + animate={{ opacity: inView ? 1 : 0, y: inView ? 0 : 60 }}` |
| Lenis / custom lerp | Subscribe to scroll MotionValue → `getBoundingClientRect()` in rAF → style when in viewport |
| No library | `IntersectionObserver` + CSS transition class toggle |

### Hover / click state

| Library | Pattern |
|---|---|
| Framer Motion | `whileHover={{ scale: 1.05 }}` or `variants` + `AnimatePresence` |
| GSAP | `el.addEventListener('mouseenter', () => gsap.to(el, { scale: 1.05 }))` |
| CSS-only | `transition` + `:hover` or `group-hover:` Tailwind |

### SVG / DOM child staggered animation

When bundle shows `.fromTo(".selector > *", ...)` with `stagger`:

```tsx
// SVG children animate individually — NEVER translate parent
useEffect(() => {
  const svg = svgRef.current
  if (!svg) return
  const children = Array.from(svg.children) as SVGElement[]
  const offset = svg.getBoundingClientRect().height * 2

  children.forEach(child => {
    child.style.transform = `translateY(${offset}px)`
    child.style.willChange = 'transform'
  })

  const timer = setTimeout(() => {
    children.forEach((child, i) => {
      child.style.transition = `transform 1s cubic-bezier(...) ${i * stagger}s`
      child.style.transform = 'translateY(0)'
    })
  }, delay)

  return () => clearTimeout(timer)
}, [])
```

**When:** bundle contains `> *`, `.children`, or `stagger` on children. Common for logo assembly, icon reveals, grid card entrances, text character animations. **Never** translate the parent when the bundle animates children individually.

### Custom scroll engine — architectural insight

If the site uses `overflow: hidden` + `translate3d` wrapper:

- Standard `IntersectionObserver` will NOT fire — elements don't actually scroll in the DOM
- Must subscribe to the scroll engine's value stream (MotionValue, event emitter, callback)
- `getBoundingClientRect()` returns correct values (browser accounts for transforms)
- Pattern: `scrollValue.on('change', () => requestAnimationFrame(() => { const rect = el.getBoundingClientRect(); /* visibility check */ }))`

---

After this step, return to `post-gen-verification.md` (Step 7 follow-up) and continue with Loop 5.
