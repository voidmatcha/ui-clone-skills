#!/usr/bin/env bash
# capture-states.sh — Phase A splash transition snapshots
#
# Captures DOM state transitions during page initial-load/splash so the impl
# can replicate the bridge between `is-loading` and `is-loaded`-style states
# instead of guessing from a single post-settled snapshot.
#
# Codex review (2026-05-25, docs/multi-snapshot-capture-design.md):
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

OUTDIR="$REF_DIR/states/splash"
mkdir -p "$OUTDIR"

# Open page in the derived session unless reusing the caller's session.
if [ "$REUSE_SESSION" = "false" ]; then
  if ! agent-browser --session "$STATES_SESSION" open "$URL" --wait 1500 >/dev/null 2>&1; then
    echo "capture-states: agent-browser open failed for $URL (session=$STATES_SESSION)" >&2
    exit 2
  fi
fi

# In-page state-hash poller. Single eval — no CLI round-trip per poll.
# djb2 hash over a composite of: html/body class + scroll lock + full-screen
# overlay presence + DOM length + computed-style fingerprint of top-3
# above-the-fold elements.
EVAL_JS='(async () => {
  const states = [];
  const startedAt = performance.now();
  let lastHash = null;
  let lastChangeAt = startedAt;

  const detectFullScreenOverlay = () => {
    const vw = window.innerWidth, vh = window.innerHeight;
    const candidates = document.querySelectorAll("body *");
    let count = 0;
    for (const el of candidates) {
      try {
        const r = el.getBoundingClientRect();
        if (r.width >= vw * 0.95 && r.height >= vh * 0.95) {
          const cs = getComputedStyle(el);
          if (cs.position === "fixed" || cs.position === "absolute") {
            const z = parseInt(cs.zIndex || "0", 10) || 0;
            if (z > 10 && cs.opacity !== "0" && cs.visibility !== "hidden" && cs.display !== "none") {
              count++;
            }
          }
        }
      } catch (e) {}
      if (count > 0) break;
    }
    return count;
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
    const composite = [
      html.className || "",
      body.className || "",
      getComputedStyle(html).overflow,
      body.style ? getComputedStyle(body).overflow : "",
      detectFullScreenOverlay(),
      (body.outerHTML || "").length,
      fingerprintTopElements(),
    ].join("|");
    return {
      hash: cheapHash(composite),
      compositeDigest: composite.slice(0, 200),
      bodyClass: body.className || "",
      htmlClass: html.className || "",
      domLength: (body.outerHTML || "").length,
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

RESPONSE_RAW="$(agent-browser --session "$STATES_SESSION" eval --json "$EVAL_JS" 2>&1)" || {
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
trap 'rm -f "$RESPONSE_TMP"' EXIT
python3 - "$OUTDIR" "$RESPONSE_TMP" <<'PY'
import json
import sys
from pathlib import Path

outdir = Path(sys.argv[1])
raw = Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace")

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

print(f"capture-states: wrote {len(trajectory)} transition(s) to {outdir}/", file=sys.stderr)
PY
