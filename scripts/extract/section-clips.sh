#!/usr/bin/env bash
# section-clips.sh — Capture per-section and per-element clip screenshots
# Section name keyword → name mapping: shared with extract-assets.sh and
# extract-section-html.sh. Adding a new keyword? Update all 3.
# Usage: bash section-clips.sh <session> <output-dir> <side>
#   session   = agent-browser session name
#   output-dir = e.g. tmp/ref/mysite
#   side       = "ref" or "impl"
#
# Produces:
#   <output-dir>/clips/<side>/sections/   — one screenshot per section (viewport-sized)
#   <output-dir>/clips/<side>/elements/   — one screenshot per key UI element (cropped)
#   <output-dir>/clips/<side>/sections.json — section metadata (name, y, height)
#
# This script uses agent-browser to:
#   1. Detect all top-level sections (header, hero, content sections, footer)
#   2. Capture each section as an isolated screenshot (resize viewport to section height)
#   3. Detect key UI elements within each section (buttons, cards, headings, nav)
#   4. Capture each element as a tight crop
#
# The clips serve as:
#   - Ground truth for code generation (see exactly what each section looks like)
#   - Comparison targets for validation (ref clip vs impl clip, per-element RMSE)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../lib/time-ms.sh"

START_TIME=$(ui_clone_now_ms)

SESSION="${1:?Usage: section-clips.sh <session> <output-dir> <side>}"
trap 'agent-browser --session "$SESSION" close 2>/dev/null || true' EXIT
DIR="${2:?Usage: section-clips.sh <session> <output-dir> <side>}"
SIDE="${3:?Usage: section-clips.sh <session> <output-dir> <side>}"
VIEW_W="${VIEW_W:-1440}"
VIEW_H="${VIEW_H:-900}"

CLIP_DIR="$DIR/clips/$SIDE"
SECTION_DIR="$CLIP_DIR/sections"
ELEMENT_DIR="$CLIP_DIR/elements"

mkdir -p "$SECTION_DIR" "$ELEMENT_DIR"

echo "═══ Section Clips: $SIDE ═══"
echo "Session: $SESSION"
echo "Output:  $CLIP_DIR"
echo ""

# ── Step 1: Detect sections ──
echo "Detecting sections..."
SECTIONS_JSON=$(agent-browser --session "$SESSION" eval "(() => {
  // Find the scroll container
  const scrollEl = document.documentElement;

  // Collect all major sections
  const candidates = [
    ...document.querySelectorAll('header, nav:not(header nav), section, footer, main > div, [class*=hero], [class*=banner], [class*=showcase], [class*=product], [class*=feature], [class*=pricing], [class*=testimonial], [class*=gallery], [class*=faq], [class*=newsletter], [class*=cta], [class*=contact], [class*=footer]')
  ];

  // Deduplicate: if a child section is inside a parent section, keep both but mark parent
  const sections = [];
  const seen = new Set();

  candidates.forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.height < 50) return;

    // Skip if this element is fully contained within a smaller already-seen element
    const key = Math.round(r.top + scrollEl.scrollTop) + ':' + Math.round(r.height);
    if (seen.has(key)) return;
    seen.add(key);

    const cn = typeof el.className === 'string' ? el.className : '';
    const tag = el.tagName.toLowerCase();

    // Generate a descriptive name
    let name = el.id || '';
    if (!name) {
      const combined = cn.toLowerCase();
      const kwMap = [['hero','hero'],['banner','banner'],['showcase','showcase'],['product','product'],['text-scroll','text-scroll'],['pricing','pricing'],['testimonial','testimonials'],['feature','features'],['discover','features'],['about','about'],['team','team'],['gallery','gallery'],['portfolio','portfolio'],['contact','contact'],['cta','cta'],['faq','faq'],['blog','blog'],['newsletter','newsletter'],['subscribe','subscribe'],['footer-section','newsletter'],['partner','partners'],['client','clients'],['stats','stats']];
      let matched = false;
      for (const [kw, n] of kwMap) {
        if (combined.includes(kw)) { name = n; matched = true; break; }
      }
      if (!matched) {
        if (tag === 'header') name = 'header';
        else if (tag === 'footer') name = 'footer';
        else if (tag === 'nav') name = 'nav';
        else name = tag + '-' + (sections.length + 1);
      }
    }

    sections.push({
      name: name.replace(/[^a-zA-Z0-9_-]/g, '-').substring(0, 40),
      tag,
      class: cn.substring(0, 80),
      y: Math.round(r.top + scrollEl.scrollTop),
      height: Math.round(r.height),
      width: Math.round(r.width)
    });
  });

  // Sort by y position, deduplicate overlapping sections
  sections.sort((a, b) => a.y - b.y);

  return JSON.stringify(sections);
})()" 2>/dev/null || echo "[]")

