# Common Computed-Diff Selector Sets — Reference

Ready-to-use selector lists for `computed-diff.sh`.
These are **framework-agnostic** — they work on any site using class-based or semantic HTML.

## Usage

```bash
SCRIPTS="${VISUAL_DEBUG_SCRIPTS_DIR:-}"
if [ -z "$SCRIPTS" ]; then
  for root in "${PLUGIN_ROOT:-}" "${CODEX_PLUGIN_ROOT:-}" "${CLAUDE_PLUGIN_ROOT:-}" "${UI_CLONE_ROOT:-}" "$PWD" "$PWD/.." "$PWD/../.." "${INSTALL_DIR:-$HOME/.local/share/ui-clone-skills}"; do
    [ -n "$root" ] && [ -f "$root/skills/visual-debug/scripts/ae-compare.sh" ] && SCRIPTS=$(cd "$root/skills/visual-debug/scripts" && pwd) && break
  done
fi
[ -n "$SCRIPTS" ] || { echo "Set VISUAL_DEBUG_SCRIPTS_DIR or PLUGIN_ROOT" >&2; exit 1; }
bash "$SCRIPTS/computed-diff.sh" <session> <orig-url> <impl-url> \
  "h1" "h2" "h3" "h4" "body" "header" "main" "footer"
```

Or paste selector groups directly:

```bash
bash computed-diff.sh my-session https://original.com http://localhost:3000 \
  "h1" "h2" "h3" "h4" \
  "body" "main" \
  "header" "footer" \
  "nav" "ul" "li" \
  "a" "button" "input" "select"
```

---

## Selector Groups

### Typography (headings + body)
Catches: fontWeight resets (Tailwind preflight), fontSize scaling, lineHeight

```
h1  h2  h3  h4  h5  h6
p  span  a  strong  em  label
```

### CSS Framework Reset Canaries
These specific combinations frequently break under Tailwind v3/v4 preflight:

```
h1  h2  h3
img
button  input  select  textarea
```

**Known Tailwind preflight resets that cause mismatches:**
| Element | Property reset | Symptom | Fix |
|---------|---------------|---------|-----|
| `h1–h6` | `font-weight: inherit` | Headings inherit body weight (500) instead of bold (700) | Add `h1,h2,h3,h4,h5,h6 { font-weight: bold }` to global CSS |
| `img` | `display: block; height: auto` | Inline images become block, HTML `height` attr ignored | Use `style={{ height: "Npx" }}` or `.class { display: inline !important; height: Npx !important }` |
| `button` | `background: transparent` | Buttons lose background color | Override explicitly |
| `a` | `color: inherit; text-decoration: inherit` | Links lose color + underline | Override per design |

### Layout Sections
Catches: margin/padding differences, width mismatches, box-sizing issues

```
header  main  footer  nav  aside  section  article
```

### News / Portal sites
Common diff-prone selectors on news / portal layouts (notice boxes, partner lists, weather/stock widgets, tabbed content):

```
[class*=notice_area]
[class*=partner_box]
[class*=list_corp]
[class*=news_logo]
[class*=tab_text]
[class*=shortcut_item]
[class*=search_input]
[class*=content_header]
[class*=content_paging]
[class*=subscription_box]
[class*=media_area]
[class*=link_login]
[class*=weather_box]
[class*=stock_box]
[class*=calendar_box]
```

### Corporate / brand homepage
Common selectors on corporate hero + masonry grid layouts:

```
.header__logo
.header__inner
.nav__link
.main-title
.main-news
.masonry-grid-item
.btn-basic
.main-middle-banner
.footer__lottie
.footer__menu
```

**Compound-selector traps** — when site CSS uses `.app.main .target { ... }`, BOTH `app` AND `main` classes must be on the SAME wrapper element. Verify via:
```bash
grep -oE '\.[a-z][a-z-]*\.[a-z][a-z-]*' site.css | sort -u
```
A wrapper missing the second class silently breaks every compound-scoped rule (logo sizing, scroll-state typography, etc.).

### General E-commerce / Portal
```
[class*=header]
[class*=footer]
[class*=nav]
[class*=logo]
[class*=search]
[class*=banner]
[class*=card]
[class*=price]
[class*=btn]
[class*=badge]
```

---

## OS Font Scaling Artifact

If fontSize/lineHeight/width/height all differ by the same ratio (e.g. orig=14.7px impl=14px, ratio=1.05),
this is caused by macOS system text size setting (105%), not a real bug.

Confirm with:
```bash
IGNORE_FONT_SIZE=1 bash computed-diff.sh <session> <orig> <impl> <selectors>
```

If mismatches drop to 0 → it's OS scaling. No fix needed.
If mismatches remain → real CSS differences exist.

---

## CSS Framework / Legacy Bundle Class Collision Canaries

Use when a CSS framework (Tailwind, Bootstrap, etc.) is loaded alongside a legacy JS bundle.
Some class names carry meaning in **both** the framework and the bundle's `querySelector` calls — renaming them to fix a style issue silently breaks the bundle's init.

**High-risk collisions (framework utility + common bundle querySelector target):**
| Class | Framework effect | Why bundles use it | Risk when renamed |
|-------|-----------------|-------------------|-------------------|
| `.container` | Tailwind: `max-width` via `@media`; Bootstrap: same | Page-scope init root | Bundle `querySelector` returns null → entire page init fails silently |
| `.sticky` | Tailwind: `position: sticky` | Sticky nav/header toggle | Position forced, nav broken |
| `.wrapper` / `.inner` / `.wrap` | No framework utility | Very common bundle container selector | Hierarchy queries break |

**Low-risk:** `.hidden`, `.block`, `.flex`, `.grid` — JS toggles *onto* these, doesn't *query* for init. Usually safe to rename.

**Diagnostic:**
```bash
# Extract all class selectors the bundle queries
grep -o "querySelector('[^']*')\|querySelector(\"[^\"]*\")" public/js/*.min.js \
  | grep -oE "'[.][^']+'" | sort -u | head -30
# Does any result match a class you've renamed or that the framework overrides?
```

**Fix pattern:**
```jsx
// ❌ Renames away → bundle querySelector('.container') returns null, init fails
<main className="nc-override">

// ✅ Keep original so bundle finds it; add override class to target with CSS
<main className="nc-override container">
```
```css
/* Override only the conflicting property */
.nc-override { max-width: none !important; }
```

---

## Pseudo-element Selectors (computed-diff blind spot)

`computed-diff.sh` only checks `getComputedStyle(el)` — it never checks `::before` / `::after`. Active indicators, decorative underlines, and hover effects are often implemented as pseudo-elements and will not appear in any diff output.

**Run this separately after computed-diff when indicators look wrong:**

```bash
agent-browser --session <s> eval "
var sel = '[class*=tab_item], [class*=nav_item], [role=tab], [aria-selected]';
var els = Array.from(document.querySelectorAll(sel)).slice(0, 3);
JSON.stringify(els.map(function(el) {
  return ['::before', '::after'].map(function(pseudo) {
    var s = window.getComputedStyle(el, pseudo);
    return { el: el.className.slice(0,30), pseudo: pseudo, content: s.content, h: s.height, bg: s.backgroundColor, position: s.position, bottom: s.bottom };
  });
}));
"
```

**Skip if:** `content: none` or `display: none` — pseudo-element is not rendered.

**Act if:** `content: ""` + `position: absolute` + non-transparent background → this is an active indicator. Measure `bottom`, `left`, `right`, `height`, `border-radius` and replicate as a child `<span>` in React.
