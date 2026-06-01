# Post-Generation Verification — Step 7 Follow-up

## Pre-verification checklist

### Check Next.js build errors first

Verify server status before starting visual-debug:

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/
```

If a screenshot shows a Next.js error overlay, check the error file path and inspect JSX tag balance:

```bash
grep -n "<section\|</section\|<div\|</div" <file-path> | head -30
```

**Root cause:** A global Next.js build error blocks ALL pages with the error overlay — the page under comparison may not be the one with the error.

---

### On AE FAIL — read the diff image first

High AE does not necessarily mean layout mismatch. Do not auto-judge `AE > 500 → FAIL`.

Read the diff image and determine whether pixel differences are in **image regions** or **layout regions**:

- **Image region differences** = live thumbnails, streaming content, dynamic media. Unrelated to layout quality → **treat as PASS**
- **Layout region differences** (header, navbar, section titles, margins) = actual layout mismatch → **fix required**

**Live service note:** Streaming CDN URLs (live-broadcast thumbnail hosts, time-windowed signed asset URLs) change every minute. Hardcoded URLs will always differ from current ones. This is NOT a layout issue.

---

Run BEFORE visual verification. Catches layout and behavior errors that screenshots miss.

## Font verification (MANDATORY — run before any visual comparison)

Font mismatch is the #1 repeated failure. Run this check immediately after generation, before any screenshot comparison:

```bash
# On IMPL — check if custom fonts are actually rendering (not falling back to system)
agent-browser --session <impl> eval "(() => {
  const body = getComputedStyle(document.body);
  const bodyFont = body.fontFamily;
  const results = [];

  // Check key text elements
  ['h1','h2','h3','p','a','button','nav'].forEach(tag => {
    const el = document.querySelector(tag);
    if (!el || !el.offsetHeight) return;
    const computed = getComputedStyle(el).fontFamily;
    const usingBodyFont = computed === bodyFont;
    results.push({ tag, font: computed.split(',')[0].trim(), fallback: usingBodyFont });
  });

  // Check document.fonts load status
  const fontStatus = [];
  document.fonts.forEach(f => {
    fontStatus.push({ family: f.family, status: f.status });
  });
  const loaded = fontStatus.filter(f => f.status === 'loaded').length;
  const total = fontStatus.length;

  return JSON.stringify({ bodyFont: bodyFont.split(',')[0], elements: results, fontsLoaded: loaded, fontsTotal: total });
})()"
```

**Diagnosis table:**

| Symptom | Cause | Fix |
|---|---|---|
| All elements show same `bodyFont` | Custom font classes not applied | Check Tailwind class: `font-[var(--x)]` broken in v4 → use `@theme` + `font-<name>` |
| `fontsLoaded: 0` | @font-face registered but no font file loaded | Check font file paths in `public/fonts/`, verify `@font-face src url()` is correct |
| `fontsLoaded > 0` but wrong family on elements | Font loaded but CSS selector not reaching element | Body scoping: copy `font-family` to `[data-project]` selector, not just `body {}` |
| Font loads on ref but not impl | CORS-blocked CDN font | Download font file, self-host in `public/fonts/`, update `@font-face` |
| `@theme` font silently ignored | Embedded project: `@theme` only works in file with `@import "tailwindcss"` | Use plain CSS vars on `[data-project]` scope |
| Text on impl looks "thinner / lighter" but font-family, size, weight all match ref | `[data-project] { -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility }` was added to host CSS, but ref uses browser defaults (`auto` / `auto`). On macOS Retina, `antialiased` is grayscale-AA — visibly *lighter* than ref's subpixel rendering. | Compare BOTH properties on ref body: `getComputedStyle(document.body)` → `webkitFontSmoothing` and `textRendering`. If ref returns `auto`/`auto`, remove these declarations from impl host CSS. Never set `antialiased` reflexively — match the ref. |

⛔ **Gate:** If `fontsLoaded: 0` or all elements show body fallback font, fix before proceeding to visual comparison — every screenshot will be wrong otherwise.

⛔ **Gate (rendering parity):** Run on BOTH ref and impl: `getComputedStyle(document.body).webkitFontSmoothing` + `.textRendering`. If they don't match, fix before pixel comparison — the entire page renders at a different perceived weight otherwise, and AE/SSIM diffs will be noisy across every text region.

## Silent failure checks (MANDATORY — run after font verification)

These are problems that produce no errors but cause every screenshot comparison to fail:

### 1. Video backgrounds rendered as static images

```bash
# Check if ref has <video> but impl has <img> for the same section
agent-browser --session <ref> eval "(() => {
  return JSON.stringify([...document.querySelectorAll('video')].map(v => ({
    src: v.currentSrc || v.src,
    parent: v.parentElement?.className?.split(' ')[0] || v.parentElement?.tagName
  })));
})()"

