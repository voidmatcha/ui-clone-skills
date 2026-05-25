# Bundle Analysis — Step 5c-a (Download & Grep)

> **This step is MANDATORY for ALL sites.** Most modern sites use JS to drive animations (GSAP, Framer Motion), smooth scroll (Lenis), intro sequences, and state transitions invisible to `getComputedStyle`.
>
> **After this step:** produce `transition-spec.json` — see `transition-spec-rules.md`.
> **Reference patterns:** see `patterns.md` for Canvas/Disc/Lottie/StateMachine/Timer detection.

## Download ALL loaded chunks (MANDATORY)

Modern frameworks code-split aggressively. Page-specific logic lives in lazy-loaded chunks, not the main bundle.

```bash
agent-browser --session <s> eval "
(() => {
  const entries = performance.getEntriesByType('resource');
  const scripts = entries
    .filter(e => e.initiatorType === 'script' && e.name.endsWith('.js'))
    .map(e => e.name)
    .filter(n => !n.includes('cloudflare') && !n.includes('analytics') && !n.includes('gtag'));
  return JSON.stringify(scripts);
})()
"

mkdir -p tmp/ref/<component>/bundles
# Download each chunk (HTTPS only, read-only analysis, never execute)
```

## Custom scroll engine detection (MANDATORY)

**Step 1: Behavioral detection**

```bash
agent-browser --session <s> eval "
(() => {
  const html = document.documentElement;
  const body = document.body;
  const htmlS = getComputedStyle(html);
  const bodyS = getComputedStyle(body);
  const nativeScrollDisabled = htmlS.overflow === 'hidden' || bodyS.overflow === 'hidden';
  const wrappers = [...document.querySelectorAll('*')].filter(el => {
    const s = getComputedStyle(el);
    return (s.position === 'fixed' || s.position === 'absolute') &&
           el.scrollHeight > window.innerHeight * 2 && el.offsetWidth >= window.innerWidth * 0.9;
  });
  const transformedWrappers = wrappers.filter(el => {
    const t = el.style.transform || getComputedStyle(el).transform;
    return t && t !== 'none';
  });
  return JSON.stringify({ nativeScrollDisabled, wrapperCount: wrappers.length, hasTransformScroll: transformedWrappers.length > 0 });
})()
"
```

**Step 2: Known library detection (after download)**

```bash
grep -liE 'new Lenis|smoothWheel|locomotive-scroll|ScrollSmoother|data-scroll' \
  tmp/ref/<component>/bundles/*.js
```

**Step 3: Scroll method verification** — when custom scroll detected, verify `window.scrollTo()` actually works. If not, use `mouse wheel` for all subsequent scroll operations. Save to `scroll-engine.json`.

## Auto-timer detection

```bash
# Take 2 screenshots 4s apart — if different, auto-timer exists
agent-browser --session <s> screenshot tmp/ref/<component>/timer-t0.png
agent-browser --session <s> wait 4000
agent-browser --session <s> screenshot tmp/ref/<component>/timer-t1.png

# Find interval timing in bundles
grep -oE 'setInterval\([^,]+,\s*[0-9]+' tmp/ref/<component>/bundles/*.js | head -10
```

## Animation library detection

> **Detailed extraction (Framer Motion decode, GSAP timeline parsing, scroll library params) → `js-animation-extraction.md`.** Run the transition extraction pipeline (Step T1→T2b) when any animation library is detected below.

### Quick detection (confirm presence)
```bash
# Framer Motion / Motion One — any hit → run transition extraction pipeline
grep -lE 'stiffness|damping|mass|bounce|useTransform|useScroll|scrollYProgress' tmp/ref/<component>/bundles/*.js

# GSAP — any hit → run transition extraction pipeline
grep -lE 'gsap\.(to|from|fromTo|timeline)|ScrollTrigger' tmp/ref/<component>/bundles/*.js

# Scroll library — any hit → run transition extraction pipeline
grep -lE 'new Lenis|smoothWheel|locomotive-scroll|ScrollSmoother' tmp/ref/<component>/bundles/*.js
```

Record detected libraries in `bundle-map.json`. Full parameter extraction happens in `js-animation-extraction.md`.

### Library-version pitfalls (silent fallbacks)

Animation libraries that change parameter names across major versions tend to **silently ignore** the old key and fall back to the library default — no console warning, no thrown error. The animation runs, but with the wrong curve / wrong duration / wrong target. AE catches it at section-compare time but the diagnosis is non-obvious because the impl source code "matches" the spec.

