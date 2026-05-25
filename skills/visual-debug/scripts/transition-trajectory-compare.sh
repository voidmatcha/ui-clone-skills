#!/usr/bin/env bash
# transition-trajectory-compare.sh — sample N scroll positions, AE-diff per point.
#
# Role in the motion-check pipeline (after the staged-check redesign):
#   This script is the CHEAP pre-filter in front of `video-motion-compare.sh`'s
#   60fps SSIM pass. It runs first; if its 5-point trajectory diverges, the
#   gross motion already mismatches and the expensive video step is skipped.
#   When trajectory passes, the verdict is INCONCLUSIVE on its own (same end-
#   state + same midpoints with a different easing curve looks identical at
#   0/25/50/75/100) — `video-motion-compare.sh` then runs to give the
#   authoritative SSIM verdict. See the "Staged motion check" comment in
#   `video-motion-compare.sh` for the full rationale.
#
# Standalone use is still supported for ad-hoc debugging (signal triage,
# regression bisecting). It is NOT a verification-plan dispatch row — the
# dispatched row is `video-motion-compare`, which internally invokes this.
#
# Closes the "right end state, wrong trajectory" failure class. Static
# section-compare.sh only proves the resting frame matches. A scroll-scrub
# animation that arrives at the same end state via different easing or a
# different scroll threshold will pass section-compare and still feel wrong
# to a user. This script scrolls both ref and impl through the page at
# matched fractions of scrollHeight and AE-diffs each pair.
#
# Usage:
#   bash transition-trajectory-compare.sh <orig-url> <impl-url> <session> <ref-dir>
#
# Output:
#   <ref-dir>/transitions/trajectory/ref/<pct>.png
#   <ref-dir>/transitions/trajectory/impl/<pct>.png
#   <ref-dir>/transitions/trajectory/diff/<pct>.png
#   <ref-dir>/transitions/trajectory-result.txt   ← scanned by post-implement gate
#
# Sample points (default): 0 25 50 75 100 (percent of document scrollHeight).
# Override: TRAJECTORY_POINTS="0 20 40 60 80 100"
#
# Threshold: per-point AE/Mpx ceiling, default 4000 (looser than section-compare
# since the intermediate frames include the in-flight transform — sub-pixel
# AA noise dominates). Override: TRAJECTORY_AE_PER_MPX_MAX=8000

set -euo pipefail

VIEW_W="${VIEW_W:-1440}"
VIEW_H="${VIEW_H:-900}"
WAIT_REF="${WAIT_REF:-8000}"
WAIT_IMPL="${WAIT_IMPL:-6000}"
WAIT_SCROLL_SETTLE_MS="${WAIT_SCROLL_SETTLE_MS:-600}"
TRAJECTORY_POINTS="${TRAJECTORY_POINTS:-0 25 50 75 100}"
TRAJECTORY_AE_PER_MPX_MAX="${TRAJECTORY_AE_PER_MPX_MAX:-4000}"
FUZZ="${TRAJECTORY_FUZZ:-8%}"

ORIG_URL="${1:?Usage: transition-trajectory-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"
IMPL_URL="${2:?Usage: transition-trajectory-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"
SESSION="${3:?Usage: transition-trajectory-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"
DIR="${4:?Usage: transition-trajectory-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"

