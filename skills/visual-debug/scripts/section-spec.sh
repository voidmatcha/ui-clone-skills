#!/usr/bin/env bash
# section-spec.sh — LLM-driven per-section grounded spec generator.
#
# Usage:
#   section-spec.sh <ref-png> [--metadata <json>] [--text <verbatim>] \
#                   [--out <json-path>] [--label <name>]
#
# Calls `claude --print` with the section-spec prompt template, passing the
# section's ref PNG + optional metadata/text. Sub-agent reads the PNG and
# emits a strict JSON spec that downstream Phase-4 generation can follow
# verbatim — eliminating the agent's tendency to fabricate text/colors/
# typography from class names and URLs.
#
# This is Phase 2.6 — "front-load the determinism". Run AFTER extraction
# (Phase 2) and asset transfer (Phase 2.5), BEFORE component generation
# (Phase 4). The spec is the primary input to Phase 4, not the lossy
# extracted JSON dumps.
set -euo pipefail

REF_PNG=""
OUT_PATH=""
LABEL="section"
METADATA_JSON=""
DIRECT_TEXT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)
      OUT_PATH="$2"
      shift 2
      ;;
    --label)
      LABEL="$2"
      shift 2
      ;;
    --metadata)
      METADATA_JSON="$2"
      shift 2
      ;;
    --text)
      DIRECT_TEXT="$2"
      shift 2
      ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *)
      if [[ -z "$REF_PNG" ]]; then
        REF_PNG="$1"
      else
        echo "section-spec: unexpected arg: $1" >&2
        exit 2
      fi
      shift
      ;;
  esac
done

if [[ -z "$REF_PNG" ]]; then
  echo "usage: section-spec.sh <ref-png> [--metadata <json>] [--text <verbatim>] [--out <json-path>] [--label <name>]" >&2
  exit 2
fi
if [[ ! -f "$REF_PNG" ]]; then
  echo "section-spec: ref PNG not found: $REF_PNG" >&2
  exit 2
fi

REF_ABS="$(cd "$(dirname "$REF_PNG")" && pwd)/$(basename "$REF_PNG")"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPT_PATH="$SCRIPT_DIR/../prompts/section-spec.md"
if [[ ! -f "$PROMPT_PATH" ]]; then
  echo "section-spec: prompt template not found: $PROMPT_PATH" >&2
  exit 2
fi
PROMPT_BODY="$(cat "$PROMPT_PATH")"

# Build the prompt. Metadata and direct text are optional context; when
# present they ground the spec further (e.g., direct text from the DOM lets
# the LLM verify what it reads in the PNG instead of guessing word boundaries
# in low-contrast crops).
EXTRA=""
if [[ -n "$METADATA_JSON" ]]; then
  EXTRA="$EXTRA

Section metadata (from Phase 2 DOM extraction):
$METADATA_JSON"
fi
if [[ -n "$DIRECT_TEXT" ]]; then
  EXTRA="$EXTRA

Direct text from this section's DOM (use as ground truth, prefer over OCR of the PNG when they conflict):
$DIRECT_TEXT"
fi

PROMPT="$PROMPT_BODY

---

REF clip path: $REF_ABS
LABEL: $LABEL$EXTRA

Read the REF PNG with the Read tool now, then emit ONLY the JSON object specified by the schema. No prose."

if ! command -v claude >/dev/null 2>&1; then
  echo "section-spec: 'claude' CLI not found on PATH" >&2
  exit 3
fi

RESPONSE="$(claude --print --permission-mode auto "$PROMPT")"

# Extract first balanced JSON object.
JSON="$(printf '%s' "$RESPONSE" | python3 -c '
import sys, re
t = sys.stdin.read()
m = re.search(r"\{[\s\S]*\}", t)
print(m.group(0) if m else t)
')"

# Validate.
if ! printf '%s' "$JSON" | python3 -c 'import json, sys; json.loads(sys.stdin.read())' >/dev/null 2>&1; then
  echo "section-spec: response was not valid JSON" >&2
  echo "--- raw response (first 600 chars) ---" >&2
  printf '%s' "$RESPONSE" | head -c 600 >&2
  echo >&2
  exit 1
fi

if [[ -n "$OUT_PATH" ]]; then
  mkdir -p "$(dirname "$OUT_PATH")"
  printf '%s\n' "$JSON" > "$OUT_PATH"
  echo "section-spec: wrote $OUT_PATH"
else
  printf '%s\n' "$JSON"
fi
