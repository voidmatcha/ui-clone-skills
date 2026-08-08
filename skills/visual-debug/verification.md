# Visual Verification — Phase A/B/D (Full Procedure)

> Part of the `visual-debug` skill. Can be invoked standalone or as Step 8+9 of `ui-reverse-engineering`.
>
> **Quick comparison?** Use `batch-scroll.sh` + `batch-compare.sh` from the parent SKILL.md instead. This document is the **full multi-phase verification procedure** with capture, comparison, self-healing, and completion gate.

> **Security note:** Screenshots and DOM snapshots may contain sensitive data visible on the page (auth tokens in DOM attributes, form inputs, user info). Clean up `tmp/ref/` after extraction: `rm -rf tmp/ref/<component>`

**Reference recordings are captured ONCE from the original site. Never re-visit the original site after initial capture.**

## Dependencies

```bash
ffmpeg -version   # required for frame extraction
# macOS: brew install ffmpeg
```

## Three Mandatory Captures

Every component verification requires these **three distinct captures**, both from the original site (Phase A) and from the implementation (Phase B):

| # | Capture Type | What | Why |
|---|---|---|---|
| **C1** | Full-page static screenshots | Screenshot at each scroll position (top, 25%, 50%, 75%, 100%) | Catches layout, spacing, color, typography mismatches |
| **C2** | Full-page scroll video | Continuous video recording while scrolling top to bottom | Catches scroll-triggered animations, parallax, sticky elements, reveal transitions |
| **C3** | Transition/interaction captures | Per-region capture by triggerType: selector screenshots (css-hover, js-class, intersection) or video (scroll-driven, mousemove, auto-timer) | Catches timing, easing, state changes, interaction behavior |

**All three are mandatory. C1 alone (static screenshots) is NOT sufficient.** C2 catches what C1 misses (scroll-triggered motion), and C3 catches what C2 misses (hover/click/scroll interactions).

## Frame Extraction: 60 fps (MANDATORY — DO NOT CHANGE)

> **THIS IS A HARD REQUIREMENT. DO NOT reduce to 10fps, 30fps, or any other value.**
> **DO NOT skip frame extraction. DO NOT use screenshots as a substitute.**
> **Every video capture MUST be extracted at exactly 60fps.**

Video captures are extracted at **60 fps** for AE diff curve analysis and frame-by-frame comparison.

```bash
# 60 fps extraction — MANDATORY for ALL video captures. DO NOT change this value.
ffmpeg -i <input>.webm -vf fps=60 <output-dir>/frame-%06d.png -y
```

> **Why 60 fps:** AE diff at 60fps gives 16.7ms resolution — sufficient to detect easing curves,
> holds, and transition boundaries. AE diff costs zero tokens (pure CLI).
> Disk usage: ~300KB/frame × 60fps × duration. Clean up frames after analysis.
>
> **css-hover / js-class / intersection:** no frame extraction needed — these use selector screenshots directly.

## Shared scroll sequence (use identically in Phase A and B)

```bash
agent-browser --session <s> eval "
(() => {
  const h = document.body.scrollHeight;
  let pos = 0;
  const step = () => {
    pos += 120;
    window.scrollTo(0, pos);
    if (pos < h) setTimeout(step, 80);
  };
  step();
})()"
agent-browser --session <s> wait 4000
```

## Content-Anchored Screenshot Alignment (MANDATORY)

**Never compare screenshots by raw y-coordinate.** Original and implementation have different total page heights (pinned/sticky sections, different padding, etc.). A screenshot at y=5000 in the original shows completely different content than y=5000 in the implementation.

**Instead, use text anchors:**

1. Identify a unique heading or text string visible in the section (e.g., "Clothing to claim every shred")
2. In BOTH original and implementation, find that text element and scroll it to the same viewport position
3. Screenshot at that aligned position

```bash
# Content-anchored scroll helper
ANCHOR="Clothing to claim"
agent-browser --session <s> eval "(() => {
  for (const h of document.querySelectorAll('h1,h2,h3')) {
    if (h.textContent.includes('${ANCHOR}')) {
      window.scrollTo(0, h.getBoundingClientRect().top + window.scrollY - 350);
      return 'found';
    }
  }
  return 'not found';
})()"
```

**For pinned/scroll-triggered sections** (e.g., GSAP ScrollTrigger pin):

The same section may appear at different scroll positions because the pin extends the scroll range. Compare at **scroll progress**, not scroll position:

