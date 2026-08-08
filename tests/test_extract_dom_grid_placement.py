"""extract-dom.sh must capture CSS-grid *item-placement* properties (navercorp #2).

The DOM capture allow-list `LAYOUT_PROPS` captures grid CONTAINER properties
(`grid-template-columns`, `grid-template-rows`, `gap`, `display:grid`) but
historically omitted the grid ITEM-placement properties (`grid-row`,
`grid-column`). getComputedStyle resolves an explicitly-placed item to e.g.
`grid-row: "1 / 4"`, but that value was never in the allow-list, so it never
reached structure.json and the transpiler never baked it.

Consequence on navercorp's hero: `.main-header.type-a .main-inner` is a
`grid-template-columns:928px 448px; grid-template-rows:repeat(3,1fr)` grid whose
carousel `.main-headline{grid-row:1/4}` spans the whole left column. With the
span dropped, grid auto-flow scatters all six children — the right-rail banner
cards (투자정보 etc.) collapse onto and overlap the carousel. This is bug #2.

An auto-placed item resolves to `grid-row: "auto"`, which the existing global
NOISE set already filters, so adding these two properties captures real
placements without bloating every node with a useless `grid-row:auto`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "skills" / "visual-debug" / "scripts" / "lib" / "extract-dom.js"


def _extract_iife(text: str) -> str:
    start = text.index("(() => {")
    end = text.index("})()", start) + len("})()")
    return text[start:end].replace("SELECTOR_PLACEHOLDER", '"#root"')


# A DOM stub whose getComputedStyle varies per element: each node carries a `_cs`
# map, and getComputedStyle reads it (defaulting display/position so paint and
# depth logic behave). This lets us assert the exact grid value that gets kept.
DOM_STUB = r"""
global.window = { scrollY: 0 };
global.SVGElement = function () {};
const DEFAULTS = { display: "block", position: "static" };
global.getComputedStyle = (el, _pseudo) => {
  const cs = (el && el._cs) || {};
  return {
    getPropertyValue: (p) => (p in cs ? cs[p] : (DEFAULTS[p] || "")),
    display: cs.display || DEFAULTS.display,
    position: cs.position || DEFAULTS.position,
  };
};
function el(tag, children, attrs, cs, text) {
  children = children || [];
  attrs = attrs || {};
  cs = cs || {};
  text = text || "";
  const inlineEntries = Object.entries(attrs._inline || {});
  const inlineStyle = {
    length: inlineEntries.length,
    getPropertyValue: (p) => (attrs._inline || {})[p] || "",
    getPropertyPriority: () => "",
  };
  inlineEntries.forEach(([name], index) => {
    inlineStyle[index] = name;
  });
    const node = {
    tagName: tag.toUpperCase(),
    className: attrs["class"] || "",
    style: inlineStyle,
    children: children,
    childNodes: text ? [{ nodeType: 3, textContent: text }] : [],
    nextSibling: null,
    nodeType: 1,
    _cs: cs,
    getAttribute: (k) => (k in attrs ? attrs[k] : null),
    querySelector: () => null,
    getBoundingClientRect: () => ({ width: 100, height: 100, top: 0, left: 0 }),
  };
  Object.defineProperty(node, "textContent", {
    get() {
      let t = text;
      for (const c of children) t += c.textContent || "";
      return t;
    },
  });
  return node;
}
// A grid whose left column is spanned by a placed carousel and whose right
// column holds an auto-placed banner card (the navercorp hero shape, minimal).
const headline = el("div", [], { class: "main-headline" },
  { display: "block", "grid-row": "1 / 4" }, "carousel");
const banner = el("div", [], { class: "main-banner-items" },
  { display: "block", "grid-column": "2 / 3", "grid-row": "auto" }, "invest");
const root = el("div", [headline, banner], { class: "main-inner", id: "root" },
  { display: "grid", "grid-template-columns": "928px 448px",
    "grid-template-rows": "repeat(3, 1fr)" }, "");
