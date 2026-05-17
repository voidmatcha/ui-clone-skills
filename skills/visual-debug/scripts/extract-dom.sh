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
VIEWPORT=""   # Fix 17 — optional WxH (e.g. 375x812). If set, agent-browser
              # resizes the session before extracting; output written to
              # structure_<W>x<H>.json instead of structure.json so a
              # multi-viewport sweep can keep both desktop and mobile
              # structures on disk for the transpiler / agent to diff.

while [[ $# -gt 0 ]]; do
  case "$1" in
    --viewport) VIEWPORT="$2"; shift 2;;
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
  echo "usage: extract-dom.sh <ref-dir> <session-name> <target-selector> [--viewport WxH]" >&2
  exit 2
fi
if [[ ! -d "$REF_DIR" ]]; then
  echo "extract-dom: ref-dir not found: $REF_DIR" >&2; exit 2
fi
if ! command -v agent-browser >/dev/null 2>&1; then
  echo "extract-dom: agent-browser not found on PATH" >&2; exit 3
fi

OUT_PATH="$REF_DIR/structure.json"
if [[ -n "$VIEWPORT" ]]; then
  # Validate WxH form so a typo doesn't silently produce desktop styles.
  if [[ ! "$VIEWPORT" =~ ^[0-9]+x[0-9]+$ ]]; then
    echo "extract-dom: --viewport must be WIDTHxHEIGHT (e.g. 375x812), got: $VIEWPORT" >&2
    exit 2
  fi
  OUT_PATH="$REF_DIR/structure_${VIEWPORT}.json"
  W="${VIEWPORT%x*}"; H="${VIEWPORT#*x}"
  # Resize before extracting. Failure here is fatal — extracting at the
  # wrong viewport silently produces desktop styles which is worse than
  # erroring.
  # agent-browser CLI uses `set viewport <w> <h>`, not `resize --width`.
  if ! agent-browser --session "$SESSION" set viewport "$W" "$H" >/dev/null 2>&1; then
    echo "extract-dom: agent-browser set viewport ${W}x${H} failed" >&2
    exit 6
  fi
fi

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
  // Fix 18 — pseudo-element capture. Helper extracts a non-empty subset of
  // LAYOUT_PROPS from a pseudo computed style, plus its `content` so the
  // transpiler can emit a <span data-pseudo="before" /> with matching styles
  // when the ref draws decorations via ::before / ::after (glow rings, icon
  // dots, gradient overlays, divider lines etc.). Without this the impl is
  // missing the entire pseudo-element layer — a dominant cause of the
  // "전체 레이아웃 못 잡는다" feel reported after V15.
  const capturePseudo = (el, which) => {
    const ps = getComputedStyle(el, which);
    const content = ps.getPropertyValue('content');
    if (!content || content === 'none' || content === 'normal') return null;
    const out = { content };
    for (const p of LAYOUT_PROPS) {
      const v = ps.getPropertyValue(p);
      if (v && !NOISE.has(v)) out[p] = v.slice(0, 200);
    }
    return out;
  };
  const extract = (el, depth = 0) => {
    if (depth > 10) return null;
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
    // Fix 18 — pseudo styles attached to the node so the transpiler can
    // synthesize <span data-pseudo> children with matching CSS.
    const before = capturePseudo(el, '::before');
    if (before) out.before_styles = before;
    const after = capturePseudo(el, '::after');
    if (after) out.after_styles = after;
    // Capture asset/link attrs so the transpiler can emit <img src>, <a href>,
    // <video poster>, etc. Without these the scaffold renders empty
    // placeholder boxes for every media element, which inflates section-compare
    // AE by ~700k per image-heavy section.
    const ATTR_KEYS = ['src','href','alt','poster','srcset','sizes','type','target','rel','aria-label','title','role','data-src','data-poster'];
    for (const k of ATTR_KEYS) {
      const v = el.getAttribute ? el.getAttribute(k) : null;
      if (v && v.length < 800) out[k] = v;
    }
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
# Newer agent-browser versions JSON-encode the eval return value, so a function
# that already calls JSON.stringify(...) yields a double-encoded string on disk.
# Unwrap once if the top-level is a string, then re-write the canonical form.
if ! python3 -c "
import json, sys
d = json.load(open('$TMP_OUT'))
if isinstance(d, str):
    d = json.loads(d)
if not isinstance(d, dict): raise ValueError('top-level must be object')
if 'tag' not in d: raise ValueError('missing tag — schema mismatch (Fix 14)')
if 'children' not in d: raise ValueError('missing children — schema mismatch')
json.dump(d, open('$OUT_PATH', 'w'), indent=2)
" 2>&1; then
  echo "extract-dom: output failed schema validation" >&2
  head -c 500 "$TMP_OUT" >&2
  rm -f "$TMP_OUT"
  exit 5
fi
rm -f "$TMP_OUT"
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
