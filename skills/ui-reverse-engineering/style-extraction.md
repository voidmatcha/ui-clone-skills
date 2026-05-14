# Style Extraction — Step 3

> All `agent-browser eval` calls must use IIFE: `(() => { ... })()` — no top-level return.

### Pre-step: Merge runtime-injected transitions (MANDATORY)

If `dom-state-diff.json` exists (from Step 2.6-pre), extract runtime-injected transitions and add them to `globals.css`:

```bash
node -e "
const diff = JSON.parse(require('fs').readFileSync('./tmp/ref/<component>/dom-state-diff.json'));
const injected = [];
for (const [sel, changes] of Object.entries(diff.elementDiffs || {})) {
  if (changes.transition && changes.transition.post && !changes.transition.pre) {
    injected.push({ selector: sel, transition: changes.transition.post });
  }
}
if (injected.length > 0) {
  console.log('Runtime-injected transitions (add to globals.css):');
  injected.forEach(i => console.log('.' + i.selector.split('.')[1] + ' { transition: ' + i.transition.slice(0, 120) + '; }'));
} else {
  console.log('No runtime-injected transitions found.');
}
"
```

⛔ **These transitions are NOT in the downloaded CSS files.** They must be added manually to `globals.css`. Without them, hover effects will have no animation (instant snap instead of smooth transition).

**Common Webflow runtime-injected transitions:**
- Button hover: `transform 0.45s linear(...)` — spring bounce
- Text hover: `clip-path 0.4s, scale 0.3s` — text clip reveal
- Background: `background 0.3s, font-size 0.3s` — Webflow global body transition
- Card links: `background 0.3s` — subtle hover feedback

## Step 3: Extract Computed Styles

### Key element styles

> **Adapt selectors below to your target component.** Replace `.target` with the actual selector identified in Step 1 (dom-extraction.md). The selectors here are starting points — add or remove based on the actual DOM structure.

```bash
agent-browser --session <s> eval "
(() => {
  const selectors = ['.target', '.target h1', '.target p', '.target button'];
  const props = [
    'display', 'flexDirection', 'alignItems', 'justifyContent', 'gap',
    'gridTemplateColumns', 'gridTemplateRows',
    'width', 'height', 'maxWidth', 'minHeight',
    'padding', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
    'margin', 'marginTop', 'marginRight', 'marginBottom', 'marginLeft',
    'fontSize', 'fontWeight', 'fontFamily', 'lineHeight', 'letterSpacing',
    'color', 'backgroundColor', 'backgroundImage',
    'borderRadius', 'border', 'boxShadow',
    'opacity', 'transform', 'filter',
    'position', 'top', 'right', 'bottom', 'left', 'zIndex',
  ];
  const result = {};
  selectors.forEach(sel => {
    const el = document.querySelector(sel);
    if (!el) return;
    const s = getComputedStyle(el);
    result[sel] = {};
    const positionProps = new Set(['top','right','bottom','left','marginTop','marginRight','marginBottom','marginLeft','paddingTop','paddingRight','paddingBottom','paddingLeft']);
    props.forEach(p => {
      const v = s[p];
      if (v && (v !== '0px' || positionProps.has(p)) && (v !== 'none' || p === 'display')) {
        result[sel][p] = v;
      }
    });
  });
  return JSON.stringify(result, null, 2);
})()
"
```

### Extract typography scale system (MANDATORY)

`getComputedStyle` returns **px values** — these are viewport-specific snapshots, NOT the original CSS values. Sites like Webflow use `em`-based typography with a viewport-scaled `body` font-size (e.g., `body { font-size: 0.83vw }`), making all `em` fonts scale proportionally. If you hardcode the computed px values, the font sizes will be **wrong at every viewport width except the one you measured at**.

**This step detects and records the typography scaling system so the implementation can reproduce it.**

