from __future__ import annotations

from ui_clone.gates.transition_fires import _stroke_drew, decide

REVEAL_ENTRY = {
    "id": "svg-stroke-draw",
    "trigger": "in-view / scroll state (t ? drawn : hidden)",
    "target": "decorative SVG strokes",
    "animation": {"property": "strokeDashoffset", "from": "dashLength", "to": 0},
}


def test_stroke_drew_detects_draw_in() -> None:
    """A stroke draw-in animates strokeDashoffset length->0 and touches neither
    opacity nor transform — the reveal judgment false-negatived a firing draw
    ('opacity 0 -> 0, transform I -> I'). A decreasing offset magnitude is the
    measured delta."""
    assert _stroke_drew({"strokeDashoffset": "240px"}, {"strokeDashoffset": "0px"})
    assert _stroke_drew({"strokeDashoffset": "240px"}, {"strokeDashoffset": "12px"})


def test_stroke_drew_rejects_static_and_draw_out() -> None:
    # anti-loosening: static dashes and draw-OUTs are not the spec'd reveal
    assert not _stroke_drew({"strokeDashoffset": "240px"}, {"strokeDashoffset": "240px"})
    assert not _stroke_drew({"strokeDashoffset": "0px"}, {"strokeDashoffset": "240px"})
    assert not _stroke_drew({}, {"strokeDashoffset": "0px"})
    assert not _stroke_drew({"strokeDashoffset": "240px"}, {})
    assert not _stroke_drew({"strokeDashoffset": "abc"}, {"strokeDashoffset": "0px"})


def test_reveal_decision_passes_on_stroke_delta_alone() -> None:
    obs = {
        "found": True,
        "before": {"opacity": "1", "transform": "none", "strokeDashoffset": "240px"},
        "after": {"opacity": "1", "transform": "none", "strokeDashoffset": "0px"},
    }
    res = decide(REVEAL_ENTRY, obs, set())
    assert res["status"] == "pass", res
    assert "strokeDashoffset" in res["observed"], res


def test_reveal_decision_still_fails_without_any_delta() -> None:
    obs = {
        "found": True,
        "before": {"opacity": "0", "transform": "none", "strokeDashoffset": "240px"},
        "after": {"opacity": "0", "transform": "none", "strokeDashoffset": "240px"},
    }
    res = decide(REVEAL_ENTRY, obs, set())
    assert res["status"] == "fail", res
