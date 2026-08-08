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
REF_NEEDS="$(python3 - "$REF_DIR" <<'PY'
import json
import re
import sys
from pathlib import Path
from typing import Any

ref_dir = Path(sys.argv[1])
markers = re.compile(r"(?:lottie|bodymovin|dotlottie|<canvas)", re.IGNORECASE)


def load(name: str) -> Any:
    try:
        return json.loads((ref_dir / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def present(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value > 0
    if isinstance(value, str):
        return value.strip().lower() not in {"", "none", "false", "0"}
    if isinstance(value, (list, dict)):
        return bool(value)
    return False


def contains_signal(value: Any) -> bool:
    if isinstance(value, str):
        return bool(markers.search(value))
    if isinstance(value, list):
        return any(contains_signal(item) for item in value)
    if not isinstance(value, dict):
        return False
    for key, item in value.items():
        if markers.search(str(key)) and present(item):
            return True
        if contains_signal(item):
            return True
    return False


canvas = load("canvas-webgl-detection.json")
if isinstance(canvas, dict):
    try:
        canvas_count = int(canvas.get("canvasCount", 0) or 0)
    except (TypeError, ValueError):
        canvas_count = 0
    if (
        canvas.get("hasCanvas") is True
        or canvas.get("hasWebGL") is True
        or canvas_count > 0
        or str(canvas.get("primaryRenderType", "")).strip().lower()
        in {"canvas", "webgl"}
    ):
        print("true")
        raise SystemExit(0)

required_media = load("required-media.json")
if isinstance(required_media, dict):
    lottie = required_media.get("lottie")
    totals = required_media.get("totals")
    try:
        lottie_total = int(totals.get("lottie", 0) or 0) if isinstance(totals, dict) else 0
    except (TypeError, ValueError):
        lottie_total = 0
    if (isinstance(lottie, list) and bool(lottie)) or lottie_total > 0:
        print("true")
        raise SystemExit(0)

for name in ("animations-detected.json", "transition-spec.json"):
    if contains_signal(load(name)):
        print("true")
        raise SystemExit(0)

print("false")
PY
)"

# Video-only refs: when the ref's motion surface is a promoted <video>
# (required-media.json), the frame proof must run and verify the impl's
# video advances — instead of skipping and leaving runtime-proof blind.
if [ "$REF_NEEDS" != "true" ] && [ -f "$REF_DIR/required-media.json" ]; then
  if python3 -c "
import json, sys
d = json.load(open('$REF_DIR/required-media.json'))
sys.exit(0 if (isinstance(d, dict) and d.get('videos')) else 1)
" 2>/dev/null; then
    REF_NEEDS="true"
  fi
fi

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

agent-browser --session "$PROBE_SESSION" open "$IMPL_URL" >/dev/null 2>&1 || true
sleep 2  # open --wait is not a supported flag; settle explicitly

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
  const sample2DRegions = (ctx, c) => {
    const stripH = Math.min(c.height, 64);
    const stripW = Math.min(c.width, 64);
    const ys = [0, Math.max(0, Math.floor((c.height - stripH) / 2)), Math.max(0, c.height - stripH)];
    const xs = [0, Math.max(0, Math.floor((c.width - stripW) / 2)), Math.max(0, c.width - stripW)];
    const hashes = [];
    for (const y of [...new Set(ys)]) {
      hashes.push(hashBytes(ctx.getImageData(0, y, c.width, stripH).data));
    }
    for (const x of [...new Set(xs)]) {
      hashes.push(hashBytes(ctx.getImageData(x, 0, stripW, c.height).data));
    }
    return hashes.join(":");
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
      return { hash: sample2DRegions(ctx, c), width: c.width, height: c.height, kind: "2d", tainted: false };
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

  // video sampling — canvas-replay fallback surface. A declared
  // canvas-replay video whose currentTime advances renders the reference
  // OWN recorded motion, so a 0-canvas hero is NOT blank.
  const videos = Array.from(document.querySelectorAll("video")).filter(visible);
  const sampleVideo = (v) => {
    const r = v.getBoundingClientRect();
    return {
      currentTime: v.currentTime || 0,
      paused: !!v.paused,
      readyState: v.readyState || 0,
      w: Math.round(r.width),
      h: Math.round(r.height),
    };
  };
  const videoBefore = videos.map(sampleVideo);

  if (lottieBefore.some((sample) => sample.hasInstance)) {
    const maxScrollForLottie = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
    if (maxScrollForLottie > 0) {
      const targetY = Math.max(1, Math.floor(maxScrollForLottie * 0.92));
      window.scrollTo(0, targetY);
      window.dispatchEvent(new Event("scroll"));
      await new Promise(r => setTimeout(r, 260));
    }
  }

  await new Promise(r => setTimeout(r, 1500));

  const videoAfter = videos.map(sampleVideo);
  const videoAdvanced = videoBefore.filter((b, i) => {
    const a = videoAfter[i] || {};
    return (a.currentTime || 0) - (b.currentTime || 0) > 0.05 && a.w > 10 && a.h > 10;
  }).length;

  const canvasAfter = canvases.map((c, i) => {
    try {
      const ctx = c.getContext("2d", { willReadFrequently: true });
      if (!ctx) return { hash: null };
      return { hash: sample2DRegions(ctx, c) };
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
    videoTotal: videos.length,
    videoAdvanced,
    canvasBefore, canvasAfter,
    webglBefore, webglAfter,
    lottieBefore, lottieAfter,
    videoBefore, videoAfter,
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
        # agent-browser may wrap the eval result in outer quotes
        # ("{...}") or emit a bare object ({...}). Accept both forms.
        if not (s.startswith("{") or s.startswith('"{')):
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
video_total = int(probe.get("videoTotal", 0))
video_adv = int(probe.get("videoAdvanced", 0))
video_frame_proof_kind = ""


def _load_replay_plan() -> dict | None:
    """canvas-replay-plan.json from the ref dir (sibling of out_path)."""
    p = Path(out_path).parent / "canvas-replay-plan.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _replay_satisfies_blank_hero(plan, video_advanced) -> bool:
    """Mirror of ui_clone.policies.canvas_replay_auto.replay_satisfies_blank_hero
    (inlined so the gate has no import dependency on the package path)."""
    if not isinstance(plan, dict) or plan.get("decision") != "canvas-replay":
        return False
    return int(video_advanced or 0) > 0

def ref_has_real_canvas() -> bool:
    """True only when the ref artifact carries genuine canvas/WebGL evidence:
    canvas-webgl-detection.json with canvasCount>0 or a canvas/webgl
    primaryRenderType. Gating the fail on this keeps a genuinely canvas-less
    ref (signal came from a lottie keyword, etc.) on the informational path.
    """
    detect = Path(out_path).parent / "canvas-webgl-detection.json"
    try:
        data = json.loads(detect.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    try:
        if int(data.get("canvasCount", 0)) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return str(data.get("primaryRenderType", "")).strip().lower() in {"webgl", "canvas"}

def ref_video_is_motion_surface() -> bool:
    """True when the ref's own evidence names <video> as a motion surface:
    required-media.json carries promoted video entries, or a video-play
    proof passed. Gives plain video-hero refs (no canvas/lottie anywhere)
    a legitimate validity path instead of the unreachable informational
    branch + rollup invalidation ("pass but no animation surface")."""
    ref_dir = Path(out_path).parent
    try:
        rm = json.loads((ref_dir / "required-media.json").read_text(encoding="utf-8"))
        if isinstance(rm, dict) and isinstance(rm.get("videos"), list) and rm["videos"]:
            return True
    except Exception:
        pass
    try:
        vp = json.loads((ref_dir / "video-play-proof.json").read_text(encoding="utf-8"))
        if isinstance(vp, dict) and str(vp.get("status", "")).lower() == "pass":
            return True
    except Exception:
        pass
    return False


if probe.get("error"):
    status = "fail"
    reasons.append(f"probe failed: {probe['error']}")
elif (
    canvas_total == 0
    and lottie_inst == 0
    and ref_has_real_canvas()
    and _replay_satisfies_blank_hero(_load_replay_plan(), video_adv)
):
    video_frame_proof_kind = "canvas-replay-video"
    # Ref renders WebGL/canvas and the impl mounts 0 canvases, BUT a declared
    # canvas-replay <video> is advancing frames — the hero now renders the
    # ref's OWN recorded motion (origin-locked engine could not be re-embedded,
    # so it was recorded and replayed). Non-blank, declared, faithful → pass.
    status = "pass"
    reasons.append(
        f"canvas-replay: impl mounts 0 canvases but a declared <video> replay "
        f"is advancing ({video_adv}/{video_total} videos). The hero renders "
        "the reference's own recorded motion (canvas-replay-plan.json) — "
        "non-blank and faithful, so the blank-hero fail is satisfied."
    )
elif canvas_total == 0 and lottie_inst == 0 and ref_has_real_canvas():
    # Ref genuinely renders WebGL/canvas (canvas-webgl-detection.json says so)
    # but the impl mounts zero canvases — a blank hero that escaped detection.
    # This is a real failure, not a false-positive: fail loudly.
    status = "fail"
    reasons.append(
        "ref renders WebGL/canvas but impl hero is blank (0 canvases). "
        "canvas-webgl-detection.json shows the reference draws on a canvas/"
        "WebGL surface, yet the impl mounted none — the hero is not "
        "reproduced (origin-locked engine, missing mount, or init failure)."
    )
elif canvas_total == 0 and lottie_inst == 0 and ref_video_is_motion_surface():
    # Plain video-hero ref: no canvas/lottie anywhere, but the ref's own
    # evidence (required-media.json / video-play proof) names <video> as the
    # motion surface. Count an advancing impl <video> as the animation
    # surface so video-only sites have a legitimate runtime-proof path.
    if video_total > 0 and video_adv > 0:
        video_frame_proof_kind = "video-surface"
        status = "pass"
        reasons.append(
            f"video-surface: the ref's motion surface is <video> and the "
            f"impl's video is advancing ({video_adv}/{video_total}) — "
            "counted as the animation surface."
        )
    elif video_total > 0:
        status = "fail"
        reasons.append(
            f"ref's motion surface is <video> but none of the impl's "
            f"{video_total} video(s) advanced frames in the probe window "
            "(autoplay blocked, paused, or React muted-attr hydration bug)."
        )
    else:
        status = "fail"
        reasons.append(
            "ref's motion surface is <video> (required-media.json / "
            "video-play proof) but the impl mounts no <video> element."
        )
elif canvas_total == 0 and lottie_inst == 0:
    # Ref signaled canvas/webgl/lottie but impl has neither surface, and the
    # ref has no genuine canvas/WebGL evidence (the signal might be a lottie
    # keyword or a stray "canvas" string). Pass with informational note.
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
    "videoTotal": video_total,
    "videoAdvanced": video_adv,
    "videoFrameProofKind": video_frame_proof_kind,
    "videoCountsAsAnimationSurface": video_frame_proof_kind in ("canvas-replay-video", "video-surface"),
    "reasons": reasons,
    "nextAction": (
        "Start the animation loop. For canvas/WebGL: confirm requestAnimationFrame "
        "is wired and the engine init runs in useEffect. For Lottie: ensure "
        "autoplay/play() advances autonomous animations, or expose the instance "
        "and update currentFrame when this probe drives scroll for scroll-scrubbed refs."
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
