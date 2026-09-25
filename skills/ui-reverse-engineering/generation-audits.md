# Generation Audits — Step 7 Reference

Post-generation audit procedures split out of `component-generation.md`. Read
only the section whose condition holds; the generation contract itself stays in
`component-generation.md`.

| Section | Read when |
|---|---|
| [Font size accuracy](#font-size-accuracy-extract--verify) | a text-bearing section fails `section-compare` or `font-parity` and you need per-element ref vs impl font metrics |
| [Post-generation CSS value diff](#post-generation-css-value-diff-mandatory-when-globalscss-copies-original-css) | `globals.css` was sourced by copying original stylesheet text (CSS-First mode) |
| [Post-generation body-scope audit](#post-generation-body-scope-audit-mandatory-for-embedded-projects) | the project runs inside another app (monorepo, showcase) with a `[data-project]` scope |
| [Post-generation font unit audit](#post-generation-font-unit-audit-mandatory-when-viewport-scaled) | `typography.json` shows `scalingSystem !== 'px-fixed'` (`em-conversion.json` exists) |

## Font size accuracy (extract + verify)

```bash
agent-browser --session <s> eval "(() => {
  const textEls = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,p,a,span,button,li,th,td,label')]
    .filter(el => el.offsetHeight > 0 && el.textContent?.trim().length > 0);
  return JSON.stringify(textEls.slice(0, 50).map(el => {
    const s = getComputedStyle(el);
    return {
      text: el.textContent?.trim().slice(0, 30),
      fontSize: s.fontSize, fontWeight: s.fontWeight,
      fontFamily: s.fontFamily?.split(',')[0],
      lineHeight: s.lineHeight, letterSpacing: s.letterSpacing,
      color: s.color, textTransform: s.textTransform,
    };
  }), null, 2);
})()"
```

Verify after implementation: compare font sizes on ≥5 text elements between ref + impl. >1px difference = fix immediately.

## Post-generation CSS value diff (MANDATORY when `globals.css` copies original CSS)

Compare ALL CSS rules in `globals.css` against the downloaded original CSS. This catches values that were changed or properties that were dropped during the copy process.

```bash
# For each major class, diff the original vs globals.css
for CLASS in ".intro_inner" ".heading-stretch_text" ".footer_bottom" ".cases__list" ".slider__wrapper"; do
  echo "=== $CLASS ==="
  ORIG=$(grep -oE "${CLASS//./\\.}[^{]*\\{[^}]+\\}" tmp/ref/<component>/css/app.css 2>/dev/null | head -1)
  IMPL=$(grep -oE "${CLASS//./\\.}[^{]*\\{[^}]+\\}" src/projects/<component>/styles/globals.css 2>/dev/null | head -1)
  echo "ORIG: $ORIG"
  echo "IMPL: $IMPL"
  echo ""
done
```

**Any property in ORIG but not in IMPL = bug.** Commonly dropped: `white-space: nowrap` (text wraps, overflow), `line-height` (inherits wrong value), `overflow: hidden` (content spills), changed `padding-top/bottom` (section height wrong).

⛔ **Gate:** Fix all diffs before proceeding to visual verification.

## Post-generation body-scope audit (MANDATORY for embedded projects)

If the project runs inside another app (monorepo, showcase), verify body-level styles are scoped:

```bash
# Check if body styles are also on the project container
grep -A10 'body {' src/projects/<component>/styles/globals.css | grep -E 'font-family|line-height|letter-spacing'
grep -A10 '\[data-project' src/projects/<component>/styles/globals.css | grep -E 'font-family|line-height|letter-spacing'
```

If body has `line-height` but `[data-project]` doesn't → all text will have wrong line-height. Copy body-level properties to the scoping selector. See `css-first-generation.md` Step 6.

## Post-generation font unit audit (MANDATORY when viewport-scaled)

After generating all components, verify NO hardcoded px font sizes leaked through:

```bash
# Scan generated components for px font sizes (FAIL if any found when viewport-scaled)
grep -rnE 'text-\[[0-9]+(\.[0-9]+)?px\]|fontSize:\s*["\x27][0-9]+(\.[0-9]+)?px["\x27]' \
  src/components/ src/app/ | grep -v '// px-override-ok'
```

If any matches found AND `typography.json` shows `scalingSystem !== 'px-fixed'`:
1. Look up the px value in `em-conversion.json`
2. Replace with the `emValue` from the conversion table
3. Re-run the scan until clean

**Exception:** `// px-override-ok` comment on the same line opts out (for borders, shadows, spacing — NOT font sizes).

---

After this step, return to `component-generation.md` (Step 7) and continue the generation contract.
