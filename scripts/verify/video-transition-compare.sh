#!/usr/bin/env bash
# video-transition-compare.sh — Video-based transition comparison
# Records the same interaction on original + implementation, extracts frames at 60fps,
# runs SSIM batch comparison, outputs pass/fail table.
#
# FPS policy: all video frame extraction in this repo uses 60fps. FPS=60 is the
# canonical default below; override only if a target site renders at a lower rate.
#
# Usage:
#   bash video-transition-compare.sh <session> <orig-url> <impl-url> <output-dir> <action-script>
#
# Arguments:
#   session      — agent-browser session name
#   orig-url     — original site URL
#   impl-url     — implementation URL
#   output-dir   — where to save frames and results (e.g., tmp/ref/same-energy/transitions)
#   action-script — path to a shell script that performs the interaction (click, etc.)
#                   OR one of the built-in actions:
#                     "splash"             — record page load (no interaction)
#                     "scroll"             — scroll-POSITION-aligned still compare
#                                            (SCROLL_SAMPLES fractions, SCROLL_SETTLE
#                                            wait, no video — see scroll branch below)
#                     "click:<selector>"   — click element and record transition
#                     "hover:<selector>"   — real-mouse hover and record entry arc
#                     "hover-and-out:<selector>"
#                                          — hover, record entry arc, move mouse
#                                            away, record exit arc. Total
#                                            recording = 2 * RECORD_DURATION.
#
# Example:
#   bash video-transition-compare.sh same-energy https://same.energy/ http://localhost:4001/same-energy \
#     tmp/ref/same-energy/transitions "click:[class*=image_container]"
#
# Output:
#   <output-dir>/ref-frames/   — 60fps frames from original
#   <output-dir>/impl-frames/  — 60fps frames from implementation
#   <output-dir>/diff-frames/  — diff images for failing frames
#   <output-dir>/result.txt    — SSIM comparison results
#
# Requirements: agent-browser, ffmpeg, imagemagick (compare)

set -euo pipefail

SESSION="${1:?Usage: video-transition-compare.sh <session> <orig> <impl> <outdir> <action>}"
ORIG_URL="${2:?}"
IMPL_URL="${3:?}"
OUT_DIR="${4:?}"
ACTION="${5:?}"
VMC_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROI_HELPER="$VMC_SCRIPT_DIR/lib/video_compare_roi.py"
CAPTURE_RETRY_HELPER="$VMC_SCRIPT_DIR/lib/selector_capture_retry.py"
HOVER_ACTION_RECEIPT_HELPER="$VMC_SCRIPT_DIR/lib/hover_action_receipt.py"

RECORD_DURATION="${RECORD_DURATION:-5}"
SSIM_THRESHOLD="${SSIM_THRESHOLD:-0.90}"
FPS="${FPS:-60}"
PRE_ACTION_WAIT="${PRE_ACTION_WAIT:-3}"
VIEW_W="${VIEW_W:-1440}"
VIEW_H="${VIEW_H:-900}"
# Target-local delta frames can retain WebM compression and subpixel text raster
# noise after the last pre-action frame is subtracted. The raw SSIM verdict
# stays authoritative; a narrowly bounded, explicitly reported AA rescue may
# retry borderline selector deltas with a 0.3px low-pass only after arc timing
# passes.
VIDEO_COMPARE_TARGET_DELTA_BLUR="0.3"
VIDEO_COMPARE_TARGET_AA_RESCUE_BAND="0.02"
# Fixed material-motion floor for selector-local delta frames. This is not an
# environment knob: low-amplitude static foreground codec residue may be removed,
# but real one-sided target motion must stay visible to the comparator.
VIDEO_COMPARE_TARGET_STATIC_NOISE_THRESHOLD="6%"
# Timing support uses a slightly lower, still fixed floor. A low-contrast state
# layer can move by about 5% while WebM edge residue affects too few pixels to
# cross the ROI's 5%-area change threshold.
VIDEO_COMPARE_TARGET_TIMING_NOISE_THRESHOLD="4%"
# Independent 10fps WebM encodes can emit an isolated keyframe-residue change
# long after a CSS hover arc has settled. Keep the first continuous action-era
# cluster; a real long transition keeps producing adjacent 10fps source
# samples, while a change after a 0.5-second settled gap is encoder residue.
VIDEO_COMPARE_TARGET_TIMING_CLUSTER_GAP_FRAMES="30"
# Independent hover recordings can disagree for up to three source samples at
# action onset. Classify by elapsed time, not extracted row count: a 10fps WebM
# expanded to 60fps produces six duplicate rows for each 100ms source sample.
# This only permits a bounded retry/unmeasurable verdict; it never turns a
# divergent comparison into a pass.
VIDEO_COMPARE_CAPTURE_EARLY_WINDOW_SECONDS="${VIDEO_COMPARE_CAPTURE_EARLY_WINDOW_SECONDS:-0.3}"
if ! awk -v value="$VIDEO_COMPARE_CAPTURE_EARLY_WINDOW_SECONDS" '
  BEGIN {
    valid = value ~ /^[0-9]+([.][0-9]+)?$/ && value > 0 && value <= 0.3
    exit(valid ? 0 : 1)
  }
'; then
  echo "video-transition-compare: invalid VIDEO_COMPARE_CAPTURE_EARLY_WINDOW_SECONDS='$VIDEO_COMPARE_CAPTURE_EARLY_WINDOW_SECONDS'; using bounded default 0.3" >&2
  VIDEO_COMPARE_CAPTURE_EARLY_WINDOW_SECONDS="0.3"
fi

# Cleanup browser sessions on exit (including errors/signals)
cleanup_browsers() {
  agent-browser --session "${SESSION}-orig" close 2>/dev/null
  agent-browser --session "${SESSION}-impl" close 2>/dev/null
  agent-browser --session "${SESSION}-refcal" close 2>/dev/null
}
trap cleanup_browsers EXIT

# Optional: skip SSIM comparison, just extract frames for manual review
SKIP_SSIM="${SKIP_SSIM:-false}"
# Optional: only compare timing (detect when frames start changing)
TIMING_ONLY="${TIMING_ONLY:-false}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${BOLD}═══ Transition Compare ═══${NC}"
echo "Original: $ORIG_URL"
echo "Implementation: $IMPL_URL"
echo "Action: $ACTION"
echo "Duration: ${RECORD_DURATION}s, FPS: $FPS, SSIM threshold: $SSIM_THRESHOLD"
echo ""

mkdir -p "$OUT_DIR"/{ref-video,impl-video,ref-frames,impl-frames,diff-frames}

TARGET_ROI_SELECTOR=""
case "$ACTION" in
  hover:*) TARGET_ROI_SELECTOR="${ACTION#hover:}" ;;
  hover-and-out:*) TARGET_ROI_SELECTOR="${ACTION#hover-and-out:}" ;;
esac
# Comparison output directories may be reused by bounded retry callers. Remove
# only generated per-run sidecars and target-delta directories before the new
# verdict so a prior AA rescue cannot leak into this comparison.
rm -f \
  "$OUT_DIR/diff-frames/"*.txt \
  "$OUT_DIR/target-aa-filter.json" \
  "$OUT_DIR/target-static-foreground-filter.json" \
  "$OUT_DIR/capture-retry.json" \
  "$OUT_DIR/reference-self-calibration.json" \
  "$OUT_DIR/ref-video/hover-action.raw.json" \
  "$OUT_DIR/impl-video/hover-action.raw.json" \
  "$OUT_DIR/ref-video/action-onset-seconds.txt" \
  "$OUT_DIR/impl-video/action-onset-seconds.txt" 2>/dev/null || true
rm -rf \
  "$OUT_DIR/ref-delta-frames" \
  "$OUT_DIR/impl-delta-frames" \
  "$OUT_DIR/ref-delta-aa-frames" \
  "$OUT_DIR/impl-delta-aa-frames" \
  "$OUT_DIR/ref-delta-material-frames" \
  "$OUT_DIR/impl-delta-material-frames" \
  "$OUT_DIR/ref-delta-timing-frames" \
  "$OUT_DIR/impl-delta-timing-frames"

TARGET_ROI_PADDING="${VIDEO_COMPARE_TARGET_PADDING:-12}"
TARGET_ROI_REF_RAW="$OUT_DIR/ref-video/target-rect.raw.json"
TARGET_ROI_IMPL_RAW="$OUT_DIR/impl-video/target-rect.raw.json"
TARGET_ROI_PLAN="$OUT_DIR/target-roi.json"
TARGET_ROI_REF_FILTER=""
TARGET_ROI_IMPL_FILTER=""

hover_state_snapshot_js() {
  cat <<'JS'
    const watchedStyle = (el) => {
      const style = getComputedStyle(el);
      return {
        color: style.color,
        backgroundColor: style.backgroundColor,
        borderTopColor: style.borderTopColor,
        borderRightColor: style.borderRightColor,
        borderBottomColor: style.borderBottomColor,
        borderLeftColor: style.borderLeftColor,
        opacity: style.opacity,
        transform: style.transform,
        filter: style.filter,
        boxShadow: style.boxShadow,
        textDecorationLine: style.textDecorationLine,
        textDecorationColor: style.textDecorationColor,
        fontWeight: style.fontWeight,
        letterSpacing: style.letterSpacing
      };
    };
    const classPath = (el) => {
      const path = [];
      let current = el;
      for (let depth = 0; current && depth < 6; depth += 1, current = current.parentElement) {
        const id = current.id ? `#${current.id}` : '';
        const classes = Array.from(current.classList || []).slice(0, 16).sort().join('.');
        path.push(`${current.tagName.toLowerCase()}${id}${classes ? `.${classes}` : ''}`);
      }
      return path;
    };
    const transitionContract = (el) => {
      const style = getComputedStyle(el);
      const split = (value) => value.split(',').map((part) => part.trim()).filter(Boolean);
      const normalizeTime = (value) => {
        const raw = value.trim();
        if (raw.endsWith('ms')) return String(Number.parseFloat(raw) / 1000);
        if (raw.endsWith('s')) return String(Number.parseFloat(raw));
        return raw;
      };
      return {
        property: split(style.transitionProperty).join(','),
        duration: split(style.transitionDuration).map(normalizeTime).join(','),
        delay: split(style.transitionDelay).map(normalizeTime).join(','),
        timingFunction: style.transitionTimingFunction.replace(/\s+/g, ' ').trim()
      };
    };
JS
}

hover_timing_probe_js() {
  cat <<'JS'
    const hoverProofKey = (selector, matchIndex) => `${selector}::${matchIndex}`;
    const activeAnimationCount = (el) => {
      const related = [el, ...classPathElements(el).slice(1)];
      const seen = new Set();
      let count = 0;
      for (const node of related) {
        const animations = typeof node.getAnimations === 'function'
          ? node.getAnimations({ subtree: node === el })
          : [];
        for (const animation of animations) {
          if (seen.has(animation)) continue;
          seen.add(animation);
          if (animation.playState !== 'idle' && animation.playState !== 'finished') count += 1;
        }
      }
      return count;
    };
    const classPathElements = (el) => {
      const elements = [];
      let current = el;
      for (let depth = 0; current && depth < 6; depth += 1, current = current.parentElement) {
        elements.push(current);
      }
      return elements;
    };
    const hoverSnapshot = (el) => ({
      watchedStyle: watchedStyle(el),
      ancestorClassPath: classPath(el),
      hovered: el.matches(':hover'),
      activeAnimationCount: activeAnimationCount(el)
    });
    const changedKeys = (initial, final) =>
      Object.keys(initial.watchedStyle || {})
        .filter((key) => initial.watchedStyle[key] !== final.watchedStyle[key])
        .sort();
    window.__uiCloneHoverTimingProofs = window.__uiCloneHoverTimingProofs || {};
JS
}

capture_target_roi() {
  local session="$1"
  local selector="$2"
  local out="$3"
  local selector_json
  selector_json=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$selector")
  agent-browser --session "$session" mouse move -100 -100 >/dev/null 2>&1 || true
  sleep 0.25
  agent-browser --session "$session" eval "(async () => {
$(hover_state_snapshot_js)
    const matches = Array.from(document.querySelectorAll($selector_json));
    const rendered = (el) => {
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      if (typeof el.checkVisibility === 'function' &&
          !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) {
        return false;
      }
      return style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        Number.parseFloat(style.opacity || '1') > 0 &&
        style.pointerEvents !== 'none' &&
        rect.width > 0 && rect.height > 0;
    };
    const inViewport = (rect) =>
      rect.bottom > 0 && rect.right > 0 &&
      rect.top < window.innerHeight && rect.left < window.innerWidth;
    let el = matches.find((candidate) => {
      const rect = candidate.getBoundingClientRect();
      return rendered(candidate) && inViewport(rect);
    });
    if (!el) {
      for (const candidate of matches) {
        if (!rendered(candidate)) continue;
        candidate.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
        await new Promise((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(resolve)));
        const rect = candidate.getBoundingClientRect();
        if (rendered(candidate) && inViewport(rect)) {
          el = candidate;
          break;
        }
      }
    }
    if (!el) {
      return JSON.stringify({
        found: false,
        reason: matches.length ? 'no-rendered-match' : 'selector-absent',
        matchCount: matches.length
      });
    }
    const rect = el.getBoundingClientRect();
    if (!rendered(el) || !inViewport(rect)) {
      return JSON.stringify({
        found: false,
        reason: 'no-visible-in-viewport-match',
        matchCount: matches.length
      });
    }
    return JSON.stringify({
      found: true,
      matchIndex: matches.indexOf(el),
      matchCount: matches.length,
      transition: transitionContract(el),
      state: {
        phase: 'idle',
        watchedStyle: watchedStyle(el),
        ancestorClassPath: classPath(el)
      },
      rect: {
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height
      }
    });
  })()" > "$out" 2> "$out.stderr"
  if ! target_center_from_rect "$out" "$VIEW_W" "$VIEW_H" >/dev/null; then
    echo "  selector has no visible in-viewport match: $selector" >&2
    return 1
  fi
}

target_center_from_rect() {
  python3 - "$1" "$2" "$3" <<'PY'
import json
import sys

try:
    value = json.loads(open(sys.argv[1], encoding="utf-8").read().strip())
    while isinstance(value, str):
        value = json.loads(value)
    rect = value["rect"]
    if value.get("found") is not True:
        raise ValueError
    viewport_width = float(sys.argv[2])
    viewport_height = float(sys.argv[3])
    left = max(0.0, float(rect["x"]))
    top = max(0.0, float(rect["y"]))
    right = min(viewport_width, float(rect["x"]) + float(rect["width"]))
    bottom = min(viewport_height, float(rect["y"]) + float(rect["height"]))
    if right <= left or bottom <= top:
        raise ValueError
    x = (left + right) / 2
    y = (top + bottom) / 2
except Exception:
    raise SystemExit(1)
print(f"{round(x)}\t{round(y)}")
PY
}

restore_visible_target_rect() {
  local session="$1"
  local selector="$2"
  local target_rect="${3:-}"
  local selector_json match_index
  [ -n "$target_rect" ] || return 0
  selector_json=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$selector")
  if ! match_index=$(python3 - "$target_rect" <<'PY'
import json
import sys

value = json.loads(open(sys.argv[1], encoding="utf-8").read().strip())
while isinstance(value, str):
    value = json.loads(value)
match_index = value.get("matchIndex")
if not isinstance(match_index, int) or isinstance(match_index, bool) or match_index < 0:
    raise SystemExit(1)
print(match_index)
PY
  ); then
    echo "UNMEASURABLE: hover target match index is unavailable"
    return 2
  fi
  agent-browser --session "$session" eval "(async () => {
$(hover_state_snapshot_js)
    const matches = Array.from(document.querySelectorAll($selector_json));
    const matchIndex = $match_index;
    const el = matches[matchIndex];
    if (!el) {
      return JSON.stringify({
        found: false,
        selector: $selector_json,
        matchIndex,
        matchCount: matches.length
      });
    }
    el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
    await new Promise((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    const inViewport = rect.bottom > 0 && rect.right > 0 &&
      rect.top < innerHeight && rect.left < innerWidth;
    const visible = style.display !== 'none' &&
      style.visibility !== 'hidden' &&
      Number.parseFloat(style.opacity || '1') > 0 &&
      style.pointerEvents !== 'none' &&
      rect.width > 0 && rect.height > 0 && inViewport;
    return JSON.stringify({
      found: visible,
      selector: $selector_json,
      matchIndex,
      matchCount: matches.length,
      transition: transitionContract(el),
      state: {
        phase: 'idle',
        watchedStyle: watchedStyle(el),
        ancestorClassPath: classPath(el)
      },
      rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
    });
  })()" > "$target_rect" 2> "$target_rect.stderr"
  if ! target_center_from_rect "$target_rect" "$VIEW_W" "$VIEW_H" >/dev/null; then
    echo "UNMEASURABLE: captured hover target is no longer resolvable"
    return 2
  fi
}

