from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_capture_artifact_inventory_fails_when_region_lacks_artifact_manifest(tmp_path: Path) -> None:
    """ui-capture regions must enumerate their capture files."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "hover": [
            {"name": "cta-hover", "triggerType": "css-hover", "selector": ".cta"}
        ]
    }))

    script = ROOT / "skills" / "visual-debug" / "scripts" / "capture-artifact-inventory-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 1, f"missing artifact manifest must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["missingArtifacts"][0]["reason"] == "missing artifacts manifest"


def test_capture_artifact_inventory_passes_dispatch_only_regions(tmp_path: Path) -> None:
    """Spec-derived dispatch-only regions carry no per-state capture manifest by
    design — they must be recorded as dispatch-only, NOT failed as 'missing
    manifest' (which would push agents to hand-edit artifacts)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "placeholder": False,
        "detectionRan": True,
        "source": "derive-from-transition-spec",
        "derivedFrom": ["transition-spec.json", "section-map.json"],
        "regions": [
            {"name": "hover-btn", "triggerType": "hover", "selector": ".btn",
             "dispatchOnly": True, "referenceFrames": ["static/ref/section-0.png"]},
        ],
    }))

    script = ROOT / "skills" / "visual-debug" / "scripts" / "capture-artifact-inventory-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, f"dispatch-only regions must not fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["missingArtifacts"] == []
    assert artifact["dispatchOnlyRegions"][0]["region"] == "hover-btn"


def test_capture_artifact_inventory_rejects_handset_dispatchonly_without_provenance(tmp_path: Path) -> None:
    """id30: a per-region dispatchOnly:true is forgeable — an agent can hand-add
    it to a real capture-needing region to skip its manifest. dispatch-only must
    be honored ONLY when the FILE carries source==derive-from-transition-spec
    (regions-level provenance the legit producer stamps). Without it, the region
    still owes its capture manifest and must FAIL."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "placeholder": False,
        "detectionRan": True,
        # NO file-level source==derive-from-transition-spec provenance.
        "regions": [
            {"name": "hover-btn", "triggerType": "hover", "selector": ".btn",
             "dispatchOnly": True},
        ],
    }))

    script = ROOT / "skills" / "visual-debug" / "scripts" / "capture-artifact-inventory-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 1, f"hand-set dispatchOnly without provenance must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["dispatchOnlyRegions"] == []
    assert artifact["missingArtifacts"][0]["reason"] == "missing artifacts manifest"


def test_capture_artifact_inventory_rejects_source_without_derived_provenance(
    tmp_path: Path,
) -> None:
    """The producer source string alone must not bypass capture evidence."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "placeholder": False,
        "detectionRan": True,
        "source": "derive-from-transition-spec",
        "regions": [
            {"name": "hover-btn", "triggerType": "hover", "selector": ".btn",
             "dispatchOnly": True},
        ],
    }))

    script = ROOT / "skills" / "visual-debug" / "scripts" / "capture-artifact-inventory-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 1, (
        f"source without transition-spec derivation must fail: {proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert artifact["dispatchOnlyRegions"] == []
    assert artifact["missingArtifacts"][0]["reason"] == "missing artifacts manifest"


def test_capture_artifact_inventory_passes_with_explicit_existing_files(tmp_path: Path) -> None:
    """Explicit per-region artifact paths are accepted when every file exists."""
    ref = tmp_path / "ref"
    (ref / "clip" / "ref").mkdir(parents=True)
    for state in ("idle", "active"):
        (ref / "clip" / "ref" / f"cta-hover-{state}.png").write_bytes(b"\x89PNG" + b"0" * 128)
    (ref / "regions.json").write_text(json.dumps({
        "hover": [
            {
                "name": "cta-hover",
                "triggerType": "css-hover",
                "selector": ".cta",
                "artifacts": {
                    "idle": "clip/ref/cta-hover-idle.png",
                    "active": "clip/ref/cta-hover-active.png",
                },
            }
        ]
    }))

    script = ROOT / "skills" / "visual-debug" / "scripts" / "capture-artifact-inventory-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, f"complete artifact manifest must pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["regionsChecked"] == 1
