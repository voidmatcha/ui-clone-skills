from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing import TypeGuard

from ui_clone.section_capture import safe_section_name

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
# phantoms (realfood idx 12/13) are near-exact positional twins of the real
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


def augment_impl_sections_from_section_map(
    section_map: Section,
    impl_sections: list[Section],
    semantic_candidates: list[Section],
) -> list[Section]:
    """Restore impl semantic wrappers that section-map synthesized for ref.

    `section-compare.sh` can replace ref runtime enumeration with
    section-map.json rows. When the impl runtime enumerator descends through a
    jumbo semantic wrapper, matching becomes asymmetric: ref keeps the wrapper,
    impl keeps only its children. This function appends matching impl DOM
    candidates for section-map rows that are missing from runtime impl sections.
    Stable identity is preferred; visible identityless landmarks are restored by
    tag and nearest document position.
    """
    raw_sections = section_map.get("sections")
    if not isinstance(raw_sections, list):
        return [dict(row) for row in impl_sections]

    augmented = [dict(row) for row in impl_sections]
    max_index = max((_as_int(row.get("index"), -1) for row in augmented), default=-1)
    next_index = max_index + 1

    sorted_sections = sorted(
        (row for row in raw_sections if isinstance(row, dict)),
        key=lambda row: _as_int(row.get("index"), _as_int(row.get("top") or row.get("y"))),
    )
    used_semantic_candidates: set[int] = set()

    for section_row in sorted_sections:
        if not _section_map_candidate(section_row):
            continue
        section_classes = _class_tokens(_class_name(section_row))
        repeated_class_signature = (
            not _section_id(section_row)
            and bool(section_classes)
            and sum(
                1
                for mapped_section in sorted_sections
                if _tag(mapped_section) == _tag(section_row)
                and _class_tokens(_class_name(mapped_section))
                == section_classes
            )
            > 1
        )
        if (
            not repeated_class_signature
            and any(_same_section_instance(section_row, row) for row in augmented)
        ):
            continue

        has_identity = bool(
            _section_id(section_row) or _class_tokens(_class_name(section_row))
        )
        candidate_rows = [
            row
            for row in semantic_candidates
            if id(row) not in used_semantic_candidates
            and _semantic_candidate_visible(row)
            and (
                _identity_matches(section_row, row)
                if has_identity
                else _tag(section_row) == _tag(row)
            )
        ]
        exact_class_candidates = [
            row
            for row in candidate_rows
            if section_classes
            and _tag(row) == _tag(section_row)
            and _class_tokens(_class_name(row)) == section_classes
        ]
        if repeated_class_signature and exact_class_candidates:
            match = min(
                exact_class_candidates,
                key=lambda row: (
                    _top(row) if _top(row) is not None else float("inf"),
                    _as_int(row.get("index")),
                ),
            )
        elif candidate_rows:
            match = min(
                candidate_rows,
                key=lambda row: (
                    _top_distance(section_row, row),
                    abs(_height(section_row) - _height(row)),
                    _as_int(row.get("index")),
                ),
            )
        else:
            match = None
        if match is None:
            continue
        used_semantic_candidates.add(id(match))

        # Positional-overlap dedup (kill fabricated phantom sections).
        # A faithful clone renders this section with empty className/id (ref
        # uses CSS-modules, clone uses Tailwind), so the real impl row already
        # present FAILS _identity_matches against the section-map row above —
        # the loop then thinks the section is "missing" and would append a
        # semantic candidate that is a POSITIONAL DUPLICATE of that real row.
        # Reject the candidate when its rect positionally overlaps any row
        # already in `augmented`. This is a strict NO-OP in the ref-vs-ref
        # self-pass: every section there carries identity, so _identity_matches
        # succeeds, the loop `continue`s above, and this guard is never reached.
        match_rect = _rect_from(match.get("rect"))
        class_locked_div = (
            _tag(section_row) == "div"
            and bool(_lockable_class_tokens(_class_name(section_row)))
        )
        if match_rect is not None and any(
            not (_tag(match) in _LANDMARK_TAGS and _tag(row) != _tag(match))
            and (existing_rect := _rect_from(row.get("rect"))) is not None
            and (
                not class_locked_div
                or _width_comparable(match_rect, existing_rect)
            )
            and _positionally_overlaps(match_rect, existing_rect)
            for row in augmented
        ):
            continue

        augmented.append(_copy_with_index(match, next_index))
        next_index += 1

    # Runtime enumeration and semantic candidate recovery can both surface the
    # exact same DOM region with distinct transient indices. Keep truly
    # separate side-by-side siblings, but remove rows whose identity and full
    # rendered rectangle are equal so they cannot become EXTRA_IN_IMPL later.
    deduped: list[Section] = []
    for row in augmented:
        rect = _rect_from(row.get("rect"))
        duplicate = False
        if rect is not None:
            for existing in deduped:
                existing_rect = _rect_from(existing.get("rect"))
                if (
                    existing_rect is not None
                    and _tag(row) == _tag(existing)
                    and _class_name(row) == _class_name(existing)
                    and all(
                        abs(rect[key] - existing_rect[key]) <= 1.0
                        for key in ("top", "left", "width", "height")
                    )
                ):
                    duplicate = True
                    break
        if not duplicate:
            deduped.append(row)
    return [
        _copy_with_index(row, index)
        for index, row in enumerate(deduped)
    ]


def synthesize_ref_sections_from_section_map(
    section_map: Section,
    semantic_candidates: list[Section],
    *,
    active_view_width: int,
    runtime_sections: list[Section] | None = None,
) -> list[Section]:
    """Build ref rows with section-map identity and live viewport geometry.

    The extraction-time section map is authoritative for section identity and
    document order, but its rects describe the extraction viewport. A live
    semantic candidate replaces viewport-dependent measurement when it matches
    by exact id, same-tag class overlap, or same-tag document order for an
    identityless landmark. Missing/frozen candidates safely retain section-map
    measurements.
    """
    raw_sections = section_map.get("sections")
    if not isinstance(raw_sections, list):
        return []

    sections = sorted(
        (row for row in raw_sections if isinstance(row, dict)),
        key=lambda row: _as_int(
            row.get("index"),
            _as_int(row.get("top") or row.get("y")),
        ),
    )
    live = sorted(
        (
            row
            for row in semantic_candidates
            if isinstance(row, dict) and _semantic_candidate_visible(row)
        ),
        key=lambda row: (
            _top(row) if _top(row) is not None else float("inf"),
            _as_int(row.get("index")),
        ),
    )
    used: set[int] = set()
    out: list[Section] = []

    def choose(section: Section) -> Section | None:
        available = [
            (candidate_index, candidate)
            for candidate_index, candidate in enumerate(live)
            if candidate_index not in used
        ]

        def horizontal_rank(pair: tuple[int, Section]) -> int:
            return (
                0
                if _horizontally_visible(pair[1], active_view_width)
                else 1
            )

        section_id = _section_id(section)
        if section_id:
            exact = [
                pair
                for pair in available
                if _section_id(pair[1]) == section_id
            ]
            if exact:
                candidate_index, candidate = min(
                    exact,
                    key=lambda pair: (horizontal_rank(pair), pair[0]),
                )
                used.add(candidate_index)
                return candidate

        section_tag = _tag(section)
        section_classes = _class_tokens(_class_name(section))
        if section_classes:
            class_matches = [
                pair
                for pair in available
                if _tag(pair[1]) == section_tag
                and bool(section_classes & _class_tokens(_class_name(pair[1])))
            ]
            if class_matches:
                # Extraction-time tops describe a different viewport, so raw
                # top distance can invert adjacent repeated wrappers at mobile
                # breakpoints. Prefer an exact class signature and preserve
                # live DOM order among repeated exact matches; only fall back
                # to geometry for partial class overlap.
                exact_class_matches = [
                    pair
                    for pair in class_matches
                    if _class_tokens(_class_name(pair[1])) == section_classes
                ]
                if exact_class_matches:
                    repeated_exact_class = sum(
                        1
                        for mapped_section in sections
                        if _tag(mapped_section) == section_tag
                        and _class_tokens(_class_name(mapped_section))
                        == section_classes
                    ) > 1
                    if repeated_exact_class:
                        candidate_index, candidate = min(
                            exact_class_matches,
                            key=lambda pair: (horizontal_rank(pair), pair[0]),
                        )
                    else:
                        candidate_index, candidate = min(
                            exact_class_matches,
                            key=lambda pair: (
                                horizontal_rank(pair),
                                _top_distance(section, pair[1]),
                                abs(_height(section) - _height(pair[1])),
                                _as_int(pair[1].get("index")),
                            ),
                        )
                else:
                    candidate_index, candidate = min(
                        class_matches,
                        key=lambda pair: (
                            horizontal_rank(pair),
                            _top_distance(section, pair[1]),
                            abs(_height(section) - _height(pair[1])),
                            _as_int(pair[1].get("index")),
                        ),
                    )
                used.add(candidate_index)
                return candidate

        if (
            not section_id
            and not section_classes
            and section_tag in _LANDMARK_TAGS
        ):
            landmark_matches = [
                pair
                for pair in available
                if _tag(pair[1]) == section_tag
            ]
            if landmark_matches:
                candidate_index, candidate = min(
                    landmark_matches,
                    key=lambda pair: (horizontal_rank(pair), pair[0]),
                )
                used.add(candidate_index)
                return candidate
        return None

    for index, section in enumerate(sections):
        height = _as_int(section.get("height") or section.get("h"))
        if height < _MIN_VISIBLE_HEIGHT:
            continue
        section_id = _section_id(section)
        class_name = _class_name(section)
        tag = _tag(section) or "section"
        if (
            tag not in _SEMANTIC_TAGS
            and not section_id
            and not _lockable_class_tokens(class_name)
        ):
            # Extraction-only geometry cannot establish a stable boundary for
            # an anonymous non-semantic child. Affirmative runtime enumeration
            # may still add it during the merge below.
            continue
        top_value = section.get("top")
        if top_value is None:
            top_value = section.get("y")
        left_value = section.get("left")
        if left_value is None:
            left_value = section.get("x")
        top = _as_int(top_value)
        left = _as_int(left_value)
        width = _as_int(
            section.get("width") or section.get("w"),
            active_view_width,
        )
        seed = section_id or class_name or f"sec-{index}"
        normalized_seed = re.sub(r"[^a-z0-9 ]", "", seed.lower())[:100]
        text_words = re.sub(
            r"\s+",
            " ",
            re.sub(
                r"[^a-z0-9 ]",
                " ",
                str(section.get("textPreview") or seed).lower(),
            ),
        ).strip()[:800]
        row: Section = {
            "index": len(out),
            "tag": tag,
            "id": section_id or None,
            "className": class_name[:80],
            "fingerprint": normalized_seed,
            "textWords": text_words,
            "hasSvgText": False,
            "rect": {
                "top": top,
                "left": left,
                "width": width,
                "height": height,
            },
            "display": section.get("display") or "block",
            "gridCols": section.get("gridCols") or None,
            "childCount": _as_int(section.get("childCount")),
            "hasVisibleMedia": section.get("hasVisibleMedia") is True,
            "visibleMediaCount": _as_int(section.get("visibleMediaCount")),
            "visibleMediaKinds": (
                section.get("visibleMediaKinds")
                if isinstance(section.get("visibleMediaKinds"), list)
                else []
            ),
            "visibleMediaKindCounts": (
                section.get("visibleMediaKindCounts")
                if isinstance(section.get("visibleMediaKindCounts"), dict)
                else {}
            ),
        }

        candidate = choose(section)
        if candidate is not None:
            if not _horizontally_visible(candidate, active_view_width):
                continue
            rect = _rect_from(candidate.get("rect"))
            if rect is not None:
                row["rect"] = {
                    "top": round(rect["top"]),
                    "left": round(rect["left"]),
                    "width": round(rect["width"]),
                    "height": round(rect["height"]),
                }
            for key in (
                "display",
                "gridCols",
                "childCount",
                "clientWidth",
                "contentBox",
                "contentGroups",
                "leftGap",
                "rightGap",
                "fingerprint",
                "textWords",
                "hasSvgText",
                "hasVisibleMedia",
                "visibleMediaCount",
                "visibleMediaKinds",
                "visibleMediaKindCounts",
            ):
                value = candidate.get(key)
                if value is not None:
                    row[key] = value
        else:
            # Private merge hint: this geometry is extraction-time fallback,
            # not affirmative live evidence. It must never escape the public
            # synthesized result.
            row["_sectionMapFallback"] = True
        out.append(row)

    visible_runtime = [
        row
        for row in (runtime_sections or [])
        if isinstance(row, dict) and _horizontally_visible(row, active_view_width)
    ]
    return merge_ref_runtime_sections(out, visible_runtime)


