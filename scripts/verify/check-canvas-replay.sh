#!/usr/bin/env bash
# check-canvas-replay.sh — canonical stamp writer for the canvas-replay
# closeout policy (v0.7.0).
#
# Validates the operator-written canvas-replay-attestation.json against
# its required-field contract, then (optionally) writes canvas-replay-stamp.json
# so the Stop hook accepts the canvas-replay closeout proof. Mirrors the
# shape of scripts/verify/check-converged.sh for the structural policy.
#
# Exit codes:
#   0  attestation valid; stamp written (if --write-stamp)
#   1  attestation invalid (missing file, bad shape, missing required field)
#   2  setup error (missing args, missing ref dir)
#
# Usage:
#   bash scripts/verify/check-canvas-replay.sh <ref-dir> [--write-stamp]
#
# Attestation contract (review 2026-05-25 item [2]):
#   <ref-dir>/canvas-replay-attestation.json must be operator-written before
#   this script runs. Required fields:
#     - license:           URL or text of source's license / owner permission
#     - disclaimer:        Non-affiliation statement + canvas-loading disclosure
#     - attestedBy:        Operator handle
#     - attestedAt:        ISO 8601 UTC timestamp
#     - ref_canvas_sources: Non-empty array of canvas-driving JS URLs
#
# Stamp contract (review item [5]):
#   <ref-dir>/canvas-replay-stamp.json carries sha256(attestation) so the
#   Stop hook detects tampering with the license/disclaimer/ref_canvas_sources
#   after the stamp was written.
#
# Scope boundary (review item [8]):
#   Canvas-replay is for <canvas> pixel-fidelity only. WebAudio output, video
#   replay, DOM replay, and non-canvas asset bypasses are explicitly OUT of
#   scope. See skills/ui-reverse-engineering/canvas-replay-mode.md.

set -u

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 <ref-dir> [--write-stamp]" >&2
  exit 2
fi

REF_DIR="$1"
WRITE_STAMP="false"
if [ "${2:-}" = "--write-stamp" ]; then
  WRITE_STAMP="true"
fi

if [ ! -d "$REF_DIR" ]; then
  echo "check-canvas-replay: ref dir not found: $REF_DIR" >&2
  exit 2
fi

ATTESTATION="$REF_DIR/canvas-replay-attestation.json"
STAMP="$REF_DIR/canvas-replay-stamp.json"

if [ ! -f "$ATTESTATION" ]; then
  echo "check-canvas-replay: attestation missing: $ATTESTATION" >&2
  echo "Create the file first — see skills/ui-reverse-engineering/canvas-replay-mode.md" >&2
  exit 1
fi

# Validate attestation shape via python (jq not guaranteed on the host).
python3 - "$ATTESTATION" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except json.JSONDecodeError as e:
    print(f"check-canvas-replay: attestation is not valid JSON: {e}", file=sys.stderr)
    sys.exit(1)

if not isinstance(data, dict):
    print("check-canvas-replay: attestation must be a JSON object", file=sys.stderr)
    sys.exit(1)

required = ("license", "disclaimer", "attestedBy", "attestedAt", "ref_canvas_sources")
missing = [k for k in required if not data.get(k)]
if missing:
    print(
        f"check-canvas-replay: attestation missing required fields: {missing}",
        file=sys.stderr,
    )
    sys.exit(1)

sources = data["ref_canvas_sources"]
if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
    print(
        "check-canvas-replay: ref_canvas_sources must be a non-empty array of strings",
        file=sys.stderr,
    )
    sys.exit(1)

if not sources:
    print(
        "check-canvas-replay: ref_canvas_sources is empty — at least one URL required",
        file=sys.stderr,
    )
    sys.exit(1)

# Scope guard (review item [8]): refuse attestations that declare
# non-canvas sources. Heuristic — looking for media-type indicators in URLs.
# Operators can still attest legitimate sources by avoiding misleading suffixes.
banned_substrings = (
    ".mp3", ".wav", ".ogg",   # audio
    ".mp4", ".webm", ".mov",  # video
    ".m3u8", ".mpd",          # streaming manifests
)
out_of_scope = [
    s for s in sources
    if any(b in s.lower() for b in banned_substrings)
]
if out_of_scope:
    print(
        f"check-canvas-replay: ref_canvas_sources contains out-of-scope URLs "
        f"(audio/video extensions): {out_of_scope}\n"
        f"Canvas-replay scope is canvas pixel-fidelity ONLY — see\n"
        f"skills/ui-reverse-engineering/canvas-replay-mode.md § scope boundary.",
        file=sys.stderr,
    )
    sys.exit(1)
PY

VALIDATION_RC=$?
if [ "$VALIDATION_RC" -ne 0 ]; then
  exit "$VALIDATION_RC"
fi

if [ "$WRITE_STAMP" = "false" ]; then
  echo "check-canvas-replay: attestation valid; --write-stamp not requested" >&2
  exit 0
fi

# Write the stamp via python — captures attestation sha256 + extracts
# fields the Stop hook compares.
python3 - "$ATTESTATION" "$STAMP" <<'PY'
import datetime
import hashlib
import json
import sys
from pathlib import Path

att_path = Path(sys.argv[1])
stamp_path = Path(sys.argv[2])

att_bytes = att_path.read_bytes()
att_sha = hashlib.sha256(att_bytes).hexdigest()
att_data = json.loads(att_bytes)

stamp = {
    "schemaVersion": 1,
    "closeoutKind": "canvas-replay",
    "stampedBy": "scripts/verify/check-canvas-replay.sh",
    "verifiedAt": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "attestationSha256": att_sha,
    "refCanvasSources": att_data.get("ref_canvas_sources", []),
    "attestedBy": att_data.get("attestedBy", ""),
    "attestedAt": att_data.get("attestedAt", ""),
}
stamp_path.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
print(f"check-canvas-replay: stamp written to {stamp_path}", file=sys.stderr)
PY
