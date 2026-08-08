#!/usr/bin/env bash
# capacity-check.sh — materialize machine capacity evidence for verification dispatch.
# Usage: capacity-check.sh <ref-dir>
set -euo pipefail
REF_DIR="${1:?Usage: capacity-check.sh <ref-dir>}"
[ -d "$REF_DIR" ] || { echo "capacity-check: ref-dir not found: $REF_DIR" >&2; exit 2; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/capacity.py" --out "$REF_DIR/capacity-report.json" >/dev/null
python3 - "$REF_DIR/capacity-report.json" <<'PY'
from __future__ import annotations
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
data.setdefault("status", "pass")
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"status": data.get("status"), "recommendedWaveSize": data.get("recommendedWaveSize"), "out": str(path)}))
PY
