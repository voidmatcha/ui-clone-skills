from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def _write_png(path: Path, *, seed: int | None = None, uniform: bool = False) -> None:
    value = seed if seed is not None else sum(path.name.encode("utf-8"))
    base = (value * 37 % 256, value * 67 % 256, value * 97 % 256)
    image = Image.new("RGB", (8, 8), base)
    if not uniform:
        accent = tuple((channel + 89) % 256 for channel in base)
        for y in range(4):
            for x in range(4):
                image.putpixel((x, y), accent)
    image.save(path)


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
    (ref / "static" / "ref").mkdir(parents=True)
    _write_png(ref / "static" / "ref" / "section-0.png")
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "hover-btn", "trigger": "hover", "reference_frames": ["static/ref/section-0.png"]},
        ]
    }))
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


def test_capture_artifact_inventory_accepts_valid_verify_reference_frame(
    tmp_path: Path,
) -> None:
    """capture-artifact-inventory must accept the same local media paths as
    gate_spec, including ordinary verify/ evidence."""
    ref = tmp_path / "ref"
    ref.mkdir()
    verify = ref / "verify"
    verify.mkdir()
    _write_png(verify / "hero.png")
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "hero", "trigger": "page load", "reference_frames": "verify/hero.png"},
        ]
    }))
    (ref / "regions.json").write_text(json.dumps({
        "placeholder": False,
        "detectionRan": True,
        "source": "derive-from-transition-spec",
        "derivedFrom": ["transition-spec.json", "section-map.json"],
        "regions": [
            {"name": "hero", "triggerType": "page-load", "dispatchOnly": True},
        ],
    }))

    script = ROOT / "skills" / "visual-debug" / "scripts" / "capture-artifact-inventory-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, f"valid verify frame must pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["checkedReferenceFrames"][0]["path"] == "verify/hero.png"


def test_capture_artifact_inventory_accepts_nested_reference_media(
    tmp_path: Path,
) -> None:
    """gate_spec-compatible media can live in any local ref-dir subdirectory."""
    ref = tmp_path / "ref"
    ref.mkdir()
    evidence = ref / "evidence"
    evidence.mkdir()
    _write_png(evidence / "hero.png")
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "hero", "trigger": "page load", "reference_frames": "evidence/hero.png"},
        ]
    }))
    (ref / "regions.json").write_text(json.dumps({
        "placeholder": False,
        "detectionRan": True,
        "source": "derive-from-transition-spec",
        "derivedFrom": ["transition-spec.json", "section-map.json"],
        "regions": [
            {"name": "hero", "triggerType": "page-load", "dispatchOnly": True},
        ],
    }))

    script = ROOT / "skills" / "visual-debug" / "scripts" / "capture-artifact-inventory-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, f"valid nested evidence frame must pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["checkedReferenceFrames"][0]["path"] == "evidence/hero.png"


def test_capture_artifact_inventory_accepts_gate_spec_range_text(
    tmp_path: Path,
) -> None:
    """Range text accepted by gate_spec names every captured media token."""
    ref = tmp_path / "ref"
    ref.mkdir()
    evidence = ref / "verify" / "intro"
    evidence.mkdir(parents=True)
    _write_png(evidence / "f010.png")
    _write_png(evidence / "f030.png")
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {
                "id": "intro",
                "trigger": "scroll",
                "reference_frames": "verify/intro/f010.png to f030.png",
            },
        ]
    }))
    (ref / "regions.json").write_text(json.dumps({
        "placeholder": False,
        "detectionRan": True,
        "source": "derive-from-transition-spec",
        "derivedFrom": ["transition-spec.json", "section-map.json"],
        "regions": [
            {"name": "intro", "triggerType": "scroll-driven", "dispatchOnly": True},
        ],
    }))

    script = ROOT / "skills" / "visual-debug" / "scripts" / "capture-artifact-inventory-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, f"gate-spec range text must pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert {item["path"] for item in artifact["checkedReferenceFrames"]} == {
        "verify/intro/f010.png",
        "f030.png",
    }


