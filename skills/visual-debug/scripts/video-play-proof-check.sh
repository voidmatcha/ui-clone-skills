#!/usr/bin/env bash
# video-play-proof-check.sh — prove that impl <video> elements
# actually play, not just exist.
#
# Usage:
#   video-play-proof-check.sh <session> <impl-url> <ref-dir>
#
#
# What this gate checks:
#   1. When ref-dir has video signals (required-media.json with
#      .mp4/.webm entries OR transition-spec.json with a video-like
#      target), the impl page must contain at least one <video>
#      element AND that element must advance currentTime by > 0.1s
#      within a 2s observation window after attempted play().
#   2. The probe respects browser autoplay policies: muted videos are
#      called via play() in the probe to satisfy the user-gesture
#      requirement; unmuted videos that don't autoplay are not failed
#      (the impl can't beat browser autoplay policy without UI).
#
# Skips when:
#   - ref-dir has no video signal (no video entries in required-media
#     and no <video>-class transition in transition-spec)
#   - agent-browser CLI missing
#
# Writes:
#   <ref-dir>/video-play-proof.json
#
# Exit 0 on pass/skip, 1 on video present but never advances, 2 on
# setup error.

set -uo pipefail

SESSION="${1:?Usage: video-play-proof-check.sh <session> <impl-url> <ref-dir>}"
IMPL_URL="${2:?impl-url required}"
REF_DIR="${3:?ref-dir required}"
WAIT_MS="${VIDEO_PLAY_PROOF_WAIT_MS:-2000}"
WAIT_S=$(awk -v ms="$WAIT_MS" 'BEGIN { printf "%.2f", ms/1000 }')

if ! command -v agent-browser >/dev/null 2>&1; then
  echo "video-play-proof: agent-browser CLI missing" >&2
  exit 2
fi
[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

OUT="$REF_DIR/video-play-proof.json"
PROBE_SESSION="${SESSION}-vpp"
PROBE_RAW=$(mktemp -t vpp.XXXX.json)
trap 'rm -f "$PROBE_RAW"; agent-browser --session "$PROBE_SESSION" close >/dev/null 2>&1 || true' EXIT

# ── Detect whether ref needs video ────────────────────────────────────
REF_HAS_VIDEO="false"
for name in required-media.json required-media-coverage.json transition-spec.json animations-detected.json; do
  path="$REF_DIR/$name"
  [ -f "$path" ] || continue
  if grep -Eiq '\.mp4|\.webm|\.mov|\.m3u8|\.mpd|"video"|"hero-video"|videoMime' "$path" 2>/dev/null; then
    REF_HAS_VIDEO="true"
    break
  fi
done

if [ "$REF_HAS_VIDEO" != "true" ]; then
  python3 - "$OUT" <<'PY'
import json, sys
from pathlib import Path
payload = {
    "schemaVersion": 1,
    "status": "skip",
    "reasons": ["ref has no video signal — gate does not apply"],
    "rule": (
        "When ref artifacts reference .mp4/.webm/.mov media or a video-class "
        "transition, the impl page must contain ≥1 <video> element AND that "
        "element must advance currentTime by >0.1s within a 2s observation "
        "window after attempted play()."
    ),
}
Path(sys.argv[1]).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"status": "skip", "out": sys.argv[1]}))
PY
  exit 0
fi

# ── Runtime probe ─────────────────────────────────────────────────────
agent-browser --session "$PROBE_SESSION" open "$IMPL_URL" --wait 2500 >/dev/null 2>&1 || true

