from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


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


def _wrap(node: dict) -> dict:
    return {"tag": "body", "children": [
        {"tag": "section", "class": "hero", "children": [node]}]}


def _iframe_line(blob: str, marker: str) -> str:
    return next(ln for ln in blob.splitlines() if marker in ln)


def test_dimensionless_tracking_iframe_collapsed(tmp_path: Path) -> None:
    """An iframe with no width/height attr and no CSS width/height falls back to
    the 300x150 replaced-element default and balloons docH. It is a cross-frame
    tracker (criteo syncframe / ebay devicebind) and must be hidden."""
    node = {"tag": "iframe", "id": "tt",
            "src": "https://devicebind.example.com/tt.html",
            "styles": {"position": "static"}}
    blob = _run(tmp_path, _wrap(node))
    line = _iframe_line(blob, 'id="tt"')
    assert 'display: "none"' in line, line


def test_pixel_tracking_iframe_collapsed(tmp_path: Path) -> None:
    """A 1px tracking-pixel iframe (css width:1px) is invisible — hide it so it
    cannot contribute the ballooned replaced-element height."""
    node = {"tag": "iframe", "id": "epik",
            "src": "https://ct.example.com/ct.html",
            "styles": {"position": "static", "width": "1px", "height": "auto"}}
    blob = _run(tmp_path, _wrap(node))
    line = _iframe_line(blob, 'id="epik"')
    assert 'display: "none"' in line, line


def test_iframe_with_size_attrs_not_collapsed(tmp_path: Path) -> None:
    """A visible content iframe carries explicit width/height attrs — it is a
    real embed (video/map) and must render, not be hidden."""
    node = {"tag": "iframe", "id": "player", "width": "560", "height": "315",
            "src": "https://embed.example.com/v/abc",
            "styles": {"position": "static"}}
    blob = _run(tmp_path, _wrap(node))
    line = _iframe_line(blob, 'id="player"')
    assert 'display: "none"' not in line, line


def test_css_sized_iframe_not_collapsed(tmp_path: Path) -> None:
    """A responsive iframe sized purely via CSS (width:100%, height:400px) is
    real visible content — the px-size gate (>4px / non-px) leaves it alone."""
    node = {"tag": "iframe", "id": "map",
            "src": "https://maps.example.com/embed",
            "styles": {"position": "static", "width": "100%", "height": "400px"}}
    blob = _run(tmp_path, _wrap(node))
    line = _iframe_line(blob, 'id="map"')
    assert 'display: "none"' not in line, line


def test_non_iframe_without_dims_not_collapsed(tmp_path: Path) -> None:
    """The collapse is iframe-only — a plain div with no dimensions must not be
    hidden (it sizes to its content)."""
    node = {"tag": "div", "id": "plain", "styles": {"position": "static"}}
    blob = _run(tmp_path, _wrap(node))
    line = _iframe_line(blob, 'id="plain"')
    assert 'display: "none"' not in line, line