```bash
# Read ScrollTrigger progress in original
agent-browser --session <s> eval "(() => {
  if (typeof ScrollTrigger !== 'undefined') {
    const triggers = ScrollTrigger.getAll();
    return JSON.stringify(triggers.map(t => ({
      trigger: t.trigger?.className?.substring(0,40),
      progress: t.progress,
      start: t.start, end: t.end
    })));
  }
  return 'no ScrollTrigger';
})()"

# In implementation, scroll to equivalent progress
# progress = (scrollY - sectionTop) / (sectionHeight - viewportHeight)
```

## Anti-pattern: "looks close enough" (HARD RULE)

> **See "No Judgment — Data Only" in `ui-reverse-engineering/SKILL.md` for the full table of judgment traps.**

**Never declare a section "done" or "almost matching" based on your own visual judgment.** Your judgment is unreliable — you consistently overestimate similarity. Instead:

1. Run `scripts/verify/auto-verify.sh` — it runs layout-health-check, batch-scroll+AE comparison, and post-implement gate automatically
2. Run `computed-diff.sh` on key elements (font-size, padding, margin, color)
3. If ANY AE > 500 or ANY computed value differs by more than 1px → it's NOT done
4. Only `scripts/verify/auto-verify.sh exit 0` + user confirmation = done

**Phrases that indicate this anti-pattern (ALL are FORBIDDEN):**
- "almost matches" / "very close" / "close enough"
- "nearly identical to the original"
- "structure is almost the same"
- "this FAIL is just a content difference"
- "this is a structural difference, not visual"
- "the remaining differences are minor"

**Replace with:** Specific `scripts/verify/auto-verify.sh` exit code, AE numbers, or `computed-diff.sh` output.

---

## Phase A: Record Reference (ONCE)

> **Inner scroll wrapper (Lenis / locomotive-scroll / overflow-hidden body):** if `body { overflow: hidden }` and a child element is the actual scrollable container, `window.scrollTo` is a no-op and every `<n>pct.png` will look identical. Detect with:
>
> ```bash
> agent-browser --session <s> eval "(() => { const dh = document.documentElement.scrollHeight; const dc = document.documentElement.clientHeight; if (dh > dc + 100) return '__document__'; let best = null; document.querySelectorAll('*').forEach(el => { const cs = getComputedStyle(el); if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll' || cs.overflowY === 'hidden') && el.scrollHeight > el.clientHeight + 100) { if (!best || el.scrollHeight > best.sh) best = { el, sh: el.scrollHeight }; } }); return best ? (best.el.tagName + (best.el.className ? '.' + best.el.className.split(' ').find(c => c.startsWith('js-') || c.includes('lenis') || c.includes('scroll')) : '')) : '__document__'; })()"
> ```
>
> If the result is anything other than `"__document__"`, replace `window.scrollTo(0, Y)` with `(() => { const w = document.querySelector('<sel>'); w.scrollTop = Y; w.dispatchEvent(new Event('scroll')); return w.scrollTop; })()` for every scroll position. The `dispatchEvent('scroll')` is required because programmatic `scrollTop` writes don't fire scroll events that Lenis/observers listen for. `batch-scroll.sh` does this automatically.

### A-C1: Full-page static screenshots

```bash
mkdir -p tmp/ref/<component>/frames/{ref,impl,diff}
mkdir -p tmp/ref/<component>/static/{ref,impl,diff}
mkdir -p tmp/ref/<component>/transitions/{ref,impl}
mkdir -p tmp/ref/<component>/responsive

agent-browser --session <s> open https://target-site.com
agent-browser --session <s> set viewport 1440 900

# Static screenshots at each scroll position
agent-browser --session <s> eval "(() => window.scrollTo(0, 0))()"
agent-browser --session <s> wait 500
agent-browser --session <s> screenshot tmp/ref/<component>/static/ref/top.png

agent-browser --session <s> eval "(() => window.scrollTo(0, document.body.scrollHeight * 0.25))()"
agent-browser --session <s> wait 500
agent-browser --session <s> screenshot tmp/ref/<component>/static/ref/25pct.png

agent-browser --session <s> eval "(() => window.scrollTo(0, document.body.scrollHeight * 0.5))()"
agent-browser --session <s> wait 500
agent-browser --session <s> screenshot tmp/ref/<component>/static/ref/50pct.png

agent-browser --session <s> eval "(() => window.scrollTo(0, document.body.scrollHeight * 0.75))()"
agent-browser --session <s> wait 500
agent-browser --session <s> screenshot tmp/ref/<component>/static/ref/75pct.png

agent-browser --session <s> eval "(() => window.scrollTo(0, document.body.scrollHeight))()"
agent-browser --session <s> wait 500
agent-browser --session <s> screenshot tmp/ref/<component>/static/ref/bottom.png
```

