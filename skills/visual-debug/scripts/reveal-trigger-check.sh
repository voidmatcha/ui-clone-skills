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

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

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
REVEAL_TRIGGER_BATCH_SIZE="${REVEAL_TRIGGER_BATCH_SIZE:-8}"

cleanup() {
  agent-browser --session "$SESSION" close 2>/dev/null
}
trap cleanup EXIT

cleanup_probes() {
  agent-browser --session "$SESSION" eval "(() => {
    document.querySelectorAll('[data-reveal-probe]').forEach(e => e.removeAttribute('data-reveal-probe'));
    return true;
  })()" >/dev/null 2>&1 || true
}

agent-browser --session "$SESSION" set viewport "$VIEW_W" "$VIEW_H" >/dev/null 2>&1
agent-browser --session "$SESSION" navigate "$URL" >/dev/null 2>&1
sleep $((WAIT_MS / 1000))

SPEC_SELECTORS_JSON="[]"
if [ -n "${REF_DIR:-}" ] && [ -f "${REF_DIR:-}/transition-spec.json" ]; then
  SPEC_SELECTORS_JSON=$(python3 "$SCRIPT_DIR/lib/reveal_trigger_artifact.py" \
    selectors "${REF_DIR:-}/transition-spec.json" 2>/dev/null || printf '[]')
fi
SPEC_SELECTORS_B64=$(printf '%s' "$SPEC_SELECTORS_JSON" | base64 | tr -d '\n')

# Phase 1: enumerate candidate hidden-init elements (opacity 0 or non-identity transform).
# We pin each by a stable index attribute so phase 2 can re-find them after scroll.
# Capture the candidate count N: phase 2 runs in bounded async batches because
# settle-per-candidate pages can exceed agent-browser's eval budget. A completed
# batch returns at least "[]"; an empty batch with N>0 is the unambiguous
# signature of a timed-out eval, so fail closed rather than treat it as all-clear.
PROBE_COUNT_RAW=$(agent-browser --session "$SESSION" eval '(() => {
  const SPEC_SELECTORS = JSON.parse(atob("'"$SPEC_SELECTORS_B64"'"));
  const hasScopedLegacy = Array.isArray(SPEC_SELECTORS) && SPEC_SELECTORS.length > 0;
  let invalidSpecSelector = "";
  const selectorMatchesSpec = (el) => {
    if (!hasScopedLegacy) return true;
    for (const selector of SPEC_SELECTORS) {
      try {
        if (el.matches(selector) || el.closest(selector)) return true;
      } catch (err) {
        invalidSpecSelector = selector;
        return false;
      }
    }
    return false;
  };
  const all = document.querySelectorAll("*");
  let n = 0;
  const SKIP_TAGS = new Set(["SCRIPT","STYLE","META","LINK","HEAD","TITLE","NOSCRIPT","NEXT-ROUTE-ANNOUNCER","HTML","BODY"]);
  for (const el of all) {
    if (invalidSpecSelector) break;
    if (SKIP_TAGS.has(el.tagName)) continue;
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden") continue;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    const stateRevealSpec = el.getAttribute("data-ui-clone-state-reveal") || "";
    const hasStateReveal = stateRevealSpec.trim().length > 0;
    const opacity = parseFloat(cs.opacity);
    const tform = cs.transform;
    if (hasStateReveal) {
      el.setAttribute("data-reveal-probe", String(n++));
      continue;
    }
    const opacityHidden = opacity === 0;
    const transformHidden = tform && tform !== "none" && tform !== "matrix(1, 0, 0, 1, 0, 0)";
    // Legacy style heuristics only cover CSS transitions. A keyframe animation
    // already attached before scrolling is timer/load motion, not evidence that
    // IntersectionObserver will start it. Generated state reveals use the marker
    // path above and are validated independently of computed style channels.
    const transitionProps = (cs.transitionProperty || "").split(",").map(p => p.trim());
    const transDurs = (cs.transitionDuration || "").split(",").map(d => parseFloat(d) || 0);
    const animName = cs.animationName || "none";
    if (animName !== "none") continue;
    const hasTimedTransition = transDurs.some(d => d > 0);
    const transitionsAll = transitionProps.includes("all");
    const initiallyHidden = hasTimedTransition && (
      (opacityHidden && (transitionsAll || transitionProps.includes("opacity"))) ||
      (transformHidden && (transitionsAll || transitionProps.includes("transform")))
    );
    if (!initiallyHidden) continue;
    if (hasScopedLegacy && !selectorMatchesSpec(el)) continue;
    // Skip hover-swap pattern: element has a visible sibling at the same position
    // (image+video stacked in a card). Those are hover-triggered swaps, not scroll reveals.
    const siblings = el.parentElement ? Array.from(el.parentElement.children) : [];
    const hasVisibleSwapSibling = siblings.some(sib => {
      if (sib === el) return false;
      const ss = getComputedStyle(sib);
      if (parseFloat(ss.opacity) === 0 || ss.display === "none" || ss.visibility === "hidden") return false;
      const sr = sib.getBoundingClientRect();
      return Math.abs(sr.width - r.width) < 2 && Math.abs(sr.height - r.height) < 2 && Math.abs(sr.left - r.left) < 2 && Math.abs(sr.top - r.top) < 2;
    });
    if (hasVisibleSwapSibling) continue;
    el.setAttribute("data-reveal-probe", String(n++));
  }
  if (invalidSpecSelector) return -1;
  return n;
})()' 2>/dev/null)
if [ "$PROBE_COUNT_RAW" = "-1" ]; then
  echo "⚠️  reveal-trigger: transition-spec contains an invalid reveal selector" >&2
  cleanup_probes
  if [ -n "${REF_DIR:-}" ] && [ -d "${REF_DIR:-}" ]; then
    python3 "$SCRIPT_DIR/lib/reveal_trigger_artifact.py" \
      error "$REF_DIR/reveal-trigger.json" "$URL" "${VIEW_W}x${VIEW_H}" 0
  fi
  exit 2
