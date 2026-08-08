"""Hitting the resource cap is a coverage hole, not a clean pass.

The mirror stops at `_DEFAULT_MAX_RESOURCES` (300) and records every remaining
candidate as `skipped / max-resources-reached`. Status was only downgraded when
nothing was attempted or a download errored, so a truncated mirror reported
`status: "pass"`.

Observed on a fresh playbook.ebay.com run: 425 candidates, 300 downloaded, 111
rows dropped to the cap — 12 distinct base images, 11 of which had no variant
mirrored at all (Motionfoundationthumb.png, Interactionfoundationthumb.png,
Iconographyfoundationthumb.png, strategyhero.png, …). Those are visible content
images; losing them surfaces much later as an image-fidelity warning whose root
cause is three phases upstream and invisible in the mirror's own status.
"""

import json
from pathlib import Path

from scripts.extract._resource_mirror import _manifest_status
from ui_clone.pipeline_phases.checks import resource_mirror_status_problem


def test_cap_truncation_is_not_a_pass() -> None:
    rows = [{"status": "downloaded"}] * 300 + [
        {"status": "skipped", "reason": "max-resources-reached"}
    ] * 111
    status, reasons = _manifest_status(rows, attempted=300)
    assert status == "warn"
    blob = " ".join(reasons)
    assert "111" in blob
    # The operator needs the lever, not just the symptom.
    assert "UI_CLONE_RESOURCE_MIRROR_MAX_RESOURCES" in blob


def test_policy_skips_alone_stay_a_pass() -> None:
    # non-mirrorable-type / external-origin are deliberate policy, not truncation.
    rows = [{"status": "downloaded"}] * 10 + [
        {"status": "skipped", "reason": "non-mirrorable-type"},
        {"status": "skipped", "reason": "external-origin"},
    ]
    status, reasons = _manifest_status(rows, attempted=10)
    assert status == "pass"
    assert reasons == []


def _write(tmp_path: Path, payload: dict) -> Path:
    (tmp_path / "resource-manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


def test_a_warn_manifest_is_surfaced_to_the_pipeline(tmp_path: Path) -> None:
    """Recording the hole is inert if nothing reads it. The only pipeline check on
    this artifact was file existence, so a capped mirror stayed invisible outside
    the CLI's own stdout."""
    ref = _write(
        tmp_path,
        {
            "status": "warn",
            "statusReasons": ["111 resource(s) dropped at the maxResources cap"],
            "summary": {"candidates": 425, "downloaded": 300},
        },
    )
    problem = resource_mirror_status_problem(ref)
    assert problem is not None
    assert "111" in problem


def test_a_clean_manifest_is_silent(tmp_path: Path) -> None:
    ref = _write(tmp_path, {"status": "pass", "statusReasons": []})
    assert resource_mirror_status_problem(ref) is None


def test_absent_or_unreadable_manifest_is_not_invented_as_a_problem(tmp_path: Path) -> None:
    assert resource_mirror_status_problem(tmp_path) is None
    (tmp_path / "resource-manifest.json").write_text("{ not json", encoding="utf-8")
    assert resource_mirror_status_problem(tmp_path) is None


def test_existing_downgrades_are_preserved() -> None:
    assert _manifest_status([], attempted=0)[0] == "warn"
    failed_rows = [{"status": "downloaded"}, {"status": "failed"}]
    status, reasons = _manifest_status(failed_rows, attempted=2)
    assert status == "warn"
    assert any("failed to download" in r for r in reasons)
