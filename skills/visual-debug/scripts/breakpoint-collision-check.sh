#!/usr/bin/env bash
# breakpoint-collision-check.sh — Detect Tailwind ↔ project `@media` collisions
# at responsive breakpoint boundaries.
#
# Why it matters:
#   Tailwind responsive utilities use `min-width: <bp>px`. Project-scoped CSS
#   commonly uses `@media (max-width: <bp>px)` for fluid root font-size,
#   container padding, mobile-only stacks, etc. Both ranges are inclusive at
#   the boundary, so at exactly <bp> pixels both rules match and apply at the
#   same time. The catastrophic case is a fluid root font-size mobile rule
#   colliding with desktop `md:`/`lg:` layout — produces a 1-pixel-wide zone
#   of horizontal overflow that AE/SSIM never sees because Step 4-C2 only
#   measures at the boundary itself (where it looks "wrong but consistent").
#
#   This script captures impl at <bp>-1, <bp>, <bp>+1 for every requested
#   breakpoint and reports any width where one of the following is true.
#   Only signals 2 and 3 fail the gate; signal 1 alone is advisory because
#   matchMedia overlap at the boundary is the W3C spec — it occurs on every
#   page that contains both `(min-width: <bp>)` and `(max-width: <bp>)` rules
#   regardless of whether the rules actually conflict.
#     1. matchMedia(`max-width: <bp>`) AND matchMedia(`min-width: <bp>`)
#        both match (always true at the boundary by spec — advisory only).
#     2. body.scrollWidth > viewport at <bp> but NOT at <bp>±1 (isolated
#        spike — real visible defect, fails gate).
#     3. rootFontSize jumps >4px between <bp>-1, <bp>, <bp>+1 (mobile-mode
#        jitter — the rem scale is changing on the boundary, fails gate).
#
# Usage: bash breakpoint-collision-check.sh <session> <impl-url> [bps]
#   bps: space-separated list (default: "640 768 1024 1280 1536" plus valid
#        breakpoints from REF_DIR/detected-breakpoints.json and
#        REF_DIR/impl-detected-breakpoints.json)
#        e.g. "640 768" to test only sm/md
#
# Env:
#   REF_DIR — if set, writes `${REF_DIR}/responsive/boundary-collisions.json`
#             (the artifact the `boundary` gate in ui_clone.gate checks).
#             Without REF_DIR, runs in stdout-only mode.
#
# Exit: 0 = no collisions, 1 = collisions found, 2 = setup error
#
# See diagnosis.md → Root Cause J: Tailwind ↔ CSS @media Boundary Collision.

set -uo pipefail

if ! command -v agent-browser &>/dev/null; then
  echo "ERROR: agent-browser not found. Install: npm i -g agent-browser"
  exit 2
fi

SESSION="${1:?Usage: breakpoint-collision-check.sh <session> <impl-url> [bps]}"
URL="${2:?Missing impl-url}"
WAIT_MS="${WAIT_MS:-700}"
HEIGHT="${HEIGHT:-900}"

if [ "$#" -ge 3 ]; then
  BP_SOURCE="explicit"
  BP_INPUT="$3"
else
  BP_SOURCE="artifacts"
  BP_INPUT="${REF_DIR:-}"
fi

BPS="$(
  node -e "
const fs = require('fs');
const path = require('path');
const source = process.argv[1];
const input = process.argv[2];
const values = source === 'explicit'
  ? input.split(/\s+/)
  : [640, 768, 1024, 1280, 1536];
if (source === 'artifacts' && input) {
  for (const name of ['detected-breakpoints.json', 'impl-detected-breakpoints.json']) {
    const file = path.join(input, name);
    if (!fs.existsSync(file)) continue;
    try {
      const document = JSON.parse(fs.readFileSync(file, 'utf8'));
      if (Array.isArray(document.breakpoints)) values.push(...document.breakpoints);
    } catch {}
  }
}
const normalized = new Set();
for (const value of values) {
  if (typeof value === 'boolean' || value == null) continue;
  const match = String(value).trim().match(/^(\\d+)(?:px)?$/i);
  if (!match) continue;
  const bp = Number(match[1]);
  if (Number.isSafeInteger(bp) && bp > 0) normalized.add(bp);
}
process.stdout.write([...normalized].sort((a, b) => a - b).join(' '));
" "$BP_SOURCE" "$BP_INPUT"
)"

