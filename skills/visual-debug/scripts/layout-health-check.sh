#!/usr/bin/env bash
# layout-health-check.sh — Structural layout comparison before pixel comparison
# Catches: empty sections, collapsed text, zero-dimension elements, height ratios,
# desktop horizontal overflow
#
# Usage: bash layout-health-check.sh <session> <orig-url> <impl-url> [outdir]
# Exit: 0 = healthy, 1 = issues found

set -uo pipefail

if ! command -v node &>/dev/null; then
  echo "ERROR: node not installed"
  exit 2
fi

SESSION="${1:?Usage: layout-health-check.sh <session> <orig-url> <impl-url> [outdir]}"
ORIG_URL="${2:?}"
IMPL_URL="${3:?}"
OUTDIR="${4:-tmp/ref/layout-check}"
VIEW_W="${VIEW_W:-1440}"
VIEW_H="${VIEW_H:-900}"

cleanup_browsers() {
  agent-browser --session "${SESSION}-ref" close 2>/dev/null
  agent-browser --session "${SESSION}-impl" close 2>/dev/null
}
trap cleanup_browsers EXIT

mkdir -p "$OUTDIR"

EXTRACT_JS='(() => {
  const body = document.body;
  const totalHeight = document.documentElement.scrollHeight;
  const viewportHeight = window.innerHeight;
  const viewportWidth = window.innerWidth;
  const scrollWidth = Math.max(document.documentElement.scrollWidth, body ? body.scrollWidth : 0);
  const overflowPx = Math.max(0, scrollWidth - viewportWidth);

  const labelFor = (el) => {
    const tag = el.tagName ? el.tagName.toLowerCase() : "element";
    const id = el.id ? "#" + el.id : "";
    const rawClass = typeof el.className === "string" ? el.className : "";
    const classes = rawClass.trim().split(/\s+/).filter(Boolean).slice(0, 3).map(c => "." + c).join("");
    return (tag + id + classes).slice(0, 100);
  };
  const overflowOffenders = [...document.body.querySelectorAll("*")]
    .map(el => {
      const rect = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      return {
        selector: labelFor(el),
        right: Math.round(rect.right),
        left: Math.round(rect.left),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        position: cs.position,
        overflow: cs.overflow,
      };
    })
    .filter(item => item.width > 0 && item.height > 0 && item.right > viewportWidth + 4)
    .sort((a, b) => b.right - a.right)
    .slice(0, 8);

  // Get all direct children of main/body that are visible sections
  // Recurse through single-child wrapper divs (e.g., eBay has main > div.home > sections)
  let main = document.querySelector("main") || body;
  while (main.children.length === 1 && main.children[0].tagName === "DIV" && main.children[0].offsetHeight > main.offsetHeight * 0.8) {
    main = main.children[0];
  }
  const sections = [...main.children].filter(c => {
    return c.offsetHeight > 0 && !["SCRIPT","STYLE","LINK","META"].includes(c.tagName);
  });

  const sectionData = sections.map((s, i) => {
    const cn = typeof s.className === "string" ? s.className : "";
    const cs = getComputedStyle(s);

    // Check for zero-dimension children
    const children = [...s.querySelectorAll("h1,h2,h3,h4,h5,h6,p,a,button,img,svg,div,span")];
    const collapsed = children.filter(c => {
      const r = c.getBoundingClientRect();
      return c.textContent && c.textContent.trim().length > 0 && (r.width < 1 || r.height < 1);
    }).length;

    // Check for empty text
    const textEls = children.filter(c => ["H1","H2","H3","H4","H5","H6","P","SPAN","A","BUTTON"].includes(c.tagName));
    const emptyText = textEls.filter(c => {
      const r = c.getBoundingClientRect();
      return r.height > 0 && r.width > 0 && (!c.textContent || c.textContent.trim().length === 0);
    }).length;

    return {
      index: i,
      tag: s.tagName,
      class: cn.slice(0, 60),
      height: Math.round(s.offsetHeight),
      top: Math.round(s.getBoundingClientRect().top + window.scrollY),
      position: cs.position,
      display: cs.display,
      collapsedChildren: collapsed,
      emptyTextElements: emptyText,
      childCount: s.children.length,
    };
  });

  return JSON.stringify({
    totalHeight,
    viewportHeight,
    viewportWidth,
    scrollWidth,
    overflowPx,
    overflowOffenders,
    sectionCount: sections.length,
    sections: sectionData
  });
})()'

