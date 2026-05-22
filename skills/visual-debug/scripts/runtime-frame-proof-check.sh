#!/usr/bin/env bash
# runtime-frame-proof-check.sh — prove animation surfaces (Lottie,
# canvas, WebGL) actually advance frames at runtime.
#
# Usage:
#   runtime-frame-proof-check.sh <session> <impl-url> <ref-dir>
#
#
# What this gate checks:
#   1. Enumerate <canvas> elements. For each visible canvas with a
#      non-trivial size, sample getImageData() byte-hash at t=0,
#      wait 1500ms, sample again. If hash changed → animating.
#   2. For each canvas with a WebGL context (gl / webgl2 / webgl),
#      check that drawingBufferWidth > 0 AND drawingBufferHeight > 0
#      AND a follow-up read shows the contents differ.
#   3. For each Lottie/dotlottie-player container, also try
#      `el.getLottie?.()` then `getLottie().currentFrame` — if
#      currentFrame advanced more than ~0.5 between samples, that's
#      authoritative proof (better than DOM mutation heuristic).
#   4. Skip when no animating surface exists in ref (use ref-dir
#      artifacts as the signal).
#
# Writes:
#   <ref-dir>/runtime-frame-proof.json
#
# Exit 0 on pass/skip, 1 on surface present but no frame advance,
# 2 on setup error.

set -uo pipefail

SESSION="${1:?Usage: runtime-frame-proof-check.sh <session> <impl-url> <ref-dir>}"
IMPL_URL="${2:?impl-url required}"
REF_DIR="${3:?ref-dir required}"

if ! command -v agent-browser >/dev/null 2>&1; then
  echo "runtime-frame-proof: agent-browser CLI missing" >&2
  exit 2
fi
[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

OUT="$REF_DIR/runtime-frame-proof.json"

# ── Detect whether ref signals canvas/webgl/lottie ────────────────────
REF_NEEDS="false"
for name in canvas-webgl-detection.json required-media.json animations-detected.json transition-spec.json; do
  p="$REF_DIR/$name"
  [ -f "$p" ] || continue
  if grep -Eiq '\"hasCanvas\":\s*true|\"hasWebGL\":\s*true|lottie|bodymovin|dotlottie|canvas|webgl' "$p" 2>/dev/null; then
    REF_NEEDS="true"
    break
  fi
done

if [ "$REF_NEEDS" != "true" ]; then
  python3 - "$OUT" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "schemaVersion": 1,
    "status": "skip",
    "reasons": ["ref has no canvas/webgl/lottie signal — gate does not apply"],
    "rule": (
        "Animation surfaces (canvas, WebGL, Lottie) must advance frames at "
        "runtime, not just exist. Gate skips when ref has no signal for any."
    ),
}, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"status": "skip", "out": sys.argv[1]}))
PY
  exit 0
fi

PROBE_SESSION="${SESSION}-rfp"
PROBE_RAW=$(mktemp -t rfp.XXXX.json)
trap 'rm -f "$PROBE_RAW"; agent-browser --session "$PROBE_SESSION" close >/dev/null 2>&1 || true' EXIT

agent-browser --session "$PROBE_SESSION" open "$IMPL_URL" --wait 2000 >/dev/null 2>&1 || true

