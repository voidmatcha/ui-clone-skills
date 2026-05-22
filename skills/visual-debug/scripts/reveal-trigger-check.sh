#!/usr/bin/env bash
# reveal-trigger-check.sh — Find scroll-triggered reveals that never trigger.
#
# Why it matters:
#   Intersection-based reveals (RevealRise / RevealLetters / fade-up patterns)
#   silently fail when the IntersectionObserver observes a node whose VISIBLE
#   intersection rect is empty — most commonly because a transform offset
#   pushes the observed element OUTSIDE an ancestor with `overflow: hidden`.
#   The element exists, but IO returns `intersect: false` forever and the
#   reveal stays in its initial (hidden) state.
#
#   AE/SSIM/section-compare miss this because the page renders SOMETHING in
#   that slot (often the background) and the static screenshot of the impl
#   may look "nearly right". The smoking gun is in the runtime: a node whose
#   `transform` and `opacity` never advance past their initial values after
#   it scrolls into view.
#
#   The bug class is invisible to the predefined hover/timer transition
#   gates because their trigger is `intersection`, not `hover` or `timer`.
#   This script makes that runtime category checkable on its own.
#
# Usage:
#   bash reveal-trigger-check.sh <session> <impl-url> [viewport-w] [viewport-h]
#
# Exit: 0 = all reveals trigger, 1 = stuck reveals found, 2 = setup error
#
# Output: Markdown table of stuck elements with selector, init/post styles,
#         and the parent-chain showing the `overflow: hidden` ancestor that
#         is most likely clipping the observer.

set -uo pipefail

if ! command -v agent-browser &>/dev/null; then
  echo "ERROR: agent-browser not found. Install: npm i -g agent-browser"
  exit 2
fi

SESSION="${1:?Usage: reveal-trigger-check.sh <session> <impl-url> [w] [h]}"
URL="${2:?Missing impl-url}"
VIEW_W="${3:-${VIEW_W:-1440}}"
VIEW_H="${4:-${VIEW_H:-900}}"
WAIT_MS="${WAIT_MS:-1500}"
SETTLE_MS="${SETTLE_MS:-1200}"

cleanup() {
  agent-browser --session "$SESSION" close 2>/dev/null
}
trap cleanup EXIT

agent-browser --session "$SESSION" set viewport "$VIEW_W" "$VIEW_H" >/dev/null 2>&1
agent-browser --session "$SESSION" navigate "$URL" >/dev/null 2>&1
sleep $((WAIT_MS / 1000))

# Phase 1: enumerate candidate hidden-init elements (opacity 0 or non-identity transform).
# We pin each by a stable index attribute so phase 2 can re-find them after scroll.
agent-browser --session "$SESSION" eval '(() => {
  const all = document.querySelectorAll("*");
  let n = 0;
  const SKIP_TAGS = new Set(["SCRIPT","STYLE","META","LINK","HEAD","TITLE","NOSCRIPT","NEXT-ROUTE-ANNOUNCER","HTML","BODY"]);
  for (const el of all) {
    if (SKIP_TAGS.has(el.tagName)) continue;
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden") continue;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    const opacity = parseFloat(cs.opacity);
    const tform = cs.transform;
    const initiallyHidden = opacity === 0 || (tform && tform !== "none" && tform !== "matrix(1, 0, 0, 1, 0, 0)");
    if (!initiallyHidden) continue;
    // Skip elements with no CSS transition — they are static hidden state (closed
    // overlays, hover-only videos, centering transforms), not reveal animations.
    const transDurs = (cs.transitionDuration || "").split(",").map(d => parseFloat(d) || 0);
    const animName = cs.animationName || "none";
    const hasMotion = transDurs.some(d => d > 0) || animName !== "none";
    if (!hasMotion) continue;
    // Skip hover-swap pattern: element has a visible sibling at the same position
    // (image+video stacked in a card). Those are hover-triggered swaps, not scroll reveals.
    const siblings = el.parentElement ? Array.from(el.parentElement.children) : [];
    const hasVisibleSwapSibling = siblings.some(sib => {
      if (sib === el) return false;
      const ss = getComputedStyle(sib);
      if (parseFloat(ss.opacity) === 0 || ss.display === 'none' || ss.visibility === 'hidden') return false;
      const sr = sib.getBoundingClientRect();
      return Math.abs(sr.width - r.width) < 2 && Math.abs(sr.height - r.height) < 2 && Math.abs(sr.left - r.left) < 2 && Math.abs(sr.top - r.top) < 2;
    });
    if (hasVisibleSwapSibling) continue;
    el.setAttribute("data-reveal-probe", String(n++));
  }
  return n;
})()' >/dev/null 2>&1