# Then verify impl has matching <video> tags, not <img>
agent-browser --session <impl> eval "(() => {
  const videos = document.querySelectorAll('video');
  const imgs = document.querySelectorAll('img[src*=video], img[src*=mp4], img[src*=webm]');
  return JSON.stringify({ videoCount: videos.length, suspiciousImgs: imgs.length });
})()"
```

⛔ If ref has videos but impl has 0 → fix before visual comparison.

### 2. CSS variables undefined

```bash
# Check for undefined CSS variables in impl
agent-browser --session <impl> eval "(() => {
  const root = getComputedStyle(document.documentElement);
  const body = getComputedStyle(document.body);
  const vars = [];
  for (const sheet of document.styleSheets) {
    try {
      for (const rule of sheet.cssRules) {
        const matches = rule.cssText.match(/var\(--[^)]+\)/g) || [];
        matches.forEach(m => {
          const name = m.match(/var\((--[^,)]+)/)?.[1];
          if (name) {
            const val = root.getPropertyValue(name).trim() || body.getPropertyValue(name).trim();
            if (!val) vars.push(name);
          }
        });
      }
    } catch(e) {}
  }
  return JSON.stringify([...new Set(vars)].slice(0, 20));
})()"
```

⛔ Undefined variables cause wrong colors, sizes, positions with no error.

### 3. JS bundle init failure (animations broken)

```bash
# Check if the site's JS bundle loaded and initialized
agent-browser --session <impl> eval "(() => {
  const checks = {
    gsap: typeof gsap !== 'undefined',
    lenis: typeof Lenis !== 'undefined' || !!document.querySelector('.lenis'),
    scrollTrigger: typeof ScrollTrigger !== 'undefined',
    animations: document.getAnimations().length
  };
  return JSON.stringify(checks);
})()"
```

If ref has GSAP/Lenis but impl shows all false → the bundle script tag is missing from `layout.tsx`, or a renamed class broke `querySelector`. See `diagnosis.md` Root Cause F.

## Loop 0: Original A/B comparison at 60fps (MANDATORY for animated components)

**The ONLY reliable way to verify animations.** Checking that values change in your impl proves nothing — you must compare AGAINST THE ORIGINAL at the same resolution. Without this, you WILL ship wrong easing, wrong axis, wrong direction, wrong timing.

**Failure mode this prevents:** You extract `clipPath: inset(0% 0% X% 0%)` (bottom clips upward), but implement `clipPath: inset(0% X% 0% 0%)` (right clips leftward). Both "animate clipPath from 0% to 5%". Both "work". Self-verification says "clipPath changes — correct!" A/B comparison instantly shows the mismatch.

### Step 1: Capture original at 60fps
Use the `agent-browser` 60fps rAF capture from `animation-detection.md` Tier 2, pointing to the original URL. Save to `tmp/ref/<component>/original-60fps.json`.

### Step 2: Capture implementation at 60fps
Same rAF capture pointing to `localhost:<port>`. Save to `tmp/ref/<component>/impl-60fps.json`.

### Step 3: Diff key properties at matching timestamps (±50ms tolerance)

**Every animated property must pass ALL 5 checks:**

```
□ DIRECTION: Which value in clipPath/transform is changing?
  Original: inset(0% 0% X% 0%) → 3rd value (bottom)
  Impl:     inset(0% X% 0% 0%) → 2nd value (right)  ← WRONG AXIS

□ RANGE: Start and end values?
  Original: opacity 1 → 0.02 (never fully 0)
  Impl:     opacity 1 → 0      ← check if intentional

