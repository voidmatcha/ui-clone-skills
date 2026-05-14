#!/usr/bin/env bash
# transition-compare.sh — Compare hover/transition behavior between original and implementation
#
# Usage: bash transition-compare.sh <orig-url> <impl-url> <session> [output-dir]
#
# For each element with CSS transitions on both sites:
# 1. Captures idle state (screenshot + computedStyle)
# 2. Simulates hover (mouseenter dispatch)
# 3. Captures hover state (screenshot + computedStyle)
# 4. Diffs idle/hover computedStyle between ref and impl
# 5. Compares transition timing (duration, easing, delay)
#
# Output: <dir>/transitions/report.json
#         <dir>/transitions/{ref,impl}/{element}-{idle,hover}.png

set -euo pipefail

# Source the timeout shim so macOS gets a working `timeout` cmd even when
# coreutils isn't installed. See scripts/lib/timeout-shim.sh.
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_SHIM="$_SCRIPT_DIR/../../../scripts/lib/timeout-shim.sh"
[ -f "$_SHIM" ] && . "$_SHIM" || true

VIEW_W="${VIEW_W:-1440}"
VIEW_H="${VIEW_H:-900}"
NO_IMAGES="${NO_IMAGES:-0}"
WAIT_REF="${WAIT_REF:-8000}"
WAIT_IMPL="${WAIT_IMPL:-6000}"
TRANSITION_WAIT="${TRANSITION_WAIT:-500}"   # ms to wait after hover before screenshot
MAX_TRANSITIONS="${MAX_TRANSITIONS:-30}"    # max elements to compare
# CSS selector(s) to exclude from ref detection (e.g. third-party SDK overlays not in the clone).
# Default skips Finsweet Cookie Consent (`.fs-cc_*`) — the clone never replicates the consent SDK.
EXCLUDE_SELECTORS="${EXCLUDE_SELECTORS:-[class*=fs-cc], [id*=cookie], [class*=cookie-banner], [class*=consent]}"

ORIG_URL="${1:?Usage: transition-compare.sh <orig-url> <impl-url> <session> [output-dir]}"
IMPL_URL="${2:?Usage: transition-compare.sh <orig-url> <impl-url> <session> [output-dir]}"
SESSION="${3:?Usage: transition-compare.sh <orig-url> <impl-url> <session> [output-dir]}"
DIR="${4:-tmp/ref/visual-debug}"

