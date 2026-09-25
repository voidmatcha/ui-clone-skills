"""Merge section-map rows with runtime-observed ref sections (section-compare).

Moved verbatim out of ``ui_clone.section_compare_sections``; that module
re-exports every name here.
"""

from __future__ import annotations

from ui_clone.section_compare_common import (
    _LANDMARK_TAGS,
    Rect,
    Section,
    _as_int,
    _class_name,
    _class_tokens,
    _copy_with_index,
    _rect_from,
    _section_id,
    _tag,
    _top,
    _vertical_iou,
    _visible,
)


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

        if _tag(candidate) != _tag(existing):
            return False

        candidate_classes = _class_tokens(_class_name(candidate))
        existing_classes = _class_tokens(_class_name(existing))
        class_overlap = candidate_classes & existing_classes

        candidate_id = _section_id(candidate)
        existing_id = _section_id(existing)

        def class_derived_identity(row_id: str, classes: set[str]) -> bool:
            return bool(row_id and row_id in classes)

        if candidate_id or existing_id:
            if candidate_id and existing_id:
                return candidate_id == existing_id
            # section-map.json stores the first captured class in ``name``.
            # _section_id() intentionally treats that as identity elsewhere,
            # but for duplicate removal it is only a class alias: the matching
            # runtime row has no DOM id/name and would otherwise be appended as
            # a phantom duplicate despite exact tag/class/rect equality.
            lone_id = candidate_id or existing_id
            lone_id_is_class_alias = (
                class_derived_identity(candidate_id, candidate_classes)
                or class_derived_identity(existing_id, existing_classes)
            )
            return bool(class_overlap and lone_id and lone_id_is_class_alias)

        # Neither row carries an identity: same tag + same rendered region is
        # the whole duplicate signal, with or without a shared class token.
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
            elif (
                weak_class_wrapper(runtime_row)
                and stronger_semantic_row(merged[duplicate_index])
            ):
                continue
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