□ TIMING: Transition start + end?
  Original: starts t=2111ms, reaches 3.67% at t=2611ms
  Impl:     starts t=1260ms, reaches 0% at t=1693ms  ← 850ms too early

□ EASING: Interpolation curve shape?
  Original: values at 25%/50%/75% progress → power5 (fast start, slow end)
  Impl:     values show linear or wrong easing

□ COUPLING: Which properties animate together?
  Original: clipPath + text reveal start at same time
  Impl:     clipPath starts 800ms before text  ← desynced
```

**Gate:** ANY check fails → fix before proceeding. Do NOT rationalize ("close enough", "similar feel"). Original values are the spec — match them or document the deviation.

**Anti-patterns this catches:**
- Wrong clipPath axis (right vs bottom vs top)
- Inventing animations that don't exist (e.g., "logo shrinks and moves up" when logo is static)
- Wrong easing (200ms polling looked linear)
- Desynced phases (splash/text/image should be coupled but aren't)
- Attributing GSAP's `transform: matrix(...)` init as an animation when it's just setup

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
If ANY coupled element didn't update → fix the `goTo()` function.

**Failure modes this prevents:**
- Carousel rotates but card background stays green (missing bg coupling)
- Card text updates but illustration doesn't rotate (missing disc rotation)
- Background color changes but service section bg stays stale (missing secondary color)
- Lottie SVGs replaced with wrong asset (`kid_flower_pants` → `kid_flower_nopants`)

### Step 4: Verify auto-timer doesn't conflict with splash
```bash
# Record first 8 seconds. If carousel rotates during splash overlay → bug.
agent-browser --session <s> record start tmp/ref/<c>/splash-timer-check.webm
sleep 8
agent-browser --session <s> record stop
# Extract frames and check: is splash visible in any frame where illustration has rotated?
```

---

## Loop 1: Section height verification

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

**Gate:** every section `waste < 100`. If `waste > 100`, reduce section height to `lastContentBottom + 65`.

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

Hover effects are the most commonly "approximately close but wrong" part of clones. The fix is simple: hover on both original and implementation, measure the same properties, compare.

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

## Loop 5: Section-Level Visual Comparison (MANDATORY)

**Replaces full-page scroll screenshots.** Full-page comparisons are noisy — scroll alignment drifts cause every position to fail even when sections are correct. Section-level cropping eliminates this.

### Run

```bash
SCRIPTS_DIR="${PLUGIN_ROOT:+$PLUGIN_ROOT/skills/visual-debug/scripts}"
SCRIPTS_DIR="${SCRIPTS_DIR:-${CODEX_PLUGIN_ROOT:+$CODEX_PLUGIN_ROOT/skills/visual-debug/scripts}}"
SCRIPTS_DIR="${SCRIPTS_DIR:-${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/visual-debug/scripts}}"
SCRIPTS_DIR="${SCRIPTS_DIR:-${VISUAL_DEBUG_SCRIPTS_DIR:-}}"
if [ -z "$SCRIPTS_DIR" ]; then
  for root in "${UI_CLONE_ROOT:-}" "$PWD" "$PWD/.." "$PWD/../.." "${INSTALL_DIR:-$HOME/.local/share/ui-clone-skills}"; do
    [ -n "$root" ] && [ -f "$root/skills/visual-debug/scripts/section-compare.sh" ] && SCRIPTS_DIR=$(cd "$root/skills/visual-debug/scripts" && pwd) && break
  done
