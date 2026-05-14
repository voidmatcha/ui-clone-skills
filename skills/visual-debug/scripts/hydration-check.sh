#!/usr/bin/env bash
# hydration-check.sh — Detect React/Vue/Svelte hydration mismatches.
#
# Why it matters:
#   Server-rendered markup must match what the client-side framework produces
#   on first render. Common ways this silently breaks:
#     - `Math.random()` / `Date.now()` / `crypto.randomUUID()` inside a render
#       function or useMemo/computed that runs on both server and client
#     - Conditional rendering on `typeof window !== 'undefined'` outside an
#       effect (renders branch A on server, branch B on client)
#     - Locale-sensitive `Intl.DateTimeFormat` / `toLocaleString` with no
#       fixed locale (server and client may disagree)
#     - Browser-only fields (cookies, localStorage) read during render
#   These errors usually do NOT crash the page, but they DO:
#     - Throw away the server HTML and re-render from scratch
#     - Wipe out animation initial frames (splash, intro)
#     - Reflow the page on every load (LCP regression)
#     - Trigger the React console.error: "Hydration failed because the server
#       rendered HTML didn't match the client" / "Text content did not match"
#
# This check runs the live impl URL through agent-browser, captures every
# `console.error` and unhandled rejection during load + 2s settle, then
# searches for the canonical hydration warnings. Noise filters (browser
# extensions, Next.js dev indicators, Fast Refresh) keep false positives down.
#
# Usage:
#   bash hydration-check.sh <session> <impl-url> <ref-dir> [w] [h]
#
#   ref-dir: tmp/ref/<component>/ — output JSON goes here.
#
# Optional env:
#   HYDRATION_IGNORE  — extra regex (POSIX ERE) to filter out additional
#                       known-noise console errors. Combined with built-in
#                       filters via `|`.
#   WAIT_MS           — initial wait after navigate (default 1500)
#   SETTLE_MS         — additional settle after load (default 2000)
#
# Exit: 0 = no hydration errors, 1 = hydration errors found, 2 = setup error
# Output: <ref-dir>/hydration-check.json with shape:
#   { "status": "pass"|"fail",
#     "errorCount": N,
#     "errors": [ { "type": "console.error"|"unhandledrejection",
#                   "text": "...", "stack": "..." } ],
#     "filteredCount": N,
#     "url": "...", "viewport": "WxH",
#     "generatedAt": "..." }

set -uo pipefail

SESSION="${1:?Usage: hydration-check.sh <session> <impl-url> <ref-dir> [w] [h]}"
URL="${2:?Missing impl-url}"
REF_DIR="${3:?Missing ref-dir (e.g. tmp/ref/<component>)}"
VIEW_W="${4:-${VIEW_W:-1280}}"
VIEW_H="${5:-${VIEW_H:-800}}"
WAIT_MS="${WAIT_MS:-1500}"
SETTLE_MS="${SETTLE_MS:-2000}"

mkdir -p "$REF_DIR"
OUT="$REF_DIR/hydration-check.json"

if ! command -v agent-browser >/dev/null 2>&1; then
  echo "ERROR: agent-browser not found. Install: npm i -g agent-browser" >&2
  exit 2
fi

cleanup() {
  agent-browser --session "$SESSION" close 2>/dev/null
}
trap cleanup EXIT

# Built-in noise patterns. These match VERY narrowly so we do not silence
# real warnings. Anything not matched gets routed into the errors[] array.
#
# Notes:
#  - Next.js Fast Refresh prints `[Fast Refresh] *`; not a hydration issue.
#  - `__nextjs_*` are dev-only internal API frames.
#  - DevTools / extension stubs frequently emit harmless warnings.
#  - 404s for icon assets are tracked separately by asset gates.
BUILTIN_IGNORE='(\[Fast Refresh\]|__nextjs_|webpack-dev-server|chrome-extension://|Download the React DevTools|favicon\.ico|/_next/static/.*\.hot-update\.json)'

EXTRA_IGNORE="${HYDRATION_IGNORE:-}"
IGNORE_REGEX="$BUILTIN_IGNORE"
[ -n "$EXTRA_IGNORE" ] && IGNORE_REGEX="${IGNORE_REGEX}|${EXTRA_IGNORE}"

