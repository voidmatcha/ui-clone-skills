# Diagnosis → Fix: Root Cause Patterns

When a visual mismatch occurs, one of 10 root causes (A–J) applies. **Identify the category first** — then apply the targeted fix. Random code tweaking without diagnosis wastes 10+ minutes.

---

## How to pick the right category

```
computed-diff shows diff?
  → YES: color/font/weight → Root Cause B (CSS Cascade)
  → YES: display/height/position → Root Cause C (Missing Wrapper) or A (DOM Mismatch)
  → NO: element renders but wrong shape/behavior → Root Cause D (Wrong Element Type)
  → NO: but ::before/::after pseudo missing → Root Cause G (Pseudo-element)

Animation doesn't animate?
  → Root Cause E (Animation)

Animation/scroll broken AND layout.tsx loads a *.min.js bundle?
  → Root Cause F (Legacy JS Bundle)

Layout looks structurally wrong (items in wrong order / missing container)?
  → Root Cause A (DOM Mismatch)

Element appears missing / overlapping unrelated content despite being in the DOM
(footer, sticky bar, badge), and computed `position: absolute`?
  → Root Cause H (Stray Absolute Positioning)

Element is *double-translated*, *double-rotated*, or *double-scaled* (e.g. arrow rotated
-180° instead of -90°, preloader element lands at -74px instead of -37px), AND the host
app uses Tailwind v4 while the cloned project uses Tailwind v3?
  → Root Cause I (Tailwind v3/v4 Transform Conflict)

Layout overflows / appears broken at *exactly one viewport width* (e.g. 768px) but is fine
1px on either side, AND the project uses both Tailwind responsive prefixes (`md:`, `lg:`)
and a project-scoped `@media (max-width: <bp>px)` rule?
  → Root Cause J (Tailwind ↔ CSS @media Boundary Collision)
```

---

## Root Cause A: DOM Mismatch

**Symptoms:** Content present but layout wrong; items in wrong order; class names not matching CSS rules; elements unstyled despite CSS being loaded

**Diagnosis:**
```bash
# 1. Compare display/flexDirection/position
bash "$SCRIPTS/computed-diff.sh" <session> <orig> <impl> ".section" ".section > *" ".section > * > *"

# 2. Verify child order
agent-browser --session <ref> eval "[...document.querySelector('.inner').children].map(c => c.className)"

# 3. Verify wrapper presence
agent-browser --session <ref> eval "document.querySelector('.section').children[0].className"

# 4. Check inner class namespace (Swiper, carousels)
agent-browser --session <ref> eval "document.querySelector('.swiper-slide').innerHTML"
```

**Common patterns:**
- Missing `.main_inner` wrapper → CSS margin/padding on wrapper collapses, content has no breathing room
- CTA items in wrong DOM order (title→mascot vs mascot→title) — DOM order ≠ visual order when CSS positions elements
- Swiper slide inner class namespace mismatch (`ReviewList_*` vs `ReviewItem_*`) → all inner elements unstyled even though CSS is loaded
- Portal elements in wrong container (modal, dropdown outside target)

**Fix:** Always `agent-browser --session <ref> eval "document.querySelector('.target').outerHTML"` on ref BEFORE writing any JSX. Screenshot ref for structural reference. Never infer DOM order from screenshots.

---

## Root Cause B: CSS Cascade Conflict

**Symptoms:** CSS rule present in file, property not applied; `computed-diff.sh` shows different value despite correct class name; color/weight/font wrong

**Diagnosis:**
```bash
# 1. Check computed value on both — the diff is the fix target
bash "$SCRIPTS/computed-diff.sh" <session> <orig> <impl> ".footer_link" ".btn_lang" "a" "button"

# 2. Grep original CSS for the property
grep -E "\.footer_link|\.btn_lang" tmp/ref/<c>/css/app.css

# 3. Enumerate ALL host CSS resets (embedded projects)
grep -E "^button\b|^a\b|^button,|^a,|^select\b|^img\b|^body\b" /path/to/host.css
```

**Common patterns:**
- Host CSS `a { color: #1a1d24 }` beats `.footer_link { color: #fff }` — host rule in later stylesheet wins specificity
- Button reset missing `font-weight` when host rule is `button,a { font-weight: 400 }` — `a.Cls` not overridden
- `body { line-height }` not scoped to `[data-project]` in embedded project
- `img { display: block }` from host makes `text-align: center` ineffective on parent → fix: `margin: 0 auto` on `<img>`
- `@theme { --font-sans }` in separate CSS file silently ignored

**Fix:** Run `grep -E "^button\b|^a\b|^select\b|^img\b|body\b" host.css`. Inventory ALL resets. Add all overrides in one block in layout.tsx — not reactively one by one. One visible cascade conflict = there are more.

---

## Root Cause C: Missing Wrapper or Layout Container

**Symptoms:** Content present and styled, but spacing/alignment wrong; section height collapsed; shadow clipped; overflow unexpected