```bash
agent-browser --session <s> eval "
(() => {
  const root = getComputedStyle(document.documentElement);
  const body = getComputedStyle(document.body);

  // 1. Find the raw body font-size declaration (not computed)
  // Checks: stylesheets, inline style, and resolves var() references
  let bodyFontSizeRaw = '';
  for (const sheet of document.styleSheets) {
    try {
      for (const rule of sheet.cssRules) {
        if (rule.selectorText === 'body' || rule.selectorText === 'html') {
          const fs = rule.style?.fontSize;
          if (fs && fs !== '') bodyFontSizeRaw = fs;
        }
      }
    } catch(e) {} // CORS-blocked sheets silently skip
  }
  // Fallback: check inline style (React/CSS-in-JS)
  if (!bodyFontSizeRaw && document.body.style.fontSize) {
    bodyFontSizeRaw = document.body.style.fontSize;
  }
  // Resolve var() to its computed expression
  let bodyFontSizeResolved = bodyFontSizeRaw;
  if (bodyFontSizeRaw.startsWith('var(')) {
    const varName = bodyFontSizeRaw.match(/var\(([^)]+)\)/)?.[1];
    if (varName) bodyFontSizeResolved = root.getPropertyValue(varName).trim();
  }

  // 2. Extract CSS custom properties for typography
  const typographyVars = {};
  for (const sheet of document.styleSheets) {
    try {
      for (const rule of sheet.cssRules) {
        if (!rule.style) continue;
        for (let i = 0; i < rule.style.length; i++) {
          const prop = rule.style[i];
          if (prop.startsWith('--') && (prop.includes('font') || prop.includes('heading') || prop.includes('paragraph') || prop.includes('size'))) {
            const val = rule.style.getPropertyValue(prop).trim();
            if (val) typographyVars[prop] = val;
          }
        }
      }
    } catch(e) {}
  }

  // 3. Detect font families used
  const fontFamilies = new Set();
  document.querySelectorAll('h1,h2,h3,h4,h5,h6,p,span,a,button,li,div').forEach(el => {
    if (!el.textContent?.trim() || el.children.length > 5) return;
    const s = getComputedStyle(el);
    if (s.display !== 'none') fontFamilies.add(s.fontFamily);
  });

  // 4. Check if site uses viewport-based scaling (check both raw and resolved)
  const checkStr = bodyFontSizeRaw + ' ' + bodyFontSizeResolved;
  const usesVwScaling = /vw|dvw|calc|clamp/.test(checkStr);
  const usesEmScaling = Object.values(typographyVars).some(v => v.includes('em'));

  return JSON.stringify({
    rootFontSize: root.fontSize,
    bodyFontSizeComputed: body.fontSize,
    bodyFontSizeRaw: bodyFontSizeRaw || '(not found — check CSS-in-JS or inline styles)',
    bodyFontSizeResolved: bodyFontSizeResolved || bodyFontSizeRaw,
    scalingSystem: usesVwScaling ? 'viewport-scaled (vw/calc/clamp)' : usesEmScaling ? 'em-based (relative to body)' : 'px-fixed',
    fontFamilies: Array.from(fontFamilies),
    typographyVars: typographyVars,
    implementationGuide: usesVwScaling || usesEmScaling
      ? 'DO NOT use computed px values. Reproduce the body font-size expression (vw/clamp) and use em units for all text.'
      : 'Computed px values are safe to use directly.',
  }, null, 2);
})()
"
```

**Save output to** `tmp/ref/<component>/typography.json`

**Implementation rules based on `scalingSystem`:**

| Scaling system | What to do |
|---|---|
| `viewport-scaled` | Copy `bodyFontSizeRaw` to `globals.css` body rule. Use `em` for all font sizes. |
| `em-based` | Copy the `typographyVars` as CSS custom properties. Use `var()` references. |
| `px-fixed` | Hardcoded px values are safe. But always check at 2+ viewport widths. |

