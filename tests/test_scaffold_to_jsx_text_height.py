from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def _run(ref: Path, impl: Path) -> str:
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return "".join(
        p.read_text() for p in (impl / "src" / "components").glob("*.tsx"))


def _ref_with(tmp_path: Path, leaf: dict) -> tuple[Path, Path]:
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "children": [leaf]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8")
    return ref, impl


# ── Fix 20/21: frozen px height -> min-height floor, max-height dropped ──

def test_text_leaf_height_becomes_min_height(tmp_path: Path) -> None:
    """A frozen px height on a text element clips the reflowed copy. It is
    converted to a min-height floor (so full-bleed sections keep their height
    yet text can grow); max-height is dropped; width and line-height stay."""
    ref, impl = _ref_with(tmp_path, {"tag": "h2", "text": "Eat Real Food", "styles": {
        "height": "333px", "max-height": "335px", "width": "246px", "line-height": "19px",
    }})
    blob = _run(ref, impl)
    assert 'minHeight: "333px"' in blob, "height must convert to a min-height floor"
    assert 'height: "333px"' not in blob, "fixed height must not remain as a hard height"
    assert "335px" not in blob, "max-height must be dropped (it clips)"
    assert "246px" in blob, "width must be preserved (wrapping geometry)"
    assert "19px" in blob, "line-height must be preserved"


def test_container_with_children_unfreezes_height(tmp_path: Path) -> None:
    """A non-text container whose children can grow must also unfreeze height —
    otherwise the now-growable text still clips at the parent box."""
    ref, impl = _ref_with(tmp_path, {"tag": "div", "styles": {"height": "900px"},
                                     "children": [{"tag": "p", "text": "hi"}]})
    blob = _run(ref, impl)
    assert 'minHeight: "900px"' in blob
    assert 'height: "900px"' not in blob


def test_overflow_hidden_mask_keeps_height(tmp_path: Path) -> None:
    """overflow:hidden height is an intentional clip / reveal mask (collapsed
    FAQ) — it must be preserved verbatim, not converted."""
    ref, impl = _ref_with(tmp_path, {"tag": "div", "text": "answer",
                                     "styles": {"height": "777px", "overflow": "hidden"}})
    blob = _run(ref, impl)
    assert 'height: "777px"' in blob, "overflow:hidden mask height must be preserved"


def test_empty_spacer_keeps_height(tmp_path: Path) -> None:
    """An element with no text and no children is a structural spacer — keep
    its height (nothing can grow to clip)."""
    ref, impl = _ref_with(tmp_path, {"tag": "div", "styles": {"height": "48px"}})
    blob = _run(ref, impl)
    assert 'height: "48px"' in blob


def test_replaced_img_keeps_height(tmp_path: Path) -> None:
    """Replaced elements size by geometry, not text flow — keep height."""
    ref, impl = _ref_with(tmp_path, {"tag": "img", "styles": {"height": "120px"},
                                     "src": "/x.png"})
    blob = _run(ref, impl)
    assert 'height: "120px"' in blob


# ── Fix 21: transform / opacity captured mid-animation reset to rest ──

def test_animated_transform_is_reset(tmp_path: Path) -> None:
    """A translate captured mid scroll-reveal (element also transitions
    transform) is an animation state -> reset to rest so the clone isn't
    displaced."""
    ref, impl = _ref_with(tmp_path, {"tag": "div", "text": "Whole Grains", "styles": {
        "transform": "matrix(1, 0, 0, 1, -81, 0)", "transition-property": "transform",
    }})
    blob = _run(ref, impl)
    assert "matrix(1, 0, 0, 1, -81, 0)" not in blob, "animation-state transform must be reset"


def test_static_transform_is_preserved(tmp_path: Path) -> None:
    """A transform with no transition/animation (e.g. centering) is static
    design — preserve it."""
    ref, impl = _ref_with(tmp_path, {"tag": "div", "text": "badge", "styles": {
        "transform": "translate(-50%, -50%)",
    }})
    blob = _run(ref, impl)
    assert "translate(-50%, -50%)" in blob, "static transform must be preserved"


def test_scroll_state_px_translation_is_reset(tmp_path: Path) -> None:
    """A pure px translation with NO css transition/animation marker is a
    JS-driven scroll/stagger state (realfood pyramid categories) -> reset."""
    ref, impl = _ref_with(tmp_path, {"tag": "div", "text": "Whole Grains", "styles": {
        "transform": "matrix(1, 0, 0, 1, -81, 0)",
    }})
    blob = _run(ref, impl)
    assert "matrix(1, 0, 0, 1, -81, 0)" not in blob


def test_rotate_scale_transform_is_preserved(tmp_path: Path) -> None:
    """A transform with a rotate/scale (non-identity linear part) is static
    design -> preserved even with no animation marker."""
    ref, impl = _ref_with(tmp_path, {"tag": "div", "text": "badge", "styles": {
        "transform": "matrix(0.7071, 0.7071, -0.7071, 0.7071, 0, 0)",  # 45deg rotate
    }})
    blob = _run(ref, impl)
    assert "matrix(0.7071" in blob, "rotate/scale transform must be preserved"


