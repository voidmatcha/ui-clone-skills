# Interaction Detection — Step 5

> All `agent-browser eval` calls must use IIFE: `(() => { ... })()` — no top-level return.
>
> **After this step:** proceed to Step 5b (capture C3 if new interactions found), then `bundle-analysis.md` (Step 5c-a).

## Step 5: Detect Interactions

> **Replace `.target` in all evals below** with the actual selector from Step 2.

### Classify interaction type

```bash
agent-browser --session <s> eval "
(() => {
  const el = document.querySelector('.target');
  if (!el) return JSON.stringify({ error: 'selector not found' });
  const s = getComputedStyle(el);
  return JSON.stringify({
    hasTransition: s.transitionDuration !== '0s',
    transition: s.transition,
    hasAnimation: s.animationName !== 'none',
    animation: s.animationName,
    canvases: document.querySelectorAll('canvas').length,
    willChange: s.willChange,
  });
})()
"
```

| Signal | Next step |
|--------|-----------|
| `hasTransition: true` | Capture hover states — see below |
| `hasAnimation: true` | Extract keyframes — see below |
| `canvases > 0` | **Run transition extraction pipeline** → `canvas-webgl-extraction.md` |
| `hasAnimation: true` (complex keyframes) | **Run transition extraction pipeline** → `css-extraction.md` |
| Scroll-triggered transitions | Detect below, then **run transition extraction pipeline** → `js-animation-extraction.md` |
| GSAP/Framer detected in bundle | **Run transition extraction pipeline** → `js-animation-extraction.md` |
| Scroll library detected (Lenis/GSAP/Locomotive) | **Run transition extraction pipeline** → `js-animation-extraction.md` |
| Splash/intro timeline | **Run transition extraction pipeline** → `splash-extraction.md` |
| `click-toggle` detected | Implement with React state + CSS transition |
| `click-cycle` detected  | Implement with React state array + activeIndex |
| Complex JS interactions | Step 6: Bundle analysis → `bundle-analysis.md` |

### Detect scroll-triggered transitions

```bash
# 1. Set up recorder before scrolling
agent-browser --session <s> eval "
(() => {
  window.__scrollTransitions = [];
  const candidates = document.querySelectorAll('[class*=fade], [class*=slide], [class*=reveal], [class*=animate], [data-aos]');
  const allEls = candidates.length > 0 ? candidates : document.querySelectorAll('section, h1, h2, h3, p, img, .card');
  const props = ['opacity', 'transform', 'filter', 'clipPath'];
  Array.from(allEls).slice(0, 30).forEach((el, i) => {
    const before = {};
    props.forEach(p => before[p] = getComputedStyle(el)[p]);
    const observer = new IntersectionObserver(entries => {
      entries.forEach(e => {
        const after = {};
        props.forEach(p => after[p] = getComputedStyle(e.target)[p]);
        const changed = props.filter(p => after[p] !== before[p]);
        if (changed.length > 0) {
          window.__scrollTransitions.push({
            index: i,
            selector: (() => { const cn = typeof el.className === 'string' ? el.className : el.className?.baseVal || ''; const first = cn.trim().split(' ')[0]?.replace(/[^a-zA-Z0-9_-]/g, ''); return el.tagName.toLowerCase() + (first ? '.' + first : ''); })(),
            ratio: e.intersectionRatio,
            changed,
            before: Object.fromEntries(changed.map(p => [p, before[p]])),
            after: Object.fromEntries(changed.map(p => [p, after[p]])),
            transition: getComputedStyle(e.target).transition,
          });
        }
        props.forEach(p => before[p] = after[p]);
      });
    }, { threshold: [0, 0.1, 0.5, 1.0] });
    observer.observe(el);
  });
  return 'Observing ' + allEls.length + ' elements';
})()
"

# 2. Scroll through the page
agent-browser scroll down 300
agent-browser --session <s> wait 800
agent-browser scroll down 300
agent-browser --session <s> wait 800
agent-browser scroll down 300
agent-browser --session <s> wait 800
agent-browser scroll down 300
agent-browser --session <s> wait 800

# 3. Retrieve results
agent-browser --session <s> eval "(() => JSON.stringify(window.__scrollTransitions || [], null, 2))()"
```