**Common mistake this prevents:** Extracting `fontSize: 26.67px` (computed at 1280px) and hardcoding `fontSize: 30px` (guessing the "design" value). The actual value is `2.5em` relative to a `0.83vw` body font. At 1440px it's 30px, at 1280px it's 26.67px, at 768px it's 16px — one px value cannot represent this.

### ⛔ Viewport-scaled font em-conversion gate (MANDATORY)

If `scalingSystem` is `viewport-scaled` or `em-based`, you MUST compute the em conversion table before proceeding. This is a **blocking gate** — do NOT move to Step 4 or generation without `em-conversion.json`.

```bash
agent-browser --session <s> eval "
(() => {
  const typo = JSON.parse(document.querySelector('#__typography_json')?.textContent || 'null');
  // Re-extract inline if not embedded
  const body = getComputedStyle(document.body);
  const rootFontSize = parseFloat(getComputedStyle(document.documentElement).fontSize);
  const bodyFontSize = parseFloat(body.fontSize);

  // Collect all unique font sizes on the page
  const textEls = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,p,a,span,button,li,th,td,label,div')]
    .filter(el => el.offsetHeight > 0 && el.textContent?.trim().length > 0 && el.children.length <= 3);

  const sizeMap = {};
  textEls.forEach(el => {
    const s = getComputedStyle(el);
    const px = parseFloat(s.fontSize);
    if (px === 0 || isNaN(px)) return;
    const em = Math.round((px / bodyFontSize) * 1000) / 1000;
    const key = px.toFixed(2);
    if (!sizeMap[key]) {
      const cn = typeof el.className === 'string' ? el.className : '';
      sizeMap[key] = {
        computedPx: px,
        emValue: em + 'em',
        remValue: Math.round((px / rootFontSize) * 1000) / 1000 + 'rem',
        sampleSelector: el.tagName.toLowerCase() + (cn.trim().split(' ')[0] ? '.' + cn.trim().split(' ')[0] : ''),
        sampleText: el.textContent?.trim().slice(0, 30),
        fontWeight: s.fontWeight,
        fontFamily: s.fontFamily?.split(',')[0]?.trim(),
        lineHeight: s.lineHeight,
        letterSpacing: s.letterSpacing,
      };
    }
  });

  return JSON.stringify({
    bodyFontSizeComputed: bodyFontSize + 'px',
    rootFontSize: rootFontSize + 'px',
    viewportWidth: window.innerWidth,
    conversionTable: Object.values(sizeMap).sort((a, b) => b.computedPx - a.computedPx),
    generationRule: 'USE emValue for all font-size declarations. NEVER use computedPx directly.',
  }, null, 2);
})()
"
```

**Save output to** `tmp/ref/<component>/em-conversion.json`

**Gate check:** `python -m ui_clone.gate <ref-dir> pre-generate` must verify this file exists when `typography.json` shows `scalingSystem !== 'px-fixed'`.

**How generation must use this:**
1. In `globals.css`: `body { font-size: <bodyFontSizeRaw from typography.json>; }` (e.g., `0.83vw`, `clamp(12px, 0.83vw, 16px)`)
2. For every text element: look up its `computedPx` in the conversion table → use the `emValue`
3. **NEVER write `text-[26.67px]` or `fontSize: '26.67px'`** when scaling system is viewport-scaled
4. If an exact match isn't in the table (±0.5px), compute: `em = computedPx / bodyFontSizeComputed`

### Extract advanced visual properties (MANDATORY)

The key element extraction above captures basic layout properties but misses several CSS effects that are invisible in screenshots yet critical for accurate reproduction. Extract these for EVERY element that has non-default values:

