#!/usr/bin/env bash
# asset-placement-check.sh — verify visible assets are referenced by the
# component mapped to the section where the ref renders them.
#
# This is stricter than global asset-utilization: a basename appearing anywhere
# in impl/src is not proof that the asset appears in its original section.
#
# Usage: asset-placement-check.sh <ref-dir> [<impl-root-or-src-dir>]
#
# Reads:
#   <ref-dir>/visible-images.json
#   <ref-dir>/section-map.json
#   <ref-dir>/component-map.json
#
# Writes:
#   <ref-dir>/asset-placement.json

set -euo pipefail

REF_DIR="${1:?Usage: asset-placement-check.sh <ref-dir> [<impl-root-or-src-dir>]}"
[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

IMPL_ARG="${2:-}"
if [ -z "$IMPL_ARG" ]; then
  CANDIDATES=(
    "$(dirname "$REF_DIR")/../impl"
    "$(dirname "$REF_DIR")/impl"
    "apps/$(basename "$REF_DIR")"
    "app"
    "."
  )
  for c in "${CANDIDATES[@]}"; do
    if [ -d "$c/src" ] || [ -d "$c/app" ] || [ -d "$c/pages" ]; then
      IMPL_ARG="$c"
      break
    fi
  done
fi

OUT="$REF_DIR/asset-placement.json"

python3 - "$REF_DIR" "$IMPL_ARG" "$OUT" <<'PY'
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

ref_dir = Path(sys.argv[1])
impl_arg_raw = sys.argv[2]
out_path = Path(sys.argv[3])


def write(payload: dict[str, Any], code: int) -> None:
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    sys.exit(code)


if not impl_arg_raw:
    write(
        {
            "schemaVersion": 1,
            "status": "skip",
            "checked": 0,
            "missingPlacements": [],
            "implRoot": "",
            "implSrcDir": "",
            "reason": "impl root not found",
        },
        0,
    )

impl_arg = Path(impl_arg_raw)
if impl_arg.name in {"src", "app", "pages"}:
    impl_root = impl_arg.parent
    impl_src = impl_arg
else:
    impl_root = impl_arg
    impl_src = impl_root / "src" if (impl_root / "src").is_dir() else impl_root

visible_path = ref_dir / "visible-images.json"
section_map_path = ref_dir / "section-map.json"
component_map_path = ref_dir / "component-map.json"
missing_inputs = [str(p.name) for p in (visible_path, section_map_path, component_map_path) if not p.is_file()]
if missing_inputs:
    write(
        {
            "schemaVersion": 1,
            "status": "skip",
            "checked": 0,
            "missingPlacements": [],
            "implRoot": str(impl_root),
            "implSrcDir": str(impl_src),
            "reason": f"missing inputs: {', '.join(missing_inputs)}",
        },
        0,
    )


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def list_payload(raw: Any, keys: tuple[str, ...]) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in keys:
            value = raw.get(key)
            if isinstance(value, list):
                return value
    return []


def first_number(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    """Read coordinates from flat rows or extractor rect/bbox payloads."""
    for key in keys:
        value = row.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    for container_key in ("rect", "bbox", "bounds", "frame"):
        container = row.get(container_key)
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = container.get(key)
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return None


def normalize_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def pascal_from_slug(value: str) -> str:
    words = [word for word in re.split(r"[^a-zA-Z0-9]+", value) if word]
    return "".join(word[:1].upper() + word[1:] for word in words)


try:
    visible = list_payload(load_json(visible_path), ("images", "visible", "entries", "items"))
    sections = list_payload(load_json(section_map_path), ("sections", "items"))
    component_raw = load_json(component_map_path)
    components = list_payload(component_raw, ("sections", "components", "items"))
except (OSError, json.JSONDecodeError) as exc:
    write(
        {
            "schemaVersion": 1,
            "status": "fail",
            "checked": 0,
            "missingPlacements": [{"reason": f"malformed input: {exc}"}],
            "implRoot": str(impl_root),
            "implSrcDir": str(impl_src),
        },
        1,
    )

section_rows: list[dict[str, Any]] = []
section_id_to_index: dict[str, int] = {}
for ordinal, row in enumerate(sections):
    if not isinstance(row, dict):
        continue
    top = first_number(row, ("top", "y"))
    height = first_number(row, ("height", "h"))
    bottom = first_number(row, ("bottom", "b"))
    if top is None:
        continue
    if height is None and bottom is not None:
        height = bottom - top
    if height is None:
        continue
    top_i = int(top)
    height_i = int(height)
    section_index = row.get("index", row.get("i", row.get("section_idx", ordinal)))
    try:
        section_index_i = int(section_index)
    except (TypeError, ValueError):
        section_index_i = ordinal
    section_id = row.get("id", row.get("sectionId", row.get("name")))
    section_rows.append({
        "index": section_index_i,
        "top": top_i,
        "bottom": top_i + max(height_i, 1),
        "id": section_id,
    })
    for value in (section_id, row.get("name"), row.get("sourceClass")):
        key = normalize_name(value)
        if key:
            section_id_to_index[key] = section_index_i

component_files_by_index: dict[int, set[str]] = {}
source_files: list[Path] = []
if impl_src.is_dir():
    for suffix in ("*.tsx", "*.jsx", "*.ts", "*.js"):
        source_files.extend(path for path in impl_src.rglob(suffix) if path.is_file())


def resolve_section_index(row: dict[str, Any], ordinal: int) -> int:
    section_index = row.get("index", row.get("i", row.get("section_idx")))
    try:
        return int(section_index)
    except (TypeError, ValueError):
        pass
    for key in ("sectionId", "id", "sourceSection", "sourceClass", "name"):
        lookup = normalize_name(row.get(key))
        if lookup in section_id_to_index:
            return section_id_to_index[lookup]
    return ordinal


def relative_to_impl(path: Path) -> str:
    try:
        return str(path.relative_to(impl_root))
    except ValueError:
        try:
            return str(path.relative_to(impl_src))
        except ValueError:
            return str(path)


def inferred_component_files(row: dict[str, Any]) -> set[str]:
    bases: set[str] = set()
    for key in ("file", "path", "sectionId", "id", "sourceClass", "componentName", "name"):
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        variants = {value, pascal_from_slug(value)}
        for suffix in ("Section", "Component", "View"):
            if value.endswith(suffix):
                variants.add(value[: -len(suffix)])
        for variant in variants:
            norm = normalize_name(variant)
            if norm:
                bases.add(norm)
    if not bases:
        return set()
    matches: set[str] = set()
    for path in source_files:
        if normalize_name(path.stem) in bases:
            matches.add(relative_to_impl(path))
    return matches


for ordinal, row in enumerate(components):
    if not isinstance(row, dict):
        continue
    file_value = row.get("file")
    section_index_i = resolve_section_index(row, ordinal)
    if isinstance(file_value, str) and file_value.strip():
        component_files_by_index.setdefault(section_index_i, set()).add(file_value)
        continue
    inferred = inferred_component_files(row)
    if inferred:
        component_files_by_index.setdefault(section_index_i, set()).update(inferred)


def section_for_top(top_value: Any) -> int | None:
    try:
        top = int(float(top_value))
    except (TypeError, ValueError):
        return None
    matches = [row for row in section_rows if row["top"] <= top < row["bottom"]]
    if matches:
        return int(matches[0]["index"])
    # Tolerate small extraction jitter just above/below a section edge.
    nearest = sorted(section_rows, key=lambda row: min(abs(top - row["top"]), abs(top - row["bottom"])))
    return int(nearest[0]["index"]) if nearest else None


def component_path(file_value: str) -> Path:
    rel = Path(file_value)
    if rel.is_absolute():
        return rel
    if rel.parts and rel.parts[0] in {"src", "app", "pages"}:
        return impl_root / rel
    return impl_src / rel


def asset_needles(src: str) -> list[str]:
    parsed = urlparse(unquote(src))
    base = os.path.basename(parsed.path)
    if "/cdn-cgi/image/" in parsed.path:
        # Cloudflare optimizer paths still end with the original asset basename.
        base = os.path.basename(parsed.path)
    if not base:
        return []
    stem = os.path.splitext(base)[0]
    needles = [base]
    if len(stem) >= 4:
        needles.append(stem)
    return needles


checked = 0
missing: list[dict[str, Any]] = []
skipped = 0

for entry in visible:
    if not isinstance(entry, dict):
        continue
    src = entry.get("src") or entry.get("url")
    if not isinstance(src, str) or not src.startswith(("http://", "https://", "//")):
        continue
    section_index = section_for_top(first_number(entry, ("top", "y")))
    if section_index is None:
        skipped += 1
        continue
    files = sorted(component_files_by_index.get(section_index) or [])
    if not files:
        skipped += 1
        continue
    needles = asset_needles(src)
    if not needles:
        skipped += 1
        continue
    checked += 1
    section_ok = False
    missing_files: list[str] = []
    for file_value in files:
        path = component_path(file_value)
        if not path.is_file():
            missing_files.append(file_value)
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            missing_files.append(file_value)
            continue
        if any(needle in text for needle in needles):
            section_ok = True
            break
    if not section_ok:
        missing.append(
            {
                "src": src,
                "sectionIndex": section_index,
                "componentFile": files[0] if len(files) == 1 else files,
                "missingComponentFiles": missing_files,
                "needles": needles,
                "reason": "asset not referenced by the component mapped to its ref section",
            }
        )

status = "fail" if missing else ("pass" if checked else "skip")
payload = {
    "schemaVersion": 1,
    "status": status,
    "checked": checked,
    "skipped": skipped,
    "missingPlacements": missing,
    "implRoot": str(impl_root),
    "implSrcDir": str(impl_src),
    "rule": "visible-images.json assets with section coordinates must be referenced by the generated component mapped to that section, not merely somewhere in impl/src.",
}
if status == "skip":
    payload["reason"] = "no section-mappable visible assets"
write(payload, 1 if missing else 0)
PY