**Diagnosis:**
```bash
# 1. Is first child a wrapper?
agent-browser --session <ref> eval \
  "document.querySelector('.section').children[0].tagName + ' ' + document.querySelector('.section').children[0].className"

# 2. What does the wrapper's CSS define?
grep -A10 "\.main_inner\|\.wrapper\|\.inner" tmp/ref/<c>/css/app.css | head -20

# 3. Compare section heights ref vs impl
bash "$SCRIPTS/computed-diff.sh" <session> <orig> <impl> ".section" ".section > div"
```

**Common patterns:**
- `.main_inner` wrapper missing → inner content touches section edges directly; heading appears inside wrong visual container
- Overflow container missing → `box-shadow` clipped by `overflow: hidden` on parent (fix: `padding-bottom` on wrapper)
- Sticky sub-nav container wrong → `position: sticky` needs a direct scroll parent with `overflow: auto/scroll`
- Grid cell wrapper missing → images in `.card` without inner `<div>` that CSS targets

**Fix:** Never skip the wrapper. Verify structure BEFORE styling. Screenshot ref to see where wrapper boundaries are.

---

## Root Cause D: Wrong Element Type or Interaction Trigger

**Symptoms:** Element renders but doesn't behave right; width wrong; dropdown doesn't open; click/hover doesn't fire; interaction missing

**Diagnosis:**
```bash
# 1. Verify tag name and inner structure
agent-browser --session <ref> eval "document.querySelector('.btn_area').innerHTML"
agent-browser --session <ref> eval "document.querySelector('.btn_lang').tagName"

# 2. Check computed appearance (is it a styled native element?)
agent-browser --session <ref> eval "getComputedStyle(document.querySelector('.btn_lang')).appearance"

# 3. Test if interaction fires
agent-browser --session <impl> eval \
  "document.querySelector('.btn').dispatchEvent(new MouseEvent('mouseenter')); document.querySelector('.btn').getAnimations().length"
```

**Common patterns:**
- `<select>` styled with `appearance: none` + absolute SVG chevron looks identical to `<button>` in screenshots — but has different width, different CSS reset needs, native dropdown behavior
- `<a>` vs `<button>` — host CSS resets differ; override must match BOTH element types when host rule is `button,a {}`
- Hover JS event listener not firing: missing `mouseenter` dispatch; `mouseover` event wrong
- SVG rotation double-applied: CSS already rotates `.slider_prev svg { transform: rotate(180deg) }`, JSX adds another → 360° total (both arrows look identical)

**Fix:** Always verify tag name. Check ref HTML: `agent-browser --session <ref> eval "document.querySelector('.selector').outerHTML"`. Grep CSS for existing transforms before adding inline: `grep "\.slider_prev.*transform\|rotate" *.css`.

---

## Root Cause E: Animation / Transition Not Applied or Wrong Easing

**Symptoms:** Element renders static when it should animate; animation snaps instead of transitions; timing wrong; scroll effect doesn't update

**Diagnosis:**
```bash
# 1. Is CSS transition defined?
agent-browser --session <impl> eval \
  "getComputedStyle(document.querySelector('.target')).transitionDuration + ' ' + getComputedStyle(document.querySelector('.target')).transitionTimingFunction"

# 2. Are WAAPI animations registered?
agent-browser --session <impl> eval "document.querySelector('.target').getAnimations().length"

# 3. What library drives it?
grep -E "gsap|Lenis|ScrollTrigger|requestAnimationFrame|transition" tmp/ref/<c>/bundles/*.js | head -20

# 4. Compare timing
SCRIPTS="${PLUGIN_ROOT:+$PLUGIN_ROOT/skills/visual-debug/scripts}"
SCRIPTS="${SCRIPTS:-${CODEX_PLUGIN_ROOT:+$CODEX_PLUGIN_ROOT/skills/visual-debug/scripts}}"
SCRIPTS="${SCRIPTS:-${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/visual-debug/scripts}}"
SCRIPTS="${SCRIPTS:-${VISUAL_DEBUG_SCRIPTS_DIR:-}}"
if [ -z "$SCRIPTS" ]; then
  for root in "${UI_CLONE_ROOT:-}" "$PWD" "$PWD/.." "$PWD/../.." "${INSTALL_DIR:-$HOME/.local/share/ui-clone-skills}"; do
    [ -n "$root" ] && [ -f "$root/skills/visual-debug/scripts/ae-compare.sh" ] && SCRIPTS=$(cd "$root/skills/visual-debug/scripts" && pwd) && break
  done
fi
[ -n "$SCRIPTS" ] || { echo "Set VISUAL_DEBUG_SCRIPTS_DIR or PLUGIN_ROOT" >&2; exit 1; }
bash "$SCRIPTS/transition-compare.sh" <orig-url> <impl-url> <session> tmp/ref/<c>
```