```bash
agent-browser --session <s> eval "
(() => {
  const result = {};
  const skip = new Set(['none','normal','auto','border-box','rgb(0, 0, 0)','all 0s ease 0s','all','0s']);
  const props = ['mixBlendMode','isolation','backgroundClip','webkitBackgroundClip','webkitTextFillColor','backdropFilter','clipPath'];
  for (const el of document.querySelectorAll('*')) {
    const s = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if (r.width < 10 || r.height < 10) continue;
    const f = {};
    for (const p of props) { const v = s[p]; if (v && !skip.has(v)) f[p] = v; }
    if (s.webkitBackgroundClip === 'text' || s.backgroundClip === 'text') {
      Object.assign(f, { backgroundImage: s.backgroundImage, backgroundClip: 'text', webkitTextFillColor: s.webkitTextFillColor });
    }
    if (Object.keys(f).length > 0) {
      const cn = typeof el.className === 'string' ? el.className : '';
      result[el.id ? '#'+el.id : el.tagName.toLowerCase()+'.'+cn.trim().split(/\\s+/).slice(0,2).join('.')] = f;
    }
  }
  return JSON.stringify(result, null, 2);
})()
"
```

**Save output to** `tmp/ref/<component>/advanced-styles.json`

**Why this matters — patterns that are invisible in screenshots but break reproduction:**
- **`mix-blend-mode: difference`** — text/elements invert color when overlapping images. Without this, overlapping elements just obscure each other
- **`background-clip: text` + gradient** — gradient text effect. Without this, text appears as solid color
- **`-webkit-text-fill-color: transparent`** — companion to gradient text. Without this, the gradient is hidden behind solid text fill
- **`backdrop-filter`** — glass/blur effects on overlapping elements
- **`body` class toggles** — many sites toggle classes on `<body>` to coordinate dark/light mode transitions across nav, background, and text simultaneously (see "Body-level state transitions" below)

### Body-level state transitions

Many sites coordinate visual transitions (light→dark backgrounds, nav color inversion) by toggling CSS classes on `<body>`. This is invisible in DOM extraction but critical for reproduction.

```bash
agent-browser --session <s> eval "
(() => {
  const bodyS = getComputedStyle(document.body);
  // Check body transition property
  const bodyTransition = bodyS.transition;

  // Find CSS rules that target body with class selectors
  const bodyRules = [];
  for (const sheet of document.styleSheets) {
    try {
      for (const rule of sheet.cssRules) {
        if (rule.selectorText && rule.selectorText.startsWith('body.') && !rule.selectorText.includes('dropdown')) {
          bodyRules.push({
            selector: rule.selectorText.slice(0, 120),
            cssText: rule.cssText.slice(0, 300),
          });
        }
      }
    } catch(e) {}
  }

  return JSON.stringify({
    bodyTransition,
    bodyClassRules: bodyRules,
    currentBodyClasses: document.body.className,
  }, null, 2);
})()
"
```

**Save output to** `tmp/ref/<component>/body-state.json`

**Generation rule:** If `bodyTransition` is not `'all 0s'` or `bodyClassRules` is non-empty, the implementation must:
1. Toggle the detected class(es) on `document.body` based on scroll position or state
2. Reproduce all CSS rules targeting `body.<class>` — including nav color inversion (`filter: brightness(0) invert(1)`), background-color transitions, and text color overrides
3. Use CSS for the cascade (e.g., `body.dark-active .nav-logo { filter: invert(1) }`) rather than React state for each affected element

### Scan for global overlays (grain, noise, texture)

Many design/architecture sites apply a full-page overlay for visual texture — film grain, noise patterns, paper texture. These are easy to miss because they are `pointer-events: none` and visually subtle, but omitting them makes the implementation look "too clean" compared to the reference.

