#!/usr/bin/env bash
# extract-section-map.sh — produce section-map.json from a live page.
#
# Closes the Phase 2 contract gap that dom-scaffold.sh has assumed forever:
# the scaffold consumes structure.json + styles.json + section-map.json, but
# only structure.json was produced by extract-dom.sh. section-map.json was
# documented in skills/ui-reverse-engineering/dom-extraction.md as a manual
# agent-browser eval the agent had to run by hand. Fresh-only runs hit
# "missing section-map.json" and aborted; sub-workspace runs only worked
# because they reused stale artifacts from older scratch dirs.
#
# The agent-browser JS below is the same enumeration logic in dom-extraction.md
# (with the recursion fix for div-only layouts). Producing it from a real
# script means fresh-only runs no longer depend on an agent reading the prose
# and pasting the JS by hand.
#
# Usage:
#   extract-section-map.sh <ref-dir> <session-name>

set -euo pipefail

if [ $# -lt 2 ]; then
  echo "usage: extract-section-map.sh <ref-dir> <session-name>" >&2
  exit 2
fi

REF_DIR="$1"
SESSION="$2"

if [ ! -d "$REF_DIR" ]; then
  echo "extract-section-map: ref-dir not found: $REF_DIR" >&2
  exit 2
fi
if ! command -v agent-browser >/dev/null 2>&1; then
  echo "extract-section-map: agent-browser not found on PATH" >&2
  exit 3
fi

OUT_PATH="$REF_DIR/section-map.json"

EVAL_JS=$(cat <<'JSEOF'
(() => {
  // Framework-agnostic: works with Webflow, React, Vue, Astro, plain HTML.
  // Mirrors skills/ui-reverse-engineering/dom-extraction.md so the prose
  // doc and the producer can never drift silently — update both together.
  const semanticTags = new Set(['section', 'footer', 'header', 'nav', 'aside', 'main', 'article']);
  const semanticRoles = new Set(['region', 'main', 'banner', 'contentinfo', 'navigation']);
  const containers = [];

  // Universality fix: a prior version stopped recursing when a div had
  // no semantic children. Sites where `<main>` contains nothing but
  // `<div>`s with opaque hashed classes (no `<section>` / `<article>` /
  // role="region") produced only 2 sections (main + footer) for a
  // 21k-tall page. Two recursion triggers added:
  //   1. Semantic containers taller than 2x viewport are decomposed
  //      instead of being added wholesale — they almost certainly wrap
  //      the page-scrolled content.
  //   2. Div containers with >= 2 large div children (each >= 50% of
  //      viewport height) recurse even without semantic children —
  //      catches the opaque-hashed-class sibling pattern.
  const VIEWPORT_H = window.innerHeight || 800;
  const LARGE_DIV_H = Math.min(VIEWPORT_H * 0.5, 600);
  function collectSections(parent, depth) {
    if (depth > 4) return;  // safety bound
    Array.from(parent.children).forEach(el => {
      const tag = el.tagName.toLowerCase();
      if (['script', 'style', 'noscript', 'template'].includes(tag)) return;
      const h = el.getBoundingClientRect().height;
      const role = el.getAttribute('role');
      if ((semanticTags.has(tag) || semanticRoles.has(role)) && h > 50) {
        if (h > VIEWPORT_H * 2) {
          collectSections(el, depth + 1);
        } else {
          containers.push(el);
        }
      } else if (tag === 'div' && h > 100) {
        const childrenArr = Array.from(el.children);
        const hasSemanticChildren = childrenArr.some(c =>
          semanticTags.has(c.tagName.toLowerCase()) ||
          semanticRoles.has(c.getAttribute('role') || '')
        );
        const bigDivChildren = childrenArr.filter(c =>
          c.tagName === 'DIV' && c.getBoundingClientRect().height >= LARGE_DIV_H
        );
        if (hasSemanticChildren) {
          collectSections(el, depth + 1);
        } else if (bigDivChildren.length >= 2) {
          collectSections(el, depth + 1);
        } else if (h > Math.min(VIEWPORT_H * 0.25, 400)) {
          containers.push(el);
        }
      }
    });
  }

  collectSections(document.body, 0);

  const unique = containers.filter((el, i) =>
    !containers.some((other, j) => j !== i && other.contains(el))
  );
  unique.sort((a, b) =>
    (a.getBoundingClientRect().top + window.scrollY) -
    (b.getBoundingClientRect().top + window.scrollY)
  );

  const isFooter = (el) =>
    el.tagName === 'FOOTER' ||
    el.getAttribute('role') === 'contentinfo' ||
    /footer/i.test(el.id || '') ||
    /footer/i.test((el.className && el.className.toString && el.className.toString()) || '');
  const isHeader = (el) =>
    el.tagName === 'HEADER' ||
    el.getAttribute('role') === 'banner' ||
    /header/i.test(el.id || '') ||
    /header/i.test((el.className && el.className.toString && el.className.toString()) || '');

  return JSON.stringify({
    totalCount: unique.length,
    hasFooter: unique.some(isFooter),
    hasHeader: unique.some(isHeader),
    sections: unique.map((el, i) => ({
      index: i,
      tag: el.tagName.toLowerCase(),
      className: ((el.className && el.className.toString && el.className.toString()) || '').slice(0, 80),
      id: el.id || null,
      role: el.getAttribute('role') || null,
      height: Math.round(el.getBoundingClientRect().height),
      top: Math.round(el.getBoundingClientRect().top + window.scrollY),
      childCount: el.children.length,
      textPreview: (el.textContent && el.textContent.trim().slice(0, 60)) || '',
    })),
  }, null, 2);
})()
JSEOF
)

TMP_OUT=$(mktemp)
agent-browser --session "$SESSION" eval "$EVAL_JS" > "$TMP_OUT" 2>&1 || {
  echo "extract-section-map: agent-browser eval failed:" >&2
  head -c 500 "$TMP_OUT" >&2
  rm -f "$TMP_OUT"
  exit 4
}

# Newer agent-browser versions JSON-encode the eval return value, so a
# function that already calls JSON.stringify(...) yields a double-encoded
# string on disk. Same unwrap-and-validate pattern as extract-dom.sh.
if ! python3 -c "
import json, sys
d = json.load(open('$TMP_OUT'))
if isinstance(d, str):
    d = json.loads(d)
if not isinstance(d, dict):
    raise ValueError('top-level must be object')
for k in ('totalCount', 'sections'):
    if k not in d:
        raise ValueError('missing ' + k)
if not isinstance(d['sections'], list):
    raise ValueError('sections must be list')
json.dump(d, open('$OUT_PATH', 'w'), indent=2, ensure_ascii=False)
" 2>&1; then
  echo "extract-section-map: output failed schema validation" >&2
  head -c 500 "$TMP_OUT" >&2
  rm -f "$TMP_OUT"
  exit 5
fi
rm -f "$TMP_OUT"

SECTION_COUNT=$(python3 -c "
import json
d = json.load(open('$OUT_PATH'))
print(len(d.get('sections', [])))
")
echo "extract-section-map: wrote $OUT_PATH"
echo "  sections: $SECTION_COUNT"
