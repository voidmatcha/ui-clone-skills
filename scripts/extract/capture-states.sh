#!/usr/bin/env bash
# capture-states.sh — Phase A splash transition snapshots
#
# Captures DOM state transitions during page initial-load/splash so the impl
# can replicate the bridge between `is-loading` and `is-loaded`-style states
# instead of guessing from a single post-settled snapshot.
#
# Review follow-up (2026-05-25, docs/multi-snapshot-capture-design.md):
#   - Single `agent-browser eval` with in-page Promise loop, not 50 shell
#     evals @ 100ms (CLI round-trip cost + no latency guarantee).
#   - State-hash includes html/body class + scroll lock + full-screen overlay
#     presence + DOM length + computed-style fingerprint — class is one
#     signal, not THE signal.
#   - Compact deltas as default; full DOM only for 0ms / settled / structural
#     mutations above threshold (DOM length delta > 20%).
#   - Derived `${SESSION}-states` session to avoid race with parallel
#     capture.sh mutations on the same page.
#   - summary.json metadata distinguishes "static checked" from "capture
#     failed" from "legacy ref dir without states/".
#
# Usage:
#   capture-states.sh <url> <session> <ref_dir> [--reuse-session]
#
# By default opens its own derived session `${session}-states`. Pass
# `--reuse-session` to use the caller's session directly (only safe when
# capture-states.sh is called sequentially from capture.sh on a quiet
# session).
#
# Output:
#   <ref_dir>/states/splash/trajectory.json   — array of {ts_ms, hash, bodyClass, htmlClass, compositeDigest, full: bool}
#   <ref_dir>/states/splash/summary.json      — {checked, durationMs, polls, timedOut, reason}
#   <ref_dir>/states/splash/0ms.json          — full outerHTML at t=0
#   <ref_dir>/states/splash/settled.json      — full outerHTML at end-of-loop
#   <ref_dir>/states/splash/<NNN>ms.json      — full outerHTML when structural mutation > 20%
#
# Exit codes:
#   0  capture completed (transitions may be 0 — that's the "static page" case)
#   1  bad usage
#   2  agent-browser open failed
#   3  agent-browser eval returned unparseable / error response

set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "Usage: $0 <url> <session> <ref_dir> [--reuse-session]" >&2
  exit 1
fi

URL="$1"
SESSION="$2"
REF_DIR="$3"
REUSE_SESSION="false"
if [ "${4:-}" = "--reuse-session" ]; then
  REUSE_SESSION="true"
fi

STATES_SESSION="${SESSION}-states"
if [ "$REUSE_SESSION" = "true" ]; then
  STATES_SESSION="$SESSION"
fi
CAPTURE_MODE="pre-navigation"
if [ "$REUSE_SESSION" = "true" ]; then
  CAPTURE_MODE="reuse-session"
fi

OUTDIR="${REF_DIR}/${STATES_PREFIX:-states}/splash"
mkdir -p "$OUTDIR"
INIT_SCRIPT=""
RESPONSE_TMP=""
trap 'rm -f "${INIT_SCRIPT:-}" "${RESPONSE_TMP:-}"' EXIT