```bash
agent-browser --session <s> eval "
(() => {
  const overlays = [];
  document.querySelectorAll('*').forEach(el => {
    const s = getComputedStyle(el);
    if (s.position === 'fixed' && s.pointerEvents === 'none' && parseInt(s.zIndex) > 100) {
      const r = el.getBoundingClientRect();
      if (r.width > window.innerWidth * 0.8 && r.height > window.innerHeight * 0.8) {
        overlays.push({
          class: (el.className || '').slice(0, 60),
          bgImage: s.backgroundImage?.slice(0, 200),
          bgSize: s.backgroundSize,
          bgRepeat: s.backgroundRepeat,
          mixBlendMode: s.mixBlendMode,
          opacity: s.opacity,
          zIndex: s.zIndex,
        });
      }
    }
  });
  return JSON.stringify(overlays, null, 2);
})()
"
```

If any overlays are found, download the background image and add a matching `<div>` to the implementation with identical `background-size`, `background-repeat`, `mix-blend-mode`, and `z-index`.

### Extract CSS custom properties (design tokens)

```bash
agent-browser --session <s> eval "
(() => {
  const vars = {};
  for (const sheet of document.styleSheets) {
    try {
      for (const rule of sheet.cssRules) {
        if (rule.selectorText === ':root' || rule.selectorText === 'html' || rule.selectorText === ':host') {
          const matches = rule.cssText.matchAll(/--([\w-]+):\s*([^;]+)/g);
          for (const m of matches) vars['--' + m[1]] = m[2].trim();
        }
      }
    } catch(e) {}
  }
  return JSON.stringify(vars, null, 2);
})()
"
```

**Save output to** `tmp/ref/<component>/styles.json`

### Decorative SVG extraction

Decorative SVGs (curves, geometric patterns, dividers) are often unique to the design and cannot be guessed. Extract them verbatim:

```bash
agent-browser --session <s> eval "
(() => {
  const svgs = document.querySelectorAll('svg');
  const decorative = [...svgs].filter(svg => {
    const s = getComputedStyle(svg);
    return s.position === 'absolute' || svg.closest('[class*=decorat]') || svg.getAttribute('aria-hidden') === 'true';
  });
  return JSON.stringify(decorative.map(svg => ({
    viewBox: svg.getAttribute('viewBox'),
    width: getComputedStyle(svg).width,
    height: getComputedStyle(svg).height,
    position: getComputedStyle(svg).position,
    paths: [...svg.querySelectorAll('path')].map(p => ({
      d: p.getAttribute('d'),
      strokeWidth: p.getAttribute('stroke-width'),
      fill: p.getAttribute('fill'),
      strokeDasharray: p.style.strokeDasharray || null,
    })),
    section: svg.closest('section')?.className?.split(' ')[0] || 'none',
  })), null, 2);
})()
"
```

**Never approximate SVG paths.** Copy the `d` attribute verbatim. A "similar" curve is a wrong curve.

### Stroke-based hover animations

For elements with stroke-based hover animations (arrow icons, SVG decorations):

```bash
# 1. Capture idle state stroke properties
agent-browser --session <s> eval "(() => {
  const el = document.querySelector('<selector>');
  const paths = el.querySelectorAll('path, rect, circle, line');
  return JSON.stringify([...paths].map(p => ({
    tag: p.tagName,
    d: p.getAttribute('d')?.slice(0, 50),
    strokeDasharray: getComputedStyle(p).strokeDasharray,
    strokeDashoffset: getComputedStyle(p).strokeDashoffset,
  })));
})()"

# 2. Hover and capture active state
agent-browser --session <s> hover "<selector>"
agent-browser --session <s> wait 800
# Re-run same eval — compare values
```

**Example patterns for stroke hover animations:**
- Stroke draw-in: idle `dasharray: totalLength 0.1px` (hidden) → hover `dasharray: 0px 999999px` (visible), or vice versa
- Stroke morph: idle `dasharray: A B` → hover `dasharray: C D` with different segment ratios
- Both typically need CSS transition rules on `stroke-dasharray` and `stroke-dashoffset`
- The exact pattern varies per site — always extract idle + hover values, never assume

### Post-extraction sanitization check

After saving `styles.json`, scan for suspicious content in CSS custom property values:

```bash
grep -iE 'javascript:|data:text|expression\(|url\(data:|@import\s+url' tmp/ref/<component>/styles.json && echo "⚠️  Suspicious CSS values detected — review before proceeding" || echo "✅ No suspicious patterns found"
```

If suspicious content is found: **log it to the user**, remove the affected property, and continue.

---

### Design bundle grouping (MANDATORY post-processing)

After extracting all styles, group properties into 5 co-varying design bundles. Bundles capture properties that move together in a design system — changing one without the others breaks visual coherence.

```bash
agent-browser --session <s> eval "
(() => {
  const allEls = document.querySelectorAll('h1,h2,h3,h4,h5,h6,p,a,button,span,div,section,nav,footer,header,li,img,figure,[class*=card],[class*=btn],[class*=title],[class*=label]');
  const seen = { surface: new Map(), shape: new Map(), type: new Map(), tone: new Map(), motion: new Map() };

  allEls.forEach(el => {
    const s = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if (r.width < 5 || r.height < 5) return;
    const cn = typeof el.className === 'string' ? el.className : el.className?.baseVal || '';
    const id = el.id ? '#' + el.id : el.tagName.toLowerCase() + (cn ? '.' + cn.trim().split(/\\s+/).slice(0, 2).join('.') : '');

    // Surface = bg + border + shadow
    const surfaceKey = [s.backgroundColor, s.border, s.boxShadow].join('|');
    if (!seen.surface.has(surfaceKey)) {
      const sid = 'surface-' + (seen.surface.size + 1);
      seen.surface.set(surfaceKey, { id: sid, backgroundColor: s.backgroundColor, border: s.border, boxShadow: s.boxShadow, elements: [] });
    }
    seen.surface.get(surfaceKey).elements.push(id);

    // Shape = radius + padding
    const shapeKey = [s.borderRadius, s.paddingTop, s.paddingRight, s.paddingBottom, s.paddingLeft].join('|');
    if (!seen.shape.has(shapeKey)) {
      const sid = 'shape-' + (seen.shape.size + 1);
      seen.shape.set(shapeKey, { id: sid, borderRadius: s.borderRadius, padding: s.paddingTop + ' ' + s.paddingRight + ' ' + s.paddingBottom + ' ' + s.paddingLeft, elements: [] });
    }
    seen.shape.get(shapeKey).elements.push(id);

    // Type = fontSize + weight + family + lineHeight + letterSpacing
    const typeKey = [s.fontSize, s.fontWeight, s.fontFamily, s.lineHeight, s.letterSpacing].join('|');
    if (!seen.type.has(typeKey)) {
      const sid = 'type-' + (seen.type.size + 1);
      seen.type.set(typeKey, { id: sid, fontSize: s.fontSize, fontWeight: s.fontWeight, fontFamily: s.fontFamily, lineHeight: s.lineHeight, letterSpacing: s.letterSpacing, elements: [] });
    }
    seen.type.get(typeKey).elements.push(id);

    // Tone = color + bg + borderColor
    const toneKey = [s.color, s.backgroundColor, s.borderColor].join('|');
    if (!seen.tone.has(toneKey)) {
      const sid = 'tone-' + (seen.tone.size + 1);
      seen.tone.set(toneKey, { id: sid, color: s.color, backgroundColor: s.backgroundColor, borderColor: s.borderColor, elements: [] });
    }
    seen.tone.get(toneKey).elements.push(id);

    // Motion = transition + animation
    const motionKey = [s.transitionDuration, s.transitionTimingFunction, s.animationDuration, s.animationTimingFunction].join('|');
    if (motionKey !== '0s|ease|0s|ease' && !seen.motion.has(motionKey)) {
      const sid = 'motion-' + (seen.motion.size + 1);
      seen.motion.set(motionKey, { id: sid, transitionDuration: s.transitionDuration, transitionTimingFunction: s.transitionTimingFunction, animationDuration: s.animationDuration, animationTimingFunction: s.animationTimingFunction, elements: [] });
    }
    if (seen.motion.has(motionKey)) seen.motion.get(motionKey).elements.push(id);
  });

  // Convert Maps to arrays
  const bundles = {};
  Object.keys(seen).forEach(k => {
    bundles[k] = [...seen[k].values()];
  });

  return JSON.stringify(bundles, null, 2);
})()
"
```

