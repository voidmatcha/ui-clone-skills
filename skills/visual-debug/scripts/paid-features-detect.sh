#!/usr/bin/env bash
# paid-features-detect.sh — Static-scan extracted bundles + CSS for paid web fonts.
#
# Why it matters:
#   Paid web fonts (Adobe Typekit, Monotype, Hoefler, FONTPLUS, TypeSquare, …)
#   load only when the deploy holds the right license/kit ID. When the impl is
#   missing the license, the browser silently falls back to the default
#   sans-serif. `getComputedStyle` still reports the declared family, so
#   section-compare reports 100% mismatch on every text-bearing section forever.
#   The font-parity gate catches this AT compare time via
#   `document.fonts.check()`, but by then Step 7 generation has already run.
#
#   This script runs BEFORE generation (Step 5c-c, between `bundle` and `spec`
#   gates). It greps already-downloaded artifacts (bundles/, css/, fonts.json,
#   head.json, external-sdks.json) for known paid-font CDN hostnames and writes
#   findings to paid-features.json with `decision: null`. The `paid-features`
#   gate then fails until every entry has a decision:
#     - "use"        → license is in place (agent confirmed with user)
#     - "substitute" → using a free alternative; downstream font-parity gate
#                      enforces declaration via asset-substitution.json
#     - "skip"       → intentionally not replicating this feature
#
# Note on GSAP plugins:
#   GSAP became 100% free for all users (including all previously-paid Club
#   plugins, including for commercial use) following the Webflow acquisition.
#   This script no longer flags DrawSVGPlugin / MorphSVGPlugin / SplitText /
#   ScrollSmoother / etc. — they are no longer paid features.
#   Source: https://gsap.com/pricing/, https://webflow.com/blog/gsap-becomes-free
#
# Usage: bash paid-features-detect.sh <ref-dir>
#   ref-dir: tmp/ref/<component>/ — must already contain bundles/ and/or css/
#
# Exit: 0 always (this is a *detection* step; the gate enforces the decision).
#       Setup errors exit 2.

set -uo pipefail

REF_DIR="${1:?Usage: paid-features-detect.sh <ref-dir>}"

if [ ! -d "$REF_DIR" ]; then
  echo "ERROR: ref-dir does not exist: $REF_DIR"
  exit 2
fi

if ! command -v node &>/dev/null; then
  echo "ERROR: node not found (required to write JSON)"
  exit 2
fi

# Paid font CDN host patterns. These hosts only serve fonts to licensed
# subscribers — finding a URL pointing at one of these is a strong signal
# the site uses a commercial typeface.
#
# Each entry must have a vendor-published doc confirming it is the actual
# subscriber-only delivery hostname. Hosts cited only by 3rd-party blogs are
# excluded to avoid false positives.
PAID_FONT_HOSTS=(
  "use.typekit.net"      # Adobe Fonts (Typekit) — kit URLs
  "p.typekit.net"        # Adobe Fonts — performance CDN
  "use.edgefonts.net"    # Adobe Edge Web Fonts (legacy)
  "fast.fonts.net"       # Monotype FontDeck
  "fast.fonts.com"       # Monotype Fonts.com
  "cloud.typography.com" # Hoefler & Co. / Monotype Cloud.typography
  "client.linotype.com"  # Linotype FontExplorer
  "mit.fontplus.jp"      # FONTPLUS (Japan, Monotype) — loader script
  "webfont.fontplus.jp"  # FONTPLUS (Japan) — Web API delivery
  "typesquare.com"       # TypeSquare / Morisawa (Japan)
)

# Commercial font family-name detection (Common cheat pattern): when a site
# self-hosts a paid foundry's typeface, the URL never points at a paid CDN
# — only the font-family name reveals the license requirement. Scan
# fonts.json / styles.json / extracted.json for family names from known
# commercial foundries. Match must be case-insensitive and word-boundary
# anchored to avoid false positives (e.g. "Inter" generic match vs "Inter
# Tight" Klim font).
COMMERCIAL_FONT_FAMILIES=(
  # Klim Type Foundry (klim.co.nz) — Inter Tight is open but distinct from these
  "Die Grotesk"
  "Söhne"
  "Calibre"
  "Founders Grotesk"
  "Untitled Sans"
  "Untitled Serif"
  # Commercial Type (commercialtype.com)
  "Publico"
  "Marr Sans"
  "Druk"
  "Action Condensed"
  # Lineto (lineto.com)
  "LL Akkurat"
  "LL Brown"
  "LL Replica"
  "LL Circular"
  # House Industries (houseind.com) — common in landing pages
  "Velo Sans"
  "Eames"
  "Neutraface"
  # Pangram Pangram (pangrampangram.com) — popular indie commercial
  "Editorial New"
  "PP Neue Montreal"
  "PP Mori"
  "PP Right Grotesk"
  # Production Type (productiontype.com)
  "Spectral"
  "Beausite"
)

