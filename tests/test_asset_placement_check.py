from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


ASSET_PLACEMENT_SCRIPT = (
    ROOT / "skills" / "visual-debug" / "scripts" / "asset-placement-check.sh"
)


def test_asset_placement_shell_wrapper_avoids_python_heredoc() -> None:
    """Keep the large Python program out of Bash 5.1+ pipe-backed heredocs."""
    shell = ASSET_PLACEMENT_SCRIPT.read_text(encoding="utf-8")
    helper = ASSET_PLACEMENT_SCRIPT.with_name("asset_placement_check.py")

    assert helper.is_file()
    assert "<<" not in shell
    assert "python3 -" not in shell
    assert (
        'python3 "$SCRIPT_DIR/asset_placement_check.py" "$REF_DIR" "$IMPL_ARG" "$OUT"'
        in shell
    )


def test_asset_placement_completes_on_current_bash_without_compat(
    tmp_path: Path,
) -> None:
    """The wrapper must complete with the current default Bash and no compat state."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    impl.mkdir()
    env = os.environ.copy()
    env.pop("BASH_COMPAT", None)
    bash = shutil.which("bash")
    assert bash is not None

    proc = subprocess.run(
        [bash, str(ASSET_PLACEMENT_SCRIPT), str(ref), str(impl)],
        capture_output=True,
        env=env,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "asset-placement.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "skip"


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
        timeout=120,
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
        timeout=120,
    )

    assert proc.returncode == 0, f"section-local asset reference must pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["checked"] == 1


def test_asset_placement_prefers_flow_section_over_overlapping_fixed_offcanvas(
    tmp_path: Path,
) -> None:
    """A viewport-sized fixed menu must not steal assets from content beneath it."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "components").mkdir(parents=True)
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"src": "https://cdn.example.com/assets/hero-banner.webp", "top": 184}
    ]))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [
            {"index": 0, "id": "mobile-nav", "top": 0, "height": 900, "position": "fixed"},
            {"index": 1, "id": "main", "top": 100, "height": 5200, "position": "relative"},
        ]
    }))
    (ref / "component-map.json").write_text(json.dumps({
        "sections": [
            {"index": 0, "file": "src/components/MobileNav.tsx"},
            {"index": 1, "file": "src/components/Main.tsx"},
        ]
    }))
    (impl / "src" / "components" / "MobileNav.tsx").write_text(
        "export function MobileNav(){return <nav />}\n",
        encoding="utf-8",
    )
    (impl / "src" / "components" / "Main.tsx").write_text(
        'export function Main(){return <img src="/assets/hero-banner.webp" />}\n',
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(ASSET_PLACEMENT_SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["checked"] == 1


def test_asset_placement_keeps_fixed_section_when_it_is_the_only_match(
    tmp_path: Path,
) -> None:
    """Fixed/sticky sections remain valid placement targets when no flow section overlaps."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "components").mkdir(parents=True)
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"src": "https://cdn.example.com/assets/nav-logo.svg", "top": 24}
    ]))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [
            {"index": 0, "id": "header", "top": 0, "height": 64, "position": "fixed"},
            {"index": 1, "id": "main", "top": 100, "height": 900, "position": "relative"},
        ]
    }))
    (ref / "component-map.json").write_text(json.dumps({
        "sections": [
            {"index": 0, "file": "src/components/Header.tsx"},
            {"index": 1, "file": "src/components/Main.tsx"},
        ]
    }))
    (impl / "src" / "components" / "Header.tsx").write_text(
        'export function Header(){return <img src="/assets/nav-logo.svg" />}\n',
        encoding="utf-8",
    )
    (impl / "src" / "components" / "Main.tsx").write_text(
        "export function Main(){return <main />}\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(ASSET_PLACEMENT_SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["checked"] == 1


def test_asset_placement_prefers_more_specific_overlapping_flow_section(
    tmp_path: Path,
) -> None:
    """Overlapping flow sections use the narrower range, independent of input order."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "components").mkdir(parents=True)
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"src": "https://cdn.example.com/assets/card.webp", "top": 1250}
    ]))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [
            {"index": 0, "id": "main", "top": 100, "height": 5200, "position": "relative"},
            {"index": 1, "id": "cards", "top": 1000, "height": 900, "position": "static"},
        ]
    }))
    (ref / "component-map.json").write_text(json.dumps({
        "sections": [
            {"index": 0, "file": "src/components/Main.tsx"},
            {"index": 1, "file": "src/components/Cards.tsx"},
        ]
    }))
    (impl / "src" / "components" / "Main.tsx").write_text(
        "export function Main(){return <main />}\n",
        encoding="utf-8",
    )
    (impl / "src" / "components" / "Cards.tsx").write_text(
        'export function Cards(){return <img src="/assets/card.webp" />}\n',
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(ASSET_PLACEMENT_SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
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
        timeout=120,
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
        timeout=120,
    )

    assert proc.returncode == 1, f"sectionId should infer Cards.tsx and fail missing asset: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["checked"] == 1
    assert artifact["missingPlacements"][0]["componentFile"] == "src/components/sections/Cards.tsx"


def test_asset_placement_accepts_assets_referenced_through_named_import(tmp_path: Path) -> None:
    """Section components often reference section assets through a local registry import."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "components").mkdir(parents=True)
    (impl / "src" / "data").mkdir(parents=True)
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"src": "https://cdn.example.com/assets/pyramid.png", "top": 1250}
    ]))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [
            {"index": 1, "id": "cards", "y": 1000, "height": 900},
        ]
    }))
    (ref / "component-map.json").write_text(json.dumps({
        "sections": [
            {"index": 1, "file": "src/components/Cards.tsx"},
        ]
    }))
    (impl / "src" / "data" / "assets.ts").write_text(
        'export const cardImages = ["/assets/pyramid.png"];\n'
        'export const unrelatedImages = ["/assets/decoy.png"];\n',
        encoding="utf-8",
    )
    (impl / "src" / "components" / "Cards.tsx").write_text(
        'import { cardImages } from "../data/assets";\n'
        "export function Cards(){return <img src={cardImages[0]} />}\n",
        encoding="utf-8",
    )

    script = ROOT / "skills" / "visual-debug" / "scripts" / "asset-placement-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, f"named import registry asset should pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "pass"


def test_asset_placement_does_not_accept_unimported_registry_assets(tmp_path: Path) -> None:
    """A central registry is not enough unless the mapped component imports the matching symbol."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "components").mkdir(parents=True)
    (impl / "src" / "data").mkdir(parents=True)
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"src": "https://cdn.example.com/assets/pyramid.png", "top": 1250}
    ]))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [
            {"index": 1, "id": "cards", "y": 1000, "height": 900},
        ]
    }))
    (ref / "component-map.json").write_text(json.dumps({
        "sections": [
            {"index": 1, "file": "src/components/Cards.tsx"},
        ]
    }))
    (impl / "src" / "data" / "assets.ts").write_text(
        'export const cardImages = ["/assets/pyramid.png"];\n'
        'export const unrelatedImages = ["/assets/decoy.png"];\n',
        encoding="utf-8",
    )
    (impl / "src" / "components" / "Cards.tsx").write_text(
        'import { unrelatedImages } from "../data/assets";\n'
        "export function Cards(){return <img src={unrelatedImages[0]} />}\n",
        encoding="utf-8",
    )

    script = ROOT / "skills" / "visual-debug" / "scripts" / "asset-placement-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 1, f"unimported registry asset should fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "fail"


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
        timeout=120,
        env=env,
    )

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"


