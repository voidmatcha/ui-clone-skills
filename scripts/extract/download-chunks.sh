#!/usr/bin/env bash
# download-chunks.sh — Download ALL JS chunks and produce initial analysis
# Usage: bash scripts/extract/download-chunks.sh <component-dir> <url-list-file>
#   url-list-file: one URL per line (output from agent-browser eval)
# Or pipe URLs:
#   echo '["https://...js","https://...js"]' | bash scripts/extract/download-chunks.sh <component-dir> -
#
# Output:
#   <component-dir>/bundles/*.js          — downloaded chunks
#   <component-dir>/bundle-analysis.json  — per-chunk analysis (libraries, selectors, transitions)
#   <component-dir>/bundle-map.json       — skeleton chunk → feature mapping

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../lib/time-ms.sh"

START_TIME=$(ui_clone_now_ms)

DIR="${1:?Usage: download-chunks.sh <component-dir> <url-list-file>}"
INPUT="${2:--}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

mkdir -p "$DIR/bundles"

# ─────────────────────────────────────────────
# 1. Parse URL list (JSON array or newline-separated)
# ─────────────────────────────────────────────
URLS=()
if [ "$INPUT" = "-" ]; then
  RAW=$(cat)
else
  RAW=$(cat "$INPUT")
fi

# Try JSON array first, fall back to newline-separated
if echo "$RAW" | jq -e '.[0]' > /dev/null 2>&1; then
  while IFS= read -r url; do
    URLS+=("$url")
  done < <(echo "$RAW" | jq -r '.[]')
else
  while IFS= read -r url; do
    [ -n "$url" ] && URLS+=("$url")
  done <<< "$RAW"
fi

