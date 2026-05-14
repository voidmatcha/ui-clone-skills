# Splash / Intro Animation Extraction

Splash animations (page-load intros, logo reveals, curtain opens) are the hardest motion to extract because they fire during page load — before any `agent-browser eval` can attach. This doc covers the full workflow for reliably capturing them.

**When to read:**
- **Step 5c-a:** preloader detected (bundle grep or DOM class) → read now for timeline extraction
- **Step 6 Phase A:** Tier 1 AE diff shows significant changes in first 1–3s → read for capture technique

**This doc** solves the *capture problem* (splash fires before eval can attach) with throttle + record techniques. Called from pipeline Steps 5c-a and 6 Phase A. Do not read back into calling docs from here.

## The capture problem

`agent-browser eval` executes AFTER the page loads and JS hydrates. Any splash animation that fires on `DOMContentLoaded` or in the first `rAF` cycle has already played by then. Your 60fps capture records a **static post-animation state** and you conclude "no splash" — but it already finished.

**Both Tier 2 methods (eval + Playwright `addInitScript`) can miss splash:** `addInitScript` runs before page load but AFTER React hydration — GSAP/CSS animations triggered on `DOMContentLoaded` have already set initial `from` values. The capture records the element moving from `from` to `to`, but `from` was applied before capture started, so it looks like "element was always at this position."

**Video frames are the ONLY reliable source for splash behavior.**

## Detection: when to use this protocol

If **Tier 1 AE diff shows significant changes in the first 1–3 seconds** (frames 1–30 at 10fps), a splash transition exists. Follow the rest of this doc.

If **Tier 1 shows NO changes in the first 3s**, run the **additional signals below before concluding splash doesn't exist**. AE-only detection misses fast splashes that finish before capture starts and misses sites whose loader element name doesn't match the standard grep list (`preloader|splash|introAnimation`).

## Detection signals when AE diff misses splash

Mark `hasPreloader: true` if **any one** of A–C below fires. A single positive signal is enough — splash detection biases toward false-positive (extra capture work) over false-negative (hero stuck at `from` state forever because the gating class never lands).

**Cross-ref to `bundle-analysis.md` (Step 5c-a):** Signal A here = BA Signal 4, Signal B = BA Signal 2, Signal C = BA Signal 3. BA orders by grep-first / standard-first; this doc orders by reliability (most reliable first).

### Signal A: html/body class transition (most reliable)

Many sites toggle a class on `<html>` or `<body>` that gates the entire splash → content flow. Capture the class **twice** — early after `open`, again after a wait — and diff:

```bash
agent-browser --session <s> open <url>
agent-browser --session <s> eval "(() => JSON.stringify({
  html: document.documentElement.className,
  body: document.body.className,
}))()" > tmp/ref/<c>/splash-classes-early.json

agent-browser --session <s> wait 6000
agent-browser --session <s> eval "(() => JSON.stringify({
  html: document.documentElement.className,
  body: document.body.className,
}))()" > tmp/ref/<c>/splash-classes-late.json
```

If the early/late diff includes any of these → splash exists:

| Class transition | Where | Meaning |
|---|---|---|
| `is-loading` → `is-loaded` | `<html>` | Generic load-state gate |
| `loading` → `loaded` | `<html>` or `<body>` | Generic load-state gate |
| `rk-preloading` → (removed) | `<html>` | Older WordPress/Slater convention |
| (added) `-once`, `-revealed` | `<body>` | First-load splash completion flag |
| (added) `-hideLogo`, `-loaded` | `<body>` | Triggers post-splash hero entry |
| (removed) `is-locked` | `<body>` | Scroll-lock released after splash |

The body class added on splash completion is usually the gate that triggers the hero entry animation. Missing it means the hero stays at its `from` state forever — a silent failure mode where everything renders but nothing animates in.

### Signal B: BEM-prefix loader element scan

Slater, Barba, and many bespoke build setups name the loader with a BEM prefix (`o-` organism, `m-` molecule, `a-` atom) or a `#js-` id. The standard `[class*=preloader]` selector misses these. Run a broader DOM scan:

