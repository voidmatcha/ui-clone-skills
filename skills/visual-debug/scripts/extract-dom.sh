#!/usr/bin/env bash
# extract-dom.sh — invoke the Fix 13 DOM extraction eval as a real callable.
#
# Bash 4+ self-relaunch: this script uses heredoc shapes / array syntax that
if [ -z "${BASH_VERSION:-}" ] || [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
  for _bashcand in /opt/homebrew/bin/bash /usr/local/bin/bash; do
    [ -x "$_bashcand" ] && exec "$_bashcand" "$0" "$@"
  done
  echo "extract-dom.sh: bash 4+ required (current ${BASH_VERSION:-unknown}); install via 'brew install bash'" >&2
  exit 1
fi
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
  // Fix 19 — :hover / :focus rule extraction. Walks document.styleSheets,
  // matches each rule's selectorText against the element's class list, and
  // pulls the LAYOUT_PROPS subset from `:hover` declarations so the
  // transpiler emits matching CSS that lets the captured transition values
  // actually animate something. Without this Fix 16's transition properties
  // exist but have nothing to interpolate to — the impl stays static under
  // hover. Tries each sheet under try/catch since cross-origin stylesheets
  // throw on cssRules access. Memoized per element classlist by the caller.
  let HOVER_RULES = null;  // lazy initialized
  const buildHoverRules = () => {
    if (HOVER_RULES !== null) return HOVER_RULES;
    const out = [];  // [{selPrefix, decls: {prop: value}}]
    for (const sheet of document.styleSheets) {
      let rules;
      try { rules = sheet.cssRules || sheet.rules; } catch (e) { continue; }
      if (!rules) continue;
      for (const rule of rules) {
        if (!rule.selectorText || !rule.style) continue;
        // Match selectors containing :hover or :focus (skip media-query
        // wrappers which we'd need recursive walking to handle; covers 95%).
        if (!rule.selectorText.match(/:(hover|focus(?!-within|-visible))/)) continue;
        // selectorText can be comma-separated: ".a:hover, .b:hover" — split
        // and store the prefix BEFORE the pseudo so we can match elements.
        for (const sel of rule.selectorText.split(',')) {
          const m = sel.match(/\.([a-zA-Z0-9_-]+)/);  // first .class token
          if (!m) continue;
          const cls = m[1];
          const decls = {};
          for (const p of LAYOUT_PROPS) {
            const v = rule.style.getPropertyValue(p);
            if (v && !NOISE.has(v)) decls[p] = v.slice(0, 200);
          }
          if (Object.keys(decls).length) out.push({ cls, decls });
        }
      }
    }
    HOVER_RULES = out;
    return out;
  };
  const captureHover = (el) => {
    if (!el.className || typeof el.className !== 'string') return null;
    const classes = el.className.split(/\s+/).filter(Boolean);
    if (!classes.length) return null;
    const rules = buildHoverRules();
    const merged = {};
    for (const r of rules) {
      if (classes.indexOf(r.cls) >= 0) {
        Object.assign(merged, r.decls);
      }
    }
    return Object.keys(merged).length ? merged : null;
  };
  // Fix 18 — pseudo-element capture. Helper extracts a non-empty subset of
  // LAYOUT_PROPS from a pseudo computed style, plus its `content` so the
  // transpiler can emit a <span data-pseudo="before" /> with matching styles
  // when the ref draws decorations via ::before / ::after (glow rings, icon
  // dots, gradient overlays, divider lines etc.). Without this the impl is
  // missing the entire pseudo-element layer — a dominant cause of the
  // "the impl doesn't capture the overall layout" failure mode.
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
  const SVG_TAGS = new Set([
    'svg','g','defs','use','symbol','marker','clippath','clip-path',
    'mask','pattern','filter','feblend','fecolormatrix',
    'fecomposite','fegaussianblur','femerge','femergenode','feoffset',
    'feflood','fetile','feturbulence','fedropshadow','fediffuselighting',
    'fespecularlighting','femorphology','feimage','fedisplacementmap',
    'lineargradient','linear-gradient','radialgradient','radial-gradient',
    'stop',
    'path','rect','circle','ellipse','line','polyline','polygon',
    'text','textpath','tspan','title','desc','foreignobject',
  ]);
  const SVG_ATTR_KEYS = [
    'id','viewBox','xmlns','xmlns:xlink',
    'fill','stroke','stroke-width','stroke-linecap','stroke-linejoin',
    'stroke-miterlimit','stroke-dasharray','stroke-dashoffset',
    'fill-rule','fill-opacity','clip-rule','clip-path','mask','filter',
    'opacity',
    'd','points','x','y','x1','y1','x2','y2','cx','cy','r','rx','ry',
    'width','height','transform','preserveAspectRatio',
    'offset','stop-color','stop-opacity',
    'gradientTransform','gradientUnits','spreadMethod',
    'href','xlink:href','xlink:title',
    'patternUnits','patternContentUnits','patternTransform',
    'markerUnits','refX','refY','orient','overflow',
    'in','in2','result','values','operator','mode','type',
    'stdDeviation','floodColor','floodOpacity',
  ];
  const SVG_DEPTH_CAP = 30;
  const HTML_DEPTH_CAP = 10;

  const isSvgNode = (el) => {
    try {
      if (typeof SVGElement !== 'undefined' && el instanceof SVGElement) return true;
    } catch (e) { /* ignore */ }
    const tag = (el.tagName || '').toLowerCase();
    return SVG_TAGS.has(tag);
  };

  const extract = (el, depth = 0, insideSvg = false) => {
    const elIsSvg = insideSvg || isSvgNode(el);
    const cap = elIsSvg ? SVG_DEPTH_CAP : HTML_DEPTH_CAP;
    if (depth > cap) return null;
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
      children: Array.from(el.children).map(c => extract(c, depth + 1, elIsSvg)).filter(Boolean),
    };
    if (elIsSvg) out.svg = true;
    if (text) out.text = text;
    if (Object.keys(styles).length) out.styles = styles;
    // Fix 18 — pseudo styles attached to the node so the transpiler can
    // synthesize <span data-pseudo> children with matching CSS.
    const before = capturePseudo(el, '::before');
    if (before) out.before_styles = before;
    const after = capturePseudo(el, '::after');
    if (after) out.after_styles = after;
    // Fix 19 — :hover/:focus rule declarations matching this element's
    // class list, so the transpiler can emit a CSS rule that gives the
    // captured `transition` (Fix 16) something to animate to.
    const hover = captureHover(el);
    if (hover) out.hover_styles = hover;
    // Capture asset/link attrs so the transpiler can emit <img src>, <a href>,
    // <video poster>, etc. Without these the scaffold renders empty
    // placeholder boxes for every media element, which inflates section-compare
    // AE by ~700k per image-heavy section.
    const ATTR_KEYS = ['src','href','alt','poster','srcset','sizes','type','target','rel','aria-label','title','role','data-src','data-poster'];
    const keys = elIsSvg ? ATTR_KEYS.concat(SVG_ATTR_KEYS) : ATTR_KEYS;
    for (const k of keys) {
      const v = el.getAttribute ? el.getAttribute(k) : null;
      if (v && v.length < 2000) out[k] = v;
    }
    // Codex universality audit HIGH FN: SVG attr whitelist drops
    // unfamiliar icon-system attrs silently. For SVG nodes, capture
    // EVERY attribute (subject to the same length cap), then the
    // JSX emitter can apply the kebab→camel rename to whatever it
    // sees. Attrs already in keys[] above are simply overwritten
    // with the same value — idempotent.
    if (elIsSvg && el.attributes) {
      for (const a of el.attributes) {
        const nm = a.name;
        // Skip standard HTML attrs already in keys[] and React-
        // unfriendly attrs starting with `on*` (event handlers).
        if (nm.startsWith('on')) continue;
        const v = a.value;
        if (v && v.length < 2000 && !(nm in out)) {
          out[nm] = v;
        }
      }
    }
    return out;
  };
  return JSON.stringify(extract(target), null, 2);
})()
JSEOF
)

# Inject the selector — must be a JS string literal. Escape single-quotes,
# then substitute via sed (avoiding bashs parameter-expansion replacement,
# which mis-lexes single quotes inside REPL on bash 3.2).
SELECTOR_LITERAL=$(printf '%s' "$TARGET" | sed "s/'/\\\\'/g")
EVAL_JS=$(printf '%s' "$EXTRACT_JS" | sed "s|SELECTOR_PLACEHOLDER|'${SELECTOR_LITERAL}'|")

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
