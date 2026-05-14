# Section Audit — Step 6c

> This step maps every visible element to its owning section via DOM hierarchy. It catches the #1 source of structural errors: placing elements in the wrong component because they **looked** like they belonged to an adjacent section visually.

## Why this step exists

**Problem:** A text block ("Settling is easy") appears visually between two sections. The LLM assumes it's in the intro section above. But DOM inspection reveals it's actually a child of the cases section below — its `parentElement` chain goes `<p> → .tagline → .cases_inner → section.cases`.

**Without this step:** Elements get placed in the wrong component. The wrong component is taller, the right one is shorter, and the page height drifts. This is the root cause of most layout verification failures.

**Rule:** Never decide section ownership by visual position. Always trace `parentElement` chain to the nearest `<section>`, `<footer>`, `<header>`, or `<main>`.

## Prerequisites

- `structure.json` from Step 2
- `styles.json` from Step 3
- `extracted.json` from Step 6b (assembled)

## Stage 1: Enumerate all top-level semantic containers

List every `<section>`, `<footer>`, `<header>`, `<nav>`, `<main>`, `<aside>` that is a direct child of the page wrapper (or `<body>`).

```bash
agent-browser --session <s> eval "
(() => {
  // Framework-agnostic: works with any site (Webflow, React, Vue, Astro, plain HTML)
  const semanticTags = new Set(['section', 'footer', 'header', 'nav', 'aside', 'main', 'article']);
  const semanticRoles = new Set(['region', 'main', 'banner', 'contentinfo', 'navigation']);
  const containers = [];

  function collectSections(parent) {
    Array.from(parent.children).forEach(el => {
      const tag = el.tagName.toLowerCase();
      const h = el.getBoundingClientRect().height;
      const role = el.getAttribute('role');
      if ((semanticTags.has(tag) || semanticRoles.has(role)) && h > 50) {
        containers.push(el);
      } else if (tag === 'div' && h > 100) {
        const hasSemanticChildren = Array.from(el.children).some(c =>
          semanticTags.has(c.tagName.toLowerCase()) || semanticRoles.has(c.getAttribute('role') || '')
        );
        if (hasSemanticChildren) {
          collectSections(el);
        } else if (h > Math.min(window.innerHeight * 0.25, 400)) {
          containers.push(el);
        }
      }
    });
  }

  // Start from main or body, but recurse through single-child wrapper divs
  let root = document.querySelector('main, [role=main]') || document.body;
  while (root.children.length === 1 && root.children[0].tagName === 'DIV' && root.children[0].offsetHeight > root.offsetHeight * 0.8) {
    root = root.children[0];
  }
  collectSections(root);

  const unique = containers.filter((el, i) => !containers.some((other, j) => j !== i && other.contains(el)));
  unique.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);

  const isFooter = (el) => el.tagName === 'FOOTER' || el.getAttribute('role') === 'contentinfo' ||
    /footer/i.test(el.id || '') || /footer/i.test(el.className?.toString() || '');
  const isHeader = (el) => el.tagName === 'HEADER' || el.getAttribute('role') === 'banner' ||
    /header/i.test(el.id || '') || /header/i.test(el.className?.toString() || '');

  return JSON.stringify({
    totalCount: unique.length,
    hasFooter: unique.some(isFooter),
    hasHeader: unique.some(isHeader),
    sections: unique.map((el, i) => ({
      index: i,
      tag: el.tagName.toLowerCase(),
      className: (el.className?.toString() || '').slice(0, 80),
      id: el.id || null,
      role: el.getAttribute('role') || null,
      height: Math.round(el.getBoundingClientRect().height),
      top: Math.round(el.getBoundingClientRect().top + window.scrollY),
      childCount: el.children.length,
      theme: el.className?.includes('dark') ? 'dark' : el.className?.includes('light') ? 'light' : null,
      textPreview: el.textContent?.trim().slice(0, 60),
    })),
  }, null, 2);
})()
"
```

**Save output to** `tmp/ref/<component>/section-map.json`

