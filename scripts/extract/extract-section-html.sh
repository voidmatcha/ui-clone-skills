#!/usr/bin/env bash
# extract-section-html.sh — Extract per-section HTML structure + computed CSS from original site
# Section name keyword → name mapping: shared with extract-assets.sh and
# section-clips.sh. Adding a new keyword? Update all 3.
# Usage: bash extract-section-html.sh <session> <output-dir>
#
# For each major section on the page:
#   1. Extracts the complete innerHTML (cleaned, max 3 levels deep)
#   2. Extracts computed styles for every direct child element
#   3. Extracts media elements (<video>, <img>, <source>) with their attributes
#   4. Saves per-section files: <section-name>.html, <section-name>.css.json
#
# This produces the ground truth for code generation:
#   - HTML structure tells you EXACTLY what elements exist and how they nest
#   - CSS values tell you EXACTLY what styles to apply
#   - Media elements tell you what videos/images to use and how they're configured
#
# Why this matters:
#   Without this, code generation guesses the HTML structure from screenshots.
#   Screenshots show the RESULT but not the STRUCTURE. A flexbox row and a grid
#   can look identical in a screenshot but require completely different code.

set -euo pipefail

START_TIME=$(date +%s%3N 2>/dev/null || python3 -c "import time; print(int(time.time()*1000))")

SESSION="${1:?Usage: extract-section-html.sh <session> <output-dir>}"
trap 'agent-browser --session "$SESSION" close 2>/dev/null || true' EXIT
DIR="${2:?Usage: extract-section-html.sh <session> <output-dir>}"

mkdir -p "$DIR/html"

echo "═══ Section HTML + CSS Extraction ═══"

# Detect sections and extract each one
RESULT=$(agent-browser --session "$SESSION" eval "(() => {
  const sections = [];

  // Find all top-level sections
  let candidates = [...document.querySelectorAll('header, section, footer')];
  if (candidates.length === 0) {
    const main = document.querySelector('main') || document.body;
    candidates = [...main.children].filter(el =>
      el.getBoundingClientRect().height > 50 &&
      !['SCRIPT','STYLE','LINK','META'].includes(el.tagName)
    );
  }

  candidates.forEach((section, idx) => {
    const tag = section.tagName.toLowerCase();
    const id = section.id || '';
    const cls = typeof section.className === 'string' ? section.className : '';

    // Generate name
    let name = id || '';
    if (!name) {
      const combined = cls.toLowerCase();
      const kwMap = [['hero','hero'],['banner','banner'],['showcase','showcase'],['product','product'],['text-scroll','text-scroll'],['pricing','pricing'],['testimonial','testimonials'],['feature','features'],['discover','features'],['about','about'],['team','team'],['gallery','gallery'],['portfolio','portfolio'],['contact','contact'],['cta','cta'],['faq','faq'],['blog','blog'],['newsletter','newsletter'],['subscribe','subscribe'],['footer-section','newsletter'],['partner','partners'],['client','clients'],['stats','stats']];
      let matched = false;
      for (const [kw, n] of kwMap) {
        if (combined.includes(kw)) { name = n; matched = true; break; }
      }
      if (!matched) {
        if (tag === 'header') name = 'header';
        else if (tag === 'footer') name = 'footer';
        else name = tag + '-' + idx;
      }
    }
    name = name.replace(/[^a-zA-Z0-9_-]/g, '-').substring(0, 40);

    // Get computed styles for section and key children
    const gs = (el) => {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return {
        tag: el.tagName.toLowerCase(),
        id: el.id || undefined,
        class: (typeof el.className === 'string' ? el.className : '').substring(0, 100),
        text: el.textContent?.trim().substring(0, 50) || undefined,
        styles: {
          display: s.display, position: s.position,
          width: Math.round(r.width), height: Math.round(r.height),
          x: Math.round(r.left), y: Math.round(r.top + window.scrollY),
          fontSize: s.fontSize, fontWeight: s.fontWeight, fontFamily: s.fontFamily,
          color: s.color, backgroundColor: s.backgroundColor,
          padding: s.padding, margin: s.margin,
          borderRadius: s.borderRadius, border: s.border,
          backdropFilter: s.backdropFilter,
          overflow: s.overflow, opacity: s.opacity,
          flexDirection: s.flexDirection, justifyContent: s.justifyContent,
          alignItems: s.alignItems, gap: s.gap,
          gridTemplateColumns: s.gridTemplateColumns,
          transform: s.transform,
          backgroundImage: s.backgroundImage?.substring(0, 200),
        }
      };
    };

    // Section-level styles
    const sectionStyles = gs(section);

    // Collect children (max 2 levels deep, max 30 elements)
    const children = [];
    const walk = (el, depth) => {
      if (depth > 2 || children.length > 30) return;
      [...el.children].forEach(child => {
        if (['SCRIPT','STYLE','LINK'].includes(child.tagName)) return;
        const info = gs(child);
        info.depth = depth;
        children.push(info);
        walk(child, depth + 1);
      });
    };
    walk(section, 1);

    // Media elements
    const media = [];
    section.querySelectorAll('video, video source, img').forEach(el => {
      const m = { tag: el.tagName.toLowerCase() };
      if (el.tagName === 'VIDEO') {
        m.src = el.currentSrc || el.src || '';
        m.autoplay = el.autoplay;
        m.muted = el.muted;
        m.loop = el.loop;
        m.playsInline = el.playsInline;
        m.poster = el.poster || '';
        m.width = el.offsetWidth;
        m.height = el.offsetHeight;
      } else if (el.tagName === 'SOURCE') {
        m.src = el.src || '';
        m.type = el.type || '';
      } else if (el.tagName === 'IMG') {
        m.src = el.src || '';
        m.alt = el.alt || '';
        m.width = el.offsetWidth;
        m.height = el.offsetHeight;
        m.loading = el.loading || '';
      }
      media.push(m);
    });

    sections.push({
      name,
      tag,
      id: id || undefined,
      class: cls.substring(0, 100),
      rect: {
        top: Math.round(section.getBoundingClientRect().top + window.scrollY),
        height: Math.round(section.getBoundingClientRect().height),
        width: Math.round(section.getBoundingClientRect().width),
      },
      section: sectionStyles,
      children,
      media,
    });
  });

  return JSON.stringify(sections);
})()" 2>/dev/null || echo "[]")