if [[ "$DIR" != /* ]]; then
  DIR="$(pwd)/$DIR"
fi

if ! command -v agent-browser >/dev/null 2>&1; then
  echo "ERROR: agent-browser CLI not on PATH" >&2
  exit 2
fi
if ! command -v magick >/dev/null 2>&1; then
  echo "ERROR: ImageMagick (magick) not on PATH" >&2
  exit 2
fi

SESSION_REF="${SESSION}-tj-ref"
SESSION_IMPL="${SESSION}-tj-impl"

cleanup() {
  agent-browser --session "$SESSION_REF"  close 2>/dev/null || true
  agent-browser --session "$SESSION_IMPL" close 2>/dev/null || true
}
trap cleanup EXIT

OUT_DIR="$DIR/transitions/trajectory"
mkdir -p "$OUT_DIR/ref" "$OUT_DIR/impl" "$OUT_DIR/diff"

structural_only_mode() {
  python3 - "$DIR" <<'PY'
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
asset_sub = ref_dir / "asset-substitution.json"
result = ref_dir / "sections" / "result.txt"
if not asset_sub.exists() or not result.exists():
    print("false")
    raise SystemExit(0)
try:
    data = json.loads(asset_sub.read_text(encoding="utf-8"))
except Exception:
    print("false")
    raise SystemExit(0)
if not data.get("structuralOnlySections"):
    print("false")
    raise SystemExit(0)
text = result.read_text(encoding="utf-8", errors="replace")
m = re.search(r"\*\*Result:\s*(\d+)\s+PASS,\s*(\d+)\s+FAIL,\s*(\d+)\s+SKIP,\s*(\d+)\s+STRUCTURAL_ONLY", text)
if not m:
    print("false")
    raise SystemExit(0)
fail = int(m.group(2))
structural = int(m.group(4))
print("true" if fail == 0 and structural > 0 else "false")
PY
}

# Open both sites once; reuse the session for every scroll-sample point so
# scroll-scrub timelines stay continuous instead of re-initializing per probe.
agent-browser --session "$SESSION_REF"  open "$ORIG_URL" --viewport "${VIEW_W}x${VIEW_H}" --wait "$WAIT_REF"  >/dev/null
agent-browser --session "$SESSION_IMPL" open "$IMPL_URL" --viewport "${VIEW_W}x${VIEW_H}" --wait "$WAIT_IMPL" >/dev/null

# Scroll height may differ between ref and impl when the impl viewport's
# content is shorter (lazy images, missing sections). Use ref's scrollHeight
# as the trajectory ruler; impl scrolls to the same FRACTION, not the same
# pixel — that's the only way a 0.8x-height impl can be compared trajectory-
# wise against the ref without phase-shifting every sample point.
REF_HEIGHT=$(agent-browser --session "$SESSION_REF" eval \
  "(() => Math.max(document.documentElement.scrollHeight - window.innerHeight, 0))()" 2>/dev/null \
  | tr -d '\n\r ' || echo "0")
IMPL_HEIGHT=$(agent-browser --session "$SESSION_IMPL" eval \
  "(() => Math.max(document.documentElement.scrollHeight - window.innerHeight, 0))()" 2>/dev/null \
  | tr -d '\n\r ' || echo "0")

echo "ref scroll-range: ${REF_HEIGHT}px  impl scroll-range: ${IMPL_HEIGHT}px"

if [ "$REF_HEIGHT" -le 0 ] && [ "$IMPL_HEIGHT" -le 0 ]; then
  # Pages don't scroll — trajectory is undefined but not a failure.
  {
    echo "# trajectory-compare: no-scroll page"
    echo "✅ skipped: neither page scrolls; trajectory check N/A"
  } > "$DIR/transitions/trajectory-result.txt"
  exit 0
fi

REPORT="$DIR/transitions/trajectory-result.txt"

STRUCTURAL_ONLY_MODE="${STRUCTURAL_TRAJECTORY_MODE:-$(structural_only_mode)}"
if [ "$STRUCTURAL_ONLY_MODE" = "true" ]; then
  SIG_DIR="$OUT_DIR/structural"
  mkdir -p "$SIG_DIR"
  SIGNATURE_JS='(() => JSON.stringify([...document.querySelectorAll(".patch[parallax=\"patch\"]")].map((el) => { const r = el.getBoundingClientRect(); const st = getComputedStyle(el); const key = [...el.classList].filter((c) => c !== "patch").sort().join(".") || el.className; const matrix = st.transform && st.transform !== "none" ? st.transform.match(/matrix.*\(([^)]+)\)/) : null; const parts = matrix ? matrix[1].split(",").map((v) => Number.parseFloat(v.trim())) : []; const ty = parts.length >= 6 ? parts[5] : 0; return { key, className: el.className, top: r.top, left: r.left, width: r.width, height: r.height, bottom: r.bottom, right: r.right, transform: st.transform, ty, visible: r.width > 1 && r.height > 1 && r.bottom >= -200 && r.top <= window.innerHeight + 200 }; }), null, 2))()'
  {
    echo "# transition-trajectory-compare"
    echo "# mode: structural-motion"
    echo "# sampled points: $TRAJECTORY_POINTS"
    echo "# ref scroll-range: ${REF_HEIGHT}px  impl scroll-range: ${IMPL_HEIGHT}px"
    echo
  } > "$REPORT"

  for pct in $TRAJECTORY_POINTS; do
    REF_Y=$(awk -v h="$REF_HEIGHT"  -v p="$pct" 'BEGIN { printf "%d", h * p / 100 }')
    IMPL_Y=$(awk -v h="$IMPL_HEIGHT" -v p="$pct" 'BEGIN { printf "%d", h * p / 100 }')
    agent-browser --session "$SESSION_REF"  eval "(() => { window.scrollTo({top: $REF_Y,  behavior: 'instant'}); return window.scrollY; })()" >/dev/null
    agent-browser --session "$SESSION_IMPL" eval "(() => { window.scrollTo({top: $IMPL_Y, behavior: 'instant'}); return window.scrollY; })()" >/dev/null
    sleep "$(awk -v ms="$WAIT_SCROLL_SETTLE_MS" 'BEGIN { printf "%.3f", ms/1000 }')"
    agent-browser --session "$SESSION_REF"  eval "$SIGNATURE_JS" > "$SIG_DIR/ref-$pct.json"
    agent-browser --session "$SESSION_IMPL" eval "$SIGNATURE_JS" > "$SIG_DIR/impl-$pct.json"
  done

  python3 - "$SIG_DIR" "$REPORT" $TRAJECTORY_POINTS <<'PY'
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sig_dir = Path(sys.argv[1])
report = Path(sys.argv[2])
points = sys.argv[3:]

MAX_TOP_DELTA = 90
MAX_LEFT_DELTA = 80
MAX_SIZE_RATIO = 0.40
MAX_TY_DELTA = 130

def load(path: Path) -> list[dict]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return []

def visible_map(items: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in items:
        key = str(item.get("key") or "")
        if key and item.get("visible"):
            out[key] = item
    return out

rows: list[str] = [
    "| point | target | topΔ | leftΔ | sizeΔ | tyΔ | status |",
    "| --- | --- | ---: | ---: | ---: | ---: | --- |",
]
fail = 0
passed = 0
compared = 0

for point in points:
    ref = visible_map(load(sig_dir / f"ref-{point}.json"))
    impl = visible_map(load(sig_dir / f"impl-{point}.json"))
    keys = sorted(set(ref) | set(impl))
    if not keys:
        rows.append(f"| {point}% | — | — | — | — | — | ⚠️ no visible motion targets |")
        continue
    for key in keys:
        r = ref.get(key)
        i = impl.get(key)
        compared += 1
        if not r or not i:
            fail += 1
            rows.append(f"| {point}% | `{key}` | — | — | — | — | ❌ missing target |")
            continue
        top_delta = abs(float(r.get("top", 0)) - float(i.get("top", 0)))
        left_delta = abs(float(r.get("left", 0)) - float(i.get("left", 0)))
        width_ref = max(abs(float(r.get("width", 0))), 1.0)
        height_ref = max(abs(float(r.get("height", 0))), 1.0)
        size_delta = max(
            abs(float(r.get("width", 0)) - float(i.get("width", 0))) / width_ref,
            abs(float(r.get("height", 0)) - float(i.get("height", 0))) / height_ref,
        )
        ty_delta = abs(float(r.get("ty", 0)) - float(i.get("ty", 0)))
        ok = (
            top_delta <= MAX_TOP_DELTA
            and left_delta <= MAX_LEFT_DELTA
            and size_delta <= MAX_SIZE_RATIO
            and ty_delta <= MAX_TY_DELTA
        )
        if ok:
            passed += 1
            status = "✅"
        else:
            fail += 1
            status = "❌"
        rows.append(
            f"| {point}% | `{key}` | {top_delta:.0f} | {left_delta:.0f} | "
            f"{size_delta:.2f} | {ty_delta:.0f} | {status} |"
        )

with report.open("a", encoding="utf-8") as fh:
    fh.write("\n".join(rows))
    fh.write("\n\n")
    if compared == 0:
        fh.write("❌ no visible motion targets sampled — structural trajectory probe is vacuous\n")
        raise SystemExit(1)
    if fail:
        fh.write(f"❌ {fail}/{compared} structural motion target sample(s) exceeded trajectory thresholds\n")
        raise SystemExit(1)
    fh.write(f"✅ all {passed}/{compared} visible structural motion target samples within trajectory thresholds\n")
PY
  STATUS=$?
  echo "Wrote $REPORT"
  exit "$STATUS"
fi

{
  echo "# transition-trajectory-compare"
  echo "# sampled points: $TRAJECTORY_POINTS"
  echo "# AE/Mpx ceiling: $TRAJECTORY_AE_PER_MPX_MAX (fuzz $FUZZ)"
  echo "# ref scroll-range: ${REF_HEIGHT}px  impl scroll-range: ${IMPL_HEIGHT}px"
  echo
  printf "| point | AE | AE/Mpx | status |\n"
  printf "| --- | --- | --- | --- |\n"
} > "$REPORT"

FAIL_COUNT=0
PASS_COUNT=0

for pct in $TRAJECTORY_POINTS; do
  REF_Y=$(awk -v h="$REF_HEIGHT"  -v p="$pct" 'BEGIN { printf "%d", h * p / 100 }')
  IMPL_Y=$(awk -v h="$IMPL_HEIGHT" -v p="$pct" 'BEGIN { printf "%d", h * p / 100 }')

  # Scroll both, then settle.
  agent-browser --session "$SESSION_REF"  eval "(() => { window.scrollTo({top: $REF_Y,  behavior: 'instant'}); return window.scrollY; })()" >/dev/null
  agent-browser --session "$SESSION_IMPL" eval "(() => { window.scrollTo({top: $IMPL_Y, behavior: 'instant'}); return window.scrollY; })()" >/dev/null

  # Settle: scroll-scrub libraries (ScrollTrigger, Lenis, Locomotive) apply
  # transforms on the next RAF tick after scroll. 600ms is empirically enough
  # for ease.out-style transitions to fully resolve at the sampled position;
  # bump WAIT_SCROLL_SETTLE_MS if your target uses long scrub durations.
  sleep "$(awk -v ms="$WAIT_SCROLL_SETTLE_MS" 'BEGIN { printf "%.3f", ms/1000 }')"

  REF_PNG="$OUT_DIR/ref/${pct}.png"
  IMPL_PNG="$OUT_DIR/impl/${pct}.png"
  DIFF_PNG="$OUT_DIR/diff/${pct}.png"

  agent-browser --session "$SESSION_REF"  screenshot "$REF_PNG"  >/dev/null
  agent-browser --session "$SESSION_IMPL" screenshot "$IMPL_PNG" >/dev/null

  if [ ! -s "$REF_PNG" ] || [ ! -s "$IMPL_PNG" ]; then
    printf "| %s%% | ERROR | — | ⚠️ |\n" "$pct" >> "$REPORT"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    continue
  fi

  # Normalize impl to ref dimensions before diff — a smaller-viewport impl
  # produces a smaller screenshot, and magick AE on size-mismatched inputs
  # returns the entire pixel count as different.
  REF_SIZE=$(magick identify -format "%wx%h" "$REF_PNG" 2>/dev/null || echo "")
  IMPL_SIZE=$(magick identify -format "%wx%h" "$IMPL_PNG" 2>/dev/null || echo "")
  if [ -n "$REF_SIZE" ] && [ "$REF_SIZE" != "$IMPL_SIZE" ]; then
    magick "$IMPL_PNG" -resize "$REF_SIZE!" -quality 95 "$IMPL_PNG" 2>/dev/null
  fi

  AE=$(magick compare -metric AE -fuzz "$FUZZ" "$REF_PNG" "$IMPL_PNG" "$DIFF_PNG" 2>&1 || true)
  AE=$(echo "$AE" | head -1 | awk '{ if ($1 ~ /^[0-9.eE+-]+$/) printf "%.0f\n", $1 }')

  if [ -z "$AE" ]; then
    printf "| %s%% | ERROR | — | ⚠️ |\n" "$pct" >> "$REPORT"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    continue
  fi

  REF_W=$(echo "$REF_SIZE" | cut -dx -f1)
  REF_H=$(echo "$REF_SIZE" | cut -dx -f2)
  AE_PER_MPX=$(awk -v ae="$AE" -v w="$REF_W" -v h="$REF_H" \
    'BEGIN { area = (w*h)/1000000; if (area > 0) printf "%.0f", ae/area; else print "0" }')

  if [ "$AE_PER_MPX" -le "$TRAJECTORY_AE_PER_MPX_MAX" ]; then
    STATUS="✅"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    STATUS="❌"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi

  printf "| %s%% | %s | %s | %s |\n" "$pct" "$AE" "$AE_PER_MPX" "$STATUS" >> "$REPORT"
done

{
  echo
  if [ "$FAIL_COUNT" -eq 0 ]; then
    echo "✅ all $PASS_COUNT sample points within ceiling"
  else
    echo "❌ $FAIL_COUNT/$((PASS_COUNT + FAIL_COUNT)) sample point(s) exceeded ceiling — trajectory diverges"
    echo "Inspect diffs in $OUT_DIR/diff/ and tighten the scrub/easing parameters."
  fi
} >> "$REPORT"

echo "Wrote $REPORT"
if [ "$FAIL_COUNT" -eq 0 ]; then
  exit 0
fi
exit 1