```bash
agent-browser --session <s> eval "(() => {
  const sels = [
    '#js-loader', '#js-preloader', '#js-splash', '#js-intro',
    '.o-loader', '.m-loader', '.a-loader',
    '.c-loader', '.c-preloader', '.c-splash',
    '[class*=loader]', '[class*=splash]', '[class*=intro]',
    '[class*=preload]', '[class*=overlay]',
  ];
  const out = [];
  for (const sel of sels) {
    document.querySelectorAll(sel).forEach(el => {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      if (r.width < 100 || r.height < 100) return;
      if (s.position !== 'fixed' && s.position !== 'absolute') return;
      out.push({
        sel, id: el.id || null,
        cls: el.className?.toString?.().slice(0, 80),
        position: s.position, zIndex: s.zIndex,
        visibility: s.visibility, opacity: s.opacity, display: s.display,
        rect: { w: Math.round(r.width), h: Math.round(r.height) },
      });
    });
  }
  return JSON.stringify(out);
})()"
```

A fixed/absolute full-viewport element with `id^=js-` or BEM-prefixed class is the splash overlay **even if it has already faded out** (`opacity: 0`, `visibility: hidden`, `display: none` after the splash finished). Existence of the element in the DOM at all is the signal.

### Signal C: Extended bundle grep — anime.js + Barba transition systems

The standard `bundle-analysis.md` grep list misses Barba-style page-transition systems where the splash lives in a `transitions[].once()` method and uses anime.js (no `gsap.` prefix to grep for):

```bash
# anime.js — additional library not in standard animation-library detection
grep -lE 'anime\.timeline|anime\(\{|anime/lib' tmp/ref/<c>/bundles/*.js

# Barba.js + once() pattern — page-load splash entrypoint
grep -nE '\bonce\s*\(\s*\)\s*\{|basicTransition|barba\.|leave\s*\(\)\s*\{|enter\s*\(\)\s*\{' \
  tmp/ref/<c>/bundles/*.js | head -30

# #js- id pattern — Slater/Barba selectors for splash + post-splash hero
grep -oE '"#js-[a-zA-Z][^"]{2,30}"' tmp/ref/<c>/bundles/*.js | sort -u
```

If `once()` or `basicTransition` matches → the splash timeline is inside a Barba transition object. Read 200 lines after the match to extract the timeline (anime.js syntax: `anime.timeline().add({ targets, translateY, duration, easing }, offsetMs)`).

## Extracted-CSS gap (asset-extraction caveat)

Even after splash is detected, the loader's CSS often **isn't** in `tmp/ref/<c>/css/*.css`. Slater's per-page CSS, Webflow's IX2 styles, Barba's runtime CSS, and many critical-CSS setups are:

- Inline `<style>` blocks (not in `performance.getEntriesByType('resource')` → asset-extraction skips them)
- Critical-CSS injected by the framework before hydration
- Generated at runtime from JS keyframe definitions in the bundle

`getComputedStyle(loaderEl)` on the live page is the source of truth, **not** the downloaded `site.css`. Reading `site.css` and finding zero `.o-loader` rules does not mean the loader has no styles — it means the styles live somewhere else. When implementing the splash, query computed styles directly from the live element:

```bash
agent-browser --session <s> eval "(() => {
  const el = document.querySelector('#js-loader, .o-loader, [class*=loader]');
  if (!el) return null;
  const s = getComputedStyle(el);
  return JSON.stringify({
    backgroundColor: s.backgroundColor,
    position: s.position, zIndex: s.zIndex,
    transform: s.transform, transformOrigin: s.transformOrigin,
    transition: s.transition, animation: s.animation, opacity: s.opacity,
    width: s.width, height: s.height, top: s.top, left: s.left,
  });
})()"
```

If the loader has already faded by the time you query it (`display: none`), temporarily restore it: `el.style.display = 'flex'; el.style.opacity = '1'` before the `getComputedStyle` call.

## Splash throttle protocol (Tier 2 variant)

Network throttle slows JS execution, giving eval time to inject before animations fire.

