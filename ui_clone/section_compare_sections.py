from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

Section = dict[str, Any]

_MIN_VISIBLE_HEIGHT = 50
_SEMANTIC_TAGS = {"main", "section", "header", "footer", "nav", "article"}


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


def _height(row: Section) -> int:
    rect = row.get("rect")
    if isinstance(rect, dict):
        return _as_int(rect.get("height"))
    return _as_int(row.get("height") or row.get("h"))


def _visible(row: Section) -> bool:
    return _height(row) >= _MIN_VISIBLE_HEIGHT


def _section_map_candidate(row: Section) -> bool:
    return _tag(row) in _SEMANTIC_TAGS and _visible(row) and bool(
        _section_id(row) or _class_tokens(_class_name(row))
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


def _copy_with_index(row: Section, index: int) -> Section:
    copied = dict(row)
    copied["index"] = index
    return copied


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
    candidates for section-map rows that have stable identity and are missing
    from runtime impl sections.
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

    for section_row in sorted_sections:
        if not _section_map_candidate(section_row):
            continue
        if any(_identity_matches(section_row, row) for row in augmented):
            continue

        match = next(
            (
                row
                for row in semantic_candidates
                if _visible(row) and _identity_matches(section_row, row)
            ),
            None,
        )
        if match is None:
            continue

        augmented.append(_copy_with_index(match, next_index))
        next_index += 1

    return augmented


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    augment = sub.add_parser("augment-impl")
    augment.add_argument("section_map")
    augment.add_argument("impl_sections")
    augment.add_argument("semantic_candidates")
    augment.set_defaults(func=_cmd_augment_impl)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
