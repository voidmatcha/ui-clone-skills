#!/usr/bin/env bash
# font-parity-check.sh — Compare the primary font family loaded by ref vs impl.
#
# Why it matters:
#   When a project substitutes a paid commercial font (Exat, Söhne, etc.) with a
#   free variable font (Roboto Flex, Inter Tight), AE pixel comparison fails for
#   every section that renders text. The gate would never clear. The fix is to
#   declare the substitution via asset-substitution.json so section-compare
#   switches matching sections to structural-only diff.
#
#   This script extracts the primary `font-family` (computed style of <body>'s
#   first text-bearing descendant) from both ref and impl, writes the result to
#   `<ref-dir>/font-parity.json`, and the `font-parity` gate enforces:
#     - parity:"match"    → PASS
#     - parity:"mismatch" → must have asset-substitution.json with fonts[] entry
#
# Usage: bash font-parity-check.sh <session> <ref-url> <impl-url> <ref-dir>
#   ref-dir: the tmp/ref/<component>/ path (gate's $REF_DIR)
#
# Exit: 0 always (this is a *parity check*; mismatch is reported via JSON,
#       not exit code, because mismatch may be declared/intentional).
#       Setup errors exit 2.

set -uo pipefail

if ! command -v agent-browser &>/dev/null; then
  echo "ERROR: agent-browser not found. Install: npm i -g agent-browser"
  exit 2
fi

SESSION="${1:?Usage: font-parity-check.sh <session> <ref-url> <impl-url> <ref-dir>}"
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
trap cleanup EXIT

# Eval that returns the primary font family AND whether it's actually loaded.
#
# `getComputedStyle().fontFamily` returns the CSS-declared name, NOT what was
# rendered. If the declared font fails to load (404, CORS, expired Typekit ID),
# the browser silently falls back, but computed style still reports the original.
# We additionally call `document.fonts.check()` to verify the FontFace is actually
# loaded — this catches the silent-fallback bug class for paid fonts that aren't
# accessible from the impl deployment.
EVAL_PRIMARY_FAMILY='(() => {
  const text = Array.from(document.body.querySelectorAll("h1, h2, h3, p, span, a, div"))
    .find(el => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && (el.textContent || "").trim().length > 0;
    });
  const target = text || document.body;
  const fam = getComputedStyle(target).fontFamily || "";
  // Strip surrounding quotes (CSS may quote multi-word families).
  const family = fam.split(",")[0].replace(/^[\s\u0022\u0027]+|[\s\u0022\u0027]+$/g, "").trim();
  const fontSize = getComputedStyle(target).fontSize || "16px";
  // document.fonts.check returns true only if the FontFace for this family/size
  // is actually loaded — covers the case where CSS declares Exat but Exat 404s.
  let loaded = false;
  try {
    loaded = document.fonts && typeof document.fonts.check === "function"
      ? document.fonts.check(fontSize + " " + JSON.stringify(family))
      : true;
  } catch (e) {
    loaded = true; // be conservative: if check throws, do not block
  }
  // Sampling one element sees one family. A page whose body text is one face
  // and whose banner is another hides the second entirely, so enumerate every
  // declared @font-face and report each one with its own load status.
  const declared = [];
  try {
    for (const sheet of Array.from(document.styleSheets)) {
      let rules = null;
      try { rules = sheet.cssRules; } catch (e) { continue; } // cross-origin
      for (const rule of Array.from(rules || [])) {
        if (rule.type !== 5) continue; // CSSRule.FONT_FACE_RULE
        const raw = (rule.style && rule.style.getPropertyValue("font-family")) || "";
        const one = raw.split(",")[0]
          .replace(/^[\s\u0022\u0027]+|[\s\u0022\u0027]+$/g, "").trim();
        if (one && declared.indexOf(one) === -1) declared.push(one);
      }
    }
  } catch (e) { /* best-effort: never block on enumeration */ }
  const families = declared.map(name => {
    let ok = true;
    try {
      ok = document.fonts && typeof document.fonts.check === "function"
        ? document.fonts.check(fontSize + " " + JSON.stringify(name))
        : true;
    } catch (e) {
      ok = true;
    }
    return { family: name, loaded: ok };
  });
  return JSON.stringify({
    family,
    fullStack: fam,
    targetTag: target.tagName.toLowerCase(),
    fontSize,
    loaded,
    families,
  });
})()'

extract_family() {
  local session="$1" url="$2"
  agent-browser --session "$session" open "$url" >/dev/null 2>&1
  agent-browser --session "$session" set viewport 1280 800 >/dev/null 2>&1
  agent-browser --session "$session" wait 2500 >/dev/null 2>&1
  # Return the raw stdout as-is. agent-browser prints the JSON-stringified
  # eval result on its own line; downstream node call double-parses to recover
  # the inner object. Avoid sed-based unwrap — it breaks on family names that
  # contain quotes (e.g. `"Clash Grotesk"` collapses one too few escape
  # levels and produces invalid JSON like `\\"Clash Grotesk\\"`).
  agent-browser --session "$session" eval "$EVAL_PRIMARY_FAMILY" 2>/dev/null | tail -1
}

REF_RAW="$(extract_family "$SESSION-ref" "$REF_URL")"
IMPL_RAW="$(extract_family "$SESSION-impl" "$IMPL_URL")"

if [ -z "$REF_RAW" ] || [ -z "$IMPL_RAW" ]; then
  echo "ERROR: failed to extract font family. ref='$REF_RAW' impl='$IMPL_RAW'"
  exit 2
fi

OUT="$REF_DIR/font-parity.json"
node -e "
const fs = require('fs');
function parse(label, raw) {
  // agent-browser eval emits a JSON-encoded *string* whose value is itself a
  // JSON document. Try double-parse first, fall back to single-parse for the
  // (theoretical) case where the in-page script returned a raw object.
  let v;
  try {
    v = JSON.parse(raw);
    if (typeof v === 'string') v = JSON.parse(v);
  } catch (e) {
    console.error(label + ' parse failed:', e.message, '\\n  raw=', raw);
    process.exit(2);
  }
  return v;
}
const ref  = parse('ref',  process.argv[1]);
const impl = parse('impl', process.argv[2]);
const norm = s => (s || '').toLowerCase().trim();
const parity = norm(ref.family) === norm(impl.family) ? 'match' : 'mismatch';
const out = { ref, impl, parity, capturedAt: new Date().toISOString() };
fs.writeFileSync('$OUT', JSON.stringify(out, null, 2));
console.log('Wrote $OUT');
console.log('  ref:  ' + ref.family);
console.log('  impl: ' + impl.family);
console.log('  parity: ' + parity);
" "$REF_RAW" "$IMPL_RAW"
