import json
from pathlib import Path

import pytest

from ui_clone.hooks._common import quick_tier_blocker, quick_tier_blocker_details
from ui_clone.pipeline import Pipeline
from ui_clone.pipeline_phases.verify import execute_verify


def test_pipeline_verify_blocks_quick_tier_stamp(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "tier": "quick", "requiredChecks": []}),
        encoding="utf-8",
    )

    blocker = quick_tier_blocker(ref)

    assert blocker is not None
    assert "tier=quick" in blocker
    assert "tier=standard" in blocker


def test_pipeline_verify_allows_standard_or_missing_plan(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    assert quick_tier_blocker(ref) is None

    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "tier": "standard", "requiredChecks": []}),
        encoding="utf-8",
    )
    assert quick_tier_blocker(ref) is None


@pytest.mark.parametrize(
    ("status", "expected_action", "expected_text"),
    [
        ("interrupted", "rerun_full_required_checks", "interrupted"),
        ("running", "wait_for_or_rerun_required_checks", "still running"),
    ],
)
def test_quick_tier_blocker_details_interrupted_and_running_receipts(
    tmp_path: Path, status: str, expected_action: str, expected_text: str
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    # A standard-tier plan must not mask the receipt-derived blocker.
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "tier": "standard", "requiredChecks": []}),
        encoding="utf-8",
    )
    (ref / "iteration-receipt.json").write_text(
        json.dumps({"schemaVersion": 1, "mode": "final", "status": status}),
        encoding="utf-8",
    )

    details = quick_tier_blocker_details(ref)

    assert details is not None
    message, action = details
    assert expected_text in message
    assert action == expected_action
    assert quick_tier_blocker(ref) == message


def test_pipeline_verify_json_routes_failed_dispatch_to_failed_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ref = tmp_path / "tmp" / "ref" / "comp"
    impl = tmp_path / "impl"
    ref.mkdir(parents=True)
    impl.mkdir()
    (ref / ".impl-root").write_text(str(impl))
    (ref / "iteration-receipt.json").write_text(
        json.dumps(
            {
                "mode": "final",
                "status": "failed",
                "failedChecks": ["layout", "motion"],
            }
        )
    )
    monkeypatch.chdir(tmp_path)
    pipeline = Pipeline("https://example.test", "comp", "test-session")

    assert execute_verify(pipeline, json_output=True) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["next_action"] == "fix_failed_required_checks"
    assert "layout, motion" in payload["reason"]
