from __future__ import annotations

from ._helpers import (
    _project_root,
)


def test_fix19_extract_dom_captures_hover_styles() -> None:
    """Fix 19 — extract-dom.sh must walk document.styleSheets and collect
    :hover (or :focus) declarations matching each element's class list,
    so the transpiler can emit CSS that gives Fix 16's captured transition
    something to animate to. Without this the impl emits inline transitions
    that never trigger because no hover-state CSS exists.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "extract-dom.sh"
    text = script.read_text(encoding="utf-8")
    assert "buildHoverRules" in text and "captureHover" in text, (
        "extract-dom.sh must define hover-rule helpers (Fix 19)"
    )
    assert "document.styleSheets" in text, (
        "captureHover must scan document.styleSheets"
    )
    assert "out.hover_styles" in text, (
        "extract() must attach hover_styles to each matching node"
    )



def test_fix19_scaffold_to_jsx_emits_hover_rules() -> None:
    """Fix 19 — scaffold-to-jsx.sh must turn hover_styles into a CSS rule
    via an auto-generated class id (h_N) appended to the node's className,
    plus a <style> block at the top of the component body so :hover works
    at runtime.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"
    text = script.read_text(encoding="utf-8")
    assert "hover_rules" in text, (
        "scaffold-to-jsx.sh must thread a hover_rules collector through render() (Fix 19)"
    )
    assert ":hover {" in text, (
        "the emitted CSS rule must include :hover selector"
    )
    assert "h_" in text, "auto-generated class ids must use h_<index> form"