# In-page state-hash poller. Single eval — no CLI round-trip per poll.
# djb2 hash over a composite of: html/body class + scroll lock + full-screen
# overlay presence + DOM length + computed-style fingerprint of top-3
# above-the-fold elements.
# shellcheck disable=SC2016 # JavaScript template literals are intentionally shell-literal.
EVAL_JS='(async () => {
  const states = [];
  while (!document.documentElement || !document.body) {
    await new Promise((resolve) => requestAnimationFrame(resolve));
  }
  const startedAt = performance.now();
  let lastHash = null;
  let lastChangeAt = startedAt;

  const cssEscape = (value) => {
    const raw = String(value || "");
    if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(raw);
    return raw.replace(/[^a-zA-Z0-9_-]/g, (char) => `\\${char.codePointAt(0).toString(16)} `);
  };

  const cssString = (value) => String(value || "").replace(/\\/g, "\\\\").replace(/"/g, "\\\"");

  const nthOfTypePath = (el) => {
    const parts = [];
    let cur = el;
    while (
      cur &&
      cur.nodeType === Node.ELEMENT_NODE &&
      cur !== document.body &&
      cur !== document.documentElement
    ) {
      if (parts.length >= 8) return null;
      const tag = cur.localName;
      if (!tag || !cur.parentElement) break;
      const siblings = Array.from(cur.parentElement.children).filter((sibling) => (
        sibling.localName === tag
      ));
      parts.unshift(`${tag}:nth-of-type(${siblings.indexOf(cur) + 1})`);
      cur = cur.parentElement;
    }
    return parts.length ? `body > ${parts.join(" > ")}` : null;
  };

  const selectorFor = (el) => {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return null;
    const tag = el.localName || "element";
    if (el.id) return `#${cssEscape(el.id)}`;
    const classes = (el.getAttribute("class") || "").trim().split(/\s+/).filter(Boolean);
    if (classes.length) return `${tag}.${classes.slice(0, 3).map(cssEscape).join(".")}`;
    for (const attr of ["data-testid", "data-test", "data-cy", "aria-label", "name", "role"]) {
      const value = el.getAttribute(attr);
      if (value) return `${tag}[${attr}="${cssString(value)}"]`;
    }
    return nthOfTypePath(el);
  };

  const identityFor = (el) => {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return null;
    const tag = el.localName || "element";
    if (el.id) return `#${cssEscape(el.id)}`;
    for (const attr of ["data-testid", "data-test", "data-cy", "aria-label", "name", "role"]) {
      const value = el.getAttribute(attr);
      if (value) return `${tag}[${attr}="${cssString(value)}"]`;
    }
    return nthOfTypePath(el);
  };

  const detectFullScreenOverlay = () => {
    const vw = window.innerWidth, vh = window.innerHeight;
    const candidates = document.querySelectorAll("body *");
    let best = null;
    for (const el of candidates) {
      try {
        const r = el.getBoundingClientRect();
        const visibleWidth = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0));
        const visibleHeight = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
        const viewportCoverage = Math.min(1, (visibleWidth * visibleHeight) / Math.max(vw * vh, 1));
        const coversViewport = viewportCoverage >= 0.75;
        if (!coversViewport) continue;
        const cs = getComputedStyle(el);
        const z = parseInt(cs.zIndex || "0", 10) || 0;
        const opacity = Number.parseFloat(cs.opacity || "1");
        if (cs.position === "sticky") continue;
        if (
          (cs.position === "fixed" || (cs.position === "absolute" && z >= 10)) &&
          opacity > 0.05 &&
          cs.visibility !== "hidden" &&
          cs.display !== "none"
        ) {
          const candidate = {
            selector: selectorFor(el),
            identity: identityFor(el),
            coverage: Math.round(viewportCoverage * 1000) / 1000,
            visible: true,
            opacity: cs.opacity,
            position: cs.position,
            zIndex: z,
          };
          if (!best || candidate.coverage > best.coverage) best = candidate;
        }
      } catch (e) {}
    }
    return best || { selector: null, identity: null, coverage: 0, visible: false, opacity: "0" };
  };

  const animationEvidence = () => {
    const samples = [];
    let runningCount = 0;
    for (const animation of document.getAnimations()) {
      const target = animation.effect && animation.effect.target;
      const timing = animation.effect && animation.effect.getTiming ? animation.effect.getTiming() : {};
      if (animation.playState === "running") runningCount++;
      if (samples.length < 8) {
        samples.push({
          selector: selectorFor(target),
          playState: animation.playState,
          currentTime: Math.round(Number(animation.currentTime || 0)),
          duration: Number.isFinite(Number(timing.duration)) ? Number(timing.duration) : timing.duration,
          delay: Number(timing.delay || 0),
        });
      }
    }
    return { activeCount: document.getAnimations().length, runningCount, samples };
  };

  const mediaFingerprint = () => {
    const videos = Array.from(document.querySelectorAll("video, audio")).slice(0, 6).map((el) => ({
      selector: selectorFor(el),
      src: el.currentSrc || el.src || "",
      currentTime: Math.round(Number(el.currentTime || 0) * 1000) / 1000,
      paused: Boolean(el.paused),
      readyState: Number(el.readyState || 0),
    }));
    const raw = JSON.stringify(videos);
    return { videos, hash: String(cheapHash(raw)) };
  };

  const fingerprintTopElements = () => {
    const top = [];
    const all = document.body ? document.body.querySelectorAll("*") : [];
    let picked = 0;
    for (const el of all) {
      try {
        const r = el.getBoundingClientRect();
        if (r.top < window.innerHeight && r.bottom > 0 && r.width > 100 && r.height > 50) {
          const cs = getComputedStyle(el);
          top.push([cs.color, cs.opacity, cs.transform, cs.visibility, Math.round(r.top), Math.round(r.height)].join(":"));
          picked++;
          if (picked >= 3) break;
        }
      } catch (e) {}
    }
    return top.join("|");
  };

  const cheapHash = (str) => {
    let h = 5381;
    for (let i = 0; i < str.length; i++) h = ((h << 5) + h) + str.charCodeAt(i);
    return h >>> 0;
  };

  const computeState = () => {
    const html = document.documentElement;
    const body = document.body || { className: "", outerHTML: "" };
    const overlay = detectFullScreenOverlay();
    const animations = animationEvidence();
    const media = mediaFingerprint();
    const composite = [
      html.className || "",
      body.className || "",
      getComputedStyle(html).overflow,
      body.style ? getComputedStyle(body).overflow : "",
      JSON.stringify(overlay),
      animations.activeCount,
      animations.runningCount,
      media.hash,
      (body.outerHTML || "").length,
      fingerprintTopElements(),
    ].join("|");
    return {
      hash: cheapHash(composite),
      compositeDigest: composite.slice(0, 200),
      bodyClass: body.className || "",
      htmlClass: html.className || "",
      domLength: (body.outerHTML || "").length,
      overlay,
      animationEvidence: animations,
      motionEvidence: {
        changed: false,
        signals: [
          overlay.visible ? "fullscreen-overlay" : "",
          animations.activeCount > 0 ? "active-animation" : "",
          media.videos.length > 0 ? "media" : "",
        ].filter(Boolean),
      },
      mediaFingerprint: media,
      fullHTML: document.documentElement.outerHTML,
    };
  };

  // Initial state @ t=0
  const initial = computeState();
  states.push({
    ts_ms: 0,
    hash: initial.hash,
    bodyClass: initial.bodyClass,
    htmlClass: initial.htmlClass,
    compositeDigest: initial.compositeDigest,
    domLength: initial.domLength,
    overlay: initial.overlay,
    animationEvidence: initial.animationEvidence,
    motionEvidence: initial.motionEvidence,
    mediaFingerprint: initial.mediaFingerprint,
    fullHTML: initial.fullHTML,  // always full for 0ms bookend
    bookend: "0ms",
  });
  lastHash = initial.hash;
  let baselineDomLength = initial.domLength;

  while ((performance.now() - startedAt) < 5000) {
    await new Promise(r => requestAnimationFrame(() => setTimeout(r, 100)));
    const cur = computeState();
    const now = performance.now();
    if (cur.hash !== lastHash) {
      const structuralDelta = Math.abs(cur.domLength - baselineDomLength) / Math.max(baselineDomLength, 1);
      const includeFullHTML = structuralDelta > 0.2;  // >20% delta
      states.push({
        ts_ms: Math.round(now - startedAt),
        hash: cur.hash,
        bodyClass: cur.bodyClass,
        htmlClass: cur.htmlClass,
        compositeDigest: cur.compositeDigest,
        domLength: cur.domLength,
        overlay: cur.overlay,
        animationEvidence: cur.animationEvidence,
        motionEvidence: {
          changed: true,
          signals: cur.motionEvidence.signals,
        },
        mediaFingerprint: cur.mediaFingerprint,
        fullHTML: includeFullHTML ? cur.fullHTML : null,
        structuralDelta: includeFullHTML,
      });
      lastHash = cur.hash;
      lastChangeAt = now;
      if (includeFullHTML) baselineDomLength = cur.domLength;
    } else if ((now - lastChangeAt) >= 2000) {
      break;
    }
  }

  // Settled state — always full
  const final = computeState();
  const finalEntry = {
    ts_ms: Math.round(performance.now() - startedAt),
    hash: final.hash,
    bodyClass: final.bodyClass,
    htmlClass: final.htmlClass,
    compositeDigest: final.compositeDigest,
    domLength: final.domLength,
    overlay: final.overlay,
    animationEvidence: final.animationEvidence,
    motionEvidence: {
      changed: states.length > 1,
      signals: final.motionEvidence.signals,
    },
    mediaFingerprint: final.mediaFingerprint,
    fullHTML: final.fullHTML,  // always full for settled bookend
    bookend: "settled",
  };
  // If settled state hash matches the last recorded state, tag it as the
  // settled bookend AND ensure fullHTML is present so the python writer
  // emits settled.json. The earlier transition pass may have pruned
  // fullHTML when structuralDelta was <= 20% — without this backfill the
  // settled snapshot would silently go missing for CSS-only transitions
  // (display:none swap, opacity fade, class flip without DOM growth).
  if (states[states.length - 1] && states[states.length - 1].hash === final.hash) {
    const last = states[states.length - 1];
    last.bookend = last.bookend || "settled-same";
    if (!last.fullHTML) last.fullHTML = final.fullHTML;
  } else {
    states.push(finalEntry);
  }

  const elapsed = performance.now() - startedAt;
  return {
    states,
    durationMs: Math.round(elapsed),
    polls: states.length,
    timedOut: elapsed >= 5000,
    reason: states.length <= 1 ? "no-change" :
            elapsed >= 5000 ? "wall-clock-cap" :
            "stable-2s",
  };
})();'