```bash
# 1. Open page with throttle — delays JS so eval can inject first
agent-browser --session <project> close
agent-browser --session <project> open <url>
agent-browser --session <project> set viewport 1440 900
agent-browser --session <project> throttle 3g

# 2. Inject 60fps rAF capture IMMEDIATELY
agent-browser --session <project> eval "(() => {
  window.__frames = [];
  const start = performance.now();
  const targets = [
    '[class*=load]', '[class*=intro]', '[class*=splash]', '[class*=overlay]',
    '[class*=logo]', '[class*=text]', '[class*=image]', '[class*=bg]',
    'section', 'header', 'nav', 'main'
  ];
  const capture = () => {
    const t = performance.now() - start;
    if (t > 12000) return;
    const els = [];
    for (const sel of targets) {
      document.querySelectorAll(sel).forEach(el => {
        const s = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        if (r.width < 10 && r.height < 10) return;
        els.push({
          sel: (el.className?.toString?.() || el.tagName).slice(0, 40),
          op: +s.opacity,
          tf: s.transform !== 'none' ? s.transform.slice(0, 50) : null,
          cp: s.clipPath !== 'none' ? s.clipPath.slice(0, 50) : null,
          y: Math.round(r.top), h: Math.round(r.height),
        });
      });
    }
    window.__frames.push({ t: Math.round(t), els });
    requestAnimationFrame(capture);
  };
  requestAnimationFrame(capture);
  return 'capturing with throttle...';
})()"

# 3. Remove throttle — page loads normally, splash fires while capture runs
agent-browser --session <project> throttle off

# 4. Wait (longer due to throttled start)
agent-browser --session <project> wait 13000

# 5. Retrieve
agent-browser --session <project> eval "(() => JSON.stringify(window.__frames || []))()"
```

Save to `tmp/ref/<component>/idle-dom-60fps-throttled.json`.

**Verify the throttle worked:** if throttled Tier 2 shows elements appearing mid-capture that weren't in the first frame → caught the splash ✅. If the first frame already has all elements in final state → throttle wasn't enough. Increase to `throttle 2g` or fall back to video frame analysis below.

## Fallback: video frames (when throttle unavailable or insufficient)

Use Tier 1 video frames directly:

```bash
# 60fps extraction
ffmpeg -i tmp/ref/<component>/idle-capture.webm -vf fps=60 \
  tmp/ref/<component>/idle-frames-60fps/frame-%04d.png -y

# AE diff to find exact transition frame range
cd tmp/ref/<component>/idle-frames-60fps
PREV=""
for f in $(ls frame-*.png | sort); do
  if [ -n "$PREV" ]; then
    AE=$(compare -metric AE "$PREV" "$f" /dev/null 2>&1 | awk '{print $1}')
    echo "$f,$AE"
  fi
  PREV="$f"
done > ../splash-ae.csv
```

Measure logo/element bounding box at frame N vs frame N+5 to compute scale/translate delta. Cross-reference with bundle grep for exact easing/duration values.

## MANDATORY: Video frame → bundle cross-reference

When Tier 1 video shows splash but Tier 2 DOM data shows "no changes" or "element always static":

### Step 1: Read 3–5 transition frames

Look for:
- Individual SVG parts moving separately (staggered assembly)
- Whole element translating vs children translating
- Scale changes on specific sub-elements
- Elements appearing from off-screen edges

### Step 2: Grep bundle for the animation target

```bash
grep -o '.\{0,150\}logo.\{0,150\}'   bundles/main.js
grep -o '.\{0,150\}loader.\{0,150\}' bundles/main.js
grep -o '.\{0,150\}splash.\{0,150\}' bundles/main.js
grep -o '.\{0,150\}intro.\{0,150\}'  bundles/main.js
```

### Step 3: Look for child-selector patterns

The critical pattern: `.fromTo(".selector > *", {y: offset}, {y: 0})` means **each child element animates individually**, not the parent. Common for:
- SVG logo assembly (each `<path>`/`<g>`/`<rect>` moves independently)
- Text word/letter stagger (splitType → each span moves)
- Grid card reveals (each card enters with delay)

If you find `> *`, `.children`, or `stagger` in the GSAP code, the animation is **per-child**, not per-container. Implement with a loop over `element.children`, not a single transform on the parent.

**Anti-pattern this prevents:** you see the logo "move up" in video, implement `logo.style.transform = 'translateY(-150%)'`, and get a completely different result because the original animates each SVG rect/path individually from below.

## MANDATORY: Parse GSAP timeline structure