**Validation:** The number of entries here is the **ground truth** for how many components to generate. If this shows 6 sections but you only planned 5 components, you are missing one.

## Stage 2: Element ownership — trace parentElement chain

For every significant text element on the page, verify which section it belongs to. This catches elements that visually appear between sections but belong to one or the other.

```bash
agent-browser --session <s> eval "
(() => {
  // Find the semantic containers — recurse through single-child wrapper divs
  let wrapper = document.querySelector('main, [role=main]') || document.body;
  while (wrapper.children.length === 1 && wrapper.children[0].tagName === 'DIV' && wrapper.children[0].offsetHeight > wrapper.offsetHeight * 0.8) {
    wrapper = wrapper.children[0];
  }
  const sections = Array.from(wrapper.querySelectorAll(':scope > section, :scope > footer, :scope > header'));

  // Collect all visible text elements
  const textEls = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6, p, span, a, button, li'))
    .filter(el => {
      const r = el.getBoundingClientRect();
      return el.textContent?.trim().length > 2 && r.height > 0 && r.width > 0;
    });

  // For each text element, find its owning section
  const ownership = textEls.slice(0, 100).map(el => {
    let owner = null;
    let current = el;
    while (current && current !== document.body) {
      if (sections.includes(current)) {
        owner = current;
        break;
      }
      current = current.parentElement;
    }

    return {
      text: el.textContent?.trim().slice(0, 50),
      tag: el.tagName.toLowerCase(),
      top: Math.round(el.getBoundingClientRect().top + window.scrollY),
      ownerTag: owner?.tagName.toLowerCase() || 'ORPHAN',
      ownerClass: (owner?.className?.toString() || '').slice(0, 60),
      ownerIndex: owner ? sections.indexOf(owner) : -1,
    };
  });

  // Group by section
  const grouped = {};
  ownership.forEach(o => {
    const key = o.ownerIndex >= 0 ? \`\${o.ownerIndex}: \${o.ownerTag}.\${o.ownerClass.split(' ')[0]}\` : 'ORPHAN';
    if (!grouped[key]) grouped[key] = [];
    grouped[key].push({ text: o.text, top: o.top });
  });

  return JSON.stringify({
    totalElements: ownership.length,
    orphans: ownership.filter(o => o.ownerIndex === -1).length,
    sections: grouped,
  }, null, 2);
})()
"
```

**Save output to** `tmp/ref/<component>/element-roles.json`

**Red flags to check:**
- Any element in `ORPHAN` group → it's outside all sections, probably absolutely positioned or in a portal. Decide: is it a floating component (dock, cursor follower) or misplaced?
- An element with `top: 1200` belonging to section 1 (which ends at `top: 1100`) → the element is inside section 1 DOM but renders below it visually. This is normal for overflow content or absolutely positioned children.
- "Settling is easy" appearing in section index 2 (cases) but visually between section 1 (intro) and section 2 → **this is the DOM truth. It belongs to cases, not intro.**

## Stage 3: Section height cross-check

Compare actual section heights from the DOM with the reference screenshots.

```bash
agent-browser --session <s> eval "
(() => {
  let wrapper = document.querySelector('main, [role=main]') || document.body;
  while (wrapper.children.length === 1 && wrapper.children[0].tagName === 'DIV' && wrapper.children[0].offsetHeight > wrapper.offsetHeight * 0.8) {
    wrapper = wrapper.children[0];
  }
  const sections = Array.from(wrapper.querySelectorAll(':scope > section, :scope > footer, :scope > header'));

  const heights = sections.map((s, i) => ({
    index: i,
    tag: s.tagName.toLowerCase(),
    className: (s.className?.toString() || '').split(' ')[0],
    height: Math.round(s.getBoundingClientRect().height),
    top: Math.round(s.getBoundingClientRect().top + window.scrollY),
    padding: getComputedStyle(s).padding,
    directChildCount: s.children.length,
  }));

  return JSON.stringify({
    pageHeight: document.body.scrollHeight,
    viewportHeight: window.innerHeight,
    sectionCount: heights.length,
    sections: heights,
  }, null, 2);
})()
"
```