def test_asset_placement_accepts_assets_referenced_by_section_scoped_runtime_controller(
    tmp_path: Path,
) -> None:
    """loop-e2e-12 false-positive guard: a sibling runtime controller (a
    setInterval carousel) that targets the section's sourceClass and assigns
    img.src from an asset list counts as valid placement, even though the
    statically-mapped component never imports it. (realfood eatReal case:
    Footer.tsx is a static scaffold; EatRealCarousel.tsx rotates the food list
    into the same .dga_eatReal__hUKXz cards at runtime.)"""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "components").mkdir(parents=True)
    (impl / "src" / "lib").mkdir(parents=True)
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"tag": "img", "src": "https://realfood.gov/images/pyramid/bowl-oats.webp", "top": 1250}
    ]))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [{"index": 1, "id": "footer", "top": 1000, "height": 900}]
    }))
    # component-map has no `file` key — only componentName + sourceClass (this
    # site's shape); the gate resolves Footer.tsx via inferred_component_files.
    (ref / "component-map.json").write_text(json.dumps({
        "sections": [
            {"sectionId": "footer", "componentName": "Footer", "sourceClass": "dga_eatReal__hUKXz"}
        ]
    }))
    # Mapped component: static scaffold, references a DIFFERENT (capture-instant) frame.
    (impl / "src" / "components" / "Footer.tsx").write_text(
        'export default function Footer(){return (<section className="dga_eatReal__hUKXz">'
        '<img src="/images/pyramid/salmon.webp" /></section>);}\n',
        encoding="utf-8",
    )
    # Sibling runtime controller: targets the section class + assigns img.src from a list.
    (impl / "src" / "lib" / "EatRealCarousel.tsx").write_text(
        "export default function EatRealCarousel(){\n"
        '  const FOODS = ["/images/pyramid/bowl-oats.webp", "/images/pyramid/eggs.webp"];\n'
        "  const cards = document.querySelectorAll('.dga_eatReal__hUKXz .dga_card__W4f_X');\n"
        "  cards.forEach((c,i)=>{ const img=c.querySelector('img'); if(img) img.src = FOODS[i]; });\n"
        "  return null;\n"
        "}\n",
        encoding="utf-8",
    )

    script = ROOT / "skills" / "visual-debug" / "scripts" / "asset-placement-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, (
        f"section-scoped runtime controller should satisfy placement: {proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["checked"] == 1