if [ -z "$BPS" ]; then
  echo "ERROR: no valid breakpoints supplied." >&2
  exit 2
fi

# REF_DIR is read later (env-only) to write the gate artifact. Surface the
# decision now so the user knows whether the run will produce the artifact
# the `boundary` gate expects. Silent skip is the failure mode this catches:
# script exits 0, gate then fails with "MISSING" and the agent has no idea
# the script was the missing link.
if [ -z "${REF_DIR:-}" ]; then
  echo "⚠️  REF_DIR not set — running in stdout-only mode."
  echo "    The 'boundary' gate will FAIL with 'MISSING' until you re-run with:"
  echo "    REF_DIR=\"\$(pwd)/tmp/ref/<component>\" $0 $*" >&2
elif [ ! -d "$REF_DIR" ]; then
  echo "ERROR: REF_DIR does not exist: $REF_DIR" >&2
  exit 2
fi

cleanup() {
  agent-browser --session "$SESSION" close >/dev/null 2>&1
}
trap cleanup EXIT

# Open once, then move the viewport. agent-browser keeps the page across
# `set viewport` calls, so we don't reload per width — much faster.
agent-browser --session "$SESSION" navigate "$URL" >/dev/null 2>&1
sleep 1

TMP="$(mktemp -d 2>/dev/null || mktemp -d -t bpcheck)"
SAMPLES="$TMP/samples.jsonl"
: > "$SAMPLES"

probe_width() {
  local W="$1"
  local BP="$2"
  agent-browser --session "$SESSION" set viewport "$W" "$HEIGHT" >/dev/null 2>&1
  # Allow layout/MQ to resettle.
  perl -e "select(undef,undef,undef,$WAIT_MS/1000)"
  local RAW
  RAW=$(agent-browser --session "$SESSION" eval "(() => {
    const bp = ${BP};
    return JSON.stringify({
      bp,
      width: window.innerWidth,
      bodyScrollWidth: document.body.scrollWidth,
      htmlScrollWidth: document.documentElement.scrollWidth,
      overflowing: document.body.scrollWidth > window.innerWidth + 0.5,
      rootFontSize: parseFloat(getComputedStyle(document.documentElement).fontSize),
      mqMaxBp: matchMedia('(max-width: ' + bp + 'px)').matches,
      mqMinBp: matchMedia('(min-width: ' + bp + 'px)').matches,
      collision: matchMedia('(max-width: ' + bp + 'px)').matches && matchMedia('(min-width: ' + bp + 'px)').matches,
    });
  })()" 2>/dev/null)
  # Unwrap agent-browser quoting.
  echo "$RAW" | sed 's/^"//;s/"$//' | sed 's/\\"/"/g' >> "$SAMPLES"
}

for BP in $BPS; do
  for OFF in -1 0 1; do
    W=$((BP + OFF))
    [ "$W" -lt 200 ] && continue
    probe_width "$W" "$BP"
  done
done

if [ ! -s "$SAMPLES" ]; then
  echo "ERROR: no samples captured (eval failed). Check that <impl-url> is reachable."
  exit 2
fi

echo "═══ Breakpoint Collision Check ═══"
echo "URL: $URL"
echo "Breakpoints tested: $BPS"
echo ""

REF_DIR_ARG="${REF_DIR:-}"
export REF_DIR_ARG

node -e "
const fs = require('fs');
const path = require('path');
const lines = fs.readFileSync('$SAMPLES', 'utf8').trim().split('\n').filter(Boolean);
const samples = lines.map(l => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);

