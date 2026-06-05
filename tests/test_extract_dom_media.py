from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "skills" / "visual-debug" / "scripts" / "extract-dom.sh"


def _extract_iife(text: str) -> str:
    """Pull the extraction IIFE out of the bash heredoc and inject a selector,
    mirroring the sed substitution the script does at runtime."""
    start = text.index("(() => {")
    end = text.index("})()", start) + len("})()")
    return text[start:end].replace("SELECTOR_PLACEHOLDER", '"#root"')


# Minimal DOM stub: a <picture><source/><img/> nested 12 <div>s deep — well past
# the HTML_DEPTH_CAP (10). A faithful extractor must still capture the media
# leaves (otherwise the clone has zero images — the diabrowser failure mode).
DOM_STUB = r"""
function cstyle() {
  return {
    getPropertyValue: (p) =>
      p === "display" ? "block" : p === "position" ? "static" : "",
    display: "block",
    position: "static",
  };
}
global.window = { scrollY: 0 };
global.getComputedStyle = (_el, _pseudo) => cstyle();
global.SVGElement = function () {};
function el(tag, children, attrs, text) {
  children = children || [];
  attrs = attrs || {};
  text = text || "";
  const node = {
    tagName: tag.toUpperCase(),
    className: attrs["class"] || "",
    children: children,
    childNodes: text ? [{ nodeType: 3, textContent: text }] : [],
    nextSibling: null,
    nodeType: 1,
    getAttribute: (k) => (k in attrs ? attrs[k] : null),
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
let node = el("picture", [
  el("source", [], { srcset: "https://cdn.example/images/x.avif 1x", type: "image/avif" }),
  el("img", [], { src: "", "data-src": "https://cdn.example/images/x.webp", alt: "deep" }),
]);
// 10 wrapping divs put <picture> at depth 10 (== HTML_DEPTH_CAP, captured) and
// its <source>/<img> at depth 11 (dropped pre-fix) — the exact diabrowser case.
for (let i = 0; i < 10; i++) node = el("div", [node], { class: "w" + i });
const root = node;
global.document = {
  querySelector: () => root,
  querySelectorAll: () => [],
  styleSheets: [],
  body: { children: [root] },
};
"""


def _find(node: object, tag: str) -> dict | None:
    if not isinstance(node, dict):
        return None
    if (node.get("tag") or "") == tag:
        return node
    for c in node.get("children") or []:
        hit = _find(c, tag)
        if hit:
            return hit
    return None


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_extract_dom_keeps_deep_media_leaves(tmp_path: Path) -> None:
    """U1: a <picture> nested past HTML_DEPTH_CAP must still have its
    <source>/<img> children captured — otherwise deeply-nested galleries (React/
    Tailwind trees) yield a clone with zero images."""
    iife = _extract_iife(SCRIPT.read_text(encoding="utf-8"))
    harness = tmp_path / "harness.js"
    harness.write_text(DOM_STUB + "\nconsole.log(" + iife + ");\n", encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, timeout=20, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    tree = json.loads(proc.stdout.strip())

    picture = _find(tree, "picture")
    assert picture is not None, "deep <picture> must be captured"
    kids = picture.get("children") or []
    assert len(kids) == 2, f"<picture> must keep its source+img leaves, got {len(kids)}: {kids}"

    img = _find(picture, "img")
    assert img is not None, "deep <img> leaf must survive the depth cap"
    assert img.get("data-src") == "https://cdn.example/images/x.webp", (
        f"lazy data-src must be captured: {img}"
    )
    source = _find(picture, "source")
    assert source is not None and source.get("srcset", "").startswith("https://cdn.example"), (
        f"<source srcset> must be captured: {source}"
    )


# A text leaf nested 12 <div>s deep — past HTML_DEPTH_CAP (10). Deep React/
# Tailwind copy and split-text word spans live here; the flat cap dropped them
# (clone text 6521 vs ref 8202).
DOM_STUB_DEEP_TEXT = r"""
function cstyle() {
  return {
    getPropertyValue: (p) =>
      p === "display" ? "block" : p === "position" ? "static" : "",
    display: "block",
    position: "static",
  };
}
global.window = { scrollY: 0 };
global.getComputedStyle = (_el, _pseudo) => cstyle();
global.SVGElement = function () {};
function el(tag, children, attrs, text) {
  children = children || [];
  attrs = attrs || {};
  text = text || "";
  const node = {
    tagName: tag.toUpperCase(),
    className: attrs["class"] || "",
    children: children,
    childNodes: text ? [{ nodeType: 3, textContent: text }] : [],
    nextSibling: null,
    nodeType: 1,
    getAttribute: (k) => (k in attrs ? attrs[k] : null),
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
// deep EMPTY wrapper chain: innermost (EMPTY_DEEP_X) sits well past the cap with
// no text — must be dropped so capture bloat stays bounded.
let empty = el("div", [], { class: "EMPTY_DEEP_X" });
for (let i = 0; i < 16; i++) empty = el("div", [empty], { class: "e" + i });
// deep text leaf at depth 12 (must survive — carries copy)
let node = el("p", [], { class: "leaf" }, "DEEP_COPY_PROBE words here");
for (let i = 0; i < 12; i++) node = el("div", [node], { class: "w" + i });
const root = el("section", [node, empty], { class: "root" });
global.document = {
  querySelector: () => root,
  querySelectorAll: () => [],
  styleSheets: [],
  body: { children: [root] },
};
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_dia_regression_deep_capture_preserves_media_text_and_ids(tmp_path: Path) -> None:
    """#16 consolidated depth-cap regression (the dia '0 images' shape in ONE
    fixture): a component tree pushes a <picture> to exactly HTML_DEPTH_CAP
    with its <source>/<img alt> one level past it, a copy block nested past
    the cap, and an id on the deep section — ALL must survive capture. If any
    of Fix 66 (media leaves), Fix 67 (deep text), or Fix 69 (ids) regresses,
    this single test fails with a named assertion."""
    iife = _extract_iife(SCRIPT.read_text(encoding="utf-8"))
    stub_tail = r"""
