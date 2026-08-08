from __future__ import annotations

import json
import re
from pathlib import Path

from ui_clone.section_compare_sections import (
    augment_impl_sections_from_section_map,
    build_crop_manifest,
    build_drift_diagnostic,
    main,
    merge_ref_runtime_sections,
    pair_sections,
    parse_agent_browser_json_list,
    promote_impl_path_reference,
    synthesize_ref_sections_from_section_map,
)


def test_section_compare_system_python_paths_avoid_runtime_pep604_unions() -> None:
    root = Path(__file__).resolve().parents[1]
    execution_surfaces = (
        root / "ui_clone" / "section_compare_sections.py",
        root / "ui_clone" / "section_capture.py",
        root / "ui_clone" / "section_dynamic.py",
        root / "skills" / "visual-debug" / "scripts" / "section-compare.sh",
    )
    runtime_union = re.compile(
        r"isinstance\([^\n]*\b(?:int|float)\s*\|\s*(?:int|float)"
    )

    offenders = [
        path.relative_to(root).as_posix()
        for path in execution_surfaces
        if runtime_union.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == [], (
        "section-compare uses macOS system Python 3.9; runtime PEP 604 unions "
        f"inside isinstance() crash there: {offenders}"
    )


def _pair(
    name: str,
    ref_top: float | None,
    ref_h: float | None,
    impl_top: float | None,
    impl_h: float | None,
    score: float = 1.0,
) -> dict[str, object]:
    ref: dict[str, object] | None = None
    impl: dict[str, object] | None = None
    if ref_top is not None and ref_h is not None:
        ref = {"rect": {"top": ref_top, "left": 0, "width": 1440, "height": ref_h}}
    if impl_top is not None and impl_h is not None:
        impl = {"rect": {"top": impl_top, "left": 0, "width": 1440, "height": impl_h}}
    return {"name": name, "score": score, "ref": ref, "impl": impl}


def test_parse_agent_browser_json_list_accepts_pretty_printed_arrays() -> None:
    raw = """[
  {
    "index": 0,
    "tag": "video",
    "top": 120.5
  }
]"""

    assert parse_agent_browser_json_list(raw) == [
        {"index": 0, "tag": "video", "top": 120.5}
    ]


def test_parse_agent_browser_json_list_unwraps_string_result() -> None:
    raw = '{"result": "[{\\"index\\": 1, \\"tag\\": \\"canvas\\"}]"}'

    assert parse_agent_browser_json_list(raw) == [
        {"index": 1, "tag": "canvas"}
    ]


def test_drift_table_reproduces_collapse_then_baked_height_cascade() -> None:
    # Synthetic cascade mirroring the verified two-stage drift:
    #   drift 0 holds across the top sections, then a section that COLLAPSES
    #   (hDelta=-675) injects +225 of drift onto everything below it, and a
    #   later section with BAKED extra height (hDelta=+150) injects a further
    #   jump. Each jump is attributed to the PREVIOUS in-order section.
    matches = [
        # name,              refTop  refH  implTop implH
        _pair("intro", 0, 600, 0, 600),
        _pair("hero", 600, 600, 600, 600),
        # collapse section: shorter impl (hDelta = -675). Its own top still
        # matches (nothing above it drifted yet), but it pushes the NEXT
        # section down by 225.
        _pair("collapse", 1200, 700, 1200, 25),  # hDelta = -675
        # first victim: drift jumps 0 -> 225, attributed to "collapse".
        _pair("victim-a", 1900, 600, 2125, 600),  # drift = 225
        # baked-height section: extra impl height (hDelta = +150). Top carries
        # the standing 225 drift; pushes the NEXT section by a further 150.
        _pair("baked", 2500, 600, 2725, 750),  # drift = 225, hDelta = +150
        # second victim: drift jumps 225 -> 375, attributed to "baked".
        _pair("victim-b", 3100, 600, 3475, 600),  # drift = 375
    ]

    diag = build_drift_diagnostic(matches)
    table = diag["table"]
    jumps = diag["jumps"]

    # Table is sorted by ref top and carries the expected per-pair math.
    names = [row["name"] for row in table]
    assert names == ["intro", "hero", "collapse", "victim-a", "baked", "victim-b"]
    by_name = {row["name"]: row for row in table}
    assert by_name["intro"]["drift"] == 0
    assert by_name["hero"]["drift"] == 0
    assert by_name["collapse"]["drift"] == 0
    assert by_name["collapse"]["hDelta"] == -675
    assert by_name["victim-a"]["drift"] == 225
    assert by_name["baked"]["drift"] == 225
    assert by_name["baked"]["hDelta"] == 150
    assert by_name["victim-b"]["drift"] == 375

    # Two jumps, each attributed to the PREVIOUS in-order section's hDelta with
    # the correct cause classification.
    assert len(jumps) == 2

    first, second = jumps
    assert first["at"] == "victim-a"
    assert first["cause"] == "collapse"
    assert first["causeHDelta"] == -675
    assert first["driftIncrease"] == 225
    assert first["fromDrift"] == 0
    assert first["toDrift"] == 225
    assert first["reason"] == "dropped negative-margin / collapsed-overlap"

    assert second["at"] == "victim-b"
    assert second["cause"] == "baked"
    assert second["causeHDelta"] == 150
    assert second["driftIncrease"] == 150
    assert second["fromDrift"] == 225
    assert second["toDrift"] == 375
    assert second["reason"] == "baked/extra height"

    assert diag["totalDriftRange"] == {"min": 0, "max": 375}


def test_drift_diagnostic_handles_missing_tops_without_crashing() -> None:
    matches = [
        _pair("a", 0, 600, 0, 600),
        # unmatched ref (no impl) -> null drift, skipped for attribution.
        {"name": "orphan-ref", "score": 0, "ref": {"rect": {"top": 600, "height": 600}}, "impl": None},
        # extra impl (no ref) -> null drift.
        {"name": "orphan-impl", "score": 0, "ref": None, "impl": {"rect": {"top": 700, "height": 600}}},
        _pair("b", 1200, 600, 1500, 600),
    ]

    diag = build_drift_diagnostic(matches)
    by_name = {row["name"]: row for row in diag["table"]}

    assert by_name["a"]["drift"] == 0
    assert by_name["orphan-ref"]["drift"] is None
    assert by_name["orphan-ref"]["implTop"] is None
    assert by_name["orphan-impl"]["drift"] is None
    assert by_name["orphan-impl"]["refTop"] is None
    assert by_name["b"]["drift"] == 300

    # Null-drift rows break the running chain, so no jump is fabricated across
    # the gap even though b's drift (300) exceeds a's (0).
    assert diag["jumps"] == []
    # Rows with no ref top sink to the end of the table.
    assert diag["table"][-1]["name"] == "orphan-impl"
    assert diag["totalDriftRange"] == {"min": 0, "max": 300}


def test_drift_diagnostic_empty_input() -> None:
    diag = build_drift_diagnostic([])
    assert diag == {
        "table": [],
        "jumps": [],
        "totalDriftRange": {"min": None, "max": None},
    }


def _section_map_row(
    index: int,
    *,
    tag: str = "section",
    section_id: str = "",
    cls: str = "",
    top: float,
    height: float,
    width: float = 1440,
) -> dict[str, object]:
    row: dict[str, object] = {
        "index": index,
        "tag": tag,
        "rect": {"top": top, "left": 0, "width": width, "height": height},
    }
    if section_id:
        row["id"] = section_id
    if cls:
        row["className"] = cls
    return row


def _impl_row(
    index: int,
    *,
    tag: str = "section",
    section_id: str = "",
    cls: str = "",
    top: float,
    height: float,
    width: float = 1440,
) -> dict[str, object]:
    return _section_map_row(
        index, tag=tag, section_id=section_id, cls=cls,
        top=top, height=height, width=width,
    )


def test_augment_rejects_positionally_overlapping_phantom() -> None:
    # A faithful Tailwind clone renders the "pyramid" section with NO id/class
    # the section-map row recognizes, so the real impl row (idx 0, no identity)
    # FAILS _identity_matches against the section-map row. The loop then thinks
    # the section is missing and would append the semantic candidate — but that
    # candidate is a POSITIONAL TWIN of the real row already present. The guard
    # must suppress it (no phantom appended).
    section_map = {
        "sections": [
            _section_map_row(0, section_id="pyramid", top=7372, height=900),
        ]
    }
    # Real impl row sits at the same position but carries no identity (Tailwind).
    impl_sections = [_impl_row(0, top=7370, height=905)]
    # The semantic candidate the matcher would restore: same identity as the
    # section-map row, near-identical position to the real impl row.
    candidates = [
        _impl_row(99, section_id="pyramid", top=7372, height=900),
    ]

    augmented = augment_impl_sections_from_section_map(
        section_map, impl_sections, candidates
    )

    # No phantom appended — the candidate twins the real row already present.
    assert len(augmented) == 1
    assert augmented[0]["index"] == 0


def test_augment_keeps_large_wrapper_that_contains_a_child_center() -> None:
    """A large semantic wrapper is not a duplicate of one nested child.

    The center-containment fallback exists for near-identical rows, but without
    a height-ratio guard it also discarded a page-sized ``main`` whenever its
    center happened to land inside an already-enumerated child section.
    """
    section_map = {
        "sections": [
            _section_map_row(
                0, tag="main", section_id="home", top=0, height=8000
            ),
        ]
    }
    impl_sections = [
        _impl_row(0, top=3500, height=1000),
    ]
    candidates = [
        _impl_row(
            99, tag="main", section_id="home", top=0, height=8000
        ),
    ]

    augmented = augment_impl_sections_from_section_map(
        section_map, impl_sections, candidates
    )

    assert len(augmented) == 2
    assert augmented[-1]["tag"] == "main"
    assert augmented[-1]["id"] == "home"


def test_augment_suppresses_class_wrapper_twin_of_existing_landmark() -> None:
    """A restored div wrapper cannot duplicate the live section it encloses."""
    section_map = {
        "sections": [
            _section_map_row(
                0,
                tag="div",
                cls="evo-grid",
                top=6290,
                height=812,
                width=375,
            ),
        ]
    }
    impl_sections = [
        _impl_row(
            0,
            tag="section",
            cls="style_playground__oXvoz",
            top=6292,
            height=812,
            width=343,
        ),
    ]
    candidates = [
        _impl_row(
            9,
            tag="div",
            cls="evo-grid",
            top=6292,
            height=812,
            width=375,
        ),
    ]

    augmented = augment_impl_sections_from_section_map(
        section_map,
        impl_sections,
        candidates,
    )

    assert len(augmented) == 1
    assert augmented[0]["className"] == "style_playground__oXvoz"


def test_augment_keeps_coarse_main_over_large_differently_tagged_child() -> None:
    """A nested section cannot suppress its enclosing semantic landmark."""
    section_map = {
        "sections": [
            _section_map_row(
                0, tag="main", section_id="home", top=0, height=8000
            ),
        ]
    }
    impl_sections = [
        _impl_row(
            0, tag="section", section_id="hero", top=1000, height=6000
        ),
    ]
    candidates = [
        _impl_row(
            99, tag="main", section_id="home", top=0, height=8000
        ),
    ]

    augmented = augment_impl_sections_from_section_map(
        section_map, impl_sections, candidates
    )

    assert [row["tag"] for row in augmented] == ["section", "main"]


def test_augment_dedups_exact_runtime_twins_with_different_indices() -> None:
    duplicate = _impl_row(
        3,
        tag="div",
        cls="evo-grid home-section-title",
        top=797,
        height=172,
        width=375,
    )

    augmented = augment_impl_sections_from_section_map(
        {"sections": []},
        [duplicate, {**duplicate, "index": 18}],
        [],
    )

    assert len(augmented) == 1
    assert augmented[0]["index"] == 0


def test_augment_restores_missing_repeated_class_instance_across_viewports() -> None:
    """A mobile tagline is not hidden by a different desktop evo-grid row."""
    map_rows = [
        {
            "index": 1,
            "tag": "div",
            "className": "evo-grid",
            "top": 136,
            "height": 438,
            "textPreview": "One system for everyone to love.",
        },
        {
            "index": 2,
            "tag": "div",
            "className": "evo-grid",
            "top": 574,
            "height": 627,
            "textPreview": "eBay Evo is our brand and design system.",
        },
        {
            "index": 3,
            "tag": "div",
            "className": "evo-grid home-section-title",
            "top": 1201,
            "height": 348,
            "textPreview": "Inspired by how people discover.",
        },
    ]
    live_candidates = [
        {
            **_impl_row(
                1, tag="div", cls="evo-grid", top=112, height=150, width=375
            ),
            "fingerprint": "one system for everyone to love",
            "textWords": "one system for everyone to love",
        },
        {
            **_impl_row(
                2, tag="div", cls="evo-grid", top=262, height=535, width=375
            ),
            "fingerprint": "ebay evo is our brand and design system",
            "textWords": "ebay evo is our brand and design system",
        },
        {
            **_impl_row(
                3,
                tag="div",
                cls="evo-grid home-section-title",
                top=797,
                height=172,
                width=375,
            ),
            "fingerprint": "inspired by how people discover",
            "textWords": "inspired by how people discover",
        },
    ]
    runtime_impl = [live_candidates[1], live_candidates[2]]
    section_map = {"sections": map_rows}

    augmented = augment_impl_sections_from_section_map(
        section_map,
        runtime_impl,
        live_candidates,
    )
    synthesized = synthesize_ref_sections_from_section_map(
        section_map,
        live_candidates,
        active_view_width=375,
    )
    matches = pair_sections(synthesized, augmented)

    assert sorted(row["rect"]["top"] for row in augmented) == [112, 262, 797]
    paired = [
        (row["ref"]["textWords"], row["ref"]["rect"]["top"], row["impl"]["rect"]["top"])
        for row in matches
        if row.get("ref") and row.get("impl")
    ]
    assert paired == [
        ("one system for everyone to love", 112, 112),
        ("ebay evo is our brand and design system", 262, 262),
        ("inspired by how people discover", 797, 797),
    ]


def test_synthesize_ref_sections_uses_live_mobile_landmark_geometry() -> None:
    """Section-map identity/order survives while live viewport geometry wins."""
    section_map = {
        "sections": [
            {
                "index": 4,
                "tag": "main",
                "id": "content",
                "cls": "desktop-main",
                "top": 120,
                "left": 0,
                "width": 1440,
                "height": 7200,
                "display": "grid",
                "childCount": 8,
            },
            {
                "index": 9,
                "tag": "footer",
                "top": 7320,
                "left": 0,
                "width": 1440,
                "height": 900,
                "display": "grid",
                "childCount": 6,
            },
        ]
    }
    live_candidates = [
        {
            "index": 7,
            "tag": "main",
            "id": "content",
            "className": "mobile-main",
            "fingerprint": "mobile content",
            "textWords": "mobile content",
            "rect": {"top": 88, "left": 0, "width": 375, "height": 9100},
            "display": "block",
            "childCount": 10,
            "clientWidth": 375,
            "contentBox": {"left": 16, "width": 343, "boxCount": 5},
            "contentGroups": [{"name": "cards", "childCount": 2}],
            "leftGap": 16,
            "rightGap": 16,
        },
        {
            "index": 12,
            "tag": "footer",
            "id": None,
            "className": "",
            "fingerprint": "footer links",
            "textWords": "footer links",
            "rect": {"top": 9188, "left": 0, "width": 375, "height": 1280},
            "display": "block",
            "childCount": 4,
            "clientWidth": 375,
            "contentBox": {"left": 24, "width": 327, "boxCount": 4},
            "contentGroups": [{"name": "links", "childCount": 4}],
            "leftGap": 24,
            "rightGap": 24,
        },
    ]

    synthesized = synthesize_ref_sections_from_section_map(
        section_map,
        live_candidates,
        active_view_width=375,
    )

    assert [(row["index"], row["tag"]) for row in synthesized] == [
        (0, "main"),
        (1, "footer"),
    ]
    assert synthesized[0]["id"] == "content"
    assert synthesized[0]["className"] == "desktop-main"
    assert synthesized[0]["rect"] == live_candidates[0]["rect"]
    assert synthesized[0]["display"] == "block"
    assert synthesized[0]["childCount"] == 10
    assert synthesized[0]["contentGroups"] == live_candidates[0]["contentGroups"]
    assert synthesized[1]["className"] == ""
    assert synthesized[1]["rect"] == live_candidates[1]["rect"]
    assert synthesized[1]["leftGap"] == 24


def test_synthesize_ref_sections_preserves_visible_media_evidence() -> None:
    section_map = {
        "sections": [
            {
                "index": 4,
                "tag": "section",
                "className": "media-shell",
                "top": 120,
                "left": 0,
                "width": 1440,
                "height": 720,
                "childCount": 1,
                "hasVisibleMedia": True,
                "visibleMediaCount": 1,
            }
        ]
    }

    synthesized = synthesize_ref_sections_from_section_map(
        section_map,
        [],
        active_view_width=1440,
    )

    assert synthesized[0]["childCount"] == 1
    assert synthesized[0]["hasVisibleMedia"] is True
    assert synthesized[0]["visibleMediaCount"] == 1


def test_synthesize_ref_sections_excludes_closed_offscreen_drawer() -> None:
    section_map = {
        "sections": [
            {
                "index": 0,
                "tag": "div",
                "className": "mo-nav",
                "top": 0,
                "height": 900,
            },
            {
                "index": 1,
                "tag": "main",
                "id": "content",
                "top": 0,
                "height": 7200,
            },
            {
                "index": 2,
                "tag": "footer",
                "top": 7200,
                "height": 500,
            },
        ]
    }
    drawer = {
        "index": 1,
        "tag": "div",
        "className": "mo-nav",
        "rect": {"top": 0, "left": 2418, "width": 782, "height": 900},
        "display": "flex",
        "childCount": 1,
    }

    synthesized = synthesize_ref_sections_from_section_map(
        section_map,
        [drawer],
        active_view_width=1600,
        runtime_sections=[drawer],
    )

    assert "mo-nav" not in [row.get("className") for row in synthesized]


def test_synthesize_ref_sections_prefers_visible_duplicate_after_offscreen_match() -> None:
    section_map = {
        "sections": [
            {
                "index": 0,
                "tag": "main",
                "id": "content",
                "className": "page-shell",
                "top": 0,
                "height": 7200,
            },
        ]
    }
    offscreen = {
        "index": 1,
        "tag": "main",
        "id": "content",
        "className": "page-shell frozen-copy",
        "rect": {"top": 0, "left": 1934, "width": 626, "height": 900},
        "display": "block",
        "childCount": 1,
    }
    visible = {
        "index": 2,
        "tag": "main",
        "id": "content",
        "className": "page-shell",
        "rect": {"top": 88, "left": 0, "width": 1280, "height": 7200},
        "display": "grid",
        "childCount": 8,
        "clientWidth": 1280,
    }

    synthesized = synthesize_ref_sections_from_section_map(
        section_map,
        [offscreen, visible],
        active_view_width=1280,
    )

    assert [(row["id"], row["rect"]) for row in synthesized] == [
        ("content", visible["rect"])
    ]
    assert synthesized[0]["display"] == "grid"


def test_pair_command_excludes_frozen_horizontal_offscreen_rows(
    tmp_path: Path,
) -> None:
    ref = [
        {
            "index": 0,
            "tag": "main",
            "className": "content",
            "textWords": "visible page content",
            "rect": {"top": 0, "left": 0, "width": 1280, "height": 800},
            "clientWidth": 1280,
        },
        {
            "index": 1,
            "tag": "div",
            "className": "mo-nav",
            "textWords": "stale closed mobile navigation",
            "rect": {"top": 0, "left": 1934, "width": 626, "height": 800},
            "clientWidth": 1280,
        },
    ]
    impl = [
        {
            "index": 0,
            "tag": "main",
            "className": "content",
            "textWords": "visible page content",
            "rect": {"top": 0, "left": 0, "width": 1280, "height": 800},
            "clientWidth": 1280,
        },
        {
            "index": 1,
            "tag": "div",
            "className": "mo-nav",
            "textWords": "stale closed mobile navigation",
            "rect": {"top": 0, "left": 1934, "width": 626, "height": 800},
            "clientWidth": 1280,
        },
        {
            "index": 2,
            "tag": "aside",
            "className": "mobile-drawer-edge",
            "textWords": "closed mobile edge drawer",
            "rect": {"top": 0, "left": 375, "width": 280, "height": 700},
            "clientWidth": 375,
        },
    ]
    ref_path = tmp_path / "ref-sections.json"
    impl_path = tmp_path / "impl-sections.json"
    out_path = tmp_path / "matches.json"
    ref_path.write_text(json.dumps(ref), encoding="utf-8")
    impl_path.write_text(json.dumps(impl), encoding="utf-8")

    assert main(["pair", str(ref_path), str(impl_path), str(out_path)]) == 0

    matches = json.loads(out_path.read_text(encoding="utf-8"))
    paired_classes = [
        (row.get("ref") or {}).get("className")
        for row in matches
        if row.get("ref") and row.get("impl")
    ]
    extra_classes = [
        (row.get("impl") or {}).get("className")
        for row in matches
        if row.get("status") == "EXTRA_IN_IMPL"
    ]

    assert paired_classes == ["content"]
    assert "mo-nav" not in paired_classes
    assert "mo-nav" not in extra_classes
    assert "mobile-drawer-edge" not in extra_classes


def test_synthesize_ref_sections_preserves_repeated_class_order_across_viewports() -> None:
    """Responsive top compression cannot shift repeated evo-grid identities."""
    section_map = {
        "sections": [
            {
                "index": 1,
                "tag": "div",
                "className": "evo-grid",
                "top": 136,
                "height": 438,
                "textPreview": "One system for everyone to love.",
            },
            {
                "index": 2,
                "tag": "div",
                "className": "evo-grid",
                "top": 574,
                "height": 627,
                "textPreview": "eBay Evo is our brand and design system.",
            },
            {
                "index": 3,
                "tag": "div",
                "className": "evo-grid home-section-title",
                "top": 1201,
                "height": 348,
                "textPreview": "Inspired by how people discover.",
            },
        ]
    }
    live_candidates = [
        {
            **_impl_row(
                1, tag="div", cls="evo-grid", top=112, height=150, width=375
            ),
            "fingerprint": "one system for everyone to love",
            "textWords": "one system for everyone to love",
        },
        {
            **_impl_row(
                2, tag="div", cls="evo-grid", top=262, height=535, width=375
            ),
            "fingerprint": "ebay evo is our brand and design system",
            "textWords": "ebay evo is our brand and design system",
        },
        {
            **_impl_row(
                3,
                tag="div",
                cls="evo-grid home-section-title",
                top=797,
                height=172,
                width=375,
            ),
            "fingerprint": "inspired by how people discover",
            "textWords": "inspired by how people discover",
        },
    ]

    synthesized = synthesize_ref_sections_from_section_map(
        section_map,
        live_candidates,
        active_view_width=375,
    )

    assert [row["rect"] for row in synthesized] == [
        live_candidates[0]["rect"],
        live_candidates[1]["rect"],
        live_candidates[2]["rect"],
    ]
    assert [row["textWords"] for row in synthesized] == [
        "one system for everyone to love",
        "ebay evo is our brand and design system",
        "inspired by how people discover",
    ]
    assert synthesized[1]["className"] == "evo-grid"
    assert synthesized[2]["className"] == "evo-grid home-section-title"


def test_synthesize_ref_sections_falls_back_when_live_candidate_is_missing() -> None:
    section_map = {
        "sections": [
            {
                "index": 0,
                "tag": "main",
                "id": "content",
                "top": 120,
                "left": 8,
                "width": 1440,
                "height": 7200,
                "display": "grid",
                "childCount": 8,
            },
        ]
    }

    synthesized = synthesize_ref_sections_from_section_map(
        section_map,
        [],
        active_view_width=375,
    )

    assert synthesized[0]["rect"] == {
        "top": 120,
        "left": 8,
        "width": 1440,
        "height": 7200,
    }
    assert synthesized[0]["display"] == "grid"
    assert synthesized[0]["childCount"] == 8
    assert "_sectionMapFallback" not in synthesized[0]


def test_synthesize_ref_replaces_wide_wrapper_with_live_landmark_geometry() -> None:
    section_map = {
        "sections": [
            {
                "index": 0,
                "tag": "div",
                "className": "navercorp main",
                "top": 100,
                "left": 0,
                "width": 1600,
                "height": 5364,
                "childCount": 1,
            },
        ],
    }
    live_main = {
        **_impl_row(
            4,
            tag="main",
            section_id="content",
            top=100,
            height=6078,
            width=1600,
        ),
        "childCount": 5,
    }

    synthesized = synthesize_ref_sections_from_section_map(
        section_map,
        [live_main],
        active_view_width=1600,
        runtime_sections=[live_main],
    )

    assert len(synthesized) == 1
    assert synthesized[0]["tag"] == "main"
    assert synthesized[0]["id"] == "content"
    assert synthesized[0]["rect"] == live_main["rect"]
    assert "_sectionMapFallback" not in synthesized[0]


def test_synthesize_ref_replaces_stale_offcanvas_fallback_by_live_identity() -> None:
    section_map = {
        "sections": [
            {
                "index": 0,
                "tag": "div",
                "className": "mo-nav",
                "top": 0,
                "left": 0,
                "width": 1600,
                "height": 900,
                "childCount": 4,
            },
        ],
    }
    live_offcanvas = _impl_row(
        8,
        tag="div",
        cls="mo-nav is-ready",
        top=100,
        height=800,
        width=420,
    )

    synthesized = synthesize_ref_sections_from_section_map(
        section_map,
        [],
        active_view_width=1600,
        runtime_sections=[live_offcanvas],
    )

    assert len(synthesized) == 1
    assert synthesized[0]["className"] == "mo-nav is-ready"
    assert synthesized[0]["rect"] == live_offcanvas["rect"]
    assert "_sectionMapFallback" not in synthesized[0]


def test_fallback_identity_rejects_conflicting_ids_and_generic_classes() -> None:
    synthesized = [
        {
            **_section_map_row(
                0,
                tag="div",
                section_id="hero",
                cls="section active",
                top=0,
                height=900,
            ),
            "_sectionMapFallback": True,
        },
    ]
    runtime = [
        _impl_row(
            8,
            tag="div",
            section_id="footer",
            cls="section active",
            top=1200,
            height=900,
        ),
    ]

    merged = merge_ref_runtime_sections(synthesized, runtime)

    assert [row.get("id") for row in merged] == ["hero", "footer"]
    assert all("_sectionMapFallback" not in row for row in merged)


def test_fallback_identity_keeps_ambiguous_class_candidates() -> None:
    synthesized = [
        {
            **_section_map_row(
                0, tag="div", cls="mo-nav primary", top=0, height=900
            ),
            "_sectionMapFallback": True,
        },
        {
            **_section_map_row(
                1, tag="div", cls="mo-nav secondary", top=1000, height=900
            ),
            "_sectionMapFallback": True,
        },
    ]
    runtime = [
        _impl_row(
            8, tag="div", cls="mo-nav is-ready", top=2200, height=800, width=420
        ),
    ]

    merged = merge_ref_runtime_sections(synthesized, runtime)

    assert len(merged) == 3
    assert [row["className"] for row in merged] == [
        "mo-nav primary",
        "mo-nav secondary",
        "mo-nav is-ready",
    ]


def test_fallback_identity_requires_equal_stable_class_tokens() -> None:
    synthesized = [
        {
            **_section_map_row(
                0,
                tag="div",
                cls="page-shell hero",
                top=0,
                height=900,
            ),
            "_sectionMapFallback": True,
        },
    ]
    runtime = [
        _impl_row(
            8,
            tag="div",
            cls="page-shell footer is-ready",
            top=1200,
            height=900,
        ),
    ]

    merged = merge_ref_runtime_sections(synthesized, runtime)

    assert [row["className"] for row in merged] == [
        "page-shell hero",
        "page-shell footer is-ready",
    ]


def test_wrapper_dedupe_requires_a_semantic_landmark_tag() -> None:
    synthesized = [
        {
            **_section_map_row(
                0,
                tag="div",
                cls="component-shell",
                top=100,
                height=870,
                width=800,
            ),
            "childCount": 1,
        },
    ]
    runtime = [
        _impl_row(
            7,
            tag="section",
            section_id="promo",
            top=100,
            height=900,
            width=800,
        ),
    ]

    assert len(merge_ref_runtime_sections(synthesized, runtime)) == 2


def test_wrapper_dedupe_requires_exactly_one_child_and_one_semantic_match() -> None:
    for child_count in (0, -1, 2):
        synthesized = [
            {
                **_section_map_row(
                    0,
                    tag="div",
                    cls="page-shell",
                    top=100,
                    height=5364,
                    width=1600,
                ),
                "childCount": child_count,
            },
        ]
        runtime = [
            _impl_row(
                7,
                tag="main",
                section_id="content",
                top=100,
                height=6078,
                width=1600,
            ),
        ]

        assert len(merge_ref_runtime_sections(synthesized, runtime)) == 2

    synthesized = [
        {
            **_section_map_row(
                0,
                tag="div",
                cls="page-shell",
                top=100,
                height=5364,
                width=1600,
            ),
            "childCount": 1,
        },
    ]
    ambiguous_runtime = [
        _impl_row(
            7,
            tag="main",
            section_id="content-a",
            top=100,
            height=6078,
            width=1600,
        ),
        _impl_row(
            8,
            tag="main",
            section_id="content-b",
            top=100,
            height=6078,
            width=1600,
        ),
    ]

    assert len(
        merge_ref_runtime_sections(synthesized, ambiguous_runtime)
    ) == 3


def test_synthesize_ref_keeps_materially_inset_wrapper_and_semantic_region() -> None:
    section_map = {
        "sections": [
            {
                "index": 0,
                "tag": "div",
                "className": "product-region",
                "top": 250,
                "left": 0,
                "width": 1600,
                "height": 5150,
                "childCount": 1,
            },
        ],
    }
    live_main = {
        **_impl_row(
            4,
            tag="main",
            section_id="content",
            top=100,
            height=6078,
            width=1600,
        ),
        "childCount": 5,
    }

    synthesized = synthesize_ref_sections_from_section_map(
        section_map,
        [live_main],
        active_view_width=1600,
        runtime_sections=[live_main],
    )

    assert [(row["tag"], row.get("id")) for row in synthesized] == [
        ("main", "content"),
        ("div", None),
    ]
    assert synthesized[1]["rect"]["top"] == 250


def test_augment_restores_visible_identityless_landmark() -> None:
    """Landmark tags remain useful even when they have no id or class."""
    for tag in ("header", "main", "footer", "nav", "article"):
        section_map = {
            "sections": [
                {
                    "index": 0,
                    "tag": tag,
                    "top": 0,
                    "height": 800,
                },
            ]
        }
        impl_sections = [
            _impl_row(0, top=1000, height=600),
        ]
        candidates = [
            _impl_row(99, tag=tag, top=0, height=800),
        ]

        augmented = augment_impl_sections_from_section_map(
            section_map, impl_sections, candidates
        )

        assert len(augmented) == 2
        assert augmented[-1]["tag"] == tag
        assert augmented[-1].get("id") in ("", None)
        assert augmented[-1].get("className") in ("", None)


def test_augment_still_restores_genuinely_missing_section() -> None:
    # A section-map row whose semantic candidate does NOT overlap any existing
    # impl row is a genuinely-missing section (the impl runtime enumerator
    # descended through a jumbo wrapper and dropped it). The legitimate restore
    # behavior must still fire.
    section_map = {
        "sections": [
            _section_map_row(0, section_id="hero", top=0, height=900),
            _section_map_row(1, section_id="faqs", top=16259, height=1200),
        ]
    }
    # Impl only has the hero (identity matches) — the faqs row is absent.
    impl_sections = [_impl_row(0, section_id="hero", top=0, height=900)]
    candidates = [
        _impl_row(50, section_id="faqs", cls="faq-block", top=16259, height=1200),
    ]

    augmented = augment_impl_sections_from_section_map(
        section_map, impl_sections, candidates
    )

    # The non-overlapping missing faqs candidate IS appended (restore works).
    assert len(augmented) == 2
    restored = augmented[-1]
    assert restored["id"] == "faqs"
    assert restored["index"] == 1  # max existing index (0) + 1


def test_augment_self_pass_noop_when_identity_matches() -> None:
    # Self-pass impl==ref: every section carries identity, so _identity_matches
    # succeeds and the loop `continue`s BEFORE reaching the overlap guard. The
    # append count is therefore unchanged (guard is a strict no-op here).
    section_map = {
        "sections": [
            _section_map_row(0, section_id="hero", top=0, height=900),
            _section_map_row(1, section_id="pyramid", top=900, height=900),
        ]
    }
    # Impl carries the SAME identity for both rows (self-pass).
    impl_sections = [
        _impl_row(0, section_id="hero", top=0, height=900),
        _impl_row(1, section_id="pyramid", top=900, height=900),
    ]
    # Candidates exist but should never be consulted (identity already matched).
    candidates = [
        _impl_row(0, section_id="hero", top=0, height=900),
        _impl_row(1, section_id="pyramid", top=900, height=900),
    ]

    augmented = augment_impl_sections_from_section_map(
        section_map, impl_sections, candidates
    )

    # Nothing appended — same count as the input impl sections.
    assert len(augmented) == len(impl_sections)


def test_pair_sections_reports_unproven_descendant_of_matched_landmark() -> None:
    """Containment alone is not evidence that an impl-only section exists in ref."""
    ref = [
        _section_map_row(
            0, tag="main", section_id="home", top=0, height=8000
        ),
    ]
    impl = [
        _impl_row(
            0, tag="main", section_id="home", top=0, height=8000
        ),
        _impl_row(
            1, tag="section", section_id="hero", top=500, height=900
        ),
    ]

    matches = pair_sections(ref, impl)

    assert matches[0]["impl"]["id"] == "home"
    extras = [
        match
        for match in matches
        if match.get("status") == "EXTRA_IN_IMPL"
    ]
    assert len(extras) == 1
    assert extras[0]["impl"]["id"] == "hero"


def test_live_runtime_descendant_evidence_pairs_classed_div_but_not_promo() -> None:
    """A captured nested ref row is affirmative evidence; containment is not."""
    synthesized = [
        _section_map_row(
            0, tag="main", section_id="home", top=0, height=3934
        ),
    ]
    runtime = [
        _impl_row(
            10, tag="main", section_id="home", top=0, height=3934
        ),
        _impl_row(
            11, tag="div", cls="mt-6", top=900, height=2450
        ),
    ]

    ref = merge_ref_runtime_sections(synthesized, runtime)

    assert [(row["tag"], row.get("className")) for row in ref] == [
        ("main", None),
        ("div", "mt-6"),
    ]

    impl = [
        _impl_row(
            0, tag="main", section_id="home", top=0, height=3934
        ),
        _impl_row(
            1, tag="div", cls="mt-6", top=900, height=2450
        ),
        _impl_row(
            2, tag="section", section_id="promo", top=3500, height=300
        ),
    ]

    matches = pair_sections(ref, impl)
    extras = [
        match
        for match in matches
        if match.get("status") == "EXTRA_IN_IMPL"
    ]

    assert any(
        match.get("ref", {}).get("className") == "mt-6"
        and match.get("impl", {}).get("className") == "mt-6"
        for match in matches
    )
    assert len(extras) == 1
    assert extras[0]["impl"]["id"] == "promo"


def test_merge_ref_runtime_dedups_near_exact_same_tag_region() -> None:
    synthesized = [
        _section_map_row(
            0, tag="header", section_id="site-header", top=0, height=88
        ),
    ]
    runtime = [
        _impl_row(
            7, tag="header", section_id="site-header", top=1, height=87
        ),
    ]

    merged = merge_ref_runtime_sections(synthesized, runtime)

    assert len(merged) == 1
    assert merged[0]["index"] == 0
    assert merged[0]["id"] == "site-header"


def test_merge_ref_runtime_prefers_semantic_row_over_empty_wrapper() -> None:
    synthesized = [
        {
            **_section_map_row(
                0, tag="div", cls="navercorp main", top=100, height=5364,
                width=375,
            ),
            "childCount": 1,
        },
    ]
    runtime = [
        {
            **_impl_row(
                7,
                tag="main",
                section_id="content",
                top=48,
                height=5540,
                width=375,
            ),
            "childCount": 8,
        },
    ]

    merged = merge_ref_runtime_sections(synthesized, runtime)

    assert len(merged) == 1
    assert merged[0]["tag"] == "main"
    assert merged[0]["id"] == "content"
    assert merged[0]["index"] == 0

    matches = pair_sections(
        merged,
        [
            _impl_row(
                0,
                tag="div",
                cls="navercorp main",
                top=100,
                height=5364,
                width=375,
            ),
            _impl_row(
                1,
                tag="main",
                section_id="content",
                top=48,
                height=5538,
                width=375,
            ),
        ],
    )
    assert matches[0]["ref"]["id"] == "content"
    assert matches[0]["impl"]["id"] == "content"


def test_merge_ref_runtime_keeps_materially_inset_single_child_region() -> None:
    synthesized = [
        {
            **_section_map_row(
                0, tag="div", cls="product-region", top=250, height=5150,
                width=375,
            ),
            "childCount": 1,
        },
    ]
    runtime = [
        _impl_row(
            7,
            tag="main",
            section_id="content",
            top=48,
            height=5540,
            width=375,
        ),
    ]

    merged = merge_ref_runtime_sections(synthesized, runtime)

    assert [(row["tag"], row.get("id")) for row in merged] == [
        ("main", "content"),
        ("div", None),
    ]


def test_synthesize_ref_uses_short_live_landmark_geometry() -> None:
    section_map = {
        "sections": [
            {
                "index": 0,
                "tag": "header",
                "id": "header",
                "top": 0,
                "left": 0,
                "width": 375,
                "height": 64,
            },
        ],
    }
    live_candidates = [
        {
            "index": 2,
            "tag": "header",
            "id": "header",
            "rect": {"top": 0, "left": 0, "width": 375, "height": 48},
            "childCount": 2,
        },
    ]

    synthesized = synthesize_ref_sections_from_section_map(
        section_map,
        live_candidates,
        active_view_width=375,
    )

    assert synthesized[0]["rect"]["height"] == 48


def test_augment_restores_short_live_landmark() -> None:
    section_map = {
        "sections": [
            {
                "index": 0,
                "tag": "header",
                "id": "header",
                "top": 0,
                "left": 0,
                "width": 375,
                "height": 64,
            },
        ],
    }
    candidate = {
        "index": 2,
        "tag": "header",
        "id": "header",
        "rect": {"top": 0, "left": 0, "width": 375, "height": 48},
        "childCount": 2,
    }

    augmented = augment_impl_sections_from_section_map(
        section_map,
        [],
        [candidate],
    )

    assert augmented == [{**candidate, "index": 0}]


def test_augment_restores_class_locked_nested_div() -> None:
    """Section-map divs remain comparable when runtime enumeration keeps only
    their larger parent wrapper.
    """
    section_map = {
        "sections": [
            {
                "index": 12,
                "tag": "div",
                "className": "style_blurb__EpnZa",
                "top": 7722,
                "left": 32,
                "width": 455,
                "height": 773,
            },
        ],
    }
    parent_runtime_row = {
        "index": 4,
        "tag": "div",
        "className": "style_video_blurb__ldcJv evo-grid",
        "rect": {"top": 7690, "left": 0, "width": 1440, "height": 805},
        "childCount": 2,
    }
    nested_candidate = {
        "index": 18,
        "tag": "div",
        "className": "style_blurb__EpnZa",
        "rect": {"top": 7722, "left": 32, "width": 455, "height": 773},
        "childCount": 1,
    }

    augmented = augment_impl_sections_from_section_map(
        section_map,
        [parent_runtime_row],
        [nested_candidate],
    )

    assert len(augmented) == 2
    assert augmented[1]["className"] == "style_blurb__EpnZa"
    assert augmented[1]["rect"]["top"] == 7722


def test_augment_restores_repeated_class_instance_by_position() -> None:
    section_map = {
        "sections": [
            {
                "index": 12,
                "tag": "div",
                "className": "style_blurb__EpnZa",
                "top": 7722,
                "height": 773,
            },
            {
                "index": 14,
                "tag": "div",
                "className": "style_blurb__EpnZa",
                "top": 8590,
                "height": 772,
            },
        ],
    }
    first = {
        "index": 3,
        "tag": "div",
        "className": "style_blurb__EpnZa",
        "rect": {"top": 7722, "left": 32, "width": 455, "height": 773},
        "childCount": 1,
    }
    second = {
        "index": 9,
        "tag": "div",
        "className": "style_blurb__EpnZa",
        "rect": {"top": 8590, "left": 728, "width": 680, "height": 772},
        "childCount": 1,
    }

    augmented = augment_impl_sections_from_section_map(
        section_map,
        [first],
        [first, second],
    )

    assert [row["rect"]["top"] for row in augmented] == [7722, 8590]


def test_synthesize_repeated_class_uses_nearest_live_instance() -> None:
    section_map = {
        "sections": [
            {
                "index": 15,
                "tag": "div",
                "className": "style_container__gnBIP",
                "top": 8590,
                "height": 772,
            },
        ],
    }
    early = {
        "index": 1,
        "tag": "div",
        "className": "style_container__gnBIP",
        "rect": {"top": 694, "left": 507, "width": 901, "height": 507},
    }
    near = {
        "index": 8,
        "tag": "div",
        "className": "style_container__gnBIP",
        "rect": {"top": 8590, "left": 32, "width": 680, "height": 772},
    }

    synthesized = synthesize_ref_sections_from_section_map(
        section_map,
        [early, near],
        active_view_width=1440,
    )

    assert synthesized[0]["rect"] == near["rect"]


def test_merge_ref_runtime_keeps_equal_size_side_by_side_siblings() -> None:
    synthesized = [
        {
            "index": 0,
            "tag": "div",
            "className": "style_container__gnBIP",
            "rect": {"top": 8590, "left": 32, "width": 680, "height": 772},
        }
    ]
    runtime = [
        {
            "index": 15,
            "tag": "div",
            "className": "style_blurb__EpnZa",
            "rect": {"top": 8590, "left": 728, "width": 680, "height": 772},
        }
    ]

    merged = merge_ref_runtime_sections(synthesized, runtime)

    assert [row["className"] for row in merged] == [
        "style_container__gnBIP",
        "style_blurb__EpnZa",
    ]


def test_merge_ref_runtime_dedups_exact_synthesized_duplicate() -> None:
    duplicate = {
        "index": 0,
        "tag": "div",
        "className": "style_blurb__EpnZa",
        "fingerprint": "Same captured body copy",
        "rect": {"top": 7826, "left": 648, "width": 600, "height": 681},
    }

    merged = merge_ref_runtime_sections(
        [duplicate, {**duplicate, "index": 1}],
        [],
    )

    assert len(merged) == 1
    assert merged[0]["className"] == "style_blurb__EpnZa"


def test_merge_ref_runtime_dedups_duplicate_after_runtime_merge() -> None:
    synthesized = [{
        "index": 0,
        "tag": "div",
        "className": "evo-grid home-section-title",
        "rect": {"top": 797, "left": 0, "width": 375, "height": 172},
    }]
    runtime_row = {
        "index": 4,
        "tag": "div",
        "className": "evo-grid home-section-title",
        "fingerprint": "inspired by how people discover",
        "rect": {"top": 797, "left": 0, "width": 375, "height": 172},
    }

    merged = merge_ref_runtime_sections(
        synthesized,
        [runtime_row, {**runtime_row, "index": 5}],
    )

    assert len(merged) == 1
    assert merged[0]["rect"]["top"] == 797


def test_promote_impl_path_reference_preserves_capture_names() -> None:
    impl = {
        "index": 7,
        "tag": "section",
        "className": "tailwind-hero",
        "rect": {"top": 262, "left": 0, "width": 375, "height": 535},
    }
    promoted = promote_impl_path_reference([
        {"name": "evo-grid-2", "ref": {"index": 1}, "impl": impl},
        {
            "name": "duplicate",
            "ref": {"index": 2},
            "impl": {**impl, "index": 8},
        },
    ])

    assert len(promoted) == 1
    assert promoted[0]["captureName"] == "evo-grid-2"
    assert promoted[0]["index"] == 0


def test_crop_manifest_ignores_stale_frozen_ref_crop(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    impl_dir = tmp_path / "impl"
    ref_dir.mkdir()
    impl_dir.mkdir()
    for name in ("hero", "stale-blurb-3"):
        (ref_dir / f"{name}.png").touch()
    (impl_dir / "hero.png").touch()
    matches = [{
        "name": "hero",
        "ref": {"index": 1},
        "impl": {"index": 1},
    }]

    manifest = build_crop_manifest(matches, ref_dir, impl_dir)

    assert manifest["rows"] == [{
        "name": "hero",
        "refIndex": 1,
        "implIndex": 1,
        "refExists": True,
        "implExists": True,
    }]
    assert manifest["staleRefCrops"] == ["stale-blurb-3.png"]


def test_synthesize_drops_unidentified_nonsemantic_map_child() -> None:
    section_map = {
        "sections": [
            {
                "index": 0,
                "tag": "div",
                "className": "",
                "id": None,
                "top": 4578,
                "height": 770,
            },
            {
                "index": 1,
                "tag": "div",
                "className": "style_grid__OqsER",
                "top": 4578,
                "height": 818,
            },
        ],
    }

    synthesized = synthesize_ref_sections_from_section_map(
        section_map,
        [],
        active_view_width=1440,
    )

    assert len(synthesized) == 1
    assert synthesized[0]["className"] == "style_grid__OqsER"


def test_pair_sections_reports_duplicate_inside_matched_landmark_as_extra() -> None:
    """A second real section is not hidden as landmark enumeration noise."""
    ref = [
        _section_map_row(
            0, tag="main", section_id="home", top=0, height=8000
        ),
        _section_map_row(
            1, tag="section", section_id="hero", top=500, height=900
        ),
    ]
    impl = [
        _impl_row(
            0, tag="main", section_id="home", top=0, height=8000
        ),
        _impl_row(
            1, tag="section", section_id="hero", top=500, height=900
        ),
        _impl_row(
            2, tag="section", section_id="hero", top=3000, height=2200
        ),
    ]

    matches = pair_sections(ref, impl)

    extras = [
        match
        for match in matches
        if match.get("status") == "EXTRA_IN_IMPL"
    ]
    assert len(extras) == 1
    assert extras[0]["impl"]["index"] == 2
    assert extras[0]["impl"]["id"] == "hero"


def test_pair_sections_reports_distinct_sibling_inside_landmark_as_extra() -> None:
    """Matched landmark children make other unmatched siblings meaningful."""
    ref = [
        _section_map_row(
            0, tag="main", section_id="home", top=0, height=8000
        ),
        _section_map_row(
            1, tag="section", section_id="hero", top=500, height=900
        ),
    ]
    impl = [
        _impl_row(
            0, tag="main", section_id="home", top=0, height=8000
        ),
        _impl_row(
            1, tag="section", section_id="hero", top=500, height=900
        ),
        _impl_row(
            2, tag="section", section_id="promo", top=3000, height=2200
        ),
    ]

    matches = pair_sections(ref, impl)

    extras = [
        match
        for match in matches
        if match.get("status") == "EXTRA_IN_IMPL"
    ]
    assert len(extras) == 1
    assert extras[0]["impl"]["index"] == 2
    assert extras[0]["impl"]["id"] == "promo"


def test_pair_sections_reports_nested_impl_only_row_as_extra() -> None:
    """Containment alone cannot prove that an impl-only row is noise."""
    ref = [
        _section_map_row(
            0, tag="main", section_id="home", top=0, height=8000
        ),
        _section_map_row(
            1, tag="section", section_id="hero", top=500, height=900
        ),
    ]
    impl = [
        _impl_row(
            0, tag="main", section_id="home", top=0, height=8000
        ),
        _impl_row(
            1, tag="section", section_id="hero", top=500, height=900
        ),
        _impl_row(
            2, tag="section", section_id="hero-copy", top=600, height=500
        ),
    ]

    matches = pair_sections(ref, impl)

    extras = [
        match
        for match in matches
        if match.get("status") == "EXTRA_IN_IMPL"
    ]
    assert len(extras) == 1
    assert extras[0]["impl"]["index"] == 2
    assert extras[0]["impl"]["id"] == "hero-copy"


def test_pair_sections_does_not_lock_shared_swiper_state_token() -> None:
    """A size-favored carousel decoy must not steal the semantic news section."""
    ref = [
        _section_map_row(
            0, section_id="hero", top=0, height=700
        ),
        _section_map_row(
            1, cls="main-news swiper-wrapper active", top=932, height=700
        ),
        _section_map_row(
            2, section_id="careers", top=1900, height=700
        ),
    ]
    impl = [
        _impl_row(
            0, section_id="hero", top=0, height=700
        ),
        _impl_row(
            1, cls="swiper-wrapper active", top=180, height=700
        ),
        _impl_row(
            2, cls="main-news swiper-wrapper next", top=930, height=300
        ),
        _impl_row(
            3, section_id="careers", top=1900, height=700
        ),
    ]

    matches = pair_sections(ref, impl)

    main_news = next(
        match
        for match in matches
        if match.get("ref", {}).get("className") == "main-news swiper-wrapper active"
    )
    assert main_news["impl"]["rect"]["top"] == 930
    assert main_news["pairing"] == "anchor-locked"
