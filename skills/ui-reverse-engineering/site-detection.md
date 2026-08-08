# Site Type Detection — Run at Step 1 (before extraction)

Detect the site's tech stack to choose the right extraction strategy.

## Detection script

```bash
agent-browser --session <s> eval "(() => {
  const signals = {};

  // CSS strategy
  const stylesheets = [...document.querySelectorAll('link[rel=stylesheet]')].map(l => l.href);
  signals.hasTailwind = document.querySelector('[class*=tw-], [class*=sm\\:], [class*=md\\:]') !== null;
  signals.hasCSSModules = document.querySelector('[class*=_module_], [class*=__]') !== null;

  // Tailwind major version — required to predict v3/v4 transform/translate/rotate/scale
  // conflicts when porting into a host that uses the *other* major. v4 emits individual
  // `translate:`/`rotate:`/`scale:` properties on shared utilities; v3 emits a composed
  // `transform:`. If the cloned site uses v3 and the host uses v4 (or vice versa), the
  // same utility class will compound and produce double-translation/rotation. See
  // diagnosis.md Root Cause I.
  // Heuristic: probe a known utility's resolved style. v4 sets `--tw-translate-x` AND
  // emits a `translate:` declaration; v3 only emits `transform:`.
  signals.tailwindMajor = (() => {
    if (!signals.hasTailwind) return null;
    const probe = document.createElement('div');
    probe.className = 'translate-x-1';
    probe.style.cssText = 'position:fixed;left:-9999px;top:0;';
    document.body.appendChild(probe);
    const cs = getComputedStyle(probe);
    const v4 = cs.translate && cs.translate !== 'none';
    probe.remove();
    return v4 ? 4 : 3;
  })();
  signals.hasReadableClasses = document.querySelectorAll('[class]').length > 0 &&
    [...document.querySelectorAll('[class]')].slice(0, 20).every(el =>
      !el.className.match(/^[a-z]{5,}$/)); // Not hashed single-word classes

  // Platform
  signals.isShopify = !!document.querySelector('meta[name=shopify-checkout-api-token], script[src*=shopify]');
  signals.isWordPress = !!document.querySelector('meta[name=generator][content*=WordPress], link[href*=wp-content]');
  signals.isNextJS = !!document.querySelector('script[src*=_next], #__next');
  signals.isGatsby = !!document.querySelector('#___gatsby');

  // Animation library
  signals.hasGSAP = typeof gsap !== 'undefined';
  signals.hasFramerMotion = !!document.querySelector('[style*=will-change]');
  signals.hasLenis = !!document.querySelector('[data-lenis], .lenis');

  // CSS file type
  signals.siteCSS = stylesheets.filter(s => !s.includes('cdn.shopify') && !s.includes('googleapis')).length;
  signals.hasTypekit = stylesheets.some(s => s.includes('typekit'));

  return JSON.stringify(signals);
})()"
```

## Strategy selection

| Signal | CSS Strategy | Class Strategy |
|--------|-------------|----------------|
| `hasReadableClasses + siteCSS > 0` | **CSS-First**: Download CSS, use original class names | Use original classes in JSX |
| `hasTailwind` | **Extract-Values**: Read computed styles | Rewrite with Tailwind utilities |
| `hasCSSModules` | **Forensic preservation**: ref-derived JSX + local CSS | Preserve original hashed/CSS-module class names |
| `isShopify` | **CSS-First** (Shopify uses readable Liquid class names) | Use original classes |
| `isNextJS + hasTailwind` | **Extract-Values** | Tailwind utilities |

**Default:** If `generation-plan.json.forensicPreservation.required=true`, use forensic preservation. If that object also has `missingCssArtifacts=true` or `blockedUntilCssArtifacts=true`, recover CSS artifacts and rerun `generation-plan.sh` before generation; do not fall back to `standard-react-rebuild`. Else if `hasReadableClasses` is true AND `siteCSS > 2`, use CSS-First. Otherwise use Extract-Values.

### Tailwind major version mismatch — flag early

`tailwindMajor` records the cloned site's Tailwind major (3 or 4). The host app you're porting *into* may use a different major. When v3 ↔ v4 is mixed, transform/translate/rotate/scale utilities apply twice (one as a composed `transform:`, one as individual `translate:`/`rotate:`/`scale:` properties). Detect the host major the same way (probe `translate-x-1` on the host page) and **before** generation:

- **Same major on both** — proceed normally.
- **Different majors** — open `diagnosis.md` Root Cause I and pre-emptively add the `[data-project="<name>"] :is(...) { translate|rotate|scale: none !important }` block to the project's scoped globals.css, listing every shared utility class the JSX uses (`-translate-x-1/2`, `-rotate-90`, `-scale-x-[1]`, plus any `max-lg:` variants). Doing this once at scaffolding cost is cheaper than chasing visual regressions one-by-one later.

