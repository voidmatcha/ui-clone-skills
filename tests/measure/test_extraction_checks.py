from __future__ import annotations

from ._helpers import (
    _project_root,
)


def test_dom_extraction_captures_direct_text() -> None:
    """Regression (Fix 6 v1): the DOM extraction eval in dom-extraction.md
    MUST capture each element's direct text (own text nodes, not descendants').
    Without `text` in the extracted schema, Phase 4 has no verbatim text to
    paste — agent fabricates from class names / URLs / asset filenames. The
    3-round benchmark showed Hero generated with "Eat Real Food" while the
    real ref hero said "Real Food Wins".
    """
    doc = _project_root() / "skills" / "ui-reverse-engineering" / "dom-extraction.md"
    text = doc.read_text(encoding="utf-8")

    # The direct-text helper that captures own-text without recursing into
    # descendants — keeps structure.json from exploding with duplicated text.
    assert "directText" in text, "dom-extraction.md must define directText helper"
    assert "nodeType === 3" in text, (
        "directText must filter to text nodes (nodeType === 3) to avoid "
        "capturing nested element duplicates"
    )
    # The extract function must populate `text` from the helper.
    assert "out.text = text" in text or "text: directText" in text, (
        "dom-extraction.md extract() must populate a `text` field on each node"
    )



def test_fix13_dom_extraction_captures_per_node_styles() -> None:
    """Fix 13 — dom-extraction.md JS eval must capture per-node computed
    styles (LAYOUT_PROPS subset). Without this the scaffold-to-jsx transpiler
    has no styling info per node, defeating the whole determinism strategy.
    """
    doc = _project_root() / "skills" / "ui-reverse-engineering" / "dom-extraction.md"
    text = doc.read_text(encoding="utf-8")
    assert "LAYOUT_PROPS" in text, (
        "dom-extraction.md must define LAYOUT_PROPS for per-node style capture"
    )
    # Critical style props that must be in the capture list.
    for prop in ('font-family', 'background-color', 'padding', 'color', 'font-size'):
        assert f"'{prop}'" in text, f"LAYOUT_PROPS must include {prop}"
    assert "out.styles = styles" in text, (
        "extract() must populate out.styles when at least one prop diverges from default"
    )

