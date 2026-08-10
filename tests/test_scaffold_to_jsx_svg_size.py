from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"
TRANSPILER = SCRIPT.parent / "lib" / "scaffold_to_jsx.py"
EXTRACT = ROOT / "skills" / "visual-debug" / "scripts" / "lib" / "extract-dom.js"


def _run(tmp_path: Path, body: dict) -> str:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps(body), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return "".join(
        p.read_text() for p in (impl / "src" / "components").glob("*.tsx"))


def _run_tree(tmp_path: Path, body: dict, *, hash_seed: int) -> dict[str, bytes]:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps(body), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = str(hash_seed)
    env["UI_CLONE_SKIP_ASSET_DOWNLOAD"] = "1"
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return {
        path.relative_to(impl).as_posix(): path.read_bytes()
        for path in sorted((impl / "src").rglob("*"))
        if path.is_file()
    }


def _wrap(*nodes: dict) -> dict:
    return {"tag": "body", "children": [
        {"tag": "section", "class": "hero", "children": list(nodes)}]}


def test_baked_svg_dimensions_are_emitted(tmp_path: Path) -> None:
    """An inline <svg> whose intrinsic size was baked by extraction (Fix 122 —
    getBoundingClientRect width/height on a viewBox-less icon) must be emitted
    with those width/height attrs, so the clone cannot fall back to the CSS
    replaced-element default of 300x150."""
    node = {
        "tag": "svg", "class": "icon", "svg": True,
        "width": "12", "height": "12", "styles": {},
        "children": [{"tag": "path", "svg": True, "d": "M0 0h12v12H0z"}],
    }
    blob = _run(tmp_path, _wrap(node))
    svg_line = next(ln for ln in blob.splitlines() if "<svg" in ln)
    assert 'width="12"' in svg_line, svg_line
    assert 'height="12"' in svg_line, svg_line


def test_svg_scaffold_is_byte_deterministic_across_processes(tmp_path: Path) -> None:
    """Identical captured SVG input must emit a byte-identical source tree."""
    body = _wrap({
        "tag": "svg",
        "class": "icon",
        "svg": True,
        "width": "12",
        "height": "12",
        "viewBox": "0 0 12 12",
        "styles": {},
        "children": [{
            "tag": "path",
            "svg": True,
            "fill": "currentColor",
            "d": "M0 0h12v12H0z",
            "stroke": "currentColor",
            "mask": "url(#mask)",
            "filter": "url(#filter)",
        }],
    })

    first = _run_tree(tmp_path / "first", body, hash_seed=1)
    second = _run_tree(tmp_path / "second", body, hash_seed=3)

    assert any(b"<path" in content for content in first.values())
    assert first == second


def test_extraction_bakes_rendered_size_on_viewboxless_svg() -> None:
    """The extract-dom SVG-size guard is present and correctly gated: it fires
    only for a root <svg> lacking viewBox AND width AND height. Asserted against
    the source so the fix cannot be silently dropped (a browser round-trip is
    covered by the manual end-to-end run, not portable to CI)."""
    src = EXTRACT.read_text(encoding="utf-8")
    # The guard exists and is gated on all three absent conditions.
    guard = re.search(
        r"out\.tag === 'svg' && !out\.viewBox && !out\.width && !out\.height",
        src)
    assert guard, "Fix 122 svg-size guard missing or its gate changed"
    # It reads the rendered box and writes width/height back onto the node.
    assert "getBoundingClientRect()" in src
    assert "out.width = String(w)" in src
    assert "out.height = String(h)" in src


def test_transpiler_passes_svg_width_height_through() -> None:
    """Fix 122 relies on the transpiler emitting svg width/height; guard the
    passthrough set so removing it (which would silently reintroduce the 300x150
    balloon) fails a test."""
    src = TRANSPILER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    assignment = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "SVG_PASSTHROUGH_ATTRS"
            for target in node.targets
        )
    )
    assert isinstance(assignment.value, ast.List | ast.Tuple | ast.Set)
    values = {
        item.value
        for item in assignment.value.elts
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    }
    assert "width" in values and "height" in values, (
        "svg width/height must stay in SVG_PASSTHROUGH_ATTRS for Fix 122")
