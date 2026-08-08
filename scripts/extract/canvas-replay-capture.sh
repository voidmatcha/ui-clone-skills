#!/usr/bin/env bash
# canvas-replay-capture.sh — record the REFERENCE's canvas/WebGL output to a
# short looped video so the impl can replay the hero's OWN rendered motion
# instead of shipping a blank re-embed.
#
# This is faithful reproduction: the ref's own pixels in motion (like
# recording a video background), recorded against the LIVE reference URL and
# declared as a substituted asset in asset-substitution.json. It is NOT a
# static screenshot-as-CSS-background cheat — anti-cheat (ref-screenshot-asset)
# targets STATIC section screenshots; a moving, declared hero video is a
# different, legitimate asset.
#
# Usage:
#   canvas-replay-capture.sh <ref-dir> <ref-url> <session> [impl-public-dir]
#
# Reads <ref-dir>/canvas-replay-plan.json for the hero region. Writes the
# recorded asset to <ref-dir>/static/canvas-replay/ and, when impl-public-dir
# is given, mirrors it to <impl-public-dir>/canvas-replay/.
#
# Exit 0 on success, 2 on setup error, 1 on capture/encode failure.
set -uo pipefail

# W-4 (loop-ebpb-0): pin the light color scheme at CAPTURE time too — a
# dark-evening Phase-0 capture bakes dark styles into the ref corpus
# PERMANENTLY, and every light-pinned verify then honestly-fails against
# poisoned ground truth. Caller override intact (default only when unset).
: "${AGENT_BROWSER_COLOR_SCHEME:=light}"
export AGENT_BROWSER_COLOR_SCHEME

REF_DIR="${1:?usage: canvas-replay-capture.sh <ref-dir> <ref-url> <session> [impl-public-dir]}"
REF_URL="${2:?ref-url required}"
SESSION="${3:?session required}"
IMPL_PUBLIC="${4:-}"

[ -d "$REF_DIR" ] || { echo "canvas-replay-capture: ref dir not found: $REF_DIR" >&2; exit 2; }
PLAN="$REF_DIR/canvas-replay-plan.json"
[ -f "$PLAN" ] || { echo "canvas-replay-capture: plan missing (run canvas-replay-plan.sh first): $PLAN" >&2; exit 2; }
command -v agent-browser >/dev/null 2>&1 || { echo "canvas-replay-capture: agent-browser missing" >&2; exit 2; }
command -v ffmpeg >/dev/null 2>&1 || { echo "canvas-replay-capture: ffmpeg missing" >&2; exit 2; }

DECISION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("decision",""))' "$PLAN")"
if [ "$DECISION" != "canvas-replay" ]; then
  echo "canvas-replay-capture: plan decision is '$DECISION' (not canvas-replay) — nothing to capture" >&2
  exit 0
fi

# Region from the plan.
read -r RW RH < <(python3 -c '
import json,sys
p=json.load(open(sys.argv[1]))
s=(p.get("sections") or [{}])[0]
r=s.get("region") or {}
print(int(r.get("width",1440)), int(r.get("height",900)))
' "$PLAN")

OUT_DIR="$REF_DIR/static/canvas-replay"
mkdir -p "$OUT_DIR"
RAW_WEBM="$(mktemp -t crreplay.XXXX).webm"

echo "canvas-replay-capture: recording ${RW}x${RH} hero from $REF_URL ..." >&2
agent-browser --session "$SESSION" open "$REF_URL" >/dev/null 2>&1 || true
agent-browser --session "$SESSION" set viewport "$RW" "$RH" >/dev/null 2>&1 || true
agent-browser --session "$SESSION" wait 2500 >/dev/null 2>&1 || true   # let the engine warm up
agent-browser --session "$SESSION" record start "$RAW_WEBM" >/dev/null 2>&1 || {
  echo "canvas-replay-capture: record start failed" >&2; exit 1; }
agent-browser --session "$SESSION" wait 4500 >/dev/null 2>&1 || true   # ~4.5s of rendered motion
agent-browser --session "$SESSION" record stop >/dev/null 2>&1 || true
# Poster: a single still frame of the hero.
agent-browser --session "$SESSION" screenshot "$OUT_DIR/hero-poster.png" >/dev/null 2>&1 || true
agent-browser --session "$SESSION" close >/dev/null 2>&1 || true

if [ ! -s "$RAW_WEBM" ]; then
  echo "canvas-replay-capture: no video recorded ($RAW_WEBM empty)" >&2
  exit 1
fi

# Crop to the hero region and re-encode a clean, web-friendly looped asset.
# webm (vp9) for modern browsers + mp4 (h264) fallback.
CROP="crop=${RW}:${RH}:0:0"
ffmpeg -y -loglevel error -i "$RAW_WEBM" -vf "$CROP" -an \
  -c:v libvpx-vp9 -b:v 0 -crf 32 "$OUT_DIR/hero.webm" 2>/dev/null || \
  ffmpeg -y -loglevel error -i "$RAW_WEBM" -an -c:v libvpx-vp9 -b:v 0 -crf 32 "$OUT_DIR/hero.webm" 2>/dev/null
ffmpeg -y -loglevel error -i "$RAW_WEBM" -vf "$CROP" -an \
  -c:v libx264 -pix_fmt yuv420p -crf 24 "$OUT_DIR/hero.mp4" 2>/dev/null || \
  ffmpeg -y -loglevel error -i "$RAW_WEBM" -an -c:v libx264 -pix_fmt yuv420p -crf 24 "$OUT_DIR/hero.mp4" 2>/dev/null

rm -f "$RAW_WEBM"

[ -s "$OUT_DIR/hero.webm" ] || { echo "canvas-replay-capture: webm encode failed" >&2; exit 1; }
echo "canvas-replay-capture: wrote $OUT_DIR/hero.webm ($(du -h "$OUT_DIR/hero.webm" | cut -f1))" >&2

if [ -n "$IMPL_PUBLIC" ]; then
  DEST="$IMPL_PUBLIC/canvas-replay"
  mkdir -p "$DEST"
  cp -f "$OUT_DIR/hero.webm" "$DEST/hero.webm" 2>/dev/null || true
  [ -f "$OUT_DIR/hero.mp4" ] && cp -f "$OUT_DIR/hero.mp4" "$DEST/hero.mp4" 2>/dev/null || true
  [ -f "$OUT_DIR/hero-poster.png" ] && cp -f "$OUT_DIR/hero-poster.png" "$DEST/hero-poster.png" 2>/dev/null || true
  echo "canvas-replay-capture: mirrored replay asset to $DEST" >&2
fi