FONT_FINDINGS=()

# Search roots — only paths we know contain extracted source-of-truth artifacts.
declare -a SEARCH_ROOTS=()
[ -d "$REF_DIR/bundles" ]            && SEARCH_ROOTS+=("$REF_DIR/bundles")
[ -d "$REF_DIR/css" ]                && SEARCH_ROOTS+=("$REF_DIR/css")
[ -f "$REF_DIR/fonts.json" ]         && SEARCH_ROOTS+=("$REF_DIR/fonts.json")
[ -f "$REF_DIR/head.json" ]          && SEARCH_ROOTS+=("$REF_DIR/head.json")
[ -f "$REF_DIR/external-sdks.json" ] && SEARCH_ROOTS+=("$REF_DIR/external-sdks.json")

if [ ${#SEARCH_ROOTS[@]} -eq 0 ]; then
  echo "WARN: no extraction artifacts under $REF_DIR (bundles/, css/, fonts.json)."
  echo "      Run the bundle gate first; nothing to scan."
fi

# grep_first <pattern> → first "file:line" hit, or empty string.
grep_first() {
  local pattern="$1"
  if [ ${#SEARCH_ROOTS[@]} -eq 0 ]; then
    return 0
  fi
  # -r recursive, -n line numbers, -I skip binary, -F fixed-string (no regex).
  grep -rnIF -- "$pattern" "${SEARCH_ROOTS[@]}" 2>/dev/null \
    | head -1 \
    | awk -F: '{print $1":"$2}'
}

for host in "${PAID_FONT_HOSTS[@]}"; do
  hit="$(grep_first "$host" || true)"
  if [ -n "$hit" ]; then
    FONT_FINDINGS+=("$host|$hit")
  fi
done

for family in "${COMMERCIAL_FONT_FAMILIES[@]}"; do
  hit="$(grep_first "$family" || true)"
  if [ -n "$hit" ]; then
    FONT_FINDINGS+=("$family|$hit")
  fi
done

FONT_JOINED="$(IFS=$'\n'; echo "${FONT_FINDINGS[*]:-}")"

OUT="$REF_DIR/paid-features.json"
FONT="$FONT_JOINED" REF_DIR="$REF_DIR" OUT="$OUT" node -e '
const fs = require("fs");
const path = require("path");

const refDir = process.env.REF_DIR;
const out = process.env.OUT;

function parseLines(raw) {
  if (!raw) return [];
  return raw.split("\n").filter(Boolean).map(line => {
    const idx = line.indexOf("|");
    return idx < 0
      ? { name: line, evidence: "" }
      : { name: line.slice(0, idx), evidence: line.slice(idx + 1) };
  });
}

// Evidence is "file:line"; relativise the file portion to ref-dir.
function relEvidence(ev) {
  if (!ev) return "";
  const lastColon = ev.lastIndexOf(":");
  const file = lastColon >= 0 ? ev.slice(0, lastColon) : ev;
  const lineno = lastColon >= 0 ? ev.slice(lastColon) : "";
  return path.relative(refDir, file) + lineno;
}

const fonts = parseLines(process.env.FONT).map(({name, evidence}) => ({
  family: null,  // unknown from URL alone — agent must inspect to identify
  cdn: name,
  evidence: relEvidence(evidence),
  decision: null,
}));

// Schema note: file is named paid-features.json, not paid-fonts.json, so
// other paid-feature categories can be added without renaming the artifact
// or changing the gate-key. paidSdks / paidAssets stubs signal intent;
// today the script only populates paidFonts.
const data = {
  scannedAt: new Date().toISOString(),
  paidFonts: fonts,
  paidSdks: [],
  paidAssets: [],
};

fs.writeFileSync(out, JSON.stringify(data, null, 2) + "\n");
console.log("Wrote " + out);
console.log("  paidFonts: " + fonts.length);
if (fonts.length > 0) {
  console.log("");
  console.log("Each entry has decision=null. Set decision to one of:");
  console.log("  use        — license is in place");
  console.log("  substitute — use a free alternative (back with asset-substitution.json)");
  console.log("  skip       — intentionally not replicating");
  console.log("Then re-run: python -m ui_clone.gate " + refDir + " paid-features");
}
'

exit 0