**Common patterns:**
- **RAF loop for class-toggle** → CSS `transition: height 1.06s` restarts every frame → snap instead of animate. Fix: use scroll event, not RAF, for class changes
- **Scroll event on smooth-scroll site** → Lenis/Locomotive absorbs scroll events → `addEventListener('scroll')` fires 0 times. Fix: RAF + `getBoundingClientRect()`
- **GSAP baked init styles** (`visibility: hidden`, `opacity: 0`) not reset → element stays invisible forever
- **Double-apply transform** → CSS already rotates, JSX adds another → wrong result
- **`.effect-data` IntersectionObserver missing** → section blank (all `opacity: 0` by CSS default, JS never adds `active` class)
- **IO ref on the transformed child of an `overflow: hidden` mask** → `intersect: false` forever, reveal never fires, no console error. IO respects ancestor clipping; a child translated 100% out of its `overflow: hidden` parent has zero visible rect. **Fix:** put the IO ref on the static outer wrapper, apply the transform to an inner child. See `transition-implementation.md` → "IntersectionObserver placement for masked reveals". Quick check: run a manual IO `eval` on the suspected element — if `intersect: false` while `boundingClientRect` is inside `rootBounds`, it's ancestor clipping.
- **Card stack height not pre-fixed** → `height: auto → 64px` not CSS-animatable; must set `item.style.height = item.offsetHeight + 'px'` before scroll handler runs
- **JS toggles a subset of a CSS multi-state set** → CSS defines 3+ states for one element (default + `-animateIn` + `-animateOut`, or idle + active + complete), JS controller toggles only one. Removing the active class reverts to *default*, but default ≠ exit state. When default == entry state (animation "from" values baked into the resting CSS — e.g. a non-identity `transform` / `opacity: 0` on the base selector), exit animates *back toward entry direction*, AND any micro-scroll near the section's end-trigger oscillates between in/default → flicker. **Detect (static):** grep CSS for `\.<section>\.\-[a-zA-Z]+ \{` distinct state classes, compare to `classList.toggle('-stateName', ...)` calls in the JS controller — JS must cover the full set. **Detect (runtime, when section-compare PASSes but the bug is reported visually):** static screenshots can't see this — a section can pass section-compare and still oscillate at its boundary, because settled positions inside/before/after the section all look correct; the flicker only exists during the scroll cross. Reproduce by scrolling across the suspect boundary in 100px increments via `agent-browser --session <s> mouse wheel 100` (×N), screenshotting at each step, then AE-compare consecutive frames — non-zero AE on a "settled" run = state class toggling on/off as `scrollY` crosses the trigger. **Fix:** add the missing branch (e.g. `passedEnd = scrollY >= endTrig; el.classList.toggle('-animateOut', passedEnd)`). Same rule for hybrid controllers that use scroll-position calculation (`scrollY` vs `topAbs ± vh*offset`) instead of pure IntersectionObserver — the position math computes a clean `inRange`/`passedEnd` split, but the class-toggle code often only writes the `inRange` half.

**Fix:** Check `transition-spec.json` for expected easing + duration. Run `transition-compare.sh`. Rule: **RAF drives transform values** (parallax progress); **scroll events drive class changes** (toggle animations). Never mix.

### Stuck-reveal triage flow (3 steps, in order)

When a scroll-triggered reveal "doesn't trigger" — opacity stays 0, transform stays at the initial offset — diagnose in this order. Each step takes seconds and rules out a whole class:

```bash
SCRIPTS="${PLUGIN_ROOT:+$PLUGIN_ROOT/skills/visual-debug/scripts}"
SCRIPTS="${SCRIPTS:-${CODEX_PLUGIN_ROOT:+$CODEX_PLUGIN_ROOT/skills/visual-debug/scripts}}"
SCRIPTS="${SCRIPTS:-${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/visual-debug/scripts}}"
SCRIPTS="${SCRIPTS:-${VISUAL_DEBUG_SCRIPTS_DIR:-}}"
if [ -z "$SCRIPTS" ]; then
  for root in "${UI_CLONE_ROOT:-}" "$PWD" "$PWD/.." "$PWD/../.." "${INSTALL_DIR:-$HOME/.local/share/ui-clone-skills}"; do
    [ -n "$root" ] && [ -f "$root/skills/visual-debug/scripts/ae-compare.sh" ] && SCRIPTS=$(cd "$root/skills/visual-debug/scripts" && pwd) && break
  done
fi
[ -n "$SCRIPTS" ] || { echo "Set VISUAL_DEBUG_SCRIPTS_DIR or PLUGIN_ROOT" >&2; exit 1; }

# 1. Is the reveal even wired in code? (catches "extracted into spec but never implemented")
bash "$SCRIPTS/transition-spec-coverage.sh" tmp/ref/<component> apps/<app>/src/projects/<component>

# 2. Is the reveal wired but stuck at runtime? (catches IO+overflow:hidden, GSAP baked init, missing observer)
bash "$SCRIPTS/reveal-trigger-check.sh" <session>-reveal <impl-url> 1280 800

# 3. Is the reveal triggering but with wrong easing/duration? (catches timing-only mismatches)
bash "$SCRIPTS/transition-compare.sh" <orig-url> <impl-url> <session> tmp/ref/<component>
```