// Group by breakpoint, then by width.
const byBp = {};
for (const s of samples) {
  byBp[s.bp] = byBp[s.bp] || {};
  byBp[s.bp][s.width] = s;
}

// Only blocking signals (overflow, rem jitter) become findings. matchMedia
// overlap at the boundary is W3C-spec behavior and fires on every project
// that uses both min-width and max-width queries at the same bp; tracking
// it as advisory keeps the diagnostic info without failing the gate.
const findings = [];
const advisories = [];
for (const [bpStr, samplesAtBp] of Object.entries(byBp)) {
  const bp = +bpStr;
  const at = samplesAtBp[bp];
  const before = samplesAtBp[bp - 1];
  const after = samplesAtBp[bp + 1];
  if (!at) continue;

  const reasons = [];
  const advisory = [];
  if (at.collision) advisory.push('matchMedia overlap at ' + bp + ' (W3C spec — both max-width and min-width match at the boundary)');
  if (at.overflowing && !(before && before.overflowing) && !(after && after.overflowing)) {
    reasons.push('isolated overflow spike (' + at.bodyScrollWidth + ' > ' + at.width + ')');
  }
  if (before && after) {
    const jumpToBefore = Math.abs(at.rootFontSize - before.rootFontSize);
    const jumpToAfter = Math.abs(at.rootFontSize - after.rootFontSize);
    if (jumpToBefore > 4 && jumpToAfter > 4) {
      reasons.push('rootFontSize jitter at boundary (' + before.rootFontSize.toFixed(1) + ' → ' + at.rootFontSize.toFixed(1) + ' → ' + after.rootFontSize.toFixed(1) + ')');
    }
  }
  if (reasons.length) findings.push({ bp, before, at, after, reasons });
  else if (advisory.length) advisories.push({ bp, advisory });
}

// Write the gate artifact when REF_DIR is provided.
const refDir = process.env.REF_DIR_ARG;
if (refDir) {
  const outDir = path.join(refDir, 'responsive');
  fs.mkdirSync(outDir, { recursive: true });
  const outPath = path.join(outDir, 'boundary-collisions.json');
  fs.writeFileSync(outPath, JSON.stringify(findings, null, 2));
  console.log('Wrote ' + outPath + ' (' + findings.length + ' finding' + (findings.length === 1 ? '' : 's') + ')');
}

if (!findings.length) {
  console.log('✅ No collisions at any breakpoint.');
  if (advisories.length) {
    console.log('');
    console.log('Advisory (matchMedia overlap — does not block gate):');
    for (const a of advisories) console.log('  bp=' + a.bp + ': ' + a.advisory.join('; '));
  }
  process.exit(0);
}

console.log('| bp | width | sw | overflow | rootFs | mqMax | mqMin | reasons |');
console.log('|----|-------|----|----------|--------|-------|-------|---------|');
const fmt = (s) => {
  if (!s) return '| — | — | — | — | — | — | — | — |';
  return [
    '',
    s.bp,
    s.width,
    s.bodyScrollWidth,
    s.overflowing ? '⛔ yes' : 'no',
    s.rootFontSize.toFixed(1),
    s.mqMaxBp ? 'Y' : '—',
    s.mqMinBp ? 'Y' : '—',
  ].join(' | ') + ' | |';
};
for (const f of findings) {
  for (const s of [f.before, f.at, f.after]) console.log(fmt(s));
  console.log('| | | | | | | | ⛔ ' + f.reasons.join('; ') + ' |');
  console.log('');
}

console.log('');
console.log('Fix — see diagnosis.md → Root Cause J. Pick ONE side:');
console.log('  A. Make project @media exclusive: \`(max-width: <bp - 0.02>px)\`');
console.log('     (Bootstrap pattern; covers every utility on every page in one edit.)');
console.log('  B. Shift Tailwind variant up one tier: md: → lg:, sm: → md:');
console.log('     (when only one component, e.g. header nav, needs to switch.)');
process.exit(1);
"