Screenshots and frame comparison show THAT something moves but NOT the timeline structure. A GSAP timeline with `delay: 0.28` and three simultaneous `.to()` calls at position `0` looks completely different from three sequential `setTimeout()` calls.

**Extract from the bundle timeline:**

1. **Defaults** — `delay`, `duration`, `ease` apply to ALL tweens unless overridden
2. **Position params** — `0` / `"<"` / `"<+=0.1"` / `">-0.5"`
   - `0` = timeline start
   - `"<"` = same time as previous tween
   - `"<+=0.1"` = 0.1s after previous tween STARTS
   - No position = after previous tween ENDS
3. **Simultaneous vs sequential** — multiple tweens at position `0` or `"<"` run AT THE SAME TIME
4. **`.add(() => {...})` callbacks** — fire at specific positions, can trigger CSS class changes, DOM mutations, sub-animations (e.g., FLIP)
5. **FLIP pattern** — `u.getState(elements)` → DOM change → `u.from(state, {targets, duration, ease})` = elements animate from OLD to NEW position

### GSAP timeline → implementation mapping

| GSAP pattern | Implementation |
|---|---|
| Multiple `.to()` at position `0` | Single `setTimeout` triggering ALL transitions simultaneously |
| `.to({}, {duration: 1.2}, 0)` (empty tween) | Timer delay of 1.2s before next sequential step |
| `"<"` position | Same `setTimeout` as previous, or CSS transitions starting at the same time |
| `"<+=0.1"` position | Second `setTimeout` at `firstTimer + 100ms` |
| `delay: 0.28` on timeline | Add 280ms to base timer value |
| FLIP `u.from(state, ...)` | `translateY(large) → translateY(0)` with same duration/easing |

## Conditional animation branches (CRITICAL)

A single JS bundle often contains MULTIPLE animation paths gated by conditions. Most common: `if (isHomePage) { introTimeline } else { heroAssembly }`. If you grep for "logo" and find an SVG assembly animation, it may NOT run on the page you're cloning.

```js
if (n) {
  // Path A: runs when condition true (e.g., homepage first visit)
  _.to(".introHome", { clipPath: "..." })
} else {
  // Path B: runs when condition false (e.g., navigating from another page)
  Oe.timeline().fromTo(".hero .gsap:logo > *", { y: offset }, { y: 0 })
}
// OR: n || (Path B code) — equivalent to if (!n) { Path B }
```

**How to identify which path runs:**
1. Find the condition variable (`n`, `i`, `r`) → trace to assignment
2. Common: `const n = t.meta.templateName === "home"`, `const i = e.meta.templateName === "work"`
3. Check what the condition means for YOUR target page
4. Only implement the animation from the CORRECT branch

**Failure this prevents:** you implement an elaborate SVG logo assembly that never runs on the target — because it's in the `else` branch. Meanwhile the ACTUAL animation (GSAP FLIP from siteLoader to introHome) is in the `if` branch and you never implemented it.

## Fixed overlay cleanup (MANDATORY before any capture)

Before capturing ANY reference screenshots or videos, remove fixed/sticky overlays NOT part of the site's actual UI (header/nav excluded). These corrupt every frame comparison and AE diff.

