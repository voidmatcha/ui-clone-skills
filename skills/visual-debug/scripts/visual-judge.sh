#!/usr/bin/env bash
# visual-judge.sh — LLM-as-judge for ref-vs-impl per-section visual comparison.
#
# Usage:
#   visual-judge.sh <ref-png> <impl-png> [--out <json-path>] [--label <name>]
#
# Calls `claude --print` with the visual-judge prompt template, passing the two
# PNG paths. Sub-agent reads both PNGs and emits a strict JSON findings object.
# Prints JSON to stdout (or writes to --out if provided). Exits non-zero on
# invalid/empty response.
#
# This is the early-iteration signal: AE/SSIM gives precision late, this gives
# direction early when AE is uniformly catastrophic.
set -euo pipefail

# Codex review (2026-05-24/25): wrap `claude --print` in a Python timeout
# (subprocess.Popen + start_new_session + killpg). Multimodal LLM calls can
# hang indefinitely (network stall, model wedged); the unbounded version
# burned vision budget on stuck calls. Default 5 min covers normal latency
# (30s–2min); override via VISUAL_JUDGE_TIMEOUT_SEC.
: "${VISUAL_JUDGE_TIMEOUT_SEC:=300}"
_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
_RUN_WITH_TIMEOUT="${_SCRIPT_DIR}/../../../scripts/lib/run_with_timeout.py"

REF_PNG=""
IMPL_PNG=""
OUT_PATH=""
LABEL="section"

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
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *)
      if [[ -z "$REF_PNG" ]]; then
        REF_PNG="$1"
      elif [[ -z "$IMPL_PNG" ]]; then
        IMPL_PNG="$1"
      else
        echo "visual-judge: unexpected arg: $1" >&2
        exit 2
      fi
      shift
      ;;
  esac
done

if [[ -z "$REF_PNG" || -z "$IMPL_PNG" ]]; then
  echo "usage: visual-judge.sh <ref-png> <impl-png> [--out <json-path>] [--label <name>]" >&2
  exit 2
fi

if [[ ! -f "$REF_PNG" ]]; then
  echo "visual-judge: ref PNG not found: $REF_PNG" >&2
  exit 2
fi
if [[ ! -f "$IMPL_PNG" ]]; then
  echo "visual-judge: impl PNG not found: $IMPL_PNG" >&2
  exit 2
fi

# Absolute paths so the sub-agent's Read tool resolves them regardless of its cwd.
REF_ABS="$(cd "$(dirname "$REF_PNG")" && pwd)/$(basename "$REF_PNG")"
IMPL_ABS="$(cd "$(dirname "$IMPL_PNG")" && pwd)/$(basename "$IMPL_PNG")"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPT_PATH="$SCRIPT_DIR/../prompts/visual-judge.md"
if [[ ! -f "$PROMPT_PATH" ]]; then
  echo "visual-judge: prompt template not found: $PROMPT_PATH" >&2
  exit 2
fi
PROMPT_BODY="$(cat "$PROMPT_PATH")"

PROMPT="$PROMPT_BODY

---

REF path: $REF_ABS
IMPL path: $IMPL_ABS
LABEL: $LABEL

Read both PNGs with the Read tool now, then emit ONLY the JSON object specified by the schema. No prose."

# Call claude --print. --permission-mode auto so it can Read the PNG paths
# without interactive approval.
if ! command -v claude >/dev/null 2>&1; then
  echo "visual-judge: 'claude' CLI not found on PATH" >&2
  exit 3
fi

# Codex review: `if ! VAR=$(...)` flips the exit code via `!`, swallowing
# 124 (the timeout signal). Use `|| { ... }` so the actual exit code lands
# in $? cleanly.
RESPONSE="$(python3 "$_RUN_WITH_TIMEOUT" "$VISUAL_JUDGE_TIMEOUT_SEC" claude --print --permission-mode auto "$PROMPT")" || {
  rc=$?
  if [[ $rc -eq 124 ]]; then
    echo "visual-judge: 'claude --print' exceeded VISUAL_JUDGE_TIMEOUT_SEC=${VISUAL_JUDGE_TIMEOUT_SEC}s and was process-group-killed" >&2
  else
    echo "visual-judge: 'claude --print' failed with exit code $rc" >&2
  fi
  exit "$rc"
}

# Extract first balanced JSON object from response. The sub-agent SHOULD emit
# only JSON, but defensively strip any prose around it.
JSON="$(printf '%s' "$RESPONSE" | python3 -c '
import sys, re
t = sys.stdin.read()
m = re.search(r"\{[\s\S]*\}", t)
print(m.group(0) if m else t)
')"

# Validate.
if ! printf '%s' "$JSON" | python3 -c 'import json, sys; json.loads(sys.stdin.read())' >/dev/null 2>&1; then
  echo "visual-judge: response was not valid JSON" >&2
  echo "--- raw response (first 600 chars) ---" >&2
  printf '%s' "$RESPONSE" | head -c 600 >&2
  echo >&2
  exit 1
fi

if [[ -n "$OUT_PATH" ]]; then
  mkdir -p "$(dirname "$OUT_PATH")"
  printf '%s\n' "$JSON" > "$OUT_PATH"
  echo "visual-judge: wrote $OUT_PATH"
else
  printf '%s\n' "$JSON"
fi
