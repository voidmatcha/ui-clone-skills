#!/usr/bin/env bash
# splash-bypass.sh — canonical splash/preloader detection + bypass
#
# Usage: splash-bypass.sh <url> <session> <ref_dir>
#
# Opens URL in an agent-browser session, runs the auto-detect loop
# (full-screen-overlay check + interactivity check + scrollability check
# + DOM-stability count, up to 40×500ms), and writes splash-state.json
# into <ref_dir>. Session stays alive for downstream callers
# (Steps 1-6 of dom-extraction.md, animation-detection.md, etc.).
#
# Third arg name `<ref_dir>` mirrors capture.sh's contract so callers
# pass the same path they already compute for ref artefacts. The file
# is part of CANONICAL_REF_ARTIFACTS (see ui_clone/_common.py).
#
# Replaces the inline `agent-browser eval` JS that used to live in
# dom-extraction.md, animation-detection.md, and element-capture.md.
# All callers now route through this one script so splash handling
# becomes deterministic and inspectable instead of slapdash.
#
# Output schema (splash-state.json):
#   {
#     "hasSplash":         true|false,   # heuristic: durationMs > 2000
#     "splashDone":        true|false,   # loop converged before maxChecks
#     "timedOut":          true|false,   # loop hit maxChecks without converging
#     "durationMs":        <int>,        # ms spent in the detect loop
#     "signals": {                       # top-level for debug ergonomics
#       "hasFullScreenOverlay": bool,
#       "linkReachable":        bool,
#       "isScrollable":         bool,
#       "domStable":            bool,
#       "stableCount":          int
#     },
#     "detectionMethod":   "auto-detect-loop-v1",
#     "sessionId":         "<session>",
#     "url":               "<url>",
#     "rawDetection":      { ... },      # full JS payload (audit trail)
#     "capturedAt":        "<ISO 8601 Z>"
#   }
#
# Exit codes:
#   0  splash check completed (may or may not have detected splash)
#   1  bad usage
#   2  agent-browser open failed
#   3  agent-browser eval returned unparseable / error response

set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "Usage: $0 <url> <session> <ref_dir>" >&2
  exit 1
fi

URL="$1"
SESSION="$2"
REF_DIR="$3"

mkdir -p "$REF_DIR"
OUTFILE="$REF_DIR/splash-state.json"

# Open (or re-navigate to) the URL in the named session. agent-browser
# is idempotent on session name. Any failure here is fatal — without
# an open page the eval loop can't run.
if ! agent-browser --session "$SESSION" open "$URL" > /dev/null 2>&1; then
  echo "splash-bypass: agent-browser open failed for $URL" >&2
  exit 2
fi

# Run the auto-detect IIFE via --stdin (no shell escaping) and --json
# (predictable wrapper: {"success":true,"data":{"result":...}}).
RAW="$(agent-browser --session "$SESSION" eval --stdin --json <<'JS'
(() => {
  return new Promise(resolve => {
    let checks = 0;
    const maxChecks = 40; // 40 × 500ms = 20s ceiling
    const check = () => {
      checks++;

      // Signal 1: full-viewport fixed overlay still present?
      const overlays = [...document.querySelectorAll('*')].filter(el => {
        const s = getComputedStyle(el);
        return s.position === 'fixed' &&
               s.zIndex !== 'auto' && parseInt(s.zIndex) > 10 &&
               el.offsetWidth >= window.innerWidth * 0.9 &&
               el.offsetHeight >= window.innerHeight * 0.9 &&
               s.opacity !== '0' && s.display !== 'none' && s.visibility !== 'hidden';
      });
      const hasFullScreenOverlay = overlays.length > 0;

      // Signal 2: first interactive element actually reachable (not
      // covered by an invisible overlay with pointer-events:none).
      const firstLink = document.querySelector('a[href], button');
      const linkRect = firstLink && firstLink.getBoundingClientRect();
      const linkReachable = !!(linkRect && linkRect.width > 0 &&
        document.elementFromPoint(linkRect.x + 5, linkRect.y + 5) === firstLink);

      // Signal 3: page is now scrollable (splash often locks scroll).
      const scrollHeight = document.documentElement.scrollHeight;
      const isScrollable = scrollHeight > window.innerHeight * 1.5;

      // Signal 4: DOM stabilised for 2 consecutive 500ms checks.
      const currentLen = document.body.innerHTML.length;
      window.__splashCheckLen = window.__splashCheckLen || 0;
      window.__splashStableCount = window.__splashStableCount || 0;
      if (Math.abs(currentLen - window.__splashCheckLen) < 500) {
        window.__splashStableCount++;
      } else {
        window.__splashStableCount = 0;
      }
      window.__splashCheckLen = currentLen;
      const stableCount = window.__splashStableCount;
      const domStable = stableCount >= 2;

      const splashDone = !hasFullScreenOverlay && isScrollable && domStable;

      if (splashDone || checks >= maxChecks) {
        resolve({
          splashDone,
          timedOut: !splashDone && checks >= maxChecks,
          checks,
          timeMs: checks * 500,
          signals: { hasFullScreenOverlay, linkReachable, isScrollable, domStable, stableCount }
        });
      } else {
        setTimeout(check, 500);
      }
    };
    check();
  });
})()
JS
)"

if [ -z "$RAW" ]; then
  echo "splash-bypass: agent-browser eval returned no output" >&2
  exit 3
fi

# Parse the agent-browser --json envelope and unwrap .data.result, then
# enrich with metadata. Python (no jq dependency).
python3 - "$RAW" "$URL" "$SESSION" "$OUTFILE" <<'PY'
import json, sys, datetime, pathlib

raw_str, url, session, outfile = sys.argv[1:5]
try:
    envelope = json.loads(raw_str)
except json.JSONDecodeError as exc:
    print(f"splash-bypass: bad JSON from agent-browser: {exc}", file=sys.stderr)
    print(f"splash-bypass: raw was: {raw_str[:200]!r}", file=sys.stderr)
    sys.exit(3)

if not envelope.get("success", False):
    print(f"splash-bypass: agent-browser eval failed: {envelope.get('error')}", file=sys.stderr)
    sys.exit(3)

result = envelope.get("data", {}).get("result")
if not isinstance(result, dict):
    print(f"splash-bypass: unexpected detection payload: {result!r}", file=sys.stderr)
    sys.exit(3)

duration_ms = int(result.get("timeMs", 0))
signals = result.get("signals", {})
state = {
    "hasSplash": duration_ms > 2000,
    "splashDone": bool(result.get("splashDone", False)),
    "timedOut": bool(result.get("timedOut", False)),
    "durationMs": duration_ms,
    "signals": signals,
    "detectionMethod": "auto-detect-loop-v1",
    "sessionId": session,
    "url": url,
    "rawDetection": result,
    "capturedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
pathlib.Path(outfile).write_text(json.dumps(state, indent=2) + "\n")
print(f"splash-state written: {outfile}")
print(f"hasSplash={state['hasSplash']} durationMs={duration_ms} "
      f"splashDone={state['splashDone']} timedOut={state['timedOut']}")
PY