**Concrete pattern observed:** anime.js renamed the easing parameter (`easing` → `ease`) between major versions. A spec that reads `easing: 'easeOutCubic'` against a newer anime.js silently uses the default ease, not the requested cubic, and the entrance curve quietly becomes a quadratic. The fix is a one-character rename, but only if you know to look.

**Rule of thumb:** before assembling `transition-spec.json`, verify the parameter names you transcribed against the *current* docs of the *exact* version detected in the bundle. Don't paste from a generic "GSAP / anime.js / Framer Motion" snippet without checking the version line in the bundle first.

Cheap sanity check after the spec exists:

```bash
# Flag suspicious legacy-named keys; cross-reference the detected library version before fixing.
grep -nE '"(easing|stagger_function|delay_in_ms)"\s*:' tmp/ref/<component>/transition-spec.json \
  && echo "⚠️  legacy key(s) found — confirm the detected library version still accepts them"
```

The point is the *check*, not the specific key list — keys that are correct today get renamed tomorrow.

## Bundle values → DOM element mapping (MANDATORY)

Extracting values without mapping to DOM elements is useless. Find selector strings near animation calls:

```bash
grep -oE '"[.#][a-zA-Z][^"]{2,40}"[^;]{0,200}(duration|ease|stagger|yPercent|opacity)' \
  tmp/ref/<component>/bundles/*.js | head -30
```

Build `element-animation-map.json` mapping each selector to its animation parameters.

## Hover event listener extraction (MANDATORY)

Most modern sites use JS-driven hover (GSAP, Framer Motion, vanilla addEventListener). CSS `:hover` inspection alone misses these entirely. Search downloaded bundles for hover event patterns:

### Step 1: Find hover event registrations

```bash
# mouseenter/mouseleave/pointerenter/pointerleave event handlers
grep -nE 'mouseenter|mouseleave|pointerenter|pointerleave|onmouseenter|onmouseleave|whileHover|hoverStart|hoverEnd' \
  tmp/ref/<component>/bundles/*.js | head -30

# GSAP hover patterns — gsap.to() near mouseenter
grep -B3 -A10 'mouseenter' tmp/ref/<component>/bundles/*.js | \
  grep -E 'gsap\.(to|from|fromTo)|duration|ease|stagger' | head -20

# Framer Motion whileHover props
grep -oE 'whileHover:\{[^}]+\}' tmp/ref/<component>/bundles/*.js | head -10
```

### Step 2: Map hover handlers to DOM elements

```bash
# Find selector strings near hover event registrations
grep -B5 'mouseenter\|pointerenter' tmp/ref/<component>/bundles/*.js | \
  grep -oE '"[.#][a-zA-Z][^"]{2,40}"' | sort -u

# Find class-based hover targets
grep -oE 'querySelectorAll?\(["\x27][^)]+["\x27]\)[^;]{0,100}(mouseenter|pointerenter)' \
  tmp/ref/<component>/bundles/*.js | head -20
```

### Step 3: Extract hover animation parameters

For each matched hover handler, extract the animation values:

```bash
# GSAP hover animations — extract duration, ease, and property values
grep -A20 'mouseenter' tmp/ref/<component>/bundles/*.js | \
  grep -oE '(duration|ease|stagger|opacity|scale|y|x|rotate|transformOrigin):\s*["\x27]?[^,}\s]+' | head -30

# Custom easing curves
grep -oE 'CustomEase\.create\([^)]+\)|ease:\s*"[^"]+"' tmp/ref/<component>/bundles/*.js | head -10
```

**Save results to** `tmp/ref/<component>/hover-bundle-map.json`:
```json
{
  "hoverHandlers": [
    {
      "bundleFile": "slater-12345.js",
      "selector": ".case__item",
      "event": "mouseenter",
      "animations": [
        { "target": ".case__img-inner", "duration": 0.7, "ease": "klaassens", "scale": 1.05 },
        { "target": ".case__hover-overlay", "duration": 0.5, "ease": "power2.out", "opacity": 1 }
      ]
    }
  ]
}
```

**Cross-reference with `hover-deltas.json`:** Every element with a visual delta from interaction-detection Step 5d-2 must have either:
1. A CSS `transition` duration (from computed style), OR
2. A bundle hover handler (from this step)

If neither → flag as `"timingSource": "unknown"` in `interactions-detected.json`. This is a data gap that must be resolved before generation.

## Cross-component DOM manipulation detection

