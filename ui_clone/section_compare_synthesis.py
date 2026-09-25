"""Ref/impl section-list synthesis from section-map.json (section-compare).

Moved verbatim out of ``ui_clone.section_compare_sections``; that module
re-exports every name here.
"""

from __future__ import annotations

import re

from ui_clone.extraction_artifacts import _is_valid_selector
from ui_clone.section_compare_common import (
    _LANDMARK_TAGS,
    _MIN_VISIBLE_HEIGHT,
    _SEMANTIC_TAGS,
    Section,
    _as_int,
    _class_name,
    _class_tokens,
    _copy_with_index,
    _height,
    _horizontally_visible,
    _identity_matches,
    _lockable_class_tokens,
    _positionally_overlaps,
    _rect_from,
    _same_section_instance,
    _section_id,
    _section_map_candidate,
    _semantic_candidate_visible,
    _tag,
    _top,
    _top_distance,
    _width_comparable,
)
from ui_clone.section_compare_merge import merge_ref_runtime_sections


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
        selector = section.get("selector")
        if _is_valid_selector(selector):
            row["selector"] = " ".join(str(selector).split())

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