```bash
agent-browser --session <s> eval "(() => {
  const removed = [];

  // 1. Dismiss cookie/consent banners via click
  const dismissSelectors = ['.iubenda-cs-reject-btn', '.iubenda-cs-accept-btn',
    '[class*=cookie] button', 'button[class*=accept]', 'button[class*=reject]'];
  for (const sel of dismissSelectors) {
    const btn = document.querySelector(sel);
    if (btn) { btn.click(); removed.push('clicked: ' + sel); }
  }
  for (const b of document.querySelectorAll('button')) {
    if (/accept|accetta|rifiuta|reject|dismiss|got.it|agree/i.test(b.textContent)) {
      b.click(); removed.push('clicked: ' + b.textContent.trim().slice(0, 20));
    }
  }

  // 2. Force-remove remaining fixed/sticky overlays (NOT header/nav/intro/loader/hero)
  for (const el of document.querySelectorAll('*')) {
    const s = getComputedStyle(el);
    if (s.position !== 'fixed' && s.position !== 'sticky') continue;
    const r = el.getBoundingClientRect();
    if (r.width < 100 || r.height < 50) continue;
    const tag = el.tagName.toLowerCase();
    const cls = (el.className?.toString?.() || '').toLowerCase();
    if (tag === 'header' || tag === 'nav') continue;
    if (cls.includes('header') || cls.includes('nav') || cls.includes('menu')) continue;
    if (cls.includes('intro') || cls.includes('loader') || cls.includes('hero')) continue;
    if (cls.includes('cookie') || cls.includes('consent') || cls.includes('modal')
      || cls.includes('toast') || cls.includes('popup') || cls.includes('banner')
      || cls.includes('chat') || cls.includes('widget') || cls.includes('promo')
      || cls.includes('iubenda') || cls.includes('gdpr') || cls.includes('notice')) {
      el.remove();
      removed.push('removed: ' + cls.slice(0, 40));
      continue;
    }
    const z = parseInt(s.zIndex) || 0;
    if (z > 1000) {
      el.style.display = 'none';
      removed.push('hidden(z=' + z + '): ' + cls.slice(0, 30));
    }
  }

  return JSON.stringify(removed);
})()"
```

Run once after page load, BEFORE any recording. If overlays reappear after scroll/interaction, re-run before the next capture. If the banner can't be dismissed, use `agent-browser cookies clear` before opening.

## Splash vs. site content boundary

Find the frame where site content first appears:
- **Splash** = overlay covering the entire viewport
- **Content** = first frame where hero/navigation is visible underneath

Use Tier 2 DOM log: when the splash/overlay element's `opacity` drops to 0 or `display` becomes `none`, that's the boundary. Cross-reference with Tier 1 AE to confirm frame number.

```bash
# Check overlay elements AFTER splash plays
agent-browser --session <project> wait 8000
agent-browser --session <project> eval "(() => {
  const overlays = document.querySelectorAll('[class*=load], [class*=intro], [class*=splash], [class*=preload], [class*=curtain], [class*=overlay]');
  return JSON.stringify(Array.from(overlays).map(el => ({
    cls: el.className?.toString?.().slice(0, 60),
    display: getComputedStyle(el).display,
    opacity: getComputedStyle(el).opacity,
    visibility: getComputedStyle(el).visibility,
    zIndex: getComputedStyle(el).zIndex,
    position: getComputedStyle(el).position,
  })));
})()"
```

## Splash end-state verification (MANDATORY post-implementation)

After impl, verify the splash sequence **terminates cleanly**:

1. Record impl splash (same as Phase A — open page, record 10s)
2. Extract frames at 10fps
3. AE diff ref vs impl at matching timestamps (zero tokens):

```bash
for FRAME in <last-splash-frame> <first-content-frame> <settled-frame>; do
  AE=$(compare -metric AE \
    tmp/ref/<component>/idle-frames/frame-${FRAME}.png \
    tmp/ref/<component>/idle-frames-impl/frame-${FRAME}.png \
    /dev/null 2>&1)
  echo "Frame $FRAME: AE=$AE"
done
# AE=0 → pass. AE>0 → check DOM log first, Read 1 frame only if DOM can't explain.
```

| Timestamp | Check | Method |
|---|---|---|
| Last splash frame | All animated elements fully hidden/removed | AE diff + DOM `opacity`/`display` |
| First content frame | Main content visible, no splash remnants | AE diff + DOM element count |
| +2s after splash | No leftover elements | DOM query for splash nodes |