# Clean the output (agent-browser may wrap in quotes)
SECTIONS_JSON=$(echo "$SECTIONS_JSON" | sed 's/^"//;s/"$//' | sed 's/\\"/"/g')

echo "$SECTIONS_JSON" | python3 -m json.tool > "$CLIP_DIR/sections.json" 2>/dev/null || echo "$SECTIONS_JSON" > "$CLIP_DIR/sections.json"

SECTION_COUNT=$(echo "$SECTIONS_JSON" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
echo "Found $SECTION_COUNT sections"

# ── Step 2: Capture each section ──
echo ""
echo "Capturing section screenshots..."

echo "$SECTIONS_JSON" | python3 -c "
import json, sys, subprocess

sections = json.load(sys.stdin)
session = '$SESSION'
section_dir = '$SECTION_DIR'

for i, sec in enumerate(sections):
    name = sec['name']
    y = sec['y']
    h = min(sec['height'], 3000)  # Cap at 3000px to avoid huge screenshots

    # Set viewport to section height
    subprocess.run(['agent-browser', '--session', session, 'set', 'viewport', '$VIEW_W', str(h)],
                   capture_output=True, timeout=10)

    # Scroll to section
    subprocess.run(['agent-browser', '--session', session, 'eval',
                   f'(() => {{ window.scrollTo(0, {y}); }})()'],
                   capture_output=True, timeout=10)

    # Wait for render
    subprocess.run(['agent-browser', '--session', session, 'wait', '600'],
                   capture_output=True, timeout=10)

    # Screenshot
    out_path = f'{section_dir}/{i:02d}-{name}.png'
    result = subprocess.run(['agent-browser', '--session', session, 'screenshot', out_path],
                           capture_output=True, timeout=15)

    if result.returncode == 0:
        print(f'  ✅ {i:02d}-{name}.png ({h}px tall)')
    else:
        print(f'  ❌ {i:02d}-{name}.png FAILED')

# Restore viewport
subprocess.run(['agent-browser', '--session', session, 'set', 'viewport', '$VIEW_W', '$VIEW_H'],
               capture_output=True, timeout=10)
" 2>/dev/null

# ── Step 3: Detect and capture key elements ──
echo ""
echo "Detecting key UI elements..."

agent-browser --session "$SESSION" set viewport $VIEW_W $VIEW_H >/dev/null 2>&1
agent-browser --session "$SESSION" eval "(() => { window.scrollTo(0, 0); })()" >/dev/null 2>&1

ELEMENTS_JSON=$(agent-browser --session "$SESSION" eval "(() => {
  const scrollY = window.scrollY || document.documentElement.scrollTop;
  const elements = [];

  // Key element selectors to capture
  const selectors = [
    'header', 'nav:first-of-type',
    'h1', 'h2', 'h3',
    'button', 'a[class*=button], a[class*=btn], a[class*=cta]',
    '[class*=card]', '[class*=product]',
    'img[class*=logo]', 'svg[class*=logo]',
    'input', 'form',
    '[class*=accordion]', '[class*=faq-item]', 'details',
  ];

  const seen = new Set();

  selectors.forEach(sel => {
    document.querySelectorAll(sel).forEach(el => {
      const r = el.getBoundingClientRect();
      if (r.width < 30 || r.height < 20) return;
      if (r.height > 1000) return; // Skip full sections

      const key = Math.round(r.left) + ':' + Math.round(r.top + scrollY) + ':' + Math.round(r.width);
      if (seen.has(key)) return;
      seen.add(key);

      const cn = typeof el.className === 'string' ? el.className : '';
      const tag = el.tagName.toLowerCase();
      const id = el.id || '';
      const name = id || cn.trim().split(/\\s+/)[0] || tag;

      elements.push({
        name: name.replace(/[^a-zA-Z0-9_-]/g, '-').substring(0, 40),
        tag, selector: id ? '#' + id : tag + (cn.trim().split(/\\s+/)[0] ? '.' + cn.trim().split(/\\s+/)[0] : ''),
        x: Math.round(r.left), y: Math.round(r.top + scrollY),
        width: Math.round(r.width), height: Math.round(r.height)
      });
    });
  });

  // Sort by y, take first 30
  elements.sort((a, b) => a.y - b.y);
  return JSON.stringify(elements.slice(0, 30));
})()" 2>/dev/null || echo "[]")