### A-C2: Full-page scroll video

```bash
agent-browser --session <s> eval "(() => window.scrollTo(0, 0))()"
agent-browser --session <s> wait 500
agent-browser --session <s> record start tmp/ref/<component>/ref-scroll.webm

# Static pause at top
agent-browser --session <s> wait 1000

# Scroll (use shared sequence above)

agent-browser --session <s> wait 1000
agent-browser --session <s> record stop

# Extract at 60 fps
ffmpeg -i tmp/ref/<component>/ref-scroll.webm -vf fps=60 tmp/ref/<component>/frames/ref/scroll-%06d.png -y
```

### A-C3: Transition/interaction captures (deferred to Step 5b)

> **This step requires `interactions-detected.json` from Step 5.** Execute A-C3 after Step 5 in Phase 2 (referenced as Step 5b in SKILL.md), not during the initial Phase A pass.

**Capture method depends on `triggerType`:**

| triggerType | Method |
|---|---|
| `css-hover`, `js-class` | eval + selector screenshot (idle + active states) |
| `intersection` | eval classList.add + selector screenshot (before + after states) |
| `scroll-driven` | video recording (continuous change) |
| `mousemove` | video recording (cursor-coordinate reaction) |
| `auto-timer` | video recording (time-based loop) |

**For css-hover / js-class / intersection regions:**

```bash
# 1. Measure element rect
agent-browser --session <s> eval "(() => {
  const el = document.querySelector('<selector>');
  el.scrollIntoView({ block: 'center' });
  const r = el.getBoundingClientRect();
  return JSON.stringify({ x: r.x, y: r.y, width: r.width, height: r.height });
})()"
agent-browser --session <s> wait 500

# 2. idle state
agent-browser --session <s> screenshot '<selector>' tmp/ref/<component>/transitions/ref/<name>-idle.png

# 3. active state
# css-hover: CDP hover
agent-browser --session <s> hover <selector>
# js-class: classList.add
# agent-browser --session <s> eval "document.querySelector('<sel>').classList.add('<cls>')"
# intersection: classList.add('in-view')
agent-browser --session <s> wait <transitionDuration + 100>
agent-browser --session <s> screenshot '<selector>' tmp/ref/<component>/transitions/ref/<name>-active.png
```

The current interface is `screenshot [selector] [path]`. If a visual subregion
cannot be addressed by a selector, take a viewport screenshot and post-crop the
measured rectangle:

```bash
agent-browser --session <s> screenshot /tmp/<name>-viewport.png
magick /tmp/<name>-viewport.png -crop <width>x<height>+<x>+<y> +repage \
  tmp/ref/<component>/transitions/ref/<name>.png
```

Selector crops only need to be decodable and nonempty; they may legitimately
be smaller than 10KB. A state pair is valid only when the expected state is
visible and its pixel difference is nonzero. Retain the >10KB floor for
viewport and full-page screenshots.

**For scroll-driven / mousemove / auto-timer regions (video):**

```bash
# Example: carousel with auto-timer
agent-browser --session <s> eval "(() => { document.querySelector('<carousel-selector>').scrollIntoView({ block: 'center' }); })()"
agent-browser --session <s> wait 500
agent-browser --session <s> record start tmp/ref/<component>/ref-transition-carousel.webm

agent-browser --session <s> wait 12000  # 2 full cycles

agent-browser --session <s> record stop

# Extract at 60 fps
ffmpeg -i tmp/ref/<component>/ref-transition-carousel.webm -vf fps=60 tmp/ref/<component>/transitions/ref/carousel-%06d.png -y
```

### A-R: Responsive screenshots

**Handled by `responsive-detection.md` Steps 4-D and A-R.** If Step 4 was already executed in Phase 2, ref screenshots already exist — do not re-capture. If Phase 1 runs before Phase 2 (as in the SKILL.md flow), defer A-R until Step 4 is complete.

### Phase A Gate

```
□ static/ref/ has 5 screenshots (top, 25%, 50%, 75%, bottom)
□ Spot-check: Read top.png ONCE — confirm it's the actual site (not blank, not bot-detection page). Do not re-read for comparison.
□ ref-scroll.webm exists and has frames extracted to frames/ref/ at 60 fps
□ C3 (transitions/) — deferred until Step 5b (needs interaction data from Step 5)
□ responsive/ — deferred until Step 4 (responsive-detection.md)
```

**If C1 or C2 is missing → go back and capture. C3 and responsive are captured later in Phase 2.**

---

## Phase B: Record Implementation (per iteration)