agent-browser --session "$PROBE_SESSION" eval "
(async () => {
  const videos = Array.from(document.querySelectorAll('video'));
  if (videos.length === 0) {
    return JSON.stringify({ count: 0 });
  }
  const classify = (v) => {
    if (v.muted) return 'muted-autoplay';
    if (v.autoplay) return 'autoplay-unmuted';
    return 'gesture-required';
  };
  const before = videos.map((v) => ({
    src: v.currentSrc || v.src || '',
    muted: v.muted,
    autoplay: v.autoplay,
    kind: classify(v),
    readyState: v.readyState,
    currentTime: v.currentTime,
    paused: v.paused,
  }));
  // Try play() on each muted/autoplay video (browsers permit muted
  // autoplay without user gesture). gesture-required videos are
  // labeled but not forced — they pass the gate without playing.
  for (const v of videos) {
    if (v.muted || v.autoplay) {
      try { await v.play(); } catch (_) {}
    }
  }
  await new Promise(r => setTimeout(r, ${WAIT_MS}));
  const after = videos.map((v, i) => ({
    src: v.currentSrc || v.src || '',
    kind: classify(v),
    readyState: v.readyState,
    currentTime: v.currentTime,
    paused: v.paused,
    delta: v.currentTime - (before[i].currentTime || 0),
  }));
  // Advancement only required for muted/autoplay videos. gesture-only
  // videos are excluded from the advance count.
  const eligible = after.filter(a => a.kind !== 'gesture-required');
  const advanced = eligible.filter(a => a.delta > 0.1).length;
  return JSON.stringify({
    count: videos.length,
    eligibleCount: eligible.length,
    gestureOnlyCount: after.length - eligible.length,
    before,
    after,
    advancedCount: advanced,
    waitMs: ${WAIT_MS},
  });
})()
" > "$PROBE_RAW" 2>/dev/null || true

python3 - "$OUT" "$PROBE_RAW" "$IMPL_URL" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

out_path, probe_path, impl_url = sys.argv[1:4]

def read_probe(path: str) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:
        return {"error": "probe-missing"}
    for line in reversed(text.strip().splitlines()):
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            value = json.loads(s)
            if isinstance(value, str):
                value = json.loads(value)
            if isinstance(value, dict):
                return value
        except Exception:
            continue
    return {"error": "probe-parse-failed"}

probe = read_probe(probe_path)
reasons: list[str] = []
count = int(probe.get("count", 0))
advanced = int(probe.get("advancedCount", 0))

eligible = int(probe.get("eligibleCount", count))
gesture_only = int(probe.get("gestureOnlyCount", 0))
wait_ms = int(probe.get("waitMs", 2000))

if probe.get("error"):
    status = "fail"
    reasons.append(f"probe failed: {probe['error']}")
elif count == 0:
    status = "fail"
    reasons.append(
        "ref signaled video but impl page has zero <video> elements. "
        "required-media-coverage may pass on the .mp4 file existing in "
        "public/, but the element that renders it is missing."
    )
elif eligible == 0 and gesture_only > 0:
    # All videos require user gesture — gate cannot test them without UI.
    # Pass with informational note (skipped, not failed).
    status = "pass"
    reasons.append(
        f"informational: all {gesture_only} <video> element(s) are "
        "gesture-required (unmuted, non-autoplay) — cannot be tested "
        "without UI interaction. Gate passed without runtime advancement."
    )
elif advanced == 0:
    status = "fail"
    reasons.append(
        f"{eligible} <video> element(s) eligible for autoplay (muted/autoplay) "
        f"but none advanced currentTime by >0.1s in {wait_ms}ms. Causes: "
        "missing `autoplay muted playsinline`, src URL 404, codec mismatch, "
        "IntersectionObserver hiding the video before play() could fire, or "
        "src= bound to state that hasn't initialized."
    )
else:
    status = "pass"

payload = {
    "schemaVersion": 1,
    "status": status,
    "implUrl": impl_url,
    "videoCount": count,
    "advancedCount": advanced,
    "before": probe.get("before", [])[:10],
    "after": probe.get("after", [])[:10],
    "reasons": reasons,
    "nextAction": (
        "Add `autoplay muted playsinline` attributes to the impl <video>, "
        "OR programmatically call play() after the play-trigger condition "
        "is met. Confirm the src URL serves 200 and the codec is supported."
        if reasons else "all required videos advanced"
    ),
    "rule": (
        "When ref artifacts reference .mp4/.webm/.mov/.m3u8/.mpd media or a "
        "video-class transition, the impl page must contain ≥1 <video> element "
        "AND that element must advance currentTime by >0.1s within the "
        "observation window after attempted play(). Browsers allow muted "
        "autoplay without user gesture, so muted videos must demonstrate "
        "playback automatically; user-gesture-required (non-muted, non-autoplay) "
        "videos are not faulted because they cannot be triggered without UI."
    ),
}

Path(out_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"status": status, "videos": count, "advanced": advanced, "out": out_path}, ensure_ascii=False))
sys.exit(0 if status in ("pass", "skip") else 1)
PY
