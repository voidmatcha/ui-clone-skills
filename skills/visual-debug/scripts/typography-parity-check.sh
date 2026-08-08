#!/usr/bin/env bash
# typography-parity-check.sh — per-element font-weight / letter-spacing /
# global body-rule diff between ref and impl.
#
# Why it matters (omx navercorp evidence class):
#   font-parity only compares the primary font FAMILY. An impl can load the
#   right family and still render visibly different text because it dropped
#   the ref's global `letter-spacing: -0.5px` body rule, or generated
#   headings at font-weight 400/600 where the ref uses 800/900. AE flags the
#   pixel diff but buries the cause; this check names it.
#
# What it compares:
#   - global body rule: computed font-weight + letter-spacing of <body>
#   - per-element: first ~30 visible text-bearing elements
#     (h1-h4, p, a, button, li, span), paired by tag + normalized text
#     prefix; font-weight exact, letter-spacing within 0.05px
#     ("normal" normalizes to 0px)
#
# Output: <ref-dir>/typography-parity.json with "status": "pass" | "fail"
#   (the verification-plan JSON enforcement keys on status; mismatch detail
#   stays in the artifact for diagnosis).
#
# Usage: bash typography-parity-check.sh <session> <ref-url> <impl-url> <ref-dir>
# Exit: 0 on completed comparison (pass OR fail — gate reads status),
#       2 on setup errors.

set -uo pipefail

# Fixture mode (regression tests): when both TYPO_PARITY_RAW_REF and
# TYPO_PARITY_RAW_IMPL point at files holding the EVAL_TYPOGRAPHY payload
# shape, skip browser probing entirely — the pairing/verdict logic is then
# exercisable without agent-browser (tests/gates/test_typography_parity.py).
FIXTURE_MODE=0
if [ -n "${TYPO_PARITY_RAW_REF:-}" ] && [ -n "${TYPO_PARITY_RAW_IMPL:-}" ]; then
  FIXTURE_MODE=1
fi

if [ "$FIXTURE_MODE" != "1" ] && ! command -v agent-browser &>/dev/null; then
  echo "ERROR: agent-browser not found. Install: npm i -g agent-browser"
  exit 2
fi

SESSION="${1:?Usage: typography-parity-check.sh <session> <ref-url> <impl-url> <ref-dir>}"
REF_URL="${2:?Missing ref-url}"
IMPL_URL="${3:?Missing impl-url}"
REF_DIR="${4:?Missing ref-dir (e.g. \$(pwd)/tmp/ref/<component>)}"

if [ ! -d "$REF_DIR" ]; then
  echo "ERROR: ref-dir does not exist: $REF_DIR"
  exit 2
fi

cleanup() {
  agent-browser --session "$SESSION-ref" close >/dev/null 2>&1
  agent-browser --session "$SESSION-impl" close >/dev/null 2>&1
}
[ "$FIXTURE_MODE" != "1" ] && trap cleanup EXIT

# Collect body rule + per-element typography samples. letter-spacing
# "normal" is normalized to "0px" so a ref that DECLARES -0.5px vs an impl
# that declares nothing diffs as -0.5 vs 0.
EVAL_TYPOGRAPHY='(() => {
  const norm = (ls) => (ls === "normal" ? "0px" : ls);
  const pick = (el) => {
    const s = getComputedStyle(el);
    return { fontWeight: s.fontWeight, letterSpacing: norm(s.letterSpacing) };
  };
  const body = pick(document.body);
  const els = [];
  const sels = "h1, h2, h3, h4, p, a, button, li, span";
  for (const el of document.querySelectorAll(sels)) {
    const r = el.getBoundingClientRect();
    const text = (el.textContent || "").trim();
    if (r.width < 2 || r.height < 2 || text.length < 3) continue;
    const sig = el.tagName.toLowerCase() + "|" + text.slice(0, 24).replace(/\s+/g, " ");
    els.push({ sig, ...pick(el) });
    if (els.length >= 30) break;
  }
  return JSON.stringify({ body, els });
})()'

