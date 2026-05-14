# Steps Most Likely to Be Skipped — by Zone

Each zone has a gate check. Run zone gate **before proceeding to next zone**.

> **Anti-skip rule:** If you think "this step probably won't find anything" — that is exactly when it WILL find something. Run it anyway. Every row below was identified from a real failure that caused user-visible bugs.

---

## ZONE 0: Staleness — Re-extraction Required

**Signal:** `python -m ui_clone.gate <ref-dir> pre-generate` prints `STALE` lines.

When a parent artifact (e.g. `structure.json`) is re-extracted *after* a downstream artifact (e.g. `extracted.json`) was assembled, the downstream file is **stale** — it reflects old data. The gate will block with:

```
✗ extracted.json (stale vs structure.json) — STALE (structure.json was re-extracted after this file was built)
  Re-run the step that produces extracted.json to pick up the new structure.json.
```

**Do not skip or ignore STALE failures.**

| Parent re-extracted | Stale downstream | Action |
|---|---|---|
| `structure.json` | `section-map.json`, `styles.json`, `extracted.json` | Re-run Steps 2→3→6b |
| `styles.json` | `extracted.json` | Re-run Step 6b (assemble) |
| `component-map.json` | `extracted.json` | Re-run Step 6b (assemble) |
| `interactions-detected.json` | `extracted.json`, `transition-spec.json` | Re-run Steps 5d→6b |
| `hover-css-rules.json` | `extracted.json` | Re-run Step 6b (assemble) |
| `transition-coverage.json` | `extracted.json` | Re-run Step 6b (assemble) |
| `bundle-map.json` | `transition-spec.json` | Re-run Step 5d |

**Assemble step (6b) is cheap** — it just merges all intermediate files into `extracted.json`. When in doubt, re-run it after any partial re-extraction.

---

## ZONE 1: Extraction Gates (Steps 2–3, 5c-a/5c-b–5e, 6c–6d)

Gate check: `python -m ui_clone.gate <ref-dir> pre-generate`

| Step | What gets skipped | Consequence | Prevention |
|---|---|---|---|
| **2.5b** SVG-as-text | Heading rendered as SVG path mistaken for font text | Wrong font rendering, size mismatch | `python -m ui_clone.gate` blocks without `svg-text-elements.json` |
| **2.6-pre** Dual-snapshot | Only one DOM snapshot taken | Runtime-injected transitions missed entirely | Gate: `dom-state-diff.json` must exist for splash sites |
| **5c-a–5d** Bundle completeness | Only `<script>` tags searched, not performance API | GSAP ScrollTrigger / Lenis params / Framer springs missed | Download ALL JS chunks via `performance.getEntriesByType('resource')` |
| **5d-2b** Hover CSS extraction | Only downloaded `.css` files searched | Inline `<style>` hover rules missed | Extract ALL `:hover` from live page: `[...document.styleSheets].flatMap(s => [...s.rules])` |
| **5d-2c** Hover DOM changes | Only style delta captured | `data-text` / `data-label` swap effects missed | Scan `data-*` on all interactive elements |
| **5d-2d** Hover video | "No visual transition" concluded from grep alone | 3D fold / text swap effects missed | Record hover video for EVERY hoverable element |
| **6c** Section audit | "I already mapped sections visually" | Element ownership wrong; portal/sticky elements misplaced | Run `section-audit.md` six-stage audit — never skip |
| **6d** Transition coverage | "Section audit done, proceed to generation" | Impl ships as static for animated elements | `python -m ui_clone.gate <ref-dir> pre-generate` blocks without `transition-coverage.json` |

---

## ZONE 2: Implementation Foundations (Step 7, HTML + CSS structure)

Gate check: `pnpm tsc --noEmit` + `computed-diff.sh` on key text elements

