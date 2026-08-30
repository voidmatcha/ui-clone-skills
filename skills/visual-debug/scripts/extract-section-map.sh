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

# W-4 (loop-ebpb-0): the reference follows prefers-color-scheme — a host
# OS theme flip (macOS auto-dark in the evening) silently captured the ref
# in dark mode and poisoned an entire compare cycle (footer dSSIM
# 0.0000065 -> 0.687 reading as catastrophic regression). Pin light unless
# the caller explicitly overrides.
: "${AGENT_BROWSER_COLOR_SCHEME:=light}"
export AGENT_BROWSER_COLOR_SCHEME

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

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/idle-reset.sh
. "$_SCRIPT_DIR/lib/idle-reset.sh"

OUT_PATH="$REF_DIR/section-map.json"

# The JS is written to a temp file INSTEAD of `VAR=$(cat <<HEREDOC)`:
# macOS bash 3.2's naive command-substitution scanner tokenizes heredoc
# CONTENT while hunting for the closing paren, so an apostrophe or paren
# inside a JS comment can break the whole script (the D21 quoting class).
# A plain heredoc redirect has no such parsing hazard.
EVAL_JS_FILE=$(mktemp)
trap 'rm -f "$EVAL_JS_FILE"' EXIT
cat <<'JSEOF' > "$EVAL_JS_FILE"
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
  //   2. Div containers with >= 2 vertically distinct large div children
  //      (each >= 50% of viewport height) recurse even without semantic
  //      children. Same-row grid/flex columns keep their shared parent so
  //      their layout context is not flattened into a vertical stack.
  const VIEWPORT_H = window.innerHeight || 800;
  const LARGE_DIV_H = Math.min(VIEWPORT_H * 0.5, 600);
  const SKIP_TAGS = ['script', 'style', 'noscript', 'template'];
  const __maskSel = (typeof window !== 'undefined' && window.__UI_RE_DYNAMIC_SELECTORS__) || '';
  let __maskRoots = [];
  if (__maskSel) {
    try { __maskRoots = Array.from(document.querySelectorAll(__maskSel)); } catch (_) { __maskRoots = []; }
  }
  const isMaskHidden = (node) =>
    __maskRoots.some(r => r === node || (r.contains && r.contains(node)));
  const elementChildren = (el) =>
    Array.from(el.children).filter(c => !SKIP_TAGS.includes(c.tagName.toLowerCase()));
  const leadsToBranch = (el, remaining = 5) => {
    const children = elementChildren(el);
    if (children.length >= 2) return true;
    if (children.length !== 1 || remaining <= 0) return false;
    const child = children[0];
    const h = el.getBoundingClientRect().height;
    return child.getBoundingClientRect().height >= h * 0.9
      && leadsToBranch(child, remaining - 1);
  };
  const hasVerticallyDistinctChildren = (children) =>
    children.some((first, index) => {
      const a = first.getBoundingClientRect();
      return children.slice(index + 1).some(second => {
        const b = second.getBoundingClientRect();
        const overlap = Math.max(
          0,
          Math.min(a.top + a.height, b.top + b.height) - Math.max(a.top, b.top)
        );
        return overlap < Math.min(a.height, b.height) * 0.5;
      });
    });
  function collectSections(parent, depth, allowPassThrough) {
    // Base pass keeps the original depth-4 bound so well-segmenting sites are
    // byte-for-byte unchanged; the degenerate retry gets 2 extra levels to
    // unwrap opaque pass-through wrappers.
    if (depth > (allowPassThrough ? 6 : 4)) return;
    Array.from(parent.children).forEach(el => {
      const tag = el.tagName.toLowerCase();
      if (SKIP_TAGS.includes(tag)) return;
      const h = el.getBoundingClientRect().height;
      const role = el.getAttribute('role');
      const style = getComputedStyle(el);
      if (style.display === 'contents') {
        // Layout-transparent wrappers have a zero-height rect but can own the
        // page's real landmarks. Do not let them consume the recursion budget.
        collectSections(el, depth, allowPassThrough);
      } else if ((semanticTags.has(tag) || semanticRoles.has(role)) && h > 50) {
        if (h > VIEWPORT_H * 2) {
          // D1 (loop-nvti-0): decompose-then-keep. A semantic container
          // taller than 2x viewport is decomposed — but when the recursion
          // collects NOTHING inside it (children are runtime-built or
          // collapsed at capture time, e.g. GSAP page-stack panels), the
          // section itself must be kept. Dropping it silently erased five
          // 3-4k-px sections (~19.4k px) into one catch-all on a 23k page.
          const before = containers.length;
          collectSections(el, depth + 1, allowPassThrough);
          if (containers.length === before) containers.push(el);
        } else {
          containers.push(el);
        }
      } else if (tag === 'div' && h > 100) {
        const childrenArr = elementChildren(el);
        // L-CAP-3 (loop-ebpb-0/1): content-empty rails are never sections.
        // ebay-playbook mounts 6 invisible, classless, childless 1350px divs
        // inside an absolute rail under <body>; enumerating them poisoned the
        // section-compare/geometry denominators with 12 phantom rows across
        // two loops. A div with NO element children, NO text, and NO media
        // is decoration, not a section — skip it entirely (it also has
        // nothing to recurse into).
        const hasAnyContent = childrenArr.length > 0
          || (el.textContent && el.textContent.trim().length > 0)
          || !!el.querySelector('img,video,canvas,iframe,svg,picture');
        if (!hasAnyContent) return;
        const hasSemanticChildren = childrenArr.some(c =>
          semanticTags.has(c.tagName.toLowerCase()) ||
          semanticRoles.has(c.getAttribute('role') || '')
        );
        const bigDivChildren = childrenArr.filter(c => {
          if (c.tagName !== 'DIV') return false;
          if (c.getBoundingClientRect().height < LARGE_DIV_H) return false;
          // FIX-2: an OUT-OF-FLOW child (position absolute/fixed) is an overlay
          // or pinned backdrop, not an in-flow section boundary. Counting it as
          // a "big div child" makes a composite look like a 2-section stack and
          // splits it — e.g. a video player + its ABSOLUTE control overlay
          // (same rect) got descended-into and emitted as two sibling sections,
          // so the control no longer overlaid the video. An out-of-flow child
          // never delimits flow, so it must not trigger the descent.
          const pos = getComputedStyle(c).position;
          return pos !== 'absolute' && pos !== 'fixed';
        });
        // Large sibling divs only delimit independent sections when they are
        // vertically distinct. Same-row grid/flex columns substantially
        // overlap on the Y axis and need their shared parent to preserve the
        // layout context (eBay Playbook media grids and video blurbs).
        const hasVerticalSectionStack =
          bigDivChildren.length >= 2
          && hasVerticallyDistinctChildren(bigDivChildren);
        // Pass-through wrapper: a container whose visual height is carried by a
        // single dominant child (>= 90% of its own height). Modern Tailwind
        // SPAs nest the whole page under 1-2 such wrappers (e.g. an
        // isRootLayout div wrapping a single flex-col div) with no semantic
        // boundary, so a prior version collapsed the entire body to one
        // section. Descend THROUGH the wrapper -- but only when the dominant
        // child chain eventually branches into >= 2 real children (i.e.
        // unwrapping yields more granularity, not just a one-level shift), so
        // a full-bleed div that wraps a single hero image stays a leaf section.
        // Only enabled on the degenerate retry (see below) so well-segmenting
        // sites never change.
        const dominantChild = childrenArr.find(c =>
          c.getBoundingClientRect().height >= h * 0.9
        );
        const passThroughWrapper =
          dominantChild && leadsToBranch(dominantChild);
        if (hasSemanticChildren) {
          collectSections(el, depth + 1, allowPassThrough);
        } else if (hasVerticalSectionStack) {
          collectSections(el, depth + 1, allowPassThrough);
        } else if (allowPassThrough && passThroughWrapper) {
          collectSections(el, depth + 1, allowPassThrough);
        } else if (h > Math.min(VIEWPORT_H * 0.25, 400)) {
          containers.push(el);
        }
      }
    });
  }

  // Two-pass with a degenerate fallback: run the standard heuristic first so
  // sites that already segment cleanly keep their exact behavior. Only when
  // that collapses to <= 2 sections (the opaque-single-wrapper SPA pattern,
  // e.g. eBay) do we retry with pass-through unwrapping enabled.
  const dedupe = (arr) => arr.filter((el, i) =>
    !arr.some((other, j) => j !== i && other.contains(el))
  );
  containers.length = 0;
  collectSections(document.body, 0, false);
  let unique = dedupe(containers);
  if (unique.length <= 2) {
    containers.length = 0;
    collectSections(document.body, 0, true);
    const retry = dedupe(containers);
    if (retry.length > unique.length) unique = retry;
  }
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

  // D1 companion: coverage stats so a silent multi-thousand-px enumeration
  // gap can never hide again — consumers (and the report) can compare
  // coveredPx against docHeight. Fixed-position overlays (header/mo-nav)
  // legitimately overlap the flow, so the union is over in-flow spans.
  const docHeight = Math.round(Math.max(
    document.body.getBoundingClientRect().height,
    (document.documentElement && document.documentElement.scrollHeight) || 0
  ));
  const spans = unique
    .filter(el => { try { return getComputedStyle(el).position !== 'fixed'; } catch (_) { return true; } })
    .map(el => {
      const r = el.getBoundingClientRect();
      const top = Math.max(0, Math.round(r.top + window.scrollY));
      return [top, top + Math.round(r.height)];
    })
    .sort((a, b) => a[0] - b[0]);
  let coveredPx = 0; let curStart = null; let curEnd = null;
  for (const [s, e] of spans) {
    if (curStart === null) { curStart = s; curEnd = e; continue; }
    if (s <= curEnd) { curEnd = Math.max(curEnd, e); }
    else { coveredPx += curEnd - curStart; curStart = s; curEnd = e; }
  }
  if (curStart !== null) coveredPx += curEnd - curStart;

  const stickyGeometry = (el) => {
    try {
      const style = getComputedStyle(el);
      if (style.position !== 'sticky' && style.position !== '-webkit-sticky') {
        return null;
      }
      const container = el.parentElement;
      if (!container) return null;
      const pxInset = (value) => {
        if (!value || value === 'auto') return 0;
        const parsed = Number.parseFloat(value);
        return Number.isFinite(parsed) ? Math.max(0, parsed) : 0;
      };
      const containerRect = container.getBoundingClientRect();
      const stickyRect = el.getBoundingClientRect();
      const containerH = containerRect.height;
      const topInset = pxInset(style.top);
      const containerTop = containerRect.top + window.scrollY;
      const stickyTop = stickyRect.top + window.scrollY;
      const pinStart = Math.max(0, stickyTop - topInset);
      const pinEnd = Math.max(
        pinStart,
        containerTop + containerH - stickyRect.height - topInset
      );
      return {
        containerH: Math.max(0, Math.round(containerH)),
        rangeH: Math.max(0, Math.round(pinEnd - pinStart)),
      };
    } catch (_) {
      return null;
    }
  };

  return JSON.stringify({
    totalCount: unique.length,
    hasFooter: unique.some(isFooter),
    hasHeader: unique.some(isHeader),
    docHeight: docHeight,
    coveredPx: coveredPx,
    sections: unique.map((el, i) => {
      const mediaSelector = 'img,video,canvas,iframe,svg,picture,object,embed';
      const mediaNodes = (() => {
        try {
          if (el.querySelectorAll) return Array.from(el.querySelectorAll(mediaSelector));
          const single = el.querySelector ? el.querySelector(mediaSelector) : null;
          return single ? [single] : [];
        } catch (_) {
          return [];
        }
      })().filter(node => {
        const r = node.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return false;
        const style = getComputedStyle(node);
        return style.display !== 'none'
          && (style.visibility !== 'hidden' || isMaskHidden(node))
          && parseFloat(style.opacity || '1') > 0;
      });
      return {
        index: i,
        tag: el.tagName.toLowerCase(),
        className: ((el.className && el.className.toString && el.className.toString()) || '').slice(0, 80),
        id: el.id || null,
        role: el.getAttribute('role') || null,
        height: Math.round(el.getBoundingClientRect().height),
        top: Math.round(el.getBoundingClientRect().top + window.scrollY),
        // Preserve the containing-block height separately from achievable sticky
        // travel. Pin start/end account for the sticky border box, top inset,
        // natural offset inside the container, and document-start clamping.
        position: (() => { try { return getComputedStyle(el).position; } catch (_) { return null; } })(),
        stickyContainerH: (() => {
          const geometry = stickyGeometry(el);
          return geometry ? geometry.containerH : null;
        })(),
        stickyRangeH: (() => {
          const geometry = stickyGeometry(el);
          return geometry ? geometry.rangeH : null;
        })(),
        childCount: el.children.length,
        hasVisibleMedia: mediaNodes.length > 0,
        visibleMediaCount: mediaNodes.length,
        textPreview: (el.textContent && el.textContent.trim().slice(0, 60)) || '',
      };
    }),
  }, null, 2);
})()
JSEOF
EVAL_JS=$(cat "$EVAL_JS_FILE")

CAPTURED_IDLE="$(ab_idle_reset "$SESSION")"

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
if ! UI_CLONE_CAPTURED_IDLE="$CAPTURED_IDLE" python3 -c "
import json, os, sys
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
ci = os.environ.get('UI_CLONE_CAPTURED_IDLE')
if ci:
    try:
        d['capturedIdle'] = json.loads(ci)
    except Exception:
        d['capturedIdle'] = {'reset': False, 'idle': None, 'note': 'provenance-parse-failed'}
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