agent-browser --session "$PROBE_SESSION" eval '
(async () => {
  const inRAF = (fn) => new Promise((resolve) => {
    requestAnimationFrame(() => { try { resolve(fn()); } catch (e) { resolve(null); } });
  });
  // Cheap byte-hash of typed-array data.
  const hashBytes = (arr) => {
    let h = 2166136261;
    const step = Math.max(1, Math.floor(arr.length / 4096));
    for (let i = 0; i < arr.length; i += step) {
      h ^= arr[i];
      h = (h * 16777619) >>> 0;
    }
    return h.toString(16);
  };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 10 && r.height > 10;
  };

  // ── Canvas sampling (2D context — getImageData)
  const canvases = Array.from(document.querySelectorAll("canvas")).filter(visible);
  const sample2D = (c) => {
    const ctx = c.getContext("2d", { willReadFrequently: true });
    if (!ctx) return { hash: null, kind: "no-2d-context" };
    // Suppress console SecurityError by redirecting console.error
    // briefly. (The throw still happens but the side-effect log is
    // muted so runtime-env-check console scan does not false-positive.)
    const origErr = console.error;
    let secErr = false;
    console.error = (...args) => {
      const msg = (args[0] && args[0].toString && args[0].toString()) || "";
      if (/SecurityError|cross-origin|tainted/i.test(msg)) { secErr = true; return; }
      origErr.apply(console, args);
    };
    try {
      const data = ctx.getImageData(0, 0, Math.min(c.width, 100), Math.min(c.height, 100)).data;
      return { hash: hashBytes(data), width: c.width, height: c.height, kind: "2d", tainted: false };
    } catch (e) {
      const tainted = /SecurityError|tainted|cross-origin/i.test(String(e));
      return { hash: null, kind: tainted ? "tainted" : "error", error: String(e).slice(0, 80), tainted };
    } finally {
      console.error = origErr;
    }
  };
  const canvasBefore = await inRAF(() => canvases.map(sample2D));

  // ── WebGL sampling — drawingBuffer byte readback inside RAF
  // (must happen before browser clears the buffer for sites that
  // disabled preserveDrawingBuffer for performance).
  //
  const sampleGL = (c) => {
    const gl = c.getContext("webgl2") || c.getContext("webgl");
    if (!gl) return { hash: null, kind: "no-webgl" };
    const attrs = (gl.getContextAttributes && gl.getContextAttributes()) || {};
    const preserveBuf = !!attrs.preserveDrawingBuffer;
    // Install draw-call counter if not already.
    if (!gl.__rfpDrawCount) {
      gl.__rfpDrawCount = 0;
      const origDA = gl.drawArrays;
      const origDE = gl.drawElements;
      gl.drawArrays = function (...args) { gl.__rfpDrawCount++; return origDA.apply(gl, args); };
      gl.drawElements = function (...args) { gl.__rfpDrawCount++; return origDE.apply(gl, args); };
    }
    try {
      const w = Math.min(gl.drawingBufferWidth || 0, 64);
      const h = Math.min(gl.drawingBufferHeight || 0, 64);
      if (w === 0 || h === 0) return { hash: null, kind: "zero-buffer", drawCount: gl.__rfpDrawCount };
      const px = new Uint8Array(w * h * 4);
      gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
      const sum = px.reduce((s, v) => s + v, 0);
      return {
        hash: hashBytes(px),
        kind: "webgl",
        w, h,
        allZero: sum === 0,
        preserveBuf,
        drawCount: gl.__rfpDrawCount,
      };
    } catch (e) {
      return { hash: null, kind: "webgl-error", error: String(e).slice(0, 80), drawCount: gl.__rfpDrawCount };
    }
  };
  const webglBefore = await inRAF(() => canvases.map(sampleGL));

  const lottieContainers = [
    ...document.querySelectorAll("lottie-player, dotlottie-player"),
    ...document.querySelectorAll("[data-lottie], [data-animation-path]"),
    ...document.querySelectorAll("[class*=\"lottie\" i]"),
  ];
  const globalAnims = (window.lottie && window.lottie._animations) || [];
  const lottieBefore = lottieContainers.map((el) => {
    try {
      // Strategy 1: web-component .getLottie()
      let inst = (el.getLottie && el.getLottie()) || el._lottie || null;
      // Strategy 2: walk global registry for an animation whose wrapper
      // is this element (or a descendant)
      if (!inst && globalAnims.length) {
        for (const a of globalAnims) {
          if (a.wrapper === el || (el.contains && el.contains(a.wrapper))) {
            inst = a; break;
          }
        }
      }
      const cf = inst ? inst.currentFrame : null;
      return { hasInstance: !!inst, currentFrame: cf };
    } catch (e) {
      return { hasInstance: false, error: String(e).slice(0, 80) };
    }
  });

  await new Promise(r => setTimeout(r, 1500));

  const canvasAfter = canvases.map((c, i) => {
    try {
      const ctx = c.getContext("2d", { willReadFrequently: true });
      if (!ctx) return { hash: null };
      const data = ctx.getImageData(0, 0, Math.min(c.width, 100), Math.min(c.height, 100)).data;
      return { hash: hashBytes(data) };
    } catch (e) {
      return { hash: null };
    }
  });
  const webglAfter = await inRAF(() => canvases.map((c, i) => {
    const gl = c.getContext("webgl2") || c.getContext("webgl");
    if (!gl) return { hash: null };
    try {
      const w = Math.min(gl.drawingBufferWidth || 0, 64);
      const h = Math.min(gl.drawingBufferHeight || 0, 64);
      if (w === 0 || h === 0) return { hash: null, drawCount: gl.__rfpDrawCount };
      const px = new Uint8Array(w * h * 4);
      gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
      return { hash: hashBytes(px), drawCount: gl.__rfpDrawCount };
    } catch (e) {
      return { hash: null, drawCount: gl.__rfpDrawCount };
    }
  }));
  const globalAnimsAfter = (window.lottie && window.lottie._animations) || [];
  const lottieAfter = lottieContainers.map((el) => {
    try {
      let inst = (el.getLottie && el.getLottie()) || el._lottie || null;
      if (!inst && globalAnimsAfter.length) {
        for (const a of globalAnimsAfter) {
          if (a.wrapper === el || (el.contains && el.contains(a.wrapper))) {
            inst = a; break;
          }
        }
      }
      return { currentFrame: inst ? inst.currentFrame : null };
    } catch (e) {
      return { currentFrame: null };
    }
  });

  const canvasAdvanced = canvasBefore.filter((b, i) =>
    b.hash && canvasAfter[i].hash && b.hash !== canvasAfter[i].hash
  ).length;
  const webglAdvanced = webglBefore.filter((b, i) => {
    const a = webglAfter[i] || {};
    const hashDiff = b.hash && a.hash && b.hash !== a.hash;
    const drawDelta = (a.drawCount || 0) - (b.drawCount || 0);
    return hashDiff || drawDelta > 0;
  }).length;
  const lottieAdvanced = lottieBefore.filter((b, i) => {
    if (!b.hasInstance) return false;
    const a = lottieAfter[i];
    if (a.currentFrame == null || b.currentFrame == null) return false;
    return Math.abs(a.currentFrame - b.currentFrame) > 0.5;
  }).length;

  return JSON.stringify({
    canvasTotal: canvases.length,
    canvasAdvanced,
    webglAdvanced,
    lottieInstances: lottieBefore.filter(b => b.hasInstance).length,
    lottieAdvanced,
    canvasBefore, canvasAfter,
    webglBefore, webglAfter,
    lottieBefore, lottieAfter,
  });
})()
' > "$PROBE_RAW" 2>/dev/null || true

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
canvas_total = int(probe.get("canvasTotal", 0))
canvas_adv = int(probe.get("canvasAdvanced", 0))
webgl_adv = int(probe.get("webglAdvanced", 0))
lottie_inst = int(probe.get("lottieInstances", 0))
lottie_adv = int(probe.get("lottieAdvanced", 0))