fi
[ -n "$SCRIPTS_DIR" ] || { echo "Set VISUAL_DEBUG_SCRIPTS_DIR or PLUGIN_ROOT" >&2; exit 1; }
bash "$SCRIPTS_DIR/section-compare.sh" <original-url> <impl-url> <session> tmp/ref/<component>
```

### What it does

1. Enumerates `<section>`, `<header>`, `<footer>`, `<main>` on both sites
2. Matches sections by text fingerprint similarity
3. Crops element-level screenshots per section (no scroll alignment dependency)
4. Runs AE comparison per section
5. Detects structural mismatches:
   - **SVG_TEXT_MISSING**: ref has SVG text paths, impl uses HTML text (wrong rendering)
   - **LAYOUT_MISMATCH**: ref uses grid, impl uses flex (or vice versa)
   - **HEIGHT_MISMATCH**: section height ratio >30% off
   - **CHILD_COUNT_MISMATCH**: significantly different DOM structure

### Gate

- ALL matched sections must PASS AE (threshold: 2000 per section)
- ALL structural diffs must be resolved or documented
- `SVG_TEXT_MISSING` is a **hard blocker** — copy the SVG outerHTML, never approximate

### When it catches things visual-debug misses

| Scenario | Full-page scroll | Section-level |
|---|---|---|
| "MORE THAN JUST GOLF" is SVG in ref, `<h2>` in impl | Fails at 60% (noisy, mixed with scroll offset) | Fails specifically on `community-section` with `SVG_TEXT_MISSING` |
| Product grid shows text in impl, not in ref | Hidden in noise at 20% | Clear: section height + child count mismatch |
| Footer matches perfectly | Fails at 90% due to scroll drift | PASS — compared in isolation |

---

## Loop 6: Transition Comparison (MANDATORY if interactions-detected.json exists)

**Compares hover/transition behavior element-by-element.** Not just "does hover work?" but "does hover produce the same visual result?"

### Run

```bash
SCRIPTS_DIR="${PLUGIN_ROOT:+$PLUGIN_ROOT/skills/visual-debug/scripts}"
SCRIPTS_DIR="${SCRIPTS_DIR:-${CODEX_PLUGIN_ROOT:+$CODEX_PLUGIN_ROOT/skills/visual-debug/scripts}}"
SCRIPTS_DIR="${SCRIPTS_DIR:-${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/visual-debug/scripts}}"
SCRIPTS_DIR="${SCRIPTS_DIR:-${VISUAL_DEBUG_SCRIPTS_DIR:-}}"
if [ -z "$SCRIPTS_DIR" ]; then
  for root in "${UI_CLONE_ROOT:-}" "$PWD" "$PWD/.." "$PWD/../.." "${INSTALL_DIR:-$HOME/.local/share/ui-clone-skills}"; do
    [ -n "$root" ] && [ -f "$root/skills/visual-debug/scripts/transition-compare.sh" ] && SCRIPTS_DIR=$(cd "$root/skills/visual-debug/scripts" && pwd) && break
  done
fi
[ -n "$SCRIPTS_DIR" ] || { echo "Set VISUAL_DEBUG_SCRIPTS_DIR or PLUGIN_ROOT" >&2; exit 1; }
bash "$SCRIPTS_DIR/transition-compare.sh" <original-url> <impl-url> <session> tmp/ref/<component>
```

### What it does

1. Finds all elements with `transition-duration !== 0s` on both sites
2. For each element:
   - Captures idle state (screenshot + computedStyle)
   - Dispatches `mouseenter`/`mouseover` to trigger hover
   - Captures hover state (screenshot + computedStyle)
   - Dispatches `mouseleave` to reset
3. Compares:
   - **Idle style**: opacity, transform, colors match at rest
   - **Hover style**: same properties change in the same direction
   - **Timing**: duration, easing function
   - **Missing transitions**: property changes on hover in ref but not in impl

### Output

```json
{
  "selector": ".product-card-img",
  "status": "FAIL",
  "issues": [
    "EASING_MISMATCH: prop=transform ref=cubic-bezier(0.32, 0.72, 0, 1) impl=ease",
    "HOVER_TRANSFORM_NOT_APPLIED: ref changes transform on hover, impl stays same"
  ]
}
```

### Gate

- ALL elements with transitions in ref must have matching transitions in impl
- Timing is compared **per property**: each property the ref animates must have a
  matching duration + easing in the impl (or be covered by an impl `transition: all`).
  Extra inert properties the ref does not animate (e.g. an added `transform`) are
  ignored, but a property the ref animates but the impl omits is a `MISSING_TRANSITION`.
- `HOVER_*_NOT_APPLIED` is a **hard blocker** — the effect is missing entirely
- `EASING_MISMATCH`, `DURATION_MISMATCH`, and `MISSING_TRANSITION` must be fixed to match ref values

### Why this catches what Loop 4 misses

Loop 4 (hover verification) requires manual selector lists from `hover-deltas.json`. This loop **auto-detects** all transition elements and compares them exhaustively. No manual enumeration needed.
