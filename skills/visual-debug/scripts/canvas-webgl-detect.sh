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

  // Physics-engine detection. A 2D-physics canvas (matter.js drop-in letters,
  // verlet cloth, etc.) is INTERACTIVE: its identity is the running simulation
  // that spawns/drops/appends bodies, not a fixed frame. Routing it to the
  // canvas-replay video path (a static loop) is wrong — the loop cannot respond
  // or append. Detect a physics engine by runtime global first, then by bundle
  // signature, so a closure-scoped engine is still caught. Purely decorative
  // shader / Spline canvas has NO physics engine, so it stays "webgl" and is
  // unaffected (no brick).
  const physicsGlobals = {
    Matter: typeof window.Matter !== "undefined",
    planck: typeof window.planck !== "undefined",
    p2: typeof window.p2 !== "undefined",
    Box2D: typeof window.Box2D !== "undefined",
    Physics: typeof window.Physics !== "undefined"
  };
  const globalDetected = Object.values(physicsGlobals).some(Boolean);
  const physicsFromScripts = scripts.some((s) => /matter-js|matter\.min|Matter\.Engine|Matter\.Bodies|\bplanck\b|p2\.World|new Box2D|verlet|cannon\.js|cannon-es|rebound\.js/i.test(s));
  let physicsName = null;
  if (physicsGlobals.Matter) physicsName = "matter-js";
  else if (physicsGlobals.planck) physicsName = "planck";
  else if (physicsGlobals.p2) physicsName = "p2";
  else if (physicsGlobals.Box2D) physicsName = "box2d";
  else if (physicsGlobals.Physics) physicsName = "physicsjs";
  else if (physicsFromScripts) physicsName = "bundled-physics";
  // Physics routing requires an actual canvas surface. A physics engine that
  // is merely bundled (or drives DOM/SVG, not canvas) is not the interactive-
  // canvas case — without this gate a no-canvas page that ships matter.js would
  // wrongly route to canvas behavioral-repro.
  const hasPhysics = !!physicsName && hasCanvas;
  const matterVersion = (window.Matter && window.Matter.version) ? window.Matter.version : null;
  // Try to reach a live engine to read gravity/body count. Most sites keep the
  // engine closure-scoped, so this is best-effort and null is expected — the
  // clone then uses the library default constants plus any bundle-grep values.
  let liveEngine = null;
  if (window.Matter && window.Matter.Engine) {
    for (const k of Object.keys(window)) {
      try {
        const v = window[k];
        if (v && v.world && v.world.gravity && typeof v.world.gravity.y === "number") {
          liveEngine = {
            handle: k,
            gravity: { x: v.world.gravity.x, y: v.world.gravity.y, scale: v.world.gravity.scale },
            bodyCount: (v.world.bodies || []).length
          };
          break;
        }
      } catch (e) {}
    }
  }

  return JSON.stringify({
    schemaVersion: 1,
    url: location.href,
    primaryRenderType: hasWebGL ? "webgl" : (hasCanvas ? "canvas" : "DOM"),
    renderKind: hasPhysics ? "interactive-physics" : (hasWebGL ? "webgl" : (hasCanvas ? "canvas-2d" : "DOM")),
    hasCanvas,
    hasWebGL,
    hasPhysics,
    physicsEngine: hasPhysics ? {
      name: physicsName,
      version: matterVersion,
      source: globalDetected ? "runtime-global" : "bundle-script",
      liveEngine
    } : null,
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
data.setdefault("hasPhysics", False)
data.setdefault("renderKind", data.get("primaryRenderType", "DOM"))
data.setdefault("physicsEngine", None)
out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
PY

echo "canvas-webgl-detect.sh: wrote $OUT"