def test_asset_placement_still_fails_when_no_component_or_controller_references_asset(
    tmp_path: Path,
) -> None:
    """Blindness guard: the controller fold must NOT blind the gate. An asset
    referenced by no static component AND no section-scoped controller still
    fails — a controller for a DIFFERENT section's class does not count (proves
    the fold is section-scoped, not a global src sweep)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "components").mkdir(parents=True)
    (impl / "src" / "lib").mkdir(parents=True)
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"tag": "img", "src": "https://realfood.gov/images/pyramid/bowl-oats.webp", "top": 1250}
    ]))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [{"index": 1, "id": "footer", "top": 1000, "height": 900}]
    }))
    (ref / "component-map.json").write_text(json.dumps({
        "sections": [
            {"sectionId": "footer", "componentName": "Footer", "sourceClass": "dga_eatReal__hUKXz"}
        ]
    }))
    # Mapped component does not reference the asset.
    (impl / "src" / "components" / "Footer.tsx").write_text(
        'export default function Footer(){return (<section className="dga_eatReal__hUKXz" />);}\n',
        encoding="utf-8",
    )
    # A controller exists but targets a DIFFERENT section class (and even lists the
    # same asset) — it must NOT satisfy the footer asset.
    (impl / "src" / "lib" / "PyramidCarousel.tsx").write_text(
        "export default function PyramidCarousel(){\n"
        '  const FOODS = ["/images/pyramid/bowl-oats.webp"];\n'
        "  const cards = document.querySelectorAll('.dga_pyramid__ZZZ .dga_card__W4f_X');\n"
        "  cards.forEach((c,i)=>{ const img=c.querySelector('img'); if(img) img.src = FOODS[i]; });\n"
        "  return null;\n"
        "}\n",
        encoding="utf-8",
    )

    script = ROOT / "skills" / "visual-debug" / "scripts" / "asset-placement-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 1, (
        f"asset with no static ref and no section-scoped controller must fail: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["missingPlacements"][0]["needles"][0] == "bowl-oats.webp"


def test_asset_placement_rejects_central_manifest_crediting_unrelated_asset(
    tmp_path: Path,
) -> None:
    """P0 hole #1 (cross-file concatenation leak): a central manifest/registry
    that names THIS section's sourceClass in one region AND, independently, lists
    a DIFFERENT section's asset in another region must NOT blanket-credit that
    asset. The section-class reference and the asset needle live in different
    regions of the same file with no provenance link between them — the genuine
    controller fix requires them to CO-OCCUR (same block), not merely the same
    file. Without that, a single global manifest credits every asset to every
    section that the manifest happens to mention."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "components").mkdir(parents=True)
    (impl / "src" / "lib").mkdir(parents=True)
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"tag": "img", "src": "https://realfood.gov/images/pyramid/bowl-oats.webp", "top": 1250}
    ]))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [{"index": 1, "id": "footer", "top": 1000, "height": 900}]
    }))
    (ref / "component-map.json").write_text(json.dumps({
        "sections": [
            {"sectionId": "footer", "componentName": "Footer", "sourceClass": "dga_eatReal__hUKXz"}
        ]
    }))
    # Mapped component does not reference the asset.
    (impl / "src" / "components" / "Footer.tsx").write_text(
        'export default function Footer(){return (<section className="dga_eatReal__hUKXz" />);}\n',
        encoding="utf-8",
    )
    # Central manifest: names the footer's sourceClass (for an UNRELATED wiring) in
    # one block, and lists bowl-oats.webp under a DIFFERENT section in another block.
    # The two never co-occur — no provenance link ties bowl-oats.webp to the footer.
    (impl / "src" / "lib" / "registry.ts").write_text(
        "export const SECTION_CLASSES = {\n"
        '  footer: "dga_eatReal__hUKXz",\n'
        '  hero: "dga_hero__AAA",\n'
        "};\n"
        "\n"
        "// ---- entirely separate region, wires the hero (not the footer) ----\n"
        "export const HERO_IMAGES = [\n"
        '  "/images/pyramid/bowl-oats.webp",\n'
        "];\n",
        encoding="utf-8",
    )

    script = ROOT / "skills" / "visual-debug" / "scripts" / "asset-placement-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 1, (
        f"central manifest naming the section class elsewhere must NOT credit an "
        f"unrelated asset: {proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["missingPlacements"][0]["needles"][0] == "bowl-oats.webp"