**Save results to** `tmp/ref/<component>/scroll-transitions.json`

| Result | Next step |
|--------|-----------|
| Empty `[]` | No scroll-triggered transitions — continue |
| CSS transition values | Implement with `IntersectionObserver` + CSS transitions |
| Complex WAAPI / stagger | **Run transition extraction pipeline** → `measurement.md` then `css-extraction.md` |

### Detect mouse-tracking interactions

```bash
agent-browser --session <s> eval "
(() => {
  const rows = document.querySelectorAll('a, [class*=row], [class*=card], [class*=item]');
  const mouseTracked = [];
  rows.forEach(row => {
    const absChildren = [...row.querySelectorAll('*')].filter(el => {
      const s = getComputedStyle(el);
      return s.position === 'absolute' && s.pointerEvents === 'none' && el.offsetHeight > 20;
    });
    if (absChildren.length > 0) {
      mouseTracked.push({
        parent: row.tagName + '.' + (row.className?.split(' ')[0] || ''),
        children: absChildren.map(c => ({
          tag: c.tagName, class: c.className?.slice(0, 40),
          width: c.offsetWidth, height: c.offsetHeight, hasImage: !!c.querySelector('img'),
        })),
      });
    }
  });
  return JSON.stringify({ mouseTracked });
})()
"
```

If `mouseTracked` is non-empty → record in `interactions-detected.json` as `type: "mouse-follow"`.

### Capture hover state delta (MANDATORY for all hoverable elements)

CSS `:hover` only reveals CSS-driven transitions. Many modern sites use **JS-driven hover** (GSAP `mouseenter`/`mouseleave`, Framer Motion `whileHover`, or vanilla `addEventListener`). These produce no CSS trace — the only way to detect them is to **actually hover and measure the delta**.

#### Step 5d-1: Enumerate all hoverable elements

```bash
agent-browser --session <s> eval "
(() => {
  const results = [];
  const seen = new Set();

  // 1. Elements with explicit hover data attributes
  // Find elements with any hover/interaction data attributes (framework-agnostic)
  document.querySelectorAll('*').forEach(el => {
    const hasHoverAttr = Array.from(el.attributes).some(a => /hover|interact|animation|motion/i.test(a.name));
    if (!hasHoverAttr) return;
    const sel = el.tagName.toLowerCase() + '.' + (el.className?.toString().split(' ')[0] || '');
    if (seen.has(sel)) return;
    seen.add(sel);
    results.push({ selector: sel, source: 'data-attribute', attr: Array.from(el.attributes).filter(a => /hover|interact|animation|motion/i.test(a.name)).map(a => a.name).join(',') });
  });

  // 2. Elements with CSS transition property (potential hover targets)
  document.querySelectorAll('a, button, [role=button], [role=link], [tabindex], [class*=btn], [class*=link]').forEach(el => {
    const s = getComputedStyle(el);
    if (s.transition && s.transition !== 'all 0s ease 0s' && s.cursor === 'pointer') {
      const sel = el.tagName.toLowerCase() + '.' + (el.className?.toString().split(' ')[0] || '');
      if (seen.has(sel)) return;
      seen.add(sel);
      results.push({ selector: sel, source: 'css-transition', transition: s.transition.slice(0, 100) });
    }
  });

  return JSON.stringify(results);
})()
"
```

#### Step 5d-2: Measure before/after hover delta for each element

For each hoverable element, **actually trigger the hover** and compare `getComputedStyle` before and after:

```bash
agent-browser --session <s> eval "
(() => {
  // This must be run INTERACTIVELY — use agent-browser hover command instead
  // For each selector from Step 5d-1:
  return JSON.stringify({ note: 'Use agent-browser hover + eval to measure deltas' });
})()
"
```

**Interactive measurement protocol:**
1. `agent-browser --session <s> hover "<selector>"` — trigger hover
2. Wait 500ms for transition to complete
3. `agent-browser --session <s> eval` — read `getComputedStyle` for the target + all children
4. `agent-browser --session <s> hover "body"` — move away to deactivate hover
5. Compare before/after for EVERY property: `transform`, `opacity`, `scale`, `display`, `visibility`, `backgroundColor`, `color`, `borderColor`, `boxShadow`, `clipPath`, `filter`

