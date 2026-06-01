#!/usr/bin/env bash
# scroll-state-machine-check.sh — Verify scroll-driven auto-return/snap state machines.
#
# A scroll transition is not complete when only its endpoint style matches. Some
# references use scroll progress plus timers/guards to move again after the user
# stops scrolling (smooth return, snap-back, section snap). This check compares
# the reference and implementation through three phases:
#   initial → active/expanded → settled/returned
#
# Usage:
#   bash scroll-state-machine-check.sh <session> <ref-url> <impl-url> <ref-dir> [w] [h]
#
# Output:
#   <ref-dir>/scroll-state-machine.json

set -uo pipefail

SESSION="${1:?Usage: scroll-state-machine-check.sh <session> <ref-url> <impl-url> <ref-dir> [w] [h]}"
REF_URL="${2:?Missing ref-url}"
IMPL_URL="${3:?Missing impl-url}"
REF_DIR="${4:?Missing ref-dir}"
WIDTH="${5:-1440}"
HEIGHT="${6:-1000}"

WAIT_MS="${WAIT_MS:-1200}"
ACTIVE_WAIT_MS="${ACTIVE_WAIT_MS:-180}"
SETTLE_WAIT_MS="${SETTLE_WAIT_MS:-1700}"
RETURN_THRESHOLD_PX="${RETURN_THRESHOLD_PX:-80}"

OUT="$REF_DIR/scroll-state-machine.json"
mkdir -p "$REF_DIR"

if ! command -v agent-browser >/dev/null 2>&1; then
  echo "ERROR: agent-browser not found. Install: npm i -g agent-browser" >&2
  exit 2
fi

has_pattern() {
  local pattern="$1"
  grep -R -Eiq "$pattern" \
    "$REF_DIR/bundles" \
    "$REF_DIR/transition-spec.json" \
    "$REF_DIR/scroll-engine.json" \
    "$REF_DIR/animation-runtime-dump.json" \
    2>/dev/null
}

HAS_PROGRESS_SIGNAL="false"
HAS_CONTROL_SIGNAL="false"
if has_pattern 'scrollYProgress|useScroll|ScrollTrigger|scrollY\.on|scroll[^[:alnum:]]*progress'; then
  HAS_PROGRESS_SIGNAL="true"
fi
if has_pattern 'window\.scrollTo|[^[:alnum:]_]scrollTo[[:space:]]*\(|scrollIntoView|setTimeout|clearTimeout|getVelocity|velocity|guardRef|autoReturning|isScrolling'; then
  HAS_CONTROL_SIGNAL="true"
fi
if has_pattern 'ScrollTrigger|gsap-scrolltrigger|sticky-scrub|scroll-scrub|scroll-pin|scrub[[:space:]]*:|pin[[:space:]]*:'; then
  HAS_PROGRESS_SIGNAL="true"
  HAS_CONTROL_SIGNAL="true"
fi

if [ "$HAS_PROGRESS_SIGNAL" != "true" ] || [ "$HAS_CONTROL_SIGNAL" != "true" ]; then
  cat > "$OUT" <<JSON
{
  "status": "pass",
  "skipped": "no-scroll-state-machine-signal",
  "signals": {
    "progress": $HAS_PROGRESS_SIGNAL,
    "control": $HAS_CONTROL_SIGNAL
  }
}
JSON
  echo "✅ Scroll state-machine: skipped (no bundle/spec signal)"
  echo "   Output: $OUT"
  exit 0
fi

REF_SESSION="${SESSION}-ref"
IMPL_SESSION="${SESSION}-impl"
TMP_REF=$(mktemp)
TMP_IMPL=$(mktemp)

