#!/usr/bin/env bash
# hover-tree-diff.sh — Per-element hover/transition diff between ref and impl
#
# Walks every visible impl element with hover-capable transitions
# (transitionDuration > 0, cursor:pointer, or interactive tag), pairs each
# with the ref element at the same screen-center via elementFromPoint, then
# for each pair:
#   1. Capture idle computed style (transition meta + visual props)
#   2. Trigger CDP-level :hover (NOT synthetic events — those don't fire :hover)
#   3. Wait for transition to settle
#   4. Capture hover computed style
#   5. Reset
#   6. Diff: timing (property/duration/easing/delay) + idle→hover delta per side
#
# Catches:
#   - Hover style not applied at all (impl missing :hover rule)
#   - Different easing/duration (stutters vs smooth glide)
#   - Different delta (ref opacity 1→.5 vs impl 1→.7)
#
# Usage: bash hover-tree-diff.sh <session> <orig-url> <impl-url> [out-dir]
#
# Env:
#   VIEW_W=1440 VIEW_H=900    Viewport
#   WAIT_MS=4000              Settle time after open
#   MIN_SIZE=16               Skip elements smaller than NxN px
#   MAX_ELEMENTS=40           Cap candidates (hover loop is slow)
#   PAIR_TOLERANCE=10         Max center-distance for valid pair (px)
#   HOVER_WAIT=600            ms after hover before capturing style
#   HOVER_MAX_WAIT=3000       Per-element cap for declared transition settling
#   RESET_WAIT=200            ms after un-hover before next element
#
# Output:
#   <dir>/hover-tree-diff.md   — Severity-sorted markdown
#   <dir>/hover-tree-diff.json — Raw pair data
# Exit 0 if no critical/major mismatches; 1 otherwise.

set -uo pipefail

if ! command -v agent-browser &>/dev/null; then
  echo "ERROR: agent-browser not found"; exit 2
fi
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found"; exit 2
fi

SESSION="${1:?Usage: hover-tree-diff.sh <session> <orig-url> <impl-url> [out-dir]}"
ORIG_URL="${2:?Missing orig-url}"
IMPL_URL="${3:?Missing impl-url}"
OUT_DIR="${4:-tmp/hover-tree-diff}"

VIEW_W="${VIEW_W:-1440}"
VIEW_H="${VIEW_H:-900}"
WAIT_MS="${WAIT_MS:-4000}"
MIN_SIZE="${MIN_SIZE:-16}"
MAX_ELEMENTS="${MAX_ELEMENTS:-40}"
PAIR_TOLERANCE="${PAIR_TOLERANCE:-10}"
HOVER_WAIT="${HOVER_WAIT:-600}"
HOVER_MAX_WAIT="${HOVER_MAX_WAIT:-3000}"
RESET_WAIT="${RESET_WAIT:-200}"
SWIPER_SETTLE_MS="${SWIPER_SETTLE_MS:-100}"

mkdir -p "$OUT_DIR"

REF_SESS="${SESSION}-htd-ref"
IMPL_SESS="${SESSION}-htd-impl"
TMP_BASE="${TMPDIR:-/tmp}"

TMP_IMPL=$(mktemp "$TMP_BASE/htd-impl.XXXXXX")
TMP_REF=$(mktemp "$TMP_BASE/htd-ref.XXXXXX")
TMP_SWIPER_REF=$(mktemp "$TMP_BASE/htd-swiper-ref.XXXXXX")
TMP_SWIPER_IMPL=$(mktemp "$TMP_BASE/htd-swiper-impl.XXXXXX")

cleanup() {
  agent-browser --session "$REF_SESS" close >/dev/null 2>&1 || true
  agent-browser --session "$IMPL_SESS" close >/dev/null 2>&1 || true
  rm -f "$TMP_IMPL" "$TMP_REF" "$TMP_SWIPER_REF" "$TMP_SWIPER_IMPL" \
    "${_HTD_PY:-}" "${TMP_IMPL_HOVER:-}" "${TMP_REF_HOVER:-}" "${TMP_REF_PAIRED:-}"
}
trap cleanup EXIT

echo "═══ Hover Tree Diff (per-element :hover pairing) ═══"
echo "  orig: $ORIG_URL"
echo "  impl: $IMPL_URL"
echo "  viewport: ${VIEW_W}x${VIEW_H}, max: $MAX_ELEMENTS, hover wait: ${HOVER_WAIT}ms"
echo ""

# ── Open both sessions ──
agent-browser --session "$REF_SESS" open "$ORIG_URL" >/dev/null 2>&1
agent-browser --session "$IMPL_SESS" open "$IMPL_URL" >/dev/null 2>&1
agent-browser --session "$REF_SESS"  set viewport "$VIEW_W" "$VIEW_H" >/dev/null 2>&1 || true
agent-browser --session "$IMPL_SESS" set viewport "$VIEW_W" "$VIEW_H" >/dev/null 2>&1 || true
agent-browser --session "$REF_SESS"  wait "$WAIT_MS" >/dev/null 2>&1
agent-browser --session "$IMPL_SESS" wait "$WAIT_MS" >/dev/null 2>&1

