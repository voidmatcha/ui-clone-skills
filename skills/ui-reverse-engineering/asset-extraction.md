# Asset Extraction — Step 2.5

> Extract `<head>` metadata, download CSS files, fonts, images, SVGs, and videos.
> Called from pipeline Step 2.5 (after DOM extraction Steps 1–2).

## Step 2.5: Extract Head Metadata, Download Assets & CSS

After DOM structure extraction, extract `<head>` metadata, download visible image assets, and **download ALL site-specific CSS files**.

### Download original CSS files (MANDATORY)

**This is the single most important extraction step.** The original CSS files contain the exact styles that make the site look the way it does. Using these directly (instead of re-implementing from extracted computed values) eliminates the entire category of "looks slightly different" bugs.

```bash
# Get all site-specific CSS URLs (exclude third-party: shopify infra, klaviyo, analytics)
agent-browser --session <s> eval "(() => JSON.stringify(
  performance.getEntriesByType('resource')
    .filter(e => e.name.match(/\.css(\?|$)/i))
    .filter(e => {
      const url = e.name;
      // Keep site-specific CSS, exclude infrastructure
      const isInfra = url.includes('shopifycloud') || url.includes('klaviyo') ||
                       url.includes('checkout-web') || url.includes('extensions/');
      return !isInfra;
    })
    .map(e => e.name)
))()"

# Download each CSS file
mkdir -p tmp/ref/<component>/css
# curl -sL "<url>" > tmp/ref/<component>/css/<descriptive-name>.css
```

**Naming convention:** Use the filename from the URL path, not a generic name:
- `hero-index-video.css`, `products-showcase.css`, `index-faq.css`, etc.
- `app.css` for the main stylesheet

**Portable filename derivation (BSD sed gotcha):** macOS `sed` is BSD, not GNU — backslash classes like `\d` and some `-E` regex features behave differently. A `sed`-based filename derivation that works on GNU may collapse every URL to the same filename on macOS, causing 13 CSS files to overwrite each other in `css/`. Use shell parameter expansion or `awk` instead:

```bash
# Portable on bash 3.2+ (macOS default)
url="https://example.com/static/css/hero-index-video.css?v=42"
fname="${url##*/}"        # hero-index-video.css?v=42
fname="${fname%%\?*}"     # hero-index-video.css
curl -sL "$url" > "tmp/ref/<component>/css/$fname"

# Verify after batch download — count must match URL count
ls tmp/ref/<component>/css/*.css | wc -l
```
Sanity-check after the batch finishes — if the file count is 1 when you downloaded N URLs, your sed pattern collapsed them.

**What to do with downloaded CSS:**
1. Read each file to understand the class names and their exact styles
2. During generation (Step 7), include these CSS files in the project
3. Use the original class names in JSX so the original CSS applies directly
4. This replaces the "extract computed values → re-implement with inline styles" approach

**Gate:**
```
□ tmp/ref/<component>/css/ directory exists
□ At least the main app.css + section-specific CSS files downloaded
□ Each file > 500 bytes (not error pages)
```

### Extract and preserve CSS variables (MANDATORY)

Before cleaning `:root` blocks from downloaded CSS, extract ALL CSS variables to a separate file:

```bash
# Extract all :root variables from all downloaded CSS files
cat tmp/ref/<component>/css/*.css | grep -oE '\-\-[a-zA-Z0-9_-]+:\s*[^;}]+' | sed 's/}.*//' | sort -u > tmp/ref/<component>/css/variables.txt

# These MUST be added to the project's globals.css :root block
# Missing variables cause silent failures — elements get wrong colors, sizes, or positions
```

**Common variables that get lost:**
- `--hero-video-container-width/height/borderadius` — splash animation initial size
- `--content-inner-container` — panel widths
- `--grey-3-50`, `--grey-9-60` — border/overlay colors
- Custom timing variables (`--duration-*`, `--ease-*`)

**Gate: After importing original CSS, verify no CSS variable is undefined:**
```bash
agent-browser --session <s> eval "(() => {
  const missing = [];
  document.querySelectorAll('*').forEach(el => {
    const s = getComputedStyle(el);
    // Check for empty/default values that indicate missing variables
  });
})()"
```

### Head metadata extraction