fi
PROBE_COUNT=$(echo "$PROBE_COUNT_RAW" | tr -dc '0-9')
PROBE_COUNT=${PROBE_COUNT:-0}

if ! [[ "$REVEAL_TRIGGER_BATCH_SIZE" =~ ^[0-9]+$ ]] || [ "$REVEAL_TRIGGER_BATCH_SIZE" -lt 1 ]; then
  echo "ERROR: REVEAL_TRIGGER_BATCH_SIZE must be a positive integer" >&2
  cleanup_probes
  exit 2
fi

# Phase 2: for each probed element, scroll into view, settle, recapture style.
# Build a parent-chain that highlights `overflow: hidden` ancestors. Run the
# async sweep in bounded batches so settle-per-candidate pages do not exceed the
# agent-browser eval budget. Any empty or invalid batch remains fail-closed.
BATCH_OUTPUTS=()
BATCH_START=0
while [ "$BATCH_START" -lt "$PROBE_COUNT" ]; do
  BATCH_END=$((BATCH_START + REVEAL_TRIGGER_BATCH_SIZE))
  if [ "$BATCH_END" -gt "$PROBE_COUNT" ]; then
    BATCH_END="$PROBE_COUNT"
  fi
  BATCH_RAW=$(agent-browser --session "$SESSION" eval "(async () => {
  const START = $BATCH_START;
  const END = $BATCH_END;
  const probes = Array.from(document.querySelectorAll('[data-reveal-probe]'))
    .filter(el => {
      const raw = el.getAttribute('data-reveal-probe') || '';
      const idx = Number.parseInt(raw, 10);
      return Number.isFinite(idx) && idx >= START && idx < END;
    });
  const out = [];
  const SETTLE = $SETTLE_MS;
  for (const el of probes) {
    const idx = el.getAttribute('data-reveal-probe');
    const csInit = getComputedStyle(el);
    const init = { opacity: csInit.opacity, transform: csInit.transform };
    const stateRevealSpec = el.getAttribute('data-ui-clone-state-reveal') || '';

    el.scrollIntoView({ block: 'center', behavior: 'instant' });
    await new Promise(r => setTimeout(r, SETTLE));

    const csPost = getComputedStyle(el);
    const post = { opacity: csPost.opacity, transform: csPost.transform };
    const stateRevealTokens = stateRevealSpec
      .trim()
      .split(/\s+/)
      .filter(Boolean);
    const invalidStateReveal = [];
    const stateReveal = stateRevealTokens
      .map(pair => {
        const eq = pair.indexOf('=');
        if (eq <= 0 || eq === pair.length - 1) {
          invalidStateReveal.push(pair);
          return null;
        }
        const name = pair.slice(0, eq);
        const expected = pair.slice(eq + 1);
        return { name, expected, actual: el.getAttribute(name) };
      })
      .filter(Boolean);
    const stateRevealStuck = stateReveal.filter(s => s.actual !== s.expected);

    // True stuck = post is STILL in a hidden-init state (opacity 0 or transform != identity).
    // If post.opacity === '1' AND post.transform is identity/none, the reveal completed —
    // any non-advance in init↔post just means we re-probed after the animation finished.
    const postIdentityTransform = !post.transform || post.transform === 'none' || post.transform === 'matrix(1, 0, 0, 1, 0, 0)';
    const postHidden = parseFloat(post.opacity) === 0 || !postIdentityTransform;
    const stuck =
      invalidStateReveal.length > 0 ||
      stateRevealStuck.length > 0 ||
      (
        stateReveal.length === 0 &&
        postHidden &&
        (init.opacity === post.opacity && init.transform === post.transform)
      );
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
      stateReveal,
      invalidStateReveal,
      chain,
    });
  }
  return JSON.stringify(out);
})()" 2>/dev/null)
  BATCH_DATA=$(echo "$BATCH_RAW" | sed 's/^"//;s/"$//' | sed 's/\\"/"/g')
  if [ -z "$BATCH_DATA" ]; then
    cleanup_probes
    echo "⚠️  reveal-trigger: phase-2 batch returned no output for candidate range ${BATCH_START}-${BATCH_END}" >&2
    echo "    — measurement did not complete (likely eval-budget timeout). Failing closed." >&2
    if [ -n "${REF_DIR:-}" ] && [ -d "${REF_DIR:-}" ]; then
      python3 "$SCRIPT_DIR/lib/reveal_trigger_artifact.py" \
        error "$REF_DIR/reveal-trigger.json" "$URL" "${VIEW_W}x${VIEW_H}" "$PROBE_COUNT"
    fi
    exit 2
  fi
  if ! python3 "$SCRIPT_DIR/lib/reveal_trigger_artifact.py" merge "$BATCH_DATA" >/dev/null 2>&1; then
    cleanup_probes
    echo "⚠️  reveal-trigger: phase-2 batch returned invalid JSON for candidate range ${BATCH_START}-${BATCH_END}" >&2
    if [ -n "${REF_DIR:-}" ] && [ -d "${REF_DIR:-}" ]; then
      python3 "$SCRIPT_DIR/lib/reveal_trigger_artifact.py" \
        error "$REF_DIR/reveal-trigger.json" "$URL" "${VIEW_W}x${VIEW_H}" "$PROBE_COUNT"
    fi
    exit 2
  fi
  BATCH_OUTPUTS+=("$BATCH_DATA")
  BATCH_START="$BATCH_END"
