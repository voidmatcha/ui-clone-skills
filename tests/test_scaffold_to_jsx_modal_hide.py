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


def _modal_node(**style_overrides: str) -> dict:
    """A captured-open ship-to-style lightbox: fixed dialog with a high z-index
    and a dark semi-transparent scrim background."""
    styles = {
        "position": "fixed",
        "z-index": "100000",
        "background-color": "rgba(17, 24, 32, 0.7)",
    }
    styles.update(style_overrides)
    return {
        "tag": "div",
        "class": "lightbox-dialog gh-ship-to__lightbox",
        "role": "dialog",
        "styles": styles,
        "children": [
            {"tag": "div", "class": "lightbox-dialog__window",
             "styles": {"position": "static",
                        "background-color": "rgb(255, 255, 255)"},
             "text": "Ship to"},
        ],
    }


def _wrap(node: dict) -> dict:
    return {"tag": "body", "children": [
        {"tag": "section", "class": "hero", "children": [node]}]}


def test_captured_open_modal_gets_display_none(tmp_path: Path) -> None:
    """A modal/lightbox captured OPEN (fixed + role=dialog + high z-index + a
    semi-transparent scrim bg) must be emitted with display:none so its
    full-viewport scrim does not cover the clone and dominate every diff."""
    blob = _run(tmp_path, _wrap(_modal_node()))
    assert 'role="dialog"' in blob
    dialog_line = next(
        ln for ln in blob.splitlines() if 'role="dialog"' in ln)
    assert 'display: "none"' in dialog_line, dialog_line


def test_opaque_dialog_window_not_hidden(tmp_path: Path) -> None:
    """The opaque white dialog card (rgb, alpha 1) is not a scrim — it must NOT
    be independently hidden (it is hidden only via its scrim parent)."""
    blob = _run(tmp_path, _wrap(_modal_node()))
    window_line = next(
        ln for ln in blob.splitlines()
        if "lightbox-dialog__window" in ln)
    assert 'display: "none"' not in window_line, window_line


def test_hero_scrim_without_dialog_role_not_hidden(tmp_path: Path) -> None:
    """A fixed dark semi-transparent hero overlay with NO dialog role/class is a
    design scrim, not a modal — it must render visible."""
    node = {
        "tag": "div",
        "class": "hero-overlay",
        "styles": {
            "position": "fixed",
            "z-index": "2",
            "background-color": "rgba(0, 0, 0, 0.4)",
        },
    }
    blob = _run(tmp_path, _wrap(node))
    line = next(ln for ln in blob.splitlines() if "hero-overlay" in ln)
    assert 'display: "none"' not in line, line


def test_non_fixed_dialog_not_hidden(tmp_path: Path) -> None:
    """An in-flow / absolutely-positioned dialog is not a fixed scrim — leave
    it alone (only position:fixed captured-open overlays are hidden)."""
    blob = _run(tmp_path, _wrap(_modal_node(position="absolute")))
    dialog_line = next(
        ln for ln in blob.splitlines() if 'role="dialog"' in ln)
    assert 'display: "none"' not in dialog_line, dialog_line