```bash
agent-browser --session <s> eval "
(() => {
  const title = document.title || '';
  const favicon = (() => {
    const link = document.querySelector('link[rel*=\"icon\"]');
    return link ? link.href : '';
  })();
  const viewport = (() => {
    const meta = document.querySelector('meta[name=\"viewport\"]');
    return meta ? meta.content : '';
  })();
  return JSON.stringify({ title, favicon, viewport }, null, 2);
})()
"
```

**Save output to** `tmp/ref/<component>/head.json`

### Collect visible images

Collect URLs of images actually rendered on screen (`height > 0`):

```bash
agent-browser --session <s> eval "
(() => {
  const images = [];
  document.querySelectorAll('img').forEach(img => {
    const r = img.getBoundingClientRect();
    if (r.height > 0 && img.src && img.src.startsWith('https://')) {
      const cn = typeof img.className === 'string' ? img.className : img.className?.baseVal || '';
      images.push({ type: 'image', src: img.src, element: img.tagName.toLowerCase() + (cn.trim().split(' ')[0] ? '.' + cn.trim().split(' ')[0] : '') });
    }
  });
  return JSON.stringify(images, null, 2);
})()
"
```

Also collect CSS `background-image` — many sites use these for hero images, section backgrounds, and card images:

```bash
agent-browser --session <s> eval "
(() => {
  const bgImages = [];
  document.querySelectorAll('*').forEach(el => {
    const bg = getComputedStyle(el).backgroundImage;
    if (bg && bg !== 'none' && bg.includes('url(')) {
      const r = el.getBoundingClientRect();
      if (r.width > 50 && r.height > 50) {
        const url = bg.match(/url\(['\"]?([^'\"\\)]+)['\"]?\)/)?.[1] || '';
        if (url.startsWith('http')) {
          bgImages.push({ type: 'bg-image', src: url, element: el.tagName.toLowerCase(), width: Math.round(r.width), height: Math.round(r.height) });
        }
      }
    }
  });
  return JSON.stringify(bgImages, null, 2);
})()
"
```

**Merge both arrays and save to** `tmp/ref/<component>/visible-images.json`

> If no `<img>` tags AND no CSS `background-image` found (site uses 100% SVG/Lottie/Canvas), save `[{"note": "No images found — site uses SVG/Lottie/Canvas only", "images": []}]` so the pipeline gate passes.

### Collect inline SVGs (logos, icons, brandmarks)

Inline SVGs cannot be downloaded as image files — they must be extracted as source code. **Never recreate SVGs from visual appearance.** A "similar" logo SVG is a wrong logo.

```bash
agent-browser --session <s> eval "
(() => {
  const svgs = [];
  document.querySelectorAll('svg').forEach(svg => {
    const r = svg.getBoundingClientRect();
    if (r.height < 1 || r.width < 1) return;

    // Classify: logo, icon, decorative, or functional
    const parent = svg.parentElement;
    const isInLink = !!svg.closest('a[aria-label], a[href]');
    const isInButton = !!svg.closest('button');
    const hasText = svg.closest('[aria-label]')?.getAttribute('aria-label') || '';
    const pathCount = svg.querySelectorAll('path, rect, circle, line, polygon').length;

    let role = 'decorative';
    if (isInLink && hasText.toLowerCase().includes('home')) role = 'logo';
    else if (isInLink || hasText) role = 'brandmark';
    else if (isInButton) role = 'icon';
    else if (pathCount <= 3 && r.width < 30) role = 'icon';

    const cn = typeof svg.className === 'string' ? svg.className : svg.className?.baseVal || '';
    svgs.push({
      role,
      selector: 'svg' + (cn.trim().split(' ')[0] ? '.' + cn.trim().split(' ')[0].replace(/[^a-zA-Z0-9_-]/g, '') : ''),
      viewBox: svg.getAttribute('viewBox'),
      width: Math.round(r.width),
      height: Math.round(r.height),
      outerHTML: svg.outerHTML,
      section: svg.closest('section')?.className?.split(' ')[0] || svg.closest('header,footer,nav')?.tagName?.toLowerCase() || 'none',
      ariaLabel: hasText || null,
    });
  });
  return JSON.stringify(svgs, null, 2);
})()
"
```

**Save output to** `tmp/ref/<component>/inline-svgs.json`