# Convert relative path to absolute (Stop gate uses absolute paths, result.txt lookup breaks otherwise)
if [[ "$DIR" != /* ]]; then
  DIR="$(pwd)/$DIR"
fi

SESSION_REF="${SESSION}-tc-ref"
SESSION_IMPL="${SESSION}-tc-impl"

_TC_PY=""  # set later; declare here so cleanup_all can reference it
cleanup_all() {
  agent-browser --session "$SESSION_REF" close 2>/dev/null || true
  agent-browser --session "$SESSION_IMPL" close 2>/dev/null || true
  [ -n "$_TC_PY" ] && rm -f "$_TC_PY"
}
trap cleanup_all EXIT

mkdir -p "$DIR/transitions/ref" "$DIR/transitions/impl"

echo "═══ Transition Comparison ═══"
echo "Original: $ORIG_URL"
echo "Implementation: $IMPL_URL"
echo ""

# ── Open both sites ──
# `open` may report a navigation timeout on slow third-party sites even when the
# page eventually loads. Tolerate that — the explicit `wait` below settles state.
echo "▸ Opening both sites..."
agent-browser --session "$SESSION_REF" open "$ORIG_URL" 2>&1 | head -1 || true
agent-browser --session "$SESSION_IMPL" open "$IMPL_URL" 2>&1 | head -1 || true

agent-browser --session "$SESSION_REF" set viewport "$VIEW_W" "$VIEW_H" > /dev/null 2>&1
agent-browser --session "$SESSION_IMPL" set viewport "$VIEW_W" "$VIEW_H" > /dev/null 2>&1

agent-browser --session "$SESSION_REF" wait "$WAIT_REF" > /dev/null 2>&1
agent-browser --session "$SESSION_IMPL" wait "$WAIT_IMPL" > /dev/null 2>&1

# Remove overlays
DISMISS='(() => {
  document.querySelectorAll("[class*=popup], [class*=modal], [class*=signup]").forEach(el => {
    const s = getComputedStyle(el);
    if (s.position === "fixed" || s.position === "absolute") el.remove();
  });
  document.body.style.overflow = "";
  document.documentElement.style.overflow = "";
  return "ok";
})()'
agent-browser --session "$SESSION_REF" eval "$DISMISS" 2>&1 > /dev/null
agent-browser --session "$SESSION_IMPL" eval "$DISMISS" 2>&1 > /dev/null

# Hide images to reduce AE noise from dynamic thumbnails
HIDE_IMAGES_JS='(() => {
  const style = document.createElement("style");
  style.id = "__no_images__";
  style.textContent = "img, picture, video, iframe { visibility: hidden !important; }";
  document.head.appendChild(style);
  document.querySelectorAll("*").forEach(el => {
    if (el.style && el.style.backgroundImage) el.style.backgroundImage = "none";
  });
})()'

# Hide <canvas> elements (WebGL/Three.js/etc.) — their content is dynamic per-frame.
HIDE_CANVAS_JS='(() => {
  const style = document.createElement("style");
  style.id = "__no_canvas__";
  style.textContent = "canvas { visibility: hidden !important; }";
  document.head.appendChild(style);
})()'

if [ "$NO_IMAGES" = "1" ]; then
  echo "▸ Hiding images (NO_IMAGES=1)..."
  agent-browser --session "$SESSION_REF" eval "$HIDE_IMAGES_JS" 2>/dev/null || true
  agent-browser --session "$SESSION_IMPL" eval "$HIDE_IMAGES_JS" 2>/dev/null || true
fi

if [ "${NO_CANVAS:-0}" = "1" ]; then
  echo "▸ Hiding canvases (NO_CANVAS=1)..."
  agent-browser --session "$SESSION_REF" eval "$HIDE_CANVAS_JS" 2>/dev/null || true
  agent-browser --session "$SESSION_IMPL" eval "$HIDE_CANVAS_JS" 2>/dev/null || true
fi

# ── Step 1: Find elements with transitions on the original ──
echo "▸ Detecting transition elements..."

DETECT_TRANSITIONS='(() => {
  const results = [];
  const seen = new Set();
  const allEls = document.querySelectorAll("a, button, [role=button], img, .product-card, [class*=card], [class*=link], [class*=hover], [class*=btn], nav a, footer a, h1, h2, h3");
  const EXCLUDE = ${EXCLUDE_SELECTORS_JSON};

  allEls.forEach(el => {
    if (EXCLUDE && el.closest(EXCLUDE)) return;
    const cs = getComputedStyle(el);
    const hasTrans = cs.transitionDuration !== "0s" && cs.transitionProperty !== "none";
    const hasAnim = cs.animationName !== "none";

    if (!hasTrans && !hasAnim) return;

    const rect = el.getBoundingClientRect();
    if (rect.width < 10 || rect.height < 10) return;
    if (rect.top + window.scrollY > document.documentElement.scrollHeight) return;

    // Build a unique selector
    let selector = "";
    if (el.id) {
      selector = "#" + el.id;
    } else if (el.className && typeof el.className === "string") {
      const cls = el.className.split(" ").filter(c => c && !c.includes("hover") && c.length < 40)[0];
      if (cls) selector = "." + cls;
    }
    if (!selector) {
      selector = el.tagName.toLowerCase();
      if (el.parentElement) {
        const siblings = [...el.parentElement.children].filter(c => c.tagName === el.tagName);
        if (siblings.length > 1) {
          const idx = siblings.indexOf(el);
          selector += `:nth-child(${idx + 1})`;
        }
      }
    }

    if (seen.has(selector)) return;
    seen.add(selector);

    // Get transition properties
    const transProps = cs.transitionProperty.split(",").map(p => p.trim());
    const transDurs = cs.transitionDuration.split(",").map(d => d.trim());
    const transEase = cs.transitionTimingFunction.split(",").map(e => e.trim());

    results.push({
      selector,
      tag: el.tagName.toLowerCase(),
      text: (el.textContent || "").trim().substring(0, 40),
      rect: {
        top: Math.round(rect.top + window.scrollY),
        left: Math.round(rect.left),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      },
      transition: {
        properties: transProps,
        durations: transDurs,
        easings: transEase,
      },
      idleStyle: {
        opacity: cs.opacity,
        transform: cs.transform,
        backgroundColor: cs.backgroundColor,
        color: cs.color,
        scale: cs.scale || "none",
        filter: cs.filter,
        boxShadow: cs.boxShadow,
      },
    });
  });

  return results.slice(0, ${MAX_TRANSITIONS});
})()'

# Substitute ${MAX_TRANSITIONS} and ${EXCLUDE_SELECTORS_JSON} into the JS body
# (single-quoted heredoc above blocks bash expansion).
# EXCLUDE_SELECTORS is JSON-encoded so it embeds as a JS string literal safely —
# matches the v0.4.2 hardening discipline that JSON-encodes selectors before eval.
EXCLUDE_SELECTORS_JSON=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$EXCLUDE_SELECTORS")
DETECT_TRANSITIONS="${DETECT_TRANSITIONS/\$\{MAX_TRANSITIONS\}/$MAX_TRANSITIONS}"
DETECT_TRANSITIONS="${DETECT_TRANSITIONS/\$\{EXCLUDE_SELECTORS_JSON\}/$EXCLUDE_SELECTORS_JSON}"

agent-browser --session "$SESSION_REF" eval "$DETECT_TRANSITIONS" > "$DIR/transitions/ref-elements.json" 2>&1
agent-browser --session "$SESSION_IMPL" eval "$DETECT_TRANSITIONS" > "$DIR/transitions/impl-elements.json" 2>&1

REF_TRANS=$(python3 -c "import json; print(len(json.loads(open('$DIR/transitions/ref-elements.json').read())))" 2>/dev/null || echo "0")
IMPL_TRANS=$(python3 -c "import json; print(len(json.loads(open('$DIR/transitions/impl-elements.json').read())))" 2>/dev/null || echo "0")

echo "  Ref:  $REF_TRANS transition elements"
echo "  Impl: $IMPL_TRANS transition elements"

if [ "$REF_TRANS" -eq 0 ]; then
  echo ""
  echo "  ℹ No transition elements detected on the original site."
  echo "  Possible causes:"
  echo "    1. All transitions are JS-driven (GSAP) — not in getComputedStyle at rest"
  echo "    2. Page not scrolled — transitions may be off-screen"
  echo "    3. Transitions only exist on hover (GSAP mouseenter), not in base CSS"
  echo "  If transitions exist, add custom selectors: bash transition-compare.sh ... then edit DETECT_TRANSITIONS"
  echo ""
  echo "═══ Transition Compare Complete ═══"
  echo "  0 elements — skipped"
  exit 0
fi

# ── Step 2: For each ref transition element, capture idle + hover states ──
echo "▸ Capturing idle + hover states..."

# Write hover capture script to a tmpfile — avoids bash quoting issues when
# embedding Python code with single-quotes inside a double-quoted -c argument.
_TC_PY=$(mktemp /tmp/tc-hover-XXXXXX.py)

cat > "$_TC_PY" << 'PYEOF'
import json, re, subprocess, sys, time, os
from pathlib import Path

SESSION_REF     = os.environ["_TC_SESSION_REF"]
SESSION_IMPL    = os.environ["_TC_SESSION_IMPL"]
DIR             = os.environ["_TC_DIR"]
TRANSITION_WAIT = float(os.environ.get("TRANSITION_WAIT", "500")) / 1000  # ms → seconds
SCROLL_WAIT     = float(os.environ.get("_TC_SCROLL_WAIT", "300")) / 1000

# Strip every char outside [A-Za-z0-9._-] so selector-derived filenames cannot
# escape into surrounding shell or path components. Also collapse to ≤30 chars.
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(selector: str) -> str:
    s = selector.replace("#", "id-").replace(".", "cls-")
    s = _SAFE_NAME_RE.sub("_", s)
    return s[:30] or "el"


def _ab_eval(session: str, js: str) -> subprocess.CompletedProcess:
    """Run agent-browser eval with argv (shell=False) — safe against selector injection."""
    return subprocess.run(
        ["agent-browser", "--session", session, "eval", js],
        capture_output=True,
        text=True,
    )


def capture_hover_state(session, elements_file, side, out_dir):
    elements = json.loads(Path(elements_file).read_text())
    results = []

    for el in elements[:20]:  # Cap at 20 elements
        selector = el["selector"]
        safe_name = _safe_name(selector)
        # JSON-encode the selector so it embeds safely as a JS string literal —
        # quotes, backslashes, and unicode all survive without breaking the JS.
        sel_lit = json.dumps(selector)

        # Scroll to element
        _ab_eval(session, (
            "(() => {"
            f"const el = document.querySelector({sel_lit});"
            "if (!el) return 'not found';"
            "el.scrollIntoView({ block: 'center' });"
            "return 'scrolled';"
            "})()"
        ))
        time.sleep(SCROLL_WAIT)

        idle_path = Path(out_dir) / f"{safe_name}-idle.png"
        subprocess.run(
            ["agent-browser", "--session", session, "screenshot", str(idle_path)],
            capture_output=True,
        )

        _ab_eval(session, (
            "(() => {"
            f"const el = document.querySelector({sel_lit});"
            "if (!el) return 'not found';"
            "el.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));"
            "el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));"
            "el.focus?.();"
            "return 'hovered';"
            "})()"
        ))
        time.sleep(TRANSITION_WAIT)  # Wait for transition (TRANSITION_WAIT env var)

        hover_path = Path(out_dir) / f"{safe_name}-hover.png"
        subprocess.run(
            ["agent-browser", "--session", session, "screenshot", str(hover_path)],
            capture_output=True,
        )

        result = _ab_eval(session, (
            "(() => {"
            f"const el = document.querySelector({sel_lit});"
            "if (!el) return JSON.stringify({ error: 'not found' });"
            "const cs = getComputedStyle(el);"
            "return JSON.stringify({"
            "opacity: cs.opacity,"
            "transform: cs.transform,"
            "backgroundColor: cs.backgroundColor,"
            "color: cs.color,"
            "scale: cs.scale || 'none',"
            "filter: cs.filter,"
            "boxShadow: cs.boxShadow,"
            "borderColor: cs.borderColor,"
            "});"
            "})()"
        ))
        hover_style = result.stdout.strip().strip('"')

        _ab_eval(session, (
            "(() => {"
            f"const el = document.querySelector({sel_lit});"
            "if (!el) return 'not found';"
            "el.dispatchEvent(new MouseEvent('mouseleave', { bubbles: true }));"
            "el.dispatchEvent(new MouseEvent('mouseout', { bubbles: true }));"
            "el.blur?.();"
            "return 'left';"
            "})()"
        ))
        time.sleep(SCROLL_WAIT)

        try:
            hs = json.loads(hover_style.replace("\\\\", "\\\\\\\\")) if hover_style else {}
        except json.JSONDecodeError:
            hs = {}

        results.append({
            "selector": selector,
            "name": safe_name,
            "hoverStyle": hs,
        })

        sys.stdout.write(f"  ✓ {side}/{safe_name}\n")
        sys.stdout.flush()

    return results

ref_results  = capture_hover_state(SESSION_REF,  f"{DIR}/transitions/ref-elements.json",  "ref",  f"{DIR}/transitions/ref")
impl_results = capture_hover_state(SESSION_IMPL, f"{DIR}/transitions/impl-elements.json", "impl", f"{DIR}/transitions/impl")

with open(f"{DIR}/transitions/hover-states.json", "w") as f:
    json.dump({"ref": ref_results, "impl": impl_results}, f, indent=2)
PYEOF

_TC_SESSION_REF="$SESSION_REF" _TC_SESSION_IMPL="$SESSION_IMPL" _TC_DIR="$DIR" \
  TRANSITION_WAIT="$TRANSITION_WAIT" \
  python3 "$_TC_PY" 2>&1

# ── Step 3: Diff transitions ──
echo ""
echo "▸ Comparing transitions..."

python3 -c "
import json

ref_els = json.loads(open('$DIR/transitions/ref-elements.json').read())
impl_els = json.loads(open('$DIR/transitions/impl-elements.json').read())
hover_states = json.loads(open('$DIR/transitions/hover-states.json').read())

# Match by selector similarity
def find_impl_match(ref_sel, impl_list):
    # Exact match
    for im in impl_list:
        if im['selector'] == ref_sel:
            return im
    # Partial match (same class name)
    ref_cls = ref_sel.replace('.', '').replace('#', '')
    for im in impl_list:
        im_cls = im['selector'].replace('.', '').replace('#', '')
        if ref_cls and im_cls and (ref_cls in im_cls or im_cls in ref_cls):
            return im
    return None

report = []
pass_count = 0
fail_count = 0

for ref_el in ref_els:
    impl_el = find_impl_match(ref_el['selector'], impl_els)

    entry = {
        'selector': ref_el['selector'],
        'text': ref_el.get('text', ''),
        'issues': [],
    }

    if not impl_el:
        entry['issues'].append('MISSING: no matching element in impl')
        entry['status'] = 'FAIL'
        fail_count += 1
        report.append(entry)
        continue

    # Compare transition timing
    ref_durs = ref_el['transition']['durations']
    impl_durs = impl_el['transition']['durations']
    ref_ease = ref_el['transition']['easings']
    impl_ease = impl_el['transition']['easings']

    if ref_durs != impl_durs:
        entry['issues'].append(f'DURATION_MISMATCH: ref={ref_durs}, impl={impl_durs}')

    if ref_ease != impl_ease:
        entry['issues'].append(f'EASING_MISMATCH: ref={ref_ease}, impl={impl_ease}')

    # Compare idle styles
    def normalize_transform(v):
        # matrix(1, 0, 0, 1, 0, 0) is the identity transform — semantically equivalent to "none"
        if v.replace(' ', '') == 'matrix(1,0,0,1,0,0)':
            return 'none'
        return v

    for prop in ['opacity', 'transform', 'backgroundColor', 'color']:
        ref_val = ref_el['idleStyle'].get(prop, '')
        impl_val = impl_el['idleStyle'].get(prop, '')
        if prop == 'transform':
            ref_val = normalize_transform(ref_val)
            impl_val = normalize_transform(impl_val)
        if ref_val != impl_val and ref_val and impl_val:
            # Allow minor color differences
            if prop in ['backgroundColor', 'color']:
                if ref_val.replace(' ', '') == impl_val.replace(' ', ''):
                    continue
            entry['issues'].append(f'IDLE_{prop.upper()}_MISMATCH: ref={ref_val}, impl={impl_val}')

    # Compare hover styles from captured states
    ref_hover = next((r for r in hover_states.get('ref', []) if r['selector'] == ref_el['selector']), None)
    impl_hover = next((r for r in hover_states.get('impl', []) if r['selector'] == (impl_el or {}).get('selector', '')), None)

    if ref_hover and impl_hover and ref_hover.get('hoverStyle') and impl_hover.get('hoverStyle'):
        for prop in ['opacity', 'transform', 'scale', 'backgroundColor', 'color']:
            ref_hv = ref_hover['hoverStyle'].get(prop, '')
            impl_hv = impl_hover['hoverStyle'].get(prop, '')
            ref_idle = ref_el['idleStyle'].get(prop, '')

            # Check if property changes on hover in ref but not in impl
            if ref_hv and ref_idle and ref_hv != ref_idle:
                if impl_hv == impl_el['idleStyle'].get(prop, ''):
                    entry['issues'].append(f'HOVER_{prop.upper()}_NOT_APPLIED: ref changes {prop} on hover ({ref_idle} -> {ref_hv}), impl stays same')

    if entry['issues']:
        entry['status'] = 'FAIL'
        fail_count += 1
    else:
        entry['status'] = 'PASS'
        pass_count += 1

    report.append(entry)

json.dump(report, open('$DIR/transitions/report.json', 'w'), indent=2)

# Print summary
print('')
print('| Element | Status | Issues |')
print('|---------|--------|--------|')
for r in report:
    issues = '; '.join(r['issues'][:2]) if r['issues'] else '—'
    print(f'| {r[\"selector\"][:30]} | {r[\"status\"]} | {issues[:60]} |')

print(f'')
print(f'**Result: {pass_count} PASS, {fail_count} FAIL**')
" 2>&1

echo ""
echo "═══ Transition Compare Complete ═══"
echo "  Report: $DIR/transitions/report.json"
echo "  States: $DIR/transitions/{ref,impl}/"
