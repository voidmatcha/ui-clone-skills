#!/usr/bin/env bash
# inline-scripts.sh — materialize inline <script> bodies into bundles/
#
# resource-mirror.sh enumerates `script[src]`, and download-chunks.sh fetches
# those URLs, so a site that declares its motion inside an inline <script> ships
# no bundle evidence at all. Measured on webflow.com: 32 inline scripts, ~89 KB,
# carrying gsap.timeline(), .to() tweens and ScrollTrigger.create() — none of
# which any extractor could see.
#
# Writing each body to bundles/inline-<n>.js means every existing extractor
# picks them up with no other change, and _find_file_for_offset attributes
# matches to a real filename.
#
# Usage: inline-scripts.sh <session> <ref-dir>
#
# Output:
#   <ref-dir>/bundles/inline-<n>.js   — one file per executable inline script
#   <ref-dir>/inline-scripts.json     — {count, totalBytes, files[], skipped[]}
#
# Exit: 0 ok (including "no inline scripts"), 2 usage/setup, 3 eval failure
set -euo pipefail

SESSION="${1:-}"
REF_DIR="${2:-}"
if [ -z "$SESSION" ] || [ -z "$REF_DIR" ]; then
  echo "Usage: inline-scripts.sh <session> <ref-dir>" >&2
  exit 2
fi
[ -d "$REF_DIR" ] || { echo "inline-scripts: ref-dir not found: $REF_DIR" >&2; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORIGIN_VALIDATOR="$SCRIPT_DIR/validate-agent-browser-origin.py"
EVAL_JS_FILE="$SCRIPT_DIR/inline-scripts-eval.js"
[ -f "$EVAL_JS_FILE" ] || { echo "inline-scripts: missing $EVAL_JS_FILE" >&2; exit 2; }

RESPONSE_TMP="$(mktemp -t inline-scripts-resp.XXXX)"
trap 'rm -f "${RESPONSE_TMP:-}"' EXIT

if ! agent-browser --session "$SESSION" eval --json --stdin < "$EVAL_JS_FILE" > "$RESPONSE_TMP" 2>&1; then
  echo "inline-scripts: agent-browser eval failed (session=$SESSION)" >&2
  head -c 400 "$RESPONSE_TMP" >&2
  exit 3
fi
if ! python3 "$ORIGIN_VALIDATOR" < "$RESPONSE_TMP"; then
  echo "inline-scripts: agent-browser eval returned a non-page origin (session=$SESSION)" >&2
  exit 3
fi

python3 "$SCRIPT_DIR/_inline_scripts.py" "$REF_DIR" "$RESPONSE_TMP"