**Generation rule:** When generating components, use the `outerHTML` from this file verbatim. Convert HTML attributes to JSX (e.g., `stroke-width` → `strokeWidth`, `class` → `className`, `fill-rule` → `fillRule`). Never manually redraw SVG paths — always copy the extracted `d` attributes.

### Download assets

Download the favicon from `head.json` and each image from `visible-images.json` to `tmp/ref/<component>/assets/`. Rules:

- **HTTPS only** — skip `http://` and `data:` URIs
- **10 MB limit** per file, 30s timeout
- **No credential forwarding** — no cookies or auth tokens
- If a download fails (404, CORS, timeout), record `"local": null` with an error note in `assets.json` — component generation will use a descriptive placeholder instead

```bash
mkdir -p tmp/ref/<component>/assets

# Download favicon (URL from head.json)
# Download each visible image (URLs from visible-images.json)
# Use: curl -s --max-time 30 --max-filesize 10485760 --fail --location -o <path> -- <url>
```

**Save** `tmp/ref/<component>/assets.json` — record each downloaded asset:

```json
[
  { "type": "favicon", "src": "https://...", "local": "assets/favicon.ico" },
  { "type": "image", "src": "https://...", "local": "assets/hero.webp", "element": "img.hero" },
  { "type": "image", "src": "https://...", "local": null, "error": "404", "element": "img.banner" }
]
```

**Generation rules for downloaded assets:**
1. **Favicon:** Copy to the project's public/static directory and reference it in the HTML head (`<link rel="icon" href="/favicon.ico" />`). Without this, the browser tab shows a generic icon.
2. **Images:** Copy to the public directory (e.g., `public/images/`). Reference them with absolute paths (`/images/hero.webp`).

### Download fonts

Custom fonts that fail to load cause cascading layout differences — wrong glyph widths change text wrapping, line heights, and element positions throughout the page. Download all fonts used by the site.

```bash
agent-browser --session <s> eval "
(() => {
  const fonts = [];
  for (const sheet of document.styleSheets) {
    try {
      for (const rule of sheet.cssRules) {
        if (rule.type === CSSRule.FONT_FACE_RULE) {
          const src = rule.style.src || '';
          const urlMatch = src.match(/url\([\"']?(https?:\/\/[^\"')]+)[\"']?\)/);
          if (urlMatch) {
            fonts.push({
              family: rule.style.fontFamily?.replace(/[\"']/g, ''),
              weight: rule.style.fontWeight || 'normal',
              style: rule.style.fontStyle || 'normal',
              url: urlMatch[1],
            });
          }
        }
      }
    } catch(e) {}
  }
  return JSON.stringify(fonts, null, 2);
})()
"
```

Download each font file:

```bash
mkdir -p tmp/ref/<component>/fonts
# For each font entry:
# curl -s --max-time 30 --fail --location -o tmp/ref/<component>/fonts/<filename>.woff2 -- <url>
```

**Save output to** `tmp/ref/<component>/fonts.json`

**Generation rule:** Copy font files to `public/fonts/` and register each with `@font-face` in CSS, matching the exact `font-family`, `font-weight`, and `font-style` from the extracted data. Without this, the browser falls back to system fonts with different metrics, causing every text element's width, height, and position to differ from the reference.

#### When `document.styleSheets` returns empty (CORS-blocked or JS-injected fonts)

Some sites inject `@font-face` rules via a separately-fetched CSS file that is CORS-blocked from `cssRules` access. The font-family name appears in `computedStyle` but the font never renders — it silently falls back to the system font.

**Verify fonts are actually loaded** (always run this after implementing):

```bash
agent-browser --session <s> eval "
var fonts = [];
document.fonts.forEach(function(f) { fonts.push({ family: f.family, status: f.status }); });
var byFamily = {};
fonts.forEach(function(f) {
  if (!byFamily[f.family]) byFamily[f.family] = { loaded: 0, total: 0 };
  byFamily[f.family].total++;
  if (f.status === 'loaded') byFamily[f.family].loaded++;
});
JSON.stringify(byFamily);
"
```

Note: `status: 'unloaded'` means the font face is registered but its unicode-range hasn't been needed yet (lazy loading). This is normal for CJK fonts with 100+ subsets — not all subsets will be used. What matters is that at least some faces for the target family show `status: 'loaded'` after the page renders text in that font.

A quick check: does the target font have *any* loaded face?