```bash
grep -oE 'querySelector\([^)]+\)\.(style\.\w+|classList\.(add|remove|toggle))' \
  tmp/ref/<component>/bundles/*.js | head -20
```

Record as `type: "cross-component"` in `interactions-detected.json`.

## Preloader/Splash Detection

**Detection (here, Step 5c-a):** Multiple signals — **any one** confirms splash exists. Signal 1 catches the standard-name case; signals 2–4 catch the failure modes (BEM-prefix loaders, anime.js + Barba transitions, body-class gates) where the standard grep list misses entirely. Each failure-mode signal cross-refs the full protocol in `splash-extraction.md`.

```bash
# Signal 1: Bundle grep — standard names
grep -l "preloader\|Preloader\|pre_loader\|splash\|introAnimation" tmp/ref/<c>/bundles/*.js

# Signal 2: DOM class on html/body — broadened with BEM-prefix selectors
#  → Full protocol: splash-extraction.md "Signal B (BEM-prefix loader element scan)"
agent-browser --session <s> eval "(() => {
  const html = document.documentElement;
  const body = document.body;
  const preloaderEl = document.querySelector('[class*=preloader], [class*=loader], [data-preloader], #js-loader, .o-loader, .m-loader, .a-loader');
  return JSON.stringify({
    htmlClass: html.className,
    bodyClass: body.className,
    preloaderEl: preloaderEl ? (preloaderEl.id || preloaderEl.className) : null,
    hasPreloading: html.classList.contains('rk-preloading') || html.classList.contains('is-loading') || html.classList.contains('loading') || body.classList.contains('loading'),
  });
})()"

# Signal 3: anime.js + Barba transition entrypoint (page-load splash often lives in transitions[].once())
#  → Full protocol: splash-extraction.md "Signal C (Extended bundle grep)"
grep -lE 'anime\.timeline|anime\(\{' tmp/ref/<c>/bundles/*.js
grep -nE '\bonce\s*\(\s*\)\s*\{|basicTransition|barba\.' tmp/ref/<c>/bundles/*.js | head -20

# Signal 4 → splash-extraction.md "Signal A (html/body class transition)" — pointer, not runnable here
#   Two-eval protocol: capture html/body class right after `open`, again at +6s, diff for
#   is-loading→is-loaded and -once/-hideLogo/-loaded body classes that gate hero entry animations.
```

If **any** signal hits → mark `"hasPreloader": true` in `interactions-detected.json`. Then:
- Extract timeline from bundle. Full procedure → `splash-extraction.md` (covers BEM-prefix scan, anime.js timelines, html class diff, extracted-CSS gap).
- Step 5e capture verification will automatically capture the splash (record from blank → navigate).
- Step 6 Phase A idle capture provides additional Tier 1 AE confirmation.

**Why four signals, not two:** Signals 1–2 miss sites that (a) name their loader `o-loader` / `js-loader` rather than `preloader`, (b) use anime.js instead of GSAP so the timeline isn't grep-visible by `gsap.` prefix, and (c) place the splash inside `transitions[].once()` (Barba page-transition pattern) where the function-name itself is the only stable grep target. Concluding `hasPreloader: false` from signals 1–2 alone leaves the body's gating class (`-once`, `-loaded`) unhandled, which silently freezes every hero/section entry animation in their `from` state.

## Output

After completing all analysis, produce two documents per `transition-spec-rules.md`:
1. `bundle-map.json` — which chunk owns which feature
2. `transition-spec.json` — complete transition specification (DRAFT, to be verified)

Then proceed to **Step 5e: Capture Verification** (in `transition-spec-rules.md`).

## Fix-iteration helper: bundle-grep

When post-implement or transition-compare fails and you need to inspect the
ref's actual source (downloaded bundles, captured HTML/CSS) before writing a
fix, use:

```bash
bash $PLUGIN_ROOT/scripts/extract/bundle-grep.sh tmp/ref/<c> '<pattern>'
```

Searches `tmp/ref/<c>/{bundles,html,css}/` recursively and prints each match
as `file:line:snippet`. Use this to:
- Find the ref's actual GSAP/ScrollTrigger/IO/IX2 call for a failing selector
- Confirm a `transition-spec.json` `source_chunk` claim is accurate (the
  `spec-bundle-grounding` post-implement check also validates this)
- Find ref CSS rules for a class your impl renders differently

Empty output means the pattern is not in the captured ref — re-check the
selector spelling or whether capture missed a chunk.