## Implementation Approach Gate (MANDATORY — decide before writing ANY code)

Beyond CSS strategy, choose the **implementation approach** based on site complexity. This decision has 10x impact on token efficiency.

### Detection: run this AFTER the signals above

```bash
agent-browser --session <s> eval "(() => {
  const signals = {};
  // Count CSS Module hashed classes (e.g., _card_j4aeg_2)
  const allEls = document.querySelectorAll('[class]');
  let hashedCount = 0;
  let totalCount = 0;
  allEls.forEach(el => {
    const cn = typeof el.className === 'string' ? el.className : '';
    if (cn.match(/_[a-z]+_[a-z0-9]+_\d+/)) hashedCount++;
    totalCount++;
  });
  signals.cssModuleRatio = totalCount > 0 ? (hashedCount / totalCount).toFixed(2) : 0;

  // Count JS-driven animations
  signals.hasGSAP = typeof gsap !== 'undefined' || !!document.querySelector('script[src*=gsap]');
  signals.hasLottie = !!document.querySelector('script[src*=lottie]') ||
    performance.getEntriesByType('resource').some(e => e.name.includes('lottie'));
  signals.hasCanvas = document.querySelectorAll('canvas').length;
  signals.hasMatterJS = typeof Matter !== 'undefined';

  // Count inline styles set by JS (GSAP artifacts)
  let inlineStyleCount = 0;
  allEls.forEach(el => {
    if (el.getAttribute('style')?.includes('translate') ||
        el.getAttribute('style')?.includes('rotate') ||
        el.getAttribute('style')?.includes('opacity')) inlineStyleCount++;
  });
  signals.jsInlineStyles = inlineStyleCount;

  // Total HTML size
  signals.totalHTMLSize = document.documentElement.outerHTML.length;

  return JSON.stringify(signals);
})()"
```

### Approach decision matrix

| Condition | Approach | Why |
|-----------|----------|-----|
| `cssModuleRatio > 0.3` OR `jsInlineStyles > 20` | **Forensic preservation** | CSS Modules hashes and JS-set layout states must be preserved; rewriting loses styling |
| `hasGSAP + hasLottie + hasCanvas > 1` | **Forensic preservation** | Too many animation libraries to freehand from scratch |
| `totalHTMLSize > 200KB` | **Forensic preservation** | Converting 200KB+ HTML by hand is token-expensive and error-prone |
| `cssModuleRatio < 0.1` AND simple Tailwind | **React Component** | Clean class names, straightforward conversion |
| Static site, no JS animations | **React Component** | Simplest approach works |

> **Behavioral signals are primary; library-name probes are a hint (allowlist audit).**
> The `hasGSAP + hasLottie + hasCanvas` row matches library IDENTITY (`typeof gsap`,
> `script[src*=lottie]`) and misses unknown/hand-rolled motion (ScrollMagic,
> anime.js, Motion One, native scroll-timeline, rAF/IO). Do NOT route a
> motion-heavy site to a clean **React Component** rebuild just because no *known*
> library was detected — that silently drops its motion. Decide "motion-heavy →
> motion-preserving approach" from the LIBRARY-AGNOSTIC behavioral signals that
> are already computed: `jsInlineStyles > 20` (row 1, JS-set inline transform/
> opacity), `hasFramerMotion` (`[style*=will-change]`), `hasCanvas`, and the
> observed-motion density from Phase B element-tracking / `transition-coverage.json`.
> The library names then only inform HOW to reproduce the motion, not WHETHER it
> exists. "Static site, no JS animations" must mean *behaviorally* static (no
> observed motion), not merely "no recognized library".

### Forensic preservation approach

**When to use:** `generation-plan.json.forensicPreservation.required=true`, or complex sites with CSS Modules, GSAP, Lottie, Canvas, heavy JS inline styles, or 200KB+ HTML.

This mode is still a component-generation strategy, not a whole-page static
mirror. Never dump `document.documentElement.outerHTML` / `document.body.innerHTML`
or a captured `live.html` into `impl/index.html`; that preserves a frozen DOM
state while dropping the original transition runtime. Generate ref-derived JSX,
copy local CSS chunks into the impl, preserve CSS-module className tokens, add
runtime libraries/data/controllers locally, and run the normal verification gates.

