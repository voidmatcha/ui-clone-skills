#!/usr/bin/env bash
# scroll-end-completion-check.sh — Catch scroll-scrub reveals that never finish.
#
# Why it matters:
#   A scroll-scrub reveal maps progress p∈[0,1] to a style. If the offset is
#   tuned such that p < 1 even at literal page-bottom (e.g. `'start center'`
#   needs the target's top to reach viewport-center, which is geometrically
#   impossible when the target lives near page-bottom on tall viewports),
#   the user scrolls all the way down and STILL sees the half-revealed state.
#
#   The bug is viewport-dependent: probe browser at 633px-tall passes; user
#   at 900px-tall sees a stuck reveal. Section-compare misses it because
#   the failure mode is "no reflow happens at maxScroll" — a *frame* check
#   passes (still renders something), only a *delta* check across multiple
#   probe heights catches it.
#
#   Catches the bug class regardless of framework (Framer Motion / Motion /
#   GSAP ScrollTrigger / hand-rolled RAF). Pure runtime invariant:
#       at p=1.0, additional scroll progress MUST NOT change element style.
#
# Method:
#   For each VIEWPORTS entry:
#     1. Navigate, wait settle.
#     2. Identify candidates: elements whose opacity OR transform changes
#        between scrollTop=0 and scrollTop=maxScroll-300. These are the
#        scroll-driven elements on this page.
#     3. Sample each candidate's style at [maxScroll-150, maxScroll-50, maxScroll].
#        Settled iff delta(maxScroll-50, maxScroll) is tiny on every axis.
#     4. Any candidate not settled at any viewport → FAIL.
#
# Usage:
#   bash scroll-end-completion-check.sh <session> <impl-url> <ref-dir>
#
#   ref-dir: tmp/ref/<component>/ — output JSON goes here.
#
# Optional env:
#   VIEWPORTS         — comma-separated WxH list, default
#                       "375x812,1280x800,1600x900,1920x1080"
#   WAIT_MS           — initial wait after navigate (default 1500)
#   SETTLE_MS         — scroll-step settle (default 600)
#   OPACITY_EPS       — opacity delta epsilon (default 0.01)
#   TRANSFORM_EPS_PX  — transform translate delta epsilon in px (default 1)
#
# Exit: 0 = all settled across all viewports, 1 = stuck elements found,
#       2 = setup error
# Output: <ref-dir>/scroll-completion.json with shape:
#   { "status": "pass"|"fail",
#     "viewports": [
#       { "w":W, "h":H, "stuck": [{ "selector":"...", "delta": {...} }, ... ] }
#     ],
#     "generatedAt": "..." }

set -uo pipefail

SESSION="${1:?Usage: scroll-end-completion-check.sh <session> <impl-url> <ref-dir>}"
URL="${2:?Missing impl-url}"
REF_DIR="${3:?Missing ref-dir}"

VIEWPORTS_CSV="${VIEWPORTS:-375x812,1280x800,1600x900,1920x1080}"
WAIT_MS="${WAIT_MS:-1500}"
SETTLE_MS="${SETTLE_MS:-600}"
OPACITY_EPS="${OPACITY_EPS:-0.01}"
TRANSFORM_EPS_PX="${TRANSFORM_EPS_PX:-1}"

mkdir -p "$REF_DIR"
OUT="$REF_DIR/scroll-completion.json"

if ! command -v agent-browser >/dev/null 2>&1; then
  echo "ERROR: agent-browser not found. Install: npm i -g agent-browser" >&2
  exit 2
fi

cleanup() {
  agent-browser --session "$SESSION" close 2>/dev/null
}
trap cleanup EXIT

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
TMP_RESULTS=$(mktemp)
trap 'rm -f "$TMP_RESULTS"; cleanup' EXIT

# Iterate viewports. Per-viewport JSON line goes to $TMP_RESULTS, then we
# aggregate with node at the end.
IFS=',' read -ra VPS <<< "$VIEWPORTS_CSV"
GLOBAL_STATUS=0

