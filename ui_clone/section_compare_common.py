"""Shared types, thresholds, and row/rect helpers for section-compare pairing.

Moved verbatim out of ``ui_clone.section_compare_sections``; that module
re-exports every name here.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing import TypeGuard


Section = dict[str, Any]
Rect = dict[str, float]

_MIN_VISIBLE_HEIGHT = 50
_MIN_LANDMARK_HEIGHT = 24
_SEMANTIC_TAGS = {"main", "section", "header", "footer", "nav", "article"}
_LANDMARK_TAGS = _SEMANTIC_TAGS - {"section"}
_GENERIC_IDENTITY_ANCHOR_TOKENS = {
    "active",
    "next",
    "prev",
    "slide",
}


def _is_number(value: object) -> TypeGuard[int | float]:
    """Keep system-Python execution compatible with macOS Python 3.9."""
    return isinstance(value, int) or isinstance(value, float)


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _tag(row: Section) -> str:
    return str(row.get("tag") or "").lower()


def _section_id(row: Section) -> str:
    value = row.get("id") or row.get("name") or ""
    return str(value).strip()


def _class_name(row: Section) -> str:
    value = row.get("className") or row.get("cls") or row.get("class") or ""
    return str(value).strip()


def _class_tokens(value: str) -> set[str]:
    return {token for token in re.split(r"\s+", value.strip()) if len(token) >= 4}


def has_grid_layout_mismatch(ref: Section, impl: Section) -> bool:
    """Return whether only the reference declares real grid columns.

    Computed style and synthesized section metadata use both ``None`` and the
    CSS sentinel string ``"none"`` for elements without a grid template.
    """
    def uses_grid_columns(row: Section) -> bool:
        value = row.get("gridCols")
        if isinstance(value, str):
            return value.strip().lower() not in {"", "none"}
        return bool(value)

    return uses_grid_columns(ref) and not uses_grid_columns(impl)


def _lockable_class_tokens(value: str) -> set[str]:
    return {
        token
        for token in _class_tokens(value)
        if token not in _GENERIC_IDENTITY_ANCHOR_TOKENS
        and not token.startswith("swiper")
    }


def _height(row: Section) -> int:
    rect = row.get("rect")
    if isinstance(rect, dict):
        return _as_int(rect.get("height"))
    return _as_int(row.get("height") or row.get("h"))


def _top(row: Section) -> float | None:
    """A section's document-space top (px), or None when no rect is present."""
    rect = row.get("rect")
    if isinstance(rect, dict):
        v = rect.get("top")
        if _is_number(v):
            return float(v)
    v = row.get("top") or row.get("y")
    return float(v) if _is_number(v) else None


def _top_distance(a: Section, b: Section) -> float:
    """|topA - topB| in px, or +inf when either side has no position.

    batch-13 ITEM 1 sub-fix 3: position is the strongest disambiguator for two
    near-identical (same id/class/text) sections — it is EXACT in the ref-vs-ref
    self-pass and order-preserving in a faithful clone, whereas DOM-index
    distance is wrong whenever the two sides enumerate a different number of
    rows (impl food cards / hero-video shift every later index).
    """
    ta, tb = _top(a), _top(b)
    if ta is None or tb is None:
        return float("inf")
    return abs(ta - tb)


def _visible(row: Section) -> bool:
    return _height(row) >= _MIN_VISIBLE_HEIGHT


def _horizontally_visible(row: Section, viewport_width: int) -> bool:
    rect = _rect_from(row.get("rect"))
    if rect is None:
        return True
    return (
        rect["width"] > 0
        and rect["left"] < viewport_width
        and rect["left"] + rect["width"] > 0
    )


def _pair_viewport_width(row: Section) -> int:
    client_width = _as_int(row.get("clientWidth"))
    if client_width > 0:
        return client_width
    view_w = _as_int(os.environ.get("VIEW_W"), 1440)
    return view_w if view_w > 0 else 1440


def _pair_input_sections(rows: Sequence[Section]) -> list[Section]:
    return [
        row
        for row in rows
        if _horizontally_visible(row, _pair_viewport_width(row))
    ]


def _semantic_candidate_visible(row: Section) -> bool:
    return _visible(row) or (
        _tag(row) in _LANDMARK_TAGS
        and _height(row) >= _MIN_LANDMARK_HEIGHT
    )


def _section_map_candidate(row: Section) -> bool:
    tag = _tag(row)
    if not _semantic_candidate_visible(row):
        return False
    if tag == "div":
        return bool(
            _section_id(row)
            or _lockable_class_tokens(_class_name(row))
        )
    if tag not in _SEMANTIC_TAGS:
        return False
    return bool(
        _section_id(row)
        or _class_tokens(_class_name(row))
        or tag in _LANDMARK_TAGS
    )


def _identity_matches(section_map_row: Section, runtime_row: Section) -> bool:
    target_tag = _tag(section_map_row)
    runtime_tag = _tag(runtime_row)
    target_id = _section_id(section_map_row)

    if target_id:
        if _section_id(runtime_row) != target_id:
            return False
        return not target_tag or not runtime_tag or target_tag == runtime_tag

    target_tokens = _class_tokens(_class_name(section_map_row))
    if not target_tokens:
        return False
    if target_tag and runtime_tag and target_tag != runtime_tag:
        return False
    return bool(target_tokens & _class_tokens(_class_name(runtime_row)))