def test_capture_artifact_inventory_rejects_missing_spec_reference_frame(
    tmp_path: Path,
) -> None:
    """Spec-derived dispatch-only regions are not capture proof, but every
    reference frame they point at must still exist as concrete ref evidence."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {
                "id": "hover-btn",
                "trigger": "hover",
                "reference_frames": ["static/ref/section-0.png"],
                "referenceFrames": ["static/ref/section-1.png"],
            },
        ]
    }))
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

    assert proc.returncode == 1, f"missing spec reference frame must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert artifact["status"] == "fail"
    missing_paths = {item["path"] for item in artifact["missingArtifacts"]}
    assert missing_paths == {"static/ref/section-0.png", "static/ref/section-1.png"}


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
        _write_png(ref / "clip" / "ref" / f"cta-hover-{state}.png")
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


def _run_inventory_check(ref: Path) -> subprocess.CompletedProcess[str]:
    script = ROOT / "skills" / "visual-debug" / "scripts" / "capture-artifact-inventory-check.sh"
    return subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_capture_artifact_inventory_rejects_undecodable_swiper_raster(tmp_path: Path) -> None:
    """swiper-next is a real emitted triggerType (capture-swiper-artifacts.py).
    It was in neither trigger table, so its artifacts skipped raster decoding
    and a non-image file passed as a captured state."""
    ref = tmp_path / "ref"
    (ref / "clip" / "ref").mkdir(parents=True)
    (ref / "clip" / "ref" / "a.png").write_bytes(b"not-a-png-at-all")
    _write_png(ref / "clip" / "ref" / "b.png")
    (ref / "regions.json").write_text(json.dumps({
        "regions": [
            {
                "name": "carousel",
                "triggerType": "swiper-next",
                "selector": ".swiper",
                "artifacts": {
                    "idle": "clip/ref/a.png",
                    "active": "clip/ref/b.png",
                },
            }
        ]
    }))

    proc = _run_inventory_check(ref)

    assert proc.returncode == 1, proc.stdout
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert artifact["status"] == "fail"
    reasons = " ".join(str(m.get("reason")) for m in artifact["missingArtifacts"])
    assert "decodable" in reasons, artifact["missingArtifacts"]


def test_capture_artifact_inventory_rejects_undecodable_raster_for_unknown_trigger(
    tmp_path: Path,
) -> None:
    """An unrecognized triggerType must not disable raster validation wholesale;
    anything already claiming an image extension still has to decode."""
    ref = tmp_path / "ref"
    (ref / "clip" / "ref").mkdir(parents=True)
    (ref / "clip" / "ref" / "a.png").write_bytes(b"not-a-png-at-all")
    (ref / "regions.json").write_text(json.dumps({
        "regions": [
            {
                "name": "x",
                "triggerType": "totally-new-trigger",
                "selector": ".x",
                "artifacts": {"idle": "clip/ref/a.png"},
            }
        ]
    }))

    proc = _run_inventory_check(ref)

    assert proc.returncode == 1, proc.stdout
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert artifact["status"] == "fail"
    reasons = " ".join(str(m.get("reason")) for m in artifact["missingArtifacts"])
    assert "decodable" in reasons, artifact["missingArtifacts"]


def test_capture_artifact_inventory_reports_extra_declared_artifacts(tmp_path: Path) -> None:
    """The reference gate requires every declared artifact path to appear as a
    checked row. Reporting only the per-trigger required set deadlocked it: the
    checker passed, the gate failed, and rerunning the checker never helped."""
    ref = tmp_path / "ref"
    (ref / "clip" / "ref").mkdir(parents=True)
    for state in ("idle", "active", "video"):
        _write_png(ref / "clip" / "ref" / f"{state}.png", seed=hash(state) % 200)
    (ref / "regions.json").write_text(json.dumps({
        "regions": [
            {
                "name": "cta",
                "triggerType": "css-hover",
                "selector": ".cta",
                "artifacts": {
                    "idle": "clip/ref/idle.png",
                    "active": "clip/ref/active.png",
                    "video": "clip/ref/video.png",
                },
            }
        ]
    }))

    proc = _run_inventory_check(ref)

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert artifact["status"] == "pass"
    reported = {row["path"] for row in artifact["checkedArtifacts"]}
    assert "clip/ref/video.png" in reported, artifact["checkedArtifacts"]


def test_capture_artifact_inventory_rejects_replay_track_without_manifest(tmp_path: Path) -> None:
    """A replay track is optional, but it must be paired with its manifest."""
    ref = tmp_path / "ref"
    (ref / "clip" / "ref").mkdir(parents=True)
    for name in ("hero-before.png", "hero-mid.png", "hero-after.png"):
        _write_png(ref / "clip" / "ref" / name)
    (ref / "clip" / "ref" / "hero-replay-track.json").write_text("{}")
    (ref / "regions.json").write_text(json.dumps({
        "scroll": [
            {
                "name": "hero",
                "triggerType": "scroll-driven",
                "selector": ".hero",
                "artifacts": {
                    "before": "clip/ref/hero-before.png",
                    "mid": "clip/ref/hero-mid.png",
                    "after": "clip/ref/hero-after.png",
                    "replayTrack": "clip/ref/hero-replay-track.json",
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

    assert proc.returncode == 1, f"unpaired replay track must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["missingArtifacts"][0]["state"] == "replayTrackManifest"
    assert artifact["missingArtifacts"][0]["reason"] == "missing paired replay artifact"


def test_capture_artifact_inventory_rejects_replay_manifest_without_track(tmp_path: Path) -> None:
    """A replay manifest is optional, but it must be paired with its track."""
    ref = tmp_path / "ref"
    (ref / "clip" / "ref").mkdir(parents=True)
    for name in ("hero-before.png", "hero-mid.png", "hero-after.png"):
        _write_png(ref / "clip" / "ref" / name)
    (ref / "clip" / "ref" / "hero-replay-track.manifest.json").write_text("{}")
    (ref / "regions.json").write_text(json.dumps({
        "scroll": [
            {
                "name": "hero",
                "triggerType": "scroll-driven",
                "selector": ".hero",
                "artifacts": {
                    "before": "clip/ref/hero-before.png",
                    "mid": "clip/ref/hero-mid.png",
                    "after": "clip/ref/hero-after.png",
                    "replayTrackManifest": "clip/ref/hero-replay-track.manifest.json",
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

    assert proc.returncode == 1, f"unpaired replay manifest must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["missingArtifacts"][0]["state"] == "replayTrack"
    assert artifact["missingArtifacts"][0]["reason"] == "missing paired replay artifact"


def test_capture_artifact_inventory_rejects_unsafe_replay_artifact_path(tmp_path: Path) -> None:
    """Replay artifacts must stay under the ref dir."""
    ref = tmp_path / "ref"
    (ref / "clip" / "ref").mkdir(parents=True)
    for name in ("hero-before.png", "hero-mid.png", "hero-after.png", "hero-replay-track.manifest.json"):
        path = ref / "clip" / "ref" / name
        if name.endswith(".png"):
            _write_png(path)
        else:
            path.write_text("{}")
    (ref / "regions.json").write_text(json.dumps({
        "scroll": [
            {
                "name": "hero",
                "triggerType": "scroll-driven",
                "selector": ".hero",
                "artifacts": {
                    "before": "clip/ref/hero-before.png",
                    "mid": "clip/ref/hero-mid.png",
                    "after": "clip/ref/hero-after.png",
                    "replayTrack": "../hero-replay-track.json",
                    "replayTrackManifest": "clip/ref/hero-replay-track.manifest.json",
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

    assert proc.returncode == 1, f"unsafe replay artifact path must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["missingArtifacts"][0]["state"] == "replayTrack"
    assert artifact["missingArtifacts"][0]["reason"] == "artifact path must be relative under ref-dir"


def test_capture_artifact_inventory_rejects_missing_replay_artifact_file(tmp_path: Path) -> None:
    """Replay artifacts must point at existing files."""
    ref = tmp_path / "ref"
    (ref / "clip" / "ref").mkdir(parents=True)
    for name in ("hero-before.png", "hero-mid.png", "hero-after.png", "hero-replay-track.manifest.json"):
        path = ref / "clip" / "ref" / name
        if name.endswith(".png"):
            _write_png(path)
        else:
            path.write_text("{}")
    (ref / "regions.json").write_text(json.dumps({
        "scroll": [
            {
                "name": "hero",
                "triggerType": "scroll-driven",
                "selector": ".hero",
                "artifacts": {
                    "before": "clip/ref/hero-before.png",
                    "mid": "clip/ref/hero-mid.png",
                    "after": "clip/ref/hero-after.png",
                    "replayTrack": "clip/ref/hero-replay-track.json",
                    "replayTrackManifest": "clip/ref/hero-replay-track.manifest.json",
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

    assert proc.returncode == 1, f"missing replay artifact file must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["missingArtifacts"][0]["state"] == "replayTrack"
    assert artifact["missingArtifacts"][0]["reason"] == "artifact file missing"


def test_capture_artifact_inventory_rejects_empty_replay_artifact_file(tmp_path: Path) -> None:
    """Replay artifacts must point at nonempty files."""
    ref = tmp_path / "ref"
    (ref / "clip" / "ref").mkdir(parents=True)
    for name in ("hero-before.png", "hero-mid.png", "hero-after.png"):
        _write_png(ref / "clip" / "ref" / name)
    (ref / "clip" / "ref" / "hero-replay-track.json").write_text("")
    (ref / "clip" / "ref" / "hero-replay-track.manifest.json").write_text("{}")
    (ref / "regions.json").write_text(json.dumps({
        "scroll": [
            {
                "name": "hero",
                "triggerType": "scroll-driven",
                "selector": ".hero",
                "artifacts": {
                    "before": "clip/ref/hero-before.png",
                    "mid": "clip/ref/hero-mid.png",
                    "after": "clip/ref/hero-after.png",
                    "replayTrack": "clip/ref/hero-replay-track.json",
                    "replayTrackManifest": "clip/ref/hero-replay-track.manifest.json",
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

    assert proc.returncode == 1, f"empty replay artifact file must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["missingArtifacts"][0]["state"] == "replayTrack"
    assert artifact["missingArtifacts"][0]["reason"] == "artifact file empty"


def test_capture_artifact_inventory_accepts_paired_replay_artifacts(tmp_path: Path) -> None:
    """Paired replay artifacts are accepted when both files exist."""
    ref = tmp_path / "ref"
    (ref / "clip" / "ref").mkdir(parents=True)
    for name in ("hero-before.png", "hero-mid.png", "hero-after.png"):
        _write_png(ref / "clip" / "ref" / name)
    (ref / "clip" / "ref" / "hero-replay-track.json").write_text("{}")
    (ref / "clip" / "ref" / "hero-replay-track.manifest.json").write_text("{}")
    (ref / "regions.json").write_text(json.dumps({
        "scroll": [
            {
                "name": "hero",
                "triggerType": "scroll-driven",
                "selector": ".hero",
                "artifacts": {
                    "before": "clip/ref/hero-before.png",
                    "mid": "clip/ref/hero-mid.png",
                    "after": "clip/ref/hero-after.png",
                    "replayTrack": "clip/ref/hero-replay-track.json",
                    "replayTrackManifest": "clip/ref/hero-replay-track.manifest.json",
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

    assert proc.returncode == 0, f"paired replay artifacts must pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "capture-artifact-inventory.json").read_text())
    assert artifact["status"] == "pass"
    checked = {(item["state"], item["path"]) for item in artifact["checkedArtifacts"]}
    assert ("replayTrack", "clip/ref/hero-replay-track.json") in checked
    assert ("replayTrackManifest", "clip/ref/hero-replay-track.manifest.json") in checked


def _run_inventory(ref: Path) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    script = ROOT / "skills" / "visual-debug" / "scripts" / "capture-artifact-inventory-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    payload = json.loads((ref / "capture-artifact-inventory.json").read_text())
    return proc, payload


def test_capture_artifact_inventory_rejects_nondecodable_raster(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    (ref / "clip" / "ref").mkdir(parents=True)
    (ref / "clip" / "ref" / "idle.png").write_bytes(b"not really a png")
    _write_png(ref / "clip" / "ref" / "active.png")
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{
            "name": "cta", "triggerType": "css-hover",
            "artifacts": {"idle": "clip/ref/idle.png", "active": "clip/ref/active.png"},
        }]
    }))

    proc, payload = _run_inventory(ref)

    assert proc.returncode == 1
    assert any("not decodable" in item["reason"] for item in payload["missingArtifacts"])


def test_capture_artifact_inventory_rejects_blank_raster(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    (ref / "clip" / "ref").mkdir(parents=True)
    _write_png(ref / "clip" / "ref" / "idle.png", seed=1, uniform=True)
    _write_png(ref / "clip" / "ref" / "active.png", seed=2)
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{
            "name": "cta", "triggerType": "css-hover",
            "artifacts": {"idle": "clip/ref/idle.png", "active": "clip/ref/active.png"},
        }]
    }))

    proc, payload = _run_inventory(ref)

    assert proc.returncode == 1
    assert any(item["reason"] == "artifact raster is blank" for item in payload["missingArtifacts"])


def test_capture_artifact_inventory_rejects_reused_state_path(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    (ref / "clip" / "ref").mkdir(parents=True)
    _write_png(ref / "clip" / "ref" / "cta.png")
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{
            "name": "cta", "triggerType": "css-hover",
            "artifacts": {"idle": "clip/ref/cta.png", "active": "clip/ref/cta.png"},
        }]
    }))

    proc, payload = _run_inventory(ref)

    assert proc.returncode == 1
    assert any(item["reason"] == "artifact path reused across required states" for item in payload["missingArtifacts"])


def test_capture_artifact_inventory_rejects_identical_hover_state_files(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    (ref / "clip" / "ref").mkdir(parents=True)
    _write_png(ref / "clip" / "ref" / "idle.png", seed=5)
    _write_png(ref / "clip" / "ref" / "active.png", seed=5)
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{
            "name": "cta", "triggerType": "css-hover",
            "artifacts": {"idle": "clip/ref/idle.png", "active": "clip/ref/active.png"},
        }]
    }))

    proc, payload = _run_inventory(ref)

    assert proc.returncode == 1
    assert any(item["reason"] == "required state rasters are identical" for item in payload["missingArtifacts"])


def test_capture_artifact_inventory_rejects_identical_intersection_states(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    (ref / "clip" / "ref").mkdir(parents=True)
    _write_png(ref / "clip" / "ref" / "before.png", seed=6)
    _write_png(ref / "clip" / "ref" / "after.png", seed=6)
    (ref / "regions.json").write_text(json.dumps({
        "scroll": [{
            "name": "reveal", "triggerType": "intersection",
            "artifacts": {"before": "clip/ref/before.png", "after": "clip/ref/after.png"},
        }]
    }))

    proc, payload = _run_inventory(ref)

    assert proc.returncode == 1
    assert any(item["reason"] == "required state rasters are identical" for item in payload["missingArtifacts"])


def test_capture_artifact_inventory_rejects_identical_click_cycle_states(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    (ref / "clip" / "ref").mkdir(parents=True)
    _write_png(ref / "clip" / "ref" / "state-0.png", seed=7)
    _write_png(ref / "clip" / "ref" / "state-1.png", seed=7)
    (ref / "regions.json").write_text(json.dumps({
        "click": [{
            "name": "tabs", "triggerType": "click-cycle", "stateCount": 2,
            "artifacts": {
                "state-0": "clip/ref/state-0.png",
                "state-1": "clip/ref/state-1.png",
            },
        }]
    }))

    proc, payload = _run_inventory(ref)

    assert proc.returncode == 1
    assert any(item["reason"] == "required state rasters are identical" for item in payload["missingArtifacts"])


def test_capture_artifact_inventory_allows_scroll_mid_matching_before(tmp_path: Path) -> None:
    """A sticky interval may legitimately have a plateau at one sampled stop."""
    ref = tmp_path / "ref"
    (ref / "clip" / "ref").mkdir(parents=True)
    _write_png(ref / "clip" / "ref" / "before.png", seed=11)
    _write_png(ref / "clip" / "ref" / "mid.png", seed=11)
    _write_png(ref / "clip" / "ref" / "after.png", seed=12)
    (ref / "regions.json").write_text(json.dumps({
        "scroll": [{
            "name": "hero", "triggerType": "scroll-driven",
            "artifacts": {
                "before": "clip/ref/before.png",
                "mid": "clip/ref/mid.png",
                "after": "clip/ref/after.png",
            },
        }]
    }))

    proc, payload = _run_inventory(ref)

    assert proc.returncode == 0, payload
    assert payload["status"] == "pass"


def test_capture_artifact_inventory_rejects_scroll_without_endpoint_change(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    (ref / "clip" / "ref").mkdir(parents=True)
    _write_png(ref / "clip" / "ref" / "before.png", seed=13)
    _write_png(ref / "clip" / "ref" / "mid.png", seed=14)
    _write_png(ref / "clip" / "ref" / "after.png", seed=13)
    (ref / "regions.json").write_text(json.dumps({
        "scroll": [{
            "name": "hero", "triggerType": "scroll-driven",
            "artifacts": {
                "before": "clip/ref/before.png",
                "mid": "clip/ref/mid.png",
                "after": "clip/ref/after.png",
            },
        }]
    }))

    proc, payload = _run_inventory(ref)

    assert proc.returncode == 1
    assert any(item["reason"] == "scroll endpoint rasters are identical" for item in payload["missingArtifacts"])