1. Extract/consume `dom-scaffold.json` as the visible DOM authority.
2. Copy ALL local `tmp/ref/<component>/css/*.css` chunks into `impl/src/ref-css/` with `bash "$PLUGIN_ROOT/scripts/extract/sanitize-ref-css.sh" "$(pwd)/tmp/ref/<component>" "<impl-root>"`, then import them before local overrides. If the plan says `missingCssArtifacts=true`, stop and recover those CSS chunks first instead of writing a freehand Tailwind approximation.
3. Download ALL fonts, images, videos, Lottie JSON to `/public/assets/` (or the host's public asset layout).
4. Translate scaffold nodes to JSX with original tags, depth, text, media elements, and CSS-module className tokens intact. Use section-level `dangerouslySetInnerHTML` only when it preserves depth better than manual JSX, never as a whole-page paste.
5. Port animations to project animation library, WAAPI, or a local controller using extracted durations/easing.
6. Clean GSAP inline styles carefully: **preserve layout values** (height, width in svh/vh), **remove animation values** (transform, opacity, visibility)

**Critical: wrapper div problem.**
```tsx
// ❌ WRONG — extra <div> breaks CSS Module selectors
<section className="program">
  <div dangerouslySetInnerHTML={{ __html: servicesHtml }} />
  <div dangerouslySetInnerHTML={{ __html: aboutHtml }} />
</section>

// ✅ CORRECT — concatenate HTML strings, single injection
const programHtml = servicesHtml + aboutHtml + beigeHtml;
<div dangerouslySetInnerHTML={{ __html:
  `<section class="program">${programHtml}</section>`
}} />
```

**Critical: GSAP inline style cleanup.**
JS-driven sites set inline styles for two purposes:
- **Layout values** (`height: 500svh`, `width: 350svh`) — MUST KEEP, re-set via ClientShell JS
- **Animation values** (`transform: rotateY(-180deg)`, `opacity: 0`, `visibility: hidden`) — REMOVE

Use `extract-dynamic-styles.sh` to classify:

```bash
PLUGIN_ROOT="${PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${UI_CLONE_ROOT:-}}}}"
if [ -z "$PLUGIN_ROOT" ]; then
  _marker="$(cat "$HOME/.config/ui-clone-skills/root" 2>/dev/null)"
  for candidate in "$PWD" "$PWD/.." "$PWD/../.." "$_marker" "${INSTALL_DIR:-$HOME/.local/share/ui-clone-skills}" "$HOME"/.claude/plugins/cache/*/ui-clone-skills/*/ "$HOME"/.codex/plugins/cache/*/ui-clone-skills/*/; do
    [ -n "$candidate" ] && [ -f "$candidate/ui_clone/pipeline.py" ] && PLUGIN_ROOT=$(cd "$candidate" && pwd) && break
  done
fi
[ -n "$PLUGIN_ROOT" ] || { echo "Set PLUGIN_ROOT=/path/to/ui-clone-skills" >&2; exit 1; }
bash "$PLUGIN_ROOT/scripts/extract/extract-dynamic-styles.sh" <session> tmp/ref/<component>
```

### React Component approach

**When to use:** Simple Tailwind sites, static pages, readable class names.

1. Extract DOM structure + computed styles
2. Generate React components with Tailwind classes
3. Copy images/fonts to public
4. Standard approach from `component-generation.md`

---

## CSS-First (readable classes)

1. Download ALL site-specific CSS files
2. Extract CSS variables to `variables.txt`
3. Import CSS into project
4. Use original class names in JSX
5. Override only for React-specific needs (sticky, scroll-driven transforms)

**⚠️ CSS-First compound selector trap** — Before generating any JSX, scan the CSS for compound selectors:
```bash
grep -o '\.[a-z][a-z-]*\.[a-z][a-z-]*' site.css | sort -u | head -30
```
If the CSS uses `.parentClass.childClass .target { ... }`, BOTH classes must be on the SAME element in JSX.
Example: `.app.main .header__logo` requires `<div className="app main">`, NOT `<div className="app"><main>`.
Missing the second class silently breaks ALL rules scoped under that compound selector.

**⚠️ CSS-First scroll-state class trap** — CSS-First sites often use body/wrapper class toggling for scroll states:
```bash
grep -o '\(is-scroll-down\|is-scroll-up\|is-scrolled\|is-fixed\|is-show\)[^{]*{[^}]*}' site.css | head -10
```
These classes must be added to the correct DOM element via a JS scroll listener.
Never assume `header.is-scrolled` — check which element the CSS selector targets. Often it's the page ROOT wrapper (`.app.main.is-scroll-down`), not the header.

## Extract-Values (obfuscated/Tailwind)

1. Extract computed styles via `getComputedStyle` for every element
2. Convert to Tailwind utilities or inline styles
3. No original CSS import (hashed classes are meaningless)
4. More manual work, more iteration needed