RUN_EVAL_JS="$EVAL_JS"

# Open page in the derived session unless reusing the caller's session. The
# default path installs the sampler before navigation so short first-load
# splashes cannot complete during a post-open settle delay.
if [ "$REUSE_SESSION" = "false" ]; then
  INIT_SCRIPT="$(mktemp -t capture-states-init.XXXX.js)"
  printf '%s\n' "window.__UI_CLONE_SPLASH_CAPTURE__ = $EVAL_JS" > "$INIT_SCRIPT"
  if ! agent-browser --session "$STATES_SESSION" --init-script "$INIT_SCRIPT" open "$URL" >/dev/null 2>&1; then
    echo "capture-states: agent-browser open failed for $URL (session=$STATES_SESSION)" >&2
    exit 2
  fi
  RUN_EVAL_JS='(async () => await window.__UI_CLONE_SPLASH_CAPTURE__)()'
fi

RESPONSE_RAW="$(agent-browser --session "$STATES_SESSION" eval --json "$RUN_EVAL_JS" 2>&1)" || {
  echo "capture-states: agent-browser eval failed (session=$STATES_SESSION)" >&2
  echo "$RESPONSE_RAW" >&2
  exit 3
}

# Validate + split into trajectory / summary / per-state files via python.
# Heredoc + stdin pipe conflict — write response to a temp file the python
# block reads via argv. Also handles multi-MB DOM blobs that would exceed
# env-var size limits.
RESPONSE_TMP="$(mktemp -t capture-states-resp.XXXX)"
printf '%s' "$RESPONSE_RAW" > "$RESPONSE_TMP"
python3 - "$OUTDIR" "$RESPONSE_TMP" "$CAPTURE_MODE" <<'PY'
import json
import sys
from pathlib import Path