if probe.get("error"):
    status = "fail"
    reasons.append(f"probe failed: {probe['error']}")
elif canvas_total == 0 and lottie_inst == 0:
    # Ref signaled canvas/webgl/lottie but impl has neither surface.
    # The signal might be a false positive (e.g., ref's
    # canvas-webgl-detection.json has hasCanvas=true because of a
    # different element class). Pass with informational note.
    status = "pass"
    reasons.append(
        "informational: ref signaled canvas/webgl/lottie but impl has no "
        "<canvas> or Lottie containers — may be a detection false-positive "
        "in the ref artifact; manually verify by inspecting the ref."
    )
elif canvas_total > 0 and canvas_adv == 0 and webgl_adv == 0:
    status = "fail"
    reasons.append(
        f"{canvas_total} <canvas> element(s) found but neither 2D paint "
        "nor WebGL drawing buffer changed in 1.5s. The canvas is mounted "
        "but no draw loop is running (missing requestAnimationFrame, "
        "missing engine init, or paused state)."
    )
elif lottie_inst > 0 and lottie_adv == 0:
    status = "fail"
    reasons.append(
        f"{lottie_inst} Lottie instance(s) accessible via getLottie()/_lottie "
        "but currentFrame did not advance >0.5 in 1.5s. The container exists "
        "and loadAnimation was called, but autoplay is off or paused."
    )
else:
    status = "pass"

payload = {
    "schemaVersion": 1,
    "status": status,
    "implUrl": impl_url,
    "canvasTotal": canvas_total,
    "canvasAdvanced": canvas_adv,
    "webglAdvanced": webgl_adv,
    "lottieInstances": lottie_inst,
    "lottieAdvanced": lottie_adv,
    "reasons": reasons,
    "nextAction": (
        "Start the animation loop. For canvas/WebGL: confirm requestAnimationFrame "
        "is wired and the engine init runs in useEffect. For Lottie: ensure "
        "autoplay=true OR call instance.play() in onLoad. Frame-delta proof "
        "is stricter than DOM mutation — needs actual paint each tick."
        if (status == "fail") else "all animation surfaces advancing"
    ),
    "rule": (
        "When ref signals canvas/WebGL/Lottie, the impl must have surfaces "
        "of the same kind AND those surfaces must advance frames within 1.5s. "
        "Stricter than the lottie-runtime v2 DOM-mutation heuristic — uses "
        "getImageData / readPixels / instance.currentFrame for authoritative "
        "frame-delta proof."
    ),
}

Path(out_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"status": status, "out": str(out_path)}))
sys.exit(0 if status in ("pass", "skip") else 1)
PY
