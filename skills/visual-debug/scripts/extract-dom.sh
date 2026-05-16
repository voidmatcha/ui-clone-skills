#!/usr/bin/env bash
# extract-dom.sh — invoke the Fix 13 DOM extraction eval as a real callable.
#
# Replaces the dom-extraction.md prose guide as the canonical entry point.
# Across V5–V10 the prose guide was repeatedly ignored — agents wrote their
# own variant of the eval that lost the per-node `text` field (Fix 6 v1)
# and the per-node `styles` field (Fix 13). With this as a script, the
# eval is invoked verbatim; downstream gates (pre-generate) check the
# resulting structure.json schema.
#
# Usage:
#   extract-dom.sh <ref-dir> <session-name> <target-selector>
#
# Writes <ref-dir>/structure.json with this schema:
#   { tag, class, display, position, text?, styles?, children: [...] }
#
# Where:
#   - text     present only if the node has its own (non-descendant) text
#   - styles   present only if at least one LAYOUT_PROPS value diverges
#              from the user-agent default. Subset of ~25 props that
#              materially affect rendered layout (display/position/box-
#              model/typography/color/transform/flex/grid/box-shadow/...).
#
# This is the deterministic primitive that Phase 4's scaffold-to-jsx.sh
# transpiler consumes. The LLM does NOT write this schema — Phase 2 does.
set -euo pipefail

REF_DIR=""
SESSION=""
TARGET=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) sed -n '2,22p' "$0"; exit 0;;
    *)
      if [[ -z "$REF_DIR" ]]; then REF_DIR="$1"
      elif [[ -z "$SESSION" ]]; then SESSION="$1"
      elif [[ -z "$TARGET" ]]; then TARGET="$1"
      else echo "extract-dom: unexpected arg: $1" >&2; exit 2
      fi
      shift
      ;;
  esac
done

if [[ -z "$REF_DIR" || -z "$SESSION" || -z "$TARGET" ]]; then
  echo "usage: extract-dom.sh <ref-dir> <session-name> <target-selector>" >&2
  exit 2
fi
if [[ ! -d "$REF_DIR" ]]; then
  echo "extract-dom: ref-dir not found: $REF_DIR" >&2; exit 2
fi
if ! command -v agent-browser >/dev/null 2>&1; then
  echo "extract-dom: agent-browser not found on PATH" >&2; exit 3
fi

OUT_PATH="$REF_DIR/structure.json"

EXTRACT_JS=$(cat <<'JSEOF'
(() => {
  const target = document.querySelector(SELECTOR_PLACEHOLDER);
  if (!target) return JSON.stringify({ error: 'selector not found' });
  const directText = (el) => {
    let t = '';
    for (const n of el.childNodes) {
      if (n.nodeType === 3) t += n.textContent;
    }
    return t.trim().replace(/\s+/g, ' ').slice(0, 300);
  };
  const LAYOUT_PROPS = [
    'display','position','top','left','right','bottom',
    'width','height','min-width','max-width','min-height','max-height',
    'padding','margin','border-radius','border',
    'background-color','background-image','background-size','background-position',
    'color','font-family','font-size','font-weight','line-height','letter-spacing',
    'text-align','text-decoration','text-transform','white-space',
    'transform','opacity','overflow',
    'flex','flex-direction','justify-content','align-items','gap',
    'grid-template-columns','grid-template-rows',
    'z-index','box-shadow',
    // Fix 16 — transition + animation. The transpiler emits each captured
    // value as a property inside style={{ ... }} so the impl renders the
    // same hover/focus/active transitions as the ref. NOISE filters out
    // the user-agent defaults ('none', 'all 0s ease 0s', etc.) so only
    // ref-authored transitions reach the JSX.
    'transition','transition-property','transition-duration',
    'transition-timing-function','transition-delay',
    'animation','animation-name','animation-duration',
    'animation-timing-function','animation-delay',
    'animation-iteration-count','animation-direction',
    'animation-fill-mode','animation-play-state',
    'cursor','pointer-events',
  ];
  const NOISE = new Set([
    '', 'normal', 'none', 'auto', '0px', 'rgba(0, 0, 0, 0)', 'visible', 'start',
    // Fix 16 — user-agent defaults for transition/animation. Without these
    // every node would carry a noisy 'all 0s ease 0s' transition value.
    'all 0s ease 0s', 'all', '0s', 'ease', '1', 'running', 'forwards', 'backwards',
  ]);
  const extract = (el, depth = 0) => {
    if (depth > 6) return null;
    const s = getComputedStyle(el);
    const text = directText(el);
    const styles = {};
    for (const p of LAYOUT_PROPS) {
      const v = s.getPropertyValue(p);
      if (v && !NOISE.has(v)) styles[p] = v.slice(0, 200);
    }
    const out = {
      tag: el.tagName.toLowerCase(),
      class: (typeof el.className === 'string' ? el.className : el.className?.baseVal || '').slice(0, 80),
      display: s.display,
      position: s.position,
      children: Array.from(el.children).map(c => extract(c, depth + 1)).filter(Boolean),
    };
    if (text) out.text = text;
    if (Object.keys(styles).length) out.styles = styles;
    return out;
  };
  return JSON.stringify(extract(target), null, 2);
})()
JSEOF
)

# Inject the selector — must be a JS string literal. Escape single-quotes.
SELECTOR_LITERAL=$(printf '%s' "$TARGET" | sed "s/'/\\\\'/g")
EVAL_JS="${EXTRACT_JS/SELECTOR_PLACEHOLDER/'$SELECTOR_LITERAL'}"

# Run via agent-browser and capture.
TMP_OUT=$(mktemp)
agent-browser --session "$SESSION" eval "$EVAL_JS" > "$TMP_OUT" 2>&1 || {
  echo "extract-dom: agent-browser eval failed:" >&2
  cat "$TMP_OUT" >&2
  rm -f "$TMP_OUT"
  exit 4
}

# Validate JSON shape: must have `tag` (root node) and `children`.
if ! python3 -c "
import json, sys
d = json.load(open('$TMP_OUT'))
if not isinstance(d, dict): raise ValueError('top-level must be object')
if 'tag' not in d: raise ValueError('missing tag — schema mismatch (Fix 14)')
if 'children' not in d: raise ValueError('missing children — schema mismatch')
" 2>&1; then
  echo "extract-dom: output failed schema validation" >&2
  head -c 500 "$TMP_OUT" >&2
  rm -f "$TMP_OUT"
  exit 5
fi

mv "$TMP_OUT" "$OUT_PATH"
NODE_COUNT=$(python3 -c "
import json
d = json.load(open('$OUT_PATH'))
def cnt(n, c=0):
  if isinstance(n, dict):
    c += 1
    for k in n.get('children', []): c = cnt(k, c)
  return c
print(cnt(d))
")
STYLE_COUNT=$(python3 -c "
import json
d = json.load(open('$OUT_PATH'))
def cnt(n, c=0):
  if isinstance(n, dict):
    if n.get('styles'): c += 1
    for k in n.get('children', []): c = cnt(k, c)
  return c
print(cnt(d))
")
echo "extract-dom: wrote $OUT_PATH"
echo "  nodes: $NODE_COUNT, with styles: $STYLE_COUNT"