outdir = Path(sys.argv[1])
raw = Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace")
capture_mode = sys.argv[3]

# agent-browser may wrap the eval result in a JSON envelope; try both.
try:
    parsed = json.loads(raw)
except json.JSONDecodeError as e:
    print(f"capture-states: invalid JSON from agent-browser eval ({e}):\n{raw[:300]}", file=sys.stderr)
    sys.exit(3)

# Peel agent-browser eval envelope: {success, data: {origin, result: <inner>}}.
# Real `agent-browser eval --json` always wraps. Unit-test fake-browser emits
# the inner JSON bare, so this peel is a no-op there.
if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict) and "result" in parsed["data"]:
    parsed = parsed["data"]["result"]
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            pass

# Legacy single-key wrapper {"result": <inner>}. Kept so a future shim that
# pre-strips the envelope on the caller side keeps working without script edits.
if isinstance(parsed, dict) and "result" in parsed and isinstance(parsed["result"], (dict, str)):
    inner = parsed["result"]
    if isinstance(inner, str):
        try:
            parsed = json.loads(inner)
        except json.JSONDecodeError:
            pass
    else:
        parsed = inner

if not isinstance(parsed, dict) or "states" not in parsed:
    print(f"capture-states: unexpected payload shape:\n{json.dumps(parsed)[:300]}", file=sys.stderr)
    sys.exit(3)

