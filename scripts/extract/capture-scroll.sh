#!/usr/bin/env bash
# capture-scroll.sh — Phase B scroll-progress snapshots
#
# Captures DOM state at fixed scroll percentages [0, 10, 25, 50, 75, 90, 100]
# so the impl can replicate scroll-driven state (sticky-navbar shrink,
# parallax position, IntersectionObserver-mounted sections, scroll-triggered
# reveals) instead of guessing from a single top-of-page snapshot.
#
# Design: docs/multi-snapshot-capture-design.md § Phase B. Pattern mirrors
# capture-states.sh (Phase A) — single in-page Promise loop with state
# capture across all 7 stops, no shell-side per-stop eval round-trips.
#
# Usage:
#   capture-scroll.sh <url> <session> <ref_dir> [--reuse-session]
#
# By default opens its own derived session `${session}-scroll`. Pass
# `--reuse-session` to use the caller's session directly (only safe when
# capture-scroll.sh is called sequentially from capture.sh on a quiet
# session, typically after capture-states.sh has settled the page).
#
# Output:
#   <ref_dir>/states/scroll/0pct.json … 100pct.json   — per-pct full DOM + visible-section index
#   <ref_dir>/states/scroll/trajectory.json           — compact per-pct entries (no outerHTML)
#   <ref_dir>/states/scroll/summary.json              — {checked, durationMs, scrollHeight, viewportHeight,
#                                                       finalScrollHeight, infiniteScroll, static, schemaVersion}
#
# Exit codes:
#   0  capture completed (may be static — single 0pct snapshot for short pages)
#   1  bad usage
#   2  agent-browser open failed
#   3  agent-browser eval returned unparseable / unexpected-shape response

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

SCROLL_SESSION="${SESSION}-scroll"
if [ "$REUSE_SESSION" = "true" ]; then
  SCROLL_SESSION="$SESSION"
fi

OUTDIR="$REF_DIR/states/scroll"
mkdir -p "$OUTDIR"

# Open page in the derived session unless reusing the caller's session.
if [ "$REUSE_SESSION" = "false" ]; then
  if ! agent-browser --session "$SCROLL_SESSION" open "$URL" --wait 1500 >/dev/null 2>&1; then
    echo "capture-scroll: agent-browser open failed for $URL (session=$SCROLL_SESSION)" >&2
    exit 2
  fi
fi