**Save delta to** `tmp/ref/<component>/hover-deltas.json`:
```json
{
  "elements": [
    {
      "selector": ".case__item-link",
      "type": "js-driven",
      "delta": {
        ".case__img-hover": { "display": ["none", "block"] },
        ".case__img-inner": { "transform": ["none", "scale(1.05)"] }
      },
      "transition": "GSAP mouseenter (from bundle analysis)",
      "duration": "0.7s",
      "easing": "cubic-bezier(0.625, 0.05, 0, 1)"
    }
  ]
}
```

**Why this step is mandatory:**
- CSS `transition` property tells you the *capability* but not the *actual change*. A button may have `transition: transform 0.45s` but the hover might scale it OR translate it — you can't know without hovering.
- JS-driven hovers (GSAP, Framer) produce NO CSS trace at all. The only evidence is the runtime style change.
- Skipping this step → guessing hover effects → "approximately close" implementations that feel wrong.

> **Full CSS hover delta extraction → `css-extraction.md`.** Use the classify eval above to detect `hasTransition: true`, then run the transition extraction pipeline for precise before/after measurement with SVG stroke tracking.

#### Step 5d-2b: Extract ALL hover CSS rules from page (MANDATORY)

CSS files alone do NOT contain all hover rules. Webflow and many CMS platforms inject hover CSS via **inline `<style>` tags** that aren't in downloaded `.css` files. This is the #1 reason hover transitions are silently missed.

```bash
agent-browser --session <s> eval "
(() => {
  const hoverRules = [];
  for (const sheet of document.styleSheets) {
    try {
      for (const rule of sheet.cssRules) {
        const sel = rule.selectorText || '';
        if (sel.includes(':hover')) {
          hoverRules.push({ selector: sel.slice(0, 80), css: rule.cssText.slice(0, 300) });
        }
      }
    } catch(e) {}
  }
  return JSON.stringify(hoverRules, null, 2);
})()
"
```

**Save to** `tmp/ref/<component>/hover-css-rules.json`

⛔ **Gate:** Every hover rule in `hover-css-rules.json` must have a corresponding CSS rule in `globals.css`. If a hover rule exists in the original but not in `globals.css`, add it immediately. Do NOT proceed to generation with missing hover rules.

**Common missed patterns:**
- `::after` pseudo-elements with `content: attr(data-text)` — text swap on hover
- `:hover .child-element` — parent hover affecting child transforms (3D card fold, text slide)
- Inline `<style>` tags injected by Webflow/CMS — NOT in downloaded CSS files
- `transform-origin` on hover children — required for 3D perspective effects

#### Step 5d-2c: Extract hover DOM changes (MANDATORY for interactive buttons)

For each hoverable element, check if hover changes **DOM content** (not just style):
- `data-text` / `data-label` attributes → text swap on hover via `::after`
- Child elements that appear/disappear (`display: none → block`)
- `::before` / `::after` pseudo-elements with new content

```bash
agent-browser --session <s> eval "
(() => {
  const btns = document.querySelectorAll('[data-text], [data-label], [data-hover-text], [data-btn-inner]');
  return JSON.stringify(Array.from(btns).map(el => ({
    selector: el.tagName + '.' + (el.className?.toString().split(' ')[0] || ''),
    dataText: el.getAttribute('data-text'),
    dataLabel: el.getAttribute('data-label'),
    text: el.textContent?.trim().slice(0, 30),
  })), null, 2);
})()
"
```

**If `data-text` attributes exist:** The hover effect includes a text swap — original text slides away and `data-text` value slides in. Implement with `::after { content: attr(data-text) }` + `translateY` transition.

#### Step 5d-2d: Hover video capture (MANDATORY)

DOM inspection alone cannot reveal the full visual effect of a hover — clip-path animations, 3D transforms, text swaps, and multi-element coordinated effects are invisible in `getComputedStyle` snapshots. **Record hover interactions as video** to capture the exact visual effect.