Execute the **identical** three captures on `localhost:<port>`:

### B-C1: Full-page static screenshots

```bash
agent-browser --session <s> open http://localhost:<port>
agent-browser --session <s> set viewport 1440 900

agent-browser --session <s> eval "(() => window.scrollTo(0, 0))()"
agent-browser --session <s> wait 500
agent-browser --session <s> screenshot tmp/ref/<component>/static/impl/top.png

agent-browser --session <s> eval "(() => window.scrollTo(0, document.body.scrollHeight * 0.25))()"
agent-browser --session <s> wait 500
agent-browser --session <s> screenshot tmp/ref/<component>/static/impl/25pct.png

agent-browser --session <s> eval "(() => window.scrollTo(0, document.body.scrollHeight * 0.5))()"
agent-browser --session <s> wait 500
agent-browser --session <s> screenshot tmp/ref/<component>/static/impl/50pct.png

agent-browser --session <s> eval "(() => window.scrollTo(0, document.body.scrollHeight * 0.75))()"
agent-browser --session <s> wait 500
agent-browser --session <s> screenshot tmp/ref/<component>/static/impl/75pct.png

agent-browser --session <s> eval "(() => window.scrollTo(0, document.body.scrollHeight))()"
agent-browser --session <s> wait 500
agent-browser --session <s> screenshot tmp/ref/<component>/static/impl/bottom.png
```

### B-C2: Full-page scroll video

```bash
agent-browser --session <s> eval "(() => window.scrollTo(0, 0))()"
agent-browser --session <s> wait 500
agent-browser --session <s> record start tmp/ref/<component>/impl-scroll.webm

agent-browser --session <s> wait 1000

# Scroll (use shared sequence — identical to Phase A)

agent-browser --session <s> wait 1000
agent-browser --session <s> record stop

ffmpeg -i tmp/ref/<component>/impl-scroll.webm -vf fps=60 tmp/ref/<component>/frames/impl/scroll-%06d.png -y
```

### B-C3: Transition/interaction captures

**Same method as A-C3 — identical triggerType classification, identical selectors and timing.**

**For css-hover / js-class / intersection regions (selector screenshot):**

```bash
agent-browser --session <s> eval "(() => {
  const el = document.querySelector('<selector>');
  el.scrollIntoView({ block: 'center' });
  const r = el.getBoundingClientRect();
  return JSON.stringify({ x: r.x, y: r.y, width: r.width, height: r.height });
})()"
agent-browser --session <s> wait 500

agent-browser --session <s> screenshot '<selector>' tmp/ref/<component>/transitions/impl/<name>-idle.png

agent-browser --session <s> hover <selector>  # or classList.add for js-class/intersection
agent-browser --session <s> wait <transitionDuration + 100>
agent-browser --session <s> screenshot '<selector>' tmp/ref/<component>/transitions/impl/<name>-active.png
```

**For scroll-driven / mousemove / auto-timer regions (video — identical to A-C3):**

```bash
agent-browser --session <s> eval "(() => { document.querySelector('<carousel-selector>').scrollIntoView({ block: 'center' }); })()"
agent-browser --session <s> wait 500
agent-browser --session <s> record start tmp/ref/<component>/impl-transition-carousel.webm

agent-browser --session <s> wait 12000

agent-browser --session <s> record stop

ffmpeg -i tmp/ref/<component>/impl-transition-carousel.webm -vf fps=60 tmp/ref/<component>/transitions/impl/carousel-%06d.png -y
```

### B-R: Responsive screenshots

**Read `responsive-detection.md`, execute the B-R section.** Captures impl screenshots at the same detected breakpoints used in A-R.

---

## Phase C: Compare & Fix

**→ See `comparison-fix.md`** for the full comparison and fix loop procedure (AE/SSIM diff, computed-style diagnosis, Phase E VLM check, self-healing loop).

---


## AE/SSIM threshold guidelines

### Handling AE FAIL on dynamic content sites

High AE does not necessarily mean layout mismatch. **Always read the diff image first.**

Decision criteria:
1. Red regions in the diff image are concentrated in **image/thumbnail card areas** → dynamic content difference, layout is PASS
2. Red regions in the diff image cover **header, navbar, margins, text areas** → actual layout mismatch, fix required

Live services (streaming, news feeds, etc.) change thumbnail URLs frequently. Do not judge by AE numbers alone.

---

## Phase D: Pixel-Perfect Diff

> **This step separates "looks similar" from "pixel-perfect".** Both phases always run.

### Phase D1: Visual Gate

