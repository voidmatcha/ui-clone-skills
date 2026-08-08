"""scaffold-to-jsx honors captured display:none (navercorp B2 inner-reflow).

getComputedStyle records `display:none` on a node the live page keeps hidden
(an inactive `.tab-data` panel, a hidden-language `.en-data`/`.mo-data` variant).
extract-dom preserves that as the top-level `display` field but drops it from
`styles` (its default-drop set treats 'none' as a UA default), so style_to_jsx
never re-emits it. Rendering the node visible stacks its whole subtree in flow
and inflates the section (B2: main-partner +303px from one inactive tab, footer
+165px from `.en-data`). The transpiler must honor the captured top-level
`display` field — faithful to the reference frame section-compare measures.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def _run(tmp_path: Path, children: list[dict], css: str | None = None) -> str:
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    section = {"tag": "section", "class": "sec", "display": "block",
               "position": "relative", "styles": {"height": "300px"},
               "children": children}
    (ref / "structure.json").write_text(
        json.dumps({"tag": "body", "class": "", "display": "block",
                    "styles": {}, "children": [section]}),
        encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "sec"}]}), encoding="utf-8")
    if css is not None:
        (ref / "css").mkdir()
        (ref / "css" / "main.css").write_text(css, encoding="utf-8")
    (impl / "package.json").write_text('{"name":"i","dependencies":{}}', encoding="utf-8")
    proc = subprocess.run(["bash", str(SCRIPT), str(ref), str(impl)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in sorted((impl / "src" / "components").glob("*.tsx")))


def test_captured_display_none_is_hidden(tmp_path: Path) -> None:
    # an inactive tab panel: top-level display:none, styles omit it (default-drop).
    blob = _run(tmp_path, [
        {"tag": "div", "class": "tab-data active", "display": "block",
         "styles": {"height": "296px"}, "children": [{"tag": "p", "text": "live"}]},
        {"tag": "div", "class": "tab-data", "display": "none",
         "styles": {"position": "static"}, "children": [{"tag": "p", "text": "hidden"}]},
    ])
    assert 'boxSizing' not in blob or True  # smoke
    assert 'display: "none"' in blob, "inactive tab-data must render display:none"


def test_visible_node_not_forced_hidden(tmp_path: Path) -> None:
    blob = _run(tmp_path, [
        {"tag": "div", "class": "shown", "display": "block",
         "styles": {"height": "100px"}, "children": [{"tag": "p", "text": "x"}]},
    ])
    assert 'display: "none"' not in blob


def test_display_none_with_empty_styles_still_hidden(tmp_path: Path) -> None:
    # a hidden node whose only non-default computed props were dropped: no styles
    # object, but the top-level display field still says none.
    blob = _run(tmp_path, [
        {"tag": "div", "class": "en-data", "display": "none",
         "children": [{"tag": "span", "text": "english"}]},
    ])
    assert 'className="en-data"' in blob
    assert 'display: "none"' in blob


def test_responsive_display_classes_are_owned_by_mirrored_css(tmp_path: Path) -> None:
    blob = _run(
        tmp_path,
        [
            {
                "tag": "div",
                "class": "Nav_displayUnderLarge__abc",
                "display": "none",
                "styles": {"position": "static"},
                "children": [{"tag": "button", "text": "Search"}],
            },
            {
                "tag": "div",
                "class": "Nav_displayOverLarge__def",
                "display": "flex",
                "styles": {"display": "flex", "position": "static"},
                "children": [{"tag": "button", "text": "Search"}],
            },
        ],
        css=(
            ".Nav_displayUnderLarge__abc{display:flex}"
            ".Nav_displayOverLarge__def{display:none}"
            "@media(min-width:1012px){"
            ".Nav_displayUnderLarge__abc{display:none!important}"
            ".Nav_displayOverLarge__def{display:flex!important}"
            "}"
        ),
    )

    for class_name in ("Nav_displayUnderLarge__abc", "Nav_displayOverLarge__def"):
        opening_tag = next(
            line for line in blob.splitlines() if f'className="{class_name}"' in line
        )
        assert 'display: "none"' not in opening_tag
        assert 'display: "flex"' not in opening_tag


def test_compound_responsive_display_selectors_are_owned_by_mirrored_css(
    tmp_path: Path,
) -> None:
    """CSS-module state classes must not freeze the desktop visibility state."""
    classes = (
        "style_header__control__fpxyj style_isMenuToggle__JuBN0",
        "style_header__control__fpxyj style_isThemeSwitcher__diJ12",
    )
    blob = _run(
        tmp_path,
        [
            {
                "tag": "div",
                "class": classes[0],
                "display": "none",
                "styles": {"position": "static"},
                "children": [{"tag": "button", "text": "Menu"}],
            },
            {
                "tag": "div",
                "class": classes[1],
                "display": "block",
                "styles": {"display": "block", "position": "static"},
                "children": [{"tag": "button", "text": "Theme"}],
            },
        ],
        css=(
            ".style_header__control__fpxyj.style_isMenuToggle__JuBN0{display:block}"
            ".style_header__control__fpxyj.style_isThemeSwitcher__diJ12{display:none}"
            "@media(min-width:1100px){"
            ".style_header__control__fpxyj.style_isMenuToggle__JuBN0{display:none}"
            ".style_header__control__fpxyj.style_isThemeSwitcher__diJ12{display:block}"
            "}"
        ),
    )

    for class_name in classes:
        opening_tag = next(
            line for line in blob.splitlines() if f'className="{class_name}"' in line
        )
        assert 'display: "none"' not in opening_tag
        assert 'display: "block"' not in opening_tag