| Step | What gets skipped | Consequence | Prevention |
|---|---|---|---|
| **Pre-impl DOM read** | Section coded from memory / screenshot reading | Wrong class names, item counts, grouping — user must catch it | `agent-browser --session <ref> eval "document.querySelector('.target').outerHTML"` on ref BEFORE writing any JSX |
| **7 lineHeight** | Explicit `lineHeight` not set on text elements | Tailwind `leading-normal` (1.5×) applied — text spacing wrong everywhere | Extract exact `lineHeight` for ALL text. Never use `leading-*` — use `style={{ lineHeight: '16.8px' }}` |
| **7 color opacity** | Text color extracted without alpha channel | Semi-transparent labels rendered at full opacity | Always check ref for `rgba()` — subtitles/event names often use 20–40% opacity |
| **7 grid height** | Grid cell heights hardcoded in px | Layout breaks at wider viewports — images overflow | Use `aspectRatio` on grid cells, not fixed `height` |
| **7 dynamic content** | CDN image URLs hardcoded without verification | URLs 404 silently — images show alt text only | `curl -I <url>` every CDN URL after hardcoding |
| **7 DOM wrapper missing** | Section content placed directly in `<section>` without `.main_inner` wrapper | CSS margin/padding on wrapper collapses; heading inside wrong container | `agent-browser --session <ref> eval "document.querySelector('.section').children[0].className"` — verify wrapper exists |
| **7 DOM child order** | Implemented by visual reading instead of DOM inspection | Mascot below title when ref has mascot above — DOM order ≠ visual order | `[...document.querySelector('.inner').children].map(c => c.className)` — always verify |
| **layout viewport meta** | `<meta name="viewport">` not added to layout | Mobile media queries never fire | Add to EVERY layout file. Verify: `agent-browser --session <s> set viewport 375 812`, reload, screenshot. |
| **7 pagination bar** | Swiper bullet `<span class="bar">` added but CSS `width: 100%` overrides | Bar appears full immediately, no fill animation | Use `requestAnimationFrame` double-tick: `width: 0%` → next tick → `width: 100%` |

---

## ZONE 3: Interaction Implementation (Step 7, hover + scroll)

Gate check: `getComputedStyle` on hover states, `getAnimations()` on scroll elements

| Step | What gets skipped | Consequence | Prevention |
|---|---|---|---|
| **7 SVG-as-text verbatim** | SVG text recreated with fonts | Kerning, weight, glyph shape all wrong | Copy SVG verbatim from `svg-text-elements.json`. Never recreate with `<span>` + CSS font. |
| **7 smooth-scroll detection** | `addEventListener('scroll')` used on Lenis/Locomotive site | Scroll-driven effects frozen at init state | Check `scroll-engine.json`. If smooth scroll: use RAF + `getBoundingClientRect()` |
| **7 JS threshold extraction** | `scrollY > 10` hardcoded for sticky header | Scroll behavior triggers too early/late | Grep bundle: `grep -E "fixed\|scrolled\|is-scroll" tmp/ref/<c>/bundles/*.js`. Never guess. |
| **7 element type verification** | `<button>` used instead of `<select>` / `<input>` / `<label>` | Wrong width, no native behavior, CSS reset mismatch | `agent-browser --session <ref> eval "document.querySelector('.btn_area').innerHTML"`. Check `tagName`. |
| **7 effect-data observer** | Per-element IntersectionObserver not wired | Section renders completely blank (all `opacity: 0` by default) | Wire observer per `.effect-data` element, not just section-level |
| **7 card stack height** | `.height` left as `auto` when hide-class added | Cards snap instantly — no height transition animation | Set `item.style.height = item.offsetHeight + 'px'` BEFORE any scroll handler runs |
| **RAF vs scroll event** | RAF loop calls `classList.add/remove` every frame | CSS `transition: height 1.06s` restarts every frame — snap instead of animate | RAF for progress-based transforms. Scroll event for class toggles. |
| **7 SVG rotation double-apply** | Rotate added in JSX when CSS already rotates via `.slider_prev svg { transform: rotate(180deg) }` | SVG rotated 360° — prev arrow looks same as next | `grep -E "\.slider_prev.*transform\|rotate" *.css` before adding any inline transform |
| **9 interaction dispatch** | "Hover works" concluded without dispatching mouseenter | JS-driven hover (GSAP mouseenter) not triggered | `el.dispatchEvent(new MouseEvent('mouseenter'))` + check `el.getAnimations()` |

---

## ZONE 4: Host/Embedding CSS Conflicts (embedded projects only)

Gate check: `grep -E "^button\b|^a\b|^select\b|^img\b|^body\b" host.css` — inventory ALL resets **before** writing any layout.tsx