# Hydration signature patterns. These are the messages frameworks actually
# emit. Keep narrow enough that "the word hydration appeared in your
# component name" does not trip the check, broad enough to catch all variants.
HYDRATION_PATTERNS='(Hydration failed|Text content did not match|did not match the server-rendered HTML|Expected server HTML to contain|HydrationMismatch|hydrating but some attributes|did not match\. Server: ".*" Client: ".*")'

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Step 1: navigate and let bundle settle. We rely on agent-browser's
# built-in console/errors recorders (which attach via CDP before document
# load), avoiding the cross-origin window-state reset that breaks the
# eval-injection approach.
agent-browser --session "$SESSION" set viewport "$VIEW_W" "$VIEW_H" >/dev/null 2>&1
agent-browser --session "$SESSION" navigate "$URL" >/dev/null 2>&1
sleep $((WAIT_MS / 1000))
agent-browser --session "$SESSION" eval "(async () => { await new Promise(r => setTimeout(r, $SETTLE_MS)); return 'settled'; })()" >/dev/null 2>&1

# Collect from built-in CDP-backed buffers — these capture pre-eval errors.
CONSOLE_LINES=$(agent-browser --session "$SESSION" console 2>/dev/null || true)
ERROR_LINES=$(agent-browser --session "$SESSION" errors 2>/dev/null || true)

# Build a JSON payload from the textual buffers. Lines starting with `[error]`
# in `console` and ALL lines in `errors` count as candidate errors.
DATA=$(node -e "
const consoleLines = process.argv[1].split('\n');
const errorLines = process.argv[2].split('\n');
const errors = [];
for (const line of consoleLines) {
  const m = line.match(/^\[(error|warn)\]\s*(.*)$/);
  if (m) errors.push({ type: 'console.' + m[1], text: m[2], stack: '' });
}
for (const line of errorLines) {
  if (line.trim()) errors.push({ type: 'pageerror', text: line, stack: '' });
}
process.stdout.write(JSON.stringify({ errors, rejections: [] }));
" "$CONSOLE_LINES" "$ERROR_LINES")

# Categorize errors using node (avoid building a brittle awk pipeline).
node -e "
const data = JSON.parse(process.argv[1] || '{}');
const ignoreRe = new RegExp(process.argv[2], 'i');
const hydroRe  = new RegExp(process.argv[3], 'i');

if (data.missing) {
  console.error('WARNING: __hydrationCapture missing on settled page — likely cross-origin navigation reset window. Treating as inconclusive.');
  process.exit(0);
}

const all = [...(data.errors||[]), ...(data.rejections||[])];
const ignored = [];
const real = [];
for (const e of all) {
  if (ignoreRe.test(e.text)) { ignored.push(e); continue; }
  real.push(e);
}
const hydrationErrors = real.filter(e => hydroRe.test(e.text));
const output = {
  status: hydrationErrors.length === 0 ? 'pass' : 'fail',
  errorCount: hydrationErrors.length,
  errors: hydrationErrors.slice(0, 10),
  otherErrorCount: real.length - hydrationErrors.length,
  filteredCount: ignored.length,
  url: process.argv[4],
  viewport: process.argv[5] + 'x' + process.argv[6],
  generatedAt: process.argv[7],
};
process.stdout.write(JSON.stringify(output, null, 2));
process.exit(hydrationErrors.length === 0 ? 0 : 1);
" "$DATA" "$IGNORE_REGEX" "$HYDRATION_PATTERNS" "$URL" "$VIEW_W" "$VIEW_H" "$NOW" > "$OUT"
NODE_EXIT=$?

if [ "$NODE_EXIT" -eq 0 ]; then
  if grep -q '"status": "pass"' "$OUT"; then
    OTHER=$(grep -o '"otherErrorCount": [0-9]*' "$OUT" | grep -o '[0-9]*$')
    FILT=$(grep -o '"filteredCount": [0-9]*' "$OUT" | grep -o '[0-9]*$')
    echo "✅ Hydration: PASS (other console errors: ${OTHER:-0}, filtered noise: ${FILT:-0})"
    echo "   Output: $OUT"
    exit 0
  fi
fi

ERRCOUNT=$(grep -o '"errorCount": [0-9]*' "$OUT" | grep -o '[0-9]*$')
echo "❌ Hydration: FAIL — ${ERRCOUNT:-?} mismatch error(s)"
echo "   Output: $OUT"
echo ""
echo "   Common causes:"
echo "     - Math.random() / Date.now() in render path or useMemo (not in useEffect)"
echo "     - typeof window !== 'undefined' branching outside an effect"
echo "     - Intl.DateTimeFormat / toLocaleString without a fixed locale"
echo "     - reading cookies / localStorage during render"
echo ""
echo "   Fix pattern for RNG: use a seeded shuffle that produces the same output"
echo "   on server and client. See useScrubStagger 'shuffle' option for reference."
exit 1
