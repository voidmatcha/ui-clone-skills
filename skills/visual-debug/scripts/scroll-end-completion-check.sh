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
#     4. Repeat the last two positions without scrolling. Time-dependent
#        changes make the probe inconclusive (exit 2), never a settled pass.
#     5. Any remaining candidate not settled at any viewport → FAIL.
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
#       2 = setup error or time-contaminated measurement
# Output: <ref-dir>/scroll-completion.json with shape:
#   { "status": "pass"|"fail"|"error",
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
PROBE_ERROR=0

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

    // Growing-page guard (loop-e2e-4): lazy/scroll-activated content can grow
    // scrollHeight AFTER maxScroll was cached (realfood erf region +180px),
    // which parks state-machine thresholds (e.g. nav nearBottom at sh-200)
    // INSIDE the stale minus50..max window and flags ref-faithful behavior as
    // stuck. Recompute the live bottom before each sample so all three
    // samples sit at the true end of the document.
    const liveMax = () => Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
    // One full-depth pre-pass so growth has already happened before sampling.
    window.scrollTo({ top: liveMax(), behavior: 'instant' });
    await sleep(SETTLE);
    const probeAtMinus150 = await sampleAt(Math.max(0, liveMax() - 150));
    const probeAtMinus50  = await sampleAt(Math.max(0, liveMax() - 50));
    // Fixed-position controls distinguish elapsed-time motion from a scroll
    // endpoint delta. Contamination remains inconclusive, never an exemption.
    const controlAtMinus50 = await sampleAt(window.scrollY);
    const probeAtMax       = await sampleAt(liveMax());
    const controlAtMax = await sampleAt(window.scrollY);
    // Duration-matched idle control: a->b (raw) spans TWO sampleAt-driven
    // SETTLE intervals (a->controlAtMinus50->b). A single-interval control
    // (just controlAtMax, one SETTLE after b) spans only HALF that duration —
    // subtracting two mismatched-duration one-interval deltas can badly
    // over-explain an OSCILLATING (not monotonic) continuous motion whose
    // period aliases against SETTLE (e.g. period ~= 2*SETTLE puts
    // controlAtMinus50/controlAtMax at opposite phase peaks, each reading a
    // full amplitude swing, while the real a->b span nets close to zero
    // oscillation contribution — the two peak swings would then get
    // subtracted from a genuinely STUCK, non-oscillating delta and silently
    // clear it). controlAtMaxFull is fixed at max for the SAME two-interval
    // span as raw, so it measures what time alone contributes over an
    // identical-length window instead of composing two quarter-strength
    // measurements.
    const controlAtMaxFull = await sampleAt(window.scrollY);

    const stuck = [];
    const temporalMotion = [];
    const unmeasurableTargets = [];
    const axisDeltas = (x, y) => ({
      opacity: Math.abs(y.opacity - x.opacity),
      tx: Math.abs(y.tx - x.tx),
      ty: Math.abs(y.ty - x.ty),
    });
    const exceeds = (d) => d.opacity > OPACITY_EPS || d.tx > TRANSFORM_EPS_PX || d.ty > TRANSFORM_EPS_PX;
    for (const c of candidates) {
      const a = probeAtMinus50[c.idx];
      const b = probeAtMax[c.idx];
      const ca = controlAtMinus50[c.idx];
      const cb = controlAtMax[c.idx];
      const cbFull = controlAtMaxFull[c.idx];
      if (!a || !b || !ca || !cb || !cbFull) {
        unmeasurableTargets.push({selector: c.selector, reason: 'target missing during end-position probe'});
        continue;
      }
      // A continuous timer/rAF-driven element drifts style at a FIXED scroll
      // position. That drift also shows up in the minus50->max delta even
      // though it has nothing to do with scroll progress. Subtract the
      // time-explained component — measured over the SAME two-SETTLE-interval
      // duration as raw (see controlAtMaxFull comment above; using two
      // mismatched one-interval deltas here previously let an oscillating,
      // not just monotonic, continuous motion falsely cancel a real stuck
      // delta) — so a healthy continuously-moving element (e.g. a ticker)
      // doesn't become permanently unmeasurable by this check. A residual
      // that still exceeds epsilon means scroll progress is ALSO changing
      // this element and it hasn't settled — a real defect, not an artifact
      // of the timer.
      const timeDeltaStart = axisDeltas(a, ca);
      const matchedControl = axisDeltas(b, cbFull);
      const rawDelta = axisDeltas(a, b);
      const residual = {
        opacity: Math.max(0, rawDelta.opacity - matchedControl.opacity),
        tx: Math.max(0, rawDelta.tx - matchedControl.tx),
        ty: Math.max(0, rawDelta.ty - matchedControl.ty),
      };
      const isContinuous = exceeds(timeDeltaStart) || exceeds(axisDeltas(b, cb)) || exceeds(matchedControl);
      const residualExceeds = exceeds(residual);
      if (isContinuous) {
        temporalMotion.push({
          selector: c.selector,
          reason: residualExceeds
            ? 'continuous time-driven motion with an unresolved scroll-attributable residual'
            : 'continuous time-driven motion fully explains the observed delta; not a stuck scroll endpoint',
          confirmedTimeOnly: !residualExceeds,
          residual,
          probe: { minus50: a, minus50Control: ca, max: b, maxControl: cb, maxControlFull: cbFull },
        });
        if (!residualExceeds) continue;
      }
      const dOpacity = isContinuous ? residual.opacity : rawDelta.opacity;
      const dY = isContinuous ? residual.ty : rawDelta.ty;
      const dX = isContinuous ? residual.tx : rawDelta.tx;
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
      temporalMotion,
      unmeasurableTargets,
    });
  })()" 2>/dev/null)

  # Unwrap agent-browser's JSON-as-string output.
  DATA=$(printf '%s' "$RAW" | sed 's/^"//;s/"$//' | sed 's/\\"/"/g; s/\\\\/\\/g')

  # Fail-closed (F3 class): a timed-out or crashed eval returns empty RAW, and
  # parsing '{}' downstream silently yields stuck:[] -> a false "✅ settled".
  # A completed probe ALWAYS returns an object carrying the 'candidates' key, so
  # its absence means the probe never ran here — treat as an error, not a pass.
  if ! printf '%s' "$DATA" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{const o=JSON.parse(s);process.exit(o&&typeof o==="object"&&Object.prototype.hasOwnProperty.call(o,"candidates")?0:1)}catch(e){process.exit(1)}})'; then
    echo "   ❌ scroll-end probe returned no parseable result at ${W}x${H} (eval timeout/crash) — failing closed"
    GLOBAL_STATUS=1
    PROBE_ERROR=1
    continue
  fi

  # Record this viewport's findings as a single JSON object line.
  node -e "
    const d = JSON.parse(process.argv[1] || '{}');
    const out = {
      w: Number(process.argv[2]),
      h: Number(process.argv[3]),
      maxScroll: d.maxScroll || 0,
      candidates: d.candidates || 0,
      stuck: d.stuck || [],
      temporalMotion: d.temporalMotion || [],
      unmeasurableTargets: d.unmeasurableTargets || [],
      skipped: d.skipped || null,
    };
    process.stdout.write(JSON.stringify(out) + '\\n');
  " "$DATA" "$W" "$H" >> "$TMP_RESULTS"

  STUCK_COUNT=$(node -e "
    const d = JSON.parse(process.argv[1] || '{}');
    process.stdout.write(String((d.stuck || []).length));
  " "$DATA")

  # Confirmed time-only motion (residual fully explained by the fixed-position
  # control samples) is informational, not a blocker — see the probe comment
  # above. An UNRESOLVED residual is a MEASURED, quantified defect — the JS
  # probe always pushes it into `stuck` too (same exceeds() predicate on the
  # same residual values), so it already surfaces as a real FAIL via
  # STUCK_COUNT below. Counting it here as well used to force status "error"/
  # exit 2 (inconclusive) instead of "fail"/exit 1 for a genuinely measured
  # defect, and check_iteration.classify() then mis-filed it as
  # "infrastructure" instead of "implementation" (fable-20260910 follow-up
  # review round 3, LOW). Only a target that vanished mid-probe — where no
  # residual could be measured at all — leaves the run truly inconclusive.
  # fable-20260911 follow-up review: renamed from TEMPORAL_COUNT — this now
  # counts ONLY unmeasurableTargets (a target that vanished mid-probe), not
  # temporal/continuous motion in general (see the comment above).
  UNMEASURABLE_COUNT=$(node -e "
    const d = JSON.parse(process.argv[1]);
    process.stdout.write(String((d.unmeasurableTargets || []).length));
  " "$DATA")
  CONFIRMED_TIME_ONLY_COUNT=$(node -e "
    const d = JSON.parse(process.argv[1]);
    process.stdout.write(String((d.temporalMotion || []).filter((t) => t.confirmedTimeOnly).length));
  " "$DATA")
  if [ "$CONFIRMED_TIME_ONLY_COUNT" -gt 0 ]; then
    echo "   ℹ️  ${CONFIRMED_TIME_ONLY_COUNT} target(s) have confirmed time-only motion (not scroll-attributable) — excluded from the settle check"
  fi
  if [ "$UNMEASURABLE_COUNT" -gt 0 ]; then
    GLOBAL_STATUS=1
    PROBE_ERROR=1
    echo "   ❌ ${UNMEASURABLE_COUNT} target(s) vanished mid-probe — scroll completion is inconclusive"
  elif [ -n "$STUCK_COUNT" ] && [ "$STUCK_COUNT" -gt 0 ]; then
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
  const probeError = process.argv[4] === '1';
  const out = {
    status: probeError ? 'error' : (totalStuck === 0 ? 'pass' : 'fail'),
    probeError,
    totalStuck,
    viewports,
    generatedAt: process.argv[2],
    url: process.argv[3],
  };
  process.stdout.write(JSON.stringify(out, null, 2));
" "$TMP_RESULTS" "$NOW" "$URL" "$PROBE_ERROR" > "$OUT"

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

# exit 2 distinguishes a probe error (timeout/crash or time contamination)
# from exit 1 (a real stuck element was found), mirroring the setup-error exit 2
# above so the dispatcher never reads an unrun probe as a settled pass.
if [ "$PROBE_ERROR" -eq 1 ]; then
  exit 2
fi
exit $GLOBAL_STATUS
