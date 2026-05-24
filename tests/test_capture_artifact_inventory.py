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
        timeout=30,
    )

    assert proc.returncode == 1, f"missing artifact manifest must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert artifact["status"] == "fail"
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
        timeout=30,
    )

    assert proc.returncode == 0, f"complete artifact manifest must pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["regionsChecked"] == 1