| Step | What gets skipped | Consequence | Prevention |
|---|---|---|---|
| **Host reset full inventory** | Noticed one broken property but didn't enumerate ALL resets | Follow-up bugs discovered one per session — user becomes QA | First step when writing `layout.tsx`: `grep -E "^button\b|^a\b|^button,|^a," host.css`. Fix ALL at once. |
| **`button,a` both element types** | `button.Cls` override added but `a.Cls` still gets `font-weight: 400` | `<a>`-rendered components have wrong weight while `<button>` is correct | When host rule targets `button,a`, override BOTH. Verify: `getComputedStyle` on `<a>` AND `<button>`. |
| **`select` reset missing** | Only `button` reset added | Language select renders with system font/color | Add `select { appearance:none; -webkit-appearance:none; border:none; background:none; font:inherit; cursor:pointer; }` |
| **`img { display:block }` centering** | `text-align: center` on parent does nothing | Image stays left-aligned | Check `getComputedStyle(img).display`. If block: add `margin: 0 auto` to `<img>`. Do NOT change display. |
| **Footer `<a>` color specificity** | CSS class says `color: #fff` but host `a { color: #1a1d24 }` wins | Footer links appear dark on colored background | `computed-diff.sh` on `.footer_link`. If mismatch: scoped override in layout.tsx. |
| **Swiper shadow clipping** | `.swiper { overflow:hidden }` clips `box-shadow` on card bottom | Shadow visible on top but clipped on bottom | Add `padding-bottom: 20px` to `.swiper` wrapper via inline style. |
| **Body scoping** | `body {}` styles not copied to project container | `line-height`, `font-family` wrong in embedded context | Copy body-level properties to `[data-project]` or `.root` selector |
| **@theme file location** | `@theme { --font-sans }` in separate CSS file | Custom fonts silently ignored, Tailwind defaults used | Use plain CSS vars on `[data-project]` in monorepo. `@theme` only works in file with `@import "tailwindcss"`. |

---

## ZONE 5: Verification & Comparison (Steps 8–9)

Gate check: `section-compare.sh` + `transition-compare.sh` → all PASS

| Step | What gets skipped | Consequence | Prevention |
|---|---|---|---|
| **8b section-compare** | "Full-page comparison already ran" | Section-level mismatches hidden in scroll noise — full-page scroll noise masks per-section bugs | `section-compare.sh` is NOT a substitute for full-page and vice versa. Always run both. Full-page passing does NOT mean section-compare can be skipped. |
| **8c transition-compare** | "Hover looks right to me" | Wrong easing, missing hover effect, timing mismatch | `transition-compare.sh` — auto-detects ALL transition elements. Not optional if `interactions-detected.json` exists. |
| **Phase E LLM review** | "AE=1M+ so layout is broken, no need for LLM" | Section-level bugs hide behind viewport-width AE noise | High AE on embedded layouts is structural, not a code bug. Phase E is ALWAYS mandatory. |
| **Guessed impl verification** | Any guessed implementation shipped without screenshot comparison | User discovers discrepancy — becomes the QA | Screenshot ref + impl at the exact trigger point. 30 seconds now, 30 minutes saved later. |
| **Reference item count** | Assumed card count from initial DOM | Missing cards → broken image paths, incomplete feature parity | `agent-browser --session <ref> eval "document.querySelectorAll('.card').length"` on ref before implementing |
| **Existing-project port check** | Saw 404, assumed page didn't exist | Wasted work on pages that already existed on a different port | `ps aux | grep next` — find the actual port first |
| **Existing-project JS audit** | Adding pages without checking if site's JS is loaded | All scroll transitions silently missing | Compare `layout.tsx` `<script>` tags vs `document.querySelectorAll('script[src]')` on live ref |
| **Step 8 self-reported** | Declared "done" based on own code reading | User discovers diffs and has to report them one by one | NEVER declare done without running `section-compare.sh` + screenshots at 375/768/1280 vs ref |
| **Transitions self-reported by category** | Said "transitions matched" after verifying only the hover entries from the spec | Intersection-fade-up / scroll-driven entries silently omitted from impl — user catches them late ("scroll-reactive section zoom transitions, text transitions, footer video — still not applied") | Run `transition-spec-coverage.sh <component-dir> <impl-src-dir>` — every spec entry must show ≥1 hit. Then run `reveal-trigger-check.sh` for the runtime side. "I checked the hover ones" is not coverage. |
| **Stuck reveals dismissed as "looks right"** | Static screenshot of reveal slot showed background and was deemed acceptable | RevealRise/RevealLetters elements never trigger because IO is observing a transformed child of an `overflow: hidden` ancestor — element renders SOMETHING in the slot but never animates in | `reveal-trigger-check.sh <session> <impl-url>` — runs IO probes on every initially-hidden element, scrolls into view, fails any whose opacity/transform never advance. See `transition-implementation.md` → IntersectionObserver placement for masked reveals. |
| **section-compare "MISSING impl" dismissed** | Treated as className-mismatch noise on a Tailwind clone of a CSS-modules ref | False-negative covers a real layout/transition gap — by ignoring the table you skip the only signal you had | If section-compare reports MISSING impl, run `auto-diagnose.sh` on the failing crop AND `reveal-trigger-check.sh` on the impl page before dismissing. Only after BOTH come up clean is the MISSING entry safe to chalk up to className strategy. |
