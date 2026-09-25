"""Crop manifest, mask coverage, extra-section, and impl-path helpers (section-compare).

Moved verbatim out of ``ui_clone.section_compare_sections``; that module
re-exports every name here.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ui_clone.section_capture import safe_section_name
from ui_clone.section_compare_common import (
    Rect,
    Section,
    _as_int,
    _class_name,
    _class_tokens,
    _copy_with_index,
    _intersect,
    _rect_from,
    _tag,
    _top,
    _union_area,
)


def promote_impl_path_reference(matches: Sequence[object]) -> list[Section]:
    """Build frozen reference metadata from the rows captured via impl path.

    Section crops are named from the reference row in ``matches.json``. The
    impl-path row can have a different class/name (or a different synthesized
    section list), so copying raw ``impl-sections.json`` beside those crops
    breaks the filename-to-section identity on the next frozen pass. Preserve
    the exact capture name on each promoted impl row and let ``_make_name`` use
    it when the next match set is materialized.
    """

    promoted: list[Section] = []
    seen: set[tuple[object, ...]] = set()
    for raw_match in matches:
        if not isinstance(raw_match, dict):
            continue
        raw_impl = raw_match.get("impl")
        if not isinstance(raw_impl, dict) or raw_impl.get("offCanvas"):
            continue
        capture_name = safe_section_name(
            str(raw_match.get("name") or ""),
            max_length=40,
        )
        if not capture_name:
            continue
        rect = _rect_from(raw_impl.get("rect"))
        key = (
            _tag(raw_impl),
            _class_name(raw_impl),
            rect["top"] if rect else None,
            rect["left"] if rect else None,
            rect["width"] if rect else None,
            rect["height"] if rect else None,
        )
        if key in seen:
            continue
        seen.add(key)
        row = dict(raw_impl)
        row["captureName"] = capture_name
        promoted.append(row)

    promoted.sort(
        key=lambda row: (
            _top(row) if _top(row) is not None else float("inf"),
            _as_int(row.get("index")),
        )
    )
    return [_copy_with_index(row, index) for index, row in enumerate(promoted)]


def build_crop_manifest(
    matches: Sequence[object],
    ref_dir: Path,
    impl_dir: Path,
) -> dict[str, object]:
    """Describe crop files that belong to the current matches identity.

    Frozen section runs intentionally preserve reference crops across passes.
    A changed enumeration can therefore leave an old ``name.png`` beside the
    current match set. Glob-driven evaluation treated that orphan as a missing
    implementation section. The manifest makes matches.json authoritative and
    records stale files for audit without evaluating them.
    """
    rows: list[dict[str, object]] = []
    current_names: set[str] = set()
    for raw in matches:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        ref = raw.get("ref")
        impl = raw.get("impl")
        if not isinstance(ref, dict) or not isinstance(impl, dict):
            continue
        current_names.add(name)
        ref_crop = ref_dir / f"{name}.png"
        impl_crop = impl_dir / f"{name}.png"
        rows.append({
            "name": name,
            "refIndex": ref.get("index"),
            "implIndex": impl.get("index"),
            "refExists": ref_crop.is_file(),
            "implExists": impl_crop.is_file(),
        })

    stale_ref_crops = sorted(
        path.name
        for path in ref_dir.glob("*.png")
        if path.stem not in current_names
    )
    return {
        "schemaVersion": 1,
        "rows": rows,
        "staleRefCrops": stale_ref_crops,
    }


def find_large_extra_sections(
    matches: Sequence[object], floor_px: int
) -> list[tuple[str, int]]:
    """Fix 94 (A3) — impl sections that paired with NO ref (EXTRA_IN_IMPL) and
    render at least floor_px tall. A faithful clone has ~0 of these; a duplicated
    or misplaced impl block (a hero re-rendered at the page bottom, or a
    dedup-renamed "-2" section) surfaces here. Bounded structural-health signal,
    NOT a general order/structural diff.
    """
    ref_rects: list[Rect] = []
    for x in matches:
        if not isinstance(x, dict) or x.get("status") == "EXTRA_IN_IMPL":
            continue
        ref_raw = x.get("ref")
        ref_row = ref_raw if isinstance(ref_raw, dict) else {}
        r = _rect_from(ref_row.get("rect"))
        if r is not None:
            ref_rects.append(r)

    span_top = min((r["top"] for r in ref_rects), default=0.0)
    span_bottom = max((r["top"] + r["height"] for r in ref_rects), default=0.0)

    def _is_unmapped_shared_band(rect: Rect) -> bool:
        # An extra living INSIDE the matched page span, overlapping no
        # sibling-level ref region, sits in a ref section-map COVERAGE GAP —
        # both pages have content there, the map just never enumerated it
        # (end-to-end run: the hero-video block between hero and stats, orphaned
        # when the off-canvas pre-pass stopped consuming it). That is
        # enumeration granularity, not a duplicated/misplaced block; docH +
        # geometry-sanity still catch genuinely inserted blocks because they
        # shift everything below them. Jumbo container rows (>=3x the extra's
        # area, fully containing it) are wrappers, not siblings — they do not
        # count as conflicts.
        if not ref_rects:
            return False
        if rect["top"] < span_top - 1 or rect["top"] + rect["height"] > span_bottom + 1:
            return False
        area = max(1.0, rect["width"] * rect["height"])
        for r in ref_rects:
            inter = _intersect(rect, r)
            if inter is None:
                continue
            overlap_frac = (inter["width"] * inter["height"]) / area
            if overlap_frac <= 0.1:
                continue
            r_area = r["width"] * r["height"]
            contains = (
                r["top"] <= rect["top"] + 1
                and r["top"] + r["height"] >= rect["top"] + rect["height"] - 1
                and r_area >= 3.0 * area
            )
            if not contains:
                return False
        return True

    out: list[tuple[str, int]] = []
    for x in matches:
        if not isinstance(x, dict):
            continue
        if x.get("status") != "EXTRA_IN_IMPL" or x.get("ref"):
            continue
        im_raw = x.get("impl")
        im = im_raw if isinstance(im_raw, dict) else {}
        rect_raw = im.get("rect")
        rect = rect_raw if isinstance(rect_raw, dict) else {}
        try:
            h = int(rect.get("height") or 0)
        except (TypeError, ValueError):
            h = 0
        if h >= floor_px:
            parsed = _rect_from(rect_raw)
            if parsed is not None and _is_unmapped_shared_band(parsed):
                continue
            out.append((str(x.get("name", "?")), h))
    return out


def calculate_mask_coverage(
    matches: Sequence[object], mask_rects: Sequence[object]
) -> dict[str, float]:
    """Compute percent of each matched REF section covered by dynamic masks.

    Values are sidecar evidence only. They do not affect section-compare pass
    rows; a later gate can use this JSON to detect pass-under-mask cases.
    """
    masks = [raw for raw in mask_rects if isinstance(raw, dict) and _rect_from(raw) is not None]

    def owned_by_section(mask: Section, section: Section, name: str) -> bool:
        """Reject dynamic media that only overlaps a section geometrically.

        Fixed headers and sticky overlays often share coordinates with a hero
        canvas without owning it. New mask evidence carries the media node's DOM
        owner chain so coverage follows containment. Older artifacts without an
        owner chain retain the geometry-only behavior for compatibility.
        """
        owners = mask.get("ownerChain")
        if not isinstance(owners, list):
            return True
        section_id = str(section.get("id") or "").strip()
        section_tag = _tag(section)
        section_classes = _class_tokens(_class_name(section))
        name_key = name.strip().lower()
        # synthesize_ref_sections_from_section_map() stores _section_id()
        # (DOM id OR section-map ``name``, which is the first captured class)
        # in ``id``. A class-alias id never equals a DOM owner id, so exact-id
        # matching alone reported 0.0 coverage for every such section. Fall
        # back to tag/class/name matching only when the id is a class alias;
        # a real DOM id keeps the strict containment rule.
        section_id_is_class_alias = bool(
            section_id and section_id in _class_name(section).split()
        )
        for owner in owners:
            if not isinstance(owner, dict):
                continue
            owner_id = str(owner.get("id") or "").strip()
            if section_id:
                if owner_id == section_id:
                    return True
                if not section_id_is_class_alias:
                    continue
            owner_tag = str(owner.get("tag") or "").strip().lower()
            owner_classes = _class_tokens(str(owner.get("className") or ""))
            if section_tag and owner_tag != section_tag:
                continue
            if section_classes and section_classes <= owner_classes:
                return True
            if name_key and (owner_id.lower() == name_key or name_key in owner_classes):
                return True
        return False

    coverage: dict[str, float] = {}
    for match in matches:
        if not isinstance(match, dict) or not match.get("ref"):
            continue
        name = str(match.get("name") or "")
        if not name:
            continue
        ref = match["ref"] if isinstance(match["ref"], dict) else {}
        section = _rect_from(ref.get("rect"))
        if section is None:
            coverage[name] = 0.0
            continue
        section_area = section["width"] * section["height"]
        clipped = []
        for mask in masks:
            if not owned_by_section(mask, ref, name):
                continue
            mask_rect = _rect_from(mask)
            if mask_rect is None:
                continue
            intersection = _intersect(section, mask_rect)
            if intersection is not None:
                clipped.append(intersection)
        pct = 0.0 if section_area <= 0 else min(100.0, (_union_area(clipped) / section_area) * 100)
        coverage[name] = round(pct, 2)
    return coverage


def parse_agent_browser_json_list(raw: str) -> list[dict[str, Any]]:
    """Read a JSON list from agent-browser's plain or wrapped eval output.

    Current agent-browser versions pretty-print arrays across multiple lines.
    Older callers tried ``json.loads`` one line at a time, which silently
    converted valid multi-line mask evidence into ``[]``. Accept the complete
    payload first, then retain the legacy line fallback for noisy wrappers.
    """
    candidates = [raw, *reversed(raw.splitlines())]
    for candidate in candidates:
        value: object = candidate.strip()
        if not value:
            continue
        for _ in range(4):
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    break
                continue
            if isinstance(value, dict):
                nested = value.get("data")
                if nested is None:
                    nested = value.get("result")
                if nested is None:
                    break
                value = nested
                continue
            break
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []
