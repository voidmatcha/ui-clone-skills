from __future__ import annotations

import json
from pathlib import Path

from ui_clone.check_inputs import get_check_inputs

from ._helpers import _run_verification_plan


def test_verification_plan_adds_replay_track_compare_for_declared_tracks(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(
        json.dumps(
            {
                "regions": [
                    {
                        "name": "hero",
                        "triggerType": "scroll-driven",
                        "artifacts": {
                            "replayTrack": "clip/ref/hero-replay-track.json",
                            "replayTrackManifest": "clip/ref/hero-replay-track.manifest.json",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    plan = _run_verification_plan(ref, tier="quick")
    rows = {row["id"]: row for row in plan["requiredChecks"]}

    assert rows["replay-track-compare"] == {
        "id": "replay-track-compare",
        "script": "skills/visual-debug/scripts/replay-track-compare.py",
        "produces": "transitions/replay-track-compare.json",
        "reason": "regions.json declares replayTrack evidence — impl must reproduce every deterministic scroll replay track against the validated reference baseline",
        "severity": "block",
        "tier": "quick",
        "argsRecipe": "{impl_url} {ref_dir}",
    }


def test_verification_plan_records_scroll_driven_without_replay_track(
    tmp_path: Path,
) -> None:
    """A scroll-driven region that declares no replayTrack evidence must appear
    as tracked debt. Omitting the row entirely let a consumer read the absence
    of a replay comparison as a passing replay comparison."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(
        json.dumps(
            {
                "regions": [
                    {
                        "name": "hero",
                        "triggerType": "scroll-driven",
                        "artifacts": {
                            "before": "clip/ref/hero-before.png",
                            "mid": "clip/ref/hero-mid.png",
                            "after": "clip/ref/hero-after.png",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    plan = _run_verification_plan(ref, tier="quick")

    required = {row["id"] for row in plan["requiredChecks"]}
    assert "replay-track-compare" not in required

    deferred = {row["id"]: row for row in plan["deferredChecks"]}
    assert "replay-track-compare" in deferred, plan["deferredChecks"]
    assert deferred["replay-track-compare"]["severity"] == "block"
    assert "no replayTrack" in deferred["replay-track-compare"]["unmetEvidence"]


def test_check_inputs_replay_track_compare_hashes_declared_ref_tracks() -> None:
    spec = get_check_inputs("replay-track-compare")

    assert spec is not None
    assert "regions.json" in spec.ref
    assert "clip/ref/*.json" in spec.ref
    assert "transitions/ref/*.json" in spec.ref