# Swiper writes transitionDuration inline while autoplay is advancing. The ref
# and impl idle walks cannot be phase-synchronized by a wall-clock wait (the
# default 4s floor itself can coincide with a 4s autoplay delay), so pin every
# live Swiper to the same deterministic idle state before candidate discovery.
SWIPER_STABILIZE_JS=$(cat <<'JSEOF'
(() => {
  const marker = 'htd-swiper-stabilize-v1';
  const seen = new Set();
  const rows = [];
  for (const el of document.querySelectorAll('.swiper')) {
    const swiper = el.swiper;
    if (!swiper || swiper.destroyed || seen.has(swiper)) continue;
    seen.add(swiper);
    const row = {ok: false, loop: Boolean(swiper.params && swiper.params.loop)};
    try {
      if (swiper.autoplay && typeof swiper.autoplay.stop === 'function') {
        swiper.autoplay.stop();
      }
      if (row.loop && typeof swiper.slideToLoop === 'function') {
        swiper.slideToLoop(0, 0, false);
      } else if (typeof swiper.slideTo === 'function') {
        swiper.slideTo(0, 0, false);
      } else {
        throw new Error('missing slideTo API');
      }
      if (typeof swiper.setTransition !== 'function') {
        throw new Error('missing setTransition API');
      }
      swiper.setTransition(0);
      el.setAttribute('data-htd-swiper-stabilized', '');
      row.ok = true;
    } catch (error) {
      row.error = String(error && error.message ? error.message : error);
    }
    rows.push(row);
  }
  return JSON.stringify({
    marker,
    ok: rows.every(row => row.ok),
    count: rows.length,
    rows,
  });
})()
JSEOF
)

SWIPER_VERIFY_JS=$(cat <<'JSEOF'
(() => {
  const marker = 'htd-swiper-verify-v1';
  const zeroDuration = value => String(value || '').split(',').every(part => {
    const parsed = Number.parseFloat(part);
    return Number.isFinite(parsed) && parsed === 0;
  });
  const seen = new Set();
  const rows = [];
  for (const el of document.querySelectorAll('.swiper')) {
    const swiper = el.swiper;
    if (!swiper || swiper.destroyed || seen.has(swiper)) continue;
    seen.add(swiper);
    const loop = Boolean(swiper.params && swiper.params.loop);
    const currentIndex = loop && Number.isFinite(Number(swiper.realIndex))
      ? Number(swiper.realIndex)
      : Number(swiper.activeIndex);
    const duration = swiper.wrapperEl
      ? getComputedStyle(swiper.wrapperEl).transitionDuration
      : '';
    rows.push({
      ok: el.hasAttribute('data-htd-swiper-stabilized')
        && (!swiper.autoplay || swiper.autoplay.running !== true)
        && swiper.animating !== true
        && currentIndex === 0
        && zeroDuration(duration),
      currentIndex,
      duration,
      animating: Boolean(swiper.animating),
      autoplayRunning: Boolean(swiper.autoplay && swiper.autoplay.running),
    });
  }
  const orphaned = Array.from(
    document.querySelectorAll('[data-htd-swiper-stabilized]')
  ).filter(el => !el.swiper || el.swiper.destroyed).length;
  return JSON.stringify({
    marker,
    ok: orphaned === 0 && rows.every(row => row.ok),
    count: rows.length,
    orphaned,
    rows,
  });
})()
JSEOF
)

validate_swiper_receipt() {
  local receipt_file="$1"
  local expected_marker="$2"
  local side="$3"
  python3 - "$receipt_file" "$expected_marker" "$side" <<'PYEOF'
import json
import sys

path, expected_marker, side = sys.argv[1:]
try:
    raw = open(path, encoding="utf-8").read().strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = json.loads(raw)
    payload = json.loads(raw)
except Exception as exc:
    print(f"ERROR: {side} Swiper stabilization receipt is invalid: {exc}", file=sys.stderr)
    raise SystemExit(1)
if payload.get("marker") != expected_marker or payload.get("ok") is not True:
    print(
        f"ERROR: {side} Swiper stabilization failed: "
        + json.dumps(payload, ensure_ascii=False),
        file=sys.stderr,
    )
    raise SystemExit(1)
PYEOF
}

echo "  ▸ Stabilizing autonomous Swipers..."
if ! agent-browser --session "$REF_SESS" eval "$SWIPER_STABILIZE_JS" >"$TMP_SWIPER_REF" 2>&1 \
  || ! validate_swiper_receipt "$TMP_SWIPER_REF" "htd-swiper-stabilize-v1" "ref"; then
  exit 2
fi
if ! agent-browser --session "$IMPL_SESS" eval "$SWIPER_STABILIZE_JS" >"$TMP_SWIPER_IMPL" 2>&1 \
  || ! validate_swiper_receipt "$TMP_SWIPER_IMPL" "htd-swiper-stabilize-v1" "impl"; then
  exit 2
fi
agent-browser --session "$REF_SESS" wait "$SWIPER_SETTLE_MS" >/dev/null 2>&1
agent-browser --session "$IMPL_SESS" wait "$SWIPER_SETTLE_MS" >/dev/null 2>&1
if ! agent-browser --session "$REF_SESS" eval "$SWIPER_VERIFY_JS" >"$TMP_SWIPER_REF" 2>&1 \
  || ! validate_swiper_receipt "$TMP_SWIPER_REF" "htd-swiper-verify-v1" "ref"; then
  exit 2
fi
if ! agent-browser --session "$IMPL_SESS" eval "$SWIPER_VERIFY_JS" >"$TMP_SWIPER_IMPL" 2>&1 \
  || ! validate_swiper_receipt "$TMP_SWIPER_IMPL" "htd-swiper-verify-v1" "impl"; then
  exit 2
