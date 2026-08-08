#!/usr/bin/env bash
# capture-artifact-inventory-check.sh — verify every ui-capture transition
# region enumerates the concrete ref artifacts it produced.
#
# Usage: capture-artifact-inventory-check.sh <ref-dir>
#
# Reads:
#   <ref-dir>/regions.json
#
# Writes:
#   <ref-dir>/capture-artifact-inventory.json
#
# Exit:
#   0 pass/skip, 1 fail, 2 setup error

set -euo pipefail

REF_DIR="${1:?Usage: capture-artifact-inventory-check.sh <ref-dir>}"
[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

REGIONS="$REF_DIR/regions.json"
OUT="$REF_DIR/capture-artifact-inventory.json"

python3 - "$REF_DIR" "$REGIONS" "$OUT" <<'PY'
import json
import os
import sys
from pathlib import Path
from typing import Any

ref_dir = Path(sys.argv[1])
regions_path = Path(sys.argv[2])
out_path = Path(sys.argv[3])


def write(payload: dict[str, Any], code: int) -> None:
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    sys.exit(code)


if not regions_path.is_file():
    write(
        {
            "schemaVersion": 1,
            "status": "skip",
            "regionsChecked": 0,
            "checkedArtifacts": [],
            "missingArtifacts": [],
            "reason": "regions.json absent",
        },
        0,
    )

try:
    data = json.loads(regions_path.read_text(encoding="utf-8"))
except json.JSONDecodeError as exc:
    write(
        {
            "schemaVersion": 1,
            "status": "fail",
            "regionsChecked": 0,
            "checkedArtifacts": [],
            "missingArtifacts": [{"region": "regions.json", "reason": f"malformed JSON: {exc}"}],
        },
        1,
    )


def walk_regions(node: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if isinstance(node.get("triggerType"), str):
            out.append(node)
        for value in node.values():
            out.extend(walk_regions(value))
    elif isinstance(node, list):
        for value in node:
            out.extend(walk_regions(value))
    return out


regions = walk_regions(data)
if not regions:
    write(
        {
            "schemaVersion": 1,
            "status": "pass",
            "regionsChecked": 0,
            "checkedArtifacts": [],
            "missingArtifacts": [],
            "reason": "no triggerType regions",
        },
        0,
    )

required_by_trigger = {
    "css-hover": {"idle", "active"},
    "js-class": {"idle", "active"},
    "hover": {"idle", "active"},
    "intersection": {"before", "after"},
    "scroll-driven": {"before", "mid", "after"},
    "click-toggle": {"idle", "active"},
    "click-content-swap": {"video", "idle", "active"},
    "mousemove": {"video"},
    "auto-timer": {"video"},
}

checked: list[dict[str, Any]] = []
missing: list[dict[str, Any]] = []
dispatch_only: list[dict[str, Any]] = []

# FILE-LEVEL provenance gate (id30): a per-region dispatchOnly flag is forgeable —
# an agent can hand-add it to a real capture-needing region to skip its manifest.
# Honor dispatch-only ONLY when the regions.json FILE was deterministically
# projected from transition-spec. Require both the producer source and its
# transition-spec derivation record; either field alone is forgeable. Without
# both, every region owes its capture manifest regardless of a per-region flag.
derived_from = data.get("derivedFrom") if isinstance(data, dict) else None
file_dispatch_provenance = (
    isinstance(data, dict) and data.get("source") == "derive-from-transition-spec"
    and isinstance(derived_from, list)
    and "transition-spec.json" in derived_from
)

for index, region in enumerate(regions):
    name = str(region.get("name") or region.get("selector") or f"region-{index}")
    trigger = str(region.get("triggerType") or "")
    # Dispatch-only regions are deterministic projections of transition-spec
    # (regions.json source: derive-from-transition-spec), NOT independent
    # capture proof. They carry no per-state capture manifest by design, so
    # they are NOT "missing" one — record them distinctly instead of failing.
    # Gated on FILE provenance so a hand-set per-region flag cannot bypass capture.
    if region.get("dispatchOnly") and file_dispatch_provenance:
        dispatch_only.append({"region": name, "triggerType": trigger, "reason": "dispatch-only (spec-derived; not capture-backed)"})
        continue
    artifacts = region.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        missing.append({"region": name, "triggerType": trigger, "reason": "missing artifacts manifest"})
        continue

    if trigger == "click-cycle":
        state_count = int(region.get("stateCount") or 0)
        required = {f"state-{i}" for i in range(state_count)} if state_count > 0 else {"state-0", "state-1"}
    else:
        required = required_by_trigger.get(trigger, set())
        if not required:
            required = set(artifacts)

    for key in sorted(required):
        value = artifacts.get(key)
        if not isinstance(value, str) or not value.strip():
            missing.append({"region": name, "triggerType": trigger, "state": key, "reason": "missing artifact path"})
            continue
        rel = Path(value)
        if rel.is_absolute() or ".." in rel.parts:
            missing.append({"region": name, "triggerType": trigger, "state": key, "path": value, "reason": "artifact path must be relative under ref-dir"})
            continue
        if not (value.startswith("clip/ref/") or value.startswith("transitions/ref/")):
            missing.append({"region": name, "triggerType": trigger, "state": key, "path": value, "reason": "artifact path must live under clip/ref/ or transitions/ref/"})
            continue
        path = ref_dir / rel
        if not path.is_file():
            missing.append({"region": name, "triggerType": trigger, "state": key, "path": value, "reason": "artifact file missing"})
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size <= 0:
            missing.append({"region": name, "triggerType": trigger, "state": key, "path": value, "reason": "artifact file empty"})
            continue
        checked.append({"region": name, "triggerType": trigger, "state": key, "path": value, "bytes": size})

status = "fail" if missing else "pass"
payload = {
    "schemaVersion": 1,
    "status": status,
    "regionsChecked": len(regions),
    "checkedArtifacts": checked,
    "dispatchOnlyRegions": dispatch_only,
    "missingArtifacts": missing,
    "rule": "Every regions.json triggerType entry must enumerate explicit ref capture artifacts; downstream generation must not infer transition evidence from trigger names alone.",
}
write(payload, 1 if missing else 0)
PY