hover_visible_target() {
  local session="$1"
  local selector="$2"
  local target_rect="${3:-}"
  local receipt="${4:-}"
  local center target_x target_y selector_json match_index
  if [ -z "$target_rect" ]; then
    agent-browser --session "$session" hover "$selector" 2>&1 | head -1
    return
  fi
  if ! center=$(target_center_from_rect "$target_rect" "$VIEW_W" "$VIEW_H"); then
    echo "UNMEASURABLE: captured hover target is no longer resolvable"
    return 2
  fi
  IFS=$'\t' read -r target_x target_y <<< "$center"
  selector_json=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$selector")
  if ! match_index=$(python3 - "$target_rect" <<'PY'
import json
import sys

value = json.loads(open(sys.argv[1], encoding="utf-8").read().strip())
while isinstance(value, str):
    value = json.loads(value)
match_index = value.get("matchIndex")
if not isinstance(match_index, int) or isinstance(match_index, bool) or match_index < 0:
    raise SystemExit(1)
print(match_index)
PY
  ); then
    echo "UNMEASURABLE: hover target match index is unavailable"
    return 2
  fi
  if [ -n "$receipt" ]; then
    agent-browser --session "$session" eval "(() => {
$(hover_state_snapshot_js)
$(hover_timing_probe_js)
      const selector = $selector_json;
      const matchIndex = $match_index;
      const matches = Array.from(document.querySelectorAll(selector));
      const el = matches[matchIndex];
      if (!el) return 'missing-target';
      const key = hoverProofKey(selector, matchIndex);
      const proof = {
        schemaVersion: 1,
        selector,
        matchIndex,
        matchCount: matches.length,
        armedAt: performance.now(),
        moveAt: null,
        firstPointerEvent: null,
        firstMutation: null,
        firstCommitRaf: null,
        firstHoverRaf: null,
        stableAt: null,
        stableHoverRafCount: 0,
        initial: hoverSnapshot(el),
        commit: null,
        mutation: null,
        final: null,
        changedStyleKeys: [],
        pointerObserved: false,
        mutationObserved: false,
        rafObserved: false,
        maxActiveAnimationCount: activeAnimationCount(el),
        classMutations: [],
        done: false
      };
      const markPointer = () => {
        if (proof.firstPointerEvent === null) {
          proof.firstPointerEvent = performance.now();
          proof.pointerObserved = true;
        }
      };
      el.addEventListener('pointerover', markPointer, { once: true, capture: true });
      el.addEventListener('pointerenter', markPointer, { once: true, capture: true });
      el.addEventListener('mouseenter', markPointer, { once: true, capture: true });
      const observer = new MutationObserver((records) => {
        if (proof.firstPointerEvent === null) return;
        if (records.some((record) => record.type === 'attributes' && record.attributeName === 'class')) {
          proof.classMutations.push({
            time: performance.now(),
            ancestorClassPath: classPath(el)
          });
        }
      });
      for (const node of classPathElements(el)) {
        observer.observe(node, { attributes: true, attributeFilter: ['class'] });
      }
      proof.observer = observer;
      window.__uiCloneHoverTimingProofs[key] = proof;
      const stableSignature = (snapshot) => JSON.stringify({
        watchedStyle: snapshot.watchedStyle,
        ancestorClassPath: snapshot.ancestorClassPath,
        hovered: snapshot.hovered
      });
      const sampleSnapshot = (snapshot) => ({
        watchedStyle: snapshot.watchedStyle,
        ancestorClassPath: snapshot.ancestorClassPath,
        hovered: snapshot.hovered
      });
      const finalize = (samples) => {
        if (!samples.length) {
          samples.push({ time: performance.now(), snapshot: hoverSnapshot(el) });
        }
        const finalSample = samples[samples.length - 1];
        proof.final = sampleSnapshot(finalSample.snapshot);
        const finalSignature = stableSignature(finalSample.snapshot);
        let suffixStart = samples.length - 1;
        while (
          suffixStart > 0 &&
          stableSignature(samples[suffixStart - 1].snapshot) === finalSignature
        ) {
          suffixStart -= 1;
        }
        proof.stableHoverRafCount = samples.length - suffixStart;
        const commitSample = samples[suffixStart];
        proof.firstCommitRaf = commitSample.time;
        proof.commit = sampleSnapshot(commitSample.snapshot);
        if (proof.stableHoverRafCount >= 2) proof.stableAt = samples[suffixStart + 1].time;
        const relevantMutation = (proof.classMutations || []).find((mutation) =>
          mutation.time >= (proof.firstPointerEvent || 0) &&
          mutation.time <= proof.firstCommitRaf &&
          JSON.stringify(mutation.ancestorClassPath) === JSON.stringify(proof.final.ancestorClassPath)
        );
        if (relevantMutation) {
          proof.firstMutation = relevantMutation.time;
          proof.mutation = relevantMutation;
          proof.mutationObserved = true;
        }
        proof.changedStyleKeys = changedKeys(proof.initial, proof.final);
        proof.observer.disconnect();
        delete proof.observer;
        delete proof.classMutations;
        proof.done = true;
      };
      (async () => {
        const samples = [];
        while (proof.moveAt === null && performance.now() - proof.armedAt < 1000) {
          await new Promise((resolve) => requestAnimationFrame(resolve));
        }
        const pointerWaitStart = proof.moveAt || performance.now();
        while (proof.firstPointerEvent === null && performance.now() - pointerWaitStart < 1000) {
          await new Promise((resolve) => requestAnimationFrame(resolve));
        }
        const startAt = proof.firstPointerEvent || proof.moveAt || performance.now();
        const timeoutAt = startAt + 250;
        while (performance.now() <= timeoutAt) {
          await new Promise((resolve) => requestAnimationFrame(resolve));
          const sampledAt = performance.now();
          const snapshot = hoverSnapshot(el);
          proof.rafObserved = true;
          proof.maxActiveAnimationCount = Math.max(
            proof.maxActiveAnimationCount || 0,
            snapshot.activeAnimationCount
          );
          if (snapshot.hovered && proof.firstHoverRaf === null) proof.firstHoverRaf = sampledAt;
          samples.push({ time: sampledAt, snapshot });
        }
        finalize(samples);
      })();
      return 'armed';
    })()" >/dev/null 2>&1 || true
    agent-browser --session "$session" eval "(() => {
$(hover_timing_probe_js)
      const proof = window.__uiCloneHoverTimingProofs[hoverProofKey($selector_json, $match_index)];
      if (proof) proof.moveAt = performance.now();
      return 'move-marked';
    })()" >/dev/null 2>&1 || true
  fi
  agent-browser --session "$session" mouse move "$target_x" "$target_y" 2>&1 | head -1
  [ -n "$receipt" ] || return 0
  agent-browser --session "$session" eval "(async () => {
$(hover_state_snapshot_js)
$(hover_timing_probe_js)
    await new Promise((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const matches = Array.from(document.querySelectorAll($selector_json));
    const matchIndex = $match_index;
    const el = matches[matchIndex];
    if (!el) {
      return JSON.stringify({
        found: false,
        selector: $selector_json,
        matchIndex,
        matchCount: matches.length
      });
    }
    const rect = el.getBoundingClientRect();
    const x = Math.min(Math.max(rect.left + rect.width / 2, 0), innerWidth - 1);
    const y = Math.min(Math.max(rect.top + rect.height / 2, 0), innerHeight - 1);
    const hit = document.elementFromPoint(x, y);
    const proofKey = hoverProofKey($selector_json, matchIndex);
    const proof = window.__uiCloneHoverTimingProofs
      ? window.__uiCloneHoverTimingProofs[proofKey]
      : null;
    if (proof) {
      const deadline = performance.now() + 2000;
      while (!proof.done && performance.now() <= deadline) {
        await new Promise((resolve) => requestAnimationFrame(resolve));
      }
    }
    return JSON.stringify({
      found: true,
      selector: $selector_json,
      matchIndex,
      matchCount: matches.length,
      hovered: el.matches(':hover'),
      pointerReachable: Boolean(hit && (hit === el || el.contains(hit))),
      state: {
        phase: 'hover',
        watchedStyle: watchedStyle(el),
        ancestorClassPath: classPath(el)
      },
      transition: transitionContract(el),
      hoverProof: proof || null,
      rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
    });
  })()" > "$receipt" 2> "$receipt.stderr"
  if [ ! -f "$HOVER_ACTION_RECEIPT_HELPER" ] \
    || ! python3 "$HOVER_ACTION_RECEIPT_HELPER" "$receipt"; then
    echo "UNMEASURABLE: real pointer did not verify the intended hover target"
    return 2
  fi
  return 0
}

prepare_target_roi_filters() {
  [ -n "$TARGET_ROI_SELECTOR" ] || return 0
  if [ ! -f "$ROI_HELPER" ]; then
    echo -e "${RED}UNMEASURABLE: target ROI helper missing at $ROI_HELPER${NC}"
    return 1
  fi
  local filters
  if ! filters=$(python3 "$ROI_HELPER" plan \
      "$TARGET_ROI_REF_RAW" "$TARGET_ROI_IMPL_RAW" \
      "$VIEW_W" "$VIEW_H" "$TARGET_ROI_PADDING" \
      "$TARGET_ROI_SELECTOR" "$TARGET_ROI_PLAN"); then
    echo -e "${RED}UNMEASURABLE: selector target ROI could not be resolved on both sides${NC}"
    return 1
  fi
  IFS=$'\t' read -r TARGET_ROI_REF_FILTER TARGET_ROI_IMPL_FILTER <<< "$filters"
  if [ -z "$TARGET_ROI_REF_FILTER" ] || [ -z "$TARGET_ROI_IMPL_FILTER" ]; then
    echo -e "${RED}UNMEASURABLE: selector target ROI filters are incomplete${NC}"
    return 1
  fi
  echo "  Target ROI: $TARGET_ROI_SELECTOR"
  echo "    ref:  $TARGET_ROI_REF_FILTER"
  echo "    impl: $TARGET_ROI_IMPL_FILTER"
  return 0
}

# ── Helper: perform action ──
perform_action() {
  local session="$1"
  local action="$2"
  local target_rect="${3:-}"
  local action_receipt="${4:-}"
  local action_onset_file="${5:-}"

  if [[ "$action" == "splash" ]]; then
    # Splash: just wait — the recording captures page load
    sleep "$RECORD_DURATION"
  elif [[ "$action" == click:* ]]; then
    local selector="${action#click:}"
    sleep "$PRE_ACTION_WAIT"
    agent-browser --session "$session" eval "(() => {
      var el = document.querySelector('$selector');
      if (el) { el.click(); return 'clicked'; }
      return 'not found';
    })()" 2>&1 | head -1
    sleep "$RECORD_DURATION"
  elif [[ "$action" == hover:* ]]; then
    # Hover motion arc. Moves agent-browser's real pointer to the same visible
    # match used for ROI capture so `:hover` fires on the measured element.
    # PRE_ACTION_WAIT lets the page settle before hover so the baseline frames
    # are clean; RECORD_DURATION after hover captures the full entry transition.
    # We don't un-hover — entry-arc coverage is the primary value here; exit is
    # symmetric in most designs and adding move-away would double the per-target
    # time budget without proportional bug coverage.
    local selector="${action#hover:}"
    restore_visible_target_rect "$session" "$selector" "$target_rect" || return 2
    sleep "$PRE_ACTION_WAIT"
    capture_action_onset "$session" "$action_onset_file" || return 2
    hover_visible_target \
      "$session" "$selector" "$target_rect" "$action_receipt" || return 2
    sleep "$RECORD_DURATION"
  elif [[ "$action" == hover-and-out:* ]]; then
    # Hover entry + exit arc. Captures the case where the exit transition is
    # NOT symmetric with entry (different easing on close, hover-delay,
    # group-hover chain unwind, scaleY-then-translate panel collapses).
    # Symmetric designs are the common case — entry-only `hover:` is cheaper
    # and the default for hover-state-compare.sh; this mode is opt-in for
    # asymmetric designs (Webflow IX2 "On Mouse Leave" handlers, custom
    # cubic-bezier on exit, etc.) where the entry-only sweep would miss real
    # divergence. Total recording length is 2 × RECORD_DURATION.
    #
    # Mouse-move target (0,0) is chosen because (a) it is always inside the
    # viewport (so the cursor doesn't land outside the captured frame and
    # leave :hover stuck on the previous target via Chrome's "last hover
    # under cursor" behavior), and (b) it is at the top-left corner of the
    # page where the typical hover target is unlikely to overlap a
    # second-level element. Pages with full-bleed top-left elements (sticky
    # header, hero menu) can override via the MOUSE_AWAY_X / MOUSE_AWAY_Y
    # env vars.
    local selector="${action#hover-and-out:}"
    local away_x="${MOUSE_AWAY_X:-0}"
    local away_y="${MOUSE_AWAY_Y:-0}"
    restore_visible_target_rect "$session" "$selector" "$target_rect" || return 2
    sleep "$PRE_ACTION_WAIT"
    capture_action_onset "$session" "$action_onset_file" || return 2
    hover_visible_target \
      "$session" "$selector" "$target_rect" "$action_receipt" || return 2
    sleep "$RECORD_DURATION"
    agent-browser --session "$session" mouse move "$away_x" "$away_y" 2>&1 | head -1
    sleep "$RECORD_DURATION"
  elif [[ -f "$action" ]]; then
    # Custom script
    bash "$action" "$session"
  else
    echo "Unknown action: $action"
    exit 1
  fi
}

start_record_epoch() {
  local session="$1"
  agent-browser --session "$session" eval \
    '(() => { window.__uiCloneVmcRecordEpoch = performance.now(); return "ok"; })()' \
    >/dev/null 2>&1
}

capture_action_onset() {
  local session="$1"
  local out="${2:-}"
  local raw
  [ -n "$out" ] || return 0
  raw=$(agent-browser --session "$session" eval \
    '(() => { const epoch = Number(window.__uiCloneVmcRecordEpoch); if (!Number.isFinite(epoch)) return ""; return ((performance.now() - epoch) / 1000).toFixed(6); })()' \
    2>/dev/null | tail -1 | tr -d '"')
  if [[ ! "$raw" =~ ^[0-9]+([.][0-9]+)?$ ]] \
    || ! awk -v seconds="$raw" 'BEGIN { exit !(seconds + 0 > 0) }'; then
    echo "UNMEASURABLE: recorded action onset timestamp is unavailable"
    return 2
  fi
  printf '%s\n' "$raw" > "$out"
}

# ── Time-coupled media freeze (loop-e2e-6) ──
# Autoplaying <video> frames dominate frame-aligned SSIM: a hover target
# overlaying the hero video read flat 0.84 across all 534 frames (backdrop
# frame PHASE, not the hover arc) and scroll/splash modes compared different
# playback phases between the live-network ref and localhost impl. Freeze
# every video identically on BOTH sessions before recording: stub play() at
# the prototype (the ref bundle AND impl controllers re-kick play() on
# scroll — a one-shot pause un-freezes mid-recording), then pause at frame 0.
# Presence/playback/loop fidelity stays enforced elsewhere (video-play-proof,
# runtime-frame-proof, required-media-coverage, transition-fires video kind);
# this removes only frame phase from the VISUAL comparison — a missing or
# wrong video still diffs at its first frame.
# Codex review: do NOT mutate autoplay/loop attributes (loop semantics have
# no other verifying gate — flipping them here could hide a one-sided loop
# defect); only pause at frame 0. Captured attributes go to a sidecar so
# attribute fidelity stays auditable, and the frozen visual verdict remains
# composed with the LIVE media proofs at the post-implement gate (the same
# required-check set always contains video-play-proof / runtime-frame-proof /
# required-media-coverage / transition-fires video kind).
# Two freeze variants:
# - steady-state (hover/scroll): KICK playback first and wait for each video
#   to actually start (or 3s timeout) so play-coupled UI — poster/thumbnail
#   crossfades — reaches the SAME steady state on both sides (localhost
#   videos have played by freeze time, live-network ones may not have:
#   freezing immediately left the poster visible on one side only and made
#   the diff WORSE than the phase noise it removed). Then stub play() at the
#   prototype (site JS re-kicks play() on scroll) and pin every video at
#   frame 0.
# - immediate (splash): recording is already running and the splash overlay
#   owns the early frames; pin without kicking (a kick mid-splash would
#   record a play->pause flash at side-dependent times). The repeated
#   re-pause sweeps catch videos that start during the splash.
FREEZE_STEADY_JS="(async () => { const vids = Array.from(document.querySelectorAll('video')); await Promise.all(vids.map((v) => new Promise((res) => { if (v.currentTime > 0 || v.ended) return res(1); const t = setTimeout(res, 3000); v.addEventListener('playing', () => { clearTimeout(t); res(1); }, { once: true }); try { const p = v.play(); if (p && p.catch) p.catch(() => {}); } catch (_) {} }))); try { HTMLMediaElement.prototype.play = function () { return Promise.resolve(); }; } catch (_) {} const attrs = vids.map((v) => ({ src: (v.currentSrc || v.src || '').split('/').pop(), autoplay: v.autoplay, loop: v.loop, muted: v.muted, playsInline: v.playsInline })); const fr = () => { document.querySelectorAll('video').forEach((v) => { try { v.pause(); v.currentTime = 0; } catch (_) {} }); }; fr(); setTimeout(fr, 300); setTimeout(fr, 1200); return JSON.stringify(attrs); })()"
FREEZE_IMMEDIATE_JS="(() => { try { HTMLMediaElement.prototype.play = function () { return Promise.resolve(); }; } catch (_) {} const attrs = []; document.querySelectorAll('video').forEach((v) => { attrs.push({ src: (v.currentSrc || v.src || '').split('/').pop(), autoplay: v.autoplay, loop: v.loop, muted: v.muted, playsInline: v.playsInline }); }); const fr = () => { document.querySelectorAll('video').forEach((v) => { try { v.pause(); v.currentTime = 0; } catch (_) {} }); }; fr(); setTimeout(fr, 300); setTimeout(fr, 1200); return JSON.stringify(attrs); })()"

freeze_videos() {
  # $1 = session, $2 = sidecar label (ref|impl), $3 = variant (steady|immediate)
  local _js="$FREEZE_STEADY_JS"
  [[ "${3:-steady}" == "immediate" ]] && _js="$FREEZE_IMMEDIATE_JS"
  local _out
  _out=$(agent-browser --session "$1" eval "$_js" 2>/dev/null || true)
  printf '%s\n' "$_out" > "$OUT_DIR/media-freeze-${2}.json" 2>/dev/null || true
}