def merge_ref_runtime_sections(
    synthesized: list[Section],
    runtime_sections: list[Section],
) -> list[Section]:
    """Merge affirmative live ref rows into section-map-derived ref rows.

    A coarse landmark synthesized from ``section-map.json`` may contain live
    runtime rows that the map intentionally does not enumerate, such as
    GitHub Docs' large classed ``div.mt-6``. Keeping those rows lets normal
    one-to-one pairing prove the corresponding impl descendant. Containment
    alone is never treated as proof.

    Runtime rows are removed only when they describe the same near-exact
    region as a row already kept. A class-only, exactly-one-child ``div`` may
    also be treated as wrapper noise when exactly one stronger live landmark/id
    row proves the same page-scale geometry. Near-exact wrappers use bounded
    2.5% edge insets; same-top full-page wrappers may be 85% as tall to account
    for extraction-time document-height drift. Ambiguous candidates are kept.
    """
    merged = [
        dict(row)
        for row in synthesized
        if isinstance(row, dict)
    ]

    def weak_class_wrapper(row: Section) -> bool:
        return (
            _tag(row) == "div"
            and not _section_id(row)
            and bool(_class_tokens(_class_name(row)))
            and row.get("childCount") is not None
            and _as_int(row.get("childCount")) == 1
        )

    def stronger_semantic_row(row: Section) -> bool:
        # Cross-tag wrapper replacement needs semantic element evidence, not
        # merely an arbitrary id on a coincident component-sized node.
        return _tag(row) in _LANDMARK_TAGS

    def merge_live_row(
        existing: Section,
        runtime_row: Section,
        *,
        replace_identity: bool,
    ) -> Section:
        replacement = dict(existing)
        if replace_identity:
            for key in (
                "id",
                "className",
                "fingerprint",
                "textWords",
                "tag",
                "childCount",
            ):
                replacement.pop(key, None)
        replacement.update(runtime_row)
        return replacement

    def merge_runtime_measurements(
        existing: Section,
        runtime_row: Section,
    ) -> Section:
        replacement = dict(existing)
        for key in (
            "rect",
            "display",
            "gridCols",
            "childCount",
            "clientWidth",
            "contentBox",
            "contentGroups",
            "leftGap",
            "rightGap",
            "fingerprint",
            "textWords",
            "hasSvgText",
            "hasVisibleMedia",
            "visibleMediaCount",
            "visibleMediaKinds",
            "visibleMediaKindCounts",
        ):
            value = runtime_row.get(key)
            if value is not None:
                replacement[key] = value
        return replacement

    def fallback_identity_match(candidate: Section, existing: Section) -> bool:
        if not existing.get("_sectionMapFallback"):
            return False
        candidate_id = _section_id(candidate)
        existing_id = _section_id(existing)
        if candidate_id or existing_id:
            return bool(
                candidate_id
                and existing_id
                and candidate_id == existing_id
            )

        unstable_tokens = {
            "active",
            "closed",
            "container",
            "content",
            "current",
            "hidden",
            "inner",
            "open",
            "ready",
            "section",
            "selected",
            "show",
            "shown",
            "visible",
            "wrapper",
        }

        def stable_tokens(row: Section) -> set[str]:
            return {
                token
                for token in _class_tokens(_class_name(row))
                if token not in unstable_tokens
                and not token.startswith(("has-", "is-", "js-"))
            }

        candidate_tokens = stable_tokens(candidate)
        existing_tokens = stable_tokens(existing)
        return (
            _tag(candidate) == _tag(existing)
            and bool(candidate_tokens)
            and candidate_tokens == existing_tokens
        )

    def wrapper_semantic_duplicate(
        candidate: Section,
        existing: Section,
        candidate_rect: Rect,
        existing_rect: Rect,
    ) -> bool:
        if weak_class_wrapper(candidate) and stronger_semantic_row(existing):
            wrapper_rect, semantic_rect = candidate_rect, existing_rect
        elif weak_class_wrapper(existing) and stronger_semantic_row(candidate):
            wrapper_rect, semantic_rect = existing_rect, candidate_rect
        else:
            return False

        if (
            abs(wrapper_rect["left"] - semantic_rect["left"]) > 1.0
            or abs(wrapper_rect["width"] - semantic_rect["width"]) > 1.0
            or wrapper_rect["width"] <= 0
            or wrapper_rect["height"] <= 0
            or semantic_rect["height"] <= 0
        ):
            return False
        height_ratio = min(
            wrapper_rect["height"],
            semantic_rect["height"],
        ) / max(wrapper_rect["height"], semantic_rect["height"])
        wrapper_bottom = wrapper_rect["top"] + wrapper_rect["height"]
        semantic_bottom = semantic_rect["top"] + semantic_rect["height"]
        same_top_page_wrapper = (
            min(wrapper_rect["height"], semantic_rect["height"]) >= 1000.0
            and abs(wrapper_rect["top"] - semantic_rect["top"]) <= 2.0
            and wrapper_rect["top"] >= semantic_rect["top"] - 1.0
            and wrapper_bottom <= semantic_bottom + 1.0
            and height_ratio >= 0.85
        )
        if same_top_page_wrapper:
            return True
        if height_ratio < 0.95:
            return False

        inset_tolerance = max(8.0, 0.025 * semantic_rect["height"])
        top_inset = wrapper_rect["top"] - semantic_rect["top"]
        bottom_inset = semantic_bottom - wrapper_bottom
        return (
            -1.0 <= top_inset <= inset_tolerance
            and -1.0 <= bottom_inset <= inset_tolerance
        )

    wrapper_semantic_match_counts: dict[int, int] = {}
    for existing in merged:
        if not weak_class_wrapper(existing):
            continue
        existing_rect = _rect_from(existing.get("rect"))
        if existing_rect is None:
            continue
        wrapper_semantic_match_counts[id(existing)] = sum(
            1
            for runtime_row in runtime_sections
            if isinstance(runtime_row, dict)
            and _visible(runtime_row)
            and (runtime_rect := _rect_from(runtime_row.get("rect"))) is not None
            and wrapper_semantic_duplicate(
                runtime_row,
                existing,
                runtime_rect,
                existing_rect,
            )
        )

    def near_exact_duplicate(candidate: Section, existing: Section) -> bool:
        candidate_rect = _rect_from(candidate.get("rect"))
        existing_rect = _rect_from(existing.get("rect"))
        if candidate_rect is None or existing_rect is None:
            return False

        if wrapper_semantic_duplicate(
            candidate,
            existing,
            candidate_rect,
            existing_rect,
        ):
            wrapper = candidate if weak_class_wrapper(candidate) else existing
            if wrapper is existing:
                return wrapper_semantic_match_counts.get(id(existing), 0) == 1
            return True

        def ratio(a: float, b: float) -> float:
            high = max(a, b)
            if high <= 0:
                return 1.0
            return min(a, b) / high

        if ratio(candidate_rect["height"], existing_rect["height"]) < 0.9:
            return False
        if ratio(candidate_rect["width"], existing_rect["width"]) < 0.9:
            return False

        horizontal_tolerance = max(
            8.0,
            0.02 * max(candidate_rect["width"], existing_rect["width"]),
        )
        candidate_right = candidate_rect["left"] + candidate_rect["width"]
        existing_right = existing_rect["left"] + existing_rect["width"]
        if (
            abs(candidate_rect["left"] - existing_rect["left"])
            > horizontal_tolerance
            or abs(candidate_right - existing_right) > horizontal_tolerance
        ):
            # Equal-sized two-column siblings often share the same top and
            # height. They are separate sections unless both horizontal edges
            # also identify the same rendered region.
            return False

        edge_tolerance = max(
            8.0,
            0.02 * max(candidate_rect["height"], existing_rect["height"]),
        )
        candidate_bottom = candidate_rect["top"] + candidate_rect["height"]
        existing_bottom = existing_rect["top"] + existing_rect["height"]
        same_region = (
            _vertical_iou(candidate_rect, existing_rect) >= 0.9
            or (
                abs(candidate_rect["top"] - existing_rect["top"])
                <= edge_tolerance
                and abs(candidate_bottom - existing_bottom) <= edge_tolerance
            )
        )
        if not same_region:
            return False

        candidate_id = _section_id(candidate)
        existing_id = _section_id(existing)
        if candidate_id or existing_id:
            return bool(
                candidate_id
                and existing_id
                and candidate_id == existing_id
            )
        if _tag(candidate) != _tag(existing):
            return False
        if _class_tokens(_class_name(candidate)) & _class_tokens(
            _class_name(existing)
        ):
            return True
        return True

    # The section map itself can contain the same live region more than once
    # (for example a component row plus an inherited semantic row with the same
    # class and exact rect). Previously only runtime rows were deduplicated
    # against the synthesized list, so those pre-existing duplicates survived
    # into matches.json and shifted crop suffixes between frozen passes.
    deduped_synthesized: list[Section] = []
    for synthesized_row in merged:
        if any(
            near_exact_duplicate(synthesized_row, existing)
            for existing in deduped_synthesized
        ):
            continue
        deduped_synthesized.append(synthesized_row)
    merged = deduped_synthesized

    for runtime_row in runtime_sections:
        if not isinstance(runtime_row, dict) or not _visible(runtime_row):
            continue
        fallback_indices = [
            index
            for index, existing in enumerate(merged)
            if fallback_identity_match(runtime_row, existing)
        ]
        if len(fallback_indices) == 1:
            fallback_index = fallback_indices[0]
            merged[fallback_index] = merge_live_row(
                merged[fallback_index],
                runtime_row,
                replace_identity=False,
            )
            continue
        duplicate_indices = [
            index
            for index, existing in enumerate(merged)
            if near_exact_duplicate(runtime_row, existing)
        ]
        if len(duplicate_indices) == 1:
            duplicate_index = duplicate_indices[0]
            if (
                weak_class_wrapper(merged[duplicate_index])
                and stronger_semantic_row(runtime_row)
                ):
                merged[duplicate_index] = merge_live_row(
                    merged[duplicate_index],
                    runtime_row,
                    replace_identity=True,
                )
            else:
                merged[duplicate_index] = merge_runtime_measurements(
                    merged[duplicate_index],
                    runtime_row,
                )
            continue
        merged.append(dict(runtime_row))

    # A repeated runtime row can become ambiguous after an earlier live row was
    # merged into a synthesized wrapper: both identity and duplicate lookups may
    # then return multiple candidates, causing the old loop to append the same
    # rendered region again. De-duplicate the completed merge as well as the
    # synthesized input so frozen passes cannot acquire suffix-shifting twins.
    final_merged: list[Section] = []
    for row in merged:
        if any(near_exact_duplicate(row, existing) for existing in final_merged):
            continue
        final_merged.append(row)
    merged = final_merged

    merged.sort(
        key=lambda row: (
            _top(row) if _top(row) is not None else float("inf"),
            _as_int(row.get("index")),
        )
    )
    public_rows: list[Section] = []
    for index, row in enumerate(merged):
        public_row = dict(row)
        public_row.pop("_sectionMapFallback", None)
        public_rows.append(_copy_with_index(public_row, index))
    return public_rows