for VP in "${VPS[@]}"; do
  W="${VP%x*}"
  H="${VP#*x}"
  echo "→ viewport ${W}x${H}"

  agent-browser --session "$SESSION" set viewport "$W" "$H" >/dev/null 2>&1
  agent-browser --session "$SESSION" navigate "$URL" >/dev/null 2>&1
  sleep $((WAIT_MS / 1000))

  # The probe runs entirely inside the page. It:
  #   1. Captures style for every visible (≥4px) element at scrollTop=0.
  #   2. Scrolls to maxScroll-300, settles, captures again.
  #   3. For elements whose opacity OR translateY changed, they are
  #      "scroll-driven candidates" — record their selector / index.
  #   4. Samples each candidate at three near-end positions and computes
  #      deltas between the last two.
  RAW=$(agent-browser --session "$SESSION" eval "(async () => {
    const OPACITY_EPS = $OPACITY_EPS;
    const TRANSFORM_EPS_PX = $TRANSFORM_EPS_PX;
    const SETTLE = $SETTLE_MS;

    const sleep = (ms) => new Promise(r => setTimeout(r, ms));

    const SKIP_TAGS = new Set(['SCRIPT','STYLE','META','LINK','HEAD','TITLE','NOSCRIPT','HTML','BODY','NEXT-ROUTE-ANNOUNCER']);

    function selectorOf(el) {
      const id = el.id ? '#' + el.id : '';
      const cls = (el.className && typeof el.className === 'string')
        ? el.className.trim().split(/\\s+/).slice(0, 2).map(c => '.' + c).join('')
        : '';
      return el.tagName.toLowerCase() + id + cls;
    }

    function parseTranslate(transformStr) {
      // matrix(a,b,c,d,e,f) or matrix3d(...). Pick translate X/Y (e,f or 13,14).
      if (!transformStr || transformStr === 'none') return { tx: 0, ty: 0 };
      const m = transformStr.match(/matrix\\(([^)]+)\\)/);
      if (m) {
        const v = m[1].split(',').map(Number);
        return { tx: v[4] || 0, ty: v[5] || 0 };
      }
      const m3 = transformStr.match(/matrix3d\\(([^)]+)\\)/);
      if (m3) {
        const v = m3[1].split(',').map(Number);
        return { tx: v[12] || 0, ty: v[13] || 0 };
      }
      return { tx: 0, ty: 0 };
    }

    function snapshotAll() {
      const out = new Map();
      const els = document.querySelectorAll('*');
      for (const el of els) {
        if (SKIP_TAGS.has(el.tagName)) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 4 || r.height < 4) continue;
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        // Skip elements with running CSS animations — their transform changes
        // on a timer, not in response to scroll, so probe deltas are noise.
        if (cs.animationName && cs.animationName !== 'none' &&
            cs.animationPlayState !== 'paused' &&
            cs.animationDuration && cs.animationDuration !== '0s') continue;
        const t = parseTranslate(cs.transform);
        out.set(el, { opacity: parseFloat(cs.opacity), tx: t.tx, ty: t.ty });
      }
      return out;
    }

    const maxScroll = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
    if (maxScroll < 200) {
      return JSON.stringify({ skipped: 'page-too-short', maxScroll, candidates: 0 });
    }

    // Step 1: snapshot at top.
    window.scrollTo({ top: 0, behavior: 'instant' });
    await sleep(SETTLE);
    const top = snapshotAll();

    // Step 2: snapshot near bottom (gives us the set of scroll-changing elements).
    window.scrollTo({ top: Math.max(0, maxScroll - 300), behavior: 'instant' });
    await sleep(SETTLE);
    const mid = snapshotAll();

    const candidates = [];
    let pinIdx = 0;
    for (const [el, s] of top.entries()) {
      const m = mid.get(el);
      if (!m) continue;
      const dOpacity = Math.abs(m.opacity - s.opacity);
      const dY = Math.abs(m.ty - s.ty);
      const dX = Math.abs(m.tx - s.tx);
      if (dOpacity > OPACITY_EPS * 5 || dY > TRANSFORM_EPS_PX * 5 || dX > TRANSFORM_EPS_PX * 5) {
        // Pin this element so phase 3 can find it again.
        el.setAttribute('data-scrollprobe', String(pinIdx));
        candidates.push({ idx: pinIdx, selector: selectorOf(el) });
        pinIdx++;
      }
    }

    if (candidates.length === 0) {
      document.querySelectorAll('[data-scrollprobe]').forEach(e => e.removeAttribute('data-scrollprobe'));
      return JSON.stringify({ maxScroll, candidates: 0, stuck: [] });
    }

    // Step 3: sample at three near-end positions. Settled iff delta between
    // last two is below epsilon on every axis.
    async function sampleAt(top) {
      window.scrollTo({ top, behavior: 'instant' });
      await sleep(SETTLE);
      const map = {};
      for (const c of candidates) {
        const el = document.querySelector('[data-scrollprobe=\"' + c.idx + '\"]');
        if (!el) { map[c.idx] = null; continue; }
        const cs = getComputedStyle(el);
        const t = parseTranslate(cs.transform);
        map[c.idx] = { opacity: parseFloat(cs.opacity), tx: t.tx, ty: t.ty };
      }
      return map;
    }

    const probeAtMinus150 = await sampleAt(Math.max(0, maxScroll - 150));
    const probeAtMinus50  = await sampleAt(Math.max(0, maxScroll - 50));
    const probeAtMax       = await sampleAt(maxScroll);

    const stuck = [];
    for (const c of candidates) {
      const a = probeAtMinus50[c.idx];
      const b = probeAtMax[c.idx];
      if (!a || !b) continue;
      const dOpacity = Math.abs(b.opacity - a.opacity);
      const dY = Math.abs(b.ty - a.ty);
      const dX = Math.abs(b.tx - a.tx);
      if (dOpacity > OPACITY_EPS || dY > TRANSFORM_EPS_PX || dX > TRANSFORM_EPS_PX) {
        const before = probeAtMinus150[c.idx];
        stuck.push({
          selector: c.selector,
          delta: { opacity: +dOpacity.toFixed(4), tx: +dX.toFixed(2), ty: +dY.toFixed(2) },
          probe: {
            minus150: before,
            minus50: a,
            max: b,
          }
        });
      }
    }

    document.querySelectorAll('[data-scrollprobe]').forEach(e => e.removeAttribute('data-scrollprobe'));

    return JSON.stringify({
      maxScroll,
      candidates: candidates.length,
      stuck,
    });
  })()" 2>/dev/null)

  # Unwrap agent-browser's JSON-as-string output.
  DATA=$(printf '%s' "$RAW" | sed 's/^"//;s/"$//' | sed 's/\\"/"/g; s/\\\\/\\/g')

  # Record this viewport's findings as a single JSON object line.
  node -e "
    const d = JSON.parse(process.argv[1] || '{}');
    const out = {
      w: Number(process.argv[2]),
      h: Number(process.argv[3]),
      maxScroll: d.maxScroll || 0,
      candidates: d.candidates || 0,
      stuck: d.stuck || [],
      skipped: d.skipped || null,
    };
    process.stdout.write(JSON.stringify(out) + '\\n');
  " "$DATA" "$W" "$H" >> "$TMP_RESULTS"

  STUCK_COUNT=$(node -e "
    const d = JSON.parse(process.argv[1] || '{}');
    process.stdout.write(String((d.stuck || []).length));
  " "$DATA")

  if [ -n "$STUCK_COUNT" ] && [ "$STUCK_COUNT" -gt 0 ]; then
    GLOBAL_STATUS=1
    echo "   ❌ ${STUCK_COUNT} stuck element(s) at ${W}x${H}"
  else
    echo "   ✅ settled at ${W}x${H}"
  fi
done

# Aggregate per-viewport lines into final JSON.
node -e "
  const fs = require('fs');
  const lines = fs.readFileSync(process.argv[1], 'utf8').split('\\n').filter(Boolean);
  const viewports = lines.map(l => JSON.parse(l));
  const totalStuck = viewports.reduce((a, v) => a + (v.stuck ? v.stuck.length : 0), 0);
  const out = {
    status: totalStuck === 0 ? 'pass' : 'fail',
    totalStuck,
    viewports,
    generatedAt: process.argv[2],
    url: process.argv[3],
  };
  process.stdout.write(JSON.stringify(out, null, 2));
" "$TMP_RESULTS" "$NOW" "$URL" > "$OUT"

if [ "$GLOBAL_STATUS" -eq 0 ]; then
  echo "✅ Scroll-end completion: PASS (all viewports settled)"
  echo "   Output: $OUT"
else
  echo "❌ Scroll-end completion: FAIL"
  echo "   Output: $OUT"
  echo ""
  echo "   Common cause: scroll-scrub offset endpoint is geometrically unreachable"
  echo "   on tall viewports (e.g. 'start center' for a footer-bound element)."
  echo "   Fix: anchor the end offset to the target's BOTTOM, not its top —"
  echo "        offset: ['start end', 'end 85%'] generalizes across viewports."
  echo "        Add a 'completeAt' headroom (e.g. 0.85) so shuffle/stagger tails"
  echo "        finish before scroll progress reaches literal 1.0."
  echo "   See: skills/ui-reverse-engineering/transition-implementation.md →"
  echo "        'Viewport-aware scroll-scrub offsets'"
fi

exit $GLOBAL_STATUS