**Read the output of step 2 first.** Its parent-chain column names the `overflow: hidden` ancestor — if one is listed, you're hitting the IO+clip bug class. Move the observer ref one level up. Step 3 (`transition-compare`) only meaningfully runs once steps 1 and 2 are clean — it cannot distinguish "easing wrong" from "transition never started".

**Pattern recipes (see `transition-implementation.md` → Anti-pattern catalog):**
- **A** — IO-fire-once vs scroll-scrub (ref scrubs forward+back, naive impl flips once and stays)
- **B** — Viewport-aware scroll-scrub offsets (works on laptop, never reaches `progress=1` on mobile / Footer-CTA bug)
- **C** — `completeAt` headroom for shuffle/stagger tails (last element stuck ~98% because eased curves never reach 1.0)
- **E** — WAAPI reveal semantics (round-trip scrolling reverses the animation; `finish()` snaps to hidden)
- **F** — IntersectionObserver + `overflow:hidden` clipping (IO never fires because child is clipped out of every viewport rect)

---

## Root Cause F: Legacy JS Bundle Conflict

**Symptoms:** Animation/scroll behavior broken *and* React component logic looks correct; class-toggle fires immediately instead of after delay (or never fires); no JS errors in console

**Trigger:** `layout.tsx` loads a `*.min.js` / `vendor.js` bundle alongside the React app.

**Diagnosis:**
```bash
# 1. Does the page load a legacy bundle?
grep -rn '\.min\.js\|vendor\.js\|bundle\.js' src/app/layout.tsx

# 2. What globals does the bundle expose? (its init classes / scroll engines)
agent-browser --session <s> eval "
  Object.keys(window).filter(k => /scroll|nav|swiper|lottie|animation|player/i.test(k))
"

# 3. What class selectors does the bundle querySelector?
grep -o "querySelector('[^']*')\|querySelector(\"[^\"]*\")" public/js/*.min.js \
  | grep -oE "['\"][.#][^'\"]+['\"]" | sort -u | head -30

# 4. Does the bundle define page-scoped init objects with hardcoded selectors?
# Look for patterns like: { el: '.page-wrap', mainEl: '.container', containerEl: '.inner' }
grep -o 'el:[[:space:]]*"[^"]*"\|el:[[:space:]]*'"'"'[^'"'"']*'"'"'' public/js/*.min.js | sort -u | head -20
```

**Common patterns:**
- **React component + bundle both manage the same DOM class** → bundle adds `is-active` on `window.load`, React component also adds it after a timeout → double-fire, wrong timing, or race condition. **Fix: remove the React component; let the bundle own it.**
- **Renamed class breaks bundle querySelector** → class renamed to avoid a CSS framework conflict (e.g. `.container` → `.nc-container`) → bundle's `querySelector('.container')` returns `null` → entire page init fails silently; all animations/scroll broken. **Fix: keep the original class, add the override class alongside** (`className="nc-override container"`), then use CSS to neutralize only the conflicting property.
- **Bundle init fails silently** → no console error, no warning; page just doesn't animate. Diagnose: check that the bundle's expected globals are populated after `window.load` (step 2 above).
- **CSS framework utility collides with bundle selector** → class names like `.container`, `.sticky` mean something in both Tailwind and the bundle. Renaming the Tailwind class away breaks the bundle. See `common-selectors.md` → "Tailwind Utility Class Collision Canaries".

**Fix:**
1. If `layout.tsx` loads a bundle: **the bundle owns class-toggle logic**. Do not re-implement scroll/reveal/sticky in React for the same elements.
2. When a CSS framework conflicts with a class the bundle queries: keep the original class, add the override class alongside, override only the conflicting CSS property with `!important`.
3. Rule: **one owner per DOM class**. If the bundle toggles it, React doesn't. If React toggles it, remove it from the bundle scope (or don't load the bundle on that page).

## Root Cause G: Pseudo-element Indicator Not Detected by computed-diff

**Symptoms:** Active tab/nav indicator visually wrong (wrong position, missing, or extra) even though `computedStyle` on the element looks correct. `computed-diff.sh` reports no mismatch.

**Root cause:** `computed-diff.sh` compares `getComputedStyle(el)` only — it does NOT check `::before` / `::after`. Sites often use pseudo-elements for active indicators (underline bars, dots, highlights).

**Diagnosis:**
```bash
# Check ::before and ::after on the suspicious element
agent-browser --session <s> eval "
var el = document.querySelector('[class*=tab_item], [class*=nav_item], [role=tab]');
['::before', '::after'].map(function(pseudo) {
  var s = window.getComputedStyle(el, pseudo);
  return { pseudo: pseudo, content: s.content, display: s.display, h: s.height, bg: s.backgroundColor, position: s.position, bottom: s.bottom, left: s.left, right: s.right, borderRadius: s.borderRadius };
});
"
```