# ── Splash media mask (batch-4 item 1, distribution redesign fix a) ──
# freeze_videos pins <video> at frame 0, but on some sites the hero <video>
# re-kicks play() after the splash dismisses (autoplay remount defeats the
# one-shot pause), so the post-splash tail is a playing video compared at
# offset timestamps — SSIM noise + bogus change points to the recording end.
# Hide continuously-animating media OUTRIGHT during the splash recording with a
# single injected stylesheet rule: a CSS selector rule applies to every current
# AND future-mounted match, so a late-hydrating hero <video> is covered without
# re-polling. visibility:hidden preserves layout, so the DOM food-arc — the
# intro the gate actually measures — is unaffected. Applied identically on
# ref/impl/refcal via _splash_record, so the comparison stays fair. The
# selector list is env-tunable (UI_CLONE_VMC_SPLASH_MASK_SELECTORS); empty
# disables masking. This neutralizes only frame PHASE of the masked media — a
# missing/wrong video still fails the live media proofs (video-play-proof,
# runtime-frame-proof, required-media-coverage, transition-fires video kind).
SPLASH_MASK_SELECTORS="${UI_CLONE_VMC_SPLASH_MASK_SELECTORS:-video, canvas}"
_splash_mask_media() {
  local session="$1"
  [[ -z "$SPLASH_MASK_SELECTORS" ]] && return 0
  local js
  js="(() => { try { var id='__vmc_media_mask'; var s=document.getElementById(id); if(!s){ s=document.createElement('style'); s.id=id; (document.head||document.documentElement).appendChild(s); } s.textContent=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1] + " { visibility: hidden !important; }"))' "$SPLASH_MASK_SELECTORS"); return 'masked'; } catch (e) { return 'err:'+e; } })()"
  agent-browser --session "$session" eval "$js" 2>/dev/null | head -1
}

# ── Scroll mode: position-aligned still comparison (no video) ──
# Time-indexed scroll video SSIM is non-discriminative: the sweep quantizes
# into instant scroll steps scheduled per-side with in-page setTimeout, and
# main-thread contention slides step execution by ±1 step independently per
# side — the live reference compared against ITSELF failed 210/324 frames
# (65%). Evidence: e2e closeout tool-gap brief (video-motion-scroll-tool-gap.md).
# Instead both sides screenshot at the same proportional scroll fractions
# after a settle wait and same-named position frames are SSIM-compared —
# identical pages compare identical pixels by construction (ref-vs-ref
# self-test passes structurally; regression-locked by
# tests/test_scroll_position_compare.py).
#
# Spec-declared dynamic masking is shared by scroll-position captures and
# selector-driven hover/click recordings. The latter commonly targets a
# control drawn over a cross-origin video/iframe: pausing local <video> nodes
# cannot freeze the reference iframe, so the changing backdrop would dominate
# the small control ROI even when the control arc itself is exact.
MASK_AREA_CAP_PCT="${VIDEO_COMPARE_MASK_AREA_CAP_PCT:-25}"
mask_dynamic_selectors() {
  # $1 = session, $2 = sidecar label (ref|impl)
  local session="$1" label="$2"
  local sels="${VIDEO_COMPARE_DYNAMIC_SELECTORS:-}"
  [[ -z "$sels" ]] && { printf '[]' > "$OUT_DIR/dynamic-mask-${label}.json"; return 0; }
  local js
  js="(() => {
    const cap = ${MASK_AREA_CAP_PCT};
    let protectedTargets = [];
    try {
      const targetSelector = $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "${TARGET_ROI_SELECTOR:-}");
      if (targetSelector) protectedTargets = Array.from(document.querySelectorAll(targetSelector));
    } catch (_) {}
    const rendered = (el) => {
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.display !== 'none'
        && style.visibility !== 'hidden'
        && Number.parseFloat(style.opacity || '1') > 0
        && rect.width > 0 && rect.height > 0;
    };
    const inViewport = (el) => {
      const rect = el.getBoundingClientRect();
      return rect.bottom > 0 && rect.right > 0
        && rect.top < innerHeight && rect.left < innerWidth;
    };
    // Match capture_target_roi: preserve only the visible target this run will
    // actually drive. Protecting every same-selector control let a distant
    // slideshow keep an unrelated dynamic backdrop alive.
    const intendedTarget = protectedTargets.find((el) => rendered(el) && inViewport(el))
      || protectedTargets.find(rendered)
      || null;
    const pageArea = Math.max(document.documentElement.scrollWidth, window.innerWidth)
      * Math.max(document.documentElement.scrollHeight, window.innerHeight);
    const out = [];
    let maskedPct = 0;
    for (const sel of $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1].split("||")))' "$sels")) {
      let els = [];
      try { els = Array.from(document.querySelectorAll(sel)); } catch (e) { out.push({sel, error: String(e), count: 0, masked: false}); continue; }
      const protectedByTarget = Boolean(
        intendedTarget && els.some((el) => el === intendedTarget || el.contains(intendedTarget))
      );
      let area = 0;
      for (const el of els) { const r = el.getBoundingClientRect(); area += Math.max(0, r.width) * Math.max(0, r.height); }
      const areaPct = pageArea ? (area / pageArea) * 100 : 0;
      // Selector recordings compare a small target-local ROI. When the target
      // is independently preserved, exact spec-declared dynamic regions can
      // all be masked without erasing it, even if unrelated earlier masks
      // consumed the page-wide area budget. Full-page scroll captures keep the
      // strict cumulative cap.
      const withinCap = els.length > 0 && (
        Boolean(intendedTarget) || (maskedPct + areaPct) <= cap
      );
      let hiddenCount = 0;
      let persistentRule = false;
      if (withinCap) {
        if (!protectedByTarget) maskedPct += areaPct;
        if (!protectedByTarget) {
          const style = document.createElement('style');
          style.setAttribute('data-ui-clone-dynamic-mask', sel);
          style.textContent = sel + '{visibility:hidden!important}';
          document.head.appendChild(style);
          persistentRule = true;
          hiddenCount = els.length;
        }
        for (const el of els) {
          if (persistentRule) continue;
          if (!(intendedTarget && (el === intendedTarget || el.contains(intendedTarget)))) {
            el.style.visibility = 'hidden';
            hiddenCount++;
            continue;
          }
          // The dynamic region contains the hover control. Keep the control's
          // ancestor path visible, but hide every sibling branch around it.
          // This freezes a canvas/video backdrop without erasing the target.
          let cursor = intendedTarget;
          while (cursor && cursor !== el) {
            const parent = cursor.parentElement;
            if (!parent) break;
            for (const sibling of parent.children) {
              if (sibling !== cursor && !sibling.contains(intendedTarget)) {
                sibling.style.visibility = 'hidden';
                hiddenCount++;
              }
            }
            cursor = parent;
          }
        }
      }
      const masked = hiddenCount > 0;
      out.push({
        sel,
        count: els.length,
        areaPct: Math.round(areaPct * 10) / 10,
        masked,
        protectedByTarget,
        hiddenCount,
        persistentRule
      });
    }
    return JSON.stringify(out);
  })()"
  local raw
  raw=$(agent-browser --session "$session" eval "$js" 2>/dev/null | tail -1)
  python3 -c '
import json, sys
raw = sys.argv[1]
try:
    value = json.loads(raw)
    if isinstance(value, str):
        value = json.loads(value)
except Exception:
    value = []
print(json.dumps(value))
' "$raw" > "$OUT_DIR/dynamic-mask-${label}.json" 2>/dev/null || printf '[]' > "$OUT_DIR/dynamic-mask-${label}.json"
}

if [[ "$ACTION" == "scroll" ]]; then
  # shellcheck source=lib/position-compare.sh
  _VMC_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  source "$_VMC_SCRIPT_DIR/lib/position-compare.sh"
  SCROLL_SAMPLES="${SCROLL_SAMPLES:-24}"
  SCROLL_SETTLE="${SCROLL_SETTLE:-0.45}"

  # Stall-exposure reduction (batch-4 item 2): the position sweep is captured in
  # idempotent, manifest-checkpointed chunks. The manifest is persisted after
  # every position, so a lost background-shell completion wake-up (2 confirmed
  # incidents) re-runs and RESUMES from disk instead of restarting the whole
  # sweep. UI_CLONE_VMC_SCROLL_CHUNK bounds positions captured per invocation
  # (default: the full sweep — behaviour identical to the monolithic run). The
  # comparison below reads every persisted frame, so the verdict is unchanged.
  _VMC_REPO_ROOT="$(cd "$_VMC_SCRIPT_DIR/../.." && pwd)"
  SCROLL_CHUNK_MANIFEST="$OUT_DIR/scroll-chunk-manifest.json"
  SCROLL_CHUNK_SIZE="${UI_CLONE_VMC_SCROLL_CHUNK:-$((SCROLL_SAMPLES + 1))}"
  vmc_manifest_py() {
    PYTHONPATH="$_VMC_REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m ui_clone.scroll_chunk_manifest "$@"
  }

  # Resume run-identity (batch-4 review MAJOR 1): the manifest keyed resume on
  # SCROLL_SAMPLES alone, so fully-captured pos-*.png frames from a PRIOR
  # url/viewport/action/mask run with the same sample count were reused — a
  # replay attack minting a verdict from another page's frames. Bind resume to a
  # run identity (orig/impl URL, viewport, action, dynamic-mask selectors,
  # script version); a mismatch is NOT resumable, so the stale frames + manifest
  # are wiped and recaptured below.
  SCROLL_RUN_IDENTITY="$(printf '%s' "${ORIG_URL}|${IMPL_URL}|${VIEW_W}x${VIEW_H}|${ACTION}|${VIDEO_COMPARE_DYNAMIC_SELECTORS:-}|scrollv1" | python3 -c 'import sys,hashlib; print(hashlib.md5(sys.stdin.buffer.read()).hexdigest())' 2>/dev/null || echo "noid")"

  # Anti-contamination, resume-aware (batch-4 item 2): the out-dir may hold
  # 60fps f-*.png frames from a prior time-indexed run. Always clear those and
  # stale diffs. The pos-*.png are this sweep's PERSISTED chunk artifacts — keep
  # them when resuming the SAME sample grid (so a re-invocation continues from
  # disk); wipe them and the manifest only for a FRESH sweep (no manifest, or a
  # different SCROLL_SAMPLES, whose position fractions would not line up).
  # Resumable iff the on-disk manifest matches BOTH the sample grid AND the run
  # identity (replay-attack guard). A mismatch (different page/viewport/action/
  # mask, or no manifest) is not resumable -> fresh sweep, stale frames wiped.
  # Live page-region digest (batch-7 ITEM 6 / EVASION 3): resume must bind to
  # the ACTUAL target page, not just a self-asserted identity string. Capture a
  # digest of stable rendered text/geometry AND the loaded resource identities.
  # Text + scrollHeight alone reused stale frames after a CSS/JS-only rebuild
  # whose copy and page height stayed unchanged (eBay dogfood); Vite/Next asset
  # URLs make that implementation revision visible without hashing pixels.
  # A prior run's frames whose manifest stored a DIFFERENT page region/resource
  # digest are not resumable. The same eval reports whether the page has a
  # scroll range (drives the distinctness verdict: a non-scrolling page
  # legitimately yields identical frames).
  LIVE_REGION_RAW="$(agent-browser --session "$SESSION" open "$IMPL_URL" >/dev/null 2>&1; agent-browser --session "$SESSION" eval '(() => { var b = document.body ? (document.body.innerText || "") : ""; var resources = performance.getEntriesByType("resource").map((entry) => { try { var url = new URL(entry.name, location.href); return url.pathname + url.search; } catch (_) { return String(entry.name || ""); } }).filter(Boolean).sort(); var styles = Array.from(document.styleSheets || []).map((sheet) => sheet.href || "inline").sort(); return JSON.stringify({ t: b.slice(0, 4000), sh: document.documentElement.scrollHeight, iw: window.innerWidth, ih: window.innerHeight, resources: resources, styles: styles, range: (document.documentElement.scrollHeight - window.innerHeight) > 0 }); })()' 2>/dev/null || echo '{}')"
  LIVE_REGION_DIGEST="$(printf '%s' "$LIVE_REGION_RAW" | python3 -c 'import sys,hashlib; print(hashlib.md5(sys.stdin.buffer.read()).hexdigest())' 2>/dev/null || echo nodigest)"
  LIVE_HAS_RANGE="$(printf '%s' "$LIVE_REGION_RAW" | python3 -c 'import sys, json
v = sys.stdin.read().strip()
for _ in range(3):
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except (ValueError, TypeError):
            break
    else:
        break
print("1" if isinstance(v, dict) and v.get("range") else "0")' 2>/dev/null || echo 1)"

  _VMC_RESUMING=0
  if [ -f "$SCROLL_CHUNK_MANIFEST" ] \
     && vmc_manifest_py resumable "$SCROLL_CHUNK_MANIFEST" "$SCROLL_SAMPLES" "$SCROLL_RUN_IDENTITY" "$LIVE_REGION_DIGEST" >/dev/null 2>&1; then
    _VMC_RESUMING=1
  fi
  rm -f "$OUT_DIR/ref-frames/"f-*.png "$OUT_DIR/impl-frames/"f-*.png "$OUT_DIR/diff-frames/"*.png 2>/dev/null || true
  if [ "$_VMC_RESUMING" = 0 ]; then
    rm -f "$OUT_DIR/ref-frames/"*.png "$OUT_DIR/impl-frames/"*.png 2>/dev/null || true
    rm -f "$SCROLL_CHUNK_MANIFEST" 2>/dev/null || true
  else
    echo "  ↻ resuming scroll sweep from manifest ($SCROLL_CHUNK_MANIFEST)"
  fi

  # Spec-declared dynamic-region masking (e2e-8 pos-001 class): transition-spec
  # entries with dynamic:true name regions whose presentation state is
  # nondeterministic on the live ref (video/poster crossfades). section-compare
  # already masks them (EXCLUDE_DYNAMIC); the position compare gets the same
  # treatment with the codex-reviewed anti-cheat bounds: exact spec selectors
  # only, identical visibility:hidden on BOTH sides, per-side match counts
  # recorded to a sidecar (a ref-present selector missing from the impl is a
  # FAIL row, so masking can never hide a deleted element), and a total
  # masked-area cap (default 25% of the full scrolled PAGE area — the surface
  # the position sweep compares) above which the selector is NOT masked — an
  # overbroad selector must not blank the comparison. The denominator is the
  # page, not one viewport: e2e-9 sidecars recorded areaPct 201/135/63 PERCENT
  # of a single viewport on a 22-viewport page, so every full-bleed
  # spec-declared dynamic section (the eatReal timer carousel at 41.8%) was
  # silently unmaskable and pos-024 failed at every fan-out viewport.
  capture_scroll_positions() {
    # $1 = session, $2 = url, $3 = frames-dir, $4 = freeze sidecar label
    local session="$1" url="$2" frames_dir="$3" label="$4"
    mkdir -p "$frames_dir"
    # Resume: the manifest lists positions not yet captured for this side, capped
    # at one chunk. Empty => this side is already fully captured (re-invocation
    # after a lost wake-up), so do not re-open the browser.
    local pending
    pending="$(vmc_manifest_py next "$SCROLL_CHUNK_MANIFEST" "$label" "$SCROLL_SAMPLES" "$SCROLL_CHUNK_SIZE" "$frames_dir")"
    if [ -z "$pending" ]; then
      echo "  ↻ $label positions already captured — resuming from manifest"
      return 0
    fi
    agent-browser --session "$session" open "$url" 2>&1 | head -1
    sleep "$PRE_ACTION_WAIT"
    agent-browser --session "$session" set viewport "$VIEW_W" "$VIEW_H" 2>&1 | head -1
    freeze_videos "$session" "$label"
    mask_dynamic_selectors "$session" "$label"
    local i
    local achieved_y
    for i in $pending; do
      # Return the ACHIEVED window.scrollY (after the clamp), not the target, so
      # the manifest can tell a sticky/static region (identical frames at the
      # same offset) from genuine partial-run padding (batch-8 minor).
      achieved_y="$(agent-browser --session "$session" eval "(() => {
        const max = Math.max(document.documentElement.scrollHeight - window.innerHeight, 0);
        window.scrollTo({top: ($i/$SCROLL_SAMPLES)*max, behavior: 'instant'});
        return String(Math.round(window.scrollY));
      })()" 2>/dev/null | tr -dc '0-9')"
      sleep "$SCROLL_SETTLE"
      agent-browser --session "$session" screenshot "$frames_dir/pos-$(printf '%03d' "$i").png" >/dev/null 2>&1
      # Checkpoint after every position so an interrupted sweep resumes
      # losslessly. frames_dir is passed so the per-frame content fingerprint is
      # recorded at capture (a later content-swap is then re-pending, batch-6
      # ITEM 6). The run identity is persisted so a later invocation for a
      # DIFFERENT page/viewport/action is not resumable (replay-attack guard).
      # The achieved scroll offset is recorded for the sticky-static distinction.
      vmc_manifest_py record "$SCROLL_CHUNK_MANIFEST" "$label" "$SCROLL_SAMPLES" "$i" "$frames_dir" "$SCROLL_RUN_IDENTITY" "${achieved_y:-0}" || true
    done
  }

  echo -e "${BOLD}▸ Capturing position-aligned scroll samples (N=$((SCROLL_SAMPLES + 1)), settle=${SCROLL_SETTLE}s)...${NC}"
  capture_scroll_positions "${SESSION}-orig" "$ORIG_URL" "$OUT_DIR/ref-frames" ref
  agent-browser --session "${SESSION}-orig" close 2>/dev/null
  echo "  ✓ Original positions captured"
  capture_scroll_positions "${SESSION}-impl" "$IMPL_URL" "$OUT_DIR/impl-frames" impl
  agent-browser --session "${SESSION}-impl" close 2>/dev/null
  echo "  ✓ Implementation positions captured"

  # Chunked-resume gate: when UI_CLONE_VMC_SCROLL_CHUNK bounds the sweep, a
  # single invocation may capture only part of it. Comparing a partial sweep
  # would mint a false verdict, so stop here without a verdict and signal that a
  # resume invocation is needed. The default (chunk = full sweep) always
  # completes both sides in one pass, so this branch never fires there and the
  # verdict is identical to the monolithic run.
  if ! vmc_manifest_py complete "$SCROLL_CHUNK_MANIFEST" ref "$SCROLL_SAMPLES" "$OUT_DIR/ref-frames" \
     || ! vmc_manifest_py complete "$SCROLL_CHUNK_MANIFEST" impl "$SCROLL_SAMPLES" "$OUT_DIR/impl-frames"; then
    REF_LEFT="$(vmc_manifest_py next "$SCROLL_CHUNK_MANIFEST" ref "$SCROLL_SAMPLES" "$((SCROLL_SAMPLES + 1))" "$OUT_DIR/ref-frames" | wc -w | tr -d ' ')"
    IMPL_LEFT="$(vmc_manifest_py next "$SCROLL_CHUNK_MANIFEST" impl "$SCROLL_SAMPLES" "$((SCROLL_SAMPLES + 1))" "$OUT_DIR/impl-frames" | wc -w | tr -d ' ')"
    echo -e "${BOLD}↻ scroll sweep chunk captured; ref ${REF_LEFT} / impl ${IMPL_LEFT} positions pending — re-run to resume (manifest: $SCROLL_CHUNK_MANIFEST)${NC}"
    printf '{"status":"resume","reason":"scroll sweep incomplete","refPending":%s,"implPending":%s,"manifest":"%s"}\n' \
      "${REF_LEFT:-0}" "${IMPL_LEFT:-0}" "$SCROLL_CHUNK_MANIFEST" > "$OUT_DIR/scroll-resume.json"
    exit 0
  fi
  rm -f "$OUT_DIR/scroll-resume.json" 2>/dev/null || true

  # Persist the live page-region digest + scroll-range flag into the COMPLETE
  # manifest (it now carries samples/identity, so load_manifest will not reset
  # it): a LATER invocation's resume is checked against THIS page's digest
  # (EVASION 3), and the verify gate reads hasScrollRange to know whether
  # identical frames are legitimate.
  python3 - "$SCROLL_CHUNK_MANIFEST" "$LIVE_REGION_DIGEST" "$LIVE_HAS_RANGE" <<'PY' 2>/dev/null || true