def test_asset_placement_rejects_asset_wired_to_wrong_cards_in_same_class_region(
    tmp_path: Path,
) -> None:
    """P0 hole #2 (mis-attribution within a same-class file): an asset wired to
    the WRONG cards is the exact defect this gate exists to catch. Here ONE file
    holds two controller blocks: the block that targets the footer's sourceClass
    assigns only decoy.webp, while a SEPARATE block (targeting a different class)
    lists the footer's actual asset bowl-oats.webp. The asset and the footer class
    live in the same FILE but not the same BLOCK — bowl-oats.webp is never placed
    into the footer's cards. The footer asset must FAIL: co-occurrence in a file
    is not provenance; only co-occurrence in the same class-scoped block is."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "components").mkdir(parents=True)
    (impl / "src" / "lib").mkdir(parents=True)
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"tag": "img", "src": "https://realfood.gov/images/pyramid/bowl-oats.webp", "top": 1250}
    ]))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [{"index": 1, "id": "footer", "top": 1000, "height": 900}]
    }))
    (ref / "component-map.json").write_text(json.dumps({
        "sections": [
            {"sectionId": "footer", "componentName": "Footer", "sourceClass": "dga_eatReal__hUKXz"}
        ]
    }))
    (impl / "src" / "components" / "Footer.tsx").write_text(
        'export default function Footer(){return (<section className="dga_eatReal__hUKXz" />);}\n',
        encoding="utf-8",
    )
    # One file, two controller blocks. The block that targets the footer's class
    # assigns only decoy.webp. A SEPARATE block targets a DIFFERENT class but lists
    # the footer's actual asset bowl-oats.webp — mis-attribution: bowl-oats.webp is
    # never placed into the footer's .dga_eatReal__hUKXz cards.
    (impl / "src" / "lib" / "EatRealCarousel.tsx").write_text(
        "export function footerCarousel(){\n"
        '  const FOODS = ["/images/pyramid/decoy.webp"];\n'
        "  const cards = document.querySelectorAll('.dga_eatReal__hUKXz .dga_card__W4f_X');\n"
        "  cards.forEach((c,i)=>{ const img=c.querySelector('img'); if(img) img.src = FOODS[i]; });\n"
        "}\n"
        "\n"
        "export function pyramidCarousel(){\n"
        '  const FOODS = ["/images/pyramid/bowl-oats.webp"];\n'
        "  const cards = document.querySelectorAll('.dga_pyramid__ZZZ .dga_card__W4f_X');\n"
        "  cards.forEach((c,i)=>{ const img=c.querySelector('img'); if(img) img.src = FOODS[i]; });\n"
        "}\n",
        encoding="utf-8",
    )

    script = ROOT / "skills" / "visual-debug" / "scripts" / "asset-placement-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 1, (
        f"asset wired to the wrong cards must fail: {proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["missingPlacements"][0]["needles"][0] == "bowl-oats.webp"


def test_asset_placement_does_not_accept_unimported_registry_assets_with_source_class(
    tmp_path: Path,
) -> None:
    """P0 live blind spot (realfood): the unimported-registry guard above stays
    green only because its fixture OMITS sourceClass. The real target carries a
    sourceClass, which activates the controller fold. This mirrors that guard but
    WITH a sourceClass on the section, the asset living only in an UNIMPORTED
    export, and a comment in the registry that mentions the section class. A bare
    class mention next to an asset export is not provenance — the mapped component
    never imports cardImages, so the asset is not placed. Must FAIL."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "components").mkdir(parents=True)
    (impl / "src" / "data").mkdir(parents=True)
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"src": "https://cdn.example.com/assets/pyramid.png", "top": 1250}
    ]))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [
            {"index": 1, "id": "cards", "y": 1000, "height": 900},
        ]
    }))
    (ref / "component-map.json").write_text(json.dumps({
        "sections": [
            {"index": 1, "file": "src/components/Cards.tsx", "sourceClass": "dga_cards__hUKXz"},
        ]
    }))
    # Registry: comment names the section class right beside the asset export, but
    # nothing imports cardImages — a bare class mention is not a wiring.
    (impl / "src" / "data" / "assets.ts").write_text(
        '// images for the dga_cards__hUKXz section\n'
        'export const cardImages = ["/assets/pyramid.png"];\n'
        'export const unrelatedImages = ["/assets/decoy.png"];\n',
        encoding="utf-8",
    )
    (impl / "src" / "components" / "Cards.tsx").write_text(
        'import { unrelatedImages } from "../data/assets";\n'
        'export function Cards(){return <section className="dga_cards__hUKXz">'
        "<img src={unrelatedImages[0]} /></section>}\n",
        encoding="utf-8",
    )

    script = ROOT / "skills" / "visual-debug" / "scripts" / "asset-placement-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 1, (
        f"unimported registry asset with sourceClass present should fail: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "fail"