1. **Select elements**: layout containers, typography carriers, visually distinct elements from each section
2. **Define states per triggerType**: static → idle; css-hover/js-class → idle + active; intersection → before + after; scroll-driven → before + mid + after
3. **Measure rect** via `getBoundingClientRect()` on both ref and impl, activating each state first (hover, classList.add, scrollTo)
4. **Selector screenshot** each element per state: `agent-browser --session <s> screenshot '<selector>' <path>`
5. **Diff**: `compare -metric AE ref.png impl.png diff.png` (ImageMagick) or `ffmpeg -lavfi "ssim"` — AE = 0 or SSIM >= 0.995 = PASS

### Phase D2: Numerical Diagnosis

> Always runs regardless of Phase D1 result — catches sub-pixel mismatches AE/SSIM misses.

Measure `getComputedStyle` on both ref and impl for all Phase D1 elements:

**Properties**: `fontSize`, `fontWeight`, `lineHeight`, `letterSpacing`, `fontFamily`, `color`, `backgroundColor`, `padding*`, `margin*`, `gap`, `width`, `height`, `display`, `flexDirection`, `alignItems`, `justifyContent`, `gridTemplateColumns`, `borderRadius`, `boxShadow`, `opacity`, `transform`

Build diff table: any property mismatch > 2px = FAIL. Fix and re-run both phases.

### Gate

```
Phase D1 Visual Gate: all elements all states = PASS
Phase D2 Numerical: mismatches = 0
Both must pass. "Approximately identical" = FAIL.
```

---

**Post-completion cleanup:**
```bash
# Remove extracted data — may contain sensitive info from the target site
rm -rf tmp/ref/<component>
```
> Screenshots, DOM snapshots, and video frames may contain auth tokens, PII, or session data visible on the target page. Always clean up after verification is complete.

## Section-Aligned Comparison (MANDATORY)

**After content-anchored comparison, verify scroll position alignment:**

Extract section top offsets from BOTH original and implementation:

```bash
# Original
agent-browser --session <ref-session> eval "(() => {
  const main = document.querySelector('main, .page-main, [role=main]') || document.body;
  return JSON.stringify([...main.children].filter(c => c.offsetHeight > 0 && c.tagName !== 'SCRIPT').map(s => ({
    class: (typeof s.className === 'string' ? s.className : '').slice(0, 60),
    top: Math.round(s.getBoundingClientRect().top + scrollY),
    height: Math.round(s.offsetHeight),
  })));
})()"

# Implementation
agent-browser --session <impl-session> eval "(() => {
  const main = document.querySelector('main, .page-main, [role=main]') || document.body;
  return JSON.stringify([...main.children].filter(c => c.offsetHeight > 0 && c.tagName !== 'SCRIPT').map(s => ({
    class: (typeof s.className === 'string' ? s.className : '').slice(0, 60),
    top: Math.round(s.getBoundingClientRect().top + scrollY),
    height: Math.round(s.offsetHeight),
  })));
})()"
```

Compare the results. If any section's top offset differs by more than 50px, there's a spacing bug. Common causes:
- Missing gap/padding on a flex container
- Section heights not matching (fixed vs auto)
- Missing spacer elements

## Original SVG/Asset Extraction (MANDATORY)

**Never create placeholder SVGs when the original has custom artwork.** If a logo, monogram, icon, or decorative element uses a custom SVG:

1. Extract the exact SVG from the DOM:
```bash
agent-browser --session <s> eval "(() => {
  const svg = document.querySelector('<selector>');
  return svg ? svg.outerHTML : 'not found';
})()"
```

2. Save the SVG as a component or public asset
3. Use `fill="currentColor"` for theming

**WHY:** In a real session, a footer logo (sparkles + bird + OR monogram = one SVG with viewBox 0 0 460 171) was replaced with a placeholder ellipse+text. It took 3 iterations to discover and extract the actual SVG. Always extract originals first.

## Tailwind Arbitrary Value Compatibility Check

Before using arbitrary values like `px-[19px]`, verify they work in the target Tailwind version:

```bash
# Quick test after first component renders
agent-browser --session <s> eval "(() => {
  const el = document.querySelector('[class*=\"px-[\"]');
  return el ? getComputedStyle(el).paddingLeft : 'no match or not rendered';
})()"
```

If the value is `0px`, Tailwind is not processing the arbitrary value. Use inline `style` props instead:
```tsx
style={{ paddingLeft: 19, paddingRight: 19 }}
```

**WHY:** Tailwind v4 with certain configs silently ignores arbitrary px values. This caused all padding to be 0px across an entire site, requiring a global find-and-replace to inline styles.