states = parsed.get("states", [])
summary = {
    "checked": True,
    "captureMode": capture_mode,
    "durationMs": parsed.get("durationMs", 0),
    "polls": parsed.get("polls", len(states)),
    "timedOut": parsed.get("timedOut", False),
    "reason": parsed.get("reason", "unknown"),
    "schemaVersion": 1,
}

# Trajectory entries — drop fullHTML from the trajectory.json (kept separately
# in per-state files so the trajectory stays human-readable).
trajectory = []
for s in states:
    entry = {k: v for k, v in s.items() if k != "fullHTML"}
    trajectory.append(entry)

(outdir / "trajectory.json").write_text(
    json.dumps(trajectory, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
(outdir / "summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

# Per-state full DOM snapshots: only for entries carrying fullHTML.
for s in states:
    html = s.get("fullHTML")
    if not html:
        continue
    ts = s.get("ts_ms", 0)
    bookend = s.get("bookend")
    if bookend == "0ms":
        filename = "0ms.json"
    elif bookend in ("settled", "settled-same"):
        filename = "settled.json"
    else:
        filename = f"{ts}ms.json"
    (outdir / filename).write_text(
        json.dumps({"ts_ms": ts, "outerHTML": html}, ensure_ascii=False),
        encoding="utf-8",
    )

def _num(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _unique(values):
    seen = set()
    out = []
    for value in values:
        if value in (None, "") or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _overlay_identity(overlay):
    if not isinstance(overlay, dict):
        return None
    return overlay.get("identity") or overlay.get("selector")


def _splash_contract(states, capture_mode, summary):
    first = states[0] if states else {}
    last = states[-1] if states else {}
    overlay_observations = [
        (index, state, state.get("overlay"))
        for index, state in enumerate(states)
        if isinstance(state.get("overlay"), dict)
    ]
    visible_observations = [
        observation
        for observation in overlay_observations
        if observation[2].get("visible") and _num(observation[2].get("coverage")) > 0
    ]
    first_visible = visible_observations[0] if visible_observations else None
    primary_identity = _overlay_identity(first_visible[2]) if first_visible else None
    primary_visible = [
        observation
        for observation in visible_observations
        if _overlay_identity(observation[2]) == primary_identity
    ]
    max_overlay = max(
        (observation[2] for observation in primary_visible),
        key=lambda overlay: _num(overlay.get("coverage")),
        default={},
    )
    exit_observation = None
    if first_visible:
        for observation in overlay_observations:
            index, _, overlay = observation
            if index <= first_visible[0]:
                continue
            if not overlay.get("visible") or _overlay_identity(overlay) != primary_identity:
                exit_observation = observation
                break
    primary_phases = {
        json.dumps(
            {
                "coverage": observation[2].get("coverage"),
                "opacity": observation[2].get("opacity"),
                "position": observation[2].get("position"),
                "zIndex": observation[2].get("zIndex"),
            },
            sort_keys=True,
        )
        for observation in primary_visible
    }
    overlay_phase_changed = len(primary_phases) > 1
    animations = [
        s.get("animationEvidence")
        for s in states
        if isinstance(s.get("animationEvidence"), dict)
    ]
    max_active_count = max((_num(a.get("activeCount")) for a in animations), default=0)
    max_running_count = max((_num(a.get("runningCount")) for a in animations), default=0)
    animation_samples = []
    for evidence in animations:
        samples = evidence.get("samples")
        if isinstance(samples, list):
            animation_samples.extend(sample for sample in samples if isinstance(sample, dict))
    media_entries = [
        s.get("mediaFingerprint")
        for s in states
        if isinstance(s.get("mediaFingerprint"), dict)
    ]
    media_hashes = _unique(m.get("hash") for m in media_entries)
    motion_signals = []
    for state in states:
        evidence = state.get("motionEvidence")
        if isinstance(evidence, dict) and isinstance(evidence.get("signals"), list):
            motion_signals.extend(str(signal) for signal in evidence["signals"] if signal)
    from_ms = first_visible[1].get("ts_ms") if first_visible else None
    to_ms = exit_observation[1].get("ts_ms") if exit_observation else None
    duration_ms = (
        int(to_ms) - int(from_ms)
        if isinstance(from_ms, int) and isinstance(to_ms, int) and to_ms >= from_ms
        else None
    )
    detected = bool(len(states) > 1 and first_visible and exit_observation)
    timed_out = bool(summary.get("timedOut"))
    reason = summary.get("reason")
    authoritative_negative = bool(
        capture_mode == "pre-navigation"
        and not detected
        and not first_visible
        and len(states) == 1
        and not timed_out
    )
    return {
        "schemaVersion": 1,
        "captureMode": capture_mode,
        "detected": detected,
        "overlay": {
            "selector": max_overlay.get("selector"),
            "identity": _overlay_identity(max_overlay),
            "maxCoverage": _num(max_overlay.get("coverage")),
            "everVisible": first_visible is not None,
            "exitObserved": exit_observation is not None,
            "phaseChanged": overlay_phase_changed,
            "initial": first.get("overlay") if isinstance(first.get("overlay"), dict) else {},
            "settled": last.get("overlay") if isinstance(last.get("overlay"), dict) else {},
        },
        "capture": {
            "stateCount": len(states),
            "timedOut": timed_out,
            "reason": reason,
            "authoritativeNegative": authoritative_negative,
        },
        "activeAnimation": {
            "maxActiveCount": int(max_active_count),
            "maxRunningCount": int(max_running_count),
            "samples": animation_samples[:12],
        },
        "motionEvidence": {
            "changed": bool(detected or (first.get("hash") != last.get("hash"))),
            "signals": _unique(motion_signals),
        },
        "mediaFingerprint": {
            "hashes": media_hashes,
            "initial": first.get("mediaFingerprint") if isinstance(first.get("mediaFingerprint"), dict) else {},
            "settled": last.get("mediaFingerprint") if isinstance(last.get("mediaFingerprint"), dict) else {},
        },
        "exitTiming": {
            "fromMs": from_ms,
            "toMs": to_ms,
            "durationMs": duration_ms,
            "source": "states/splash/trajectory.json",
        },
        "bookends": [
            "states/splash/0ms.json",
            "states/splash/settled.json",
        ],
    }


(outdir / "contract.json").write_text(
    json.dumps(_splash_contract(states, capture_mode, summary), ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print(f"capture-states: wrote {len(trajectory)} transition(s) to {outdir}/", file=sys.stderr)
PY

SPEC_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/state-structure-spec.py"
if [ "${STATE_STRUCTURE_SPEC:-1}" != "0" ] && [ -f "$SPEC_PY" ]; then
  python3 "$SPEC_PY" "$REF_DIR" >/dev/null
fi
