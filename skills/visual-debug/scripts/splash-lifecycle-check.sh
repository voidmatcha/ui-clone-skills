#!/usr/bin/env bash
# splash-lifecycle-check.sh — Verify first-load splash mount, motion, and exit.
#
# Usage: splash-lifecycle-check.sh <session> <ref-url> <impl-url> <ref-dir>

set -uo pipefail

SESSION="${1:-}"
REF_URL="${2:-}"
IMPL_URL="${3:-}"
REF_DIR="${4:-}"

if [ -z "$SESSION" ] || [ -z "$REF_URL" ] || [ -z "$IMPL_URL" ] || [ -z "$REF_DIR" ]; then
  echo "Usage: splash-lifecycle-check.sh <session> <ref-url> <impl-url> <ref-dir>" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROBE_JS="$SCRIPT_DIR/lib/splash-lifecycle-probe.js"
OUT="$REF_DIR/splash-lifecycle.json"
WAIT_MS="${UI_CLONE_SPLASH_LIFECYCLE_WAIT_MS:-4500}"
MIN_WAIT_MS=500
MAX_WAIT_MS=30000

write_artifact() {
  local status="$1" reason="$2"
  python3 - "$OUT" "$status" "$reason" <<'PY'
import json, sys
from pathlib import Path
out, status, reason = sys.argv[1:4]
Path(out).write_text(json.dumps({
    "schemaVersion": 1,
    "status": status,
    "reason": reason,
    "violations": [reason] if status != "pass" else [],
}, indent=2) + "\n", encoding="utf-8")
PY
}

mkdir -p "$REF_DIR"
if [ ! -f "$PROBE_JS" ]; then
  write_artifact fail "probe-script-missing"
  exit 1
fi
if [[ ! "$WAIT_MS" =~ ^[0-9]{1,5}$ ]]; then
  write_artifact fail "invalid-wait-ms"
  exit 1
fi
if [ "$WAIT_MS" -lt "$MIN_WAIT_MS" ] || [ "$WAIT_MS" -gt "$MAX_WAIT_MS" ]; then
  write_artifact fail "wait-ms-out-of-range"
  exit 1
fi
if ! command -v agent-browser >/dev/null 2>&1; then
  write_artifact fail "agent-browser-missing"
  exit 1
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/splash-lifecycle.XXXXXX")" || exit 2
trap 'rm -rf "$TMP_DIR"; agent-browser --session "$SESSION-ref" close >/dev/null 2>&1 || true; agent-browser --session "$SESSION-impl" close >/dev/null 2>&1 || true' EXIT
INIT_JS="$TMP_DIR/splash-lifecycle-init.js"
{
  printf 'window.__UI_CLONE_SPLASH_LIFECYCLE_WINDOW_MS__ = %s;\n' "$WAIT_MS"
  cat "$PROBE_JS"
} > "$INIT_JS"

capture_side() {
  local side="$1" url="$2" session="$3" raw="$4"
  agent-browser --session "$session" close >/dev/null 2>&1 || true
  if ! agent-browser --session "$session" --init-script "$INIT_JS" open "$url" >/dev/null 2>&1; then
    printf '{"schemaVersion":1,"status":"error","side":"%s","reason":"open-failed"}\n' "$side" > "$raw"
    return 1
  fi
  agent-browser --session "$session" wait "$WAIT_MS" >/dev/null 2>&1 || true
  if ! agent-browser --session "$session" eval --json '(() => window.__uiCloneSplashLifecycleResult ? window.__uiCloneSplashLifecycleResult() : {schemaVersion:1,status:"error",reason:"init-script-did-not-run",samples:[]})()' > "$raw" 2>/dev/null; then
    printf '{"schemaVersion":1,"status":"error","side":"%s","reason":"eval-failed"}\n' "$side" > "$raw"
    return 1
  fi
  return 0
}

REF_RAW="$TMP_DIR/ref.raw.json"
IMPL_RAW="$TMP_DIR/impl.raw.json"
capture_side ref "$REF_URL" "$SESSION-ref" "$REF_RAW" || true
capture_side impl "$IMPL_URL" "$SESSION-impl" "$IMPL_RAW" || true

node - "$PROBE_JS" "$REF_RAW" "$IMPL_RAW" "$OUT" "$REF_URL" "$IMPL_URL" <<'NODE'
const fs = require("fs");
const probe = require(process.argv[2]);
const refRaw = process.argv[3];
const implRaw = process.argv[4];
const out = process.argv[5];
const refUrl = process.argv[6];
const implUrl = process.argv[7];

function unwrap(value) {
  let current = value;
  for (let i = 0; i < 5; i += 1) {
    if (typeof current === "string") {
      current = JSON.parse(current);
      continue;
    }
    if (current && typeof current === "object") {
      if (current.data && Object.prototype.hasOwnProperty.call(current.data, "result")) {
        current = current.data.result;
        continue;
      }
      if (Object.prototype.hasOwnProperty.call(current, "result")) {
        current = current.result;
        continue;
      }
    }
    break;
  }
  return current;
}

function readCapture(path) {
  try {
    return unwrap(JSON.parse(fs.readFileSync(path, "utf8")));
  } catch (error) {
    return {schemaVersion: 1, status: "error", reason: "capture-parse-failed", samples: []};
  }
}

const refCapture = readCapture(refRaw);
const implCapture = readCapture(implRaw);
const verdict = probe.compareLifecycles(refCapture.samples || [], implCapture.samples || []);
if (refCapture.status === "error") verdict.violations.push(`ref-${refCapture.reason || "capture-error"}`);
if (implCapture.status === "error") verdict.violations.push(`impl-${implCapture.reason || "capture-error"}`);
verdict.status = verdict.violations.length ? "fail" : "pass";
verdict.refUrl = refUrl;
verdict.implUrl = implUrl;
verdict.refCapture = refCapture;
verdict.implCapture = implCapture;
fs.writeFileSync(out, JSON.stringify(verdict, null, 2) + "\n");
process.exit(verdict.status === "pass" ? 0 : 1);
NODE
