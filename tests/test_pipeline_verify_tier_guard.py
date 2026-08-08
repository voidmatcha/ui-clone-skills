import json
from pathlib import Path

from ui_clone.pipeline_phases.verify import _quick_tier_blocker


def test_pipeline_verify_blocks_quick_tier_stamp(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "tier": "quick", "requiredChecks": []}),
        encoding="utf-8",
    )

    blocker = _quick_tier_blocker(ref)

    assert blocker is not None
    assert "tier=quick" in blocker
    assert "tier=standard" in blocker


def test_pipeline_verify_allows_standard_or_missing_plan(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    assert _quick_tier_blocker(ref) is None

    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "tier": "standard", "requiredChecks": []}),
        encoding="utf-8",
    )
    assert _quick_tier_blocker(ref) is None