# Phase 2: for each probed element, scroll into view, settle, recapture style.
# Build a parent-chain that highlights `overflow: hidden` ancestors.
RAW=$(agent-browser --session "$SESSION" eval "(async () => {
  const probes = document.querySelectorAll('[data-reveal-probe]');
  const out = [];
  const SETTLE = $SETTLE_MS;
  for (const el of probes) {
    const idx = el.getAttribute('data-reveal-probe');
    const csInit = getComputedStyle(el);
    const init = { opacity: csInit.opacity, transform: csInit.transform };

    el.scrollIntoView({ block: 'center', behavior: 'instant' });
    await new Promise(r => setTimeout(r, SETTLE));

    const csPost = getComputedStyle(el);
    const post = { opacity: csPost.opacity, transform: csPost.transform };

    // True stuck = post is STILL in a hidden-init state (opacity 0 or transform != identity).
    // If post.opacity === '1' AND post.transform is identity/none, the reveal completed —
    // any non-advance in init↔post just means we re-probed after the animation finished.
    const postIdentityTransform = !post.transform || post.transform === 'none' || post.transform === 'matrix(1, 0, 0, 1, 0, 0)';
    const postHidden = parseFloat(post.opacity) === 0 || !postIdentityTransform;
    const stuck =
      postHidden &&
      (init.opacity === post.opacity && init.transform === post.transform);
    if (!stuck) continue;

    // Walk ancestors, collect overflow-hidden hops.
    const chain = [];
    let p = el.parentElement;
    let depth = 0;
    while (p && depth < 12) {
      const pcs = getComputedStyle(p);
      const ovh = pcs.overflow === 'hidden' || pcs.overflowY === 'hidden' || pcs.overflowX === 'hidden';
      const cls = (p.className || '').toString().slice(0, 32);
      chain.push({ tag: p.tagName, cls, overflowHidden: ovh });
      p = p.parentElement;
      depth++;
    }
    const tag = el.tagName;
    const cls = (el.className || '').toString().slice(0, 48);
    const r = el.getBoundingClientRect();
    out.push({
      idx,
      tag,
      cls,
      box: Math.round(r.width) + 'x' + Math.round(r.height),
      init,
      post,
      chain,
    });
  }
  // Cleanup probe attributes so a follow-up run starts clean.
  document.querySelectorAll('[data-reveal-probe]').forEach(e => e.removeAttribute('data-reveal-probe'));
  return JSON.stringify(out);
})()" 2>/dev/null)

DATA=$(echo "$RAW" | sed 's/^"//;s/"$//' | sed 's/\\"/"/g')

ART_OUT="${REF_DIR:-}"
if [ -n "$ART_OUT" ] && [ -d "$ART_OUT" ]; then
  ART_FILE="$ART_OUT/reveal-trigger.json"
else
  ART_FILE=""
fi

if [ -z "$DATA" ] || [ "$DATA" = "[]" ] || [ "$DATA" = "null" ]; then
  echo "✅ No stuck reveals found."
  if [ -n "$ART_FILE" ]; then
    cat > "$ART_FILE" <<JSON
{
  "schemaVersion": 1,
  "status": "pass",
  "implUrl": "$URL",
  "viewport": "${VIEW_W}x${VIEW_H}",
  "stuckCount": 0,
  "stuck": []
}
JSON
  fi
  exit 0
fi
# Emit fail artifact (data is JSON array of stuck entries).
if [ -n "$ART_FILE" ]; then
  python3 - "$DATA" "$URL" "${VIEW_W}x${VIEW_H}" "$ART_FILE" <<'PY'
import json, sys
data_raw, url, viewport, out_path = sys.argv[1:5]
try:
    entries = json.loads(data_raw)
except ValueError:
    entries = []
if not isinstance(entries, list):
    entries = []
result = {
    "schemaVersion": 1,
    "status": "fail",
    "implUrl": url,
    "viewport": viewport,
    "stuckCount": len(entries),
    "stuck": entries[:30],
    "rule": (
        "Elements with hidden-init style (opacity:0 or non-identity "
        "transform) must advance to a visible state after being "
        "scrolled into view. Stuck reveals indicate an IO+overflow:"
        "hidden bug class — IntersectionObserver attached to the "
        "transformed child instead of the non-moving outer wrapper, "
        "or a CSS reset killing the transition."
    ),
}
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(result, fh, indent=2)
PY
fi

echo "═══ Stuck Reveal Detection ═══"
echo "URL: $URL"
echo "Viewport: ${VIEW_W}x${VIEW_H}"
echo ""
echo "Elements with hidden-init style (opacity 0 or non-identity transform)"
echo "whose style did NOT advance after scrolling them into view."
echo ""

node -e "
const data = JSON.parse(process.argv[1]);
console.log('| # | tag | class | box | init opacity | init transform | post opacity | post transform | clipping ancestor |');
console.log('|---|-----|-------|-----|--------------|----------------|--------------|----------------|-------------------|');
data.forEach((s, i) => {
  const clipped = s.chain.find(c => c.overflowHidden);
  const clip = clipped ? (clipped.tag + (clipped.cls ? '.' + clipped.cls.split(' ')[0] : '')) : '—';
  const initT = s.init.transform.length > 28 ? s.init.transform.slice(0, 28) + '…' : s.init.transform;
  const postT = s.post.transform.length > 28 ? s.post.transform.slice(0, 28) + '…' : s.post.transform;
  const cls = s.cls.split(' ')[0] || '';
  console.log('| ' + i + ' | ' + s.tag + ' | ' + cls + ' | ' + s.box + ' | ' + s.init.opacity + ' | ' + initT + ' | ' + s.post.opacity + ' | ' + postT + ' | ' + clip + ' |');
});
console.log('');
console.log('Likely root cause for entries with a clipping ancestor:');
console.log('  IntersectionObserver was attached to the TRANSFORMED CHILD, not the');
console.log('  non-moving outer wrapper. The transform pushed the observed element');
console.log('  outside the overflow:hidden box, so the visible intersection rect is');
console.log('  empty and IO returns intersect:false forever.');
console.log('');
console.log('Fix: split the component into outer (IO ref + overflow:hidden) and inner');
console.log('     (the moving element). See:');
console.log('       ui-reverse-engineering/transition-implementation.md');
console.log('         → IntersectionObserver placement for masked reveals');
console.log('       ui-reverse-engineering/diagnosis.md → Root Cause E');
process.exit(1);
" "$DATA"
