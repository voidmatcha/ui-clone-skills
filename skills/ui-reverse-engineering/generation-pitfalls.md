# Generation Pitfalls — Step 7 Reference

CSS-to-React translation errors and failure-based diagnosis for common bugs.

## CSS-to-React translation — 3 categories

### 1. Exit animations are impossible with conditional rendering

**Wrong:** `{showSplash && <SplashScreen />}` — React removes the DOM node instantly. No CSS transition can run on a removed node.

**Right:** keep animated elements in the DOM. Control visibility with `opacity`, `visibility`, `pointer-events`, `clip-path`. Only unmount after exit animation completes (use `AnimatePresence` or a `transitionend` listener).

### 2. Callback chains break on React lifecycle timing

**Wrong:** parent passes `onComplete` → child calls it in `useEffect` timeout → parent sets state → enables scroll. Callback reference changes between renders or timing misaligns with commit phase → chain breaks silently.

**Right:** independent timers with the same duration values. Parent and child both know the intro is 4.6s — each manages state independently. Or use a shared ref (no re-renders) for time-critical flags.

### 3. Text line splitting must match CSS, not character counts

**Wrong:** `text.split()` with hardcoded char limit (`> 55 chars`). Different line breaks than browser layout.

**Right:** one of:
- Apply `overflow: hidden` + `translateY` to the whole text block (whole-block reveal)
- Use `splitText` AFTER initial render to detect CSS-computed line boundaries
- Never pre-split text in JS unless matching exact container width + font metrics

## Failure-based diagnosis

When implementation visually matches but **behaves wrong** — or matches poorly — scan this table before debugging.