_GENERIC_TAG_TOKENS = {
    "section", "header", "footer", "article", "aside", "main", "nav", "figure",
}


def _make_name(item: Section, fallback_prefix: str) -> str:
    raw = item.get("captureName") or item.get("id") or ""
    if not raw and item.get("className"):
        raw = str(item["className"]).split()[0]
    if not raw:
        raw = f"{fallback_prefix}-{item['index']}"
    return safe_section_name(raw, max_length=40)


def _dedup_name(base: str, used: set[str]) -> str:
    if base not in used:
        used.add(base)
        return base
    i = 2
    while f"{base}-{i}" in used:
        i += 1
    n = f"{base}-{i}"
    used.add(n)
    return n


def _norm_key(s: Section) -> list[str]:
    raw = " ".join(str(s.get(k) or "") for k in ("id", "tag", "className"))
    tokens = [
        t for t in "".join(c if c.isalnum() else " " for c in raw.lower()).split()
        if len(t) >= 4
    ]
    return [t for t in tokens if t not in _GENERIC_TAG_TOKENS]


def _has_identity_overlap(a: Section, b: Section) -> bool:
    return bool(set(_norm_key(a)) & set(_norm_key(b)))


# Function words carry no section identity — two unrelated sections both say
# "the", "and", "of". Strip them before measuring text overlap so the signal
# reflects distinctive content words ("pyramid", "resources", "faq").
_TEXT_STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "your", "our", "with",
    "that", "this", "from", "has", "have", "was", "were", "all", "can", "will",
    "its", "his", "her", "their", "they", "them", "out", "who", "what", "when",
    "how", "why", "into", "than", "then", "now", "get", "got", "use", "used",
    "been", "being", "more", "most", "some", "any", "each", "every", "about",
    "over", "under", "also", "just", "only", "very", "such", "these", "those",
}

# Pairing requires text similarity at or above this floor. Tuned so a short,
# distinctive ref seed fully contained in a long impl section (containment 1.0)
# anchors, while incidental single-stopword overlaps (stripped above) do not.
_STRONG_TEXT_SIM = 0.4


def _text_word_set(row: Section) -> set[str]:
    """Distinctive visible-text words for a section.

    Prefers the full normalized innerText (`textWords`); falls back to the
    legacy `fingerprint` (first-100-char text OR class-derived seed) so the
    matcher still works on artifacts captured before `textWords` existed.
    """
    raw = str(row.get("textWords") or row.get("fingerprint") or "")
    normalized = "".join(c if c.isalnum() else " " for c in raw.lower())
    return {
        w for w in normalized.split()
        if len(w) >= 3 and w not in _TEXT_STOPWORDS
    }


def text_similarity(a: Section, b: Section) -> float:
    """Content similarity by what two sections SAY, not what they are named.

    Returns max(Jaccard, containment) over distinctive word sets. Containment
    (|A∩B| / min(|A|,|B|)) handles the asymmetric case where one side is a
    short label/seed and the other is the full rendered paragraph — a faithful
    clone of a CSS-Modules reference where class signatures share nothing.
    """
    wa = _text_word_set(a)
    wb = _text_word_set(b)
    if not wa or not wb:
        return 0.0
    inter = wa & wb
    if not inter:
        return 0.0
    jaccard = len(inter) / len(wa | wb)
    containment = len(inter) / min(len(wa), len(wb))
    sim = max(jaccard, containment)
    # A single shared word across two large vocabularies is weak evidence;
    # damp it unless one side is a short, focused label.
    if len(inter) == 1 and min(len(wa), len(wb)) > 4:
        sim *= 0.5
    return sim


