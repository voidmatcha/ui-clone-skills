#!/usr/bin/env bash
# monolithic-impl-check.sh — fail when the impl puts the entire UI
# into a single App.jsx / App.tsx / page.tsx without componentizing.
#
# Common failure pattern: agent built scratch/validation run/impl with
# src/App.jsx at 23 KB and NO components/ directory. scaffold-residue
# returns "0 orphans" because 0 components are defined; the gate
# never flags the monolithic shape. The result is impossible to
# iterate against per-section (every fix touches App.jsx, every
# section-compare diff merges into one file).
#
# Detection: when the impl's primary entry (App.{jsx,tsx} for vite,
# app/page.{jsx,tsx} for next) exceeds MONOLITHIC_SIZE_BYTES AND the
# components directory has fewer than MIN_COMPONENTS — fail.
#
# Thresholds tuned conservatively so legitimate small clones don't
# trip (only flags when the entry file is large AND component
# directory is sparse).
#
# Usage:
#   monolithic-impl-check.sh <ref-dir> [<impl-root>]
#
# Output: <ref-dir>/monolithic-impl.json
# Exit: 0 pass, 1 fail, 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_ROOT="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: monolithic-impl-check.sh <ref-dir> [<impl-root>]" >&2
  exit 2
fi

if [ -z "$IMPL_ROOT" ]; then
  PLUGIN_ROOT_CAND="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}}"
  for cand_root in "$PLUGIN_ROOT_CAND" "$(cd "$(dirname "$0")/../../.." && pwd)"; do
    [ -z "$cand_root" ] && continue
    RESOLVER="$cand_root/scripts/extract/find-impl-root.sh"
    if [ -f "$RESOLVER" ]; then
      IMPL_ROOT=$(bash "$RESOLVER" "$REF_DIR" 2>/dev/null | head -1)
      [ -n "$IMPL_ROOT" ] && [ -d "$IMPL_ROOT" ] && break
    fi
  done
fi

OUT_PATH="$REF_DIR/monolithic-impl.json"

if [ -z "$IMPL_ROOT" ] || [ ! -d "$IMPL_ROOT" ]; then
  cat > "$OUT_PATH" <<JSON
{
  "schemaVersion": 1,
  "status": "skip",
  "reason": "impl_root not found",
  "violations": []
}
JSON
  echo "monolithic-impl: skip (no impl)"
  exit 0
fi

python3 - "$IMPL_ROOT" "$OUT_PATH" "$REF_DIR" <<'PY'
import json
import sys
from pathlib import Path

impl_root = Path(sys.argv[1])
out_path = Path(sys.argv[2])
ref_dir = Path(sys.argv[3])

# Thresholds — tuned so a small landing page with one component file
# doesn't trip, but a sites-worth of UI packed into one file does.
MONOLITHIC_SIZE_BYTES = 8000
MIN_COMPONENTS = 3
# When section-map.json declares N sections, expect at least N//3
# components (each component typically wraps 1-3 sections).
SECTION_MAP_MIN_RATIO = 3

# Find primary entry files.
ENTRY_CANDIDATES = [
    impl_root / "src" / "App.tsx",
    impl_root / "src" / "App.jsx",
    impl_root / "src" / "App.ts",
    impl_root / "src" / "App.js",
    impl_root / "app" / "page.tsx",
    impl_root / "app" / "page.jsx",
    impl_root / "src" / "app" / "page.tsx",
    impl_root / "pages" / "index.tsx",
    impl_root / "pages" / "index.jsx",
]
entry_path = next((p for p in ENTRY_CANDIDATES if p.is_file()), None)
entry_size = entry_path.stat().st_size if entry_path else 0

# Count component files (PascalCase .tsx/.jsx under src/components or
# src/ at any non-entry path).
comp_count = 0
comp_dirs = [
    impl_root / "src" / "components",
    impl_root / "src" / "sections",
    impl_root / "src" / "ds-components",
    impl_root / "src" / "ui",
    impl_root / "app" / "components",
    impl_root / "components",
]
for cd in comp_dirs:
    if not cd.is_dir():
        continue
    for p in cd.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in {".tsx", ".jsx", ".ts", ".js"}:
            continue
        # PascalCase basename only (filters index.ts, types.ts, etc.)
        name = p.stem
        if name[:1].isupper() and name not in {"App", "Page", "Layout", "Root"}:
            comp_count += 1

# Expected components from section-map.json (if available).
expected_min = MIN_COMPONENTS
section_map = ref_dir / "section-map.json"
total_sections = 0
if section_map.is_file():
    try:
        sm = json.loads(section_map.read_text(encoding="utf-8"))
        total_sections = int(sm.get("totalCount") or 0)
        if total_sections > 0:
            expected_min = max(MIN_COMPONENTS, total_sections // SECTION_MAP_MIN_RATIO)
    except (OSError, ValueError):
        pass

violations: list[dict] = []
if (
    entry_path is not None
    and entry_size >= MONOLITHIC_SIZE_BYTES
    and comp_count < expected_min
):
    violations.append({
        "kind": "monolithic-entry",
        "entry": str(entry_path.relative_to(impl_root)),
        "entryBytes": entry_size,
        "componentCount": comp_count,
        "expectedMin": expected_min,
        "totalSections": total_sections,
        "detail": (
            f"Entry file ({entry_path.name}, {entry_size} bytes) packs "
            f"the whole UI; only {comp_count} components exist (expected "
            f">= {expected_min}). Per-section iteration is impossible "
            "when every fix touches one file. Split into components."
        ),
    })

status = "fail" if violations else "pass"
result = {
    "schemaVersion": 1,
    "status": status,
    "implRoot": str(impl_root),
    "entry": str(entry_path.relative_to(impl_root)) if entry_path else None,
    "entryBytes": entry_size,
    "componentCount": comp_count,
    "expectedMin": expected_min,
    "totalSections": total_sections,
    "violations": violations,
    "rule": (
        f"When entry file >= {MONOLITHIC_SIZE_BYTES} bytes AND "
        f"component count < expected_min (max(MIN_COMPONENTS={MIN_COMPONENTS}, "
        f"sections // {SECTION_MAP_MIN_RATIO})), flag as monolithic. "
        "Forces per-section componentization so visual-debug-iterator "
        "can fix one section at a time."
    ),
}

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(
    f"monolithic-impl: entry={entry_size}B, components={comp_count}, "
    f"expected>={expected_min} → {status}"
)
sys.exit(0 if status == "pass" else 1)
PY
