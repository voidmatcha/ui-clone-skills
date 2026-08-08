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
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="$SCRIPT_DIR/video_play_proof.py"
trap 'rm -f "$PROBE_RAW"; agent-browser --session "$PROBE_SESSION" close >/dev/null 2>&1 || true' EXIT

# ── Detect whether ref needs video ────────────────────────────────────
REF_HAS_VIDEO=$(python3 "$HELPER" detect-ref "$REF_DIR")

if [ "$REF_HAS_VIDEO" != "true" ]; then
  python3 "$HELPER" write-skip "$OUT"
  exit 0
fi

# ── Runtime probe ─────────────────────────────────────────────────────
agent-browser --session "$PROBE_SESSION" open "$IMPL_URL" >/dev/null 2>&1 || true
sleep 3  # open --wait is not a supported flag; settle explicitly

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

python3 "$HELPER" write-result "$OUT" "$PROBE_RAW" "$IMPL_URL"
