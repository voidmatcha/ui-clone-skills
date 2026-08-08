"""Root background un-bake: the transpiler must not bake the CAPTURED root
background as an `html,body{background-color:… !important}` override when the
imported ref CSS already sets body/html background.

Real defect (eBay Playbook): the ref's light/dark theme resolves body bg via
`body,html{background-color:var(--color-background-primary)}` (→ white in the
light default). The capture snapshotted the root while dark (rgb(0,0,0)); the
transpiler baked that as an `!important` override which beat the correct forensic
CSS and rendered the WHOLE clone black. When the ref CSS covers the page base,
trust it; only bake the override when the ref leaves body/html bg undefined (the
realfood dark-margin case it was written for).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def _emit(tmp_path: Path, body_styles: dict, css: str | None) -> str:
    ref = tmp_path / "ref"
    ref.mkdir()
    structure = {
        "tag": "body", "styles": body_styles, "children": [
            {"tag": "section", "class": "sec", "styles": {}, "children": [
                {"tag": "h1", "styles": {}, "text": "eBay Playbook"},
            ]},
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "sec"}]}),
        encoding="utf-8")
    if css is not None:
        (ref / "css").mkdir()
        (ref / "css" / "main.css").write_text(css, encoding="utf-8")
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120, env={**os.environ},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = impl / "src" / "App.tsx"
    return app.read_text(encoding="utf-8") if app.is_file() else ""


def _emit_component(
    tmp_path: Path,
    node_styles: dict,
    css: str,
    *,
    inline_props: list[str] | None = None,
) -> str:
    ref = tmp_path / "ref"
    ref.mkdir()
    structure = {
        "tag": "body",
        "styles": {},
        "children": [
            {
                "tag": "section",
                "class": "style_header__tjhHk",
                "styles": node_styles,
                "inlineProps": inline_props or [],
                "children": [{"tag": "p", "text": "eBay Playbook"}],
            },
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "style_header__tjhHk"}]}),
        encoding="utf-8",
    )
    (ref / "css").mkdir()
    (ref / "css" / "main.css").write_text(css, encoding="utf-8")
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120, env={**os.environ},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((impl / "src" / "components").glob("*.tsx"))
    )


_BLACK = {"background-color": "rgb(0, 0, 0)"}


def test_root_bg_override_skipped_when_ref_css_sets_body_bg(tmp_path: Path) -> None:
    blob = _emit(
        tmp_path, _BLACK,
        "body,html{color:var(--fg);background-color:var(--color-background-primary)}",
    )
    # The captured dark root bg must NOT be baked over the forensic CSS.
    assert "background-color:rgb(0, 0, 0) !important" not in blob, blob
    # The forensic-CSS import path is untouched; the global clip style still emits.
    assert "overflow-x:clip" in blob, blob


def test_root_inline_bg_and_color_skipped_when_ref_css_sets_body_tokens(
    tmp_path: Path,
) -> None:
    blob = _emit(
        tmp_path,
        {"background-color": "rgb(0, 0, 0)", "color": "rgb(247, 247, 247)"},
        "html,body{background-color:var(--color-background-primary);color:var(--color-text-primary)}",
    )
    root_line = next(line for line in blob.splitlines() if line.strip().startswith("<div"))
    assert "backgroundColor" not in root_line, root_line
    assert "color:" not in root_line, root_line
    assert "overflowX" in root_line, root_line


def test_same_node_theme_colors_released_to_ref_css(tmp_path: Path) -> None:
    blob = _emit_component(
        tmp_path,
        {"background-color": "rgb(0, 0, 0)", "color": "rgb(247, 247, 247)"},
        ".style_header__tjhHk{color:var(--color-text-primary);background-color:var(--color-background-primary)}",
    )
    line = next(line for line in blob.splitlines() if "style_header__tjhHk" in line)
    assert "backgroundColor" not in line, line
    assert "color:" not in line, line


def test_author_inline_theme_colors_preserved(tmp_path: Path) -> None:
    blob = _emit_component(
        tmp_path,
        {"background-color": "rgb(0, 0, 0)", "color": "rgb(247, 247, 247)"},
        ".style_header__tjhHk{color:var(--color-text-primary);background-color:var(--color-background-primary)}",
        inline_props=["background-color", "color"],
    )
    line = next(line for line in blob.splitlines() if "style_header__tjhHk" in line)
    assert 'backgroundColor: "rgb(0, 0, 0)"' in line, line
    assert 'color: "rgb(247, 247, 247)"' in line, line


def test_root_bg_override_emitted_when_ref_css_leaves_body_bg_undefined(
    tmp_path: Path,
) -> None:
    # Ref CSS sets background on a component, NOT on body/html → the page base is
    # undefined, so the baked override is still needed (realfood dark-margin case).
    blob = _emit(tmp_path, _BLACK, ".style_card{background-color:#111}")
    assert "background-color:rgb(0, 0, 0) !important" in blob, blob


def test_descendant_body_selector_does_not_count(tmp_path: Path) -> None:
    # `.wrap body` is a descendant selector, not the base page selector — it must
    # not suppress the override.
    blob = _emit(tmp_path, _BLACK, ".wrap body{background-color:#222}")
    assert "background-color:rgb(0, 0, 0) !important" in blob, blob