echo "═══ Layout Health Check ═══"
echo "Original: $ORIG_URL"
echo "Implementation: $IMPL_URL"
echo ""

# Extract from original
echo "▸ Analyzing original..."
agent-browser --session "${SESSION}-ref" close 2>/dev/null
agent-browser --session "${SESSION}-ref" open "$ORIG_URL" 2>/dev/null
agent-browser --session "${SESSION}-ref" set viewport $VIEW_W $VIEW_H 2>/dev/null
agent-browser --session "${SESSION}-ref" wait 5000 2>/dev/null
ORIG_RAW=$(agent-browser --session "${SESSION}-ref" eval "$EXTRACT_JS" 2>/dev/null)
# agent-browser wraps output in quotes and escapes inner quotes — unwrap
ORIG_DATA=$(echo "$ORIG_RAW" | sed 's/^"//;s/"$//' | sed 's/\\"/"/g' | sed 's/\\n/\n/g' | sed 's/\\\\/\\/g')

# Extract from implementation
echo "▸ Analyzing implementation..."
agent-browser --session "${SESSION}-impl" close 2>/dev/null
agent-browser --session "${SESSION}-impl" open "$IMPL_URL" 2>/dev/null
agent-browser --session "${SESSION}-impl" set viewport $VIEW_W $VIEW_H 2>/dev/null
agent-browser --session "${SESSION}-impl" wait 5000 2>/dev/null
IMPL_RAW=$(agent-browser --session "${SESSION}-impl" eval "$EXTRACT_JS" 2>/dev/null)
IMPL_DATA=$(echo "$IMPL_RAW" | sed 's/^"//;s/"$//' | sed 's/\\"/"/g' | sed 's/\\n/\n/g' | sed 's/\\\\/\\/g')

# Save raw data
echo "$ORIG_DATA" > "$OUTDIR/orig-layout.json"
echo "$IMPL_DATA" > "$OUTDIR/impl-layout.json"

# Parse and compare using node
node -e "
const orig = JSON.parse(process.argv[1]);
const impl = JSON.parse(process.argv[2]);

let issues = 0;

// 1. Total page height ratio
const ratio = impl.totalHeight / orig.totalHeight;
console.log('');
console.log('── Page Height ──');
console.log('  Original: ' + orig.totalHeight + 'px');
console.log('  Implementation: ' + impl.totalHeight + 'px');
console.log('  Ratio: ' + ratio.toFixed(2) + 'x');
if (ratio > 1.3 || ratio < 0.7) {
  console.log('  ⛔ HEIGHT MISMATCH: implementation is ' + (ratio > 1 ? 'taller' : 'shorter') + ' by ' + Math.round(Math.abs(ratio - 1) * 100) + '%');
  issues++;
} else if (ratio > 1.1 || ratio < 0.9) {
  console.log('  ⚠️  Height differs by ' + Math.round(Math.abs(ratio - 1) * 100) + '%');
} else {
  console.log('  ✅ Within 10% tolerance');
}

// 2. Horizontal overflow
const origOverflow = Math.max(0, (orig.scrollWidth || orig.viewportWidth || 0) - (orig.viewportWidth || 0));
const implOverflow = Math.max(0, (impl.scrollWidth || impl.viewportWidth || 0) - (impl.viewportWidth || 0));
const overflowTolerance = Math.max(4, origOverflow + 4);
console.log('');
console.log('── Horizontal Overflow ──');
console.log('  Original: viewport ' + orig.viewportWidth + 'px, scrollWidth ' + orig.scrollWidth + 'px, overflow ' + origOverflow + 'px');
console.log('  Implementation: viewport ' + impl.viewportWidth + 'px, scrollWidth ' + impl.scrollWidth + 'px, overflow ' + implOverflow + 'px');
if (implOverflow > overflowTolerance) {
  console.log('  ⛔ HORIZONTAL OVERFLOW: implementation exceeds the reference tolerance by ' + (implOverflow - overflowTolerance) + 'px');
  console.log('  Typical cause: fixed-width absolute layers, offscreen rails, marquees, or mosaics not clipped in a section-local wrapper.');
  const offenders = impl.overflowOffenders || [];
  if (offenders.length > 0) {
    console.log('  Top right-edge offenders:');
    offenders.forEach(item => {
      console.log('    - ' + item.selector + ' right=' + item.right + 'px left=' + item.left + 'px width=' + item.width + 'px position=' + item.position + ' overflow=' + item.overflow);
    });
  }
  issues++;
} else {
  console.log('  ✅ Within reference overflow tolerance');
}

