#!/usr/bin/env bash
# canvas-webgl-detect.sh — Phase 0A render-surface detection.
#
# Usage:
#   canvas-webgl-detect.sh <url> <session> <ref-dir>
#
# Writes:
#   <ref-dir>/canvas-webgl-detection.json
#
# The pipeline run driver calls this before any DOM extraction so canvas/WebGL
# pages are identified as approximation-risk early instead of falling through
# to a DOM-only implementation path.
set -euo pipefail

URL="${1:?usage: canvas-webgl-detect.sh <url> <session> <ref-dir>}"
SESSION="${2:?usage: canvas-webgl-detect.sh <url> <session> <ref-dir>}"
REF_DIR="${3:?usage: canvas-webgl-detect.sh <url> <session> <ref-dir>}"

command -v agent-browser >/dev/null 2>&1 || {
  echo "canvas-webgl-detect.sh: agent-browser not found in PATH" >&2
  exit 2
}

mkdir -p "$REF_DIR"
OUT="$REF_DIR/canvas-webgl-detection.json"

agent-browser --session "$SESSION" open "$URL" >/dev/null
agent-browser --session "$SESSION" set viewport 1440 900 >/dev/null
agent-browser --session "$SESSION" wait 2000 >/dev/null

RAW="$(agent-browser --session "$SESSION" eval '(() => {
  const canvases = Array.from(document.querySelectorAll("canvas"));
  const canvasInfo = canvases.map((canvas, index) => {
    const rect = canvas.getBoundingClientRect();
    let hasWebGL = false;
    try {
      hasWebGL = !!(
        canvas.getContext("webgl") ||
        canvas.getContext("webgl2") ||
        canvas.getContext("experimental-webgl")
      );
    } catch (e) {
      hasWebGL = false;
    }
    return {
      index,
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      area: Math.round(rect.width * rect.height),
      hasWebGL
    };
  });
  const scripts = Array.from(document.scripts).map((s) => `${s.src || ""} ${s.textContent || ""}`);
  const hasCanvas = canvasInfo.length > 0;
  const hasWebGL = canvasInfo.some((c) => c.hasWebGL) ||
    scripts.some((s) => /webgl|three\.|threejs|babylon|pixi|spline|shader/i.test(s));
  return JSON.stringify({
    schemaVersion: 1,
    url: location.href,
    primaryRenderType: hasWebGL ? "webgl" : (hasCanvas ? "canvas" : "DOM"),
    hasCanvas,
    hasWebGL,
    canvasCount: canvasInfo.length,
    canvases: canvasInfo.slice(0, 20)
  });
})()')"

python3 - "$RAW" "$OUT" <<'PY'
import json
import sys
from pathlib import Path

raw = sys.argv[1].strip()
out = Path(sys.argv[2])

data = None
for candidate in (raw, raw.strip("'\"")):
    try:
        data = json.loads(candidate)
        break
    except Exception:
        pass
if isinstance(data, str):
    data = json.loads(data)
if not isinstance(data, dict):
    raise SystemExit(f"canvas-webgl-detect.sh: agent-browser returned non-object JSON: {raw[:120]}")

data.setdefault("schemaVersion", 1)
data.setdefault("primaryRenderType", "DOM")
data.setdefault("hasCanvas", False)
data.setdefault("hasWebGL", False)
out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
PY

echo "canvas-webgl-detect.sh: wrote $OUT"