fi

# ── Step 1: walk impl tree, collect hover candidates ──
echo "  ▸ Walking impl for hover candidates..."
WALK_JS=$(cat <<JSEOF
(() => {
  const SKIP_TAGS = new Set(['SCRIPT','STYLE','META','LINK','HEAD','TITLE','NOSCRIPT','BR','HR']);
  const INTERACTIVE = new Set(['A','BUTTON','INPUT','SELECT','TEXTAREA','LABEL','SUMMARY']);
  const minSize = ${MIN_SIZE};
  const maxN    = ${MAX_ELEMENTS};
  const visualProps = ['color','backgroundColor','opacity','transform','filter',
                       'borderTopColor','borderBottomColor','textDecorationLine',
                       'textDecorationColor','fontStyle','fontWeight','letterSpacing',
                       'boxShadow','scale','translate','rotate'];
  const transProps = ['transitionProperty','transitionDuration','transitionTimingFunction','transitionDelay'];
  const out = [];
  const all = document.querySelectorAll('body *');
  for (const el of all) {
    if (SKIP_TAGS.has(el.tagName)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < minSize || r.height < minSize) continue;
    if (r.bottom < 0 || r.top > window.innerHeight) continue;
    if (r.right  < 0 || r.left > window.innerWidth)  continue;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) === 0) continue;

    // Hover candidate filter
    const dur = s.transitionDuration || '0s';
    const hasTrans = dur !== '0s' && dur !== '0s, 0s' && s.transitionProperty !== 'none' && s.transitionProperty !== 'all 0s ease 0s';
    const cursor = s.cursor;
    const isInteractive = INTERACTIVE.has(el.tagName) || el.getAttribute('role') === 'button' || cursor === 'pointer';
    if (!hasTrans && !isInteractive) continue;

    const cx = Math.max(1, Math.min(window.innerWidth  - 1, r.left + r.width  / 2));
    const cy = Math.max(1, Math.min(window.innerHeight - 1, r.top  + r.height / 2));
    const txt = (el.textContent || '').trim().replace(/\s+/g,' ').slice(0, 30);
    const idle = {};
    visualProps.forEach(p => idle[p] = s[p]);
    const trans = {};
    transProps.forEach(p => trans[p] = s[p]);
    out.push({
      target: el,
      tag: el.tagName,
      cls: (el.className && el.className.toString) ? el.className.toString().slice(0, 60) : '',
      txt,
      x: +cx.toFixed(1), y: +cy.toFixed(1),
      w: +r.width.toFixed(1), h: +r.height.toFixed(1),
      area: +(r.width * r.height).toFixed(0),
      cursor,
      hasTrans,
      idle,
      trans,
    });
  }
  // Prefer transition-having elements; tie-break by area
  out.sort((a,b) => (b.hasTrans - a.hasTrans) * 1e9 + (b.area - a.area));
  const selected = out.slice(0, maxN);
  selected.forEach((entry, index) => {
    entry.target.setAttribute('data-htd-target-' + index, '');
    delete entry.target;
  });
  return JSON.stringify(selected);
})()
JSEOF
)
agent-browser --session "$IMPL_SESS" eval "$WALK_JS" > "$TMP_IMPL" 2>&1
if [ ! -s "$TMP_IMPL" ]; then
  echo "ERROR: impl walk returned empty"; exit 2
fi