let gallery = el("picture", [
  el("source", [], { srcset: "https://cdn.example/images/d/pic.avif 1x", type: "image/avif" }),
  el("img", [], { src: "", "data-src": "https://cdn.example/images/d/pic.webp", alt: "Dia gallery shot" }),
]);
let copy = el("p", [], { "class": "copy" }, "DIA_DEEP_COPY body text");
let inner = el("section", [gallery, copy], { "class": "deepsec", id: "gallery" });
for (let i = 0; i < 10; i++) inner = el("div", [inner], { "class": "lvl" + i });
const root = inner;
global.document = {
  querySelector: () => root,
  querySelectorAll: () => [],
  styleSheets: [],
  body: { children: [root] },
};
"""
    # reuse the shared el()/getComputedStyle stub, replace the tree construction
    base_stub = DOM_STUB.split("let node = el(")[0]
    harness = tmp_path / "harness.js"
    harness.write_text(base_stub + stub_tail + "\nconsole.log(" + iife + ");\n", encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, timeout=20, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    tree = json.loads(proc.stdout.strip())

    sec = _find(tree, "section")
    assert sec is not None and sec.get("id") == "gallery", "deep section id must survive (Fix 69)"
    picture = _find(tree, "picture")
    assert picture is not None and len(picture.get("children") or []) == 2, (
        "deep <picture> must keep source+img past the cap (Fix 66 — the dia 0-images shape)"
    )
    img = _find(picture, "img")
    assert img is not None and img.get("data-src", "").endswith("pic.webp"), (
        "lazy data-src must be captured on the deep img (Fix 66)"
    )
    assert img.get("alt") == "Dia gallery shot", "alt text must survive"
    copy = _find(tree, "p")
    assert copy is not None and "DIA_DEEP_COPY" in (copy.get("text") or ""), (
        "copy nested past the cap must survive (Fix 67)"
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_extract_dom_captures_html_id(tmp_path: Path) -> None:
    """Section anchors: section-map identifies sections by their HTML `id`
    (#problem, #pyramid, ...), and section-compare locates impl sections by the
    same id — but ATTR_KEYS never captured `id` on HTML nodes (only the SVG
    capture-everything path kept them), so the clone had no ids and 11/14
    sections reported MISSING impl in the canonical visual diff."""
    iife = _extract_iife(SCRIPT.read_text(encoding="utf-8"))
    stub = DOM_STUB.replace(
        'el("img", [], { src: "", "data-src": "https://cdn.example/images/x.webp", alt: "deep" })',
        'el("img", [], { src: "", "data-src": "https://cdn.example/images/x.webp", alt: "deep", id: "hero-img" })',
    )
    harness = tmp_path / "harness.js"
    harness.write_text(stub + "\nconsole.log(" + iife + ");\n", encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, timeout=20, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    tree = json.loads(proc.stdout.strip())
    img = _find(tree, "img")
    assert img is not None and img.get("id") == "hero-img", (
        f"HTML id attribute must be captured: {img}"
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_extract_dom_keeps_deep_text(tmp_path: Path) -> None:
    """#6: real copy nested past HTML_DEPTH_CAP (deep React/Tailwind trees,
    split-text word spans) must still be captured so clone text reaches the ref;
    empty wrapper subtrees past the cap stay dropped to bound capture bloat."""
    iife = _extract_iife(SCRIPT.read_text(encoding="utf-8"))
    harness = tmp_path / "harness.js"
    harness.write_text(DOM_STUB_DEEP_TEXT + "\nconsole.log(" + iife + ");\n", encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, timeout=20, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    tree = json.loads(proc.stdout.strip())

    leaf = _find(tree, "p")
    assert leaf is not None, "deep text <p> past the cap must be captured"
    assert "DEEP_COPY_PROBE" in (leaf.get("text") or ""), f"deep copy must survive: {leaf}"
    # empty wrapper subtree past the cap is still pruned (bloat control)
    assert "EMPTY_DEEP_X" not in json.dumps(tree), (
        "empty wrapper nested past the cap should be dropped to bound capture bloat"
    )