**Save output to** `tmp/ref/<component>/element-groups.json`

## Stage 4: Layout decision log

For each section, document the key layout decisions from the extracted data:

```bash
agent-browser --session <s> eval "
(() => {
  let wrapper = document.querySelector('main, [role=main]') || document.body;
  while (wrapper.children.length === 1 && wrapper.children[0].tagName === 'DIV' && wrapper.children[0].offsetHeight > wrapper.offsetHeight * 0.8) {
    wrapper = wrapper.children[0];
  }
  const sections = Array.from(wrapper.querySelectorAll(':scope > section, :scope > footer, :scope > header'));

  return JSON.stringify(sections.map((section, i) => {
    const s = getComputedStyle(section);
    const inner = section.querySelector('[class*=wrapper], [class*=inner], [class*=container], [class*=content]');
    const innerS = inner ? getComputedStyle(inner) : null;

    return {
      index: i,
      tag: section.tagName.toLowerCase(),
      className: (section.className?.toString() || '').slice(0, 60),
      layout: {
        display: s.display,
        flexDirection: s.flexDirection,
        gap: s.gap,
        padding: s.padding,
        backgroundColor: s.backgroundColor,
        minHeight: s.minHeight,
        overflow: s.overflow,
      },
      innerWrapper: inner ? {
        className: (inner.className?.toString() || '').slice(0, 60),
        display: innerS?.display,
        flexDirection: innerS?.flexDirection,
        gap: innerS?.gap,
        padding: innerS?.padding,
        height: Math.round(inner.getBoundingClientRect().height),
      } : null,
      directChildren: Array.from(section.children).slice(0, 10).map(c => ({
        tag: c.tagName.toLowerCase(),
        className: (c.className?.toString() || '').slice(0, 40),
        height: Math.round(c.getBoundingClientRect().height),
        text: c.textContent?.trim().slice(0, 40) || '',
      })),
    };
  }), null, 2);
})()
"
```

**Save output to** `tmp/ref/<component>/layout-decisions.json`

## Stage 5: Assemble component-map.json

Using the data from Stages 1–4, create the final component map:

```json
{
  "sections": [
    {
      "index": 0,
      "componentName": "Hero",
      "sourceTag": "section",
      "sourceClass": "section theme-dark",
      "height": 900,
      "top": 0,
      "theme": "dark",
      "elements": ["hero title", "hero CTA", "unicorn background"],
      "transitions": ["preloader", "heroEffect"],
      "notes": ""
    },
    {
      "index": 1,
      "componentName": "IntroSection",
      "sourceTag": "section",
      "sourceClass": "section theme-base",
      "height": 523,
      "top": 900,
      "theme": "base",
      "elements": ["3x text paragraphs with SplitText reveal", "Leave yours text"],
      "transitions": ["splitTextReveal"],
      "notes": "No bottom nav — 'The Cavalry/Works/August' bar belongs to CasesSection (index 2)"
    }
  ],
  "totalPageHeight": 5126,
  "sectionCount": 6
}
```

**Rules for component-map.json:**
1. Every entry in `section-map.json` (Stage 1) must appear as a `sections` entry. No exceptions.
2. `sectionCount` must match `section-map.json` length.
3. The `elements` array must only list elements verified in `element-roles.json` (Stage 2) as belonging to this section.
4. Cross-reference: if Stage 2 shows text "X" belongs to section index 3, it MUST NOT appear in section index 2's elements list — even if it visually appears between sections 2 and 3.

**Save output to** `tmp/ref/<component>/component-map.json`

## Stage 6: Final verification

Before proceeding to generation, verify:

```
□ section-map.json has N entries
□ component-map.json has N entries (same N)
□ No ORPHAN elements in element-roles.json (or they're documented as floating components)
□ Sum of section heights ≈ pageHeight (within 5%)
□ Every entry in transition-spec.json maps to exactly one section in component-map.json
□ Footer exists if section-map.json shows a <footer> tag
```

