"""Tests for verification-plan.sh --amend (design fix #9 — close the ordering
hole where signature-effects-coverage never registers because the plan is minted
before generation-plan.json exists)."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from ._helpers import _project_root

SCRIPT = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"

_SIG_EFFECTS_PLAN = {
    "schemaVersion": 2,
    "componentList": [],
    "signatureEffects": [{"id": "char-scrub", "kind": "per-char-scroll"}],
}


def _mint(ref: Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), "--tier=quick", *extra_args],
        capture_output=True, text=True, timeout=60,
    )


def _seed(ref: Path) -> None:
    ref.mkdir(parents=True, exist_ok=True)
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"id": "a", "trigger": "scroll"}]})
    )
    (ref / "extracted.json").write_text(json.dumps({"sections": []}))


def _ids(ref: Path) -> set[str]:
    plan = json.loads((ref / "verification-plan.json").read_text())
    return {c["id"] for c in plan["requiredChecks"]}


def test_amend_appends_signature_effects_when_plan_arrives(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    _seed(ref)
    # 1) mint before generation-plan.json exists — no plan-derived row.
    assert _mint(ref).returncode == 0
    assert "signature-effects-coverage" not in _ids(ref)
    before = json.loads((ref / "verification-plan.json").read_text())

    # 2) generation-plan.json now declares signatureEffects → amend appends it.
    (ref / "generation-plan.json").write_text(json.dumps(_SIG_EFFECTS_PLAN))
    proc = _mint(ref, "--amend")
    assert proc.returncode == 0, proc.stderr
    after = json.loads((ref / "verification-plan.json").read_text())
    assert "signature-effects-coverage" in _ids(ref)
    # closed list: generatedAt frozen, amendedAt stamped, only the one row added.
    assert after["generatedAt"] == before["generatedAt"]
    assert "amendedAt" in after
    added = _ids(ref) - {c["id"] for c in before["requiredChecks"]}
    assert added == {"signature-effects-coverage"}


def test_amend_is_idempotent(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    _seed(ref)
    _mint(ref)
    (ref / "generation-plan.json").write_text(json.dumps(_SIG_EFFECTS_PLAN))
    _mint(ref, "--amend")
    _mint(ref, "--amend")
    ids = [c["id"] for c in json.loads((ref / "verification-plan.json").read_text())["requiredChecks"]]
    assert ids.count("signature-effects-coverage") == 1


def test_amend_without_signature_effects_adds_nothing(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    _seed(ref)
    _mint(ref)
    before = _ids(ref)
    (ref / "generation-plan.json").write_text(
        json.dumps({"schemaVersion": 2, "componentList": []})
    )
    _mint(ref, "--amend")
    assert _ids(ref) == before
    assert "signature-effects-coverage" not in _ids(ref)


def test_amend_without_existing_plan_generates_fresh(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    _seed(ref)
    (ref / "generation-plan.json").write_text(json.dumps(_SIG_EFFECTS_PLAN))
    # No prior plan — amend degrades to a fresh full generation that already
    # includes the plan-derived row (generation-plan.json exists).
    proc = _mint(ref, "--amend")
    assert proc.returncode == 0, proc.stderr
    assert (ref / "verification-plan.json").is_file()
    assert "signature-effects-coverage" in _ids(ref)


def test_amend_regenerates_stale_plan_after_hover_manifest_refresh(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    _seed(ref)
    assert _mint(ref).returncode == 0
    before = json.loads((ref / "verification-plan.json").read_text())
    assert before["signals"]["hasHover"] is False

    hover = ref / "states" / "hover"
    hover.mkdir(parents=True)
    manifest = hover / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "hover-card",
                        "kind": "css",
                        "selector": ".card",
                        "activation": ".card",
                    }
                ]
            }
        )
    )
    future = time.time() + 5
    os.utime(manifest, (future, future))

    proc = _mint(ref, "--amend")
    assert proc.returncode == 0, proc.stderr
    after = json.loads((ref / "verification-plan.json").read_text())
    assert after["signals"]["hasHover"] is True