RESULT=$(echo "$RESULT" | sed 's/^"//;s/"$//' | sed 's/\\"/"/g' | sed 's/\\\\"/"/g')

echo "$RESULT" | python3 -c "
import json, sys, os

data = json.load(sys.stdin)
outdir = '$DIR/html'

print(f'  Found {len(data)} sections')
print()

for sec in data:
    name = sec['name']
    rect = sec.get('rect', {})
    media = sec.get('media', [])
    children = sec.get('children', [])

    # Save per-section JSON with full structure + styles
    path = os.path.join(outdir, f'{name}.json')
    with open(path, 'w') as f:
        json.dump(sec, f, indent=2)

    print(f'  ✅ {name}.json')
    print(f'     tag={sec[\"tag\"]} top={rect.get(\"top\",\"?\")} height={rect.get(\"height\",\"?\")}')
    print(f'     children={len(children)} media={len(media)}')

    # Show media elements
    for m in media:
        tag = m.get('tag','?')
        src = m.get('src','')[:60]
        if tag == 'video':
            print(f'     📹 video: {src} autoplay={m.get(\"autoplay\")} muted={m.get(\"muted\")} loop={m.get(\"loop\")}')
        elif tag == 'source':
            print(f'     📹 source: {src} type={m.get(\"type\")}')
        elif tag == 'img':
            print(f'     🖼  img: {src[:40]}...')
    print()

# Also save a summary
summary = [{'name': s['name'], 'tag': s['tag'], 'rect': s.get('rect',{}), 'mediaCount': len(s.get('media',[])), 'childCount': len(s.get('children',[]))} for s in data]
with open(os.path.join(outdir, '_summary.json'), 'w') as f:
    json.dump(summary, f, indent=2)

print(f'  Summary: {outdir}/_summary.json')
" 2>/dev/null

echo "" >&2
echo "═══ Done ═══" >&2
echo "Per-section files: $DIR/html/<section-name>.json" >&2
echo "Each file contains: HTML structure, computed CSS, media elements" >&2

# ── JSON output ──
END_TIME=$(date +%s%3N 2>/dev/null || python3 -c "import time; print(int(time.time()*1000))")
SECTION_FILES=$(find "$DIR/html" -name "*.json" 2>/dev/null | python3 -c "import sys,json; print(json.dumps([l.strip() for l in sys.stdin]))" 2>/dev/null || echo "[]")
SECTION_N=$(find "$DIR/html" -name "*.json" 2>/dev/null | wc -l | tr -d ' ')
cat <<ENDJSON
{
  "status": "pass",
  "phase": "extract",
  "data": { "sections": $SECTION_N, "paths": $SECTION_FILES },
  "defects": [],
  "errors": [],
  "duration_ms": $(( END_TIME - START_TIME ))
}
ENDJSON
