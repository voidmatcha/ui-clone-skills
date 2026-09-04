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

    # evidenceGaps, NOT deferredChecks: deferred_checks_blocker() blocks
    # closeout on deferredChecks and tells the agent to re-run at a higher
    # tier, which cannot conjure evidence that was never captured.
    assert not [
        row for row in plan["deferredChecks"]
        if row.get("id") == "replay-track-compare"
    ], plan["deferredChecks"]

    gaps = {row["id"]: row for row in plan["evidenceGaps"]}
    assert "replay-track-compare" in gaps, plan["evidenceGaps"]
    assert gaps["replay-track-compare"]["severity"] == "block"
    assert "hero" in gaps["replay-track-compare"]["unmetEvidence"]


def test_verification_plan_gap_does_not_block_closeout(tmp_path: Path) -> None:
    """An uncaptured-evidence gap must stay visible without deadlocking
    closeout — raising the tier cannot produce a missing artifact."""
    from ui_clone.hooks._common import deferred_checks_blocker

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(
        json.dumps({
            "regions": [{
                "name": "hero",
                "triggerType": "scroll-driven",
                "artifacts": {"before": "clip/ref/hero-before.png"},
            }]
        }),
        encoding="utf-8",
    )

    plan = _run_verification_plan(ref, tier="comprehensive")
    assert plan["evidenceGaps"], plan
    assert deferred_checks_blocker(ref) is None


def test_verification_plan_reports_per_region_replay_track_gaps(
    tmp_path: Path,
) -> None:
    """One region declaring a track must not hide the gap for a sibling that
    declares none — replay-track-compare only compares declared tracks."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(
        json.dumps({
            "regions": [
                {
                    "name": "hero",
                    "triggerType": "scroll-driven",
                    "artifacts": {"replayTrack": "clip/ref/hero.track.json"},
                },
                {
                    "name": "pricing",
                    "triggerType": "scroll-driven",
                    "artifacts": {"before": "clip/ref/pricing.png"},
                },
            ]
        }),
        encoding="utf-8",
    )

    plan = _run_verification_plan(ref, tier="comprehensive")

    required = {row["id"] for row in plan["requiredChecks"]}
    assert "replay-track-compare" in required

    gaps = {row["id"]: row for row in plan["evidenceGaps"]}
    assert "replay-track-compare" in gaps, plan["evidenceGaps"]
    evidence = gaps["replay-track-compare"]["unmetEvidence"]
    assert "pricing" in evidence, evidence
    assert "hero" not in evidence, evidence


def test_check_inputs_replay_track_compare_hashes_declared_ref_tracks() -> None:
    spec = get_check_inputs("replay-track-compare")

    assert spec is not None
    assert "regions.json" in spec.ref
    assert "clip/ref/*.json" in spec.ref
    assert "transitions/ref/*.json" in spec.ref