```bash
agent-browser --session <s> eval "document.fonts.check('16px \"<Font Family>\"', '<sample-glyphs>')"
# Returns true if the font is available for those characters, false if falling back.
# For CJK fonts, pass a few characters from the target subset (e.g. one Hangul
# triplet or three CJK ideographs). For Latin-only fonts, 'ABC' is sufficient.
```

If this returns `false` but `computedStyle.fontFamily` lists the font — no `@font-face` source was loaded. The name is in the cascade but the browser has no file to render it from.

**Fix: extract `@font-face` rules directly from the CSS file via curl**

```bash
# 1. Find the CSS file URL (check <link> tags or network requests)
agent-browser --session <s> eval "
JSON.stringify(Array.from(document.querySelectorAll('link[rel=stylesheet]')).map(function(l) { return l.href; }));
"

# 2. Grep the CSS for @font-face rules containing the font name
curl -s "<css-url>" | grep -o '@font-face{[^}]*FontName[^}]*}' > /tmp/fontname-fontface.css

# 3. Copy to public and link it in your layout
cp /tmp/fontname-fontface.css public/css/fontname.css
# In layout: <link rel="stylesheet" href="/css/fontname.css" />
```

For CJK fonts with 100+ unicode-range subsets, extracting just the `@font-face` rules and linking them from `/public` is faster to set up than downloading every woff2 file. The tradeoff: the font files themselves still load from the original CDN (not self-hosted), so this only works while the CDN URL remains stable. For a permanent clone, download the woff2 files too. For a dev/review clone, linking the CSS rules is sufficient.

### Download video backgrounds

Sites with full-screen video backgrounds (hero videos, product videos) require the actual video file for accurate reproduction. Without it, implementations use a static image placeholder that will always fail SSIM comparison against the original.

```bash
# Extract video source URLs
agent-browser --session <s> eval "(() => {
  const videos = document.querySelectorAll('video');
  return JSON.stringify([...videos].map((v, i) => ({
    index: i,
    currentSrc: v.currentSrc || v.src,
    sources: [...v.querySelectorAll('source')].map(s => ({ src: s.src, type: s.type })),
    section: (() => {
      let p = v.parentElement;
      while (p && p !== document.body) {
        const c = typeof p.className === 'string' ? p.className : '';
        if (c.includes('hero')) return 'hero';
        if (c.includes('showcase')) return 'showcase';
        if (p.tagName === 'SECTION') return 'section-' + i;
        p = p.parentElement;
      }
      return 'unknown';
    })()
  })));
})()"
```

Download each video (prefer mp4 for compatibility):

```bash
mkdir -p public/videos
# curl -sL --max-time 60 -o public/videos/<section>-bg.mp4 -- <mp4-url>
```

Also extract a static frame as fallback for SSG/loading state:

```bash
ffmpeg -y -i public/videos/<section>-bg.mp4 -vframes 1 -ss 2 public/images/<section>-video-frame.jpg
```

**Generation rule:** Use `<video autoPlay muted loop playsInline>` for video backgrounds, with the static frame as `poster`. This eliminates the #1 source of SSIM mismatch between original and implementation.

### Download fonts from Typekit / Adobe Fonts

Many sites use Adobe Fonts (Typekit) which loads via a CSS file like `https://use.typekit.net/<id>.css`. The standard font extraction (above) may not capture these because cross-origin stylesheets block `cssRules` access.

```bash
# 1. Find Typekit CSS URL
agent-browser --session <s> eval "(() => {
  const links = [...document.querySelectorAll('link[href*=typekit]')];
  return JSON.stringify(links.map(l => l.href));
})()"

# 2. Download the CSS and extract @font-face URLs
curl -sL "https://use.typekit.net/<id>.css" > tmp/ref/<component>/typekit.css

# 3. Extract woff2 URLs for each font-family
grep -oE 'url\("[^"]+\.woff2[^"]*"\)' tmp/ref/<component>/typekit.css

# 4. Download each woff2 file
# curl -sL -o public/fonts/<family>-<weight>.woff2 -- <url>
```

**Or use the automated script:**

```bash
bash "$PLUGIN_ROOT/scripts/extract/extract-assets.sh" <session> tmp/ref/<component> <public-dir>
```

This handles videos, Typekit fonts, and CDN fonts in one pass.

---