ELEMENTS_JSON=$(echo "$ELEMENTS_JSON" | sed 's/^"//;s/"$//' | sed 's/\\"/"/g')
echo "$ELEMENTS_JSON" | python3 -m json.tool > "$CLIP_DIR/elements.json" 2>/dev/null || echo "$ELEMENTS_JSON" > "$CLIP_DIR/elements.json"

ELEM_COUNT=$(echo "$ELEMENTS_JSON" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
echo "Found $ELEM_COUNT key elements"

# Capture element clips
echo ""
echo "Capturing element clips..."

echo "$ELEMENTS_JSON" | python3 -c "
import json, sys, subprocess

elements = json.load(sys.stdin)
session = '$SESSION'
element_dir = '$ELEMENT_DIR'

for i, elem in enumerate(elements):
    name = elem['name']
    x, y, w, h = elem['x'], elem['y'], elem['width'], elem['height']

    # Scroll element into view
    subprocess.run(['agent-browser', '--session', session, 'eval',
                   f'(() => {{ window.scrollTo(0, {max(0, y - 100)}); }})()'],
                   capture_output=True, timeout=10)
    subprocess.run(['agent-browser', '--session', session, 'wait', '400'],
                   capture_output=True, timeout=10)

    # Take clip screenshot
    out_path = f'{element_dir}/{i:02d}-{name}.png'

    # Recalculate position after scroll
    clip_y = y - max(0, y - 100)
    clip_arg = f'{x},{clip_y},{w},{h}'

    result = subprocess.run(['agent-browser', '--session', session, 'screenshot',
                            '--clip', clip_arg, out_path],
                           capture_output=True, timeout=15)

    if result.returncode == 0:
        print(f'  ✅ {i:02d}-{name}.png ({w}x{h})')
    else:
        # Fallback: full viewport screenshot
        result2 = subprocess.run(['agent-browser', '--session', session, 'screenshot', out_path],
                                capture_output=True, timeout=15)
        if result2.returncode == 0:
            print(f'  ⚠️  {i:02d}-{name}.png (full viewport fallback)')
        else:
            print(f'  ❌ {i:02d}-{name}.png FAILED')
" 2>/dev/null

echo "" >&2
echo "═══ Done: $SECTION_COUNT sections, $ELEM_COUNT elements ═══" >&2
echo "Sections: $SECTION_DIR/" >&2
echo "Elements: $ELEMENT_DIR/" >&2
echo "Metadata: $CLIP_DIR/sections.json, $CLIP_DIR/elements.json" >&2

# ── JSON output ──
END_TIME=$(ui_clone_now_ms)
CLIPS=$(find "$SECTION_DIR" -name "*.png" 2>/dev/null | python3 -c "import sys,json; print(json.dumps([l.strip() for l in sys.stdin]))" 2>/dev/null || echo "[]")
cat <<ENDJSON
{
  "status": "pass",
  "phase": "capture",
  "data": { "clips": $CLIPS, "sections": ${SECTION_COUNT:-0}, "elements": ${ELEM_COUNT:-0} },
  "defects": [],
  "errors": [],
  "duration_ms": $(( END_TIME - START_TIME ))
}
ENDJSON