# In-page scroll sweep. Single eval — no CLI round-trip per stop.
#
# Codex review (2026-05-25):
#   (a) Detect Lenis/Locomotive wrapper scroll — `window.scrollY` may not
#       reflect engine-driven scroll position. Use engine API when present.
#   (b) Stability loop per stop — 500ms floor + up to 3 × 200ms hash-stable
#       polls (max ~1.1s per stop). Pure-fixed-wait pattern is brittle for
#       IO reveals + GSAP scrub on slow machines.
#   (d) `infiniteScroll` threshold raised 1.1 → 1.5 (lazy-loaded sections
#       easily grow 10-15% without being infinite feeds). Always expose
#       `scrollHeightDeltaPct` as numeric for gate consumers to threshold.
#   (g) Accepted risk: 7 stops × full outerHTML may be multi-MB. Phase A's
#       heredoc temp-file pattern handles this transport. State-coverage
#       gate needs per-pct DOM to detect "section X first visible at 75%".
EVAL_JS='(async () => {
  const PCTS = [0, 10, 25, 50, 75, 90, 100];
  const SETTLE_FLOOR_MS = 500;
  const STABILITY_POLL_MS = 200;
  const STABILITY_POLLS = 3;
  const startedAt = performance.now();

  const initialScrollHeight = document.documentElement.scrollHeight;
  const viewportHeight = window.innerHeight;
  const maxScrollable = Math.max(0, initialScrollHeight - viewportHeight);

  const cheapHash = (str) => {
    let h = 5381;
    for (let i = 0; i < str.length; i++) h = ((h << 5) + h) + str.charCodeAt(i);
    return h >>> 0;
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

  // Enumerate top-level section candidates and record those currently in viewport.
  const visibleSections = () => {
    const selectors = ["section", "[data-section]", "main > *", "[class*=\"section\"]", "header", "footer", "nav"];
    const out = [];
    const seen = new Set();
    for (const sel of selectors) {
      let nodes;
      try { nodes = document.querySelectorAll(sel); } catch (e) { continue; }
      for (const el of nodes) {
        if (seen.has(el)) continue;
        seen.add(el);
        try {
          const r = el.getBoundingClientRect();
          if (r.bottom > 0 && r.top < window.innerHeight && r.width > 50 && r.height > 50) {
            // Compose a stable selector signal: tag + id-or-class fragment + dataset section
            const id = el.id ? "#" + el.id : "";
            const cls = (el.className && typeof el.className === "string")
              ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : "";
            const dataSec = el.dataset && el.dataset.section ? "[data-section=" + el.dataset.section + "]" : "";
            out.push({
              selector: el.tagName.toLowerCase() + id + cls + dataSec,
              top: Math.round(r.top),
              height: Math.round(r.height),
            });
            if (out.length >= 30) break;
          }
        } catch (e) {}
      }
      if (out.length >= 30) break;
    }
    return out;
  };

  // Codex item (a): detect wrapper-scroll engines and use their API for scrolling.
  // Returns the engine name used and the actual scroll position read from the engine.
  const detectScrollEngine = () => {
    const lenis = window.lenis || (typeof window.Lenis === "object" ? window.Lenis : null);
    if (lenis && typeof lenis.scrollTo === "function") return { name: "lenis", instance: lenis };
    const loco = window.locomotive || window.locomotiveScroll
      || (window.scroll && typeof window.scroll.scrollTo === "function" ? window.scroll : null);
    if (loco && typeof loco.scrollTo === "function") return { name: "locomotive", instance: loco };
    return { name: "native", instance: null };
  };

  const scrollEngine = detectScrollEngine();

  const performScroll = (targetY) => {
    if (scrollEngine.name === "lenis") {
      try { scrollEngine.instance.scrollTo(targetY, { immediate: true, force: true }); return; }
      catch (e) {}
    }
    if (scrollEngine.name === "locomotive") {
      try { scrollEngine.instance.scrollTo(targetY, { duration: 0, disableLerp: true }); return; }
      catch (e) {}
    }
    try { window.scrollTo({ top: targetY, behavior: "instant" }); }
    catch (e) { window.scrollTo(0, targetY); }
  };

  const readScrollY = () => {
    if (scrollEngine.name === "lenis" && scrollEngine.instance) {
      const s = scrollEngine.instance.scroll;
      if (typeof s === "number") return Math.round(s);
    }
    if (scrollEngine.name === "locomotive" && scrollEngine.instance) {
      const s = scrollEngine.instance.scroll && scrollEngine.instance.scroll.instance
        ? scrollEngine.instance.scroll.instance.scroll : null;
      if (s && typeof s.y === "number") return Math.round(s.y);
    }
    return Math.round(window.scrollY);
  };

  // Codex item (b): per-stop stability loop after the 500ms floor.
  const stabilityWait = async () => {
    await new Promise(r => requestAnimationFrame(() => setTimeout(r, SETTLE_FLOOR_MS)));
    let lastHash = cheapHash(fingerprintTopElements());
    for (let i = 0; i < STABILITY_POLLS; i++) {
      await new Promise(r => setTimeout(r, STABILITY_POLL_MS));
      const cur = cheapHash(fingerprintTopElements());
      if (cur === lastHash) break;
      lastHash = cur;
    }
  };

  const captureAtPct = (pct) => ({
    pct,
    scrollY: readScrollY(),
    outerHTML: document.documentElement.outerHTML,
    visibleSections: visibleSections(),
    compositeDigest: fingerprintTopElements().slice(0, 200),
  });

  const stops = [];
  const isStatic = maxScrollable <= 0;

  if (isStatic) {
    // Page fits in viewport — emit only pct=0 snapshot.
    stops.push(captureAtPct(0));
  } else {
    for (const pct of PCTS) {
      const targetY = Math.round(maxScrollable * pct / 100);
      performScroll(targetY);
      await stabilityWait();
      stops.push(captureAtPct(pct));
    }
  }

  const finalScrollHeight = document.documentElement.scrollHeight;
  // Codex item (d): looser threshold + always expose delta percentage.
  const scrollHeightDeltaPct = initialScrollHeight > 0
    ? Math.round(((finalScrollHeight - initialScrollHeight) / initialScrollHeight) * 100)
    : 0;
  const scrollHeightGrew = !isStatic && finalScrollHeight > initialScrollHeight;
  const infiniteScroll = !isStatic && finalScrollHeight > initialScrollHeight * 1.5;

  return {
    stops,
    durationMs: Math.round(performance.now() - startedAt),
    scrollHeight: initialScrollHeight,
    viewportHeight,
    finalScrollHeight,
    scrollHeightDeltaPct,
    scrollHeightGrew,
    infiniteScroll,
    scrollEngine: scrollEngine.name,
    static: isStatic,
  };
})();'

