from __future__ import annotations

import json
import os
import subprocess
import sys
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


def test_asset_placement_uses_nested_rect_coordinates_and_section_y(tmp_path: Path) -> None:
    """Extractor output may put image coordinates under rect and section starts under y."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "components").mkdir(parents=True)
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps({
        "images": [
            {
                "src": "https://cdn.example.com/assets/card.png?w=800",
                "rect": {"y": 1250, "height": 240, "width": 320},
            }
        ]
    }))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [
            {"index": 0, "id": "hero", "y": 0, "height": 900},
            {"index": 1, "id": "cards", "y": 1000, "height": 900},
        ]
    }))
    (ref / "component-map.json").write_text(json.dumps({
        "sections": [
            {"index": 0, "file": "src/components/Hero.tsx"},
            {"index": 1, "file": "src/components/Cards.tsx"},
        ]
    }))
    (impl / "src" / "components" / "Hero.tsx").write_text(
        'export function Hero(){return <img src="/assets/card.png" />}\n',
        encoding="utf-8",
    )
    (impl / "src" / "components" / "Cards.tsx").write_text(
        "export function Cards(){return <section />}\n",
        encoding="utf-8",
    )

    script = ROOT / "skills" / "visual-debug" / "scripts" / "asset-placement-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 1, f"nested rect coordinates must be checked: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["checked"] == 1
    assert artifact["missingPlacements"][0]["sectionIndex"] == 1


def test_asset_placement_infers_component_file_from_section_id(tmp_path: Path) -> None:
    """component-map may name sections/components without concrete file paths."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "components" / "sections").mkdir(parents=True)
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"src": "https://cdn.example.com/assets/card.png", "top": 1250}
    ]))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [
            {"index": 1, "id": "cards", "y": 1000, "height": 900},
        ]
    }))
    (ref / "component-map.json").write_text(json.dumps({
        "sections": [
            {"sectionId": "cards", "componentName": "CardsSection"},
        ]
    }))
    (impl / "src" / "components" / "sections" / "Cards.tsx").write_text(
        "export function Cards(){return <section />}\n",
        encoding="utf-8",
    )

    script = ROOT / "skills" / "visual-debug" / "scripts" / "asset-placement-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 1, f"sectionId should infer Cards.tsx and fail missing asset: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["checked"] == 1
    assert artifact["missingPlacements"][0]["componentFile"] == "src/components/sections/Cards.tsx"


def test_asset_placement_runs_with_python39_annotation_semantics(tmp_path: Path) -> None:
    """The script calls host python3, so inline Python must not require 3.10+."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "components").mkdir(parents=True)
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"tag": "img", "src": "https://cdn.example.com/images/pyramid.webp", "top": 1250}
    ]))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [
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
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "python3"
    shim.write_text(
        "#!" + sys.executable + "\n"
        "import subprocess\n"
        "import sys\n"
        "source = sys.stdin.read()\n"
        "future_lines = source.splitlines()[:10]\n"
        "if '| None' in source and 'from __future__ import annotations' not in future_lines:\n"
        "    sys.stderr.write(\"TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'\\n\")\n"
        "    raise SystemExit(1)\n"
        f"proc = subprocess.run([{sys.executable!r}, *sys.argv[1:]], input=source, text=True)\n"
        "raise SystemExit(proc.returncode)\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    env = {**os.environ, "PATH": f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}"}

    script = ROOT / "skills" / "visual-debug" / "scripts" / "asset-placement-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