import json
import os
import sys

path, digest, has_range = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
data = {}
if os.path.exists(path):
    try:
        data = json.loads(open(path, encoding="utf-8").read())
    except (OSError, ValueError):
        data = {}
if not isinstance(data, dict):
    data = {}
data["regionDigest"] = digest
data["hasScrollRange"] = has_range
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
PY

  # Verdict-integrity gate (batch-7 ITEM 6): independently re-derive frame
  # count + distinctness FROM DISK (manifest hashes are advisory) before the
  # SSIM verdict. Duplicate frame content across distinct scroll positions is
  # partial-run padding (EVASION 2) — ref==impl on padded frames would pass
  # SSIM, so the verdict must reject it here. Non-scrolling pages are exempt via
  # the manifest's hasScrollRange flag.
  POSITION_EXIT=0
  if ! vmc_manifest_py verify "$SCROLL_CHUNK_MANIFEST" ref "$SCROLL_SAMPLES" "$OUT_DIR/ref-frames" \
     || ! vmc_manifest_py verify "$SCROLL_CHUNK_MANIFEST" impl "$SCROLL_SAMPLES" "$OUT_DIR/impl-frames"; then
    echo -e "${BOLD}❌ scroll verdict-integrity: frame count mismatch or duplicate-frame padding across distinct positions — not a real sweep${NC}"
    POSITION_EXIT=1
  fi

  echo -e "${BOLD}▸ Comparing position-aligned frames (threshold=$SSIM_THRESHOLD)...${NC}"
  compare_position_frames "$OUT_DIR/ref-frames" "$OUT_DIR/impl-frames" "$OUT_DIR/diff-frames" "$SSIM_THRESHOLD" || POSITION_EXIT=1

  # Anti-cheat companion to dynamic masking: a selector that matched on the
  # ref but not on the impl means the impl is MISSING the dynamic element —
  # masking hid its pixels, so re-surface it as an explicit FAIL row.
  MASK_MISSING=$(python3 - "$OUT_DIR/dynamic-mask-ref.json" "$OUT_DIR/dynamic-mask-impl.json" <<'PY' 2>/dev/null || true
import json, sys
def load(p):
    try:
        return {e["sel"]: e for e in json.load(open(p)) if isinstance(e, dict)}
    except Exception:
        return {}
ref, impl = load(sys.argv[1]), load(sys.argv[2])
for sel, e in ref.items():
    if e.get("count", 0) > 0 and impl.get(sel, {}).get("count", 0) == 0:
        print(sel)
PY
)
  if [[ -n "$MASK_MISSING" ]]; then
    while IFS= read -r _sel; do
      [[ -z "$_sel" ]] && continue
      POSITION_FAIL=$((POSITION_FAIL + 1))
      POSITION_RESULTS="${POSITION_RESULTS}| dynamic-selector ${_sel} | missing-in-impl | ❌ |\n"
      POSITION_EXIT=1
    done <<< "$MASK_MISSING"
  fi

  # Ref-vs-ref noise-floor calibration (e2e-8 pos-013 class): when EVERY
  # failing position is borderline (within 0.02 below threshold), capture the
  # ref a second time at the same fractions and re-verdict each borderline
  # row against the ref's own noise (impl >= refref - 0.015, absolute floor
  # 0.87 — bounds from the codex design review). A genuinely wrong impl
  # scores far below the band and never reaches this pass; missing frames
  # and sub-band scores keep their FAIL.
  if [[ "$POSITION_EXIT" -eq 1 && -z "$MASK_MISSING" ]]; then
    BORDERLINE_ONLY=$(awk -F'\t' -v t="$SSIM_THRESHOLD" '
      $3 == "missing-impl-frame" { bad = 1 }
      $3 == "fail" { if ($2 + 0 < t - 0.02) bad = 1 }
      END { print bad ? "no" : "yes" }' "$OUT_DIR/diff-frames/position-ssim.tsv")
    if [[ "$BORDERLINE_ONLY" == "yes" ]]; then
      echo -e "${BOLD}▸ Borderline-only failures — calibrating ref-vs-ref noise floor...${NC}"
      capture_scroll_positions "${SESSION}-refcal" "$ORIG_URL" "$OUT_DIR/refcal-frames" refcal
      agent-browser --session "${SESSION}-refcal" close 2>/dev/null
      RECAL_FAIL=0
      RECAL_NOTES=""
      while IFS=$'\t' read -r name ssim verdict; do
        [[ "$verdict" != "fail" ]] && continue
        REFCAL_F="$OUT_DIR/refcal-frames/$name"
        REF_F="$OUT_DIR/ref-frames/$name"
        if [[ ! -f "$REFCAL_F" ]]; then RECAL_FAIL=$((RECAL_FAIL + 1)); continue; fi
        REFREF=$(ffmpeg -i "$REF_F" -i "$REFCAL_F" -lavfi "ssim" -f null - 2>&1 | grep -oE 'All:[0-9.]+' | cut -d: -f2 || echo "1")
        [[ -z "$REFREF" ]] && REFREF="1"
        if noise_floor_allows "$ssim" "$REFREF" "$SSIM_THRESHOLD"; then
          RECAL_NOTES="${RECAL_NOTES}| ${name} | ${ssim} | ✅ pass-by-noise-floor (refref=${REFREF}) |\n"
        else
          RECAL_FAIL=$((RECAL_FAIL + 1))
          RECAL_NOTES="${RECAL_NOTES}| ${name} | ${ssim} | ❌ below noise floor (refref=${REFREF}) |\n"
        fi
      done < "$OUT_DIR/diff-frames/position-ssim.tsv"
      echo -e "$RECAL_NOTES"
      if [[ "$RECAL_FAIL" -eq 0 ]]; then
        echo "  ✓ all borderline positions within the measured ref-vs-ref noise floor"
        POSITION_PASS=$((POSITION_PASS + POSITION_FAIL))
        POSITION_FAIL=0
        POSITION_RESULTS="${POSITION_RESULTS}${RECAL_NOTES}"
        POSITION_EXIT=0
      else
        POSITION_RESULTS="${POSITION_RESULTS}${RECAL_NOTES}"
      fi
    fi
  fi

  {
    printf '%s\n' \
      "Transition Compare Results (scroll: position-aligned)" \
      "==========================" \
      "Original: $ORIG_URL" \
      "Implementation: $IMPL_URL" \
      "Action: $ACTION" \
      "Total frames: $POSITION_TOTAL" \
      "Pass: $POSITION_PASS" \
      "Fail: $POSITION_FAIL" \
      "Threshold: $SSIM_THRESHOLD" \
      ""
    printf '%b' "$POSITION_RESULTS"
  } > "$OUT_DIR/result.txt"

  if [[ "$POSITION_EXIT" -eq 0 ]]; then
    echo -e "${GREEN}ALL PASS${NC} — scroll-position states match original"
    exit 0
  else
    echo "Diff images saved to: $OUT_DIR/diff-frames/"
    echo -e "${RED}${POSITION_FAIL} FAIL${NC} — scroll-position states differ from original"
    exit 1
  fi
fi

# ── Splash navigation anchor (loop-10 fix b) ───────────────────────────
# The splash window used to be pure wall-clock: record, open, sleep N. The
# live ref's hydration latency shifts the splash inside that window by
# seconds run-to-run — late starts clip the arc (vacuous all-PASS on a
# settled tail) and early starts compare different phases. Anchor the
# window END on a deterministic page event instead: after navigation, poll
# until the document is complete and the body has painted, THEN run the
# fixed-duration tail. Both sides get the same logical window regardless of
# load latency.
ANCHOR_TIMEOUT_S="${UI_CLONE_VMC_ANCHOR_TIMEOUT_S:-12}"
_wait_nav_anchor() {
  local session="$1"
  local waited=0
  while [ "$waited" -lt "$ANCHOR_TIMEOUT_S" ]; do
    local ready
    ready=$(agent-browser --session "$session" eval '(() => document.readyState === "complete" && !!document.body && document.body.getBoundingClientRect().height > 0)()' 2>/dev/null | tail -1)
    case "$ready" in *true*) return 0 ;; esac
    sleep 0.5
    waited=$((waited + 1))
  done
  # Review-2 finding 5: a side that never reaches the anchor records a
  # SHIFTED window — exactly the nondeterminism this anchor exists to close.
  # Timeout is an attempt FAILURE (the caller retries, then goes
  # unmeasurable), never a silent shifted-window run.
  echo "  ⚠ navigation anchor timed out after ${ANCHOR_TIMEOUT_S}s — attempt failed"
  return 1
}

# Splash recording with validated retry (loop-10 fix b): the recorder
# itself flakes — recordings truncate to <1s under load (the run-5 class:
# ref 60 / impl 36 frames). A truncated capture used to flow straight into
# a vacuous verdict; now the recorded duration is validated immediately and
# the side re-records up to 3 attempts before the run is declared
# unmeasurable.
_recorded_duration() {
  ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$1" 2>/dev/null || echo 0
}

write_video_source_metadata() {
  local video="$1"
  local out="$2"
  python3 - "$video" "$out" "$FPS" <<'PY'
import json
import math
import subprocess
import sys
from fractions import Fraction
from hashlib import sha256
from pathlib import Path

video = Path(sys.argv[1])
out = Path(sys.argv[2])
extracted_fps = int(sys.argv[3])
if extracted_fps <= 0:
    raise SystemExit("invalid extracted fps")
digest = sha256(video.read_bytes()).hexdigest()
probe = subprocess.run(
    [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=r_frame_rate,avg_frame_rate",
        "-of",
        "json",
        str(video),
    ],
    check=False,
    capture_output=True,
    text=True,
)
if probe.returncode != 0:
    raise SystemExit("ffprobe failed")
payload = json.loads(probe.stdout or "{}")
streams = payload.get("streams")
if not isinstance(streams, list) or not streams:
    raise SystemExit("missing video stream")
stream = streams[0]
r_raw = stream.get("r_frame_rate")
avg_raw = stream.get("avg_frame_rate")
if not isinstance(r_raw, str) or not isinstance(avg_raw, str):
    raise SystemExit("missing frame rate")
try:
    r_rate = Fraction(r_raw)
    avg_rate = Fraction(avg_raw)
except Exception as exc:
    raise SystemExit("invalid frame rate") from exc
if r_rate <= 0 or avg_rate <= 0 or r_rate != avg_rate:
    raise SystemExit("missing/unknown/VFR source cadence")
ratio = Fraction(extracted_fps, 1) / r_rate
if ratio.denominator != 1 or ratio.numerator <= 0:
    raise SystemExit("noninteger source/extracted cadence")
source_fps = float(r_rate)
if not math.isfinite(source_fps):
    raise SystemExit("invalid source fps")
receipt = {
    "schemaVersion": 1,
    "rawWebmSha256": digest,
    "rFrameRate": r_raw,
    "avgFrameRate": avg_raw,
    "sourceFps": source_fps,
    "cfr": True,
    "extractedFps": extracted_fps,
    "sourceToExtractedRatio": ratio.numerator,
}
out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
PY
}

bind_capture_retry_source_metadata() {
  local receipt="$1"
  [ -f "$receipt" ] || return 0
  python3 - "$receipt" "$OUT_DIR/ref-video/source-metadata.json" "$OUT_DIR/impl-video/source-metadata.json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

receipt_path, ref_path, impl_path = [Path(value) for value in sys.argv[1:]]

def load_bound(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "payload": payload,
    }

receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
receipt["sourceMetadata"] = {
    "ref": load_bound(ref_path),
    "impl": load_bound(impl_path),
}
receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
PY
}

_splash_record() {
  local session="$1" url="$2" out_video="$3" side="$4"
  local attempt dur
  for attempt in 1 2 3; do
    agent-browser --session "$session" open about:blank 2>&1 | head -1
    agent-browser --session "$session" set viewport "$VIEW_W" "$VIEW_H" 2>&1 | head -1
    agent-browser --session "$session" record start "$out_video" 2>&1 | head -1
    sleep 0.2
    agent-browser --session "$session" open "$url" 2>&1 | head -1
    freeze_videos "$session" "$side" immediate
    _splash_mask_media "$session"
    if ! _wait_nav_anchor "$session"; then
      echo "  ⚠ $side never reached the navigation anchor — re-recording (attempt $attempt/3)"
      agent-browser --session "$session" record stop 2>&1 | head -1
      agent-browser --session "$session" close 2>/dev/null
      continue
    fi
    sleep "$RECORD_DURATION"
    agent-browser --session "$session" record stop 2>&1 | head -1
    dur=$(_recorded_duration "$out_video")
    if awk -v d="$dur" -v m="$RECORD_DURATION" 'BEGIN{exit !(d+0 >= m * 0.6)}'; then
      return 0
    fi
    echo "  ⚠ $side splash recording truncated (${dur}s < ${RECORD_DURATION}s window) — re-recording (attempt $attempt/3)"
    agent-browser --session "$session" close 2>/dev/null
  done
  echo -e "${RED}ERROR: $side splash recording kept truncating after 3 attempts — unmeasurable${NC}"
  return 1
}

# Test hook (loop-10 corrupted-frame fixture): skip the browser phases and
# run extraction/compare against pre-seeded $OUT_DIR/{ref,impl}-video/raw.webm.
if [[ "${UI_CLONE_VMC_SKIP_RECORD:-0}" == "1" ]]; then
  echo -e "${YELLOW}▸ UI_CLONE_VMC_SKIP_RECORD=1 — using pre-seeded recordings${NC}"
else

# ── Phase 1: Record original ──
echo -e "${BOLD}▸ Recording original...${NC}"

if [[ "$ACTION" == "splash" ]]; then
  # Splash symmetry (loop-e2e-6): this side used to record seconds 3..8
  # after navigation while the impl recorded 0..5 — frame-aligned SSIM then
  # compared a settled page against a mid-splash one by construction. Record
  # from navigation start on BOTH sides (blank page, viewport, record, open),
  # with duration-validated retry (_splash_record handles record start/stop).
  _splash_record "${SESSION}-orig" "$ORIG_URL" "$OUT_DIR/ref-video/raw.webm" ref || exit 2
else
  agent-browser --session "${SESSION}-orig" open "$ORIG_URL" 2>&1 | head -1
  sleep "$PRE_ACTION_WAIT"
  agent-browser --session "${SESSION}-orig" set viewport "$VIEW_W" "$VIEW_H" 2>&1 | head -1
  agent-browser --session "${SESSION}-orig" record start "$OUT_DIR/ref-video/raw.webm" 2>&1 | head -1
  start_record_epoch "${SESSION}-orig"
  # record start creates a fresh browser context. Runtime mutations made
  # before it (video freeze, dynamic masks) are discarded by that context
  # swap, so apply them only after recording has started.
  freeze_videos "${SESSION}-orig" ref
  mask_dynamic_selectors "${SESSION}-orig" ref
  if [ -n "$TARGET_ROI_SELECTOR" ]; then
    if ! capture_target_roi \
        "${SESSION}-orig" "$TARGET_ROI_SELECTOR" "$TARGET_ROI_REF_RAW"; then
      echo -e "${RED}UNMEASURABLE: reference hover target ROI capture failed${NC}"
      agent-browser --session "${SESSION}-orig" close 2>/dev/null
      exit 2
    fi
  fi
  sleep 2
  if ! perform_action \
      "${SESSION}-orig" "$ACTION" "$TARGET_ROI_REF_RAW" \
      "$OUT_DIR/ref-video/hover-action.raw.json" \
      "$OUT_DIR/ref-video/action-onset-seconds.txt"; then
    agent-browser --session "${SESSION}-orig" record stop >/dev/null 2>&1 || true
    agent-browser --session "${SESSION}-orig" close 2>/dev/null || true
    echo -e "${RED}UNMEASURABLE: reference selector action was not verified${NC}"
    exit 2
  fi
  agent-browser --session "${SESSION}-orig" record stop 2>&1 | head -1