def _same_section_instance(section_map_row: Section, runtime_row: Section) -> bool:
    """Require repeated class identities to refer to the same page instance."""
    if not _identity_matches(section_map_row, runtime_row):
        return False
    if _section_id(section_map_row):
        return True
    distance = _top_distance(section_map_row, runtime_row)
    if distance == float("inf"):
        return True
    heights = [
        value
        for value in (_height(section_map_row), _height(runtime_row))
        if value > 0
    ]
    comparable_height = min(heights) if heights else 0
    return distance <= max(64.0, comparable_height * 0.5)


def _copy_with_index(row: Section, index: int) -> Section:
    copied = dict(row)
    copied["index"] = index
    return copied


# Two impl rows count as the SAME section when their heights are comparable and
# their vertical extents overlap heavily on the page's major (scroll) axis.
# IoU > 0.5 is the primary test; as a robustness backstop we also reject when
# the candidate's vertical CENTER falls inside an existing row's [top, bottom]
# and the two widths are within this ratio of each other — the verified
# phantoms (one observed site's idx 12/13) are near-exact positional twins of the real
# pyramid/faqs rows, so center-containment catches them even if a small height
# difference dents the IoU.
_OVERLAP_IOU_THRESHOLD = 0.5
_OVERLAP_WIDTH_RATIO = 0.6
_OVERLAP_HEIGHT_RATIO = 0.6


def _vertical_iou(a: Rect, b: Rect) -> float:
    """1-D intersection-over-union of two rects on the vertical (scroll) axis."""
    top = max(a["top"], b["top"])
    bottom = min(a["top"] + a["height"], b["top"] + b["height"])
    inter = bottom - top
    if inter <= 0:
        return 0.0
    union = a["height"] + b["height"] - inter
    return inter / union if union > 0 else 0.0


def _width_comparable(a: Rect, b: Rect) -> bool:
    """True when two rects have comparable widths (min/max ratio above floor).

    Width-0 rects (no horizontal extent recorded) are treated as comparable so
    the vertical-position signal still governs — a row that omits width should
    not dodge the dedup guard purely because its width is unknown.
    """
    hi = max(a["width"], b["width"])
    if hi <= 0:
        return True
    return (min(a["width"], b["width"]) / hi) >= _OVERLAP_WIDTH_RATIO


def _height_comparable(a: Rect, b: Rect) -> bool:
    """True when two rects are plausibly duplicate-sized on the scroll axis."""
    hi = max(a["height"], b["height"])
    if hi <= 0:
        return True
    return (min(a["height"], b["height"]) / hi) >= _OVERLAP_HEIGHT_RATIO


def _positionally_overlaps(candidate: Rect, existing: Rect) -> bool:
    """True when `candidate` is a positional duplicate of an `existing` row.

    Heavy vertical IoU OR vertical-center-containment (with comparable heights,
    plus comparable widths for center containment) signal "same section in a
    different DOM shape" — the augment path must not append a candidate that
    twins a row already present, or it fabricates a phantom EXTRA / steals the
    real section's AE measurement.
    """
    if not _height_comparable(candidate, existing):
        return False
    if _vertical_iou(candidate, existing) > _OVERLAP_IOU_THRESHOLD:
        return True
    center = candidate["top"] + candidate["height"] / 2.0
    within = existing["top"] <= center <= existing["top"] + existing["height"]
    return within and _width_comparable(candidate, existing)


def _float_value(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def _rect_from(raw: object) -> Rect | None:
    if not isinstance(raw, dict):
        return None
    left = _float_value(raw.get("left") or raw.get("x"))
    top = _float_value(raw.get("top") or raw.get("y"))
    width = _float_value(raw.get("width") or raw.get("w"))
    height = _float_value(raw.get("height") or raw.get("h"))
    if width <= 0 or height <= 0:
        return None
    return {"left": left, "top": top, "width": width, "height": height}


def _intersect(a: Rect, b: Rect) -> Rect | None:
    left = max(a["left"], b["left"])
    top = max(a["top"], b["top"])
    right = min(a["left"] + a["width"], b["left"] + b["width"])
    bottom = min(a["top"] + a["height"], b["top"] + b["height"])
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return None
    return {"left": left, "top": top, "width": width, "height": height}


def _union_area(rects: list[Rect]) -> float:
    """Return exact union area for axis-aligned rectangles."""
    if not rects:
        return 0.0
    x_edges = sorted(
        {r["left"] for r in rects} | {r["left"] + r["width"] for r in rects}
    )
    area = 0.0
    for x1, x2 in zip(x_edges, x_edges[1:]):
        slab_width = x2 - x1
        if slab_width <= 0:
            continue
        intervals: list[tuple[float, float]] = []
        for rect in rects:
            if rect["left"] < x2 and rect["left"] + rect["width"] > x1:
                intervals.append((rect["top"], rect["top"] + rect["height"]))
        if not intervals:
            continue
        intervals.sort()
        covered = 0.0
        current_start, current_end = intervals[0]
        for start, end in intervals[1:]:
            if start <= current_end:
                current_end = max(current_end, end)
            else:
                covered += current_end - current_start
                current_start, current_end = start, end
        covered += current_end - current_start
        area += slab_width * covered
    return area