**Common splash failures:**
- Animated elements (3D objects, logos) remain visible — missing opacity/visibility transition to terminal state
- Overlay z-index not reset — splash overlay blocks interaction after animation completes
- Content scale/opacity animation not triggered — content stays at initial state (e.g., `scale(0.8)`)
- **Counter/progress starts at 40%+ instead of 0%:** the `startTime = performance.now()` reference was captured at `useEffect` mount, but the counter loop runs much later — preloaders typically chain delays (mount → init delay → slide-in transition → THEN counter). On frame 1, `elapsed = now - startTime` already includes every pre-counter delay, so the counter jumps in mid-progress. **Detection:** reload impl, screenshot at ~1s — counter should still read low single digits, never 40%+. **Fix:** capture `startTime` inside the function that actually starts the counter, not at outer scope:
  ```ts
  // BAD — startTime captured at useEffect mount
  useEffect(() => {
    const startTime = performance.now()                  // captured here
    setTimeout(() => {                                   // 1000ms init delay
      preloader.classList.add('is-visible')
      setTimeout(() => {                                 // 700ms slide-in
        const step = () => {
          const elapsed = performance.now() - startTime  // 1700ms+ on frame 1 → 42%
          counter.textContent = `${Math.round(elapsed / MIN_LOADING_TIME * 100)}%`
          if (elapsed < MIN_LOADING_TIME) requestAnimationFrame(step)
        }
        requestAnimationFrame(step)
      }, 700)
    }, 1000)
  }, [])

  // GOOD — startTime captured when the counter actually starts
  useEffect(() => {
    setTimeout(() => {
      preloader.classList.add('is-visible')
      setTimeout(() => {
        const startTime = performance.now()              // captured here instead
        const step = () => {
          const elapsed = performance.now() - startTime  // ≈0 on frame 1 → 0%
          counter.textContent = `${Math.round(elapsed / MIN_LOADING_TIME * 100)}%`
          if (elapsed < MIN_LOADING_TIME) requestAnimationFrame(step)
        }
        requestAnimationFrame(step)
      }, 700)
    }, 1000)
  }, [])
  ```
  Generalizes to any "elapsed since start" timestamp behind a delay chain — not just preloaders.
- **Body bg flash on preloader fade-out:** preloader uses a utility class (`bg-white`, `bg-cream`, etc.) which in scoped/embedded projects can resolve to a different RGB than the global `body { background: ... }` declaration — typically because a project-scoped CSS framework (Tailwind under `[data-project]`, design-system layer) redefines the utility's color token. As preloader opacity drops, the body shows through in a different color → visible flash before content reveal. **Detection (run on impl, not ref — ref won't show the bug):**
  ```bash
  agent-browser --session <s> eval "(() => {
    const el = document.querySelector('.preloader, [class*=preload], [class*=splash]')
    return {
      body: getComputedStyle(document.body).backgroundColor,
      preloader: el ? getComputedStyle(el).backgroundColor : '(no preloader element matched — pass an explicit selector)',
    }
  })()"
  ```
  If the two RGB values differ, the fade will flash. Cross-check against ref's body: `getComputedStyle(document.body).backgroundColor` on ref gives the canonical color — both impl body AND impl preloader must match it. **Fix:** set body bg to the exact hex/rgb value (not a CSS keyword like `white`, which can't *match* a redefined utility class).
- **Post-splash reveal-all anti-pattern:** the splash-finish handler unconditionally adds `.is-visible` to *every* element with the reveal class (`.js-hero-lines`, `.js-element-appears`, etc.), including ones far below the viewport. The hero animates correctly, but every scroll-triggered reveal further down the page also fires immediately, so subsequent sections never animate in — they're already at their final state when the user scrolls to them. **Detection:** scroll the impl page slowly and watch reveal classes — if every `.js-element-appears` already has `.is-visible` at scroll=0, the handler is over-revealing. **Fix:** in the splash-finish handler, gate by viewport visibility — only reveal elements whose `getBoundingClientRect().top < window.innerHeight`; let the IntersectionObserver pick up the rest as the user scrolls:
  ```ts
  const vh = window.innerHeight
  reveals.forEach((el) => {
    if (el.getBoundingClientRect().top < vh)
      el.classList.add('is-visible')
  })
  ```

```bash
# Post-splash DOM check — run after splash should have completed
agent-browser --session <project> wait 8000
agent-browser --session <project> eval "(() => {
  const remnants = document.querySelectorAll('[class*=intro], [class*=splash], [class*=overlay], [class*=preload]');
  return JSON.stringify(Array.from(remnants).map(el => ({
    cls: el.className?.toString?.().slice(0, 40),
    display: getComputedStyle(el).display,
    opacity: getComputedStyle(el).opacity,
    pointerEvents: getComputedStyle(el).pointerEvents,
    note: el.offsetHeight > 0 ? 'STILL VISIBLE — should be removed or hidden' : 'hidden/removed ✓',
  })));
})()"
```