// 3. Section count
console.log('');
console.log('── Section Count ──');
console.log('  Original: ' + orig.sectionCount);
console.log('  Implementation: ' + impl.sectionCount);
if (orig.sectionCount !== impl.sectionCount) {
  console.log('  ⚠️  Section count differs');
}

// 4. Per-section comparison
console.log('');
console.log('── Section Heights ──');
console.log('| # | Orig Height | Impl Height | Ratio | Status |');
console.log('|---|------------|-------------|-------|--------|');

const maxSections = Math.max(orig.sections.length, impl.sections.length);
for (let i = 0; i < maxSections; i++) {
  const o = orig.sections[i];
  const m = impl.sections[i];
  if (!o && m) {
    console.log('| ' + i + ' | — | ' + m.height + 'px | — | ⚠️ EXTRA in impl |');
    continue;
  }
  if (o && !m) {
    console.log('| ' + i + ' | ' + o.height + 'px | — | — | ⛔ MISSING in impl |');
    issues++;
    continue;
  }
  const sRatio = m.height / o.height;
  const absDiff = Math.abs(m.height - o.height);
  let status = '✅';
  if (sRatio > 2 || sRatio < 0.3) { status = '⛔ ' + sRatio.toFixed(2) + 'x'; issues++; }
  else if (sRatio > 1.3 || sRatio < 0.7) { status = '⛔ ' + sRatio.toFixed(2) + 'x (' + absDiff + 'px)'; issues++; }
  else if (sRatio > 1.15 || sRatio < 0.85 || absDiff > 80) { status = '⚠️ ' + sRatio.toFixed(2) + 'x (' + absDiff + 'px)'; }
  console.log('| ' + i + ' | ' + o.height + 'px | ' + m.height + 'px | ' + sRatio.toFixed(2) + ' | ' + status + ' |');
}

// 5. Collapsed elements
console.log('');
console.log('── Collapsed Elements ──');
let totalCollapsed = 0;
impl.sections.forEach((s, i) => {
  if (s.collapsedChildren > 0) {
    console.log('  ⛔ Section ' + i + ' (' + s.class.slice(0,30) + '): ' + s.collapsedChildren + ' collapsed children');
    totalCollapsed += s.collapsedChildren;
    issues++;
  }
});
if (totalCollapsed === 0) console.log('  ✅ No collapsed elements');

// 6. Empty text elements
console.log('');
console.log('── Empty Text Elements ──');
let totalEmpty = 0;
impl.sections.forEach((s, i) => {
  if (s.emptyTextElements > 0) {
    console.log('  ⚠️  Section ' + i + ': ' + s.emptyTextElements + ' empty text elements');
    totalEmpty += s.emptyTextElements;
  }
});
if (totalEmpty === 0) console.log('  ✅ No empty text elements');

// 7. Sections taller than 3x viewport (potential blank space)
console.log('');
console.log('── Oversized Sections ──');
impl.sections.forEach((s, i) => {
  if (s.height > impl.viewportHeight * 3 && s.position !== 'fixed') {
    console.log('  ⚠️  Section ' + i + ' (' + s.class.slice(0,30) + '): ' + s.height + 'px (' + (s.height / impl.viewportHeight).toFixed(1) + ' viewports)');
  }
});

console.log('');
if (issues > 0) {
  console.log('⛔ LAYOUT CHECK FAILED: ' + issues + ' critical issue(s)');
  process.exit(1);
} else {
  console.log('✅ Layout check passed');
}
" "$ORIG_DATA" "$IMPL_DATA"
