# No Judgment — Data Only

**Every decision must be backed by extracted data, screenshots, or script output — never "probably", "should be", "close enough".**

Read this when you feel a temptation to shortcut. Find your thought in the table below, then do the required action instead.

---

## Group A: Measurement vs Assumption

| Temptation | Required action |
|---|---|
| "This is probably a small popover" | Capture idle + active screenshot. Check canvas/dialog dimensions vs viewport. It may be a full-scene renderer. |
| "The threshold is probably 10px" | Grep bundle: `grep -E "fixed\|scrolled\|is-scroll" bundles/*.js`. Thresholds are tied to `element.offsetTop` or `headerHeight`. Never guess. |
| "No hover transition — bundle grep returned empty" | Inline `<style>` tags are invisible to bundle grep. Extract ALL `:hover` rules from live page stylesheets. |
| "The transition is sound-only, no visual change" | Record hover video. CSS `:hover` in inline `<style>` may apply 3D transforms invisible to bundle search. |
| "The image URLs look right" | `curl -I <url>` every CDN URL. Images with `naturalWidth: 0` show alt text only — user won't report until they see it. |
| "I implemented this by reasoning — it's probably right" | Screenshot ref + impl at the exact trigger point before moving on. 30 seconds now, 30 minutes saved. |
| "This looks like a button, so I used `<button>`" | `agent-browser --session <s> eval "document.querySelector('.btn_area').innerHTML"`. Styled `<select appearance:none>` + absolute SVG chevron is indistinguishable from custom button. |
| "`scroll-engine.json` shows 'native' so I used addEventListener" | Verify: `agent-browser --session <s> eval "typeof window.lenis !== 'undefined' || typeof locomotiveScroll !== 'undefined'"`. Site may still use custom scroll. |
| "The reference has the same items as I implemented" | `agent-browser --session <s> eval "document.querySelectorAll('.card, .point-items, .item').length"` on ref. Items may be added post-launch. |
| "I know what this section looks like" | `agent-browser --session <s> eval "document.querySelector('.section-class').outerHTML"` on ref BEFORE any impl. Then `section-compare.sh` after. No exceptions. |
| "Header should hide on scroll-down — that's standard UX" | Scroll ref to a deep position (`window.scrollTo(0, 4000)` or via the custom scroll wrapper), wait 500ms, then check `getComputedStyle(headerEl).transform`. If `none`, do NOT add hide-on-scroll — ref doesn't have it. |
| "em-conversion failed so I'll use 16px as 1em" | Stop generation and recover the base from CSS (`html/body font-size`, root variables, or `sizing-expressions.json`). If CSS is unavailable, measure it with a browser/screenshot probe before writing code. Never commit a guessed base or a TODO marker. |
| "The gate only checks file existence, so I'll assemble the JSON myself" | Stop. Add or fix `artifact-provenance.json` with real browser/script evidence paths. `manual`/`guess` provenance blocks `pre-generate`; rerun the extraction step instead. |

---

## Group B: Library Choice vs Simplification

| Temptation | Required action |
|---|---|
| "This animation dependency is inconvenient, so I'll simplify" | Check `package.json` + `transition-implementation.md` alternatives. Use the project library, OSS option, or native implementation before simplifying. |
| "The library isn't critical — I'll replace with useState" | Library defines DOM structure, CSS active states, touch behavior. `useState` replaces none of that. If in `package.json`, USE IT. |
| "I'll replace Swiper with custom carousel" | Install Swiper. Custom slider: no touch drag, no `.swiper-slide-active` CSS state, no keyboard nav, wrong timing. |
| "GSAP dependency — I'll approximate" | See `transition-implementation.md`: SplitText→`splitting`, MorphSVG→`flubber`, ScrollSmoother→`lenis`, DrawSVG→`stroke-dashoffset`. |

---

## Group C: Visual Semantics vs CSS Reality

| Temptation | Required action |
|---|---|
| "This heading is just text with a font" | Check `svg-text-elements.json`. It may be an SVG `<path>` — font recreation produces wrong kerning, weight, glyph shape. |
| "DOM section wrapper probably isn't needed" | `agent-browser --session <s> eval "document.querySelector('.section').children[0].className"` — CSS sets margin/padding ON the wrapper. |
| "CTA order is title→mascot by visual reading" | `[...document.querySelector('.inner').children].map(c => c.className)` — DOM order ≠ visual order when CSS positions elements. |
| "The image isn't centering but `text-align: center` is set" | `getComputedStyle(img).display` — host CSS may set `img { display: block }`. Fix: `margin: 0 auto` on `<img>`. |
| "I added the CSS transform for the prev arrow in JSX" | Grep CSS for `.slider_prev svg { transform }` first. Adding JSX rotate when CSS already rotates = 360° total. |
| "The shadow is gone — overflow:hidden must be wrong" | Swiper wrapper `overflow: hidden` clips bottom shadow. Add `padding-bottom: 20px` to `.swiper` via inline style. |
| "footer links are white — CSS class says `color: #fff`" | `getComputedStyle(aEl).color` on impl. Host CSS `a { color }` may beat `.footer_link { color }`. Add scoped override in layout.tsx. |
| "This Canvas is just a small overlay" | `canvas.width >= viewportWidth * 0.8` = full-scene renderer, not overlay. |