**Save output to** `tmp/ref/<component>/design-bundles.json`

**Bundle semantics — properties that must change together:**
- **surface** (bg + border + shadow): visual depth. Never change `backgroundColor` without checking `border` and `boxShadow`.
- **shape** (radius + padding): element form. `borderRadius` and `padding` are proportionally related — a pill button has both large radius and generous padding.
- **type** (fontSize + weight + family + lineHeight + letterSpacing): text hierarchy. Never change `fontSize` without checking `lineHeight` and `letterSpacing`.
- **tone** (color + bg + borderColor): semantic color palette. The text color, background, and border of an element form a coherent tone.
- **motion** (transition + animation): timing feel. Duration and easing function are paired — a long duration with `linear` easing feels different from the same duration with `ease-out`.

Elements sharing the same bundle ID should receive identical values in the implementation. If two cards share `surface-3`, they must have the same bg + border + shadow.

---

> **Next:** Step 4 (Responsive Detection) is in `responsive-detection.md`.

## Section Height and Inter-Section Spacing (MANDATORY)

Font sizes and element spacing are the #1 source of "looks different" feedback. Extract these EXPLICITLY:

### Extract exact section heights and gaps

After extracting per-element styles, extract the **page-level layout**:

```bash
agent-browser --session <s> eval "(() => {
  const main = document.querySelector('main, .page-main, [role=main]') || document.body;
  const sections = [...main.children].filter(c => c.offsetHeight > 0 && c.tagName !== 'SCRIPT');
  return JSON.stringify(sections.map(s => {
    const cs = getComputedStyle(s);
    return {
      tag: s.tagName,
      class: (typeof s.className === 'string' ? s.className : '').slice(0, 80),
      top: Math.round(s.getBoundingClientRect().top + scrollY),
      height: Math.round(s.offsetHeight),
      padding: cs.padding,
      margin: cs.margin,
      gap: cs.gap,
      display: cs.display,
      flexDirection: cs.flexDirection,
    };
  }), null, 2);
})()"
```

Save this as `section-layout.json`. This captures:
- Exact section heights (use these as `h-[Xpx]` or `min-h-[Xpx]`)
- Gaps between sections (often 100-300px, invisible in screenshots)
- Flex/grid container properties that affect spacing

### Extract container wrapping properties

If sections are wrapped in a parent (e.g., `dark-section_wrap`), extract the wrapper's gap/padding:

```bash
agent-browser --session <s> eval "(() => {
  // Find flex/grid containers with gap
  const containers = [...document.querySelectorAll('*')].filter(el => {
    const s = getComputedStyle(el);
    return (s.display === 'flex' || s.display === 'grid') && s.gap !== 'normal' && s.gap !== '0px' && el.children.length > 2;
  });
  return JSON.stringify(containers.slice(0, 10).map(el => {
    const s = getComputedStyle(el);
    return {
      class: (typeof el.className === 'string' ? el.className : '').slice(0, 80),
      display: s.display, gap: s.gap, padding: s.padding,
      childCount: el.children.length,
      totalHeight: Math.round(el.offsetHeight),
    };
  }), null, 2);
})()"
```

**WHY THIS MATTERS:** In a real session, a dark-section wrapper had `gap: 234px` between 4 child sections. Without extracting this, the implementation was 957px shorter than the original, causing every section below to be at a wrong scroll position. This was caught only after 3 rounds of user feedback.