| Symptom | Root cause | Fix |
|---|---|---|
| `position: fixed` element scrolls with content | Inside a `transform`-ed parent | `createPortal(el, document.body)` |
| Scroll animations don't trigger | Custom scroll engine — IntersectionObserver / `window.scrollY` don't work | Subscribe to scroll engine's value stream |
| Splash elements remain visible after completion | Missing terminal state | Explicit `opacity: 0` + cleanup, or `return null` |
| Hover works on element but not children | Children have `pointer-events: auto` intercepting parent hover | `pointer-events-none` on overlay children |
| Text wraps differently despite same font-size | Different `max-width`, or WordReveal spans add whitespace | Remove max-width constraint; check word-splitting effect |
| SVG icon looks "similar" but wrong | SVG was recreated instead of extracted | Use `outerHTML` from `inline-svgs.json` verbatim |
| Sticky element exits viewport instead of pinning | Custom scroll wrapper uses transform, not native scroll | Use scroll progress to toggle `position: relative` vs manual transform pinning |
| Animation timing "feels off" | Easing/duration mismatch | Extract exact `transition`/`animation` CSS or GSAP `ease`/`duration` from bundle |
| Menu opens but nav bar doesn't animate | Overlay opens but nav elements lack per-element transition rules | Add per-element `transform`/`opacity` transitions keyed to `isOpen` |
| Text doesn't invert color over images | Missing `mix-blend-mode: difference` on parent | Check `advanced-styles.json` — apply to container, not text |
| "Gradient text" shows as solid color | Missing `background-clip: text` + `-webkit-text-fill-color: transparent` | CSS class with exact gradient from `backgroundImage` |
| Background doesn't transition dark/light on scroll | Missing `body` class toggle + `transition: background-color` | Check `body-state.json` — toggle class via scroll handler, CSS cascade handles rest |
| Nav logo doesn't invert in dark sections | Per-element React state instead of CSS cascade | `body.dark-class .nav-logo { filter: brightness(0) invert(1) }` |
| Sticky element unsticks too early/late | Wrong wrapper (container) height | Measure `diff(stickyCenter, lastContentCenter)` after unstick; adjust until `\|diff\| < 15` |
| Hundreds of pixels of dead space below last card | Section height hardcoded too large | Reduce to `lastContentBottom + 65px` |
| Marquee/scroll animation speed or spacing wrong | Guessed `gap` + `animation-duration` | Extract exact `gap`, `animation`, `animationDuration` from marquee track |
| Splash/intro transition doesn't play | `{condition && <div>}` — React unmounts before CSS transition | Keep in DOM, animate `opacity: 0` + `pointer-events: none`. Use `AnimatePresence` if unmount required |
| Preloader counter starts at 40%+ instead of 0% on page load | `startTime = performance.now()` captured at `useEffect` mount but the counter loop runs after init/slide-in delays — `elapsed` already includes those delays on frame 1 | Capture `startTime` *inside* the animation function body, not at outer scope. See `splash-extraction.md` "Common splash failures" |
| Color flash when preloader fades out | impl preloader's utility class (`bg-white`) resolves to a different RGB than impl body's CSS keyword (`background: white`) due to scoped Tailwind/framework overrides — fade reveals the body's different color | Run `getComputedStyle` on both impl preloader and impl body; they must return identical RGB. Set body bg to the exact hex/rgb. See `splash-extraction.md` "Common splash failures" |
| First-paint flash of fallback color/state, then snaps to correct (FOUC for cascade-class overrides) | A cascade-overriding wrapper class (theme class like `-darkmode`, state class like `-loaded` / `-hideHeader`, `data-mode="dark"`, etc.) is added to `<html>` / `<body>` / a top wrapper inside `useEffect`. SSR-rendered HTML paints with the CSS *fallback* (e.g. `background: var(--bg-color, <fallback-hex>)`, the un-overridden initial state) for one frame before the effect commits. **Detection:** reload twice and diff the first vs settled frame — `agent-browser --session <s> open <url> && agent-browser --session <s> screenshot tmp/t0.png` (no `wait` between, so we catch the first paint), then `agent-browser --session <s> wait 1000 && agent-browser --session <s> screenshot tmp/t1.png`. If `t0.png` differs from `t1.png` in the wrapper-color region, this is the bug. | Stamp the wrapper class on the SSR markup directly — in Next.js, set `className="-themeFoo -stateBar"` on the top `<div>` / `<body>` in `layout.tsx` (or the project's per-route layout) so the class is present on first paint; the `useEffect` becomes a no-op for the initial render and only handles runtime toggles. Ancestor-CSS selectors (`.-themeFoo .target`) keep working unchanged. |
| Header hides on scroll-down in impl but stays visible in ref | Delta-based `translateY(-100%)` hide handler added on the assumption it's "common UX" — ref doesn't have it | Scroll ref deep, wait 500ms, check `getComputedStyle(headerEl).transform`. If `none`, do NOT add hide-on-scroll. See `no-judgment.md` Group A |
| Grid layout breaks at wider viewports | Fixed `height: 'Npx'` on grid cells | Use `aspectRatio` instead — e.g., 1col square = `aspectRatio: '1/1'`, 2col span = `aspectRatio: '2/1'`. Heights scale proportionally with column width |
| Text line-height wrong across entire page | Missing explicit `lineHeight` — Tailwind default `leading-normal` (1.5) applied | Always extract and set exact `lineHeight` from reference for ALL text. Never rely on Tailwind `leading-*` classes — use inline `style={{ lineHeight: '16.8px' }}` |
| Muted/semi-transparent text rendered as full-opacity | Color extracted as `rgb(7,85,187)` without opacity | Check ref for `rgba()` or opacity — event names, subtitles often use 20-40% opacity. Extract exact `color` including alpha |
| Font-size/spacing doesn't scale at wider viewports | All values hardcoded in px | Check if reference uses viewport-relative units (`vw`, `clamp()`, `calc()`). For sites that scale proportionally, use `aspect-ratio` on containers and relative units inside |
| Text line breaks differ from reference | Hardcoded char-count split | CSS handles wrapping naturally (single `<p>`). Per-line reveal: `splitText` after render, or `overflow: hidden` + `translateY` on whole block |
| Scroll-driven overlay doesn't disappear | `onReady` callback error prevents scroll enable; listener gated behind state that never becomes true | Decouple scroll listener registration from animation callbacks. Use `setTimeout` matching intro duration. Register wheel listener immediately; gate delta application on a ref flag |
| Logo "assembles" in original but slides in impl | Bundle uses `.fromTo(".selector > *", {y: off}, {y: 0})` — children animate individually | Loop over `element.children` with per-child `translateY` + stagger. Never translate parent container |
| Splash data shows "element was always static" | `agent-browser --session <s> eval` + `addInitScript` both missed it — GSAP set `from` before capture, so captured "initial" is mid-animation | Use video frames (Tier 1) as ground truth. Grep bundle for exact selectors + easing. DOM polling can't reliably capture first frame of DOMContentLoaded animations |
| Animation never runs on target page | Bundle has `if (isHome) { A } else { B }` — implemented B but target runs A | Read FULL conditional structure. Trace condition to source. `n \|\| (code)` means code runs when `!n`. See `splash-extraction.md` "Conditional animation branches" |
| All transitions fire sequentially but original fires simultaneously | Separate `setTimeout` for each, but GSAP timeline has multiple `.to()` at position `0` or `"<"` | Parse GSAP position params: `0` = timeline start; `"<"` = same time as previous. Multiple tweens at same position = ONE setTimeout triggering ALL |
| CSS transition can't do multi-step (A→B hold→C) | CSS transition only supports A→B. Chaining is fragile | Use WAAPI `element.animate()` with multi-keyframe + `offset` |
| Animation code changes don't take effect | Modified shared package but consuming app has stale build | Rebuild the package → restart dev server. Bundlers don't hot-reload package `dist` changes |
| Overlay clips gradually (clipPath) but original disappears as whole panel | Original uses `translateX(-100%)` to slide off-screen, not clipPath progression. ClipPath is set once and stays fixed; scroll drives transform | Check `getBoundingClientRect().x` of overlay after scrolling. If `x < 0`, translated. But also check: is overlay `position: fixed` with separate scroll, OR part of horizontal scroll content itself? If `scrollY` stays 0 while `x` changes → inside Lenis/GSAP horizontal scroll — make it `flex-none w-screen` child, not fixed overlay |
| Overlay disappears too fast or too slow on scroll | `position: fixed` with `translateX(scrollProgress * -100%)` across entire content width | Likely NOT a fixed overlay — it's the FIRST item in horizontal scroll container. Place as `flex-none w-screen h-screen` child before hero/work items. Scrolls at 1:1 with content, no separate math. Verify: original `window.scrollY` stays 0 while overlay's `x` decreases → Lenis virtual scroll, inline |
| Hover transition works but hover-OUT snaps/breaks | CSS `transition` on `:hover` state but **no initial value** on idle state for animated properties | Always declare initial values for all animated CSS properties in the idle selector. Example: if `:hover` sets `clip-path: inset(0 0 100% 0)`, the idle state MUST have `clip-path: inset(0 0 0 0)` (not omitted). Without it, browser interpolates from `none` → `inset(...)` which is not animatable. Same applies to `scale`, `transform`, `opacity`. Check `::after` pseudo-elements too — they need initial `clip-path` + `scale` for reverse transition |

| CSS `top`/`left` hardcoded in JS with viewport-specific px values | Parallax/hero items at fixed px positions based on one viewport width | Read initial position from CSS at runtime: `parseFloat(getComputedStyle(el).top)`. CSS handles responsive positioning via `%`, `clamp()`, `nth-child` — JS should only apply the delta, not the base |
| Off-screen DOM elements not implemented (mo-nav, search overlay, modals) | section-compare reports MISSING — elements exist in ref but not impl | Always enumerate ALL elements in ref DOM, including off-screen ones (`left: 2000px`, `display: none`, `visibility: hidden`). Off-screen ≠ decorative. section-compare's section detection finds them |
| CSS animation classes (`effect-flip`, `is-active`, `enter`) declared but never triggered | Classes exist in CSS, item has class in HTML, but trigger condition never fires | Check JS bundle for the trigger: `heroScrolled >= 450` → `hero.classList.add('enter')`. Every named CSS animation class needs a corresponding JS trigger. Verify in browser: `el.classList.contains('enter')` after scrolling |
| `dangerouslySetInnerHTML` used for nav messages with `<br>` / `<span>` | Bypasses JSX type-checking, hides structure, violates skill rules | Convert to JSX: `<><br /><span className="en-text">text</span></>`. Inspect the actual DOM to find the structure |
| Nav intro message HTML not converted properly | `<br>` in string becomes literal text in JSX | Use `<>first line <br /> second line</>` — never template strings with HTML tags |
| Pasted-HTML element compiles fine but its interaction is silently dead (button doesn't navigate, link doesn't scroll) | Source HTML uses inline event handlers (`onclick="..."`, `onmouseover="..."`); JSX treats those as unknown string attributes and never executes them. No console error — the handler is just absent | When ingesting `outerHTML`, grep for `\bon[a-z]+="` BEFORE conversion. For each match, port to a real React handler (`onClick={() => ...}`). If the original calls a global (e.g. `lenis.scrollTo(0, 0)`), capture the instance via a ref/context — don't assume the global exists in the React tree |
| TS error `'foo' is not a JSX.IntrinsicElements` on a tag you didn't write | Source HTML had a typo (`<snap>` instead of `<span>`, `<sectiom>` instead of `<section>`). Browsers parse unknown tags as `HTMLUnknownElement` and render them; JSX rejects | Before pasting `outerHTML` into a `.tsx`, scan for non-standard tags: `grep -oE '<[a-z]+[ />]' extracted.html \| sort -u` and reconcile against the standard HTML element list. Fix at source then re-extract — don't silently rename in the JSX, the typo may be load-bearing for a CSS selector |
| Swiper fade slides all visible / text overlapping | Swiper EffectFade sets `opacity: 1` on ALL slides via inline style — CSS `opacity: 0` is overridden | Add `!important` to non-active slide opacity: `.swiper-fade .swiper-slide:not(.swiper-slide-active) { opacity: 0 !important; position: absolute !important }`. Must be in layout-level CSS, not a component stylesheet |
| Scoped CSS selectors not matching — all styles broken | Original CSS uses compound selector like `.app.main .xxx` requiring BOTH classes on the SAME element, but wrapper only has one class | Check original CSS for compound selectors (`.a.b`) — both classes must be on the same DOM node, not parent/child. Adding the second class to wrapper `<div className="app main">` unlocks hundreds of rules at once |
| Logo/element correct size in CSS rule but renders too large on desktop | Media query with higher-specificity rule overrides base: e.g. `@media (min-width:1280px) { .parent .logo { width: 292px } }` — this is intentional large state | Check if original has scroll-triggered size reduction (`.is-scroll-down .logo { width: 104px }`). Add scroll direction tracking to wrapper element, not just header. `is-scroll-down`/`is-scroll-up` classes belong on the page root wrapper, not the header |
| CSS class used in impl doesn't exist in original stylesheet | Invented class name (e.g. `btn-news-more`) applied to element — no CSS matches so element is unstyled | Always verify class names against original DOM via `agent-browser --session <s> eval "document.querySelector('.target').className"`. Never invent class names; use exact classes from reference |
| Host CSS resets `button`/`a` properties — embedded project styles silently broken | Host stylesheet resets `button { background-color: transparent; border: 0; padding: 0; border-radius: 0 }` and `button,a { font-family: ...; font-weight: 400 }` — embedded component buttons render transparent/unstyled | **Before writing any override in layout.tsx**: run `grep -E "^button\b|^a\b|^button," host.css` to enumerate ALL resets. Write overrides for EVERY reset property at once — not just the one you noticed. Missing even one (e.g. `font-weight`) becomes a follow-up bug. |
| `a` tag font overridden by host CSS but only `button` override added | Host rule `button,a { font-weight: 400 }` — added `button.Cls { font-weight: 700 }` but `a.Cls` still gets `400` because same host rule applies to `a` | `<a>`-rendered components (e.g. link-styled buttons) have wrong font-weight while `<button>` is correct | Check if host CSS rules target `button,a` together. If so, add the same override for `a` elements too. Test computed style on BOTH element types before closing. |
| 3rd-party library replaced with custom implementation, behavior untested | Swiper/Splide/Glide replaced with `useState` slider — but these libraries define DOM structure (`swiper-wrapper`, `swiper-slide`), CSS active states (`.swiper-slide-active`), and touch handling | ⛔ If the original uses Swiper/Splide/Glide/keen-slider: use the SAME library. Check `package.json` — if already in deps, use it. Custom re-implementations always miss edge cases (touch drag, keyboard nav, CSS active states). Only replace if the library is NOT in the project at all, and document what behavior is missing. |
| JS scroll threshold guessed instead of extracted | Round number like `scrollY > 10` hardcoded for header class toggle — actual value from bundle may be `> 0`, `> 80`, or tied to element height | Scroll behavior triggers too early or too late vs reference | Grep the JS bundle for the class being toggled (e.g. `grep -E "fixed|is-scroll|scrolled" bundle.js`). Extract the exact condition. Never guess a pixel threshold. |
| `!important` used as first resort for CSS conflict resolution | Conflicts between host CSS resets and embedded project styles fixed with `!important` without investigating cascade | `!important` is valid ONLY when overriding CSS you cannot modify (e.g. a third-party stylesheet loaded externally). For own cascade conflicts: fix specificity by scoping (e.g. `.embed-root .btn { ... }`) or load order. When `!important` IS required, scope it narrowly and comment why. |
| CSS `height` transition doesn't animate — card stack items snap | `height: auto` cannot be transitioned in CSS. Without fixing a `px` value first, `height: auto → 64px` snaps instantly with no motion. The original site's JS bundle always sets `style.height = measuredPx` on each card before toggling the `hide` class. | In `useEffect` after mount, fix each item's height: `items.forEach(item => { item.style.height = item.offsetHeight + 'px'; })`. Now CSS `transition: height 1.06s cubic-bezier(...)` can animate `608px → 64px` smoothly. |
| RAF loop prevents CSS height/opacity transitions from completing | Calling `classList.add/remove('hide')` inside every `requestAnimationFrame` tick resets the CSS transition on each frame — it never completes. Items appear to snap rather than animate. | Replace RAF with `window.addEventListener('scroll', handler, { passive: true })`. Keep a `hiddenState: boolean[]` array and only call `classList.add/remove` when state actually **changes**. This lets `transition: height 1.06s` complete uninterrupted between scroll events. |
| `.effect-data .effect-value` elements stay `opacity: 0` — section renders blank | CSS sets `.effect-data .effect-value { opacity: 0 }` by default. The original site's JS adds `active` class to each `.effect-data` via scroll tracking, revealing content. Without this, the entire section appears blank even though the DOM is present. | Add a per-element `IntersectionObserver` for each `.effect-data` element (not just one observer for the section). On `isIntersecting`, add `active` class and disconnect. Pattern: `el.querySelectorAll('.effect-data').forEach(effEl => { const obs = new IntersectionObserver(([e]) => { if (e.isIntersecting) { effEl.classList.add('active'); obs.disconnect(); } }, { threshold: 0.2 }); obs.observe(effEl); })` |
| Next.js 16 `proxy.ts` throws "Proxy is missing expected function export name" | Renamed file from `middleware.ts` → `proxy.ts` but kept `export function middleware(...)`. Next.js 16 requires the function name to match the file name. | Rename the exported function: `export function proxy(request: NextRequest) { ... }`. The `config` export is unchanged. Clear `.next` cache and restart dev server if stale build errors persist. |
| Card stack has fewer items than reference | Only counted visible/initially-loaded cards. Reference may have additional cards requiring scroll to reveal, or added in a content update not visible without inspecting the full DOM. | Always verify item count via `agent-browser --session <s> eval "document.querySelectorAll('.point-items').length"` on the reference. Extract ALL card data (title, desc, list items, image paths) from reference DOM before implementing. Missing cards = missing `imgMap` entries → broken images. |

| Section implemented without verifying against reference DOM | Context pressure / multiple sections in flight → skipped `agent-browser --session <s> eval` on the original → guessed class names, item count, data structure | Component "works" but is structurally wrong (wrong class names, missing items, wrong grouping). User discovers mismatch only on visual review. | **Never guess DOM structure.** Before implementing any section, run `agent-browser --session <s> eval "document.querySelector('.target-class').outerHTML"` on the reference. After implementing, run `section-compare.sh` before declaring done. "I wrote it so it must be right" is not verification. |
| `position: sticky` parallax section only 1 viewport tall — no scroll room | `fade-animation__wrap` is `position: sticky; height: 100vh`. If parent section height = 100vh, sticky has nowhere to scroll and the section collapses to just one viewport with no parallax | Section renders blank/collapsed mid-scroll; text visible only at `scrollIntoView()` top, vanishes as soon as scroll proceeds past 100vh | Set `min-height: 300vh` on BOTH the section AND its direct child wrappers (`.section-wrap`, `.fade-animation`). `min-height: 100%` won't work on children unless parent has explicit `height` (not just `min-height`). Set `min-height` equal to the same value on all wrapper descendants. |
| Turbopack dev server doesn't recompile CSS after edit | Next.js dev mode uses content-hash CSS chunk filenames. If hash doesn't change (file path unchanged), Turbopack can serve stale `.next/dev/static/chunks/*.css` indefinitely despite source file changes | `globals.css` updated but browser still sees old compiled CSS; manual patch required | Directly patch `.next/dev/static/chunks/src_app_globals_*.css` to match your globals.css intent. Also touch the source file (`touch globals.css`) and reload browser. If still stale, restart dev server (`npm run dev`). The compiled cache file is authoritative at runtime. |

| Interaction implemented as `onClick` but original uses hover | React component uses `onClick` / `setState` for interactive list/tab/card — original JS fires on `mouseenter`. Result: hover does nothing; only click works. | Check original JS bundle for `mouseenter` / `mouseover` event listeners on the element. Use `onMouseEnter` in React, NOT `onClick`, for any component that expands/activates on hover in the reference. |
| `<br>` tags missing from multi-line desc causes section height mismatch | Desc text extracted as plain string; original HTML had `<br class="br_pc"><br class="br_tab-lg">` inside. Without these, text collapses to 30px instead of 60px → section is 30px shorter. | When extracting text content from reference, always run `el.innerHTML` not `el.textContent`. Never use `textContent` for any element that may contain `<br>` or `<span>` — check `innerHTML` and convert to JSX. |
| `scroll-title` div takes up height when it should be 0 | `scroll-title` has `opacity: 0` by default (CSS) and is only visible on `.is-sticky`/`.is-fixed`. But it still has physical height from its text content, causing a gap above the first section. | Add CSS: `.app .scroll-title:not(.is-sticky):not(.is-fixed) { height: 0 !important; overflow: hidden }` — collapsing its height until JS activates it. |
| All section-compare AE failures treated as real bugs | Hero parallax images are captured at scroll position 0 with different timing → different image positions → high AE. Header background is dynamic (theme class applied after load). These show as FAIL but are not layout bugs. | Before debugging AE failures: read the diff image first. If diff shows only the hero area with random image positions, or header-only diff with consistent AE (≈4360), these are dynamic rendering artifacts — not bugs. Focus on section content below the hero. |

| Swiper `spaceBetween` gap invisible without Swiper.js | Original CSS sets `card__list { gap: 0 }` and relies on Swiper JS injecting `margin-right: Npx` inline per slide. Clone copies class names (`swiper-wrapper`, `swiper-slide`) but not the library → cards render flush with `gap: 0`. | Cards are visually stuck together with no spacing; user reports "gaps not applied" | Check original: `agent-browser --session <s> eval "(function(){const r1=items[0].getBoundingClientRect(),r2=items[1].getBoundingClientRect();return r2.left-r1.right})()"`. Extract exact `spaceBetween` value. Add `gap: Npx !important` in globals.css to override the `gap: 0` rule that was designed for Swiper. |
| Mobile CSS references `/img/mo/` images that don't exist in clone | Site CSS has two sets of thumbnail rules: `@media desktop` uses `/img/pc/` and `@media mobile` uses `/img/mo/`. Clone only downloaded `/img/pc/` files → mobile viewport shows blank/black card backgrounds. | Card thumbnails render as solid black at mobile viewport width; `new Image(); img.onerror` confirms load failure | Run `ls public/img/mo/ \| grep <pattern>` to check which `/img/mo/` files are missing. For missing files, add `globals.css` overrides: `.selector .thumbnail { background-image: url(/img/pc/fallback.jpg) !important }`. Always audit `/img/mo/` against site mobile rules after downloading assets. |

| Tailwind v4 cascade layer overrides original CSS | Tailwind v4 wraps utilities in `@layer utilities`, which beats unlayered CSS (original site CSS) regardless of specificity. `.sticky` from Tailwind overrides `.page-headline .sticky { position: static }` from site CSS. | Original site CSS rules silently ignored — layout breaks, sticky elements in wrong position | Check if project uses Tailwind v4. If so, wrap conflicting original CSS rules in `@layer overrides { ... }` in globals.css. Or use `@layer base` to import original CSS so it participates in the cascade correctly. |
| Centered container off by ~0.4px on each side | Replicated Webflow's `width: 97vw; margin: 0 auto` as fixed margin (e.g. `marginLeft/Right: 1.83rem`). At one viewport it looks identical, but the ref's centering uses `(100vw − 97vw)/2 = 1.5vw` per side, which differs from any fixed rem at most viewports. Sub-pixel x-offset cascades to every child position. | First inspect the ref container's CSS rule — `width: 97vw; margin: 0 auto` is the Webflow default container. Replicate the rule, not its computed margin. Check `agent-browser --session <s> eval "getComputedStyle(el).width"` returns a `vw`/`%` string before assuming px is the source of truth. |
| Text appears "lighter" or "thinner" than ref despite identical `font-family`, `font-size`, `font-weight` | Project-scoped CSS sets `-webkit-font-smoothing: antialiased` and/or `text-rendering: optimizeLegibility`, but ref uses defaults (`auto` / `auto`). On macOS Retina, `antialiased` produces grayscale-AA which renders text noticeably *lighter* than `auto`'s subpixel-AA. The user reads this as a font-weight mismatch. | Don't add `-webkit-font-smoothing` or `text-rendering` overrides reflexively — many "best practice" snippets recommend `antialiased`, but it diverges from any ref using browser defaults. Verify on ref: `getComputedStyle(document.body).webkitFontSmoothing` — if `auto`, do not set it on impl. Same for `textRendering`. |
| Absolute-positioned decoration image stretches full wrapper width, ref shows it smaller and centered | Used `className="absolute inset-0 w-full h-full"` on a decoration `<img>` (ellipse, underline, badge). Ref CSS is `position: absolute; height: 100%` with no width — image keeps its natural aspect, smaller than the wrapper, centered via `left/right` insets. | Inspect ref image rect vs wrapper rect: if `img.width < wrapper.width`, do NOT use `inset-0 w-full`. Use `absolute top-0 h-full w-auto left-1/2 -translate-x-1/2` (or matching `left/right` insets from ref CSS) so the image keeps natural aspect inside a larger interactive wrapper. Common in Webflow ellipse buttons where the click-target is wider than the visual decoration. |
| Sub-pixel offsets dismissed as rounding noise mask real structural mismatches | `getBoundingClientRect()` rounded to integers loses sub-pixel detail. A 0.4px x-offset across many elements isn't AA noise — it's a centering/margin rule mismatch. A 0.06px height delta is 0.005rem at fluid root, indicating the element uses a slightly different `rem` value than yours. | Always log rect with 2-3 decimal places (`+r.x.toFixed(3)`). When ref and impl differ by < 1px on a coordinate, do not stop — find the rule. The fluid-rem-to-px conversion gives non-integer px values (e.g. `21.594` = `1.7944rem` at 1440 viewport), and matching them exactly is what reveals the underlying CSS rule (`97vw`, `9rem`, etc.). |

| Hero/section taller than ref by ~30-60px | Mask span wrapping splitText chars/lines has `line-height: 0.95em` (or any explicit line-height). The mask is `display: inline-block`, so its line-box height = `line-height × font-size`. With `0.95em` on a 100px H1, each line becomes 175.8px instead of the H1's intended 148px (line-height: 1.0 inherited). | Mask spans for split text (chars, words, lines) MUST use ONLY: `position:relative; display:inline-block; overflow:clip;`. Never set `line-height`, `vertical-align`, `font-size`, or any property that affects the line-box. The mask is a clipping container — it must inherit ALL typography from the parent verbatim. Same for `vertical-align: bottom` — it shifts the baseline and breaks descender rendering. |
| Flex sibling's fixed width fails to pin parent flex container width | Wrapped flex children in a `<div style={{display:'contents'}}>` (or any other intermediate container) for React keys. `display: contents` makes the wrapper layout-transparent in most ways, BUT a flex container determines its size from its DIRECT flex items. A `display:contents` wrapper IS a single direct child to the flex parent, so the parent only sees one item — sibling width-pinning rules don't apply. | When mapping flex children with React keys, use `<Fragment key={k}>...</Fragment>` from `react`, NEVER `<div style={{display:'contents'}}>`. Fragments compile to no DOM node — children become true direct flex siblings. Test: `flexParent.children.length` should equal sibling count, not 1. |
| Text content extracted via `textContent` introduces typos at `<br>` boundaries | `el.textContent` concatenates all child text nodes WITHOUT preserving whitespace at element boundaries. Ref content "Research and ideas <br>curated into practical, ..." extracted as textContent becomes "Research and ideascurated into practical" — the trailing space before `<br>` is collapsed by the browser's text-node normalization in some cases. | Always extract via `el.childNodes` and emit text + `<br/>` tokens separately. Treat ref's hand-broken lines as VERBATIM content — including any awkward splits or apparent typos. The original is the source of truth; pixel-match means matching exactly what renders, not what reads correctly. |

**Usage:** When visual verification fails and the cause isn't obvious, scan this table before debugging. Most behavioral bugs match one of these categories.

## Layout implementation pitfalls

### float→flexbox replacement requires height measurement

Replacing `float: left` + `overflow: hidden` (BFC trick) with Flexbox changes item heights.

Check the ref's layout method before implementing:
```javascript
getComputedStyle(document.querySelector('.list-item')).float
// "left" means float layout
```

After replacing with Flexbox, measure item height in pixels and verify against ref:
```javascript
document.querySelector('.list-item').getBoundingClientRect().height
```

---

### JS carousel/tab container height must be fixed

When replacing a ref's JS carousel (tab panels transformed horizontally) with React state, the container height must be fixed to match the ref.

Measure the ref's tab container height:
```javascript
document.querySelector('.flicking-viewport').offsetHeight
// e.g., 249 → apply h-[249px] to impl
```

---

### Live service image URLs — onError handler required

Streaming CDN URLs (live-broadcast thumbnail hosts, signed/time-windowed asset URLs) are session-specific snapshots. They return 404 when the broadcast ends or the signature expires.

```tsx
// Always add
<img
  src={thumbnailUrl}
  onError={(e) => { e.currentTarget.src = '/fallback-thumbnail.png'; }}
  alt=""
/>
```

When using Next.js Image component, use `placeholder="blur"` + `blurDataURL` for fallback handling.

---

### Next.js Image — remotePatterns registration required

If external domain images render at 0x0, the cause is missing `remotePatterns` in `next.config.ts`.

```typescript
// next.config.ts — replace hostnames with the target site's actual CDN
// domains identified during asset extraction (see assets.json
// `externalDomains` or grep `visible-images.json` for unique hosts).
images: {
  remotePatterns: [
    { protocol: 'https', hostname: '<cdn-domain-1>' },
    { protocol: 'https', hostname: '<cdn-domain-2>' },
    // …one entry per distinct external image host
  ],
},
```

Verify original URLs before adding images:
```bash
curl -I "https://cdn.example.com/image.jpg" | grep -i "http\|content-type"
```
