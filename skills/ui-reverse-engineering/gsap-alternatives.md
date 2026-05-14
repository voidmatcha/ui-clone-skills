# GSAP Plugin Alternatives — Step 7

Read this sub-doc when `transition-spec.json` lists GSAP plugins but the implementation should avoid a GSAP dependency or match a project-native animation stack. Otherwise skip. These alternatives are dead weight on every clone that can use GSAP directly. Pointer in from `transition-implementation.md` Step 7. GSAP plugins are not paid-feature gate findings; that gate concerns paid fonts and CDNs.

Priority order:
1. Project-specific animation library if available.
2. Open-source npm packages.
3. Manual CSS / native API implementation.

> **Anti-patterns embedded in transition-implementation.md** stay in that file even when you use the OSS alternatives here:
> - SplitText mask wrapper CSS strict rules (`line-height` / `vertical-align` traps)
> - IntersectionObserver placement for masked reveals (`overflow: hidden` + transform child)
> - GSAP `stagger` semantics (`each` vs `amount`)
>
> Those rules apply to ANY split-text / reveal implementation, GSAP or not — they live in transition-implementation.md because their context is generation, not alternative selection.

---

## SplitText → `splitting` npm package (or project animation library)

GSAP's SplitText splits text into chars/words/lines for stagger animations. Open-source alternative: [`splitting`](https://splitting.js.org/) npm package, which splits text into chars/words/lines with CSS custom properties for index-based stagger. If the project has its own animation library with splitText support, prefer that.

```ts
// Using splitting (npm install splitting)
import Splitting from 'splitting'

const result = Splitting({ target: element, by: 'chars' })
const chars = result[0].chars

// Animate with WAAPI
chars.forEach((char, i) => {
  char.style.opacity = '0'
  char.style.transform = 'translateY(200%)'
  const anim = char.animate(
    [
      { opacity: 0, transform: 'translateY(200%) scaleY(0)' },
      { opacity: 1, transform: 'translateY(0) scaleY(1)' },
    ],
    { delay: i * 100, duration: 1000, fill: 'forwards', easing: 'cubic-bezier(0.16, 1, 0.3, 1)' }
  )
  anim.onfinish = () => { char.style.opacity = '1'; char.style.transform = 'none'; anim.cancel() }
})
```

**When to use:** Any site with `SplitText.create()` in the bundle.

---

## MorphSVG → Manual SVG path interpolation or `rx`/`ry` animation

GSAP's MorphSVG morphs between SVG path shapes. Without the plugin:

1. **For simple rect → circle morphs** (CTA buttons): Animate `rx`/`ry` attributes of the SVG `<rect>` from pill radius to circle radius using `gsap.to()`.
2. **For complex path morphs**: Use `flubber` (npm package) for path interpolation, or pre-compute intermediate paths and crossfade with opacity.

```ts
// Simple rect → circle morph (no MorphSVG needed)
gsap.to(rectElement, {
  attr: { rx: circleRadius, ry: circleRadius, width: circleSize, height: circleSize },
  duration: 0.9,
  ease: 'elastic.out(0.8, 0.8)',
})
```

---

## ScrollSmoother → Lenis (or project library)

GSAP's ScrollSmoother adds smooth scroll behavior. Alternatives:
- [`lenis`](https://github.com/darkroomengineering/lenis) npm package — widely used, lightweight, open-source
- Project-specific smooth scroll library if available

---

## Draggable → Native pointer events

GSAP's Draggable is actually free, but if not using GSAP at all:
- Use native `pointerdown`/`pointermove`/`pointerup` events
- Calculate drag delta and apply transforms

---

## DrawSVG → CSS `stroke-dashoffset` animation

```css
.draw-in {
  stroke-dasharray: var(--path-length);
  stroke-dashoffset: var(--path-length);
  transition: stroke-dashoffset 1s ease-out;
}
.draw-in.active {
  stroke-dashoffset: 0;
}
```

---

## Detection rule

When `transition-spec.json` contains entries referencing GSAP plugins that you plan to replace, add a note in the spec:

```json
{
  "name": "text-reveal-stagger",
  "gsap_plugin": "SplitText",
  "oss_alternative": "splitting (npm) or project animation library",
  "notes": "Replace SplitText.create() with splitting({ target: el, by: 'chars' })"
}
```

This ensures the generation step uses the correct alternative without re-discovering it.

---

After this step, return to `transition-implementation.md`. The SplitText mask wrapper CSS rules and the IntersectionObserver placement rules below the "GSAP Plugin Alternatives" pointer apply to ANY split-text / reveal implementation, regardless of which OSS library you swapped in here.