---

## Group D: CSS Cascade & Host Resets (embedded projects)

| Temptation | Required action |
|---|---|
| "body CSS applies everywhere" | Scope `font-family`, `line-height`, `letter-spacing` to `[data-project]` / `.root` selector. |
| "@theme sets my custom fonts" | `@theme` only works in file with `@import "tailwindcss"`. In monorepo separate files: use plain CSS vars on `[data-project]`. |
| "I fixed the one CSS conflict I noticed" | Host resets are comprehensive. One visible bug = cluster. `grep -E "^button\b|^a\b|^button," host.css`. Fix ALL at once. |
| "I added `button.Cls` override — `a.Cls` works too" | Host rules targeting `button,a` apply to both. Run `getComputedStyle(aEl).propertyName` on BOTH element types. |
| "I'll add !important to override this" | Only valid for third-party CSS you cannot touch (Tailwind preflight). If your own cascade: find and fix the root cause. |
| "Layout viewport meta probably isn't critical" | ALWAYS add `<meta name="viewport" content="width=device-width, initial-scale=1">` to every layout file. |
| "Only button needs the reset, not select" | Add `select { appearance:none; -webkit-appearance:none; border:none; background:none; font:inherit; cursor:pointer; }` alongside button resets. |

---

## Group E: Verification & Data Trust

| Temptation | Required action |
|---|---|
| "This looks close enough" | Run `scripts/verify/auto-verify.sh`. AE number decides. Not your judgment. |
| "This FAIL is just a content difference" | Run `computed-diff.sh`. Name the specific CSS property. "Content difference" is not a diagnosis. |
| "AE is 1M+ so the whole thing is broken" | High AE on embedded/max-width layouts is expected (ref 1440px, impl 480px). Run Phase E LLM review on ref+impl pairs regardless. |
| "The project already has CSS/styles, so animations work" | CSS defines animation rules. JS triggers them. `typeof gsap !== 'undefined'` in browser — if false, all scroll transitions are missing. |
| "The hero renders so the page works" | `window.scrollTo(0, document.body.scrollHeight * 0.4)` + screenshot. Hero ≠ page. Often 300–500vh with sticky effects. |
| "I'm done — it matches the reference" | Run `section-compare.sh` against live ref. Declare done only after script passes. |
| "I'll use dangerouslySetInnerHTML" | ⛔ NEVER for standard JSX components. **Exception:** `site-detection.md` Raw HTML Injection approach only — when `cssModuleRatio > 0.3` OR `jsInlineStyles > 20` OR `hasGSAP + hasLottie + hasCanvas > 1`. Run the approach detection gate in `site-detection.md` first. If that gate does not fire, convert to proper JSX. |
| "I'll use a hardcoded placeholder div" | ⛔ NEVER. `<div style={{height:"594px"}}/>` is not an implementation. Extract and implement real DOM. |
| "The scroll animation works — I used RAF" | RAF is correct for progress-based transforms (parallax). Wrong for CSS class-toggle transitions (card stack). RAF cancels the CSS transition every frame. |
| "I'll use a placeholder" | No placeholders. Extract real asset or leave unimplemented. |
| "The scraped HTML has correct initial state" | GSAP-baked inline styles (`visibility:hidden`, `opacity:0`) are animation init states, NOT defaults. Reset them. |
| "I'll verify the CSS change via curl on the compiled chunk" | ⛔ NEVER. Next.js HMR may not have recompiled yet — `curl` on chunk URLs returns stale cached content. Always use `agent-browser --session <s> eval "getComputedStyle(document.querySelector('.target')).propertyName"` to verify CSS changes in a live browser. |
| "I'll use Puppeteer MCP / Playwright MCP for this" | ⛔ NEVER. All browser automation MUST use `agent-browser` CLI via Bash. Puppeteer/Playwright MCP tools are prohibited — they bypass session management and conflict with `agent-browser` sessions. This rule survives context compaction. |
