"""Release absolute computed insets when mirrored CSS owns authored offsets."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def _emit(
    tmp_path: Path,
    *,
    css: str,
    child: dict,
    ancestor_class: str = "navercorp esg",
    hero_class: str = "esg-hero",
    before_child: dict | None = None,
    capture_w: int = 1440,
) -> str:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "css").mkdir()
    (ref / "css" / "main.css").write_text(css, encoding="utf-8")
    children = []
    if before_child is not None:
        children.append(before_child)
    children.append(child)
    structure = {
        "tag": "body",
        "class": ancestor_class,
        "styles": {"width": f"{capture_w}px"},
        "children": [
            {
                "tag": "section",
                "class": hero_class,
                "styles": {"position": "relative"},
                "children": children,
            }
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": hero_class}]}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "UI_CLONE_UNBAKE_CAPTURE_W": str(capture_w)},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((impl / "src" / "components").glob("*.tsx"))
    )


def _absolute_child(**overrides: object) -> dict:
    node: dict[str, object] = {
        "tag": "div",
        "class": "parallax-items",
        "styles": {
            "position": "absolute",
            "top": "90px",
            "right": "760px",
            "bottom": "610px",
            "left": "288px",
            "width": "392px",
            "height": "200px",
        },
        "children": [],
    }
    node.update(overrides)
    return node


def _line_for(blob: str, class_name: str) -> str:
    return next(line for line in blob.splitlines() if f'className="{class_name}"' in line)


def test_descendant_nth_absolute_insets_release_owned_sides_and_complements(
    tmp_path: Path,
) -> None:
    blob = _emit(
        tmp_path,
        css=".navercorp.esg .esg-hero .parallax-items:nth-child(1){top:10%;left:20%}",
        child=_absolute_child(),
    )

    line = _line_for(blob, "parallax-items")
    assert 'position: "absolute"' in line, line
    assert "top:" not in line, line
    assert "right:" not in line, line
    assert "bottom:" not in line, line
    assert "left:" not in line, line


def test_bottom_left_absolute_arrow_releases_complements(tmp_path: Path) -> None:
    blob = _emit(
        tmp_path,
        css=".page-hero-arrow{bottom:34px;left:50%}",
        child=_absolute_child(**{"class": "page-hero-arrow"}),
    )

    line = _line_for(blob, "page-hero-arrow")
    assert "top:" not in line, line
    assert "right:" not in line, line
    assert "bottom:" not in line, line
    assert "left:" not in line, line


@pytest.mark.parametrize(
    "declaration",
    [
        "inset:10%",
        "inset:10% 20%",
        "inset:10% 20% 30%",
        "inset:10% 20% 30% 40%",
        "inset:calc(50% - 10px) 0",
        "inset:calc(50% - 10px) 0 calc(25% + 4px)",
    ],
)
def test_absolute_inset_shorthand_releases_all_physical_sides(
    tmp_path: Path,
    declaration: str,
) -> None:
    blob = _emit(
        tmp_path,
        css=f".page-hero-arrow{{{declaration}}}",
        child=_absolute_child(**{"class": "page-hero-arrow"}),
    )

    line = _line_for(blob, "page-hero-arrow")
    assert "top:" not in line, line
    assert "right:" not in line, line
    assert "bottom:" not in line, line
    assert "left:" not in line, line


@pytest.mark.parametrize(
    ("declaration", "kept"),
    [
        ("inset-inline:50%", ("top", "bottom")),
        ("inset-inline:50% 24px", ("top", "bottom")),
        ("inset-inline:calc(50% - 10px) 0", ("top", "bottom")),
        ("inset-block:12px", ("right", "left")),
        ("inset-block:12px 24px", ("right", "left")),
        ("inset-block:calc(50% - 10px) 0", ("right", "left")),
    ],
)
def test_absolute_logical_inset_shorthands_release_their_physical_axis(
    tmp_path: Path,
    declaration: str,
    kept: tuple[str, str],
) -> None:
    blob = _emit(
        tmp_path,
        css=f".page-hero-arrow{{{declaration}}}",
        child=_absolute_child(**{"class": "page-hero-arrow"}),
    )

    line = _line_for(blob, "page-hero-arrow")
    for side in kept:
        assert f"{side}:" in line, line
    for side in sorted({"top", "right", "bottom", "left"} - set(kept)):
        assert f"{side}:" not in line, line


def test_absolute_insets_preserved_when_ancestor_chain_does_not_match(tmp_path: Path) -> None:
    blob = _emit(
        tmp_path,
        css=".navercorp.esg .other-hero .parallax-items:nth-child(1){top:10%;left:20%}",
        child=_absolute_child(),
    )

    line = _line_for(blob, "parallax-items")
    assert 'top: "90px"' in line, line
    assert 'right: "760px"' in line, line
    assert 'bottom: "610px"' in line, line
    assert 'left: "288px"' in line, line


def test_absolute_insets_preserved_when_nth_child_does_not_match(tmp_path: Path) -> None:
    blob = _emit(
        tmp_path,
        css=".navercorp.esg .esg-hero .parallax-items:nth-child(1){top:10%;left:20%}",
        before_child={"tag": "div", "class": "intro", "styles": {}, "children": []},
        child=_absolute_child(),
    )

    line = _line_for(blob, "parallax-items")
    assert 'top: "90px"' in line, line
    assert 'right: "760px"' in line, line
    assert 'bottom: "610px"' in line, line
    assert 'left: "288px"' in line, line


def test_absolute_inset_inline_props_are_preserved(tmp_path: Path) -> None:
    blob = _emit(
        tmp_path,
        css=".navercorp.esg .esg-hero .parallax-items:nth-child(1){top:10%;left:20%}",
        child=_absolute_child(inlineProps=["top", "right"]),
    )

    line = _line_for(blob, "parallax-items")
    assert 'top: "90px"' in line, line
    assert 'right: "760px"' in line, line
    assert "bottom:" not in line, line
    assert "left:" not in line, line


def test_absolute_insets_preserved_for_non_applying_media(tmp_path: Path) -> None:
    blob = _emit(
        tmp_path,
        css="@media (max-width: 768px){.page-hero-arrow{bottom:34px;left:50%}}",
        child=_absolute_child(**{"class": "page-hero-arrow"}),
        capture_w=1440,
    )

    line = _line_for(blob, "page-hero-arrow")
    assert 'top: "90px"' in line, line
    assert 'right: "760px"' in line, line
    assert 'bottom: "610px"' in line, line
    assert 'left: "288px"' in line, line


def test_absolute_insets_preserved_for_non_absolute_node(tmp_path: Path) -> None:
    blob = _emit(
        tmp_path,
        css=".page-hero-arrow{bottom:34px;left:50%}",
        child=_absolute_child(
            **{
                "class": "page-hero-arrow",
                "styles": {"position": "relative", "bottom": "34px", "left": "50px"},
            }
        ),
    )

    line = _line_for(blob, "page-hero-arrow")
    assert 'bottom: "34px"' in line, line
    assert 'left: "50px"' in line, line


@pytest.mark.parametrize(
    "selector",
    [
        "#hero-arrow",
        ".page-hero-arrow[data-active=true]",
        ".esg-hero > .page-hero-arrow",
        ".intro + .page-hero-arrow",
        ".page-hero-arrow:hover",
    ],
)
def test_absolute_insets_preserved_for_unsupported_selectors(
    tmp_path: Path,
    selector: str,
) -> None:
    blob = _emit(
        tmp_path,
        css=f"{selector}{{bottom:34px;left:50%}}",
        child=_absolute_child(**{"class": "page-hero-arrow", "id": "hero-arrow"}),
    )

    line = _line_for(blob, "page-hero-arrow")
    assert 'top: "90px"' in line, line
    assert 'right: "760px"' in line, line
    assert 'bottom: "610px"' in line, line
    assert 'left: "288px"' in line, line