global.document = {
  querySelector: () => root,
  querySelectorAll: () => [],
  styleSheets: [],
  body: { children: [root] },
};
"""


def _find(node: dict, cls: str) -> dict | None:
    if not isinstance(node, dict):
        return None
    if cls in (node.get("class") or ""):
        return node
    for c in node.get("children") or []:
        hit = _find(c, cls)
        if hit:
            return hit
    return None


def _run(tmp_path: Path) -> dict:
    iife = _extract_iife(SCRIPT.read_text(encoding="utf-8"))
    harness = tmp_path / "harness.js"
    harness.write_text(DOM_STUB + "\nconsole.log(" + iife + ");\n", encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, timeout=20, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    tree: dict = json.loads(proc.stdout.strip())
    return tree


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_grid_row_span_placement_is_captured(tmp_path: Path) -> None:
    tree = _run(tmp_path)
    headline = _find(tree, "main-headline")
    assert headline is not None, "carousel node must survive capture"
    styles = headline.get("styles") or {}
    assert styles.get("grid-row") == "1 / 4", (
        f"grid-row span must be captured so the transpiler bakes it, got {styles}"
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_grid_column_placement_is_captured(tmp_path: Path) -> None:
    tree = _run(tmp_path)
    banner = _find(tree, "main-banner-items")
    assert banner is not None, "banner node must survive capture"
    styles = banner.get("styles") or {}
    assert styles.get("grid-column") == "2 / 3", (
        f"grid-column placement must be captured, got {styles}"
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_auto_grid_placement_is_not_captured(tmp_path: Path) -> None:
    # An auto-placed item resolves to grid-row:"auto"; the existing NOISE set
    # must filter it so we do not bloat every node with a useless default.
    tree = _run(tmp_path)
    banner = _find(tree, "main-banner-items")
    assert banner is not None, "banner node must survive capture"
    styles = banner.get("styles") or {}
    assert "grid-row" not in styles, (
        f"grid-row:auto is a default and must be filtered as noise, got {styles}"
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_two_token_auto_grid_placement_is_filtered(tmp_path: Path) -> None:
    # Some engines serialize an unplaced item as the two-token "auto / auto"
    # rather than bare "auto". That default must also be filtered, or every
    # node bakes a redundant gridRow:"auto / auto".
    iife = _extract_iife(SCRIPT.read_text(encoding="utf-8"))
    stub = DOM_STUB.replace('"grid-row": "auto"', '"grid-row": "auto / auto"')
    harness = tmp_path / "harness.js"
    harness.write_text(stub + "\nconsole.log(" + iife + ");\n", encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, timeout=20, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    tree = json.loads(proc.stdout.strip())
    banner = _find(tree, "main-banner-items")
    assert banner is not None, "banner node must survive capture"
    styles = banner.get("styles") or {}
    assert "grid-row" not in styles, (
        f'grid-row:"auto / auto" must be filtered as noise, got {styles}'
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_long_class_list_preserves_late_css_module_token(tmp_path: Path) -> None:
    tokens = [f"utility-{i:03d}" for i in range(35)]
    late_token = "MarkdownContent-module__heading--abc123"
    class_name = " ".join([*tokens, late_token])
    assert 300 < len(class_name) < 2000

    iife = _extract_iife(SCRIPT.read_text(encoding="utf-8"))
    stub = DOM_STUB.replace(
        '{ class: "main-headline" }',
        "{ class: " + json.dumps(class_name) + " }",
    )
    harness = tmp_path / "harness.js"
    harness.write_text(stub + "\nconsole.log(" + iife + ");\n", encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, timeout=20, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    tree = json.loads(proc.stdout.strip())
    headline = _find(tree, late_token)
    assert headline is not None
    assert headline["class"] == class_name


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_class_safety_envelope_never_cuts_a_token(tmp_path: Path) -> None:
    first = "a" * 1990
    boundary_token = "moduleXYZ"
    expected = f"{first} {boundary_token}"
    assert len(expected) == 2000
    over_limit = f"{expected} truncated-token"

    iife = _extract_iife(SCRIPT.read_text(encoding="utf-8"))
    stub = DOM_STUB.replace(
        '{ class: "main-headline" }',
        "{ class: " + json.dumps(over_limit) + " }",
    )
    harness = tmp_path / "harness.js"
    harness.write_text(stub + "\nconsole.log(" + iife + ");\n", encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, timeout=20, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    tree = json.loads(proc.stdout.strip())
    headline = _find(tree, boundary_token)
    assert headline is not None
    assert headline["class"] == expected
    assert "truncated-token" not in headline["class"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_inline_typography_props_are_captured_for_unbake_guard(
    tmp_path: Path,
) -> None:
    iife = _extract_iife(SCRIPT.read_text(encoding="utf-8"))
    stub = DOM_STUB.replace(
        '{ class: "main-headline" }',
        '{ class: "main-headline", _inline: {'
        '"font-size": "18px", "line-height": "27px" } }',
    )
    harness = tmp_path / "harness.js"
    harness.write_text(stub + "\nconsole.log(" + iife + ");\n", encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, timeout=20, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    tree = json.loads(proc.stdout.strip())
    headline = _find(tree, "main-headline")
    assert headline is not None
    assert headline["inlineProps"] == ["font-size", "line-height"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_inline_forensic_flow_props_are_captured(tmp_path: Path) -> None:
    iife = _extract_iife(SCRIPT.read_text(encoding="utf-8"))
    stub = DOM_STUB.replace(
        '{ class: "main-headline" }',
        '{ class: "main-headline", _inline: {'
        '"margin": "0 -24px", "padding": "80px 48px", '
        '"max-height": "600px", '
        '"padding-left": "48px", "padding-right": "48px", '
        '"padding-top": "80px", "padding-bottom": "80px", '
        '"margin-left": "-24px", "margin-right": "-24px", '
        '"gap": "80px 48px", "row-gap": "80px", "column-gap": "48px", '
        '"grid-template-columns": "264px 1fr", '
        '"grid-template-rows": "120px 300px" } }',
    )
    harness = tmp_path / "harness.js"
    harness.write_text(stub + "\nconsole.log(" + iife + ");\n", encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, timeout=20, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    tree = json.loads(proc.stdout.strip())
    headline = _find(tree, "main-headline")
    assert headline is not None
    assert headline["inlineProps"] == [
        "max-height",
        "padding-left",
        "padding-right",
        "padding-top",
        "padding-bottom",
        "margin-left",
        "margin-right",
        "grid-template-columns",
        "grid-template-rows",
        "margin",
        "padding",
        "gap",
        "row-gap",
        "column-gap",
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_inline_custom_properties_are_captured_as_node_styles(
    tmp_path: Path,
) -> None:
    iife = _extract_iife(SCRIPT.read_text(encoding="utf-8"))
    stub = DOM_STUB.replace(
        '{ class: "main-headline" }',
        '{ class: "main-headline", _inline: {'
        '"--index": "3", "--tile-hue": "210deg" } }',
    )
    harness = tmp_path / "harness.js"
    harness.write_text(stub + "\nconsole.log(" + iife + ");\n", encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, timeout=20, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    tree = json.loads(proc.stdout.strip())
    headline = _find(tree, "main-headline")
    assert headline is not None
    styles = headline.get("styles") or {}
    assert styles.get("--index") == "3", styles
    assert styles.get("--tile-hue") == "210deg", styles


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_svg_geometry_uses_bounded_extended_attribute_envelope(
    tmp_path: Path,
) -> None:
    long_d = "M" + ("1" * 2322)
    oversized_d = "M" + ("2" * 20000)
    assert len(long_d) == 2323
    assert len(oversized_d) > 20000

    iife = _extract_iife(SCRIPT.read_text(encoding="utf-8"))
    headline_def = (
        'const headline = el("path", [], '
        f'{{ class: "octicon-gear", d: {json.dumps(long_d)} }}, '
        '{ display: "block" }, "");'
    )
    banner_def = (
        'const banner = el("path", [], '
        f'{{ class: "oversized-path", d: {json.dumps(oversized_d)} }}, '
        '{ display: "block" }, "");'
    )
    stub = DOM_STUB
    stub = stub.replace(
        'const headline = el("div", [], { class: "main-headline" },\n'
        '  { display: "block", "grid-row": "1 / 4" }, "carousel");',
        headline_def,
    )
    stub = stub.replace(
        'const banner = el("div", [], { class: "main-banner-items" },\n'
        '  { display: "block", "grid-column": "2 / 3", "grid-row": "auto" }, "invest");',
        banner_def,
    )
    harness = tmp_path / "harness.js"
    harness.write_text(stub + "\nconsole.log(" + iife + ");\n", encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, timeout=20, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    tree = json.loads(proc.stdout.strip())
    kept = _find(tree, "octicon-gear")
    rejected = _find(tree, "oversized-path")
    assert kept is not None
    assert kept["d"] == long_d
    assert rejected is not None
    assert "d" not in rejected