probe() {
  local session="$1" url="$2"
  agent-browser --session "$session" open "$url" >/dev/null 2>&1
  agent-browser --session "$session" set viewport 1280 800 >/dev/null 2>&1
  agent-browser --session "$session" wait 2500 >/dev/null 2>&1
  agent-browser --session "$session" eval "$EVAL_TYPOGRAPHY" 2>/dev/null | tail -1
}

if [ "$FIXTURE_MODE" = "1" ]; then
  REF_RAW="$(cat "$TYPO_PARITY_RAW_REF")"
  IMPL_RAW="$(cat "$TYPO_PARITY_RAW_IMPL")"
else
  REF_RAW="$(probe "$SESSION-ref" "$REF_URL")"
  IMPL_RAW="$(probe "$SESSION-impl" "$IMPL_URL")"
fi

if [ -z "$REF_RAW" ] || [ -z "$IMPL_RAW" ]; then
  echo "ERROR: failed to extract typography. ref='${REF_RAW:0:60}' impl='${IMPL_RAW:0:60}'"
  exit 2
fi

OUT="$REF_DIR/typography-parity.json"
node -e "
const fs = require('fs');
function parse(label, raw) {
  let v;
  try {
    v = JSON.parse(raw);
    if (typeof v === 'string') v = JSON.parse(v);
  } catch (e) {
    console.error(label + ' parse failed:', e.message);
    process.exit(2);
  }
  return v;
}
const ref = parse('ref', process.argv[1]);
const impl = parse('impl', process.argv[2]);
const px = (s) => parseFloat(s) || 0;
const lsMatch = (a, b) => Math.abs(px(a) - px(b)) <= 0.05;
const bodyMismatches = [];
if (ref.body.fontWeight !== impl.body.fontWeight)
  bodyMismatches.push({ prop: 'font-weight', ref: ref.body.fontWeight, impl: impl.body.fontWeight });
if (!lsMatch(ref.body.letterSpacing, impl.body.letterSpacing))
  bodyMismatches.push({ prop: 'letter-spacing', ref: ref.body.letterSpacing, impl: impl.body.letterSpacing });
// Duplicate sigs are real (e2e-9: four span|Real Food at fw 400/600/700/700);
// a sig->element Map collapses them last-wins and the ref fails against
// itself. Group impl elements per sig and zip the k-th ref instance with the
// k-th impl instance instead.
const implBySig = new Map();
for (const e of impl.els) {
  const g = implBySig.get(e.sig);
  if (g) g.push(e); else implBySig.set(e.sig, [e]);
}
const nextIdx = new Map();
const perElement = [];
let pairs = 0;
for (const r of ref.els) {
  const g = implBySig.get(r.sig);
  const k = nextIdx.get(r.sig) || 0;
  if (!g || k >= g.length) continue;
  nextIdx.set(r.sig, k + 1);
  const i = g[k];
  pairs++;
  const weightOk = r.fontWeight === i.fontWeight;
  const lsOk = lsMatch(r.letterSpacing, i.letterSpacing);
  if (!weightOk || !lsOk) {
    perElement.push({
      sig: r.sig,
      refWeight: r.fontWeight, implWeight: i.fontWeight,
      refLetterSpacing: r.letterSpacing, implLetterSpacing: i.letterSpacing,
    });
  }
}
const status = bodyMismatches.length === 0 && perElement.length === 0 ? 'pass' : 'fail';
const out = {
  schemaVersion: 1,
  status,
  body: { ref: ref.body, impl: impl.body, mismatches: bodyMismatches },
  pairedElements: pairs,
  elementMismatches: perElement,
  capturedAt: new Date().toISOString(),
};
fs.writeFileSync('$OUT', JSON.stringify(out, null, 2));
console.log('Wrote $OUT');
console.log('  status: ' + status + ' (paired ' + pairs + ', element mismatches ' + perElement.length + ', body mismatches ' + bodyMismatches.length + ')');
for (const m of bodyMismatches) console.log('  body ' + m.prop + ': ref=' + m.ref + ' impl=' + m.impl);
for (const m of perElement.slice(0, 8)) console.log('  ' + m.sig + ': weight ' + m.refWeight + '->' + m.implWeight + ', ls ' + m.refLetterSpacing + '->' + m.implLetterSpacing);
" "$REF_RAW" "$IMPL_RAW"