RESPONSE_RAW="$(agent-browser --session "$SCROLL_SESSION" eval --json "$EVAL_JS" 2>&1)" || {
  echo "capture-scroll: agent-browser eval failed (session=$SCROLL_SESSION)" >&2
  echo "$RESPONSE_RAW" >&2
  exit 3
}

# Validate + split into trajectory / summary / per-pct files via python.
# Heredoc + stdin pipe conflict — write response to a temp file the python
# block reads via argv. Also handles multi-MB DOM blobs (7 stops × ~500KB).
RESPONSE_TMP="$(mktemp -t capture-scroll-resp.XXXX)"
printf '%s' "$RESPONSE_RAW" > "$RESPONSE_TMP"
trap 'rm -f "$RESPONSE_TMP"' EXIT
python3 - "$OUTDIR" "$RESPONSE_TMP" <<'PY'
import json
import sys
from pathlib import Path

outdir = Path(sys.argv[1])
raw = Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace")

try:
    parsed = json.loads(raw)
except json.JSONDecodeError as e:
    print(f"capture-scroll: invalid JSON from eval ({e}):\n{raw[:300]}", file=sys.stderr)
    sys.exit(3)

# agent-browser may wrap the eval result in a JSON envelope; drill if so.
if isinstance(parsed, dict) and "result" in parsed and isinstance(parsed["result"], (dict, str)):
    inner = parsed["result"]
    if isinstance(inner, str):
        try:
            parsed = json.loads(inner)
        except json.JSONDecodeError:
            pass
    else:
        parsed = inner

if not isinstance(parsed, dict) or "stops" not in parsed:
    print(f"capture-scroll: unexpected payload shape:\n{json.dumps(parsed)[:300]}", file=sys.stderr)
    sys.exit(3)

stops = parsed.get("stops", [])
summary = {
    "checked": True,
    "durationMs": parsed.get("durationMs", 0),
    "scrollHeight": parsed.get("scrollHeight", 0),
    "viewportHeight": parsed.get("viewportHeight", 0),
    "finalScrollHeight": parsed.get("finalScrollHeight", parsed.get("scrollHeight", 0)),
    "scrollHeightDeltaPct": parsed.get("scrollHeightDeltaPct", 0),
    "scrollHeightGrew": parsed.get("scrollHeightGrew", False),
    "infiniteScroll": parsed.get("infiniteScroll", False),
    "scrollEngine": parsed.get("scrollEngine", "native"),
    "static": parsed.get("static", False),
    "schemaVersion": 1,
}

# Trajectory entries — drop outerHTML; keep pct + scrollY + visibleSections + digest.
trajectory = []
for s in stops:
    entry = {k: v for k, v in s.items() if k != "outerHTML"}
    trajectory.append(entry)

(outdir / "trajectory.json").write_text(
    json.dumps(trajectory, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
(outdir / "summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

# Per-pct full DOM snapshots.
for s in stops:
    pct = s.get("pct")
    html = s.get("outerHTML")
    if pct is None or not html:
        continue
    (outdir / f"{pct}pct.json").write_text(
        json.dumps({
            "pct": pct,
            "scrollY": s.get("scrollY", 0),
            "outerHTML": html,
            "visibleSections": s.get("visibleSections", []),
        }, ensure_ascii=False),
        encoding="utf-8",
    )

print(f"capture-scroll: wrote {len(trajectory)} stop(s) to {outdir}/", file=sys.stderr)
PY
