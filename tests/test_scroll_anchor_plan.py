import json
from pathlib import Path

from ui_clone.scroll_anchor_plan import build_scroll_anchor_plan, load_eval_json

REPO_ROOT = Path(__file__).resolve().parents[1]


def _section(index: int, top: int, height: int, text: str, cls: str = "") -> dict:
    return {
        "index": index,
        "tag": "section",
        "className": cls,
        "rect": {"top": top, "height": height, "width": 1440, "left": 0},
        "fingerprint": text,
        "textWords": text.lower(),
        "childCount": 2,
    }


def test_scroll_anchor_plan_pairs_sections_by_own_offsets_not_percent() -> None:
    ref = [
        _section(0, 0, 700, "Real Food Wins hero headline"),
        _section(1, 1200, 700, "Food pyramid section content"),
        _section(2, 2400, 500, "Footer banana design credit"),
    ]
    impl = [
        _section(0, 0, 500, "Real Food Wins hero headline"),
        _section(1, 800, 500, "Food pyramid section content"),
        _section(2, 1500, 400, "Footer banana design credit"),
    ]

    plan = build_scroll_anchor_plan(ref, impl, viewport_height=800)

    assert [row["reason"] for row in plan] == ["section-anchor"] * 3
    pyramid = next(row for row in plan if "section-1" in row["name"])
    assert pyramid["refY"] == 1150
    assert pyramid["implY"] == 750
    assert pyramid["refY"] != pyramid["implY"], (
        "section-aligned capture must use each page's matched section offset, "
        "not one shared scroll percentage"
    )


def test_scroll_anchor_plan_adds_sticky_phase_probes() -> None:
    ref = [
        _section(0, 0, 700, "Hero intro content"),
        _section(1, 900, 2400, "Broken system sticky cards", "dga_broken_system_sticky__K1eSK"),
        _section(2, 3500, 500, "Footer content"),
    ]
    impl = [
        _section(0, 0, 650, "Hero intro content"),
        _section(1, 760, 1800, "Broken system sticky cards", "broken-system-sticky"),
        _section(2, 2700, 500, "Footer content"),
    ]

    plan = build_scroll_anchor_plan(
        ref,
        impl,
        viewport_height=800,
        sticky=[{"cls": "dga_broken_system_sticky__K1eSK", "position": "sticky"}],
    )

    sticky_rows = [row for row in plan if row["reason"] == "sticky-phase"]
    assert [row["phase"] for row in sticky_rows] == ["enter", "mid", "exit"]
    assert sticky_rows[0]["refY"] < sticky_rows[1]["refY"] < sticky_rows[2]["refY"]
    assert sticky_rows[0]["implY"] < sticky_rows[1]["implY"] < sticky_rows[2]["implY"]


def test_scroll_anchor_plan_adds_scroll_transition_phases_for_large_sections() -> None:
    ref = [
        _section(0, 0, 900, "Hero intro"),
        _section(1, 1100, 2200, "Cards reveal as you scroll"),
    ]
    impl = [
        _section(0, 0, 700, "Hero intro"),
        _section(1, 900, 1800, "Cards reveal as you scroll"),
    ]
    spec = {"transitions": [{"id": "cards", "trigger": "intersection", "selector": "main section"}]}

    plan = build_scroll_anchor_plan(ref, impl, viewport_height=800, transition_spec=spec)

    assert any(row["reason"] == "scroll-transition-phase" for row in plan)
    phases = [row["phase"] for row in plan if row["reason"] == "scroll-transition-phase"]
    assert phases == ["enter", "mid", "exit"]


def test_load_eval_json_peels_agent_browser_result_envelope(tmp_path: Path) -> None:
    path = tmp_path / "eval.json"
    path.write_text(json.dumps({"success": True, "data": {"result": json.dumps([{"index": 0}])}}))

    assert load_eval_json(path) == [{"index": 0}]


def test_batch_scroll_missing_screenshots_are_hard_failure() -> None:
    script = (REPO_ROOT / "skills/visual-debug/scripts/batch-scroll.sh").read_text()

    assert "CAPTURE_RC=1" in script
    assert 'exit "$CAPTURE_RC"' in script


def test_scroll_coverage_propagates_batch_scroll_capture_failure() -> None:
    script = (REPO_ROOT / "skills/visual-debug/scripts/scroll-coverage-check.sh").read_text()

    assert "BS_RC=$?" in script
    assert "batch-scroll capture failed" in script
    assert 'exit 1' in script
