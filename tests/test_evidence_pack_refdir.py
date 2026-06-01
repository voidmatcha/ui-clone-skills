from __future__ import annotations

import json
from pathlib import Path

from ui_clone import evidence_pack


def test_build_pack_from_ref_dir_rolls_up_capture_and_bundle_artifacts(tmp_path: Path) -> None:
    ref = tmp_path / "ref" / "demo"
    (ref / "states" / "splash").mkdir(parents=True)
    (ref / "states" / "hover").mkdir(parents=True)
    (ref / "sections").mkdir(parents=True)
    (ref / "states" / "splash" / "summary.json").write_text(
        json.dumps({"checked": True, "changed": True, "durationMs": 900}),
        encoding="utf-8",
    )
    (ref / "states" / "hover" / "summary.json").write_text(
        json.dumps({"candidatesFound": 4, "candidatesProcessed": 2}),
        encoding="utf-8",
    )
    (ref / "verification-plan.json").write_text(
        json.dumps({"signals": {"hasSplash": True, "hasHover": True}}),
        encoding="utf-8",
    )
    (ref / "bundle-map.json").write_text(
        json.dumps({"chunks": [{"file": "bundles/app.js", "libraries": ["gsap"]}]}),
        encoding="utf-8",
    )
    (ref / "external-sdks.json").write_text(json.dumps({"gsap": True}), encoding="utf-8")
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"id": "entry-reveal", "trigger": "initial"}]}),
        encoding="utf-8",
    )
    (ref / "element-target-a.json").write_text(
        json.dumps(
            {
                "ok": True,
                "annotation": {
                    "selector": "[data-probe=target-a]",
                    "selectorCandidates": [
                        "[data-probe=target-a]",
                        "main > section:nth-of-type(1) > button:nth-of-type(2)",
                    ],
                    "bbox": {"x": 10, "y": 20, "width": 100, "height": 40},
                    "computedStyle": {"fontSize": "16px", "transitionDuration": "180ms"},
                    "timeline": [{"phase": "idle", "changed": True}],
                },
            }
        ),
        encoding="utf-8",
    )
    (ref / "sections" / "result.txt").write_text("2/3 sections pass\n", encoding="utf-8")

    pack = evidence_pack.build_pack_from_ref_dir(ref)
    brief = evidence_pack.build_worker_brief(pack)

    assert pack["session"]["refDir"] == str(ref)
    assert {item["id"] for item in pack["annotations"]} == {
        "bundle-analysis",
        "element-target-a",
        "state-hover",
        "state-splash",
        "verification-plan",
    }
    assert "[data-probe=target-a]" in brief
    assert "main > section:nth-of-type(1) > button:nth-of-type(2)" in brief
    assert "element-target-a.json" in brief
    assert "trigger: hover" in brief
    assert "trigger: initial-auto" in brief
    assert "bundle analysis is mandatory" in brief
    assert "bundle-map.json" in brief
    assert "transition-spec.json" in brief
    assert "states/splash/summary.json" in brief
    assert "verification-plan.json" in brief
