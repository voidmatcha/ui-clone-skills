from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_asset_placement_fails_when_section_asset_is_only_used_elsewhere(tmp_path: Path) -> None:
    """Global asset usage is insufficient; section-local assets must land in the mapped component."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "components").mkdir(parents=True)
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {
            "tag": "img",
            "src": "https://cdn.example.com/images/pyramid.webp",
            "top": 1250,
            "w": 320,
            "h": 240,
        }
    ]))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [
            {"i": 0, "top": 0, "height": 800, "className": "hero"},
            {"i": 1, "top": 1000, "height": 800, "className": "pyramid"},
        ]
    }))
    (ref / "component-map.json").write_text(json.dumps({
        "sections": [
            {"index": 0, "file": "src/components/Hero.tsx"},
            {"index": 1, "file": "src/components/Pyramid.tsx"},
        ]
    }))
    (impl / "src" / "components" / "Hero.tsx").write_text(
        'export function Hero(){return <img src="/images/pyramid.webp" />}\n',
        encoding="utf-8",
    )
    (impl / "src" / "components" / "Pyramid.tsx").write_text(
        "export function Pyramid(){return <section />}\n",
        encoding="utf-8",
    )

    script = ROOT / "skills" / "visual-debug" / "scripts" / "asset-placement-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 1, f"asset in wrong component must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["missingPlacements"][0]["componentFile"] == "src/components/Pyramid.tsx"


def test_asset_placement_passes_when_section_component_references_asset(tmp_path: Path) -> None:
    """A section-mapped component that references its own asset passes."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "components").mkdir(parents=True)
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"tag": "img", "src": "https://cdn.example.com/images/pyramid.webp", "top": 1250}
    ]))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [
            {"i": 0, "top": 0, "height": 800},
            {"i": 1, "top": 1000, "height": 800},
        ]
    }))
    (ref / "component-map.json").write_text(json.dumps({
        "sections": [
            {"index": 1, "file": "src/components/Pyramid.tsx"},
        ]
    }))
    (impl / "src" / "components" / "Pyramid.tsx").write_text(
        'export function Pyramid(){return <img src="/images/pyramid.webp" />}\n',
        encoding="utf-8",
    )

    script = ROOT / "skills" / "visual-debug" / "scripts" / "asset-placement-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, f"section-local asset reference must pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["checked"] == 1