**Common pattern (tab active indicator):**
```
::before {
  content: "";
  display: block;
  position: absolute;
  bottom: 0px;
  left: 5px;
  right: 5px;
  height: 3px;
  background-color: <brand-color>;
  border-radius: 2px;
}
```

**Fix:** In React, implement as a child `<span>` with `position: absolute` matching the pseudo-element's geometry. Ensure the parent has `position: relative`. Use the **measured** `bottom`, `left`, `right`, `height`, `border-radius` values exactly — do not guess or adjust visually.

---

## Root Cause H: Stray Absolute Positioning

**Symptoms:** An element appears "missing" (footer, watermark, sticky CTA) — or, conversely, is unexpectedly visible overlapping unrelated content. AE/SSIM/section-compare may not catch it because the element does render *somewhere* on the page; just not where DOM order suggests. Especially common after porting a site where the original CSS assumed the element was a descendant of a positioned wrapper, but the port made it a sibling.

**Root cause:** `position: absolute` with any offset (`top`/`bottom`/`left`/`right`) resolves against the **nearest non-static ancestor**. When no ancestor is positioned, the offset resolves against `<body>` — parking the element near the document origin where it overlaps unrelated content (or scrolls out of view on shorter pages).

**Pattern:** original CSS sets `top: <large-px>` on an element that was authored as a descendant of `position: relative` wrapper; the port restructures the tree so the element is a sibling (or top-level) of that wrapper, leaving the offset to resolve against `<body>`. Shorter viewports amplify the visible breakage because the rendered y-coordinate falls further outside the visible scroll range.

**Diagnosis:**
```bash
# Run the dedicated check (one-shot, single URL — no ref needed):
bash "$SCRIPTS/stray-absolute-check.sh" <session> <impl-url> <viewport-w> <viewport-h>
# Exit 0 = clean. Exit 1 = stray absolutes found, table printed.

# Or inline:
agent-browser --session <s> eval "
[...document.querySelectorAll('*')].filter(el => {
  if (getComputedStyle(el).position !== 'absolute') return false;
  let p = el.parentElement;
  while (p && p !== document.documentElement) {
    if (getComputedStyle(p).position !== 'static') return false;
    p = p.parentElement;
  }
  return true;
}).map(el => ({ tag: el.tagName, cls: el.className, top: getComputedStyle(el).top }))
"
```

