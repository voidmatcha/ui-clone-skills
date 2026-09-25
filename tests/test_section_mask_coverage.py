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


def test_mask_coverage_does_not_mask_fixed_sibling_with_hero_canvas() -> None:
    matches = [
        {
            "name": "site-nav",
            "ref": {
                "tag": "header",
                "className": "site-nav fixed",
                "rect": {"top": 0, "left": 0, "width": 1440, "height": 84},
            },
            "impl": {},
        },
        {
            "name": "home",
            "ref": {
                "tag": "section",
                "id": "home",
                "className": "hero-sequence",
                "rect": {"top": 0, "left": 0, "width": 1440, "height": 3600},
            },
            "impl": {},
        },
        {
            "name": "hero-stage",
            "ref": {
                "tag": "div",
                "className": "hero-stage",
                "rect": {"top": 0, "left": 0, "width": 1440, "height": 900},
            },
            "impl": {},
        },
    ]
    canvas = {
        "tag": "canvas",
        "top": 0,
        "left": 0,
        "width": 1440,
        "height": 900,
        "ownerChain": [
            {"tag": "canvas", "id": None, "className": ""},
            {"tag": "div", "id": None, "className": "hero-stage"},
            {"tag": "section", "id": "home", "className": "hero-sequence"},
            {"tag": "body", "id": None, "className": ""},
        ],
    }

    assert calculate_mask_coverage(matches, [canvas]) == {
        "site-nav": 0.0,
        "home": 25.0,
        "hero-stage": 100.0,
    }


def test_mask_coverage_keeps_media_owned_by_footer() -> None:
    matches = [{
        "name": "site-footer",
        "ref": {
            "tag": "footer",
            "className": "site-footer relative",
            "rect": {"top": 900, "left": 0, "width": 100, "height": 100},
        },
        "impl": {},
    }]
    canvas = {
        "tag": "canvas",
        "top": 900,
        "left": 0,
        "width": 100,
        "height": 100,
        "ownerChain": [
            {"tag": "canvas", "id": None, "className": ""},
            {"tag": "footer", "id": None, "className": "site-footer relative"},
        ],
    }

    assert calculate_mask_coverage(matches, [canvas]) == {"site-footer": 100.0}


def test_mask_coverage_follows_class_alias_section_id() -> None:
    # synthesize_ref_sections_from_section_map() stores the section-map
    # ``name`` (first captured class) in ``id`` when the node has no DOM id.
    # That alias never equals an owner's DOM id, so coverage used to be 0.0.
    matches = [{
        "name": "hero-stage",
        "ref": {
            "tag": "div",
            "id": "hero-stage",
            "className": "hero-stage relative",
            "rect": {"top": 0, "left": 0, "width": 100, "height": 100},
        },
        "impl": {},
    }]
    canvas = {
        "tag": "canvas",
        "top": 0,
        "left": 0,
        "width": 100,
        "height": 50,
        "ownerChain": [
            {"tag": "canvas", "id": None, "className": ""},
            {"tag": "div", "id": None, "className": "hero-stage relative"},
            {"tag": "body", "id": None, "className": ""},
        ],
    }

    assert calculate_mask_coverage(matches, [canvas]) == {"hero-stage": 50.0}


def test_mask_coverage_keeps_strict_containment_for_real_dom_id() -> None:
    matches = [{
        "name": "home",
        "ref": {
            "tag": "section",
            "id": "home",
            "className": "hero-sequence",
            "rect": {"top": 0, "left": 0, "width": 100, "height": 100},
        },
        "impl": {},
    }]
    # Same tag and classes, but a different DOM id owns the canvas.
    canvas = {
        "tag": "canvas",
        "top": 0,
        "left": 0,
        "width": 100,
        "height": 100,
        "ownerChain": [
            {"tag": "canvas", "id": None, "className": ""},
            {"tag": "section", "id": "about", "className": "hero-sequence"},
        ],
    }

    assert calculate_mask_coverage(matches, [canvas]) == {"home": 0.0}
