"""What a stamped absence certificate may and may not override downstream.

The producer stamps `capture.authoritativeNegative`; three consumers read it.
The certificate is derived from the Phase A samples, so it speaks for signals
from the same capture or that mean only "something animates at load"
(`summary.json polls > 1`, transition-spec `page-load`). It must never veto a
splash-specific claim from a detector that reads what the capture cannot
(bundle analysis `hasPreloader`/`hasSplash`, dual-snapshot `preloaderRemoved`).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_verification_plan(ref_dir: Path, tier: str | None = None) -> dict[str, Any]:
    script = REPO_ROOT / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    cmd = ["bash", str(script), str(ref_dir)]
    if tier is not None:
        cmd.append(f"--tier={tier}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"verification-plan.sh failed: {proc.stderr}"
    return json.loads((ref_dir / "verification-plan.json").read_text())  # type: ignore[no-any-return]


def _stamped_contract(stamp: bool, *, capture_mode: str = "pre-navigation") -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "captureMode": capture_mode,
        "detected": False,
        "overlay": {"everVisible": False, "maxCoverage": 0, "exitObserved": False},
        "capture": {
            "stateCount": 13,
            "timedOut": False,
            "reason": "stable-2s",
            "absenceEvidence": {
                "overlayEverVisible": False,
                "coveringSurveyed": True,
                "coveringExits": [],
                "rootClassesRemoved": [],
                "structuralShift": False,
            },
            "authoritativeNegative": stamp,
        },
    }


def _write_splash_dir(ref: Path, contract: dict[str, Any], *, polls: int = 13) -> None:
    splash = ref / "states" / "splash"
    splash.mkdir(parents=True, exist_ok=True)
    (splash / "contract.json").write_text(json.dumps(contract), encoding="utf-8")
    (splash / "summary.json").write_text(
        json.dumps({"checked": True, "polls": polls, "reason": "stable-2s", "timedOut": False}),
        encoding="utf-8",
    )
    (splash / "trajectory.json").write_text(
        json.dumps([
            {"ts_ms": 0, "hash": 1, "bodyClass": "", "htmlClass": "", "domLength": 121759},
            {"ts_ms": 559, "hash": 2, "bodyClass": "", "htmlClass": "", "domLength": 122542},
            {"ts_ms": 4154, "hash": 3, "bodyClass": "", "htmlClass": "", "domLength": 124262},
        ]),
        encoding="utf-8",
    )


# ── verification-plan.sh ────────────────────────────────────────────────────


def test_certified_absence_overrides_same_capture_and_generic_load_signals(tmp_path: Path) -> None:
    """navercorp shape: 13 polls and a transition-spec page-load reveal, no
    splash-specific detector. The certificate wins; no splash check runs."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_splash_dir(ref, _stamped_contract(True))
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"id": "hero-reveal", "trigger": "page-load", "kind": "fade"}]}),
        encoding="utf-8",
    )

    plan = _run_verification_plan(ref, tier="standard")
    rows = {c["id"] for c in plan["requiredChecks"]}

    assert plan["signals"]["hasSplash"] is False
    assert "splash-lifecycle" not in rows


