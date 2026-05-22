#!/usr/bin/env bash
# invalidation-check.sh — fail when the ref dir was explicitly marked
# invalid via .invalidated stamp.
#
#
# Stamp format: `<ref-dir>/.invalidated` is a JSON file with shape
#   { "reason": "...", "markedAt": ISO8601, "markedBy": "..." }
# A bare empty `.invalidated` file is also honored (reason defaults
# to "marked invalid").
#
# Usage:
#   invalidation-check.sh <ref-dir>
#
# Output: <ref-dir>/invalidation.json
#
# Exit: 0 pass (no stamp), 1 fail (stamp present), 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"
if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: invalidation-check.sh <ref-dir>" >&2
  exit 2
fi

OUT_PATH="$REF_DIR/invalidation.json"
STAMP="$REF_DIR/.invalidated"

if [ ! -e "$STAMP" ]; then
  cat > "$OUT_PATH" <<JSON
{
  "schemaVersion": 1,
  "status": "pass",
  "stampPresent": false
}
JSON
  echo "invalidation: pass (no .invalidated stamp)"
  exit 0
fi

python3 - "$STAMP" "$OUT_PATH" <<'PY'
import json
import sys
from pathlib import Path

stamp_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])

reason = "marked invalid"
marked_at = None
marked_by = None
raw = ""
try:
    raw = stamp_path.read_text(encoding="utf-8", errors="ignore").strip()
    if raw:
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                reason = str(payload.get("reason") or reason)
                marked_at = payload.get("markedAt")
                marked_by = payload.get("markedBy")
            else:
                reason = raw[:300]
        except ValueError:
            reason = raw[:300]
except OSError:
    pass

out_path.write_text(json.dumps({
    "schemaVersion": 1,
    "status": "fail",
    "stampPresent": True,
    "stampPath": str(stamp_path),
    "reason": reason,
    "markedAt": marked_at,
    "markedBy": marked_by,
    "rule": (
        "<ref-dir>/.invalidated stamp marks this ref as a known-bad "
        "clone result (e.g. caught cheating after the fact). Gates "
        "refuse to pass until the stamp is removed and the underlying "
        "issue is genuinely fixed."
    ),
}, indent=2) + "\n", encoding="utf-8")
print(f"invalidation: FAIL — {reason[:120]}")
sys.exit(1)
PY