def _rect_size_sim(a: Section, b: Section) -> float:
    """Scale-robust similarity of two section boxes by width+height ratio.

    Absolute coordinates drift between ref/impl (different scroll phase), so we
    compare SIZE (min/max ratio per dimension), not position — a same-shaped
    footer scores ~1.0 regardless of where each side captured it.
    """
    ra = a.get("rect") if isinstance(a.get("rect"), dict) else {}
    rb = b.get("rect") if isinstance(b.get("rect"), dict) else {}

    def _dim(rect: object, key: str) -> float:
        if not isinstance(rect, dict):
            return 0.0
        try:
            return float(rect.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    sims: list[float] = []
    for key in ("width", "height"):
        x, y = _dim(ra, key), _dim(rb, key)
        hi = max(x, y)
        sims.append((min(x, y) / hi) if hi > 0 else 1.0)
    return sum(sims) / len(sims)


def _identity_pair_score(r: Section, im: Section) -> float:
    """Composite identity-pairing score (tools-batch-11 ITEM 4a).

    A single shared id/class token (e.g. a generic "footer" id on two distinct
    sections) cannot disambiguate which impl a ref pairs to. Blend id-exact +
    class-token Jaccard (weighted — the class signature is the decider) + rect
    SIZE similarity + DOM-order proximity so the correct same-id section wins
    over a nearest-by-index blank. Pairing only; the AE/dssim/structure compare
    downstream is unchanged, so better pairing yields more accurate measurement,
    never an easier pass.
    """
    score = 0.0
    rid, iid = _section_id(r), _section_id(im)
    if rid and rid == iid:
        score += 1.0
    rt, it = _class_tokens(_class_name(r)), _class_tokens(_class_name(im))
    if rt and it:
        inter = rt & it
        if inter:
            score += 2.0 * (len(inter) / len(rt | it))
    score += _rect_size_sim(r, im)
    # Order proximity: prefer the same-identity candidate that sits at the same
    # place. Use document position (viewport-scaled) when both sides have a
    # rect — exact in self-pass, order-preserving in a faithful clone — and fall
    # back to DOM-index distance only when a rect is missing. This is what
    # disambiguates two id="footer" sections with distinct class signatures that
    # share the generic id token; it never outweighs the id-exact (+1.0) or
    # class-signature (+2.0) terms above.
    td = _top_distance(r, im)
    if td != float("inf"):
        score += 0.25 / (1.0 + td / 800.0)
    else:
        score += 0.25 / (1.0 + abs(_as_int(r.get("index")) - _as_int(im.get("index"))))
    return score


# ── Anchor-first / Y-order-stable pairing (rank-3) ─────────────────────────
# A faithful clone of a CSS-modules reference reuses some of the ref's compiled
# class names on a few sections (hero / stats / footer) while rendering the rest
# with empty className/id (Tailwind). The verified geometry is monotonic in Y:
# impl.top = ref.top + a small, growing global drift. The global, order-blind
# text/identity pairing below RESHUFFLES on any section-count change — a ref with
# an incidental text/token collision steals the impl that belongs to its
# Y-neighbour, leaving a real section MISSING and fabricating an EXTRA twin.
#
# Phase A locks the high-confidence identity anchors that ALSO preserve Y-order;
# Phase B fills the gaps between locked anchors by monotonic Y-order so each ref
# pairs to the impl that sits in the same Y-band; only genuine orphans fall back
# to the text/identity heuristic (Phase C, the legacy stages). This is a PAIRING
# signal only — the AE/dssim/structure compare downstream is unchanged, so a more
# position-consistent pairing yields more accurate measurement, never an easier
# pass. NO-OP-equivalent in ref-vs-ref self-pass: identity is present on every
# section, Y-order is perfect, drift ~0, so Phase A locks all pairs 1:1.

# An identity anchor must clear this composite score to be eligible for locking.
# id-exact alone contributes +1.0 and a full class-signature match +2.0, so the
# floor (1.5) demands more than a single shared generic token (e.g. two sections
# sharing only a "section" token score ~0.7 from a partial Jaccard and never
# lock). Tuned against realfood: hero/stats/winning/end/cta/eatReal all clear it;
# the spurious faqs<->cta "section"-token overlap (score ~1.93 but Y-order
# inconsistent) is rejected by the monotonic-chain selection, not this floor.
_ANCHOR_SCORE_FLOOR = 1.5


def _anchor_score(r: Section, im: Section) -> float | None:
    """Identity-anchor strength for a (ref, impl) pair, or None when no identity.

    Returns the composite `_identity_pair_score` only when the pair shares a
    distinctive exact id or class token. Generic carousel/library state tokens
    are intentionally ignored here so they cannot become locked anchors; the
    later identity stage remains unchanged. PAIRING ONLY.
    """
    rid, iid = _section_id(r), _section_id(im)
    id_match = (
        bool(rid)
        and rid == iid
        and rid.lower() not in _GENERIC_IDENTITY_ANCHOR_TOKENS
    )
    r_tokens = _lockable_class_tokens(_class_name(r))
    im_tokens = _lockable_class_tokens(_class_name(im))
    tok_match = bool(r_tokens & im_tokens)
    if not id_match and not tok_match:
        return None
    return _identity_pair_score(r, im)


def _lock_identity_anchors(
    ref: list[Section],
    impl: list[Section],
    eligible_ref: list[int],
    used_impl: set[int],
) -> dict[int, int]:
    """Phase A — lock a Y-monotonic chain of high-confidence identity anchors.

    Build every (ref, impl) identity-anchor candidate at or above the score
    floor, then select a subset that (1) is 1:1 and (2) is strictly increasing
    in BOTH ref document-top and impl document-top — i.e. an order-preserving
    assignment. This forbids an anchor that would cross a stronger one out of
    Y-order (the realfood faqs<->cta "section"-token collision), so a locked
    anchor can never be stolen later nor violate the page's vertical order.

    Greedy-by-score selection with a Y-order feasibility check is deterministic
    and, on the self-pass (impl==ref), trivially locks every section to itself:
    each ref's strongest anchor is its own copy, all are Y-monotonic. Ties break
    on score then ref/impl index (no RNG/clock — repo scripts forbid them).

    Returns the locked {ref_index: impl_index} map and mutates used_impl.
    """
    ref_by_index = {r["index"]: r for r in ref}
    candidates: list[tuple[float, float, int, int]] = []
    for r_idx in eligible_ref:
        r = ref_by_index.get(r_idx)
        if r is None:
            continue
        for im in impl:
            if im["index"] in used_impl:
                continue
            score = _anchor_score(r, im)
            if score is None or score < _ANCHOR_SCORE_FLOOR:
                continue
            candidates.append((score, _top_distance(r, im), r_idx, im["index"]))

    # Strongest-first; ties on smallest top-distance, then indices (determinism).
    candidates.sort(key=lambda c: (-c[0], c[1], c[2], c[3]))

    locked: dict[int, int] = {}
    locked_impl: dict[int, int] = {}  # impl_index -> ref_index (reverse lookup)
    for _score, _td, r_idx, im_idx in candidates:
        if r_idx in locked or im_idx in locked_impl:
            continue
        r_top = _top(ref_by_index[r_idx])
        im_top = _top(next(x for x in impl if x["index"] == im_idx))
        # Y-order feasibility: a ref above an already-locked ref must pair to an
        # impl above that ref's locked impl, and vice-versa. A pair that would
        # cross a locked anchor in Y is rejected (it is a token collision, not a
        # real anchor). When a top is missing fall back to DOM index order.
        if not _y_order_consistent(
            r_idx, im_idx, r_top, im_top, locked, ref_by_index, impl
        ):
            continue
        locked[r_idx] = im_idx
        locked_impl[im_idx] = r_idx
        used_impl.add(im_idx)
    return locked


def _y_order_consistent(
    r_idx: int,
    im_idx: int,
    r_top: float | None,
    im_top: float | None,
    locked: dict[int, int],
    ref_by_index: dict[int, Section],
    impl: list[Section],
) -> bool:
    """True when adding (r_idx -> im_idx) preserves Y-order vs every locked pair.

    For each already-locked (lr -> li): if the new ref sits ABOVE lr it must map
    ABOVE li, and if BELOW lr it must map BELOW li. Ordering uses document-top
    when both sides have a rect (exact in self-pass, order-preserving in a
    faithful clone) and falls back to DOM index when a top is missing.
    """
    impl_top_by_index = {im["index"]: _top(im) for im in impl}

    def _ref_before(a: int, b: int) -> bool:
        ta = _top(ref_by_index[a]) if a in ref_by_index else None
        tb = _top(ref_by_index[b]) if b in ref_by_index else None
        if ta is not None and tb is not None:
            return ta < tb
        return a < b

    def _impl_before(a: int, b: int) -> bool:
        ta, tb = impl_top_by_index.get(a), impl_top_by_index.get(b)
        if ta is not None and tb is not None:
            return ta < tb
        return a < b

    for lr, li in locked.items():
        ref_lt = _ref_before(r_idx, lr)
        impl_lt = _impl_before(im_idx, li)
        if ref_lt != impl_lt:
            return False
    return True


def _fill_between_anchors(
    ref: list[Section],
    impl: list[Section],
    locked: dict[int, int],
    used_impl: set[int],
    eligible_ref: list[int],
) -> dict[int, int]:
    """Phase B — pair remaining ref/impl by monotonic Y-order within anchor gaps.

    The locked anchors from Phase A partition the page into Y-bands. Within each
    band (between two consecutive locked refs, or before the first / after the
    last), the still-free ref sections and still-free impl sections are each in
    Y-order; pair them positionally band-by-band. When a band has more refs than
    free impls, the surplus refs stay unpaired (a genuine enumeration gap — the
    impl rendered fewer sections in that region, e.g. realfood card_bg collapsed
    into its pyramid sibling). When it has more impls than refs, the surplus
    impls stay free for Phase C / EXTRA.

    Pairs WITHIN a band by closest drift to the band's anchor drift, so a ref
    pairs to the impl that sits at the corresponding Y-offset rather than blindly
    by within-band rank — this keeps the assignment robust when the band holds an
    unequal count on each side. Order-preserving: never pairs a ref to an impl
    that lies outside (above the upper anchor / below the lower anchor) its band.

    Returns newly-paired {ref_index: impl_index}; mutates used_impl.
    """
    ref_by_index = {r["index"]: r for r in ref}
    impl_by_index = {im["index"]: im for im in impl}

    def _ref_top_or_index(ri: int) -> float:
        t = _top(ref_by_index[ri])
        return t if t is not None else float(ri)

    # Locked refs in Y-order define the band boundaries.
    locked_refs_sorted = sorted(locked, key=_ref_top_or_index)

    def _impl_top(im_idx: int) -> float | None:
        return _top(impl_by_index[im_idx])

    # Boundaries as (lower_ref_top, upper_ref_top, lower_impl_top, upper_impl_top)
    # with -inf/+inf sentinels for the open ends.
    boundaries: list[tuple[float, float, float, float]] = []
    prev_r_top, prev_i_top = float("-inf"), float("-inf")
    for ri in locked_refs_sorted:
        rt = _top(ref_by_index[ri])
        it = _impl_top(locked[ri])
        rt_f = rt if rt is not None else prev_r_top
        it_f = it if it is not None else prev_i_top
        boundaries.append((prev_r_top, rt_f, prev_i_top, it_f))
        prev_r_top, prev_i_top = rt_f, it_f
    boundaries.append((prev_r_top, float("inf"), prev_i_top, float("inf")))

    free_refs = [
        ri for ri in eligible_ref
        if ri not in locked and _top(ref_by_index.get(ri, {})) is not None
    ]
    free_impls = [
        im["index"] for im in impl
        if im["index"] not in used_impl and _top(im) is not None
    ]

    new_pairs: dict[int, int] = {}
    for r_lo, r_hi, i_lo, i_hi in boundaries:
        band_refs = sorted(
            (ri for ri in free_refs if r_lo < (_top(ref_by_index[ri]) or 0) < r_hi),
            key=lambda ri: _top(ref_by_index[ri]) or 0.0,
        )
        band_impls = sorted(
            (ii for ii in free_impls if i_lo < (_impl_top(ii) or 0) < i_hi),
            key=lambda ii: _impl_top(ii) or 0.0,
        )
        if not band_refs or not band_impls:
            continue
        # Reference drift for this band. A faithful clone's drift grows
        # MONOTONICALLY down the page, so a single fixed drift mis-ranks pairs in
        # a tall band where drift climbs from the lower to the upper anchor. Take
        # the MIDPOINT of the two bounding anchors' drifts as the band reference,
        # and size the tolerance to cover the band's drift SPAN (how much drift
        # grows across it) plus the gross-outlier floor. Open-ended bands (before
        # the first / after the last anchor) inherit the single available anchor's
        # drift. This admits every in-band pair (whose drift sits between the two
        # anchors) while still forbidding the cross-band surplus gap (card_bg's
        # ~+4500px jump far exceeds the span+floor tolerance).
        lo_drift = i_lo - r_lo if i_lo != float("-inf") and r_lo != float("-inf") else None
        hi_drift = i_hi - r_hi if i_hi != float("inf") and r_hi != float("inf") else None
        drifts = [d for d in (lo_drift, hi_drift) if d is not None]

        # An anchor on at least one side is REQUIRED. A band with no bounding
        # anchor (the whole page, when Phase A locked nothing) has no reliable
        # drift reference, so Y-order alignment cannot disambiguate which impl a
        # ref pairs to — the text/identity stages (Phase C) handle that case
        # correctly by CONTENT. Defer such bands entirely rather than guess by
        # geometry. (Also keeps Phase B inert on clones that share no class
        # tokens at all, preserving the legacy text-first behavior there.)
        if not drifts:
            continue

        band_drift = sum(drifts) / len(drifts)
        drift_span = (max(drifts) - min(drifts)) if len(drifts) == 2 else 0.0

        # Order-preserving optimal assignment (sequence-alignment DP). When the
        # band holds an unequal count on each side, a greedy top-to-bottom walk
        # can pair the wrong surplus (it forces the LOWER ref to consume the last
        # impl, stranding the section that actually has no partner). The DP picks
        # the monotonic subset of (ref, impl) pairs that MAXIMIZES pairs, breaking
        # ties by MINIMIZING total drift deviation from band_drift, so the
        # genuinely-unmatched section (the one the impl never rendered) falls out
        # as the surplus — e.g. realfood card_bg, whose impl collapsed into its
        # pyramid sibling. A pair whose deviation exceeds the band tolerance is
        # forbidden (it is a cross-band mispair, not a real partner). Each pair is
        # rewarded so the DP prefers pairing; the deviation is a tie-break penalty
        # scaled below the reward. Deterministic (no RNG/clock).
        rt_list = [_top(ref_by_index[ri]) or 0.0 for ri in band_refs]
        it_list = [_impl_top(ii) or 0.0 for ii in band_impls]
        n, m = len(band_refs), len(band_impls)
        # A pair must keep its drift within this much of the band reference. The
        # floor + the band's drift span absorb a faithful clone's intra-band
        # growth; it stays well under the surplus gap (card_bg's +4500px).
        band_tol = _DRIFT_OUTLIER_FLOOR_PX + drift_span
        # Maximize pairs (reward 1.0 each), minimize total deviation as a
        # sub-unit tie-break. dp[i][j] = best (pairs, -deviation) for the
        # suffixes; compared lexicographically.
        dp: list[list[tuple[float, float]]] = [
            [(0.0, 0.0)] * (m + 1) for _ in range(n + 1)
        ]
        back = [[0] * (m + 1) for _ in range(n + 1)]  # 0=skip-ref,1=skip-impl,2=pair
        for i in range(n - 1, -1, -1):
            for j in range(m - 1, -1, -1):
                # skip-ref / skip-impl carry their suffix value unchanged.
                skip_ref = dp[i + 1][j]
                skip_impl = dp[i][j + 1]
                best, mv = skip_ref, 0
                if skip_impl > best:
                    best, mv = skip_impl, 1
                dev = abs((it_list[j] - rt_list[i]) - band_drift)
                if dev <= band_tol:
                    sub = dp[i + 1][j + 1]
                    pair = (sub[0] + 1.0, sub[1] - dev)
                    if pair > best:
                        best, mv = pair, 2
                dp[i][j] = best
                back[i][j] = mv
        i = j = 0
        while i < n and j < m:
            mv = back[i][j]
            if mv == 2:
                ri, ii = band_refs[i], band_impls[j]
                new_pairs[ri] = ii
                used_impl.add(ii)
                i += 1
                j += 1
            elif mv == 0:
                i += 1
            else:
                j += 1

    return new_pairs


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


# Gross-outlier floor (px). A pairing whose vertical drift differs from the
# per-page median by MORE than max(this, 3*MAD) is rejected as a mispair. The
# 300px floor is conservative — a faithful clone's section tops cluster within a
# few hundred px of a consistent global offset, so this never fires on normal
# sections. It is also the ref-vs-ref-self-pass safety: in self-pass every drift
# is ~0, so the median is ~0 and no pair can exceed a 300px deviation — the
# repair pass is a strict NO-OP there (achievability meta-gate stays green).
_DRIFT_OUTLIER_FLOOR_PX = 300.0


def _pair_drift(r: Section, im: Section) -> float | None:
    """Vertical drift impl.top - ref.top, or None when either side lacks a top.

    This is the per-pair signal the repair pass clusters on: a faithful clone's
    correct pairings share a near-constant global offset, so a pairing whose
    drift is a gross outlier crops the wrong impl region (BLACK / bogus AE).
    """
    rt, it = _top(r), _top(im)
    if rt is None or it is None:
        return None
    return it - rt


def _repair_drift_outliers(
    ref: list[Section],
    impl: list[Section],
    preferred_impl: dict[int, int],
    used_impl: set[int],
    off_canvas_refs: set[int],
    text_paired: dict[int, float] | None = None,
    semantic_key_paired: set[int] | None = None,
    locked_pairs: dict[int, int] | None = None,
) -> set[int]:
    """Reject gross drift-outlier pairings in favor of position-consistent ones.

    The text/identity stages can pair two sections that SAY the same thing (a
    CTA whose section-map textWords were captured from a shared-base-class FAQ
    sibling) or that share a generic id/class token, even when their document
    positions are wildly inconsistent with the rest of the page. Such a pairing
    crops the wrong impl region and produces a BLACK / bogus-AE verdict.

    A faithful clone's correct pairings share a near-constant vertical offset
    (the impl is uniformly taller/shorter by the intro delta). We compute the
    per-page MEDIAN drift and its MAD, flag any assigned pair whose drift is a
    gross outlier (|drift - median| > max(300px, 3*MAD)), RELEASE it, and try to
    re-pair the freed ref to a still-free impl whose drift is consistent with
    the page median. The released impl re-enters the free pool so a different
    ref (or the order-consistent candidate) can claim it.

    PAIRING ONLY — the downstream AE/dssim/structure compare is unchanged, so a
    more position-consistent pairing yields more accurate measurement, never an
    easier pass. NO-OP in the ref-vs-ref self-pass: every drift is ~0 there, the
    median is ~0, and no pair can exceed the 300px floor.

    Mutates preferred_impl / used_impl (and the text_paired / semantic_key_paired
    label sets, if passed) in place. Returns the set of ref indices that were
    successfully RE-paired to a position-consistent impl, so the caller can label
    them `position-repaired` instead of inheriting a stale text/semantic label.
    Deterministic ordering only.
    """
    ref_by_index = {r["index"]: r for r in ref}
    impl_by_index = {im["index"]: im for im in impl}

    # Drift baseline = every currently-assigned content pair. After Phase A/B
    # these are almost all position-consistent, so the median + MAD capture the
    # page's true (monotonically-growing) drift band. NOTE: do NOT baseline on
    # the Phase-A locked anchors alone — on a tall page the locks straddle the
    # full 0..N px drift growth, making their distribution BIMODAL (half near the
    # top offset, half near the bottom), which collapses the MAD to ~0 and would
    # then flag a perfectly-consistent early-page pair as an outlier. The full
    # assigned set keeps a healthy MAD across the monotonic spread. Skip
    # off-canvas synthetic pairs (they sit at the ref's own off-canvas rect and
    # are not page-flow).
    assigned_drifts: list[float] = []
    for r_idx, im_idx in preferred_impl.items():
        if r_idx in off_canvas_refs:
            continue
        r = ref_by_index.get(r_idx)
        im = impl_by_index.get(im_idx)
        if r is None or im is None:
            continue
        d = _pair_drift(r, im)
        if d is not None:
            assigned_drifts.append(d)

    # Need a stable majority to define "normal" drift. With <3 measurable pairs
    # there is no reliable median to judge an outlier against — leave pairing
    # untouched (the small-page / degenerate case).
    if len(assigned_drifts) < 3:
        return set()

    median_drift = _median(assigned_drifts)
    mad = _median([abs(d - median_drift) for d in assigned_drifts])
    threshold = max(_DRIFT_OUTLIER_FLOOR_PX, 3.0 * mad)

    # Identify outlier pairs (deterministic order: by ref index). Never flag a
    # Phase-A locked anchor — those are the order-consistent ground truth the
    # baseline is built from, so they cannot be outliers by construction.
    locked = locked_pairs or {}
    outliers: list[int] = []
    for r_idx in sorted(preferred_impl):
        if r_idx in off_canvas_refs or r_idx in locked:
            continue
        r = ref_by_index.get(r_idx)
        im = impl_by_index.get(preferred_impl[r_idx])
        if r is None or im is None:
            continue
        d = _pair_drift(r, im)
        if d is None:
            continue
        if abs(d - median_drift) > threshold:
            outliers.append(r_idx)

    if not outliers:
        return set()

    # Release every outlier's impl first so freed impls can be reclaimed by the
    # order-consistent candidate (e.g. a ref that should own an impl currently
    # held by an outlier pairing). Drop the stale text/semantic labels too — a
    # re-paired ref must materialize via the position-anchored fallback, not the
    # text/semantic score from the rejected pairing.
    for r_idx in outliers:
        im_idx = preferred_impl.pop(r_idx)
        used_impl.discard(im_idx)
        if text_paired is not None:
            text_paired.pop(r_idx, None)
        if semantic_key_paired is not None:
            semantic_key_paired.discard(r_idx)

    # Re-pair each freed ref (deterministic order) to the still-free impl whose
    # drift is MOST consistent with the page median. Only accept a candidate
    # whose drift deviation is within the threshold AND strictly better than the
    # rejected pairing by a meaningful margin — otherwise leave the ref unpaired
    # (UNMATCHED) rather than re-introduce a different gross mispair.
    repaired: set[int] = set()
    for r_idx in outliers:
        r = ref_by_index.get(r_idx)
        if r is None:
            continue
        best_im_idx: int | None = None
        best_dev = float("inf")
        for im in impl:
            if im["index"] in used_impl:
                continue
            d = _pair_drift(r, im)
            if d is None:
                continue
            dev = abs(d - median_drift)
            if dev > threshold:
                continue
            # Prefer the most position-consistent candidate; break ties on
            # text similarity then DOM-index proximity (deterministic).
            key_dev = dev
            if best_im_idx is None or key_dev < best_dev or (
                key_dev == best_dev
                and best_im_idx is not None
                and (
                    text_similarity(r, im),
                    -abs(r["index"] - im["index"]),
                )
                > (
                    text_similarity(r, impl_by_index[best_im_idx]),
                    -abs(r["index"] - impl_by_index[best_im_idx]["index"]),
                )
            ):
                best_dev = key_dev
                best_im_idx = im["index"]
        if best_im_idx is not None:
            preferred_impl[r_idx] = best_im_idx
            used_impl.add(best_im_idx)
            repaired.add(r_idx)
        # else: leave UNMATCHED — the downstream loop emits status UNMATCHED,
        # which is an honest "no consistent impl" verdict, never a false pass.

    return repaired


def pair_sections(ref: list[Section], impl: list[Section]) -> list[Section]:
    """Pair ref sections to impl sections, returning a matches list.

    Pairing order, strongest signal first:
    (A) ANCHOR-LOCK — Y-order-stable identity anchors. Pairs that share an exact
        id/class token AND preserve the page's monotonic Y-order are LOCKED:
        they cannot be stolen by a later stage. This makes the assignment
        deterministic under section-count perturbation — a ref with an
        incidental text/token collision can no longer reshuffle the pairing of
        its Y-neighbours.
    (B) Y-ORDER FILL — remaining ref/impl are aligned by monotonic document-top
        within the bands the locked anchors carve out, so a ref at a given Y
        pairs to the impl at the corresponding Y position. Surplus refs in a
        band stay unpaired (a genuine enumeration gap), never reshuffling.
    (C) the legacy fallback for genuine orphans, strongest signal first:
        (1) TEXT-CONTENT similarity — what a section SAYS;
        (2) semantic-key identity overlap (id/class tokens);
        (3) className-exact tokens;
        (4) text similarity with a same-tag + DOM-order tiebreaker.
    A drift-outlier repair pass (baselined on the LOCKED anchors) rejects any
    surviving gross mispair.

    This is a PAIRING signal only — the AE/dssim/structure comparison
    downstream is unchanged, so better pairing yields more accurate
    measurement, never an easier pass. Self-pass (impl==ref): identity is
    present on every section, Y-order is perfect, drift ~0, so Phase A locks
    all pairs 1:1 and B/C/repair are no-ops.
    """
    matches: list[Section] = []
    used_impl: set[int] = set()
    used_names: set[str] = set()
    preferred_impl: dict[int, int] = {}

    # ── Off-canvas pre-pass ──
    # A ref row whose stored rect lies entirely above the canvas is a settled
    # splash/overlay the ref itself unmounted (loop-e2e-4 intro at -900..0).
    # The impl has no enumerable candidate, so normal pairing garbage-matches
    # it to an unrelated on-canvas section and the compare crops painted
    # content against the ref's transparent off-canvas stub. Pair it to a
    # SYNTHETIC impl entry carrying the same rect: both sides then crop the
    # identical off-canvas region (deterministic transparent stubs, AE 0) and
    # no real impl section is consumed.
    off_canvas_refs: set[int] = set()
    for r in ref:
        rect = r.get("rect") or {}
        try:
            off = float(rect.get("top", 0)) + float(rect.get("height", 0)) <= 0
        except (TypeError, ValueError):
            off = False
        if off:
            off_canvas_refs.add(r["index"])

    eligible_ref = [r["index"] for r in ref if r["index"] not in off_canvas_refs]

    # ── Phase A: lock Y-order-stable identity anchors ──
    # High-confidence id/class anchors that preserve the page's monotonic Y-order
    # are pinned 1:1 and may never be stolen by a later stage. On the self-pass
    # this locks every section to its own copy.
    locked_pairs = _lock_identity_anchors(ref, impl, eligible_ref, used_impl)
    preferred_impl.update(locked_pairs)

    # ── Phase B: fill anchor gaps by monotonic Y-order ──
    # Within each Y-band carved out by the locked anchors, pair the still-free
    # ref/impl sections by document-top so a ref at a given Y maps to the impl at
    # the corresponding Y. Surplus refs in a band stay unpaired (enumeration gap,
    # e.g. realfood card_bg collapsed into its pyramid sibling).
    band_pairs = _fill_between_anchors(
        ref, impl, locked_pairs, used_impl, eligible_ref
    )
    preferred_impl.update(band_pairs)
    band_paired: set[int] = set(band_pairs)

    # ── TEXT-CONTENT pre-pass (strongest signal, runs first) ──
    # Collect every (ref, impl) candidate at or above the strong-text floor and
    # assign 1:1, globally-best-first. Ties break deterministically on index
    # proximity then index (no RNG/clock — repo scripts forbid them).
    text_paired: dict[int, float] = {}
    text_candidates: list[tuple[float, float, int, int, int]] = []
    for r in ref:
        if r["index"] in off_canvas_refs or r["index"] in preferred_impl:
            continue
        for im in impl:
            if im["index"] in used_impl:
                continue
            sim = text_similarity(r, im)
            if sim >= _STRONG_TEXT_SIM:
                # Tiebreak among equal-text candidates by POSITION first (the
                # section-map duplicates the same innerText onto adjacent
                # same-class rows, so two ref rows tie at sim=1.0 to one impl
                # row — position picks the right one), then DOM-index, then
                # indices for determinism.
                text_candidates.append(
                    (sim, _top_distance(r, im), abs(r["index"] - im["index"]),
                     r["index"], im["index"])
                )
    text_candidates.sort(key=lambda t: (-t[0], t[1], t[2], t[3], t[4]))
    for sim, _td, _dist, r_idx, im_idx in text_candidates:
        if r_idx in preferred_impl or im_idx in used_impl:
            continue
        preferred_impl[r_idx] = im_idx
        used_impl.add(im_idx)
        text_paired[r_idx] = sim

    # ── Identity stage (semantic-key + class-signature), GLOBALLY disambiguated ──
    # batch-11 ITEM 4(a): a single shared id/class token (e.g. a generic "footer"
    # id on two distinct sections — a CTA section and a content section) overlaps
    # EVERY footer, so the old greedy per-ref index-proximity tiebreak cross-paired
    # the ref CTA section -> the nearest impl blank footer and stranded the real
    # CTA. Score every
    # identity-overlapping (ref, impl) candidate with _identity_pair_score
    # (id-exact + class-token Jaccard + rect-size + DOM-order) and assign 1:1
    # GLOBALLY best-first, so the class signature — not raw index distance —
    # decides which same-id footer pairs where. Deterministic ordering only
    # (no RNG/clock — repo scripts forbid them).
    semantic_key_paired: set[int] = set()
    ident_candidates: list[tuple[float, float, int, int, int, bool]] = []
    for r in ref:
        if r["index"] in preferred_impl or r["index"] in off_canvas_refs:
            continue
        r_class = _class_tokens(_class_name(r))
        for im in impl:
            if im["index"] in used_impl:
                continue
            sem = _has_identity_overlap(r, im)
            cls_overlap = bool(r_class & _class_tokens(_class_name(im)))
            if not sem and not cls_overlap:
                continue
            ident_candidates.append(
                (
                    _identity_pair_score(r, im),
                    _top_distance(r, im),
                    abs(r["index"] - im["index"]),
                    r["index"],
                    im["index"],
                    sem,
                )
            )
    ident_candidates.sort(key=lambda t: (-t[0], t[1], t[2], t[3], t[4]))
    for _score, _td, _dist, r_idx, im_idx, sem in ident_candidates:
        if r_idx in preferred_impl or im_idx in used_impl:
            continue
        preferred_impl[r_idx] = im_idx
        used_impl.add(im_idx)
        if sem:
            # id/tag/class-token (semantic-key) overlap; otherwise it is a pure
            # class-signature match, labeled className-exact in the materializer.
            semantic_key_paired.add(r_idx)

    # ── Drift-outlier repair (gross mispair rejection) ──
    # Both stages above can assign a high-confidence pairing whose vertical
    # position is wildly inconsistent with the rest of the page — a CTA that
    # inherited a sibling FAQ's textWords (shared base class), or a section that
    # shares a generic id/class token with a far-away impl block. Reject such
    # gross drift outliers in favor of a position-consistent candidate. NO-OP in
    # ref-vs-ref self-pass (all drifts ~0). Repaired pairs lose their text/
    # semantic label below (no longer in text_paired/semantic_key_paired), so
    # they materialize via the position-anchored fallback score.
    drift_repaired: set[int] = _repair_drift_outliers(
        ref, impl, preferred_impl, used_impl, off_canvas_refs,
        text_paired, semantic_key_paired, locked_pairs,
    )

    for r in ref:
        if r["index"] in off_canvas_refs:
            rect = dict(r.get("rect") or {})
            name = _dedup_name(_make_name(r, "section"), used_names)
            matches.append({
                "name": name,
                "score": 1.0,
                "ref": r,
                "impl": {
                    "rect": rect,
                    "className": r.get("className"),
                    "tag": r.get("tag"),
                    "offCanvas": True,
                },
                "pairing": "off-canvas",
            })
            continue
        if r["index"] in preferred_impl:
            anchored = next(
                (x for x in impl if x["index"] == preferred_impl[r["index"]]), None
            )
            if anchored:
                name = _dedup_name(_make_name(r, "section"), used_names)
                if r["index"] in drift_repaired:
                    # Re-paired by the drift-outlier repair pass after its
                    # text/semantic pairing was rejected as a gross mispair.
                    pairing_kind = "position-repaired"
                    score_val: float = round(
                        text_similarity(r, anchored), 3
                    )
                elif r["index"] in locked_pairs:
                    # Phase A: Y-order-stable identity anchor (id/class + Y-order).
                    pairing_kind = "anchor-locked"
                    score_val = 1.0
                elif r["index"] in band_paired:
                    # Phase B: paired by monotonic Y-order within an anchor gap.
                    pairing_kind = "y-order"
                    score_val = round(text_similarity(r, anchored), 3)
                elif r["index"] in semantic_key_paired:
                    pairing_kind = "semantic-key"
                    score_val = 1.0
                elif r["index"] in text_paired:
                    pairing_kind = "text-content"
                    score_val = round(text_paired[r["index"]], 3)
                else:
                    pairing_kind = "className-exact"
                    score_val = 1.0
                matches.append({
                    "name": name,
                    "score": score_val,
                    "ref": r,
                    "impl": anchored,
                    "pairing": pairing_kind,
                })
                continue

        # Fallback: no identity or strong-text anchor. Text similarity is the
        # primary signal; same-tag + DOM order survive only as a final
        # tiebreaker (the +0.1 nudge cannot outweigh any real text overlap).
        best_score = 0.0
        best_impl: Section | None = None
        for im in impl:
            if im["index"] in used_impl:
                continue
            score = text_similarity(r, im)
            if r.get("tag") == im.get("tag"):
                score += 0.1
            if score > best_score:
                best_score = score
                best_impl = im

        if best_impl and best_score > 0.05:
            used_impl.add(best_impl["index"])
            name = _dedup_name(_make_name(r, "section"), used_names)
            is_wrapper = (
                not str(r.get("fingerprint", "")).strip()
                and _as_int(r.get("childCount")) <= 1
            )
            entry: Section = {
                "name": name,
                "score": round(best_score, 3),
                "ref": r,
                "impl": best_impl,
            }
            if is_wrapper:
                entry["wrapper"] = True
            matches.append(entry)
        else:
            name = _dedup_name(_make_name(r, "section"), used_names)
            matches.append({
                "name": name,
                "score": 0,
                "ref": r,
                "impl": None,
                "status": "UNMATCHED",
            })

    for im in impl:
        if im["index"] not in used_impl:
            name = _dedup_name(_make_name(im, "impl-section"), used_names)
            matches.append({
                "name": name,
                "score": 0,
                "ref": None,
                "impl": im,
                "status": "EXTRA_IN_IMPL",
            })

    return matches


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
        # (loop-e2e-5: the hero-video block between hero and stats, orphaned
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


def calculate_mask_coverage(
    matches: Sequence[object], mask_rects: Sequence[object]
) -> dict[str, float]:
    """Compute percent of each matched REF section covered by dynamic masks.

    Values are sidecar evidence only. They do not affect section-compare pass
    rows; a later gate can use this JSON to detect pass-under-mask cases.
    """
    masks = [rect for raw in mask_rects if (rect := _rect_from(raw)) is not None]
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
        clipped = [
            rect for mask in masks if (rect := _intersect(section, mask)) is not None
        ]
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


# ── Cross-section cumulative-drift diagnostic (read-only) ──
# The matcher pairs ref<->impl sections but offers no view of the cumulative
# vertical drift that turns one baked/collapsed section into a cascade of
# downstream "victim" sections — each shifted by every height error above it.
# This is a purely ADDITIVE observation surface: it never touches matches.json
# contents, the pairing, any verdict, or any gate. It only reads the final
# paired list and emits a sidecar table + a stdout summary so a human/agent can
# see WHERE drift was injected and which earlier section's height delta most
# plausibly caused each jump.
_DRIFT_JUMP_EPSILON = 8.0


def _rect_top(side: object) -> float | None:
    if not isinstance(side, dict):
        return None
    rect = side.get("rect")
    if not isinstance(rect, dict):
        return None
    value = rect.get("top")
    if isinstance(value, bool) or not _is_number(value):
        return None
    return float(value)


def _rect_height(side: object) -> float | None:
    if not isinstance(side, dict):
        return None
    rect = side.get("rect")
    if not isinstance(rect, dict):
        return None
    value = rect.get("height")
    if isinstance(value, bool) or not _is_number(value):
        return None
    return float(value)


def build_drift_diagnostic(
    matches: Sequence[Section], jump_epsilon: float = _DRIFT_JUMP_EPSILON
) -> dict[str, Any]:
    """Produce a read-only cross-section cumulative-drift diagnostic.

    Given the final paired list, build a table sorted by ref top with per-pair
    drift (impl top - ref top) and height delta (impl height - ref height), and
    attribute each running-drift JUMP to the PREVIOUS in-order section's height
    delta — the section whose baked/extra height (positive hDelta) or
    collapsed-overlap / dropped negative-margin (negative hDelta) pushed every
    following section down.

    Pure observation: callers must not feed the result back into pairing,
    verdicts, or gating. Pairs missing a top on either side carry null drift and
    are skipped for jump attribution (never crash).
    """
    rows: list[dict[str, Any]] = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        ref = match.get("ref")
        impl = match.get("impl")
        ref_top = _rect_top(ref)
        impl_top = _rect_top(impl)
        ref_h = _rect_height(ref)
        impl_h = _rect_height(impl)
        drift = (
            impl_top - ref_top
            if ref_top is not None and impl_top is not None
            else None
        )
        h_delta = (
            impl_h - ref_h if ref_h is not None and impl_h is not None else None
        )
        rows.append(
            {
                "name": str(match.get("name") or ""),
                "refTop": ref_top,
                "implTop": impl_top,
                "drift": drift,
                "refH": ref_h,
                "implH": impl_h,
                "hDelta": h_delta,
                "score": match.get("score"),
            }
        )

    # Sort by ref top so the table reads top-to-bottom of the page. Rows with no
    # ref top sink to the end (they have no place in the vertical cascade) while
    # staying in the table for completeness.
    table = sorted(
        rows,
        key=lambda r: (r["refTop"] is None, r["refTop"] if r["refTop"] is not None else 0.0),
    )

    jumps: list[dict[str, Any]] = []
    prev_drift: float | None = None
    prev_row: dict[str, Any] | None = None
    for row in table:
        drift = row["drift"]
        if drift is None:
            # Cannot place this pair in the running cascade; reset the chain so a
            # later pair is not compared against a stale drift across a gap.
            prev_drift = None
            prev_row = None
            continue
        if (
            prev_drift is not None
            and prev_row is not None
            and drift - prev_drift > jump_epsilon
        ):
            cause_h_delta = prev_row.get("hDelta")
            if _is_number(cause_h_delta) and cause_h_delta < 0:
                cause = "dropped negative-margin / collapsed-overlap"
            else:
                cause = "baked/extra height"
            jumps.append(
                {
                    "at": row["name"],
                    "cause": prev_row["name"],
                    "causeHDelta": cause_h_delta,
                    "driftIncrease": round(drift - prev_drift, 4),
                    "fromDrift": prev_drift,
                    "toDrift": drift,
                    "reason": cause,
                }
            )
        prev_drift = drift
        prev_row = row

    drifts = [r["drift"] for r in table if _is_number(r["drift"])]
    total_drift_range = {
        "min": min(drifts) if drifts else None,
        "max": max(drifts) if drifts else None,
    }

    return {
        "table": table,
        "jumps": jumps,
        "totalDriftRange": total_drift_range,
    }


def _format_drift_value(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def print_drift_diagnostic(diagnostic: dict[str, Any]) -> None:
    """Print a compact human-readable cumulative-drift table to stdout."""
    table = diagnostic.get("table") or []
    jumps = diagnostic.get("jumps") or []
    if not table:
        return
    print("  cumulative drift (impl top - ref top), sorted by ref top:")
    print(
        f"    {'name':<28} {'refTop':>8} {'implTop':>8} "
        f"{'drift':>7} {'hDelta':>8} {'score':>6}"
    )
    for row in table:
        print(
            f"    {str(row.get('name') or '')[:28]:<28} "
            f"{_format_drift_value(row.get('refTop')):>8} "
            f"{_format_drift_value(row.get('implTop')):>8} "
            f"{_format_drift_value(row.get('drift')):>7} "
            f"{_format_drift_value(row.get('hDelta')):>8} "
            f"{_format_drift_value(row.get('score')):>6}"
        )
    if jumps:
        print("  drift jumps (running drift increased > epsilon):")
        for jump in jumps:
            print(
                f"    +{_format_drift_value(jump.get('driftIncrease'))} at "
                f"'{jump.get('at')}' <- '{jump.get('cause')}' "
                f"(hDelta={_format_drift_value(jump.get('causeHDelta'))}, "
                f"{jump.get('reason')})"
            )


def _load_json(path: Path) -> object | None:
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
        return data
    except (OSError, json.JSONDecodeError):
        return None


def _cmd_augment_impl(args: argparse.Namespace) -> int:
    section_map_raw = _load_json(Path(args.section_map))
    impl_sections_raw = _load_json(Path(args.impl_sections))
    candidates_raw = _load_json(Path(args.semantic_candidates))

    if not isinstance(section_map_raw, dict):
        return 0
    if not isinstance(impl_sections_raw, list) or not isinstance(candidates_raw, list):
        return 0

    augmented = augment_impl_sections_from_section_map(
        section_map_raw,
        [row for row in impl_sections_raw if isinstance(row, dict)],
        [row for row in candidates_raw if isinstance(row, dict)],
    )
    Path(args.impl_sections).write_text(json.dumps(augmented, indent=2), encoding="utf-8")
    return 0


def _cmd_synthesize_ref(args: argparse.Namespace) -> int:
    section_map_raw = _load_json(Path(args.section_map))
    candidates_raw = _load_json(Path(args.semantic_candidates))
    runtime_raw = (
        _load_json(Path(args.runtime_sections))
        if args.runtime_sections
        else []
    )
    if not isinstance(section_map_raw, dict):
        return 0

    candidates = (
        [row for row in candidates_raw if isinstance(row, dict)]
        if isinstance(candidates_raw, list)
        else []
    )
    runtime_sections = (
        [row for row in runtime_raw if isinstance(row, dict)]
        if isinstance(runtime_raw, list)
        else []
    )
    synthesized = synthesize_ref_sections_from_section_map(
        section_map_raw,
        candidates,
        active_view_width=args.active_view_width,
        runtime_sections=runtime_sections,
    )
    if len(synthesized) < 3:
        return 0
    Path(args.ref_sections).write_text(
        json.dumps(synthesized, indent=2),
        encoding="utf-8",
    )
    return 0


def _cmd_pair(args: argparse.Namespace) -> int:
    ref_raw = _load_json(Path(args.ref_sections))
    impl_raw = _load_json(Path(args.impl_sections))
    if not isinstance(ref_raw, list) or not isinstance(impl_raw, list):
        return 1

    ref = _pair_input_sections([row for row in ref_raw if isinstance(row, dict)])
    impl = _pair_input_sections([row for row in impl_raw if isinstance(row, dict)])
    matches = pair_sections(ref, impl)
    out_path = Path(args.out)
    out_path.write_text(json.dumps(matches, indent=2), encoding="utf-8")

    # Read-only cumulative-drift diagnostic. This is a SIDECAR next to
    # matches.json; it never alters matches.json, the pairing, or any verdict.
    diagnostic = build_drift_diagnostic(matches)
    sidecar = out_path.parent / "drift-diagnostic.json"
    sidecar.write_text(json.dumps(diagnostic, indent=2), encoding="utf-8")

    matched = len([m for m in matches if m.get("impl")])
    unmatched_ref = len([m for m in matches if not m.get("impl")])
    extra_impl = len([m for m in matches if not m.get("ref")])
    print(f"  {matched} matched, {unmatched_ref} unmatched ref, {extra_impl} extra impl")
    print_drift_diagnostic(diagnostic)
    return 0


def _cmd_mask_coverage(args: argparse.Namespace) -> int:
    matches_raw = _load_json(Path(args.matches))
    mask_rects_raw = _load_json(Path(args.mask_rects))
    matches = [row for row in matches_raw if isinstance(row, dict)] if isinstance(matches_raw, list) else []
    mask_rects = (
        [row for row in mask_rects_raw if isinstance(row, dict)]
        if isinstance(mask_rects_raw, list)
        else []
    )
    coverage = calculate_mask_coverage(matches, mask_rects)
    Path(args.out).write_text(json.dumps(coverage, indent=2, sort_keys=True), encoding="utf-8")
    return 0


def _cmd_crop_manifest(args: argparse.Namespace) -> int:
    matches_raw = _load_json(Path(args.matches))
    matches = matches_raw if isinstance(matches_raw, list) else []
    manifest = build_crop_manifest(
        matches,
        Path(args.ref_dir),
        Path(args.impl_dir),
    )
    Path(args.out).write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return 0


def _cmd_promote_impl_path(args: argparse.Namespace) -> int:
    matches_raw = _load_json(Path(args.matches))
    matches = matches_raw if isinstance(matches_raw, list) else []
    promoted = promote_impl_path_reference(matches)
    if not promoted:
        return 1
    Path(args.out).write_text(
        json.dumps(promoted, indent=2),
        encoding="utf-8",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    augment = sub.add_parser("augment-impl")
    augment.add_argument("section_map")
    augment.add_argument("impl_sections")
    augment.add_argument("semantic_candidates")
    augment.set_defaults(func=_cmd_augment_impl)

    synthesize_ref = sub.add_parser("synthesize-ref")
    synthesize_ref.add_argument("section_map")
    synthesize_ref.add_argument("ref_sections")
    synthesize_ref.add_argument("semantic_candidates")
    synthesize_ref.add_argument("active_view_width", type=int)
    synthesize_ref.add_argument("runtime_sections", nargs="?")
    synthesize_ref.set_defaults(func=_cmd_synthesize_ref)

    pair = sub.add_parser("pair")
    pair.add_argument("ref_sections")
    pair.add_argument("impl_sections")
    pair.add_argument("out")
    pair.set_defaults(func=_cmd_pair)

    mask_coverage = sub.add_parser("mask-coverage")
    mask_coverage.add_argument("matches")
    mask_coverage.add_argument("mask_rects")
    mask_coverage.add_argument("out")
    mask_coverage.set_defaults(func=_cmd_mask_coverage)

    crop_manifest = sub.add_parser("crop-manifest")
    crop_manifest.add_argument("matches")
    crop_manifest.add_argument("ref_dir")
    crop_manifest.add_argument("impl_dir")
    crop_manifest.add_argument("out")
    crop_manifest.set_defaults(func=_cmd_crop_manifest)

    promote_impl_path = sub.add_parser("promote-impl-path")
    promote_impl_path.add_argument("matches")
    promote_impl_path.add_argument("out")
    promote_impl_path.set_defaults(func=_cmd_promote_impl_path)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
