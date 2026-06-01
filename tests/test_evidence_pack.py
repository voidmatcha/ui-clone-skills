from __future__ import annotations

import json
from pathlib import Path

from ui_clone import evidence_pack


def _write_pack(tmp_path: Path) -> Path:
    pack = {
        "session": {
            "id": "qa-sample",
            "url": "https://example.com/product",
            "viewport": {"width": 1440, "height": 900},
        },
        "artifacts": [
            {"id": "target-a-crop", "kind": "screenshot", "path": "screenshots/target-a.png"},
        ],
        "annotations": [
            {
                "id": "target-a",
                "selector": "[data-probe=target-a]",
                "selectorCandidates": [
                    "[data-probe=target-a]",
                    "main > section:nth-of-type(1) > button:nth-of-type(2)",
                ],
                "note": "Button is 12px too low and hover lift is missing.",
                "component": "ComponentA",
                "bbox": {"x": 128, "y": 640, "width": 180, "height": 52},
                "computedStyle": {
                    "position": "absolute",
                    "display": "flex",
                    "fontSize": "16px",
                    "lineHeight": "20px",
                    "color": "rgb(255, 255, 255)",
                    "backgroundColor": "rgb(0, 0, 0)",
                    "transform": "matrix(1, 0, 0, 1, 0, 0)",
                    "transitionDuration": "180ms",
                    "transitionTimingFunction": "cubic-bezier(0.2, 0, 0, 1)",
                    "irrelevantVendorNoise": "should not be returned",
                },
                "dom": "<button data-probe='target-a'><span>Start</span></button>",
                "timeline": [
                    {"phase": "idle", "changed": False},
                    {
                        "phase": "hover",
                        "changed": True,
                        "properties": ["transform", "box-shadow"],
                    },
                ],
                "artifacts": ["target-a-crop"],
            },
            {
                "id": "entry-heading",
                "selector": "main > section:nth-of-type(1) h1",
                "note": "Initial splash reveal should happen before interaction.",
                "bbox": {"x": 120, "y": 220, "width": 840, "height": 128},
                "computedStyle": {"fontSize": "96px", "opacity": "1"},
                "timeline": [{"phase": "idle", "changed": True, "properties": ["opacity"]}],
            },
        ],
    }
    path = tmp_path / "pack.json"
    path.write_text(json.dumps(pack), encoding="utf-8")
    return path


def test_load_pack_builds_context_budgeted_worker_brief(tmp_path: Path) -> None:
    pack_path = _write_pack(tmp_path)

    pack = evidence_pack.load_pack(pack_path)
    brief = evidence_pack.build_worker_brief(pack, max_chars=1800)

    assert "Copy the reference UI as closely as possible" in brief
    assert "https://example.com/product" in brief
    assert "[data-probe=target-a]" in brief
    assert "main > section:nth-of-type(1) > button:nth-of-type(2)" in brief
    assert "trigger: hover" in brief
    assert "trigger: initial-auto" in brief
    assert "screenshots/target-a.png" in brief
    assert "irrelevantVendorNoise" not in brief
    assert "<button" not in brief
    assert len(brief) <= 1800


def test_style_slice_keeps_clone_relevant_properties_only() -> None:
    style = {
        "fontSize": "24px",
        "display": "grid",
        "gridTemplateColumns": "1fr 1fr",
        "transitionDuration": "200ms",
        "irrelevantVendorNoise": "drop",
    }

    sliced = evidence_pack.slice_computed_style(style)

    assert sliced == {
        "display": "grid",
        "fontSize": "24px",
        "gridTemplateColumns": "1fr 1fr",
        "transitionDuration": "200ms",
    }


def test_style_slice_truncates_large_values() -> None:
    sliced = evidence_pack.slice_computed_style(
        {
            "backgroundImage": "linear-gradient(" + ("red, " * 80) + "blue)",
            "boxShadow": "0 0 0 1px black",
        }
    )

    assert sliced["backgroundImage"].endswith("…")
    assert len(sliced["backgroundImage"]) <= evidence_pack.STYLE_VALUE_MAX_CHARS
    assert sliced["boxShadow"] == "0 0 0 1px black"


def test_materialize_skill_briefs_for_other_skills(tmp_path: Path) -> None:
    pack_path = _write_pack(tmp_path)
    out_dir = tmp_path / "briefs"

    written = evidence_pack.materialize_skill_briefs(pack_path, out_dir, max_chars=2400)

    assert {path.name for path in written} == {
        "ATTEMPT_FEEDBACK.md",
        "CURRENT_STATE.json",
        "NORTH_STAR.md",
        "REFERENCE_BRIEF.md",
        "WORKER_BRIEF.md",
    }
    state = json.loads((out_dir / "CURRENT_STATE.json").read_text(encoding="utf-8"))
    assert state["targetUrl"] == "https://example.com/product"
    assert state["annotationCount"] == 2
    assert state["triggerCounts"] == {"hover": 1, "initial-auto": 1}
    assert "Latest is not best" in (out_dir / "NORTH_STAR.md").read_text(encoding="utf-8")