def test_stamped_false_falls_through_to_summary_polls(tmp_path: Path) -> None:
    """An uncertified capture keeps the polls > 1 backstop live."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_splash_dir(ref, _stamped_contract(False))

    plan = _run_verification_plan(ref, tier="standard")
    rows = {c["id"] for c in plan["requiredChecks"]}

    assert plan["signals"]["hasSplash"] is True
    assert "splash-lifecycle" in rows


@pytest.mark.parametrize(
    ("artifact", "payload"),
    [
        ("interactions-detected.json", {"interactions": [], "hasPreloader": True}),
        ("interactions-detected.json", {"interactions": [], "hasSplash": True}),
        ("dom-state-diff.json", {"preloaderRemoved": True, "splashElements": [".o-loader"]}),
    ],
)
def test_certified_absence_never_vetoes_a_splash_specific_detector(
    tmp_path: Path, artifact: str, payload: dict[str, Any]
) -> None:
    """Bundle analysis and the dual-snapshot diff can see a clip-path curtain
    or a body-background preloader that changes no bounding box, class, or DOM
    length. Their positive claim stands over the capture's absence."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_splash_dir(ref, _stamped_contract(True), polls=1)
    (ref / artifact).write_text(json.dumps(payload), encoding="utf-8")

    plan = _run_verification_plan(ref, tier="standard")
    rows = {c["id"] for c in plan["requiredChecks"]}

    assert plan["signals"]["hasSplash"] is True
    assert "splash-lifecycle" in rows


def test_stamped_true_under_reuse_session_does_not_certify(tmp_path: Path) -> None:
    """The producer never writes this shape; a consumer must not honour it."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_splash_dir(ref, _stamped_contract(True, capture_mode="reuse-session"))

    plan = _run_verification_plan(ref, tier="standard")

    assert plan["signals"]["hasSplash"] is True


# ── generation-plan.sh ──────────────────────────────────────────────────────


def _run_generation_plan(ref: Path) -> dict[str, Any]:
    subprocess.run(
        ["bash", str(REPO_ROOT / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return json.loads((ref / "generation-plan.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _page_load_transition_spec() -> dict[str, Any]:
    return {
        "transitions": [
            {
                "id": "page-load-overlay",
                "trigger": "page-load",
                "selector": ".intro",
                "kind": "splash",
                "durationMs": 1400,
                "sourceArtifact": "transition-spec.json",
            }
        ]
    }


@pytest.mark.parametrize(
    ("stamp", "capture_mode", "intro_required"),
    [
        (True, "pre-navigation", False),
        (False, "pre-navigation", True),
        (True, "reuse-session", True),
    ],
)
def test_generation_plan_reads_the_stamp_for_the_page_load_fallback(
    tmp_path: Path, stamp: bool, capture_mode: str, intro_required: bool
) -> None:
    ref = tmp_path / "ref" / "stamped"
    ref.mkdir(parents=True)
    (ref / "animation-init-styles.json").write_text("[]", encoding="utf-8")
    _write_splash_dir(ref, _stamped_contract(stamp, capture_mode=capture_mode))
    (ref / "transition-spec.json").write_text(json.dumps(_page_load_transition_spec()), encoding="utf-8")

    intro = _run_generation_plan(ref)["introAnimation"]

    assert intro["required"] is intro_required
    if intro_required:
        assert intro["sourceId"] == "page-load-overlay"


# ── state-structure-spec.py ─────────────────────────────────────────────────


def _run_state_structure_spec(ref: Path) -> list[dict[str, Any]]:
    subprocess.run(
        ["python3", str(REPO_ROOT / "scripts/extract/state-structure-spec.py"), str(ref)],
        check=True,
        capture_output=True,
        timeout=120,
    )
    spec = json.loads((ref / "state-structure-spec.json").read_text(encoding="utf-8"))
    return [event for event in spec["events"] if event["phase"] == "splash"]


def test_state_structure_spec_reads_the_stamp_in_both_directions(tmp_path: Path) -> None:
    certified = tmp_path / "certified"
    _write_splash_dir(certified, _stamped_contract(True))
    assert _run_state_structure_spec(certified) == []

    uncertified = tmp_path / "uncertified"
    _write_splash_dir(uncertified, _stamped_contract(False))
    events = _run_state_structure_spec(uncertified)
    assert [event["id"] for event in events] == ["splash:page-load"]

    reuse = tmp_path / "reuse"
    _write_splash_dir(reuse, _stamped_contract(True, capture_mode="reuse-session"))
    assert [event["id"] for event in _run_state_structure_spec(reuse)] == ["splash:page-load"]
