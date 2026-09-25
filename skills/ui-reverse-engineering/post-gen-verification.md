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

### On AE FAIL — diagnose the measured region

Use section diff metadata and computed-style/tree differences to locate the
mismatch. Reference images are inspected only through the delegated Phase E
workflow in `visual-debug`; do not read them in the main fix loop.

Image-region differences remain failures until reference-backed dynamic-state
or approved substitution evidence explains their treatment. Pin carousel/video
states on both sides, recover stale signed assets when needed, and rerun the
relevant comparison. Never convert an image-region mismatch to PASS merely
because the region contains media.

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
| Text on impl looks "thinner / lighter" but font-family, size, weight all match ref | `[data-project] { -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility }` was added to host CSS, but ref uses browser defaults (`auto` / `auto`). On macOS Retina, `antialiased` is grayscale-AA — visibly *lighter* than subpixel rendering. | Compare BOTH properties on ref body: `getComputedStyle(document.body)` → `webkitFontSmoothing` and `textRendering`. If ref returns `auto`/`auto`, remove these declarations from impl host CSS. Never set `antialiased` reflexively — match the ref. |

⛔ **Gate:** If `fontsLoaded: 0` or all elements show body fallback font, fix before proceeding to visual comparison — every screenshot will be wrong otherwise.

⛔ **Gate (rendering parity):** Run on BOTH ref and impl: `getComputedStyle(document.body).webkitFontSmoothing` + `.textRendering`. If they don't match, fix before pixel comparison — the entire page renders at a different perceived weight otherwise.

## Silent failure checks (MANDATORY — run after font verification)

These produce no errors but cause every screenshot comparison to fail:

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

**The ONLY reliable way to verify animations.** Checking that values change in your impl proves nothing — compare AGAINST THE ORIGINAL at the same resolution, or you WILL ship wrong easing, wrong axis, wrong direction, wrong timing. Example: `clipPath: inset(0% 0% X% 0%)` (bottom clips upward) implemented as `inset(0% X% 0% 0%)` (right clips leftward) — both "animate clipPath", self-verification passes, A/B comparison instantly shows the mismatch.

1. **Capture original at 60fps** with the `agent-browser` rAF capture from `animation-detection.md` Tier 2, pointing to the original URL → `tmp/ref/<component>/original-60fps.json`.
2. **Capture implementation at 60fps** — same capture pointing to `localhost:<port>` → `tmp/ref/<component>/impl-60fps.json`.
3. **Diff key properties at matching timestamps (±50ms tolerance).** Every animated property must pass ALL 5 checks:

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

**Gate:** ANY check fails → fix before proceeding. Do NOT rationalize ("close enough", "similar feel"). Original values are the spec — match them or document the deviation. This catches wrong clipPath axes, invented animations (e.g. "logo shrinks and moves up" when the logo is static), wrong easing (5fps polling looked linear), desynced phases, and GSAP `transform: matrix(...)` init mistaken for an animation.

## Conditional loops (state coupling, section bounds, sticky, body state, hover)

Read `post-gen-state-loops.md` only when the matching signal is present: carousels/tabs
(Loop 0.5), a section with a fixed inline height under suspicion (Loop 1),
`sticky-elements.json` entries (Loop 2), `body-state.json` rules (Loop 3 + the body-state
implementation pattern), `hover-deltas.json` (Loop 4), or a detected animation library
whose wiring pattern you need.

---

## Loop 5: Section-Level Visual Comparison (MANDATORY)

Section-level cropping replaces noisy full-page scroll screenshots (scroll drift fails every position even when sections are correct). This is pipeline Step 8b; resolve `SCRIPTS_DIR` once per session as in `../visual-debug/SKILL.md`, then:

```bash
SECTION_COMPARE_QUIET=1 bash "$SCRIPTS_DIR/section-compare.sh" <original-url> <impl-url> <session> "$(pwd)/tmp/ref/<component>"
```

Quiet mode sends progress to `sections/section-compare.log` and prints only the result table, verdicts, exit code, and paths; open the log only when `result.txt` was not written.

It enumerates `<section>`, `<header>`, `<footer>`, `<main>` on both sites, matches sections by text fingerprint, crops per-section screenshots, runs AE per section, and flags `SVG_TEXT_MISSING` (ref SVG text paths vs impl HTML text), `LAYOUT_MISMATCH` (grid vs flex), `HEIGHT_MISMATCH` (height ratio >30% off), and `CHILD_COUNT_MISMATCH`.

### Gate

- ALL matched sections must PASS AE (threshold: 2000 per section)
- ALL structural diffs must be resolved or documented
- `SVG_TEXT_MISSING` is a **hard blocker** — copy the SVG outerHTML, never approximate

---

## Loop 6: Transition Comparison (MANDATORY if interactions-detected.json exists)

Compares hover/transition behavior element-by-element — not "does hover work?" but "does hover produce the same visual result?". This is pipeline Step 8c; it auto-detects every element with `transition-duration !== 0s` on both sites, captures idle → hover (`mouseenter`/`mouseover`) → reset (`mouseleave`) with screenshots + computedStyle, and compares idle style, hover style direction, timing (duration, easing), and missing transitions.

```bash
bash "$SCRIPTS_DIR/transition-compare.sh" <original-url> <impl-url> <session> tmp/ref/<component>
```

Per-selector output lists `status` plus `issues[]` such as `EASING_MISMATCH: prop=transform ref=cubic-bezier(0.32, 0.72, 0, 1) impl=ease` or `HOVER_TRANSFORM_NOT_APPLIED`.

### Gate

- ALL elements with transitions in ref must have matching transitions in impl
- Timing is compared **per property**: each property the ref animates must have a
  matching duration + easing in the impl (or be covered by an impl `transition: all`).
  Extra inert properties the ref does not animate (e.g. an added `transform`) are
  ignored, but a property the ref animates but the impl omits is a `MISSING_TRANSITION`.
- `HOVER_*_NOT_APPLIED` is a **hard blocker** — the effect is missing entirely
- `EASING_MISMATCH`, `DURATION_MISMATCH`, and `MISSING_TRANSITION` must be fixed to match ref values
