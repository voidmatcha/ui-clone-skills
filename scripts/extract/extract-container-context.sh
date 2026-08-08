#!/usr/bin/env bash
# extract-container-context.sh -- capture the reference page's CSS container-query
# context into container-context.json.
#
# Why it matters:
#   Modern commercial SPAs (eBay, etc.) size whole modules with CSS container
#   queries: an ancestor carries `container-type: inline-size` and its
#   descendants use `@container` / `@md:` / `@lg:` utilities that resolve against
#   that ancestor's WIDTH, not the viewport. If the transpiler drops a
#   container-type ancestor, or reproduces it at the wrong width (e.g. a product
#   grid that renders 2 columns instead of 4, doubling each cell's width), every
#   `@container` utility beneath it snaps to the wrong breakpoint and the whole
#   subtree mis-sizes -- the silent root cause behind "recognizable but ~15% off"
#   clones. This artifact is the ground-truth inventory the verify-side
#   container-context-check.sh compares the implementation against.
#
# Usage:
#   extract-container-context.sh <ref-dir> <session-name>
#
#   <ref-dir>:      tmp/ref/<component> (container-context.json is written here)
#   <session-name>: agent-browser session already navigated to the reference URL
#
# Exit: 0 = wrote artifact, 2 = setup error, 4 = eval failed, 5 = schema invalid

set -uo pipefail

REF_DIR="${1:?Usage: extract-container-context.sh <ref-dir> <session-name>}"
SESSION="${2:?Missing session-name}"

if [ ! -d "$REF_DIR" ]; then
  echo "extract-container-context: ref-dir not found: $REF_DIR" >&2
  exit 2
fi
if ! command -v agent-browser >/dev/null 2>&1; then
  echo "extract-container-context: agent-browser not found on PATH" >&2
  exit 2
fi

OUT_PATH="$REF_DIR/container-context.json"

EVAL_JS=$(cat <<'JSEOF'
(() => {
  // Inventory every element that establishes a CSS container-query context.
  // Signature keys a container by its container-name (if any) else its first
  // class token else its tag, so the verify side can match ref -> impl even
  // when the transpiler renames nothing (forensic preservation keeps classes).
  const SKIP = new Set(['SCRIPT','STYLE','META','LINK','HEAD','TITLE','NOSCRIPT']);
  const items = [];
  document.querySelectorAll('*').forEach(el => {
    if (SKIP.has(el.tagName)) return;
    const cs = getComputedStyle(el);
    const ct = cs.containerType;
    if (!ct || ct === 'normal') return;
    const r = el.getBoundingClientRect();
    if (r.width < 1 && r.height < 1) return;
    const cls = (el.className && el.className.toString ? el.className.toString() : '').trim();
    const first = cls ? cls.split(/\s+/)[0] : '';
    const name = (cs.containerName && cs.containerName !== 'none') ? cs.containerName : '';
    items.push({
      sig: name || first || el.tagName.toLowerCase(),
      tag: el.tagName.toLowerCase(),
      firstClass: first.slice(0, 60),
      containerType: ct,
      containerName: name,
      width: Math.round(r.width),
      height: Math.round(r.height)
    });
  });
  // Aggregate per signature so counts/widths are stable across list repetition.
  const bySig = {};
  items.forEach(it => {
    const g = bySig[it.sig] || (bySig[it.sig] = {
      sig: it.sig, tag: it.tag, firstClass: it.firstClass,
      containerType: it.containerType, count: 0, widths: []
    });
    g.count += 1;
    g.widths.push(it.width);
  });
  const groups = Object.values(bySig).map(g => {
    const ws = g.widths.slice().sort((a, b) => a - b);
    return {
      sig: g.sig, tag: g.tag, firstClass: g.firstClass,
      containerType: g.containerType, count: g.count,
      medianWidth: ws[Math.floor(ws.length / 2)]
    };
  }).sort((a, b) => b.count - a.count);
  return JSON.stringify({
    schemaVersion: 1,
    source: 'extract-container-context.sh',
    totalContainers: items.length,
    distinctSignatures: groups.length,
    viewport: { w: window.innerWidth, h: window.innerHeight },
    groups: groups
  });
})()
JSEOF
)

TMP_OUT=$(mktemp)
agent-browser --session "$SESSION" eval "$EVAL_JS" > "$TMP_OUT" 2>&1 || {
  echo "extract-container-context: agent-browser eval failed:" >&2
  head -c 500 "$TMP_OUT" >&2
  rm -f "$TMP_OUT"
  exit 4
}

# Newer agent-browser versions JSON-encode the eval return value; unwrap the
# double-encoding then validate, same pattern as extract-section-map.sh.
if ! python3 -c "
import json, sys
d = json.load(open('$TMP_OUT'))
if isinstance(d, str):
    d = json.loads(d)
if not isinstance(d, dict):
    raise ValueError('top-level must be object')
for k in ('totalContainers', 'groups'):
    if k not in d:
        raise ValueError('missing ' + k)
if not isinstance(d['groups'], list):
    raise ValueError('groups must be list')
json.dump(d, open('$OUT_PATH', 'w'), indent=2, ensure_ascii=False)
" 2>&1; then
  echo "extract-container-context: output failed schema validation" >&2
  head -c 500 "$TMP_OUT" >&2
  rm -f "$TMP_OUT"
  exit 5
fi
rm -f "$TMP_OUT"

TOTAL=$(python3 -c "import json; print(json.load(open('$OUT_PATH'))['totalContainers'])")
echo "extract-container-context: wrote $OUT_PATH"
echo "  container-type elements: $TOTAL"
