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

# This check is dispatched by detectors that are deliberately biased toward
# false positives (splash-extraction.md: `hasPreloader` fires on any one of
# three signals) and by `summary.json polls > 1`. When the reference mounts no
# overlay the probe can see, `ref-overlay-absent` describes the reference, not
# the implementation, and no implementation change can clear it. It stays a
# FAIL. The Phase A certificate (states/splash/contract.json
# capture.authoritativeNegative) is read here only to say WHICH reference
# measurement the FAIL is about - never to clear it: the certificate is derived
# by a sampler that, like this probe, enumerates elements
# (`document.querySelectorAll("body *")` here, the rendered-element walk in
# capture-states.sh there) and reads `getComputedStyle(el)` with no
# pseudo-element argument. Both are blind to the same curtains - an
# `html:not(.loaded)::before` overlay, a body background hiding
# opacity-gated content, a canvas - so two negatives from them are one blind
# spot counted twice, not corroboration. When the certificate is true the
# generic dispatches (polls > 1, page-load trigger) were already vetoed in
# verification-plan.sh; a check that still ran was dispatched by a detector
# that read bundle source or a DOM diff, and ui_clone.splash_contract does not
# let the certificate override those.
#
# The repo root goes AHEAD of sys.path[0] (the working directory, for
# `python3 -c`): a `ui_clone/` package in the caller's cwd must not be able to
# answer this question, because the answer is written into the artifact.
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
REF_ABSENCE_CERTIFIED="$(python3 -c '
import json, sys
sys.path.insert(0, sys.argv[1])
from ui_clone.splash_contract import is_authoritative_absence
try:
    data = json.loads(open(sys.argv[2], encoding="utf-8").read())
except Exception:
    data = None
print("true" if is_authoritative_absence(data) else "false")
' "$ROOT_DIR" "$REF_DIR/states/splash/contract.json" 2>/dev/null || echo false)"

node - "$PROBE_JS" "$REF_RAW" "$IMPL_RAW" "$OUT" "$REF_URL" "$IMPL_URL" "$REF_ABSENCE_CERTIFIED" <<'NODE'
const fs = require("fs");
const probe = require(process.argv[2]);
const refRaw = process.argv[3];
const implRaw = process.argv[4];
const out = process.argv[5];
const refUrl = process.argv[6];
const implUrl = process.argv[7];
const refAbsenceCertified = process.argv[8] === "true";

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

// The reference measured no overlay. Only a reference that was actually
// sampled counts as measured; an open/eval failure has no samples and is
// handled by the capture-error violations above. `ref-overlay-absent` stays in
// the violations either way: the annotation below names the reference
// measurement the FAIL is about and what would resolve it, and nothing here
// removes a violation or changes the status.
const refMeasuredAbsent = refCapture.status !== "error" && verdict.ref.sampleCount > 0 && !verdict.ref.mounted;
if (refMeasuredAbsent) {
  const probeStatement = `the lifecycle probe saw no fixed/absolute overlay covering >= ${Math.round(probe.MIN_AREA_RATIO * 100)}% of the viewport on the reference across ${verdict.ref.sampleCount} samples`;
  if (refAbsenceCertified) {
    verdict.refAbsence = {
      certified: true,
      source: "states/splash/contract.json capture.authoritativeNegative",
      guidance: `${probeStatement}, and the Phase A capture also certified absence over the whole first load. Both instruments enumerate elements and are blind to the same curtains (a pseudo-element such as html:not(.loaded)::before, a body background over opacity-gated content, a canvas), so agreement between them is not corroboration and does not clear this FAIL. This is a reference measurement, not an implementation defect: this check was dispatched by a detector that read bundle source or a DOM diff (the hasPreloader signal the interactions detector writes into the interactions-detected artifact, splash-extraction artifacts), so inspect that detector's evidence for the loader it saw - and if the reference really has no splash, that dispatch signal is what to correct.`,
    };
  } else {
    // The Phase A capture timed out, saw a loading lifecycle the probe cannot
    // classify (root class removed, covering element leaving, DOM shift),
    // predates the covering survey, or was attached after navigation.
    verdict.refAbsence = {
      certified: false,
      source: "states/splash/contract.json capture.authoritativeNegative",
      guidance: `${probeStatement}, but states/splash/contract.json does not certify absence, so the reference may hold a splash this probe cannot classify. This is a reference measurement, not an implementation defect: re-run scripts/extract/capture-states.sh against the reference so the certificate is derived from the current sampler, then check capture.absenceEvidence for the channel that refused it.`,
    };
  }
}
verdict.status = verdict.violations.length ? "fail" : "pass";
verdict.refUrl = refUrl;
verdict.implUrl = implUrl;
verdict.refCapture = refCapture;
verdict.implCapture = implCapture;
fs.writeFileSync(out, JSON.stringify(verdict, null, 2) + "\n");
process.exit(verdict.status === "pass" ? 0 : 1);
NODE
