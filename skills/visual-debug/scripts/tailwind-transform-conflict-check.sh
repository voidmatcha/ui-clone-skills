#!/usr/bin/env bash
# tailwind-transform-conflict-check.sh — Detect Tailwind v3 ↔ v4 transform
# conflicts on a running impl.
#
# Why it matters:
#   Tailwind v3 emits `transform: translate(...) rotate(...) scale(...)` (and
#   compiles its `translate-x-*` / `rotate-*` / `scale-*` utilities into the
#   same shorthand chain). Tailwind v4 emits individual CSS properties
#   (`translate:`, `rotate:`, `scale:`) which compose ON TOP OF whatever
#   `transform:` already declares — so when a Tailwind v3 utility class lands
#   on an element under a Tailwind v4 host (or vice versa), the combined
#   matrix stacks twice and the element renders offset / rotated double its
#   intended amount.
#
#   AE/SSIM may catch the visible mismatch *if* the element is on-screen at
#   the captured scroll position. The transform compositor is silent — no
#   console error. The structural signature is "computed style has BOTH a
#   non-identity `transform:` AND a non-`none` `translate:`/`rotate:`/`scale:`
#   on the same element", which is what this check flags.
#
#   This is the static counterpart to AE/section-compare for Root Cause I in
#   diagnosis.md. Cheap (one page load); should run alongside
#   `stray-absolute-check.sh` at Step 8-pre.
#
# Usage:
#   bash tailwind-transform-conflict-check.sh <session> <impl-url> [w] [h] [scope-selector]
#
#   <session>:        agent-browser session name
#   <impl-url>:       http://localhost:<port>/<route>
#   [w], [h]:         viewport (default 1440 / 900)
#   [scope-selector]: optional — limit scan to descendants of this selector
#                     (defaults to the whole document). Useful when the clone
#                     is embedded inside a larger app: pass `[data-project="<c>"]`.
#
# Output:
#   Markdown table of offending elements + writes
#   `<REF_DIR>/tailwind-conflict.json` if REF_DIR env var is set.
#
# Exit: 0 = no conflicts, 1 = conflicts found, 2 = setup error

set -uo pipefail

if ! command -v agent-browser &>/dev/null; then
  echo "ERROR: agent-browser not found. Install: npm i -g agent-browser"
  exit 2
fi

SESSION="${1:?Usage: tailwind-transform-conflict-check.sh <session> <impl-url> [w] [h] [scope-selector]}"
URL="${2:?Missing impl-url}"
VIEW_W="${3:-${VIEW_W:-1440}}"
VIEW_H="${4:-${VIEW_H:-900}}"
SCOPE="${5:-*}"
WAIT_MS="${WAIT_MS:-3000}"
REF_DIR="${REF_DIR:-}"

cleanup() {
  agent-browser --session "$SESSION" close 2>/dev/null
}
trap cleanup EXIT

agent-browser --session "$SESSION" set viewport "$VIEW_W" "$VIEW_H" >/dev/null 2>&1
agent-browser --session "$SESSION" navigate "$URL" >/dev/null 2>&1
sleep $((WAIT_MS / 1000))

# Walk every descendant of <scope-selector>. Flag elements where computed
# style has a non-identity `transform:` AND a non-`none` `translate:` /
# `rotate:` / `scale:`. Both being set is the structural signature of the
# v3↔v4 conflict — the v4 properties compose ON TOP of whatever the v3
# shorthand produced.
RAW=$(agent-browser --session "$SESSION" eval "(() => {
  const out = [];
  const SKIP_TAGS = new Set(['SCRIPT','STYLE','META','LINK','HEAD','TITLE','NOSCRIPT','NEXT-ROUTE-ANNOUNCER']);
  const root = document.querySelector(${SCOPE@Q}) || document.documentElement;
  const all = root.querySelectorAll('*');
  for (const el of all) {
    if (SKIP_TAGS.has(el.tagName)) continue;
    const cs = getComputedStyle(el);
    const tFx = cs.transform && cs.transform !== 'none' && cs.transform !== 'matrix(1, 0, 0, 1, 0, 0)';
    const indiv = (cs.translate && cs.translate !== 'none') ||
                  (cs.rotate && cs.rotate !== 'none') ||
                  (cs.scale && cs.scale !== 'none');
    if (!(tFx && indiv)) continue;
    // Skip invisible elements — bug only matters when rendered.
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 && r.height < 1) continue;
    out.push({
      tag: el.tagName,
      id: el.id || '',
      cls: (el.className && el.className.toString ? el.className.toString() : '').slice(0, 80),
      transform: cs.transform,
      translate: cs.translate,
      rotate: cs.rotate,
      scale: cs.scale,
    });
  }
  return JSON.stringify(out);
})()" 2>/dev/null)

# Unwrap agent-browser quoting.
DATA=$(echo "$RAW" | sed 's/^\"//;s/\"$//' | sed 's/\\\"/\"/g')

if [ -z "$DATA" ] || [ "$DATA" = "[]" ] || [ "$DATA" = "null" ]; then
  echo "✅ No Tailwind v3↔v4 transform conflicts found."
  if [ -n "$REF_DIR" ] && [ -d "$REF_DIR" ]; then
    SCOPE_JSON=$(node -e 'process.stdout.write(JSON.stringify(process.argv[1]))' "$SCOPE")
    printf '{"status":"pass","conflictCount":0,"scope":%s,"conflicts":[]}\n' "$SCOPE_JSON" \
      > "$REF_DIR/tailwind-conflict.json"
  fi
  exit 0
fi

echo "═══ Tailwind v3 ↔ v4 Transform Conflicts ═══"
echo "URL: $URL"
echo "Viewport: ${VIEW_W}x${VIEW_H}"
echo "Scope: $SCOPE"
echo ""
echo "Elements where computed style stacks \`transform:\` AND individual"
echo "\`translate:\`/\`rotate:\`/\`scale:\` — see diagnosis.md → Root Cause I."
echo ""

node -e "
const data = JSON.parse(process.argv[1]);
const refDir = process.argv[2];
const scope = process.argv[3];
console.log('| # | tag | class/id | transform | translate | rotate | scale |');
console.log('|---|-----|----------|-----------|-----------|--------|-------|');
data.forEach((s, i) => {
  const idCls = s.id ? ('#' + s.id) : (s.cls ? ('.' + s.cls.split(' ').slice(0, 2).join('.')) : '');
  console.log('| ' + i + ' | ' + s.tag + ' | ' + idCls + ' | ' + s.transform + ' | ' + s.translate + ' | ' + s.rotate + ' | ' + s.scale + ' |');
});
console.log('');
console.log('Fix pattern (see diagnosis.md → Root Cause I):');
console.log('  Override the v4 CSS variables, NOT the resolved property:');
console.log('    [data-project=\"<c>\"] <selector> {');
console.log('      --tw-translate-x: 0 !important;');
console.log('      --tw-translate-y: 0 !important;');
console.log('    }');
console.log('  Do NOT write \`transform: none !important; translate: 0 0 !important\`');
console.log('  together — the v4 minifier collapses them and drops the translate.');
console.log('');
if (refDir) {
  const fs = require('fs');
  fs.writeFileSync(
    refDir + '/tailwind-conflict.json',
    JSON.stringify({
      status: 'fail',
      conflictCount: data.length,
      scope,
      conflicts: data,
    }, null, 2) + '\n'
  );
}
process.exit(1);
" "$DATA" "${REF_DIR:-}" "$SCOPE"