**Smoking-gun signals:**
- The flagged element is the **last child** in its DOM parent but is rendered *above* its prior sibling.
- A footer/sticky bar reports `top: NNNpx` with `parent.position: static`.
- Page total height < expected (because the absolute element doesn't contribute to flow).

**Fix patterns:**
1. **Move the element inside the intended positioned ancestor** so the original offset resolves correctly. Best when the original CSS was authored against a specific wrapper.
2. **Convert to natural flow:** change `position: absolute` to `position: relative` (or remove positioning) so the element flows after its DOM siblings. Best when the element should genuinely be at the document end.
3. **Pin to viewport:** `position: fixed; bottom: 0` if the element should stick to the viewport regardless of scroll.
4. **Add `position: relative` to the intended ancestor** if you can't restructure the tree but just need to establish positioning context.

**Don't:**
- Don't add a hardcoded large-pixel offset to "push the element to the bottom" — that breaks at every viewport size and on every content change.
- Don't wrap with a fake fixed-height `position: relative` container just to anchor a stray absolute — that's CSS-by-accident; pick option 1 or 2 instead.

**Pattern recipe:** `transition-implementation.md` → Anti-pattern **G** (Footer-disappeared / stray `position: absolute`) — same root cause, with the framework-agnostic fix pattern restated.

---

## Root Cause I: Tailwind v3/v4 Transform Conflict

**Symptoms:** Element is *doubly* translated/rotated/scaled despite using a single utility class. Common manifestations:
- Preloader marker drifts to `-74px` when the design calls for `-37px` (`-translate-x-1/2` applied twice).
- Arrow next to a heading appears horizontal (`-180°`) when ref shows it vertical (`-90°`) — `-rotate-90` applied twice.
- Off-screen element (footer wordmark, mobile menu) never enters viewport even after the reveal class is added.
- The element's `getBoundingClientRect()` shows it at the wrong position but no inline transform is set.

**Root cause:** The host app (e.g. Tailwind v4 monorepo / showcase) and the cloned project (Tailwind v3 in `_<project>.css`) both define the same utility class, but with **different generated CSS**:

| Utility | Tailwind v3 emits | Tailwind v4 emits |
|---|---|---|
| `.-translate-x-1/2` | `transform: translate(var(--tw-translate-x), var(--tw-translate-y)) ...` | `translate: var(--tw-translate-x) var(--tw-translate-y)` |
| `.-rotate-90` | `transform: ... rotate(var(--tw-rotate)) ...` | `rotate: var(--tw-rotate)` |
| `.-scale-x-[1]` | `transform: ... scaleX(var(--tw-scale-x)) ...` | `scale: var(--tw-scale-x) var(--tw-scale-y)` |

Both rules apply simultaneously because they target different CSS properties (`transform` vs `translate`/`rotate`/`scale`). The browser composes them — so the element gets translated/rotated/scaled twice.

**Diagnosis:**
```bash
# Inspect a suspected element — do BOTH transform AND translate/rotate/scale resolve to non-identity?
agent-browser --session <s> eval "(() => {
  const el = document.querySelector('<selector>');
  const cs = getComputedStyle(el);
  return JSON.stringify({
    transform: cs.transform,            // v3 path
    translate: cs.translate,            // v4 path
    rotate: cs.rotate,                  // v4 path
    scale: cs.scale,                    // v4 path
    twX: cs.getPropertyValue('--tw-translate-x'),
    twY: cs.getPropertyValue('--tw-translate-y'),
    twR: cs.getPropertyValue('--tw-rotate'),
  });
})()"

# Smoking gun: transform != identity AND (translate != none OR rotate != none OR scale != none)
# e.g. transform: matrix(0,-1,1,0,0,0)  AND  rotate: -90deg  →  total -180°
```

**Fix pattern** — null v4's individual properties for utilities defined by both. Place in the project-scoped globals.css:

```css
/* v3's composed `transform` is canonical for the cloned project; null v4's
   individual properties for shared utility class names so they don't compound. */
[data-project="<name>"] :is(
  .-translate-x-1\/2, .translate-x-full, .-translate-x-full,
  .-translate-y-1\/2, .translate-y-full, .translate-y-0
) { translate: none !important; }

[data-project="<name>"] :is(
  .-rotate-90, .rotate-90, .max-lg\:-rotate-90, .max-lg\:rotate-90
) { rotate: none !important; }

[data-project="<name>"] :is(
  .-scale-x-\[1\], .scale-x-\[-1\]
) { scale: none !important; }
```

**Subtle gotcha — Tailwind v4 minifier collapses your override:** writing `transform: none !important; translate: 0 0 !important` together gets collapsed by the v4 minifier into a single `transform: translate3d(0,0,0) !important` and the `translate:` declaration is **dropped**. The element stays translated.

**Fix:** override the underlying CSS variables, not the resolved property — v4's own rule then computes to identity:

```css
[data-project="<name>"] .js-footer-bar-logo.is-visible path {
  --tw-translate-x: 0 !important;
  --tw-translate-y: 0 !important;
}
```

**SVG path quirk:** Tailwind v3's `transform: translate(0, 100%)` on an SVG `<path>` resolves percentages against the **view-box** (because `transform-box: view-box` is the default for SVG). Tailwind v4's `translate: 0 100%` on the same path resolves against the path's **bounding box**. Different reference frames mean the same utility class produces different visual offsets in v3 vs v4. Always inspect the resolved `transform` matrix, not the raw `translate-y-full` class.

**Detection script** (run this once when standing up a new project clone in a v4 host):
```bash
agent-browser --session <s> eval "(() => {
  const out = [];
  document.querySelectorAll('[data-project=\"<name>\"] *').forEach(el => {
    const cs = getComputedStyle(el);
    const tFx = cs.transform !== 'none' && cs.transform !== 'matrix(1, 0, 0, 1, 0, 0)';
    const indiv = cs.translate !== 'none' || cs.rotate !== 'none' || cs.scale !== 'none';
    if (tFx && indiv) out.push({ tag: el.tagName, cls: el.className?.toString?.()?.slice(0,120), transform: cs.transform, translate: cs.translate, rotate: cs.rotate, scale: cs.scale });
  });
  return JSON.stringify(out, null, 2);
})()" > tmp/v3v4-conflict.json
```

**Don't:**
- Don't rename the utility classes in JSX to dodge the conflict — the cloned project's bundled JS may query them by name (see Root Cause F).
- Don't `* { transform: none !important }` — kills legitimate transforms inside the project.
- Don't override the resolved property (`translate: 0 0 !important`) without also setting `transform: none` — see minifier gotcha above. Override the CSS variables instead.

**Prevention:** Run `stray-absolute-check.sh` as part of Phase 4 / Step 8 verification. Cost is one page load — far cheaper than catching the bug later via screenshot review.

## Root Cause J: Tailwind ↔ CSS `@media` Boundary Collision

**Symptoms:** Layout looks correct at 375, 1280, 1440 — and broken at *exactly one* viewport (typically 768). Horizontal scrollbar appears, header nav and hamburger render simultaneously, fonts balloon to 2× their intended size, sections overflow `<body>` width by hundreds of pixels. Resizing 1px in either direction makes the bug disappear.

**Root cause:** The project ships a CSS rule with `@media (max-width: <bp>px)` (typically for a fluid root `font-size`, mobile-only padding, or a vertical-stack override) and the JSX uses Tailwind's `md:` / `lg:` responsive prefixes whose breakpoint is `min-width: <bp>px`. **Both ranges are inclusive at the boundary** — so at *exactly* `<bp>` pixels, both rules match and apply simultaneously:

| Width | `(max-width: 768px)` mobile rule | Tailwind `md:` (`min-width: 768px`) |
|---|---|---|
| 767 | ✅ active | ❌ inactive |
| **768** | **✅ active** | **✅ active** ← collision |
| 769 | ❌ inactive | ✅ active |

The catastrophic case is when the mobile rule changes the **root font-size** (e.g. `html { font-size: 26.667vw }` for a `1rem = 100px @ 375` mobile design). At 768 the mobile mode resolves `1rem ≈ 204.8px`, while every Tailwind `md:` utility (`md:flex`, `md:gap-[0.24rem]`, `md:text-[0.14rem]`) snaps the layout into desktop mode — desktop columns + mobile-scale rems = unrecoverable horizontal overflow. The same bug class also fires for any project rule that exclusively expects mobile mode (vertical stack, hidden desktop nav, padding overrides).

**Diagnosis** — at the exact boundary, are both rule sets active?

```bash
# Set viewport to the exact boundary, then probe both axes simultaneously.
agent-browser --session <s> open <impl-url>
agent-browser --session <s> set viewport 768 1024
agent-browser --session <s> wait 800
agent-browser --session <s> eval "(() => {
  const html = document.documentElement;
  const body = document.body;
  // mobile-mode signal: root font-size in vw mode → big rem at this width
  const rootFontSize = parseFloat(getComputedStyle(html).fontSize);
  // desktop-mode signal: a Tailwind md:flex / md:hidden element actually displays per md: rule
  const navMd = document.querySelector('nav.md\\\\:flex, header nav.lg\\\\:flex');
  const hamburger = document.querySelector('button[aria-label=\"menu\"], [class*=md\\\\:hidden]');
  return JSON.stringify({
    width: window.innerWidth,
    rootFontSize,                 // > ~30px at 768 = mobile mode active
    bodyScrollWidth: body.scrollWidth,
    htmlScrollWidth: html.scrollWidth,
    overflowing: body.scrollWidth > window.innerWidth,
    navVisible: navMd ? getComputedStyle(navMd).display !== 'none' : null,
    hamburgerVisible: hamburger ? getComputedStyle(hamburger).display !== 'none' : null,
    matchedMediaMaxBp: matchMedia('(max-width: 768px)').matches,
    matchedMediaMinBp: matchMedia('(min-width: 768px)').matches,
  }, null, 2);
})()"
```

**Smoking gun:** `matchedMediaMaxBp === true` AND `matchedMediaMinBp === true` AND (`overflowing === true` OR `rootFontSize` jumps from ~16 to ~200). Both navs visible at once is a secondary tell.

**Fix — pick ONE side:**

**A. Make the project's CSS query exclusive (preferred for `font-size` / root rules)** — shift the upper bound below the Tailwind breakpoint by 0.02px. This is the canonical pattern Bootstrap uses for exactly this reason; subpixel values are honored by all evergreen browsers:

```diff
-@media screen and (max-width: 768px) and (orientation: portrait) {
+@media screen and (max-width: 767.98px) and (orientation: portrait) {
   html:has([data-project='<name>']) { font-size: 26.667vw; }
 }
```

Apply to **every** `(max-width: <bp>px)` rule that targets the same Tailwind boundary — leaving even one inclusive rule reproduces the bug only for that property.

**B. Shift the Tailwind variant up one tier** — when the affected utilities are confined to a single component (header nav, hamburger). `md:` (768) → `lg:` (1024), `sm:` (640) → `md:` (768):

```diff
-<nav className="hidden md:flex items-center gap-[0.24rem] text-[0.14rem]">
+<nav className="hidden lg:flex items-center gap-[0.24rem] text-[0.14rem]">
-<button className="relative w-[0.32rem] md:hidden">
+<button className="relative w-[0.32rem] lg:hidden">
```

Pick A when the mobile-mode CSS is broad (fluid root font-size, container padding, section padding) — one CSS edit fixes every utility on every page. Pick B when only one component (nav, hero) needs to switch and the rest of the layout is already correct at 768.

**Don't:**
- Don't paper over with `overflow-x: hidden` on `body` — it hides the symptom, not the bug; the broken layout is still there, just invisible. You'll discover it again the next time content extends below the fold.
- Don't change Tailwind's `screens` config to `768.02px` — Tailwind utilities elsewhere in the host monorepo expect the standard breakpoint, breaking other apps.
- Don't apply A and B together unless you've verified each fix in isolation. Two simultaneous shifts make it impossible to reason about which utility resolves at which width.

**Prevention — boundary sweep is mandatory whenever a project mixes Tailwind and project-scoped `@media` rules.** Step 4-C2 measures only at 768/1280/1440 — exactly the points where collision is invisible (768 itself looks "wrong but consistent" because both sides apply uniformly across the page; only the *overflow* gives it away). Add a boundary ±1 viewport check at every detected breakpoint:

```bash
# For every CSS @media (max-width: <bp>) found in Step 4-A and every Tailwind breakpoint
# the project uses, capture at <bp>-1, <bp>, <bp>+1. The bug shows as a body.scrollWidth
# spike at exactly <bp> with no spike on either side.
for W in 767 768 769; do
  agent-browser --session <s> set viewport $W 900
  agent-browser --session <s> wait 400
  agent-browser --session <s> eval "(() => JSON.stringify({
    w: innerWidth,
    sw: document.body.scrollWidth,
    overflow: document.body.scrollWidth > innerWidth,
    rootFs: parseFloat(getComputedStyle(document.documentElement).fontSize)
  }))()" > tmp/ref/<c>/responsive/boundary-$W.json
done
```

A spike of `sw > w` at exactly `<bp>` (with `<bp>-1` and `<bp>+1` clean) is the unambiguous signature.

---

## Sub-agent / inline-diagnosis contract (Phase 4 gate failures)

This file is the catalog. Both Claude Code (via `mismatch-diagnoser` sub-agent at `.claude-plugin/agents/mismatch-diagnoser.md`) and Codex (inline at any Phase 4 gate-failure point per `.codex-plugin/plugin.json defaultPrompt`) classify failures using the catalog above and emit a structured diagnosis JSON.

### Additional root-cause classes (beyond A-J)

The A-J classes cover visual / layout mismatches surfaced by AE, computed-diff, and viewport probes. The classes below cover gate-sidecar failure modes detected by `post-implement` checks:

| Class | Symptom | Typical fix |
|---|---|---|
| **K. Stub function** | `generation-completeness` reports empty body | Fill the component body with real JSX |
| **L. Missing image asset** | `asset-transfer-check` reports 404 | Copy from `tmp/ref/<component>/static/` to `impl/public/` |
| **M. Missing library install** | `bundle-impl-coverage` reports library detected but not in package.json deps | Install package |
| **N. Wrong library wiring** | library installed but not used (e.g. `framer-motion` in deps but no `motion.*` calls) | Wire per `generation-plan.json` `componentList[].wires` |
| **O. Missing component** | impl has 0 of a section that ref has 1 | Generate missing component, add to `App.tsx` |
| **P. Wrong text content** | text differs (typo / placeholder / truncation) | Replace from `structure.json` / `extracted.json` |
| **Q. Wrong sticky rendering** | Same nav rendered N times (one per section that shows it in screenshots) | Move sticky element to App level, render once |
| **R. Wrong animation initial state** | section appears already-revealed; `animation-init-styles.json` shows entry transform | Set initial state via library `initial` / GSAP `from` / style |

### Diagnostic workflow

1. **Read the check sidecar**: e.g. `dom-mirror-check.json`, `text-fidelity-check.json`, `bundle-impl-coverage.json`, `image-fidelity-check.json`, `transition-spec-coverage.json`. Identify the specific delta.
2. **Find the matching impl source location**: grep `impl/src/` for the affected text/tag/library.
3. **Find the matching ref artifact**: where does the ref declare this element? `structure.json`, `extracted.json`, `bundle-map.json`, `interactions-detected.json`, etc.
4. **Classify**: pick ONE root-cause class from the A-J or K-R catalog. Don't return multiple primary causes.

### Output (Claude Code sub-agent path AND Codex inline path)

```json
{
  "schemaVersion": 1,
  "failingGate": "<gate>",
  "failingCheck": "<check>",
  "rootCauseClass": "<A | B | ... | R>",
  "hypothesis": "<one sentence>",
  "evidence": {
    "refArtifact": "<path:JSONPointer | path:line>",
    "implFile": "<path:line>",
    "deltaSummary": "<specific value differs>"
  },
  "suggestedFix": "<concise instruction the main agent can act on>",
  "confidence": "<high | medium | low>"
}
```

### Don'ts

- Don't apply fixes (Claude path); the main agent applies. Codex inline: diagnose first, then apply — never apply before classifying.
- Don't return multiple primary causes. If 2+ classes contribute, pick the highest-severity one and note alternates in `alternates: []`.
- Don't speculate when evidence is missing. Return `confidence: "low"` with a `needMore: [...]` array of artifacts you'd need to read.
- Don't return `rootCauseClass: "no-failure"` unless the sidecar's `status` field is actually `"pass"` — re-read carefully.
- **Don't recommend a fix that removes a detected feature from spec/plan to dodge the failing check.** If `transition-spec-coverage` fails because a sticky / scroll-scrub entry has no impl artifact, the fix is "wire the entry" (Class N — wrong library wiring), never "delete the spec entry." Treat any suggestion that shrinks the verification surface as a gate-game and refuse to emit it.
