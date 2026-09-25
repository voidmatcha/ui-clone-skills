"""Section-compare pairing/synthesis CLI and facade.

The implementation lives in sibling modules (``section_compare_common``,
``_synthesis``, ``_merge``, ``_scoring``, ``_pairing``, ``_coverage``,
``_drift``); every name they define is re-exported here so existing imports,
``python -m ui_clone.section_compare_sections`` invocations, and monkeypatch
targets keep working unchanged.
"""

from __future__ import annotations

import argparse
import json
import os  # noqa: F401 - kept as a module attribute for import-compat
import re  # noqa: F401 - kept as a module attribute for import-compat
from collections.abc import Sequence  # noqa: F401 - kept for import-compat
from pathlib import Path
from typing import TYPE_CHECKING, Any  # noqa: F401 - kept for import-compat

from ui_clone.extraction_artifacts import _is_valid_selector  # noqa: F401 - import-compat
from ui_clone.section_capture import safe_section_name  # noqa: F401 - import-compat
from ui_clone.section_compare_common import (
    _GENERIC_IDENTITY_ANCHOR_TOKENS,
    _LANDMARK_TAGS,
    _MIN_LANDMARK_HEIGHT,
    _MIN_VISIBLE_HEIGHT,
    _OVERLAP_HEIGHT_RATIO,
    _OVERLAP_IOU_THRESHOLD,
    _OVERLAP_WIDTH_RATIO,
    _SEMANTIC_TAGS,
    Rect,
    Section,
    _as_int,
    _class_name,
    _class_tokens,
    _copy_with_index,
    _float_value,
    _height,
    _height_comparable,
    _horizontally_visible,
    _identity_matches,
    _intersect,
    _is_number,
    _lockable_class_tokens,
    _pair_input_sections,
    _pair_viewport_width,
    _positionally_overlaps,
    _rect_from,
    _same_section_instance,
    _section_id,
    _section_map_candidate,
    _semantic_candidate_visible,
    _tag,
    _top,
    _top_distance,
    _union_area,
    _vertical_iou,
    _visible,
    _width_comparable,
    has_grid_layout_mismatch,
)
from ui_clone.section_compare_coverage import (
    build_crop_manifest,
    calculate_mask_coverage,
    find_large_extra_sections,
    parse_agent_browser_json_list,
    promote_impl_path_reference,
)
from ui_clone.section_compare_drift import (
    _DRIFT_JUMP_EPSILON,
    _format_drift_value,
    _rect_height,
    _rect_top,
    build_drift_diagnostic,
    print_drift_diagnostic,
)
from ui_clone.section_compare_merge import merge_ref_runtime_sections
from ui_clone.section_compare_pairing import (
    _DRIFT_OUTLIER_FLOOR_PX,
    _fill_between_anchors,
    _lock_identity_anchors,
    _median,
    _pair_drift,
    _repair_drift_outliers,
    _y_order_consistent,
    pair_sections,
)
from ui_clone.section_compare_scoring import (
    _ANCHOR_SCORE_FLOOR,
    _GENERIC_TAG_TOKENS,
    _STRONG_TEXT_SIM,
    _TEXT_STOPWORDS,
    _anchor_score,
    _dedup_name,
    _has_identity_overlap,
    _identity_pair_score,
    _make_name,
    _norm_key,
    _rect_size_sim,
    _text_word_set,
    text_similarity,
)
from ui_clone.section_compare_synthesis import (
    augment_impl_sections_from_section_map,
    synthesize_ref_sections_from_section_map,
)

__all__ = [
    "Rect",
    "Section",
    "_ANCHOR_SCORE_FLOOR",
    "_DRIFT_JUMP_EPSILON",
    "_DRIFT_OUTLIER_FLOOR_PX",
    "_GENERIC_IDENTITY_ANCHOR_TOKENS",
    "_GENERIC_TAG_TOKENS",
    "_LANDMARK_TAGS",
    "_MIN_LANDMARK_HEIGHT",
    "_MIN_VISIBLE_HEIGHT",
    "_OVERLAP_HEIGHT_RATIO",
    "_OVERLAP_IOU_THRESHOLD",
    "_OVERLAP_WIDTH_RATIO",
    "_SEMANTIC_TAGS",
    "_STRONG_TEXT_SIM",
    "_TEXT_STOPWORDS",
    "_anchor_score",
    "_as_int",
    "_class_name",
    "_class_tokens",
    "_copy_with_index",
    "_dedup_name",
    "_fill_between_anchors",
    "_float_value",
    "_format_drift_value",
    "_has_identity_overlap",
    "_height",
    "_height_comparable",
    "_horizontally_visible",
    "_identity_matches",
    "_identity_pair_score",
    "_intersect",
    "_is_number",
    "_lock_identity_anchors",
    "_lockable_class_tokens",
    "_make_name",
    "_median",
    "_norm_key",
    "_pair_drift",
    "_pair_input_sections",
    "_pair_viewport_width",
    "_positionally_overlaps",
    "_rect_from",
    "_rect_height",
    "_rect_size_sim",
    "_rect_top",
    "_repair_drift_outliers",
    "_same_section_instance",
    "_section_id",
    "_section_map_candidate",
    "_semantic_candidate_visible",
    "_tag",
    "_text_word_set",
    "_top",
    "_top_distance",
    "_union_area",
    "_vertical_iou",
    "_visible",
    "_width_comparable",
    "_y_order_consistent",
    "augment_impl_sections_from_section_map",
    "build_crop_manifest",
    "build_drift_diagnostic",
    "calculate_mask_coverage",
    "find_large_extra_sections",
    "has_grid_layout_mismatch",
    "main",
    "merge_ref_runtime_sections",
    "pair_sections",
    "parse_agent_browser_json_list",
    "print_drift_diagnostic",
    "promote_impl_path_reference",
    "synthesize_ref_sections_from_section_map",
    "text_similarity",
]


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