# Filter out analytics/tracking
FILTERED=()
for url in "${URLS[@]}"; do
  if [[ "$url" =~ (cloudflare|iubenda|analytics|gtag|gtm|hotjar|facebook|sentry) ]]; then
    continue
  fi
  if [[ "$url" =~ ^https:// ]]; then
    FILTERED+=("$url")
  fi
done

echo -e "${GREEN}Found ${#FILTERED[@]} JS chunks to download${NC}"

# ─────────────────────────────────────────────
# 2. Download all chunks
# ─────────────────────────────────────────────
DOWNLOADED=0
FAILED=0
for url in "${FILTERED[@]}"; do
  FILENAME=$(basename "$url" | sed 's/?.*//')
  if curl -s --max-time 30 --max-filesize 10485760 --fail --location \
    -o "$DIR/bundles/$FILENAME" -- "$url" 2>/dev/null; then
    SIZE=$(wc -c < "$DIR/bundles/$FILENAME" | tr -d ' ')
    if [ "$SIZE" -lt 100 ]; then
      echo -e "${YELLOW}⚠️  $FILENAME — ${SIZE}B (suspiciously small)${NC}"
    else
      echo -e "${GREEN}✅${NC} $FILENAME (${SIZE}B)"
    fi
    DOWNLOADED=$((DOWNLOADED + 1))
  else
    echo -e "${RED}❌${NC} Failed: $FILENAME"
    FAILED=$((FAILED + 1))
  fi
done

echo ""
echo -e "Downloaded: $DOWNLOADED | Failed: $FAILED"

# ─────────────────────────────────────────────
# 3. Analyze each chunk for animation libraries + transition selectors
# ─────────────────────────────────────────────
echo ""
echo "═══ Analyzing chunks ═══"

# Animation library patterns
LIBS="gsap|ScrollTrigger|ScrollSmoother|Lenis|locomotive|Flip|SplitText|motion\.|framer-motion|anime\."
# Transition-related patterns
TRANS="clipPath|xPercent|yPercent|autoAlpha|stagger|fromTo|scrollTrigger|\.to\(|\.from\("
# Selector patterns near animations
SELS='"[.#][a-zA-Z][^"]{2,40}"'

ANALYSIS="[]"

for f in "$DIR"/bundles/*.js; do
  [ -f "$f" ] || continue
  FNAME=$(basename "$f")
  SIZE=$(wc -c < "$f" | tr -d ' ')

  # Skip tiny files
  [ "$SIZE" -lt 500 ] && continue

  # Detect libraries
  FOUND_LIBS=$(grep -oE "$LIBS" "$f" 2>/dev/null | sort -u | tr '\n' ',' | sed 's/,$//' || true)

  # Detect transition patterns
  FOUND_TRANS=$(grep -oE "$TRANS" "$f" 2>/dev/null | sort -u | tr '\n' ',' | sed 's/,$//' || true)

  # Extract selectors near animation calls (first 20)
  FOUND_SELS=$(grep -oE "\"[.#][a-zA-Z][^\"]*\"[^;]{0,100}(duration|ease|stagger|clipPath|yPercent|opacity)" "$f" 2>/dev/null | head -20 | grep -oE '"[.#][^"]*"' | sort -u | tr '\n' ',' | sed 's/,$//' || true)

  # Sanitization check
  SUSPICIOUS="$(grep -ciE 'eval\(atob|document\.cookie' "$f" 2>/dev/null)" || SUSPICIOUS="0"

  if [ -n "$FOUND_LIBS" ] || [ -n "$FOUND_TRANS" ]; then
    echo -e "${GREEN}📦 $FNAME${NC}"
    [ -n "$FOUND_LIBS" ] && echo "   Libraries: $FOUND_LIBS"
    [ -n "$FOUND_TRANS" ] && echo "   Transitions: $FOUND_TRANS"
    [ -n "$FOUND_SELS" ] && echo "   Selectors: $FOUND_SELS"
    [ "$SUSPICIOUS" -gt 0 ] && echo -e "   ${RED}⚠️  Suspicious patterns: $SUSPICIOUS${NC}"
  fi

  # Build JSON entry
  ENTRY=$(jq -n \
    --arg file "$FNAME" \
    --arg size "$SIZE" \
    --arg libs "$FOUND_LIBS" \
    --arg trans "$FOUND_TRANS" \
    --arg sels "$FOUND_SELS" \
    --arg suspicious "$SUSPICIOUS" \
    '{file: $file, size: ($size | tonumber), libraries: ($libs | split(",") | map(select(. != ""))), transitions: ($trans | split(",") | map(select(. != ""))), selectors: ($sels | split(",") | map(select(. != ""))), suspicious: ($suspicious | tonumber)}')

  ANALYSIS=$(echo "$ANALYSIS" | jq --argjson entry "$ENTRY" '. + [$entry]')
done

echo "$ANALYSIS" | jq '.' > "$DIR/bundle-analysis.json"
echo ""
echo -e "${GREEN}✅ Saved bundle-analysis.json${NC}"

# ─────────────────────────────────────────────
# 4. Generate skeleton bundle-map.json
# ─────────────────────────────────────────────
CHUNKS=$(echo "$ANALYSIS" | jq '[.[] | select((.libraries | type == "array" and length > 0) or (.transitions | type == "array" and length > 0)) | {file, contains: (.libraries + .transitions), key_selectors: .selectors}]')

jq -n --argjson chunks "$CHUNKS" '{chunks: $chunks}' > "$DIR/bundle-map.json"
echo -e "${GREEN}✅ Saved bundle-map.json (skeleton — review and enrich manually)${NC}"

echo "" >&2
echo "Next: Review bundle-map.json and create transition-spec.json" >&2

# ── JSON output ──
END_TIME=$(ui_clone_now_ms)
DOWNLOADED=$(find "$DIR/bundles" -name "*.js" 2>/dev/null | python3 -c "import sys,json; print(json.dumps([l.strip() for l in sys.stdin]))" 2>/dev/null || echo "[]")
LIBS=$(python3 -c "import json; d=json.load(open('$DIR/bundle-analysis.json')); libs=set(); [libs.update(e.get('libraries',[])) for e in d]; print(json.dumps(list(libs)))" 2>/dev/null || echo "[]")
cat <<ENDJSON
{
  "status": "pass",
  "phase": "bundle",
  "data": { "files": $DOWNLOADED, "libraries": $LIBS },
  "defects": [],
  "errors": [],
  "duration_ms": $(( END_TIME - START_TIME ))
}
ENDJSON
