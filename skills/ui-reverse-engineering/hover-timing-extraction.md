# JS-Driven Hover Timing Extraction — Step 5d-3

Read this sub-doc when Step 5d-2 measured a hover delta but the element (or its children) have `transitionDuration: 0s` — i.e., the timing lives in JavaScript (GSAP `mouseenter`/`mouseleave`, Framer Motion `whileHover`, vanilla `addEventListener`). Otherwise skip — pure-CSS hovers have their timing in `getComputedStyle` already.

CSS hover deltas from Step 5d-2 capture the **what** (which properties change) but NOT the **when** (duration, easing) for JS-driven animations.

## Detection: does this element use JS-driven hover?

After measuring the delta in Step 5d-2, check whether the element has CSS transition timing:

```bash
agent-browser --session <s> eval "
(() => {
  // For each hoverable element from Step 5d-1
  const selectors = [/* paste selectors from Step 5d-1 results */];
  const jsHovers = [];

  selectors.forEach(sel => {
    const el = document.querySelector(sel);
    if (!el) return;
    const s = getComputedStyle(el);
    const hasCSStransition = s.transitionDuration !== '0s' && s.transitionDuration !== '0ms';

    // Check children too — GSAP often animates children, not parent
    const children = el.querySelectorAll('*');
    let anyChildHasCSS = false;
    children.forEach(child => {
      const cs = getComputedStyle(child);
      if (cs.transitionDuration !== '0s' && cs.transitionDuration !== '0ms') anyChildHasCSS = true;
    });

    if (!hasCSStransition && !anyChildHasCSS) {
      jsHovers.push({ selector: sel, reason: 'no-css-transition' });
    }
  });

  return JSON.stringify(jsHovers);
})()
"
```

## For each JS-driven hover, measure timing via `getAnimations()`

```bash
# 1. Set up animation listener before hover
agent-browser --session <s> eval "
(() => {
  window.__hoverAnimCapture = {};

  window.__captureHoverAnims = function(selector) {
    const el = document.querySelector(selector);
    if (!el) return;
    const allEls = [el, ...el.querySelectorAll('*')];
    // Snapshot BEFORE hover
    window.__hoverAnimCapture.before = allEls.map(e => ({
      sel: e.tagName + '.' + (e.className?.toString().split(' ')[0] || ''),
      anims: e.getAnimations?.()?.length || 0,
    }));
  };

  window.__readHoverAnims = function(selector) {
    const el = document.querySelector(selector);
    if (!el) return JSON.stringify({ error: 'not found' });
    const allEls = [el, ...el.querySelectorAll('*')];
    const results = [];

    allEls.forEach(e => {
      const anims = e.getAnimations?.() || [];
      anims.forEach(anim => {
        const timing = anim.effect?.getTiming?.() || {};
        const keyframes = anim.effect?.getKeyframes?.() || [];
        results.push({
          target: e.tagName + '.' + (e.className?.toString().split(' ')[0] || ''),
          duration: timing.duration,
          easing: timing.easing,
          delay: timing.delay,
          fill: timing.fill,
          keyframes: keyframes.map(kf => {
            const clean = {};
            for (const [k, v] of Object.entries(kf)) {
              if (k !== 'offset' && k !== 'computedOffset' && k !== 'easing' && k !== 'composite') clean[k] = v;
            }
            clean.offset = kf.offset;
            return clean;
          }),
        });
      });
    });

    return JSON.stringify(results, null, 2);
  };

  return 'hover animation capture ready';
})()
"

# 2. For each JS-driven hover element:
# a. Prepare capture
agent-browser --session <s> eval "(() => window.__captureHoverAnims('<selector>'))()"
# b. Trigger hover
agent-browser --session <s> hover "<selector>"
# c. Wait for animation to start (50ms is enough for GSAP/Framer)
agent-browser --session <s> wait 50
# d. Read WAAPI animations
agent-browser --session <s> eval "(() => window.__readHoverAnims('<selector>'))()"
# e. Move away
agent-browser --session <s> hover "body"
```

**If `getAnimations()` returns results:** Extract `duration`, `easing`, `keyframes` — these are the exact JS-driven hover values.

## Fallback: bundle grep (GSAP internal tweens)

GSAP often uses its internal tween engine instead of WAAPI, so `getAnimations()` returns empty. Search downloaded bundles for the element's selector near hover patterns:

```bash
# Find mouseenter/mouseleave handlers near known selectors
grep -B5 -A15 'mouseenter\|mouseleave\|onmouseenter\|pointerenter\|pointerleave' \
  tmp/ref/<component>/bundles/*.js | \
  grep -B10 -A10 '<selector-class-fragment>'
```

Extract `duration`, `ease`/`easing`, and property values from nearby `gsap.to()` or `gsap.fromTo()` calls.

## Output

Save JS hover timing to `tmp/ref/<component>/hover-timing.json`:

```json
{
  "jsHovers": [
    {
      "selector": ".case__item-link",
      "source": "waapi|bundle-grep",
      "targets": [
        {
          "child": ".case__img-inner",
          "duration": 700,
          "easing": "cubic-bezier(0.625, 0.05, 0, 1)",
          "properties": { "transform": ["none", "scale(1.05)"] }
        },
        {
          "child": ".case__img-hover",
          "duration": 500,
          "easing": "cubic-bezier(0.25, 0.1, 0.25, 1)",
          "properties": { "opacity": ["0", "1"] }
        }
      ]
    }
  ]
}
```

⛔ **Gate:** If Step 5d-2 detected visual deltas but this step found no timing for a JS-driven element, the hover implementation will be missing duration/easing. Flag these in `interactions-detected.json` as `"timingSource": "unknown"` — bundle analysis (Step 5c-a) must resolve them.

---

After this step, return to `interaction-detection.md` Step 5d-4 (hover child cascade detection).