done
cleanup_probes

if [ "$PROBE_COUNT" -gt 0 ] 2>/dev/null; then
  RAW=$(python3 "$SCRIPT_DIR/lib/reveal_trigger_artifact.py" merge "${BATCH_OUTPUTS[@]}" 2>/dev/null)
else
  RAW="[]"
fi

DATA=$(echo "$RAW" | sed 's/^"//;s/"$//' | sed 's/\\"/"/g')

ART_OUT="${REF_DIR:-}"
if [ -n "$ART_OUT" ] && [ -d "$ART_OUT" ]; then
  ART_FILE="$ART_OUT/reveal-trigger.json"
else
  ART_FILE=""
fi

# F3 fail-closed: empty phase-2 output while phase-1 pinned candidates means the
# async sweep never completed (eval budget exceeded / dropped output), NOT that
# every reveal triggered. A completed sweep always returns at least "[]". Refuse to
# certify — emit an error artifact and exit setup-error so the harness re-measures
# (e.g. with a smaller viewport / chunked run) instead of trusting a false clean.
if [ -z "$DATA" ] && [ "$PROBE_COUNT" -gt 0 ] 2>/dev/null; then
  echo "⚠️  reveal-trigger: phase-2 sweep returned no output for $PROBE_COUNT candidate(s)" >&2
  echo "    — measurement did not complete (likely eval-budget timeout). Failing closed." >&2
  if [ -n "$ART_FILE" ]; then
    python3 "$SCRIPT_DIR/lib/reveal_trigger_artifact.py" \
      error "$ART_FILE" "$URL" "${VIEW_W}x${VIEW_H}" "$PROBE_COUNT"
  fi
  exit 2
fi

if [ -z "$DATA" ] || [ "$DATA" = "[]" ] || [ "$DATA" = "null" ]; then
  echo "✅ No stuck reveals found."
  if [ -n "$ART_FILE" ]; then
    python3 "$SCRIPT_DIR/lib/reveal_trigger_artifact.py" \
      pass "$ART_FILE" "$URL" "${VIEW_W}x${VIEW_H}" "$PROBE_COUNT"
  fi
  exit 0
fi
# Emit fail artifact (data is JSON array of stuck entries).
if [ -n "$ART_FILE" ]; then
  python3 "$SCRIPT_DIR/lib/reveal_trigger_artifact.py" \
    fail "$ART_FILE" "$URL" "${VIEW_W}x${VIEW_H}" "$DATA"
fi

echo "═══ Stuck Reveal Detection ═══"
echo "URL: $URL"
echo "Viewport: ${VIEW_W}x${VIEW_H}"
echo ""
echo "Elements with hidden-init style (opacity 0 or non-identity transform)"
echo "whose style did NOT advance after scrolling them into view."
echo ""

node "$SCRIPT_DIR/lib/reveal-trigger-report.js" "$DATA"