Run the gate:
```bash
uv run python -m ui_clone.gate tmp/ref/<component> pre-generate
```

## Stage 7: Post-implementation height verification

**When:** After generating components and confirming they render, run this stage to catch height drift before visual diff.

This step re-measures the implementation's section heights and compares them with the original measurements from Stage 3. This catches:
- Hardcoded heights that don't match the original (e.g., `height: 900px` when original is `800px`)
- Grid ratio mismatches that change section height (e.g., `330px + 1fr` vs original `1fr 2fr`)
- Missing or extra margin/padding between sections
- Font metric differences that accumulate across sections

```bash
# 1. Open both URLs and measure
agent-browser --session ref open "<ORIGINAL_URL>" 2>/dev/null
agent-browser --session ref set viewport 1440 900 2>/dev/null
agent-browser --session ref wait 3000 2>/dev/null

agent-browser --session impl open "<IMPL_URL>" 2>/dev/null
agent-browser --session impl set viewport 1440 900 2>/dev/null
agent-browser --session impl wait 3000 2>/dev/null

# 2. Extract from both — use main > * and recurse into wrapper divs
MEASURE_JS='(() => {
  const main = document.querySelector("main") || document.body;
  // Recurse: if main has a single child div wrapper, use its children instead
  let root = main;
  while (root.children.length === 1 && root.children[0].tagName === "DIV" && root.children[0].offsetHeight > root.offsetHeight * 0.8) {
    root = root.children[0];
  }
  const kids = Array.from(root.children).filter(c => c.offsetHeight > 0 && !["SCRIPT","STYLE","LINK","META"].includes(c.tagName));
  return JSON.stringify({
    totalHeight: document.documentElement.scrollHeight,
    rootTag: root.tagName,
    rootClass: (root.className || "").toString().slice(0,60),
    sections: kids.map((el, i) => {
      const cs = getComputedStyle(el);
      return {
        i, tag: el.tagName, cls: (el.className || "").toString().slice(0,60),
        h: el.offsetHeight,
        mt: cs.marginTop, mb: cs.marginBottom,
        pt: cs.paddingTop, pb: cs.paddingBottom,
        text: el.textContent.trim().slice(0,50)
      };
    })
  });
})()'

REF_DATA=$(agent-browser --session ref eval "$MEASURE_JS" 2>/dev/null)
IMPL_DATA=$(agent-browser --session impl eval "$MEASURE_JS" 2>/dev/null)

# 3. Compare — pipe to file for token efficiency
echo "$REF_DATA" > tmp/ref/post-impl-ref.json
echo "$IMPL_DATA" > tmp/ref/post-impl-clone.json
```

**Thresholds (per-section):**
- `|ratio - 1| > 0.15` (>15% off) OR `|absDiff| > 80px` → **⚠️ WARNING** — investigate
- `|ratio - 1| > 0.30` (>30% off) → **⛔ FAIL** — must fix before proceeding

**Thresholds (total page height):**
- `|ratio - 1| > 0.10` (>10% off) → **⚠️ WARNING**
- `|ratio - 1| > 0.30` (>30% off) → **⛔ FAIL**

**Action on warning:** Read the per-section table. For each section with >15% or >80px difference:
1. Measure the original section's key dimensions (grid template, padding, hardcoded heights)
2. Compare with implementation's values
3. Fix the root cause (don't adjust padding to compensate)

**Do not proceed to pixel-diff (visual-debug) until all sections are within threshold.**

## Common mistakes this step prevents

| Mistake | How this step catches it |
|---|---|
| Text block placed in wrong section | Stage 2 traces parentElement → reveals actual owner |
| Footer section omitted | Stage 1 lists ALL semantic tags including `<footer>` |
| Section height mismatch | Stage 3 records exact heights; implementation must match |
| Element appears between sections | Stage 2 ownership > visual position. DOM is truth. |
| Invisible elements missed (collapsed nav) | Stage 1 counts children; hidden-elements.json from Step 2 supplements |