CANDIDATE_COUNT=$(python3 -c "
import json
with open('$TMP_IMPL') as f: raw = f.read().strip()
if raw.startswith('\"'): raw = json.loads(raw)
print(len(json.loads(raw)))
" 2>/dev/null || echo "0")
echo "  ▸ Hover candidates: $CANDIDATE_COUNT"

if [ "$CANDIDATE_COUNT" = "0" ]; then
  # Zero impl candidates is only benign when the reference has none either.
  # Otherwise nothing was compared, which is the same unmeasured-pass that the
  # all_unpaired branch below rejects. Walking the ref here is safe: both
  # outcomes exit, so the attributes this eval sets cannot reach the later
  # pairing pass.
  TMP_REF_WALK=$(mktemp "$TMP_BASE/htd-ref-walk.XXXXXX")
  agent-browser --session "$REF_SESS" eval "$WALK_JS" > "$TMP_REF_WALK" 2>&1
  if [ ! -s "$TMP_REF_WALK" ]; then
    echo "ERROR: ref walk returned empty"; rm -f "$TMP_REF_WALK"; exit 2
  fi
  REF_CANDIDATE_COUNT=$(python3 -c "
import json
with open('$TMP_REF_WALK') as f: raw = f.read().strip()
if raw.startswith('\"'): raw = json.loads(raw)
print(len(json.loads(raw)))
" 2>/dev/null || echo "unknown")
  rm -f "$TMP_REF_WALK"
  if [ "$REF_CANDIDATE_COUNT" = "unknown" ]; then
    echo "ERROR: ref walk output was not parseable"; exit 2
  fi
  if [ "$REF_CANDIDATE_COUNT" != "0" ]; then
    echo "❌ FAIL hover-tree-diff: ref exposes $REF_CANDIDATE_COUNT hover candidate(s) but impl exposes none, so no hover comparison was measured."
    exit 1
  fi
  echo "  No hover candidates in ref or impl. Exiting."
  exit 0
fi

# ── Step 2: capture hover state for each impl candidate ──
# JS helper: tag element at xy, return its tag/cls; we hover via CDP using attribute selector
echo "  ▸ Capturing impl hover states (CDP-level :hover)..."

_HTD_PY=$(mktemp "$TMP_BASE/htd-hover.XXXXXX")
cat > "$_HTD_PY" << 'PYEOF'
import json, math, subprocess, sys, time, os

SESSION  = os.environ["_HTD_SESSION"]
SRC_FILE = os.environ["_HTD_SRC"]
DST_FILE = os.environ["_HTD_DST"]
HOVER_WAIT = float(os.environ.get("HOVER_WAIT", "600")) / 1000
HOVER_MAX_WAIT = max(
    HOVER_WAIT,
    float(os.environ.get("HOVER_MAX_WAIT", "3000")) / 1000,
)
RESET_WAIT = float(os.environ.get("RESET_WAIT", "200")) / 1000
AGENT_TIMEOUT = float(os.environ.get("HTD_AGENT_TIMEOUT", "20"))
SETTLE_MARGIN = 0.05

VISUAL_PROPS = ['color','backgroundColor','opacity','transform','filter',
                'borderTopColor','borderBottomColor','textDecorationLine',
                'textDecorationColor','fontStyle','fontWeight','letterSpacing',
                'boxShadow','scale','translate','rotate']

def parse(raw):
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        return json.loads(json.loads(raw))
    return json.loads(raw)

def css_time_list(value):
    values = []
    for token in str(value or "").split(","):
        token = token.strip().lower()
        try:
            if token.endswith("ms"):
                seconds = float(token[:-2]) / 1000
            elif token.endswith("s"):
                seconds = float(token[:-1])
            else:
                continue
        except ValueError:
            continue
        if math.isfinite(seconds):
            values.append(seconds)
    return values or [0.0]

def declared_settle_wait(element):
    transition = element.get("trans") or {}
    durations = [
        max(0.0, value)
        for value in css_time_list(transition.get("transitionDuration"))
    ]
    delays = css_time_list(transition.get("transitionDelay"))
    item_count = max(len(durations), len(delays))
    transition_end = max(
        max(0.0, durations[index % len(durations)] + delays[index % len(delays)])
        for index in range(item_count)
    )
    return transition_end + SETTLE_MARGIN if transition_end else 0.0

with open(SRC_FILE) as f:
    elements = parse(f.read())

def _agent_run(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=AGENT_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            args,
            124,
            stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
            stderr=f"timeout after {AGENT_TIMEOUT}s",
        )

def br_eval(js):
    return _agent_run(["agent-browser", "--session", SESSION, "eval", js]).stdout.strip()

def br_hover(sel):
    return _agent_run(["agent-browser", "--session", SESSION, "hover", sel])

def br_mouse(x, y):
    return _agent_run(
        [
            "agent-browser",
            "--session",
            SESSION,
            "mouse",
            "move",
            str(int(round(x))),
            str(int(round(y))),
        ]
    )

# The walk/pair phases assign one unique marker per candidate. Re-resolving the
# element from its center point here lands on the deepest child (for example a
# <span> inside a <button>) and compares that child's hover style against the
# parent's idle style. Always target the exact marked element instead.
EXISTS_JS = """(() => Boolean(
  document.querySelector('[data-htd-target-%d]')
))()"""

# Playwright/agent-browser hover defaults to the bounding-box center. For CJK
# inline text that point can fall between glyphs, where elementFromPoint sees a
# container behind the link and :hover never activates. Find a genuinely
# hittable pixel inside the target instead.
HIT_POINT_JS = """(() => {
  const i = %d;
  const el = document.querySelector('[data-htd-target-' + i + ']');
  if (!el) return JSON.stringify({found: false});
  const r = el.getBoundingClientRect();
  const stepX = Math.max(1, r.width / 24);
  const stepY = Math.max(1, r.height / 8);
  for (let y = r.top + 1; y < r.bottom - 0.5; y += stepY) {
    for (let x = r.left + 1; x < r.right - 0.5; x += stepX) {
      const hit = document.elementFromPoint(x, y);
      if (hit === el || (hit && el.contains(hit))) {
        return JSON.stringify({found: true, x, y});
      }
    }
  }
  return JSON.stringify({found: false});
})()"""

# Capture hover-state computed style for marked element
CAP_JS = """(() => {
  const i = %d;
  const el = document.querySelector('[data-htd-target-' + i + ']');
  if (!el) return JSON.stringify({miss: true});
  const s = getComputedStyle(el);
  const out = {__hovered: el.matches(':hover')};
  %s
  return JSON.stringify(out);
})()"""
prop_lines = "\n  ".join([f"out['{p}'] = s['{p}'];" for p in VISUAL_PROPS])

# Reset: remove attribute and hover off-screen
RESET_JS = """(() => {
  document.querySelectorAll('*').forEach(el => {
    for (const attr of Array.from(el.attributes)) {
      if (attr.name.startsWith('data-htd-target-')) {
        el.removeAttribute(attr.name);
      }
    }
  });
  return 'ok';
})()"""

results = []
for i, el in enumerate(elements):
    exists = br_eval(EXISTS_JS % i)
    if "true" not in exists.lower():
        results.append({"i": i, "miss": True})
        continue

    # Hover via a real mouse move to a point that hit-tests to this exact
    # element (or one of its descendants).
    point_raw = br_eval(HIT_POINT_JS % i)
    try:
        if point_raw.startswith('"') and point_raw.endswith('"'):
            point_raw = json.loads(point_raw)
        point = json.loads(point_raw) if isinstance(point_raw, str) else point_raw
    except Exception:
        point = {"found": False}
    if not point.get("found"):
        results.append({"i": i, "miss": True, "reason": "no-hittable-point"})
        continue
    br_mouse(float(point["x"]), float(point["y"]))
    required_wait = declared_settle_wait(el)
    observation_wait = min(max(HOVER_WAIT, required_wait), HOVER_MAX_WAIT)
    time.sleep(observation_wait)

    # Capture style
    cap = br_eval(CAP_JS % (i, prop_lines))
    try:
        if cap.startswith('"') and cap.endswith('"'):
            cap = json.loads(cap)
        hover_style = json.loads(cap) if isinstance(cap, str) else cap
    except Exception:
        hover_style = {"err": "parse"}

    # Move hover off element (hover body or off-screen)
    br_hover("body")
    time.sleep(RESET_WAIT)

    results.append({
        "i": i,
        "hover": hover_style,
        "observation": {
            "wait_ms": round(observation_wait * 1000, 3),
            "required_ms": round(required_wait * 1000, 3),
            "settled": observation_wait + 0.001 >= required_wait,
        },
    })
    if (i + 1) % 5 == 0:
        sys.stdout.write(f"    ✓ {i + 1}/{len(elements)}\n")
        sys.stdout.flush()

# Cleanup attributes
br_eval(RESET_JS)

with open(DST_FILE, "w") as f:
    json.dump(results, f)
PYEOF

# Capture impl hover states
TMP_IMPL_HOVER=$(mktemp "$TMP_BASE/htd-impl-hover.XXXXXX")
_HTD_SESSION="$IMPL_SESS" _HTD_SRC="$TMP_IMPL" _HTD_DST="$TMP_IMPL_HOVER" \
  HOVER_WAIT="$HOVER_WAIT" HOVER_MAX_WAIT="$HOVER_MAX_WAIT" RESET_WAIT="$RESET_WAIT" \
  python3 "$_HTD_PY"

# ── Step 3: pair on ref via elementFromPoint, capture ref idle + hover ──
echo "  ▸ Pairing on ref + capturing ref hover states..."

PAIR_JS=$(python3 - "$TMP_IMPL" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f: raw = f.read().strip()
if raw.startswith('"'): raw = json.loads(raw)
impl_list = json.loads(raw)
points = [
    {
        "i": i,
        "x": e["x"],
        "y": e["y"],
        "tag": e["tag"],
        "cls": e["cls"],
        "txt": e["txt"],
    }
    for i, e in enumerate(impl_list)
]
points_json = json.dumps(points)
js = """
(() => {
  const points = %s;
  const visualProps = ['color','backgroundColor','opacity','transform','filter',
                       'borderTopColor','borderBottomColor','textDecorationLine',
                       'textDecorationColor','fontStyle','fontWeight','letterSpacing',
                       'boxShadow','scale','translate','rotate'];
  const transProps = ['transitionProperty','transitionDuration','transitionTimingFunction','transitionDelay'];
  const norm = value => String(value || '').trim().replace(/\\s+/g, ' ').toLocaleLowerCase();
  const classNoise = new Set([
    'active', 'current', 'selected', 'open', 'closed', 'on', 'off',
    'hover', 'focus', 'focused', 'disabled', 'hidden', 'visible',
    'nclick-target',
  ]);
  const classTokens = value => norm(value).split(' ')
    .filter(token => token && !classNoise.has(token));
  const compatibleClass = (expected, actual) => {
    const a = classTokens(expected);
    const b = classTokens(actual);
    if (!a.length || !b.length) return true;
    return a.some(token => b.includes(token));
  };
  const compatibleText = (expected, actual) => {
    const a = norm(expected);
    const b = norm(actual);
    if (!a && !b) return true;
    return Boolean(a && b && (a === b || a.includes(b) || b.includes(a)));
  };
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0
      && r.bottom >= 0 && r.top <= window.innerHeight
      && r.right >= 0 && r.left <= window.innerWidth
      && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const distance = (el, p) => {
    const r = el.getBoundingClientRect();
    return Math.hypot((r.left + r.width / 2) - p.x, (r.top + r.height / 2) - p.y);
  };
  const out = [];
  for (const p of points) {
    const direct = document.elementFromPoint(p.x, p.y);
    let el = direct;
    let match = 'coordinate';
    const expectedTag = String(p.tag || '').toUpperCase();
    const directCompatible = direct
      && direct.tagName === expectedTag
      && compatibleClass(p.cls, direct.className)
      && compatibleText(p.txt, direct.textContent);
    if (!directCompatible && direct) {
      let ancestor = direct.parentElement;
      while (ancestor && ancestor !== document.body) {
        if (
          ancestor.tagName === expectedTag
          && compatibleClass(p.cls, ancestor.className)
          && compatibleText(p.txt, ancestor.textContent)
        ) {
          el = ancestor;
          match = 'semantic-ancestor';
          break;
        }
        ancestor = ancestor.parentElement;
      }
    }
    if (
      (!el || el.tagName !== expectedTag || !compatibleText(p.txt, el.textContent))
      && norm(p.txt)
      && /^[A-Z][A-Z0-9-]*$/.test(expectedTag)
    ) {
      const candidates = Array.from(document.querySelectorAll(expectedTag.toLowerCase()))
        .filter(candidate => (
          visible(candidate)
          && compatibleClass(p.cls, candidate.className)
          && compatibleText(p.txt, candidate.textContent)
        ))
        .sort((a, b) => distance(a, p) - distance(b, p));
      if (candidates.length) {
        el = candidates[0];
        match = 'semantic-text';
      }
    }
    if (!el) { out.push({ i: p.i, miss: true }); continue; }
    el.setAttribute('data-htd-target-' + p.i, '');
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    const idle = {}; visualProps.forEach(k => idle[k] = s[k]);
    const trans = {}; transProps.forEach(k => trans[k] = s[k]);
    const cx = r.left + r.width / 2;
    const cy = r.top  + r.height / 2;
    out.push({
      i: p.i,
      tag: el.tagName,
      cls: (el.className && el.className.toString) ? el.className.toString().slice(0, 60) : '',
      txt: (el.textContent || '').trim().replace(/\\s+/g,' ').slice(0, 30),
      x: +cx.toFixed(1), y: +cy.toFixed(1),
      w: +r.width.toFixed(1), h: +r.height.toFixed(1),
      match,
      idle, trans,
    });
  }
  return JSON.stringify(out);
})()
""" % points_json
print(js)
PYEOF
)
agent-browser --session "$REF_SESS" eval "$PAIR_JS" > "$TMP_REF" 2>&1
if [ ! -s "$TMP_REF" ]; then
  echo "ERROR: ref pairing returned empty"; exit 2
fi

# Now capture ref hover for each i
TMP_REF_HOVER=$(mktemp "$TMP_BASE/htd-ref-hover.XXXXXX")

# Build a stripped impl-list for hover-capture (only points for ref, but we need refs that paired ok)
TMP_REF_PAIRED=$(mktemp "$TMP_BASE/htd-ref-paired.XXXXXX")
python3 - "$TMP_REF" "$TMP_REF_PAIRED" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f: raw = f.read().strip()
if raw.startswith('"'): raw = json.loads(raw)
ref_list = json.loads(raw)
# Keep the paired transition metadata so hover capture can wait through a
# reference-only delay instead of sampling its idle state too early.
out = []
for r in ref_list:
    if r.get("miss"):
        out.append({"x": -1, "y": -1, "miss": True})
    else:
        out.append({"x": r["x"], "y": r["y"], "trans": r.get("trans", {})})
with open(sys.argv[2], "w") as f:
    json.dump(out, f)
PYEOF

_HTD_SESSION="$REF_SESS" _HTD_SRC="$TMP_REF_PAIRED" _HTD_DST="$TMP_REF_HOVER" \
  HOVER_WAIT="$HOVER_WAIT" HOVER_MAX_WAIT="$HOVER_MAX_WAIT" RESET_WAIT="$RESET_WAIT" \
  python3 "$_HTD_PY"

# ── Step 4: diff ──
echo "  ▸ Diffing pairs..."
echo ""

python3 - "$TMP_IMPL" "$TMP_IMPL_HOVER" "$TMP_REF" "$TMP_REF_HOVER" "$OUT_DIR" "$PAIR_TOLERANCE" <<'PYEOF'
import json, sys, os

def parse(path):
    with open(path) as f: raw = f.read().strip()
    if raw.startswith('"') and raw.endswith('"'):
        return json.loads(json.loads(raw))
    return json.loads(raw)

impl       = parse(sys.argv[1])
impl_hover = parse(sys.argv[2])
ref        = parse(sys.argv[3])
ref_hover  = parse(sys.argv[4])
out_dir    = sys.argv[5]
tol        = float(sys.argv[6])

ref_by_i  = {r.get("i", idx): r for idx, r in enumerate(ref)}
impl_hov_by_i = {h.get("i", idx): h for idx, h in enumerate(impl_hover)}
ref_hov_by_i  = {h.get("i", idx): h for idx, h in enumerate(ref_hover)}

VISUAL_PROPS = ['color','backgroundColor','opacity','transform','filter',
                'borderTopColor','borderBottomColor','textDecorationLine',
                'textDecorationColor','fontStyle','fontWeight','letterSpacing',
                'boxShadow','scale','translate','rotate']
TRANS_PROPS = ['transitionProperty','transitionDuration','transitionTimingFunction','transitionDelay']

CRITICAL_TIMING = {"transitionDuration", "transitionTimingFunction"}

def changed(a, b):
    if not a or not b: return False
    a, b = str(a).strip(), str(b).strip()
    if a == b: return False
    if a in ("", "none", "normal", "auto") and b in ("", "none", "normal", "auto"):
        return False
    return True

def normalized_text(value):
    return " ".join(str(value or "").split()).casefold()

CLASS_NOISE = {
    "active", "current", "selected", "open", "closed", "on", "off",
    "hover", "focus", "focused", "disabled", "hidden", "visible",
    "nclick-target",
}

def class_tokens(value):
    return {
        token
        for token in normalized_text(value).split()
        if token and token not in CLASS_NOISE
    }

def split_css_list(value):
    items = []
    current = []
    depth = 0
    for char in str(value or ""):
        if char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        if char == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    items.append("".join(current).strip())
    return [item for item in items if item]

def canonical_transition_value(value):
    items = split_css_list(value)
    if items and all(item == items[0] for item in items):
        return items[0]
    return ", ".join(items)

def semantically_compatible(impl_el, ref_el):
    # elementFromPoint can land on an unrelated child at the same coordinate
    # when the two pages have even a small horizontal layout offset. Comparing
    # an interactive <a> against a neighboring SVG <path> then fabricates a
    # critical timing mismatch. Same-tag descendants can be wrong too: a card
    # wrapper's text contains all of its children's text. When both elements
    # expose structural classes, require a shared non-state token before
    # comparing their transition/hover deltas.
    if str(impl_el.get("tag", "")).upper() != str(ref_el.get("tag", "")).upper():
        return False
    impl_classes = class_tokens(impl_el.get("cls"))
    ref_classes = class_tokens(ref_el.get("cls"))
    if impl_classes and ref_classes and impl_classes.isdisjoint(ref_classes):
        return False
    impl_text = normalized_text(impl_el.get("txt"))
    ref_text = normalized_text(ref_el.get("txt"))
    if impl_text or ref_text:
        return bool(
            impl_text
            and ref_text
            and (
                impl_text == ref_text
                or impl_text in ref_text
                or ref_text in impl_text
            )
        )
    return True

rows = []
for i, ie in enumerate(impl):
    re = ref_by_i.get(i)
    row = {
        "i": i,
        "impl_tag": ie["tag"], "impl_cls": ie["cls"], "txt": ie["txt"],
        "impl_xy": (ie["x"], ie["y"]),
        "issues": [],
    }
    if not re or re.get("miss"):
        row["sev"] = "unpaired"
        row["issues"].append("ref miss at xy")
        rows.append(row); continue

    dx = abs(ie["x"] - re["x"]); dy = abs(ie["y"] - re["y"])
    row["ref_tag"] = re["tag"]; row["ref_cls"] = re["cls"]; row["ref_txt"] = re["txt"]
    row["ref_xy"] = (re["x"], re["y"]); row["dx"] = dx; row["dy"] = dy
    row["pair_method"] = re.get("match", "coordinate")

    if not semantically_compatible(ie, re):
        row["sev"] = "unpaired"
        row["issues"].append(
            "semantic pair mismatch "
            f"{ie['tag']}:{ie['txt']!r} vs {re['tag']}:{re['txt']!r}"
        )
        rows.append(row); continue

    impl_text = normalized_text(ie.get("txt"))
    ref_text = normalized_text(re.get("txt"))
    strong_text_pair = bool(
        impl_text
        and ref_text
        and (
            impl_text == ref_text
            or impl_text in ref_text
            or ref_text in impl_text
        )
    )
    if (dx > tol or dy > tol) and not strong_text_pair:
        row["sev"] = "unpaired"
        row["issues"].append(f"pair offset Δ{dx:.0f},{dy:.0f}")
        rows.append(row); continue

    impl_hover_entry = impl_hov_by_i.get(i, {})
    ref_hover_entry = ref_hov_by_i.get(i, {})
    observations = {
        "impl": impl_hover_entry.get("observation") or {},
        "ref": ref_hover_entry.get("observation") or {},
    }
    ih = impl_hover_entry.get("hover") or {}
    rh = ref_hover_entry.get("hover") or {}
    inactive_sides = []
    if impl_hover_entry.get("miss") or ih.get("__hovered") is not True:
        inactive_sides.append("impl")
    if ref_hover_entry.get("miss") or rh.get("__hovered") is not True:
        inactive_sides.append("ref")
    if inactive_sides:
        row["sev"] = "unpaired"
        row["issues"].append(
            "hover activation unproven on " + "/".join(inactive_sides)
        )
        rows.append(row); continue

    # ── Diff transition timing ──
    timing_diffs = []
    for p in TRANS_PROPS:
        iv = ie["trans"].get(p, ""); rv = re["trans"].get(p, "")
        if changed(canonical_transition_value(iv), canonical_transition_value(rv)):
            timing_diffs.append((p, iv, rv))

    # ── Diff idle→hover delta ──
    observed_hover_deltas = []
    delta_diffs = []
    for p in VISUAL_PROPS:
        i_idle = ie["idle"].get(p, ""); i_hov = ih.get(p, "")
        r_idle = re["idle"].get(p, ""); r_hov = rh.get(p, "")
        i_changes = changed(i_idle, i_hov)
        r_changes = changed(r_idle, r_hov)
        if i_changes:
            observed_hover_deltas.append(("impl", p))
        if r_changes:
            observed_hover_deltas.append(("ref", p))
        if r_changes and not i_changes:
            delta_diffs.append((p, "no-change", f"{r_idle}→{r_hov}", "missing-hover-effect"))
        elif i_changes and not r_changes:
            delta_diffs.append((p, f"{i_idle}→{i_hov}", "no-change", "extra-hover-effect"))
        elif i_changes and r_changes:
            if changed(i_hov, r_hov):
                delta_diffs.append((p, f"{i_idle}→{i_hov}", f"{r_idle}→{r_hov}", "different-target"))

    has_hover_delta = bool(observed_hover_deltas)
    unsettled_sides = [
        side
        for side, observation in observations.items()
        if observation.get("settled") is False
    ]
    sev = "ok"
    if delta_diffs: sev = "major"
    if unsettled_sides:
        sev = "critical"
        row["issues"].append(
            "hover observation capped before declared transition settled on "
            + "/".join(unsettled_sides)
        )
    elif timing_diffs and has_hover_delta:
        if any(p in CRITICAL_TIMING for p, *_ in timing_diffs):
            sev = "critical"
        elif sev != "critical":
            sev = "major"
    elif timing_diffs and sev == "ok":
        sev = "minor"
        row["issues"].append("transition timing metadata differs without observed hover delta")
    if any(d[3] == "missing-hover-effect" for d in delta_diffs): sev = "critical"

    row["sev"] = sev
    row["timing_diffs"] = timing_diffs
    row["delta_diffs"] = delta_diffs
    row["observed_hover_deltas"] = observed_hover_deltas
    row["observations"] = observations
    rows.append(row)

SEV_RANK = {"critical": 4, "unpaired": 3, "major": 2, "minor": 1, "ok": 0}
counts = {"critical": 0, "major": 0, "minor": 0, "ok": 0, "unpaired": 0}
for r in rows: counts[r["sev"]] += 1
rows.sort(key=lambda r: -SEV_RANK[r["sev"]])
all_unpaired = bool(rows) and counts["unpaired"] == len(rows)

# ── Markdown ──
md = os.path.join(out_dir, "hover-tree-diff.md")
with open(md, "w") as f:
    f.write("# Hover Tree Diff Report\n\n")
    f.write(f"**Walked**: {len(impl)} hover candidates  ")
    f.write(f"**Critical**: {counts['critical']}  ")
    f.write(f"**Major**: {counts['major']}  ")
    f.write(f"**Unpaired**: {counts['unpaired']}  ")
    f.write(f"**Match**: {counts['ok']}\n\n")
    if counts["critical"] or counts["major"] or all_unpaired:
        if all_unpaired:
            f.write(
                "❌ FAIL hover-tree-diff: every impl hover candidate was "
                "unpaired, so no ref/impl hover comparison was measured.\n\n"
            )
        else:
            f.write(
                f"❌ FAIL hover-tree-diff: {counts['critical']} critical / "
                f"{counts['major']} major hover mismatch(es). Impl hover motion "
                "must match ref and must not add extra transform/opacity deltas.\n\n"
            )
    else:
        f.write(
            f"✅ PASS hover-tree-diff: no critical/major hover mismatches "
            f"across {len(impl)} impl hover candidate(s).\n\n"
        )
    f.write("| # | Sev | Impl tag.cls | Text | Timing diffs | Hover delta diffs |\n")
    f.write("|---|---|---|---|---|---|\n")
    for r in rows:
        if r["sev"] == "ok": continue
        sev_label = {
            "critical": "❌ FAIL",
            "major": "❌ FAIL",
            "minor": "⚠️ WARN",
            "unpaired": "⚪ UNPAIRED",
        }[r["sev"]]
        impl_id = f"{r['impl_tag']}.{r['impl_cls'][:25]}".rstrip(".")
        txt = r["txt"][:24]
        td = r.get("timing_diffs", [])
        td_str = "; ".join(f"`{p}`: {a[:14]}→{b[:14]}" for p, a, b in td[:2])
        if len(td) > 2: td_str += f" (+{len(td)-2})"
        if not td_str: td_str = "—"
        dd = r.get("delta_diffs", [])
        dd_str = "; ".join(f"`{p}`[{kind}]" for p, _, _, kind in dd[:3])
        if len(dd) > 3: dd_str += f" (+{len(dd)-3})"
        if not dd_str:
            dd_str = "(unpaired)" if r["sev"] == "unpaired" else "—"
        f.write(f"| {r['i']} | {sev_label} | `{impl_id}` | {txt} | {td_str} | {dd_str} |\n")

# ── JSON ──
js = os.path.join(out_dir, "hover-tree-diff.json")
with open(js, "w") as f:
    json.dump(rows, f, indent=2, default=str)

print(f"  Walked {len(impl)} hover candidates")
print(f"  🔴 critical: {counts['critical']}   🟠 major: {counts['major']}   ⚪ unpaired: {counts['unpaired']}   ✓ ok: {counts['ok']}")
print(f"  Report: {md}")
print(f"  Raw:    {js}")
print()
if counts["critical"] or counts["major"]:
    print("Top critical/major:")
    for r in rows[:6]:
        if r["sev"] in ("ok",): continue
        sev_label = {"critical": "🔴", "major": "🟠", "minor": "🟡", "unpaired": "⚪"}[r["sev"]]
        impl_id = f"{r['impl_tag']}.{r['impl_cls'][:30]}"
        txt = (r["txt"] or "")[:20]
        bits = []
        for p, a, b in (r.get("timing_diffs") or [])[:2]:
            bits.append(f"{p}: {str(a)[:12]}→{str(b)[:12]}")
        for p, _, _, kind in (r.get("delta_diffs") or [])[:2]:
            bits.append(f"{p}[{kind}]")
        d = "; ".join(bits) or "(unpaired)"
        print(f"  {sev_label} #{r['i']}  {impl_id}  '{txt}'  | {d}")

sys.exit(1 if (counts["critical"] or counts["major"] or all_unpaired) else 0)
PYEOF
EXIT=$?

rm -f "$_HTD_PY" "$TMP_IMPL_HOVER" "$TMP_REF_HOVER" "$TMP_REF_PAIRED"
exit $EXIT