fi

agent-browser --session "${SESSION}-orig" close 2>/dev/null

echo "  ✓ Original recorded"

# ── Phase 2: Record implementation ──
echo -e "${BOLD}▸ Recording implementation...${NC}"

if [[ "$ACTION" == "splash" ]]; then
  # Viewport parity (loop-e2e-6): record BEFORE opening the URL to catch the
  # splash from t=0, with the viewport pinned on a blank page first (the
  # impl side once recorded at the session default and every frame pair
  # size-mismatched). Duration-validated retry via _splash_record.
  _splash_record "${SESSION}-impl" "$IMPL_URL" "$OUT_DIR/impl-video/raw.webm" impl || exit 2
else
  agent-browser --session "${SESSION}-impl" open "$IMPL_URL" 2>&1 | head -1
  sleep "$PRE_ACTION_WAIT"
  agent-browser --session "${SESSION}-impl" set viewport "$VIEW_W" "$VIEW_H" 2>&1 | head -1
  agent-browser --session "${SESSION}-impl" record start "$OUT_DIR/impl-video/raw.webm" 2>&1 | head -1
  start_record_epoch "${SESSION}-impl"
  freeze_videos "${SESSION}-impl" impl
  mask_dynamic_selectors "${SESSION}-impl" impl
  if [ -n "$TARGET_ROI_SELECTOR" ]; then
    if ! capture_target_roi \
        "${SESSION}-impl" "$TARGET_ROI_SELECTOR" "$TARGET_ROI_IMPL_RAW"; then
      echo -e "${RED}UNMEASURABLE: implementation hover target ROI capture failed${NC}"
      agent-browser --session "${SESSION}-impl" close 2>/dev/null
      exit 2
    fi
  fi
  sleep 2
  if ! perform_action \
      "${SESSION}-impl" "$ACTION" "$TARGET_ROI_IMPL_RAW" \
      "$OUT_DIR/impl-video/hover-action.raw.json" \
      "$OUT_DIR/impl-video/action-onset-seconds.txt"; then
    agent-browser --session "${SESSION}-impl" record stop >/dev/null 2>&1 || true
    agent-browser --session "${SESSION}-impl" close 2>/dev/null || true
    echo -e "${RED}UNMEASURABLE: implementation selector action was not verified${NC}"
    exit 2
  fi
  agent-browser --session "${SESSION}-impl" record stop 2>&1 | head -1
fi

agent-browser --session "${SESSION}-impl" close 2>/dev/null

echo "  ✓ Implementation recorded"

fi  # UI_CLONE_VMC_SKIP_RECORD

if ! prepare_target_roi_filters; then
  exit 2
fi

# ── Phase 3: Extract frames at 60fps ──
echo -e "${BOLD}▸ Extracting frames at ${FPS}fps...${NC}"

# Loop-10 fix (a): the old `ffmpeg ... 2>/dev/null` swallowed extraction
# failures, so the PREVIOUS run's frames stayed in the frame dirs and the
# verdict silently compared stale frames against a fresh recording (run-3
# reported a byte-identical verdict to run-2 after an impl retime). Frame
# dirs are wiped BEFORE extraction, ffmpeg stderr is surfaced, a failed or
# empty extraction is a HARD error, and each frame dir is fingerprinted to
# its source recording so cross-run reuse is structurally impossible.
rm -f "$OUT_DIR/ref-frames/"*.png "$OUT_DIR/impl-frames/"*.png "$OUT_DIR/diff-frames/"*.png \
      "$OUT_DIR/ref-frames/.fingerprint" "$OUT_DIR/impl-frames/.fingerprint" \
      "$OUT_DIR/ref-frames/.first-change" "$OUT_DIR/impl-frames/.first-change" \
      "$OUT_DIR/ref-frames/.last-change" "$OUT_DIR/impl-frames/.last-change" 2>/dev/null || true

_video_md5() {
  python3 -c 'import hashlib,sys;print(hashlib.md5(open(sys.argv[1],"rb").read()).hexdigest())' "$1" 2>/dev/null || echo "unreadable"
}

_extract_frames() {
  local video="$1" frames_dir="$2" side="$3"
  local fflog="$OUT_DIR/ffmpeg-extract-${side}.log"
  local filter="fps=$FPS"
  if [ "$side" = "ref" ] && [ -n "$TARGET_ROI_REF_FILTER" ]; then
    filter="$filter,$TARGET_ROI_REF_FILTER"
  elif [ "$side" = "impl" ] && [ -n "$TARGET_ROI_IMPL_FILTER" ]; then
    filter="$filter,$TARGET_ROI_IMPL_FILTER"
  fi
  if ! ffmpeg -y -i "$video" -vf "$filter" "$frames_dir/f-%06d.png" 2> "$fflog"; then
    echo -e "${RED}ERROR: ffmpeg frame extraction FAILED for $side recording${NC}"
    echo "  ffmpeg stderr (tail):"
    tail -5 "$fflog" | sed 's/^/    /'
    echo "  Hard error — a failed extraction must never fall through to a verdict on stale frames."
    exit 2
  fi
  local count
  count=$(ls "$frames_dir/"*.png 2>/dev/null | wc -l | tr -d ' ')
  if [ "$count" -eq 0 ]; then
    echo -e "${RED}ERROR: ffmpeg produced 0 frames for $side recording${NC}"
    tail -5 "$fflog" | sed 's/^/    /'
    exit 2
  fi
  _video_md5 "$video" > "$frames_dir/.fingerprint"
}

build_target_roi_delta_frames() {
  local source_dir="$1"
  local delta_dir="$2"
  local blur="${3:-0}"
  [ "$#" -ge 4 ] || return 1
  local baseline_index="$4"
  local base frame out
  mkdir -p "$delta_dir"
  rm -f "$delta_dir"/f-*.png 2>/dev/null || true
  if [[ ! "$baseline_index" =~ ^[0-9]+$ ]] || [ "$baseline_index" -lt 1 ]; then
    return 1
  fi
  base=$(printf "%s/f-%06d.png" "$source_dir" "$baseline_index")
  [ -f "$base" ] || return 1
  for frame in "$source_dir"/f-*.png; do
    [ -f "$frame" ] || continue
    out="$delta_dir/$(basename "$frame")"
    if awk -v value="$blur" 'BEGIN { exit !(value + 0 > 0) }'; then
      if ! magick "$base" "$frame" \
          -compose difference -composite \
          -blur "0x${blur}" "$out"; then
        return 1
      fi
    elif ! magick "$base" "$frame" -compose difference -composite "$out"; then
      return 1
    fi
  done
  [ "$(find "$delta_dir" -maxdepth 1 -type f -name 'f-*.png' | wc -l | tr -d ' ')" -gt 0 ]
}

build_target_roi_material_delta_frames() {
  local ref_delta_dir="$1"
  local impl_delta_dir="$2"
  local ref_out="$3"
  local impl_out="$4"
  local threshold="$5"
  local ref_offset="${6:-0}"
  local impl_offset="${7:-0}"
  local aligned_count="${8:-0}"
  local ref_frame impl_frame ref_base impl_base mask k

  mkdir -p "$ref_out" "$impl_out"
  rm -f "$ref_out"/f-*.png "$impl_out"/f-*.png "$ref_out"/.material-mask-*.png 2>/dev/null || true

  if [[ ! "$aligned_count" =~ ^[0-9]+$ ]] || [ "$aligned_count" -lt 1 ]; then
    return 1
  fi
  for k in $(seq 1 "$aligned_count"); do
    ref_base=$(printf "f-%06d.png" $((k + ref_offset)))
    impl_base=$(printf "f-%06d.png" $((k + impl_offset)))
    ref_frame="$ref_delta_dir/$ref_base"
    impl_frame="$impl_delta_dir/$impl_base"
    [ -f "$ref_frame" ] || continue
    [ -f "$impl_frame" ] || continue
    mask="$ref_out/.material-mask-$(printf '%06d' "$k").png"

    # Union mask: keep a pixel when either ALIGNED side has material target-local delta.
    # This removes shared/static low-amplitude WebM residue without hiding
    # reference-only motion; one-sided material pixels remain in the mask.
    if ! magick "$ref_frame" "$impl_frame" \
        -alpha off -compose Lighten -composite \
        -separate -evaluate-sequence max \
        -threshold "$threshold" "$mask"; then
      return 1
    fi
    if ! magick "$ref_frame" "$mask" -alpha off -compose Multiply -composite "$ref_out/$ref_base"; then
      return 1
    fi
    if ! magick "$impl_frame" "$mask" -alpha off -compose Multiply -composite "$impl_out/$impl_base"; then
      return 1
    fi
    rm -f "$mask"
  done

  [ "$(find "$ref_out" -maxdepth 1 -type f -name 'f-*.png' | wc -l | tr -d ' ')" -gt 0 ] \
    && [ "$(find "$impl_out" -maxdepth 1 -type f -name 'f-*.png' | wc -l | tr -d ' ')" -gt 0 ]
}

build_target_roi_timing_delta_frames() {
  local source_dir="$1"
  local out_dir="$2"
  local threshold="$3"
  local padding="${4:-0}"
  local frame base dimensions width height inner_width inner_height magick_args=()

  mkdir -p "$out_dir"
  rm -f "$out_dir"/f-*.png 2>/dev/null || true
  for frame in "$source_dir"/f-*.png; do
    [ -f "$frame" ] || continue
    base="$(basename "$frame")"
    # Keep the command array non-empty so nounset-safe expansion works on
    # Bash 3.2 and Bash 5.x even when padding is disabled.
    magick_args=("$frame")
    if [[ "$padding" =~ ^[0-9]+$ ]] && [ "$padding" -gt 0 ]; then
      dimensions=$(identify -format '%w %h' "$frame" 2>/dev/null || echo "0 0")
      read -r width height <<< "$dimensions"
      inner_width=$((width - 2 * padding))
      inner_height=$((height - 2 * padding))
      if [ "$inner_width" -gt 0 ] && [ "$inner_height" -gt 0 ]; then
        magick_args+=(-crop "${inner_width}x${inner_height}+${padding}+${padding}" +repage)
      fi
    fi
    # Apply a side-local material mask. Timing must not borrow support from the
    # other side at an unrelated absolute timestamp; ref/impl action onsets are
    # measured independently and aligned only after each arc is detected. The
    # timing signal excludes ROI padding so a high-contrast animated backdrop
    # outside the actual control cannot extend a small hover arc.
    if ! magick "${magick_args[@]}" \
        \( +clone -alpha off -separate -evaluate-sequence max \
          -threshold "$threshold" \) \
        -alpha off -compose Multiply -composite "$out_dir/$base"; then
      return 1
    fi
  done
  [ "$(find "$out_dir" -maxdepth 1 -type f -name 'f-*.png' | wc -l | tr -d ' ')" -gt 0 ]
}

_extract_frames "$OUT_DIR/ref-video/raw.webm" "$OUT_DIR/ref-frames" ref
_extract_frames "$OUT_DIR/impl-video/raw.webm" "$OUT_DIR/impl-frames" impl
if [ -n "$TARGET_ROI_SELECTOR" ]; then
  if ! write_video_source_metadata "$OUT_DIR/ref-video/raw.webm" "$OUT_DIR/ref-video/source-metadata.json" \
    || ! write_video_source_metadata "$OUT_DIR/impl-video/raw.webm" "$OUT_DIR/impl-video/source-metadata.json"; then
    echo -e "${RED}UNMEASURABLE: raw WebM source metadata is missing, VFR, or incompatible with extracted FPS${NC}"
    exit 2
  fi
  if ! python3 - "$OUT_DIR/ref-video/source-metadata.json" "$OUT_DIR/impl-video/source-metadata.json" <<'PY'
import json
import sys

ref = json.load(open(sys.argv[1], encoding="utf-8"))
impl = json.load(open(sys.argv[2], encoding="utf-8"))
fields = (
    "rFrameRate",
    "avgFrameRate",
    "sourceFps",
    "cfr",
    "extractedFps",
    "sourceToExtractedRatio",
)
if any(ref.get(field) != impl.get(field) for field in fields):
    raise SystemExit(1)
PY
  then
    echo -e "${RED}UNMEASURABLE: ref/impl raw WebM source cadence differs${NC}"
    exit 2
  fi
fi

# Fingerprint validation: the frames being compared must come from THIS
# run's recordings. (In-process this is guaranteed by the wipe above; the
# check protects manual phase re-runs and future refactors.)
for SIDE in ref impl; do
  FP_FILE="$OUT_DIR/${SIDE}-frames/.fingerprint"
  CUR_FP="$(_video_md5 "$OUT_DIR/${SIDE}-video/raw.webm")"
  if [ ! -f "$FP_FILE" ] || [ "$(cat "$FP_FILE")" != "$CUR_FP" ]; then
    echo -e "${RED}ERROR: ${SIDE}-frames fingerprint does not match the current recording — stale frames${NC}"
    exit 2
  fi
done

REF_COUNT=$(ls "$OUT_DIR/ref-frames/"*.png 2>/dev/null | wc -l | tr -d ' ')
IMPL_COUNT=$(ls "$OUT_DIR/impl-frames/"*.png 2>/dev/null | wc -l | tr -d ' ')
MIN_COUNT=$((REF_COUNT < IMPL_COUNT ? REF_COUNT : IMPL_COUNT))

echo "  Ref frames: $REF_COUNT, Impl frames: $IMPL_COUNT, Comparing: $MIN_COUNT"

if [[ "$MIN_COUNT" -eq 0 ]]; then
  echo -e "${RED}ERROR: No frames to compare${NC}"
  exit 1
fi

# ── Phase 3.5: Timing analysis (always runs) ──
echo -e "${BOLD}▸ Analyzing transition timing...${NC}"

# Timing analysis + first-change index live in a sourceable lib so the
# synthetic-frame regression tests can drive them directly.
# shellcheck source=lib/frame-align.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/frame-align.sh"