def test_subpixel_translation_is_preserved(tmp_path: Path) -> None:
    """A sub-threshold px nudge is not a scroll state -> preserved."""
    ref, impl = _ref_with(tmp_path, {"tag": "div", "text": "x", "styles": {
        "transform": "matrix(1, 0, 0, 1, 1, 0)",
    }})
    blob = _run(ref, impl)
    assert "matrix(1, 0, 0, 1, 1, 0)" in blob


def test_split_text_animation_collapses_to_clean_text(tmp_path: Path) -> None:
    """Framer split-text wraps each character in its own span; rendering the
    nested char-spans drops word spaces ('RealFoodcansolve'). The transpiler
    must collapse such a subtree to clean, correctly-spaced visible text."""
    def chars(w: str) -> list[dict]:
        return [{"tag": "span", "class": "dga_disintegrating", "text": c} for c in w]
    heading = {"tag": "h2", "class": "dga_disintegrating", "children": [
        {"tag": "span", "children": chars("Real")},
        {"tag": "span", "text": " "},
        {"tag": "span", "children": chars("Food")},
        {"tag": "span", "text": " "},
        {"tag": "span", "children": chars("can")},
        {"tag": "span", "text": " "},
        {"tag": "span", "children": chars("solve")},
    ]}
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps(
        {"tag": "body", "children": [{"tag": "section", "class": "hero", "children": [heading]}]}))
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"id": 0, "tag": "section", "cls": "hero"}]}))
    blob = _run(ref, impl)
    assert "Real Food can solve" in blob, "split-text must collapse to spaced copy"
    assert "dga_disintegrating" not in blob, "per-character spans must be collapsed away"


def test_normal_short_word_list_not_collapsed(tmp_path: Path) -> None:
    """A normal list of multi-character items must NOT be mistaken for split-text
    (guards the heuristic against false positives)."""
    items = [{"tag": "span", "text": w} for w in
             ["Home", "About", "Resources", "FAQs", "Contact", "Winning",
              "Pyramid", "Solution", "Health", "Real", "Food", "Wins"]]
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps(
        {"tag": "body", "children": [{"tag": "section", "class": "hero",
         "children": [{"tag": "nav", "children": items}]}]}))
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"id": 0, "tag": "section", "cls": "hero"}]}))
    blob = _run(ref, impl)
    assert "Resources" in blob and "Winning" in blob, "real multi-char items must survive"


def test_sticky_section_root_wrapped_in_relative_ancestor(tmp_path: Path) -> None:
    """A section whose root is position:sticky must be emitted wrapped in its
    captured relative containing-block ancestor (with that ancestor's height as
    a min-height floor) so the sticky releases at the section end instead of
    pinning to the page body for the whole scroll."""
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "res_section__VAZi",
             "styles": {"position": "relative", "height": "2700px"},
             "children": [
                 {"tag": "div", "class": "res_sticky__EiBuU",
                  "styles": {"position": "sticky", "top": "0px"},
                  "children": [{"tag": "p", "text": "Resources"}]},
             ]},
        ],
    }), encoding="utf-8")
    # section-map points at the STICKY element as the section root (the bug case)
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"id": 0, "tag": "div", "cls": "res_sticky__EiBuU"}]}),
        encoding="utf-8")
    blob = _run(ref, impl)
    assert "res_section__VAZi" in blob, "sticky must be wrapped in its relative section ancestor"
    assert 'position: "relative"' in blob and 'minHeight: "2700px"' in blob, (
        "wrapper must carry position:relative + the section height as min-height"
    )
    # the relative wrapper must OPEN before the sticky div
    assert blob.index("res_section__VAZi") < blob.index("res_sticky__EiBuU")


def test_word_split_spans_keep_spaces(tmp_path: Path) -> None:
    """Word-split headline spans (<span>For</span> <span>the</span>) carry a
    whitespace text node between them (wsAfter); the transpiler must emit {' '}
    so the words don't run together ('Forthe')."""
    ref, impl = _ref_with(tmp_path, {"tag": "h2", "children": [
        {"tag": "span", "text": "For", "wsAfter": True},
        {"tag": "span", "text": "the", "wsAfter": True},
        {"tag": "span", "text": "first"},
    ]})
    blob = _run(ref, impl)
    assert "{' '}" in blob, "word-split spans must be separated by an explicit JSX space"
    assert blob.count("{' '}") >= 2, "each wsAfter span emits one space"


def test_char_split_spans_have_no_spurious_spaces(tmp_path: Path) -> None:
    """Per-character spans have NO whitespace nodes between them (no wsAfter),
    so no spaces are injected ('Hello', not 'H e l l o')."""
    ref, impl = _ref_with(tmp_path, {"tag": "h2", "children": [
        {"tag": "span", "text": "H"}, {"tag": "span", "text": "i"},
    ]})
    blob = _run(ref, impl)
    assert "{' '}" not in blob, "char-split spans must not get injected spaces"


def test_animated_hidden_opacity_is_reset(tmp_path: Path) -> None:
    """opacity:0 captured before a fade-in reveal -> reset so content is visible."""
    ref, impl = _ref_with(tmp_path, {"tag": "div", "text": "reveal me", "styles": {
        "opacity": "0", "animation-name": "fadeUp",
    }})
    blob = _run(ref, impl)
    assert 'opacity: "0"' not in blob, "hidden animation-state opacity must be reset to visible"
