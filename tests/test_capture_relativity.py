"""Capture-relativity regression suite — one canonical test per family.

A static capture freezes COMPUTED values; several classes of those values are
RELATIVE at author time and freezing them breaks the clone. Each family below
was found and fixed individually; this module is the coherence map plus one
end-to-end regression test per family so no future change silently regresses
another family. Behavior under test is unchanged — the helpers live where the
fixes put them (scope deliberately NOT expanded to %/calc inference):

  family                    helper / site                          fix
  ------------------------  -------------------------------------  ----
  derived page height       root_styles px strip (scaffold)         75
  vh-authored tracks        _vh_or_px (scaffold)                    80
  centering translate       _is_centering_transform (scaffold)      68
  scroll-state translation  _is_scroll_state_translation (scaffold) 21
  frozen text height        _height_should_unfreeze (scaffold)      20/21
  sticky-track overlap      _effective_flow_height (scaffold)       64
  svg dash draw state       stroke-draw stamping (scaffold)         76
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def _run(tmp_path: Path, structure: dict, extra: dict[str, dict] | None = None) -> str:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [{"index": 0, "tag": "section", "cls": "s0"}],
    }), encoding="utf-8")
    for name, payload in (extra or {}).items():
        (ref / name).write_text(json.dumps(payload), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = ""
    for p in (impl / "src").rglob("*.tsx"):
        out += p.read_text(encoding="utf-8")
    return out


def test_family_derived_page_height_not_frozen(tmp_path: Path) -> None:
    """Fix 75 — the body height is derived from content at capture time;
    freezing it pins docH and anchors height:100% descendants."""
    blob = _run(tmp_path, {
        "tag": "body", "styles": {"height": "20133.3px"},
        "children": [{"tag": "section", "class": "s0",
                      "children": [{"tag": "h1", "text": "Hi"}]}],
    })
    assert "20133" not in blob
    assert 'minHeight: "100vh"' in blob


def test_family_vh_track_rescaled(tmp_path: Path) -> None:
    """Fix 80 — vh-authored scroll tracks freeze as capture-viewport px and
    render the wrong height at any other viewport."""
    blob = _run(tmp_path, {
        "tag": "body",
        "children": [{"tag": "section", "class": "s0",
                      "styles": {"height": "1800px"},
                      "children": [{"tag": "h1", "text": "Hi"}]}],
    }, extra={"orig-layout.json": {"viewportHeight": 900, "viewportWidth": 1440}})
    assert '"200vh"' in blob, blob


def test_family_centering_translate_preserved(tmp_path: Path) -> None:
    """Fix 68 — translate(-50%,-50%) centering resolves to px matrix form and
    must not be stripped as a scroll state (half-size displacement)."""
    blob = _run(tmp_path, {
        "tag": "body",
        "children": [{"tag": "section", "class": "s0", "children": [
            {"tag": "img", "class": "glow", "src": "https://x.example/images/g.webp",
             "styles": {"position": "absolute", "width": "1282px", "height": "810px",
                        "transform": "matrix(1, 0, 0, 1, -641, -405)"}},
        ]}],
    })
    assert "matrix(1, 0, 0, 1, -641, -405)" in blob


def test_family_scroll_state_translation_stripped(tmp_path: Path) -> None:
    """Fix 21 — a large marker-less translate is a frozen mid-scroll state and
    must reset to rest (NOT centering: offsets don't match half the size)."""
    blob = _run(tmp_path, {
        "tag": "body",
        "children": [{"tag": "section", "class": "s0", "children": [
            {"tag": "div", "class": "reveal", "text": "Reveal",
             "styles": {"position": "absolute", "width": "400px", "height": "200px",
                        "transform": "matrix(1, 0, 0, 1, 0, 600)"}},
        ]}],
    })
    import re as _re
    rv = _re.search(r'className="reveal"[^>]*style=\{\{([^}]*)\}\}', blob)
    assert rv and "matrix" not in rv.group(1)


def test_family_text_height_floored_not_clamped(tmp_path: Path) -> None:
    """Fix 20/21 — frozen px heights on text-bearing elements clip reflowed
    text; they convert to a min-height floor."""
    blob = _run(tmp_path, {
        "tag": "body",
        "children": [{"tag": "section", "class": "s0", "children": [
            {"tag": "h1", "class": "title", "text": "A very long title",
             "styles": {"height": "120px"}},
        ]}],
    })
    import re as _re
    t = _re.search(r'className="title"[^>]*style=\{\{([^}]*)\}\}', blob)
    assert t and 'minHeight: "120px"' in t.group(1) and 'height: "120px"' not in t.group(1)


def test_family_sticky_overlap_preserved(tmp_path: Path) -> None:
    """S1 — a section root's negative bottom margin is a deliberate overlap onto
    the next section. It is flow-neutral (the box is M px taller; the negative
    margin pulls the next in-flow sibling up by exactly M), so the root keeps its
    FULL captured height as a min-height floor and preserves the negative bottom
    margin verbatim — it is NOT folded to H-M with margin-bottom zeroed (that
    rendered the box M px too short and erased the overlap). The Fix-26 sticky-
    ancestor WRAPPER still folds to H-M via _effective_flow_height (separate
    path; see test_sticky_wrapper_minheight_bakes_negative_bottom_margin)."""
    blob = _run(tmp_path, {
        "tag": "body",
        "children": [{"tag": "section", "class": "s0",
                      "styles": {"position": "relative", "height": "2700px",
                                 "margin": "0px 0px -675px"},
                      "children": [{"tag": "h2", "text": "Track"}]}],
    })
    import re as _re
    s = _re.search(r'className="s0"[^>]*style=\{\{([^}]*)\}\}', blob)
    assert s, f"s0 section must be emitted; got:\n{blob}"
    style = s.group(1)
    assert 'minHeight: "2700px"' in style, f"full captured height kept as floor; got:\n{style}"
    assert 'minHeight: "2025px"' not in style, "must not fold the overlap into the floor"
    assert 'margin: "0px 0px -675px"' in style, f"negative bottom margin preserved; got:\n{style}"
    assert 'marginBottom: "0px"' not in style, "must not neutralise the overlap margin"


def test_family_svg_dash_state_stamped(tmp_path: Path) -> None:
    """Fix 76 — dasharray-frozen draw paths are the JS-prepared INACTIVE state;
    spec-gated stamping hands them to the runtime driver."""
    blob = _run(tmp_path, {
        "tag": "body",
        "children": [{"tag": "section", "class": "s0", "children": [
            {"tag": "svg", "svg": True, "children": [
                {"tag": "path", "svg": True, "class": "draw", "d": "M0 0L9 9",
                 "stroke": "#111", "stroke-dasharray": "240"},
            ]},
        ]}],
    }, extra={
        "transition-spec.json": {"transitions": [
            {"id": "svg-stroke-draw", "trigger": "in-view / scroll state",
             "bundle_branch": "animate:{strokeDashoffset:t?0:o}",
             "animation": {"property": "strokeDashoffset", "from": "dashLength",
                           "to": 0, "duration": 1.0, "ease": "[0.25, 1, 0.5, 1]"}},
        ]},
        "generation-plan.json": {"smoothScroll": {"required": False, "config": {}},
                                 "scrollDriven": {"required": True, "hooks": []}},
    })
    assert "data-stroke-draw" in blob