# shellcheck disable=SC2329 # Invoked via trap.
cleanup() {
  rm -f "$TMP_REF" "$TMP_IMPL"
  agent-browser --session "$REF_SESSION" close >/dev/null 2>&1 || true
  agent-browser --session "$IMPL_SESSION" close >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

probe_page() {
  local session="$1"
  local url="$2"
  local output="$3"

  agent-browser --session "$session" set viewport "$WIDTH" "$HEIGHT" >/dev/null 2>&1 || return 1
  agent-browser --session "$session" navigate "$url" >/dev/null 2>&1 || return 1
  agent-browser --session "$session" eval --json "(async () => {
    const WAIT_MS = $WAIT_MS;
    const ACTIVE_WAIT_MS = $ACTIVE_WAIT_MS;
    const SETTLE_WAIT_MS = $SETTLE_WAIT_MS;
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const scrollingElement = () => document.scrollingElement || document.documentElement || document.body;
    const maxScroll = () => Math.max(
      0,
      Math.max(
        document.documentElement ? document.documentElement.scrollHeight : 0,
        document.body ? document.body.scrollHeight : 0
      ) - window.innerHeight
    );
    const parseScaleY = (transform) => {
      if (!transform || transform === 'none') return 1;
      const matrix = transform.match(/matrix\\(([^)]+)\\)/);
      if (matrix) {
        const values = matrix[1].split(',').map((value) => Number(value.trim()));
        return Number.isFinite(values[3]) ? values[3] : 1;
      }
      const matrix3d = transform.match(/matrix3d\\(([^)]+)\\)/);
      if (matrix3d) {
        const values = matrix3d[1].split(',').map((value) => Number(value.trim()));
        return Number.isFinite(values[5]) ? values[5] : 1;
      }
      return 1;
    };
    const sampleMovingElements = () => Array.from(document.querySelectorAll('*'))
      .filter((element) => {
        const rect = element.getBoundingClientRect();
        if (rect.width < 20 || rect.height < 8) return false;
        const style = getComputedStyle(element);
        return style.display !== 'none' && style.visibility !== 'hidden' && (
          style.transform !== 'none' || Number(style.opacity) < 0.99 || style.position === 'sticky' || style.position === 'fixed'
        );
      })
      .slice(0, 12)
      .map((element) => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return {
          tag: element.tagName.toLowerCase(),
          id: element.id || null,
          className: typeof element.className === 'string' ? element.className.split(/\\s+/).slice(0, 4).join(' ') : null,
          y: Math.round(rect.y),
          height: Math.round(rect.height),
          opacity: Number(style.opacity),
          transform: style.transform,
          scaleY: Number(parseScaleY(style.transform).toFixed(4)),
        };
      });
    const state = (phase) => ({
      phase,
      scrollY: Math.round(window.scrollY || scrollingElement().scrollTop || 0),
      maxScroll: Math.round(maxScroll()),
      htmlClass: document.documentElement ? document.documentElement.className : '',
      bodyClass: document.body ? document.body.className : '',
      moving: sampleMovingElements(),
    });

    window.scrollTo({ top: 0, behavior: 'instant' });
    await sleep(WAIT_MS);
    const initial = state('initial');
    const target = maxScroll();
    window.scrollTo({ top: target, behavior: 'instant' });
    await sleep(ACTIVE_WAIT_MS);
    const active = state('active');
    await sleep(SETTLE_WAIT_MS);
    const settled = state('settled');

    return {
      url: location.href,
      viewport: { width: window.innerWidth, height: window.innerHeight },
      timing: { waitMs: WAIT_MS, activeWaitMs: ACTIVE_WAIT_MS, settleWaitMs: SETTLE_WAIT_MS },
      initial,
      active,
      settled,
    };
  })()" > "$output"
}

if ! probe_page "$REF_SESSION" "$REF_URL" "$TMP_REF"; then
  echo "ERROR: failed to probe reference URL" >&2
  exit 2
fi
if ! probe_page "$IMPL_SESSION" "$IMPL_URL" "$TMP_IMPL"; then
  echo "ERROR: failed to probe implementation URL" >&2
  exit 2
fi

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
if ! RESULT_STATUS=$(node - "$TMP_REF" "$TMP_IMPL" "$OUT" "$NOW" "$RETURN_THRESHOLD_PX" <<'NODE'
const fs = require('fs');

const [refPath, implPath, outPath, generatedAt, thresholdRaw] = process.argv.slice(2);
const threshold = Number(thresholdRaw) || 80;
const unwrapAgentBrowserResult = (payload) => {
  if (payload && payload.success === true && payload.data && Object.prototype.hasOwnProperty.call(payload.data, 'result')) {
    return payload.data.result;
  }
  if (payload && payload.data && Object.prototype.hasOwnProperty.call(payload.data, 'result')) {
    return payload.data.result;
  }
  return payload;
};
const ref = unwrapAgentBrowserResult(JSON.parse(fs.readFileSync(refPath, 'utf8')));
const impl = unwrapAgentBrowserResult(JSON.parse(fs.readFileSync(implPath, 'utf8')));

const delta = (probe) => (probe.active?.scrollY ?? 0) - (probe.settled?.scrollY ?? 0);
const sign = (value) => (value === 0 ? 0 : value > 0 ? 1 : -1);
const refDelta = delta(ref);
const implDelta = delta(impl);
const refMovedAfterStop = Math.abs(refDelta) >= threshold;
const requiredImplDelta = Math.max(threshold, Math.abs(refDelta) * 0.5);
const implMovedAfterStop = Math.abs(implDelta) >= requiredImplDelta && sign(implDelta) === sign(refDelta);

let status = 'pass';
let reason = 'scroll state-machine parity observed';
if (!refMovedAfterStop) {
  reason = 'reference did not auto-move after scroll stop at this viewport; runtime parity check skipped after bundle signal';
} else if (!implMovedAfterStop) {
  status = 'fail';
  reason = 'reference auto-moves after scroll stop, but implementation does not match the settled/returned phase';
}

const out = {
  status,
  reason,
  generatedAt,
  thresholdPx: threshold,
  requiredImplDelta: Number(requiredImplDelta.toFixed(2)),
  phases: 'initial → active/expanded → settled/returned',
  ref: {
    initial: ref.initial,
    active: ref.active,
    settled: ref.settled,
    deltaAfterStop: refDelta,
    movedAfterStop: refMovedAfterStop,
  },
  impl: {
    initial: impl.initial,
    active: impl.active,
    settled: impl.settled,
    deltaAfterStop: implDelta,
    movedAfterStop: implMovedAfterStop,
  },
};
fs.writeFileSync(outPath, JSON.stringify(out, null, 2));
process.stdout.write(status);
NODE
); then
  echo "ERROR: failed to aggregate scroll state-machine probe" >&2
  exit 2
fi

if [ "$RESULT_STATUS" = "pass" ]; then
  echo "✅ Scroll state-machine: PASS"
  echo "   Output: $OUT"
  exit 0
fi

echo "❌ Scroll state-machine: FAIL"
echo "   Output: $OUT"
echo "   Required proof: initial → active/expanded → settled/returned"
echo "   Common cause: implementation copied only the expanded endpoint and missed the ref's scroll stop timer / smooth return / guard ref."
exit 1