On splash sites, this is especially critical because the agent session may timeout while waiting for preloader.

```bash
# Wait for splash to complete
agent-browser wait 8000 --session <s>

# Scroll to target element
agent-browser eval --session <s> "(() => { window.scrollTo(0, <target-scroll>); return 'ok'; })()"
agent-browser wait 800 --session <s>

# Record hover interaction
agent-browser record start tmp/ref/<component>/hover-<element>.webm --session <s>
agent-browser hover "<selector>" --session <s>
agent-browser wait 1000 --session <s>
agent-browser hover "body" --session <s>
agent-browser wait 500 --session <s>
agent-browser record stop --session <s>

# Extract frames for review
mkdir -p tmp/ref/<component>/hover-frames/<element>
ffmpeg -i tmp/ref/<component>/hover-<element>.webm -vf fps=10 tmp/ref/<component>/hover-frames/<element>/frame-%03d.png -y
```

**Read hover frames** to understand the exact visual effect:
- Text slides up and is replaced? → `translateY` + `::after` text swap
- Text fades and shrinks in place? → `opacity` + `scale` in same position
- Text clips from bottom? → `clip-path: inset()` animation
- 3D rotation? → `perspective` + `rotateX/Y` transform
- Card elements separate? → child transforms with different `transform-origin`

⛔ **Never conclude "no visual transition" without video evidence.** Bundle grep returning empty does NOT mean no hover effect — CSS `:hover` rules in inline `<style>` tags are invisible to bundle search.

#### Step 5d-3: JS-driven hover timing extraction (MANDATORY)

> **If Step 5d-2 measured a hover delta on an element whose `transitionDuration` is `0s`** (the element AND its children) — the timing lives in JavaScript. Read `hover-timing-extraction.md` for the `getAnimations()` capture procedure and the bundle-grep fallback for GSAP internal tweens. Otherwise skip — pure-CSS hovers already have timing in `getComputedStyle`.
>
> Output: `tmp/ref/<component>/hover-timing.json`. ⛔ Gate: any element with visual deltas but `timingSource: "unknown"` must be resolved via bundle analysis (Step 5c-a) before generation.

#### Step 5d-4: Hover child cascade detection (MANDATORY)

Step 5d-2 measures the hovered element, but hover effects often cascade to **sibling and child elements** (e.g., hovering a card scales the image, fades in an overlay, shifts the title). Measure ALL children:

```bash
# For each hoverable element, measure all children before/after hover
agent-browser --session <s> eval "
(() => {
  window.__measureChildren = function(parentSel) {
    const parent = document.querySelector(parentSel);
    if (!parent) return JSON.stringify({ error: 'not found' });
    const children = [...parent.querySelectorAll('*')].filter(el => {
      const r = el.getBoundingClientRect();
      return r.width > 5 && r.height > 5;
    }).slice(0, 30);

    return children.map(el => {
      const s = getComputedStyle(el);
      const cn = typeof el.className === 'string' ? el.className : '';
      return {
        selector: el.tagName.toLowerCase() + (cn.trim().split(' ')[0] ? '.' + cn.trim().split(' ')[0] : ''),
        transform: s.transform,
        opacity: s.opacity,
        display: s.display,
        visibility: s.visibility,
        backgroundColor: s.backgroundColor,
        color: s.color,
        scale: s.scale,
        clipPath: s.clipPath,
        filter: s.filter,
        width: el.offsetWidth,
        height: el.offsetHeight,
      };
    });
  };
  return 'ready';
})()
"

# Before hover:
agent-browser --session <s> eval "(() => JSON.stringify(window.__measureChildren('<selector>'), null, 2))()"
# Save as before-children.json

agent-browser --session <s> hover "<selector>"
agent-browser --session <s> wait 500

# After hover:
agent-browser --session <s> eval "(() => JSON.stringify(window.__measureChildren('<selector>'), null, 2))()"
# Save as after-children.json

agent-browser --session <s> hover "body"
```

Compare before/after for ALL children. Add changed children to `hover-deltas.json` under the parent's entry.

### Extract CSS keyframes

> **Full keyframe extraction → `css-extraction.md`.** Use the classify eval above to detect `hasAnimation: true`, then run the transition extraction pipeline for keyframe extraction with frame capture and cross-origin fallback.