def test_asset_placement_rejects_string_map_value_naming_class_in_same_block(
    tmp_path: Path,
) -> None:
    """Codex HIGH #1 (lexical): a registry/string-map block that names the
    section's sourceClass as a STRING-MAP VALUE (`footer: "dga_eatReal__hUKXz"`)
    AND, in the SAME brace-balanced block, lists the asset must NOT credit it.
    The class is not WIRED in real code — it is a quoted map value, not a CSS
    selector / className / classList target. Co-occurrence in a block where the
    class appears only as a string literal is not provenance."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "components").mkdir(parents=True)
    (impl / "src" / "lib").mkdir(parents=True)
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"tag": "img", "src": "https://realfood.gov/images/pyramid/bowl-oats.webp", "top": 1250}
    ]))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [{"index": 1, "id": "footer", "top": 1000, "height": 900}]
    }))
    (ref / "component-map.json").write_text(json.dumps({
        "sections": [
            {"sectionId": "footer", "componentName": "Footer", "sourceClass": "dga_eatReal__hUKXz"}
        ]
    }))
    (impl / "src" / "components" / "Footer.tsx").write_text(
        'export default function Footer(){return (<section className="dga_eatReal__hUKXz" />);}\n',
        encoding="utf-8",
    )
    # The class name appears only as a string-map VALUE, and the asset is listed in
    # the SAME object literal block. A raw co-occurrence + a regex `.find(cls)`
    # would credit this; lexical class-wiring detection must reject it because the
    # class is inside a string literal, not a DOM-target wiring.
    (impl / "src" / "lib" / "registry.ts").write_text(
        "export const SECTION_REGISTRY = {\n"
        '  footer: { sectionClass: "dga_eatReal__hUKXz", images: ["/images/pyramid/bowl-oats.webp"] },\n'
        "};\n",
        encoding="utf-8",
    )

    script = ROOT / "skills" / "visual-debug" / "scripts" / "asset-placement-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 1, (
        f"string-map value naming the class must not credit a co-located asset: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["missingPlacements"][0]["needles"][0] == "bowl-oats.webp"


def test_asset_placement_rejects_brace_inside_string_merging_blocks_minified(
    tmp_path: Path,
) -> None:
    """Codex HIGH #2 (lexical brace matching): minified single-line code where a
    string literal contains an unbalanced `{` would make a raw-char brace matcher
    merge two distinct blocks into one whole-file block, reintroducing whole-file
    credit. Here the footer's real wiring block assigns only decoy.webp; a later,
    separate block (different class) lists bowl-oats.webp, and BETWEEN them a
    string literal holds a stray `{`. With lexical brace matching (braces inside
    strings ignored) the two blocks stay distinct → footer asset must FAIL."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "components").mkdir(parents=True)
    (impl / "src" / "lib").mkdir(parents=True)
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"tag": "img", "src": "https://realfood.gov/images/pyramid/bowl-oats.webp", "top": 1250}
    ]))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [{"index": 1, "id": "footer", "top": 1000, "height": 900}]
    }))
    (ref / "component-map.json").write_text(json.dumps({
        "sections": [
            {"sectionId": "footer", "componentName": "Footer", "sourceClass": "dga_eatReal__hUKXz"}
        ]
    }))
    (impl / "src" / "components" / "Footer.tsx").write_text(
        'export default function Footer(){return (<section className="dga_eatReal__hUKXz" />);}\n',
        encoding="utf-8",
    )
    # Minified one-liner. The footer block (querySelectorAll('.dga_eatReal__hUKXz'))
    # assigns only decoy.webp. A string literal carries a stray unbalanced "{" that a
    # raw-char brace matcher would treat as opening a block, swallowing the later
    # pyramid block (which lists bowl-oats.webp) into the footer's "same block".
    (impl / "src" / "lib" / "bundle.min.ts").write_text(
        'function f(){var a=document.querySelectorAll(".dga_eatReal__hUKXz");'
        'var t="prefix {unbalanced";var F=["/images/pyramid/decoy.webp"];a.forEach(function(c,i){var m=c.querySelector("img");if(m)m.src=F[i];});}'
        'function g(){var b=document.querySelectorAll(".dga_pyramid__ZZZ");'
        'var G=["/images/pyramid/bowl-oats.webp"];b.forEach(function(c,i){var m=c.querySelector("img");if(m)m.src=G[i];});}\n',
        encoding="utf-8",
    )

    script = ROOT / "skills" / "visual-debug" / "scripts" / "asset-placement-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 1, (
        f"stray brace inside a string must not merge distinct blocks: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["missingPlacements"][0]["needles"][0] == "bowl-oats.webp"


