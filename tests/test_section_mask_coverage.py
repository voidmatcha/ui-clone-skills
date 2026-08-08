from __future__ import annotations

from ui_clone.section_compare_sections import calculate_mask_coverage


def test_mask_coverage_uses_union_of_dynamic_rect_intersections() -> None:
    matches = [
        {
            "name": "hero",
            "ref": {"rect": {"top": 0, "left": 0, "width": 100, "height": 100}},
            "impl": {"rect": {"top": 0, "left": 0, "width": 100, "height": 100}},
        },
        {
            "name": "news",
            "ref": {"rect": {"top": 100, "left": 0, "width": 100, "height": 100}},
            "impl": {"rect": {"top": 100, "left": 0, "width": 100, "height": 100}},
        },
    ]
    mask_rects = [
        {"top": 0, "left": 0, "width": 100, "height": 50},
        {"top": 25, "left": 0, "width": 100, "height": 50},
        {"top": 125, "left": 50, "width": 100, "height": 50},
    ]

    assert calculate_mask_coverage(matches, mask_rects) == {
        "hero": 75.0,
        "news": 25.0,
    }


def test_mask_coverage_omits_unmatched_impl_extras() -> None:
    matches = [
        {
            "name": "impl-section-extra",
            "ref": None,
            "impl": {"rect": {"top": 0, "left": 0, "width": 100, "height": 100}},
        }
    ]

    assert calculate_mask_coverage(matches, [{"top": 0, "left": 0, "width": 100, "height": 100}]) == {}