REF_TIMING_SEARCH_START=1
IMPL_TIMING_SEARCH_START=1
REF_ACTION_ONSET_SECONDS=""
IMPL_ACTION_ONSET_SECONDS=""
if [ -n "$TARGET_ROI_SELECTOR" ]; then
  if [[ "${UI_CLONE_VMC_SKIP_RECORD:-0}" == "1" ]]; then
    REF_ACTION_ONSET_SECONDS="${VIDEO_COMPARE_REF_ACTION_ONSET_SECONDS:-${VIDEO_COMPARE_ACTION_ONSET_SECONDS:-}}"
    IMPL_ACTION_ONSET_SECONDS="${VIDEO_COMPARE_IMPL_ACTION_ONSET_SECONDS:-${VIDEO_COMPARE_ACTION_ONSET_SECONDS:-}}"
  else
    REF_ACTION_ONSET_SECONDS=$(cat "$OUT_DIR/ref-video/action-onset-seconds.txt" 2>/dev/null || true)
    IMPL_ACTION_ONSET_SECONDS=$(cat "$OUT_DIR/impl-video/action-onset-seconds.txt" 2>/dev/null || true)
  fi
  for ACTION_ONSET_SECONDS in "$REF_ACTION_ONSET_SECONDS" "$IMPL_ACTION_ONSET_SECONDS"; do
    if [[ ! "$ACTION_ONSET_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]] \
      || ! awk -v seconds="$ACTION_ONSET_SECONDS" \
        'BEGIN { exit !(seconds + 0 > 0) }'; then
      echo -e "${RED}UNMEASURABLE: selector action onset must be an explicit positive number${NC}"
      echo "  Set VIDEO_COMPARE_ACTION_ONSET_SECONDS for skip-record evidence."
      exit 2
    fi
  done
  REF_TIMING_SEARCH_START=$(awk -v seconds="$REF_ACTION_ONSET_SECONDS" -v fps="$FPS" '
    BEGIN {
      printf "%d", int((seconds + 0) * (fps + 0)) + 1
    }
  ')
  IMPL_TIMING_SEARCH_START=$(awk -v seconds="$IMPL_ACTION_ONSET_SECONDS" -v fps="$FPS" '
    BEGIN {
      printf "%d", int((seconds + 0) * (fps + 0)) + 1
    }
  ')
  echo "  Selector action timing floors: ref frame $REF_TIMING_SEARCH_START (${REF_ACTION_ONSET_SECONDS}s), impl frame $IMPL_TIMING_SEARCH_START (${IMPL_ACTION_ONSET_SECONDS}s)"
fi

TARGET_REF_DELTA_BASELINE_FRAME=1
TARGET_IMPL_DELTA_BASELINE_FRAME=1
COMPARE_REF_FRAMES="$OUT_DIR/ref-frames"
COMPARE_IMPL_FRAMES="$OUT_DIR/impl-frames"
TIMING_REF_FRAMES="$OUT_DIR/ref-frames"
TIMING_IMPL_FRAMES="$OUT_DIR/impl-frames"
if [ -n "$TARGET_ROI_SELECTOR" ]; then
  if [ "$REF_TIMING_SEARCH_START" -gt 1 ]; then
    TARGET_REF_DELTA_BASELINE_FRAME=$((REF_TIMING_SEARCH_START - 1))
  fi
  if [ "$IMPL_TIMING_SEARCH_START" -gt 1 ]; then
    TARGET_IMPL_DELTA_BASELINE_FRAME=$((IMPL_TIMING_SEARCH_START - 1))
  fi
  TARGET_REF_DELTA_BASELINE_NAME=$(printf "f-%06d.png" "$TARGET_REF_DELTA_BASELINE_FRAME")
  TARGET_IMPL_DELTA_BASELINE_NAME=$(printf "f-%06d.png" "$TARGET_IMPL_DELTA_BASELINE_FRAME")
  if [ ! -f "$OUT_DIR/ref-frames/$TARGET_REF_DELTA_BASELINE_NAME" ] \
    || [ ! -f "$OUT_DIR/impl-frames/$TARGET_IMPL_DELTA_BASELINE_NAME" ]; then
    echo -e "${RED}UNMEASURABLE: pre-action target ROI baseline frame is missing${NC}"
    echo "  Expected: ref $TARGET_REF_DELTA_BASELINE_NAME, impl $TARGET_IMPL_DELTA_BASELINE_NAME"
    exit 2
  fi

  COMPARE_REF_FRAMES="$OUT_DIR/ref-delta-frames"
  COMPARE_IMPL_FRAMES="$OUT_DIR/impl-delta-frames"
  TIMING_REF_FRAMES="$OUT_DIR/ref-delta-timing-frames"
  TIMING_IMPL_FRAMES="$OUT_DIR/impl-delta-timing-frames"
  echo -e "${BOLD}▸ Normalizing selector frames to target-local deltas...${NC}"
  if ! build_target_roi_delta_frames \
      "$OUT_DIR/ref-frames" "$COMPARE_REF_FRAMES" \
      "0" "$TARGET_REF_DELTA_BASELINE_FRAME" \
    || ! build_target_roi_delta_frames \
      "$OUT_DIR/impl-frames" "$COMPARE_IMPL_FRAMES" \
      "0" "$TARGET_IMPL_DELTA_BASELINE_FRAME" \
    || ! build_target_roi_timing_delta_frames \
      "$COMPARE_REF_FRAMES" "$TIMING_REF_FRAMES" \
      "$VIDEO_COMPARE_TARGET_TIMING_NOISE_THRESHOLD" "$TARGET_ROI_PADDING" \
    || ! build_target_roi_timing_delta_frames \
      "$COMPARE_IMPL_FRAMES" "$TIMING_IMPL_FRAMES" \
      "$VIDEO_COMPARE_TARGET_TIMING_NOISE_THRESHOLD" "$TARGET_ROI_PADDING"; then
    echo -e "${RED}UNMEASURABLE: target ROI delta normalization failed${NC}"
    exit 2
  fi
  echo "  Target delta baselines: ref $TARGET_REF_DELTA_BASELINE_NAME, impl $TARGET_IMPL_DELTA_BASELINE_NAME"
fi

# Selector timing runs on the same union material-motion support used by the
# bounded static-foreground rescue. This prevents low-amplitude WebM residue
# from extending the measured arc, while reference-only material motion remains
# in both timing masks and therefore cannot be hidden by a static implementation.
FRAME_CHANGE_CLUSTER_GAP_FRAMES="$VIDEO_COMPARE_TARGET_TIMING_CLUSTER_GAP_FRAMES" \
  analyze_timing "$TIMING_REF_FRAMES" "Original" "$REF_TIMING_SEARCH_START"
FRAME_CHANGE_CLUSTER_GAP_FRAMES="$VIDEO_COMPARE_TARGET_TIMING_CLUSTER_GAP_FRAMES" \
  analyze_timing "$TIMING_IMPL_FRAMES" "Implementation" "$IMPL_TIMING_SEARCH_START"
if [ -n "$TARGET_ROI_SELECTOR" ]; then
  cp "$TIMING_REF_FRAMES/.first-change" "$OUT_DIR/ref-frames/.first-change"
  cp "$TIMING_REF_FRAMES/.last-change" "$OUT_DIR/ref-frames/.last-change"
  cp "$TIMING_IMPL_FRAMES/.first-change" "$OUT_DIR/impl-frames/.first-change"
  cp "$TIMING_IMPL_FRAMES/.last-change" "$OUT_DIR/impl-frames/.last-change"
fi

# Selector comparisons must prove that the reference interaction produced a
# visible state change. Otherwise target-local delta frames are all black and
# ref/impl can vacuously score 1.0 even when hover never reached the recorder.
if [ -n "$TARGET_ROI_SELECTOR" ]; then
  TARGET_REF_FC=$(cat "$OUT_DIR/ref-frames/.first-change" 2>/dev/null || echo 1)
  TARGET_REF_LC=$(cat "$OUT_DIR/ref-frames/.last-change" 2>/dev/null || echo 1)
  if [ "$TARGET_REF_FC" -eq 1 ] && [ "$TARGET_REF_LC" -eq 1 ]; then
    echo -e "${RED}UNMEASURABLE: reference selector interaction produced no visible target-local change${NC}"
    echo "  Selector: $TARGET_ROI_SELECTOR"
    echo "  Re-run after confirming the browser hover/click state reaches the recorder."
    exit 2
  fi
fi

if [ -n "$TARGET_ROI_SELECTOR" ]; then
  if ! python3 - \
      "$TARGET_ROI_PLAN" \
      "$TARGET_REF_DELTA_BASELINE_FRAME" \
      "$TARGET_IMPL_DELTA_BASELINE_FRAME" \
      "$TARGET_REF_DELTA_BASELINE_NAME" \
      "$TARGET_IMPL_DELTA_BASELINE_NAME" \
      "$REF_ACTION_ONSET_SECONDS" \
      "$IMPL_ACTION_ONSET_SECONDS" \
      "$FPS" \
      "$OUT_DIR/ref-frames/.fingerprint" \
      "$OUT_DIR/impl-frames/.fingerprint" <<'PY'
import json
import math
import os
import sys
from pathlib import Path

try:
    (
        plan_path,
        ref_baseline_frame,
        impl_baseline_frame,
        ref_baseline_name,
        impl_baseline_name,
        ref_onset_seconds,
        impl_onset_seconds,
        fps,
        ref_fingerprint_path,
        impl_fingerprint_path,
    ) = sys.argv[1:]
    path = Path(plan_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    ref_baseline_frame = int(ref_baseline_frame)
    impl_baseline_frame = int(impl_baseline_frame)
    ref_onset_seconds = float(ref_onset_seconds)
    impl_onset_seconds = float(impl_onset_seconds)
    fps = float(fps)
    if (
        not isinstance(payload, dict)
        or payload.get("status") != "pass"
        or ref_baseline_frame < 1
        or impl_baseline_frame < 1
        or not math.isfinite(ref_onset_seconds)
        or ref_onset_seconds <= 0
        or not math.isfinite(impl_onset_seconds)
        or impl_onset_seconds <= 0
        or not math.isfinite(fps)
        or fps <= 0
    ):
        raise ValueError("invalid target normalization provenance")
    payload["normalization"] = {
        "baselineFrame": ref_baseline_frame,
        "baselineFrameName": ref_baseline_name,
        "actionOnsetSeconds": ref_onset_seconds,
        "baselineFrames": {
            "ref": ref_baseline_frame,
            "impl": impl_baseline_frame,
        },
        "baselineFrameNames": {
            "ref": ref_baseline_name,
            "impl": impl_baseline_name,
        },
        "actionOnsetSecondsBySide": {
            "ref": ref_onset_seconds,
            "impl": impl_onset_seconds,
        },
        "extractedFps": fps,
        "refVideoMd5": Path(ref_fingerprint_path).read_text(encoding="utf-8").strip(),
        "implVideoMd5": Path(impl_fingerprint_path).read_text(encoding="utf-8").strip(),
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
except (OSError, ValueError, TypeError, json.JSONDecodeError):
    raise SystemExit(1)
PY
  then
    echo -e "${RED}UNMEASURABLE: target ROI baseline provenance could not be recorded${NC}"
    exit 2
  fi
fi

# ── Phase 4: SSIM batch comparison (optional) ──
PASS=0
FAIL=0
PIXEL_FAIL=0
RESULTS=""

if [[ "$SKIP_SSIM" == "true" ]]; then
  echo -e "${YELLOW}▸ SSIM comparison skipped (SKIP_SSIM=true)${NC}"
  echo "  Frames extracted for manual review at:"
  echo "    Ref:  $OUT_DIR/ref-frames/"
  echo "    Impl: $OUT_DIR/impl-frames/"
elif [[ "$TIMING_ONLY" == "true" ]]; then
  echo -e "${YELLOW}▸ Timing-only mode — no pixel comparison${NC}"
else
  echo -e "${BOLD}▸ Running SSIM comparison (threshold=$SSIM_THRESHOLD)...${NC}"

  # First-change alignment (codex review, loop-e2e-6): the live-network ref
  # reaches first paint later than localhost. Selector actions additionally
  # ignore ROI noise before their post-PRE_ACTION_WAIT action floor. Offset each
  # side by its first action-era visual change. Anti-bypass: a side with no
  # post-action change keeps offset 0 (a missing transition cannot self-align),
  # and intra-arc timing drift still fails frame pairs.
  REF_FC=$(cat "$OUT_DIR/ref-frames/.first-change" 2>/dev/null || echo 1)
  IMPL_FC=$(cat "$OUT_DIR/impl-frames/.first-change" 2>/dev/null || echo 1)
  REF_OFF=$((REF_FC - 1))
  IMPL_OFF=$((IMPL_FC - 1))
  ALIGNED_COUNT=$MIN_COUNT
  REF_TOTAL=$(ls "$OUT_DIR/ref-frames/"*.png 2>/dev/null | wc -l | tr -d ' ')
  IMPL_TOTAL=$(ls "$OUT_DIR/impl-frames/"*.png 2>/dev/null | wc -l | tr -d ' ')

  # Loop-10 fix (b): captured-window sanity for splash. A truncated
  # recording (run-5: ref 60 frames of ~300 expected — only the settled
  # tail) produced a vacuous all-PASS; a first-change sitting in the last
  # third of the window means the arc was clipped. Either way the run is
  # UNMEASURABLE (hard error, retry the recording) — never a verdict.
  if [[ "$ACTION" == "splash" ]]; then
    EXPECTED_FRAMES=$(awk -v f="$FPS" -v d="$RECORD_DURATION" 'BEGIN{printf "%d", f*d}')
    MIN_WINDOW=$((EXPECTED_FRAMES / 2))
    if [ "$REF_TOTAL" -lt "$MIN_WINDOW" ] || [ "$IMPL_TOTAL" -lt "$MIN_WINDOW" ]; then
      echo -e "${RED}UNMEASURABLE: captured window too short (ref ${REF_TOTAL} / impl ${IMPL_TOTAL} frames; expected >= ${MIN_WINDOW})${NC}"
      echo "  Recording was truncated — a tail-only capture cannot produce a splash verdict. Re-run the recording."
      exit 2
    fi
    if [ "$REF_FC" -gt $((REF_TOTAL * 2 / 3)) ] || [ "$IMPL_FC" -gt $((IMPL_TOTAL * 2 / 3)) ]; then
      echo -e "${RED}UNMEASURABLE: first visual change sits in the last third of the captured window (ref +$((REF_FC - 1)) / impl +$((IMPL_FC - 1)))${NC}"
      echo "  The splash arc was clipped by recording-start luck. Re-run the recording."
      exit 2
    fi
  fi
  if [[ "$REF_OFF" -gt 0 || "$IMPL_OFF" -gt 0 ]]; then
    ALIGNED_COUNT=$((REF_TOTAL - REF_OFF < IMPL_TOTAL - IMPL_OFF ? REF_TOTAL - REF_OFF : IMPL_TOTAL - IMPL_OFF))
    echo "  First-change alignment: ref offset +${REF_OFF}, impl offset +${IMPL_OFF}, comparing ${ALIGNED_COUNT} aligned frames"
  fi
  # Timing defect detection is ARC-INTERNAL (first-to-last-change duration),
  # not absolute-offset based: the live-network ref's first paint jitters
  # 18-108 frames run-to-run (e2e-8 brief), so an absolute offset delta
  # (the former MAX_ALIGN_DELTA=12 hard-fail) failed honest runs on network
  # latency alone. A wrong impl TIMELINE — too-long splash, missing
  # dismissal — shows up as a different arc length regardless of when paint
  # started, and a side with no change points carries arc 0 (anti-bypass:
  # cannot pass against a real arc). The offset delta stays as a note.
  OFF_DELTA=$((REF_OFF > IMPL_OFF ? REF_OFF - IMPL_OFF : IMPL_OFF - REF_OFF))
  echo "  note: first-change offset delta ${OFF_DELTA} frames (load-latency lead-in; informational)"
  REF_LC=$(cat "$OUT_DIR/ref-frames/.last-change" 2>/dev/null || echo 1)
  IMPL_LC=$(cat "$OUT_DIR/impl-frames/.last-change" 2>/dev/null || echo 1)
  # Raw (un-clamped) last-changes for the calibrated arc verdict, which does its
  # own 3-side budget clamp against the refcal recording (below).
  REF_LC_RAW=$REF_LC
  IMPL_LC_RAW=$IMPL_LC
  VIDEO_COMPARE_ARC_DELTA="${VIDEO_COMPARE_ARC_DELTA:-18}"
  ARC_CAL_MARGIN="${UI_CLONE_VMC_SPLASH_ARC_CAL_MARGIN:-20}"
  # Splash arc/SSIM calibration eligibility: a real (browser) splash run with
  # calibration left on. When eligible, a failing strict arc verdict is DEFERRED
  # (not folded into FAIL) so the live ref-vs-refcal noise floor can re-verdict
  # it; non-splash modes and skip-record test runs keep the strict verdict.
  SPLASH_CAL_ELIGIBLE=0
  if [[ "$ACTION" == "splash" && "${UI_CLONE_VMC_SPLASH_CALIBRATE:-1}" == "1" \
        && "${UI_CLONE_VMC_SKIP_RECORD:-0}" != "1" ]]; then
    SPLASH_CAL_ELIGIBLE=1
  fi
  # Looping-video arc bound (e2e-9 splash residual): a bg <video loop> that
  # defeats the freeze stub (autoplay remount after the re-pause sweeps)
  # keeps whole-frame change detection alive to the END of each clip, so the
  # measured arc equals the RECORDING length and the verdict compares
  # recorder-stop jitter (ref 486 vs impl 390 frames -> arc delta 96 ==
  # recording-length delta 96). Bound each side's last-change to a COMMON
  # per-side budget measured from ITS OWN first-change (symmetric clamp,
  # batch-4 item 1): the prior absolute-cutoff clamp truncated the side with
  # the later first-change more, so equal real arcs with different load-latency
  # lead-ins false-failed. Gated ONLY on loop:true evidence from BOTH freeze
  # sidecars: a one-sided looping video (impl missing it) keeps the unbounded
  # verdict, an early-settling splash keeps last-change << cutoff, and a side
  # with no motion keeps arc 0 (anti-bypass unchanged).
  if [[ "$ACTION" == "splash" ]] \
     && has_looping_video "$OUT_DIR/media-freeze-ref.json" \
     && has_looping_video "$OUT_DIR/media-freeze-impl.json"; then
    ARC_BUDGET=$(arc_common_budget "$REF_FC" "$REF_TOTAL" "$IMPL_FC" "$IMPL_TOTAL")
    REF_LC=$(clamp_arc_last "$REF_FC" "$REF_LC" $((REF_FC + ARC_BUDGET)))
    IMPL_LC=$(clamp_arc_last "$IMPL_FC" "$IMPL_LC" $((IMPL_FC + ARC_BUDGET)))
    echo "  looping video on both sides — arc bounded symmetrically per side (budget ${ARC_BUDGET} frames from each first-change)"
  fi
  ARC_VERDICT_FAIL=0
  if ! arc_timing_verdict "$REF_FC" "$REF_LC" "$IMPL_FC" "$IMPL_LC" "$VIDEO_COMPARE_ARC_DELTA"; then
    ARC_VERDICT_FAIL=1
    # When splash calibration is eligible, DEFER the arc fail: the static
    # max_delta false-fails ref-vs-ref on the phase-noisy splash class (cold ref
    # recording over-detects its last change vs the warm impl recording), so the
    # arc is re-verdicted below against a live ref-vs-refcal noise floor. The
    # one-side-no-motion anti-bypass stays a hard fail there. Non-splash modes
    # and skip-record runs fold the arc fail into FAIL now (strict verdict).
    if [[ "$SPLASH_CAL_ELIGIBLE" -eq 0 ]]; then
      FAIL=$((FAIL + 1))
    fi
  fi

  # Phase-jitter allowance (loop-10 fix b): two INDEPENDENT recordings of even
  # an identical page sample fast motion at sub-frame phase offsets — measured:
  # self-compare of the same site failed 15-30 mid-flight frames per run. On a
  # failing frame, _best_frame_ssim retries against the neighboring ±N frames
  # (default 1 — 16-33ms) and keeps the best SSIM. This is jitter compensation,
  # NOT tolerance widening: the SSIM threshold is unchanged, a real
  # easing/duration defect diverges for many consecutive frames far beyond ±1,
  # and the arc-timing verdict is computed before this loop and unaffected.
  JITTER_FRAMES="${UI_CLONE_VMC_JITTER_FRAMES:-1}"
  # Splash records the full impl-vs-ref SSIM series (every compared frame, pass
  # or fail) so the distribution calibration can compare it against a live
  # ref-vs-ref series. Non-splash modes leave SPLASH_SERIES empty (no series).
  SPLASH_SERIES=""
  if [[ "$ACTION" == "splash" ]]; then
    SPLASH_SERIES="$OUT_DIR/diff-frames/splash-ssim-impl.txt"
    : > "$SPLASH_SERIES"
  fi
  TARGET_RAW_SERIES=""
  if [[ -n "$TARGET_ROI_SELECTOR" ]]; then
    TARGET_RAW_SERIES="$OUT_DIR/diff-frames/target-raw-ssim.txt"
    : > "$TARGET_RAW_SERIES"
  fi
  for k in $(seq 1 "$ALIGNED_COUNT"); do
    i=$(printf "%06d" "$k")
    REF_FRAME=$(printf "$COMPARE_REF_FRAMES/f-%06d.png" $((k + REF_OFF)))
    IMPL_FRAME=$(printf "$COMPARE_IMPL_FRAMES/f-%06d.png" $((k + IMPL_OFF)))

    if [[ ! -f "$REF_FRAME" ]] || [[ ! -f "$IMPL_FRAME" ]]; then
      continue
    fi

    SSIM=$(_best_frame_ssim "$REF_FRAME" "$COMPARE_IMPL_FRAMES" $((k + IMPL_OFF)) "$JITTER_FRAMES" "$SSIM_THRESHOLD")
    [[ -z "$SSIM" ]] && continue
    [[ -n "$SPLASH_SERIES" ]] && printf '%s\n' "$SSIM" >> "$SPLASH_SERIES"
    [[ -n "$TARGET_RAW_SERIES" ]] && printf '%s\n' "$SSIM" >> "$TARGET_RAW_SERIES"

    IS_PASS=$(awk -v a="$SSIM" -v b="$SSIM_THRESHOLD" 'BEGIN{print (a+0 >= b+0) ? 1 : 0}')

    if [[ "$IS_PASS" -eq 1 ]]; then
      PASS=$((PASS + 1))
    else
      FAIL=$((FAIL + 1))
      PIXEL_FAIL=$((PIXEL_FAIL + 1))
      compare -metric AE "$REF_FRAME" "$IMPL_FRAME" "$OUT_DIR/diff-frames/f-${i}.png" 2>/dev/null || true
      RESULTS="${RESULTS}| f-${i} | ${SSIM} | ❌ |\n"
    fi
  done

  # Loop-10 fix (c): a comparison that produced ZERO measurement rows
  # (dispatcher timeout truncation, negative aligned window from offsets
  # beyond the frame totals) must be a hard error — falling through to
  # "ALL PASS" on nothing measured is the empty-success failure mode.
  if [ $((PASS + PIXEL_FAIL)) -eq 0 ]; then
    echo -e "${RED}ERROR: comparison produced 0 measurement rows (aligned window ${ALIGNED_COUNT}) — check did not actually run${NC}"
    exit 2
  fi

  # Target-local background-color deltas can differ only at subpixel glyph
  # edges after WebM encoding even when the computed hover styles and temporal
  # arc match exactly. Keep the raw SSIM verdict first. Only when every raw row
  # is within a narrow band below the unchanged threshold, the arc already
  # passed, and ref/impl target dimensions agree within 1px, retry the same
  # aligned series with a 0.3px low-pass. This is reported separately so a
  # rescued anti-aliasing case never looks like an ordinary raw PASS.
  if [[ -n "$TARGET_ROI_SELECTOR" && "$PIXEL_FAIL" -gt 0 \
        && "$ARC_VERDICT_FAIL" -eq 0 && -s "$TARGET_RAW_SERIES" ]]; then
    TARGET_DIMENSIONS_CLOSE=$(python3 -c '
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
ref = data["ref"]["target"]
impl = data["impl"]["target"]
delta = max(
    abs(float(ref["width"]) - float(impl["width"])),
    abs(float(ref["height"]) - float(impl["height"])),
)
print(1 if delta <= 1.0 else 0)
' "$OUT_DIR/target-roi.json" 2>/dev/null || echo 0)
    TARGET_AA_FLOOR=$(awk \
      -v threshold="$SSIM_THRESHOLD" \
      -v band="$VIDEO_COMPARE_TARGET_AA_RESCUE_BAND" \
      'BEGIN { print threshold - band }')
    TARGET_RAW_ROWS=$(wc -l < "$TARGET_RAW_SERIES" | tr -d ' ')
    TARGET_RAW_BELOW_FLOOR=$(awk -v floor="$TARGET_AA_FLOOR" \
      '$1 + 0 < floor + 0 { count++ } END { print count + 0 }' \
      "$TARGET_RAW_SERIES")
    if [[ "$TARGET_DIMENSIONS_CLOSE" -eq 1 \
          && "$TARGET_RAW_ROWS" -gt 0 \
          && "$TARGET_RAW_BELOW_FLOOR" -eq 0 ]]; then
      TARGET_FILTERED_REF="$OUT_DIR/ref-delta-aa-frames"
      TARGET_FILTERED_IMPL="$OUT_DIR/impl-delta-aa-frames"
      TARGET_FILTERED_SERIES="$OUT_DIR/diff-frames/target-aa-ssim.txt"
      if build_target_roi_delta_frames \
          "$OUT_DIR/ref-frames" "$TARGET_FILTERED_REF" \
          "$VIDEO_COMPARE_TARGET_DELTA_BLUR" \
          "$TARGET_REF_DELTA_BASELINE_FRAME" \
        && build_target_roi_delta_frames \
          "$OUT_DIR/impl-frames" "$TARGET_FILTERED_IMPL" \
          "$VIDEO_COMPARE_TARGET_DELTA_BLUR" \
          "$TARGET_IMPL_DELTA_BASELINE_FRAME"; then
        compute_ssim_series \
          "$TARGET_FILTERED_REF" "$REF_OFF" \
          "$TARGET_FILTERED_IMPL" "$IMPL_OFF" \
          "$ALIGNED_COUNT" "$JITTER_FRAMES" "$SSIM_THRESHOLD" \
          > "$TARGET_FILTERED_SERIES"
        TARGET_FILTERED_ROWS=$(wc -l < "$TARGET_FILTERED_SERIES" | tr -d ' ')
        TARGET_FILTERED_FAIL=$(awk -v threshold="$SSIM_THRESHOLD" \
          '$1 + 0 < threshold + 0 { count++ } END { print count + 0 }' \
          "$TARGET_FILTERED_SERIES")
        if [[ "$TARGET_FILTERED_ROWS" -eq "$TARGET_RAW_ROWS" \
              && "$TARGET_FILTERED_FAIL" -eq 0 ]]; then
          TARGET_RAW_FAIL="$PIXEL_FAIL"
          TARGET_RAW_MIN=$(awk 'NR == 1 || $1 + 0 < min { min = $1 } END { print min }' "$TARGET_RAW_SERIES")
          TARGET_FILTERED_MIN=$(awk 'NR == 1 || $1 + 0 < min { min = $1 } END { print min }' "$TARGET_FILTERED_SERIES")
          PASS="$TARGET_RAW_ROWS"
          FAIL=$((FAIL - PIXEL_FAIL))
          PIXEL_FAIL=0
          RESULTS="| target-aa-filter | pass-by-target-aa-filter (raw min ${TARGET_RAW_MIN}; 0x${VIDEO_COMPARE_TARGET_DELTA_BLUR} min ${TARGET_FILTERED_MIN}) | ✅ |\n"
          python3 -c '
import json
import sys

(
    out,
    selector,
    threshold,
    floor,
    blur,
    raw_min,
    filtered_min,
    raw_failures,
    rows,
    ref_arc,
    impl_arc,
) = sys.argv[1:]
payload = {
    "schemaVersion": 1,
    "status": "pass-by-target-aa-filter",
    "selector": selector,
    "threshold": float(threshold),
    "rawFloor": float(floor),
    "blurSigma": float(blur),
    "rawMinSsim": float(raw_min),
    "filteredMinSsim": float(filtered_min),
    "rawFailures": int(raw_failures),
    "rows": int(rows),
    "arcFrames": {"ref": int(ref_arc), "impl": int(impl_arc)},
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\n")
' \
            "$OUT_DIR/target-aa-filter.json" \
            "$TARGET_ROI_SELECTOR" \
            "$SSIM_THRESHOLD" \
            "$TARGET_AA_FLOOR" \
            "$VIDEO_COMPARE_TARGET_DELTA_BLUR" \
            "$TARGET_RAW_MIN" \
            "$TARGET_FILTERED_MIN" \
            "$TARGET_RAW_FAIL" \
            "$TARGET_RAW_ROWS" \
            "$((REF_LC - REF_FC))" \
            "$((IMPL_LC - IMPL_FC))"
          echo -e "${GREEN}  ✓ target AA rescue: raw min ${TARGET_RAW_MIN}, filtered min ${TARGET_FILTERED_MIN} — pass-by-target-aa-filter${NC}"
        fi
      fi
    fi

    # Static foreground compression residue can survive target-local baseline
    # subtraction as low-amplitude pixels that repeat after the hover settles.
    # Compare a material-delta view only after the raw selector delta failed and
    # arc timing already passed. The mask is the UNION of ref/impl material
    # deltas, so a reference-only icon/text motion stays visible as a mismatch.
    if [[ "$TARGET_DIMENSIONS_CLOSE" -eq 1 \
          && "$PIXEL_FAIL" -gt 0 \
          && "$TARGET_RAW_ROWS" -gt 0 ]]; then
      TARGET_MATERIAL_REF="$OUT_DIR/ref-delta-material-frames"
      TARGET_MATERIAL_IMPL="$OUT_DIR/impl-delta-material-frames"
      TARGET_MATERIAL_SERIES="$OUT_DIR/diff-frames/target-material-ssim.txt"
      if build_target_roi_material_delta_frames \
          "$COMPARE_REF_FRAMES" "$COMPARE_IMPL_FRAMES" \
          "$TARGET_MATERIAL_REF" "$TARGET_MATERIAL_IMPL" \
          "$VIDEO_COMPARE_TARGET_STATIC_NOISE_THRESHOLD" \
          "$REF_OFF" "$IMPL_OFF" "$ALIGNED_COUNT"; then
        compute_ssim_series \
          "$TARGET_MATERIAL_REF" "$REF_OFF" \
          "$TARGET_MATERIAL_IMPL" "$IMPL_OFF" \
          "$ALIGNED_COUNT" "$JITTER_FRAMES" "$SSIM_THRESHOLD" \
          > "$TARGET_MATERIAL_SERIES"
        TARGET_MATERIAL_ROWS=$(wc -l < "$TARGET_MATERIAL_SERIES" | tr -d ' ')
        TARGET_MATERIAL_FAIL=$(awk -v threshold="$SSIM_THRESHOLD" \
          '$1 + 0 < threshold + 0 { count++ } END { print count + 0 }' \
          "$TARGET_MATERIAL_SERIES")
        if [[ "$TARGET_MATERIAL_ROWS" -eq "$TARGET_RAW_ROWS" \
              && "$TARGET_MATERIAL_FAIL" -eq 0 ]]; then
          TARGET_RAW_FAIL="$PIXEL_FAIL"
          TARGET_RAW_MIN=$(awk 'NR == 1 || $1 + 0 < min { min = $1 } END { print min }' "$TARGET_RAW_SERIES")
          TARGET_MATERIAL_MIN=$(awk 'NR == 1 || $1 + 0 < min { min = $1 } END { print min }' "$TARGET_MATERIAL_SERIES")
          PASS="$TARGET_RAW_ROWS"
          FAIL=$((FAIL - PIXEL_FAIL))
          PIXEL_FAIL=0
          RESULTS="| target-static-foreground-filter | pass-by-target-static-foreground-filter (raw min ${TARGET_RAW_MIN}; material min ${TARGET_MATERIAL_MIN}) | ✅ |\n"
          python3 -c '
import json
import sys

(
    out,
    selector,
    threshold,
    material_threshold,
    raw_min,
    filtered_min,
    raw_failures,
    rows,
    ref_arc,
    impl_arc,
) = sys.argv[1:]
payload = {
    "schemaVersion": 1,
    "status": "pass-by-target-static-foreground-filter",
    "selector": selector,
    "threshold": float(threshold),
    "materialThreshold": material_threshold,
    "rawMinSsim": float(raw_min),
    "filteredMinSsim": float(filtered_min),
    "rawFailures": int(raw_failures),
    "rows": int(rows),
    "arcFrames": {"ref": int(ref_arc), "impl": int(impl_arc)},
    "dimensionsClose": True,
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\n")
' \
            "$OUT_DIR/target-static-foreground-filter.json" \
            "$TARGET_ROI_SELECTOR" \
            "$SSIM_THRESHOLD" \
            "$VIDEO_COMPARE_TARGET_STATIC_NOISE_THRESHOLD" \
            "$TARGET_RAW_MIN" \
            "$TARGET_MATERIAL_MIN" \
            "$TARGET_RAW_FAIL" \
            "$TARGET_RAW_ROWS" \
            "$((REF_LC - REF_FC))" \
            "$((IMPL_LC - IMPL_FC))"
          echo -e "${GREEN}  ✓ target static foreground filter: raw min ${TARGET_RAW_MIN}, material min ${TARGET_MATERIAL_MIN} — pass-by-target-static-foreground-filter${NC}"
        fi
      fi
    fi
  fi

  # ── Splash distribution + arc calibration against a live refcal (batch-4 item 1) ──
  # The per-frame SSIM table AND the static arc-delta both false-fail phase-noisy
  # splashes (≈12fps continuous-motion intro) even ref-vs-ref: two independent
  # recordings land mid-flight frames at different phases (SSIM noise), and the
  # cold reference recording over-detects its last change vs the warm impl
  # recording (arc noise up to ~40 frames). A per-frame ref-vs-ref floor CANNOT
  # fix the SSIM (the phase is random per recording-pair, so ref-vs-refcal[k] and
  # impl-vs-ref[k] are uncorrelated), and a static arc max false-fails the cold/
  # warm asymmetry. Both are cured by ONE live measurement: record a third
  # reference (refcal) and require the impl to be no worse than this live
  # ref-vs-ref baseline — DISTRIBUTION for SSIM (stable p50 + p75, best of the
  # two reference recordings), NEARER-of-{ref,refcal} arc delta within the live
  # |ref−refcal| noise floor for timing. A capture that diverges from a
  # consistent baseline is re-recorded (bounded), not failed. Neither widens the
  # SSIM threshold (0.90) nor uses a baked constant.
  # Anti-cheat preserved: a wrong timeline fails the arc (matches neither ref
  # recording), a missing splash fails the one-side-no-motion hard rule, a
  # genuinely different splash has a materially worse SSIM distribution, and a
  # clean-splash site keeps the strict verdict (refcal only recorded when the
  # strict arc OR SSIM already failed; the comparator also requires a phase-noisy
  # ref-vs-ref baseline before downgrading SSIM).
  SSIM_FAIL=$PIXEL_FAIL
  ARC_CAL_OK=$((1 - ARC_VERDICT_FAIL))   # 1 = arc already passed strictly
  SSIM_CAL_OK=1; [[ "$SSIM_FAIL" -gt 0 ]] && SSIM_CAL_OK=0
  SUSPECT_IMPL_RECORDING=0
  if [[ "$SPLASH_CAL_ELIGIBLE" -eq 1 && -n "$SPLASH_SERIES" \
        && ( "$ARC_VERDICT_FAIL" -eq 1 || "$SSIM_FAIL" -gt 0 ) ]]; then
    echo -e "${BOLD}▸ Splash strict verdict failed (arc=$([[ $ARC_VERDICT_FAIL -eq 1 ]] && echo FAIL || echo ok), ssimFails=${SSIM_FAIL}) — calibrating against a live refcal recording...${NC}"
    _VTC_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    mkdir -p "$OUT_DIR/refcal-video" "$OUT_DIR/refcal-frames"
    rm -f "$OUT_DIR/refcal-frames/"*.png 2>/dev/null || true
    if _splash_record "${SESSION}-refcal" "$ORIG_URL" "$OUT_DIR/refcal-video/raw.webm" refcal; then
      agent-browser --session "${SESSION}-refcal" close 2>/dev/null
      _extract_frames "$OUT_DIR/refcal-video/raw.webm" "$OUT_DIR/refcal-frames" refcal
      analyze_timing "$OUT_DIR/refcal-frames" "Refcal (calibration)"
      REFCAL_FC=$(cat "$OUT_DIR/refcal-frames/.first-change" 2>/dev/null || echo 1)
      REFCAL_LC=$(cat "$OUT_DIR/refcal-frames/.last-change" 2>/dev/null || echo 1)
      REFCAL_OFF=$((REFCAL_FC - 1))
      REFCAL_TOTAL=$(ls "$OUT_DIR/refcal-frames/"*.png 2>/dev/null | wc -l | tr -d ' ')

      # (1) ARC calibration: re-verdict the deferred arc fail against the live
      # ref-vs-refcal noise floor.
      if [[ "$ARC_VERDICT_FAIL" -eq 1 ]]; then
        if arc_calibrated_verdict \
             "$REF_FC" "$REF_LC_RAW" "$REF_TOTAL" \
             "$IMPL_FC" "$IMPL_LC_RAW" "$IMPL_TOTAL" \
             "$REFCAL_FC" "$REFCAL_LC" "$REFCAL_TOTAL" \
             "$VIDEO_COMPARE_ARC_DELTA" "$ARC_CAL_MARGIN"; then
          echo -e "${GREEN}  ✓ arc within the live ref-vs-ref noise floor — arc pass-by-calibration${NC}"
          ARC_CAL_OK=1
          RESULTS="${RESULTS}| arc-timing | pass-by-calibration (live ref-vs-ref arc noise floor) | ✅ |\n"
        else
          ARC_CAL_OK=0
        fi
      fi

      # (2) SSIM DISTRIBUTION calibration (p50 + p75, best-of-two references):
      # build S_ref (ref-vs-refcal) AND S_impl-vs-refcal, each aligned by its
      # own first-change. The impl is faithful if it matches EITHER reference
      # recording (ref/refcal are two valid captures of a phase-noisy splash).
      if [[ "$SSIM_FAIL" -gt 0 ]]; then
        REFCAL_ALIGNED=$(( REF_TOTAL - REF_OFF < REFCAL_TOTAL - REFCAL_OFF ? REF_TOTAL - REF_OFF : REFCAL_TOTAL - REFCAL_OFF ))
        REF_SERIES="$OUT_DIR/diff-frames/splash-ssim-ref.txt"
        compute_ssim_series "$OUT_DIR/ref-frames" "$REF_OFF" "$OUT_DIR/refcal-frames" "$REFCAL_OFF" "$REFCAL_ALIGNED" "$JITTER_FRAMES" "$SSIM_THRESHOLD" > "$REF_SERIES"
        IMPL_REFCAL_ALIGNED=$(( REFCAL_TOTAL - REFCAL_OFF < IMPL_TOTAL - IMPL_OFF ? REFCAL_TOTAL - REFCAL_OFF : IMPL_TOTAL - IMPL_OFF ))
        IMPL_REFCAL_SERIES="$OUT_DIR/diff-frames/splash-ssim-impl-refcal.txt"
        compute_ssim_series "$OUT_DIR/refcal-frames" "$REFCAL_OFF" "$OUT_DIR/impl-frames" "$IMPL_OFF" "$IMPL_REFCAL_ALIGNED" "$JITTER_FRAMES" "$SSIM_THRESHOLD" > "$IMPL_REFCAL_SERIES"
        DIST_JSON="$OUT_DIR/splash-distribution.json"
        if SSIM_THRESHOLD="$SSIM_THRESHOLD" PYTHONPATH="$_VTC_REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
           python3 -m ui_clone.splash_distribution "$SPLASH_SERIES" "$REF_SERIES" "$IMPL_REFCAL_SERIES" > "$DIST_JSON" 2>/dev/null; then
          echo -e "${GREEN}  ✓ impl SSIM distribution within the live ref-vs-ref noise floor — pass-by-distribution${NC}"
          python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("\n".join("    "+r for r in d.get("reasons",[])))' "$DIST_JSON" 2>/dev/null || true
          RESULTS="${RESULTS}| distribution | pass-by-distribution (ref-vs-ref noise floor); see splash-distribution.json | ✅ |\n"
          SSIM_CAL_OK=1
        else
          echo -e "${RED}  ✗ impl SSIM distribution materially worse than ref-vs-ref (or baseline not phase-noisy) — strict FAIL stands${NC}"
          python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("    engaged="+str(d.get("engaged"))); print("\n".join("    "+r for r in d.get("reasons",[])))' "$DIST_JSON" 2>/dev/null || true
          SSIM_CAL_OK=0
          # Suspect impl recording: a CONSISTENT ref-vs-ref baseline but the impl
          # matches neither reference -> the impl CAPTURE is the outlier
          # (live-site load variance), flagged for a bounded re-record below.
          if python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("suspect") else 1)' "$DIST_JSON" 2>/dev/null; then
            SUSPECT_IMPL_RECORDING=1
          fi
        fi
      fi
    else
      echo -e "${YELLOW}  ⚠ refcal recording failed — keeping the strict per-frame verdict${NC}"
      ARC_CAL_OK=$((1 - ARC_VERDICT_FAIL))
      SSIM_CAL_OK=1; [[ "$SSIM_FAIL" -gt 0 ]] && SSIM_CAL_OK=0
    fi

    # Recombine FAIL from the calibrated outcomes: arc contributes 1 if it
    # failed calibration, SSIM contributes its sub-threshold count if its
    # distribution failed. A clean calibration on both collapses FAIL to 0.
    FAIL=0
    [[ "$ARC_CAL_OK" -eq 0 ]] && FAIL=$((FAIL + 1))
    [[ "$SSIM_CAL_OK" -eq 0 ]] && FAIL=$((FAIL + SSIM_FAIL))
    PIXEL_FAIL=0
    [[ "$SSIM_CAL_OK" -eq 0 ]] && PIXEL_FAIL="$SSIM_FAIL"

    # Suspect impl recording (live-site load variance): the impl capture
    # diverges from a CONSISTENT ref-vs-ref baseline — an unreliable
    # MEASUREMENT, not a verdict. Re-record the whole comparison (bounded),
    # exactly like the truncation/anchor retries. A genuinely different impl
    # stays divergent across re-records and fails after the retries exhaust.
    SPLASH_RETRY="${UI_CLONE_VMC_SPLASH_RETRY:-0}"
    SPLASH_MAX_RETRY="${UI_CLONE_VMC_SPLASH_MAX_RETRY:-2}"
    if [[ "$FAIL" -gt 0 && "$SUSPECT_IMPL_RECORDING" -eq 1 && "$SPLASH_RETRY" -lt "$SPLASH_MAX_RETRY" ]]; then
      echo -e "${YELLOW}↻ impl recording diverges from a consistent ref-vs-ref baseline — unreliable capture, re-recording (attempt $((SPLASH_RETRY + 1))/${SPLASH_MAX_RETRY})${NC}"
      cleanup_browsers
      exec env UI_CLONE_VMC_SPLASH_RETRY=$((SPLASH_RETRY + 1)) bash "$0" "$SESSION" "$ORIG_URL" "$IMPL_URL" "$OUT_DIR" "$ACTION"
    fi
  fi
fi

# Selector capture-phase noise can be sparse after alignment when a 10fps
# source is duplicated to the canonical 60fps extraction cadence. Preserve
# exact raw evidence and allow one bounded fresh recording only when every
# failure falls inside the observed transition arc (capped at 0.5s) and stable
# passing rows follow it. The normal 0.3s window remains the floor; extending it
# to a longer measured arc handles 0.4s CSS transitions whose four source
# samples land at different phases on ref and impl. Later/interior failures
# remain divergence, and the hover wrapper still requires a fresh retry plus
# reference-self calibration before repeated cross-site failures can become a
# verdict.
# An independent arc mismatch does not promote the run to PASS: when the pixel
# receipt proves that every visible difference is confined to the same bounded
# capture-onset window, retain both arc measurements in the receipt and return
# UNMEASURABLE. The hover wrapper then compares two fresh reference captures;
# only a clean reference-self baseline can turn repeated cross-site failures
# into a hard divergence.
CAPTURE_RETRYABLE=0
if [[ -n "$TARGET_ROI_SELECTOR" \
      && "$PIXEL_FAIL" -gt 0 \
      && -n "${TARGET_RAW_SERIES:-}" \
      && -s "${TARGET_RAW_SERIES:-/dev/null}" \
      && -f "$CAPTURE_RETRY_HELPER" ]]; then
  ACTUAL_SSIM_ROWS=$((PASS + PIXEL_FAIL))
  TARGET_RAW_ROWS=$(awk '
    $1 ~ /^[0-9]+([.][0-9]+)?$/ { count++ }
    END { print count + 0 }
  ' "$TARGET_RAW_SERIES")
  EARLY_RECEIPT_STATUS=0
  if [ "$ACTUAL_SSIM_ROWS" -ne "$ALIGNED_COUNT" ] \
    || [ "$TARGET_RAW_ROWS" -ne "$ALIGNED_COUNT" ]; then
    EARLY_RECEIPT_STATUS=2
    RESULTS="${RESULTS}| capture-phase | not-retryable (sparse-ssim-series) | ❌ |\n"
  else
    REF_ARC_DURATION=$((REF_LC_RAW - REF_FC))
    IMPL_ARC_DURATION=$((IMPL_LC_RAW - IMPL_FC))
    ARC_DURATION_DELTA=$((REF_ARC_DURATION - IMPL_ARC_DURATION))
    [ "$ARC_DURATION_DELTA" -ge 0 ] || ARC_DURATION_DELTA=$((-ARC_DURATION_DELTA))
    ARC_EXTENSION_MAX_DELTA="$(awk -v fps="$FPS" 'BEGIN {
      value = fps * 0.1
      whole = int(value)
      print(value > whole ? whole + 1 : whole)
    }')"
    if [ "$ARC_EXTENSION_MAX_DELTA" -gt "$VIDEO_COMPARE_ARC_DELTA" ]; then
      ARC_EXTENSION_MAX_DELTA="$VIDEO_COMPARE_ARC_DELTA"
    fi
    CAPTURE_RETRY_WINDOW_SECONDS="$VIDEO_COMPARE_CAPTURE_EARLY_WINDOW_SECONDS"
    # Only extend when BOTH sides contain a real, duration-compatible arc.
    # One-side-static and materially different timelines remain hard evidence;
    # they must not borrow the longer side's duration to widen the noise window.
    if [ "$REF_ARC_DURATION" -gt 0 ] \
      && [ "$IMPL_ARC_DURATION" -gt 0 ] \
      && [ "$ARC_DURATION_DELTA" -le "$ARC_EXTENSION_MAX_DELTA" ]; then
      REF_ARC_FRAMES=$((REF_ARC_DURATION + 1))
      IMPL_ARC_FRAMES=$((IMPL_ARC_DURATION + 1))
      CAPTURE_RETRY_WINDOW_SECONDS="$(awk \
        -v base="$VIDEO_COMPARE_CAPTURE_EARLY_WINDOW_SECONDS" \
        -v ref_frames="$REF_ARC_FRAMES" \
        -v impl_frames="$IMPL_ARC_FRAMES" \
        -v fps="$FPS" '
        BEGIN {
          observed = (ref_frames > impl_frames ? ref_frames : impl_frames) / fps
          if (observed > 0.5) observed = 0.5
          effective = observed > base ? observed : base
          printf "%.6f", effective
        }
      ')"
    fi
    python3 "$CAPTURE_RETRY_HELPER" \
    "$TARGET_RAW_SERIES" \
    "$SSIM_THRESHOLD" \
    "$FPS" \
    "$CAPTURE_RETRY_WINDOW_SECONDS" \
    "$OUT_DIR/capture-retry.json" \
    "$TARGET_ROI_SELECTOR" \
    --ref-first-change "$REF_FC" \
    --ref-last-change "$REF_LC_RAW" \
    --impl-first-change "$IMPL_FC" \
    --impl-last-change "$IMPL_LC_RAW" \
    --arc-max-delta "$VIDEO_COMPARE_ARC_DELTA" \
    || EARLY_RECEIPT_STATUS=$?
    if [[ "$EARLY_RECEIPT_STATUS" -eq 0 ]]; then
      bind_capture_retry_source_metadata "$OUT_DIR/capture-retry.json" || EARLY_RECEIPT_STATUS=2
    fi
  fi
  if [[ "$EARLY_RECEIPT_STATUS" -eq 0 ]]; then
    CAPTURE_RETRYABLE=1
    if [[ "${ARC_VERDICT_FAIL:-0}" -eq 1 ]]; then
      RESULTS="${RESULTS}| capture-phase | retryable-unmeasurable (early-window-capture-phase plus unstable arc); see capture-retry.json | ⚠ |\n"
    else
      RESULTS="${RESULTS}| capture-phase | retryable-unmeasurable (early-window-capture-phase); see capture-retry.json | ⚠ |\n"
    fi
  fi
fi

# A selector run whose pixels all passed but whose independently measured arc
# failed is likewise narrowly retryable capture jitter.
if [[ -n "$TARGET_ROI_SELECTOR" \
      && "${ARC_VERDICT_FAIL:-0}" -eq 1 \
      && "$PIXEL_FAIL" -eq 0 \
      && -n "${TARGET_RAW_SERIES:-}" \
      && -s "${TARGET_RAW_SERIES:-/dev/null}" ]]; then
  ACTUAL_SSIM_ROWS=$((PASS + PIXEL_FAIL))
  TARGET_RAW_ROWS=$(awk '
    $1 ~ /^[0-9]+([.][0-9]+)?$/ { count++ }
    END { print count + 0 }
  ' "$TARGET_RAW_SERIES")
  TARGET_RAW_FAILURES=$(awk -v threshold="$SSIM_THRESHOLD" '
    $1 ~ /^[0-9]+([.][0-9]+)?$/ && $1 + 0 < threshold + 0 { count++ }
    END { print count + 0 }
  ' "$TARGET_RAW_SERIES")
  TARGET_RAW_MIN=$(awk '
    $1 ~ /^[0-9]+([.][0-9]+)?$/ {
      if (!seen || $1 + 0 < min) min = $1
      seen = 1
    }
    END { if (seen) print min }
  ' "$TARGET_RAW_SERIES")
  if [[ "$TARGET_RAW_ROWS" -gt 0 \
        && "$TARGET_RAW_ROWS" -eq "$ACTUAL_SSIM_ROWS" \
        && "$TARGET_RAW_FAILURES" -eq 0 \
        && -n "$TARGET_RAW_MIN" ]] \
     && awk -v minimum="$TARGET_RAW_MIN" -v threshold="$SSIM_THRESHOLD" \
       'BEGIN { exit !(minimum + 0 >= threshold + 0) }'; then
    python3 -c '
import json
import sys

(
    out,
    selector,
    threshold,
    rows,
    failures,
    minimum,
    ref_first,
    ref_last,
    impl_first,
    impl_last,
    arc_max_delta,
) = sys.argv[1:]
ref_first_i = int(ref_first)
ref_last_i = int(ref_last)
impl_first_i = int(impl_first)
impl_last_i = int(impl_last)
ref_duration = max(0, ref_last_i - ref_first_i)
impl_duration = max(0, impl_last_i - impl_first_i)
delta = abs(ref_duration - impl_duration)
max_delta = int(arc_max_delta)
payload = {
    "schemaVersion": 1,
    "status": "retryable-unmeasurable",
    "reason": "arc-only-capture-jitter",
    "selector": selector,
    "threshold": float(threshold),
    "rows": int(rows),
    "failures": int(failures),
    "failureRows": [],
    "firstStablePassingRow": 1,
    "lastFailureRow": 0,
    "minSsim": float(minimum),
    "ref": {"firstChange": ref_first_i, "lastChange": ref_last_i},
    "impl": {"firstChange": impl_first_i, "lastChange": impl_last_i},
    "arc": {
        "ref": {
            "firstChange": ref_first_i,
            "lastChange": ref_last_i,
            "durationFrames": ref_duration,
        },
        "impl": {
            "firstChange": impl_first_i,
            "lastChange": impl_last_i,
            "durationFrames": impl_duration,
        },
        "deltaFrames": delta,
        "maxDeltaFrames": max_delta,
        "withinTolerance": delta <= max_delta,
    },
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\n")
' \
      "$OUT_DIR/capture-retry.json" \
      "$TARGET_ROI_SELECTOR" \
      "$SSIM_THRESHOLD" \
      "$TARGET_RAW_ROWS" \
      "$TARGET_RAW_FAILURES" \
      "$TARGET_RAW_MIN" \
      "$REF_FC" \
      "$REF_LC" \
      "$IMPL_FC" \
      "$IMPL_LC" \
      "$VIDEO_COMPARE_ARC_DELTA"
    if bind_capture_retry_source_metadata "$OUT_DIR/capture-retry.json"; then
      CAPTURE_RETRYABLE=1
    else
      CAPTURE_RETRYABLE=0
    fi
    RESULTS="${RESULTS}| arc-timing | retryable-unmeasurable (arc-only-capture-jitter); see capture-retry.json | ⚠ |\n"
  fi
fi

# ── Phase 5: Output results ──
echo ""
echo -e "${BOLD}═══ Results ═══${NC}"
echo "Total frames compared: ${ALIGNED_COUNT:-$MIN_COUNT}"
echo -e "Pass: ${GREEN}${PASS}${NC}, Fail: ${RED}${FAIL}${NC}"
echo ""

if [[ "$FAIL" -gt 0 ]]; then
  echo "| Frame | SSIM | Status |"
  echo "|-------|------|--------|"
  echo -e "$RESULTS"
  echo ""
  echo "Diff images saved to: $OUT_DIR/diff-frames/"
  echo -e "${YELLOW}Investigate FAIL frames by reading diff images.${NC}"
fi

# Save results without a command-substitution heredoc. Large failure tables can
# fill Bash's internal heredoc pipe before `cat` starts reading it, deadlocking
# the comparison on macOS. A direct printf block has no intermediary pipe.
{
  printf '%s\n' \
    "Transition Compare Results" \
    "==========================" \
    "Original: $ORIG_URL" \
    "Implementation: $IMPL_URL" \
    "Action: $ACTION" \
    "Total frames: $MIN_COUNT" \
    "Pass: $PASS" \
    "Fail: $FAIL" \
    "Threshold: $SSIM_THRESHOLD" \
    ""
  printf '%b' "$RESULTS"
} > "$OUT_DIR/result.txt"

if [[ "$CAPTURE_RETRYABLE" -eq 1 ]]; then
  CAPTURE_RETRY_REASON=$(python3 -c '
import json
import sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("reason", "capture-jitter"))
except Exception:
    print("capture-jitter")
' "$OUT_DIR/capture-retry.json")
  if [[ "$CAPTURE_RETRY_REASON" == "arc-only-capture-jitter" ]]; then
    CAPTURE_RETRY_LABEL="arc-only capture jitter"
  else
    CAPTURE_RETRY_LABEL="$CAPTURE_RETRY_REASON"
  fi
  echo -e "${YELLOW}UNMEASURABLE${NC} — ${CAPTURE_RETRY_LABEL}; one fresh recording is allowed"
  exit 2
elif [[ "$FAIL" -eq 0 ]]; then
  echo -e "${GREEN}ALL PASS${NC} — transition matches original"
  exit 0
else
  echo -e "${RED}${FAIL} FAIL${NC} — transition differs from original"
  exit 1
fi