### Detect scroll behavior (snap, smooth, overscroll)

```bash
agent-browser --session <s> eval "
(() => {
  const results = { snap: [], smooth: [], overscroll: [] };
  const scanned = new Set();
  document.querySelectorAll('*').forEach(el => {
    const s = getComputedStyle(el);
    const cn = typeof el.className === 'string' ? el.className : el.className?.baseVal || '';
    const sel = el.tagName.toLowerCase() + (cn.trim().split(' ')[0] ? '.' + cn.trim().split(' ')[0] : '');
    if (scanned.has(sel)) return;
    scanned.add(sel);
    if (s.scrollSnapType && s.scrollSnapType !== 'none') {
      const children = [];
      el.querySelectorAll(':scope > *').forEach(child => {
        const cs = getComputedStyle(child);
        if (cs.scrollSnapAlign && cs.scrollSnapAlign !== 'none') {
          const ccn = typeof child.className === 'string' ? child.className : child.className?.baseVal || '';
          children.push({ selector: child.tagName.toLowerCase() + (ccn.trim().split(' ')[0] ? '.' + ccn.trim().split(' ')[0] : ''), snapAlign: cs.scrollSnapAlign });
        }
      });
      results.snap.push({ selector: sel, snapType: s.scrollSnapType, children });
    }
    if (s.scrollBehavior === 'smooth') results.smooth.push({ selector: sel });
    if (s.overscrollBehavior && s.overscrollBehavior !== 'auto auto' && s.overscrollBehavior !== 'auto')
      results.overscroll.push({ selector: sel, behavior: s.overscrollBehavior });
  });
  return JSON.stringify(results, null, 2);
})()
"
```

### Save interaction detection results (MANDATORY)

Save a summary to `tmp/ref/<component>/interactions-detected.json`. Include all discovered interactions from the evals above. If zero found, save `{ "interactions": [], "note": "static component" }`. This file is required by the Phase 2 Gate and by Step 9.

### Capture idle + active states (MANDATORY for hover/click)

> **Capture is delegated to `ui-capture` Phase 2C (Claude slash command: `/ui-capture`).** Do not capture here — interaction-detection only detects. After saving `interactions-detected.json`, Step 5b triggers `ui-capture` Phase 2B–2E which captures idle+active pairs for every detected interaction.
>
> **Re-capture trigger (SKILL.md Step 5b):** Re-run `ui-capture` Phase 2B–2E if interaction detection found ANY hover, click, or scroll-triggered element that was not captured in the initial Phase 1 run — i.e., if `interactions-detected.json` contains elements not already in `regions.json`. If `interactions-detected.json` is `{ "interactions": [] }`, skip Step 5b entirely.

**Gate:** `python -m ui_clone.gate <ref-dir> pre-generate` checks idle+active pairs exist (produced by ui-capture).

### Extract click-transition structure (MANDATORY for content-swap)

When clicking triggers a **content swap**, extract the transition's DOM structure at 100ms after click — not just the visual result. See the eval script for capturing `paneCount`, `zIndex`, `background`, `animationName` to determine single-pane vs two-pane, old-on-top vs new-on-top patterns.

Save to `tmp/ref/<component>/transition-structure.json`.

### Post-detection sanitization check

```bash
grep -iE 'ignore previous|you are now|system prompt|<script|javascript:|data:text' \
  tmp/ref/<component>/interactions-detected.json && echo "Warning: Suspicious content" || echo "Clean"
```

---

## Step 5e: Classify Drag/Swipe Effects

| Effect type | Detection | Implementation |
|---|---|---|
| **State flip** (carousel) | Drag → snap to new position | `pointerup` checks `dx > threshold` → `goTo()`. **No translateX during drag.** |
| **Transform tracking** (slider) | Element follows pointer in real-time | `pointermove` applies `translateX(dx)`. `pointerup` snaps. |
| **Parallax tracking** (cursor follow) | Elements shift with cursor | `pointermove` (global) → update with lerp/decay. |

**Critical rule:** State-flip drags must NOT apply `translateX` during drag.