def test_asset_placement_passes_multiclass_sourceclass_with_scoped_token(
    tmp_path: Path,
) -> None:
    """Codex MEDIUM over-tighten (a): a multi-class sourceClass like
    "section dga_eatReal__hUKXz" must still match a controller that targets the
    module-scoped token `.dga_eatReal__hUKXz`. scoped_source_class returning the
    whole string false-failed this; splitting on whitespace and matching ANY
    module-scoped token fixes it. Must PASS."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "components").mkdir(parents=True)
    (impl / "src" / "lib").mkdir(parents=True)
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"tag": "img", "src": "https://realfood.gov/images/pyramid/bowl-oats.webp", "top": 1250}
    ]))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [{"index": 1, "id": "footer", "top": 1000, "height": 900}]
    }))
    (ref / "component-map.json").write_text(json.dumps({
        "sections": [
            {"sectionId": "footer", "componentName": "Footer", "sourceClass": "section dga_eatReal__hUKXz"}
        ]
    }))
    (impl / "src" / "components" / "Footer.tsx").write_text(
        'export default function Footer(){return (<section className="dga_eatReal__hUKXz" />);}\n',
        encoding="utf-8",
    )
    (impl / "src" / "lib" / "EatRealCarousel.tsx").write_text(
        "export default function EatRealCarousel(){\n"
        '  const FOODS = ["/images/pyramid/bowl-oats.webp", "/images/pyramid/eggs.webp"];\n'
        "  const cards = document.querySelectorAll('.dga_eatReal__hUKXz .dga_card__W4f_X');\n"
        "  cards.forEach((c,i)=>{ const img=c.querySelector('img'); if(img) img.src = FOODS[i]; });\n"
        "  return null;\n"
        "}\n",
        encoding="utf-8",
    )

    script = ROOT / "skills" / "visual-debug" / "scripts" / "asset-placement-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, (
        f"multi-class sourceClass with a scoped token should match the controller: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["checked"] == 1


def test_asset_placement_passes_url_literal_before_queryselector_wiring(
    tmp_path: Path,
) -> None:
    """Codex MEDIUM over-tighten (b): a faithful controller whose wiring line has
    a "https://" URL literal BEFORE the querySelector('.dga...') call must still
    be recognized as real wiring. A raw `//` find treated the URL's `//` as a
    line comment and wrongly rejected the wiring. Lexical comment detection
    (// only counts outside strings) fixes it. Must PASS."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "components").mkdir(parents=True)
    (impl / "src" / "lib").mkdir(parents=True)
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"tag": "img", "src": "https://realfood.gov/images/pyramid/bowl-oats.webp", "top": 1250}
    ]))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [{"index": 1, "id": "footer", "top": 1000, "height": 900}]
    }))
    (ref / "component-map.json").write_text(json.dumps({
        "sections": [
            {"sectionId": "footer", "componentName": "Footer", "sourceClass": "dga_eatReal__hUKXz"}
        ]
    }))
    (impl / "src" / "components" / "Footer.tsx").write_text(
        'export default function Footer(){return (<section className="dga_eatReal__hUKXz" />);}\n',
        encoding="utf-8",
    )
    # The querySelector wiring line contains a "https://" URL literal BEFORE the
    # selector. A raw `//` find would flag the whole line as a comment and reject
    # the wiring; lexical comment detection must recognize the `//` is inside a string.
    (impl / "src" / "lib" / "EatRealCarousel.tsx").write_text(
        "export default function EatRealCarousel(){\n"
        '  const FOODS = ["https://realfood.gov/images/pyramid/bowl-oats.webp"];\n'
        '  const base = "https://realfood.gov/"; const cards = document.querySelectorAll(base + ".dga_eatReal__hUKXz .dga_card__W4f_X");\n'
        "  cards.forEach((c,i)=>{ const img=c.querySelector('img'); if(img) img.src = FOODS[i]; });\n"
        "  return null;\n"
        "}\n",
        encoding="utf-8",
    )

    script = ROOT / "skills" / "visual-debug" / "scripts" / "asset-placement-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, (
        f"URL literal before querySelector wiring must not be treated as a comment: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["checked"] == 1


def test_asset_placement_rejects_comment_only_reference(tmp_path: Path) -> None:
    """A section asset named only inside a comment is not placement evidence.

    Mirrors the passing section-local case but moves the asset path into a
    comment: the raw `needle in evidence_text` scan would have credited it,
    so the comment-stripping hardening must now fail this."""
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
    # No live token contains the asset stem — the ONLY mention of
    # `/images/pyramid.webp` is the comment, so stripping comments must leave
    # zero placement evidence.
    (impl / "src" / "components" / "Pyramid.tsx").write_text(
        "export function Section(){\n"
        "  // TODO: wire /images/pyramid.webp into this section\n"
        "  return <img alt=\"stone monument\" />\n"
        "}\n",
        encoding="utf-8",
    )

    script = ROOT / "skills" / "visual-debug" / "scripts" / "asset-placement-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 1, f"comment-only reference must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "asset-placement.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["checked"] == 1
    assert artifact["missingPlacements"], artifact
