from __future__ import annotations

import json
import subprocess

from ._helpers import (
    _project_root,
)


def test_fix19_extract_dom_captures_hover_styles() -> None:
    """Fix 19 — extract-dom.sh must walk document.styleSheets and collect
    :hover declarations matching each element's full selector,
    so the transpiler can emit CSS that gives Fix 16's captured transition
    something to animate to. Without this the impl emits inline transitions
    that never trigger because no hover-state CSS exists.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lib" / "extract-dom.js"
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
    assert "el.matches(r.matchSelector)" in text, (
        "hover extraction must preserve attributes and selector structure; "
        "matching only the first class contaminates component variants"
    )
    assert "splitSelectorList" in text
    assert "walkRules(rule.cssRules)" in text


def test_extract_dom_preserves_cssom_double_colon_pseudo_hover_selectors() -> None:
    """CSSOM often serializes legacy ``:before`` rules as ``::before``.

    Normalizing with a bare ``/:before/`` replacement turns that into the
    invalid ``:::before`` selector, so pseudo hover endpoints silently vanish
    from ``structure.json``.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lib" / "extract-dom.js"
    text = script.read_text(encoding="utf-8")

    assert ".replace(/:{1,2}before\\b/g, '::before')" in text
    assert ".replace(/:{1,2}after\\b/g, '::after')" in text


def test_extract_dom_materializes_double_colon_pseudo_hover_endpoints() -> None:
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lib" / "extract-dom.js"
    source = script.read_text(encoding="utf-8").replace(
        "SELECTOR_PLACEHOLDER", json.dumps("#target"), 1
    )
    harness = r"""
const makeComputedStyle = (pseudo) => new Proxy({
  getPropertyValue(prop) {
    if (prop === 'content') return pseudo ? 'none' : '';
    if (prop === 'display') return 'block';
    if (prop === 'position') return 'static';
    return '';
  },
}, {
  get(target, prop) {
    if (prop in target) return target[prop];
    if (prop === 'display') return 'block';
    if (prop === 'position') return 'static';
    return '';
  },
});
const target = {
  tagName: 'A',
  className: 'social__link',
  childNodes: [],
  children: [],
  textContent: '',
  style: { length: 0, getPropertyValue() { return ''; } },
  attributes: [],
  matches(selector) { return selector === '.social__link:is(*)'; },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  getAttribute() { return null; },
  getBoundingClientRect() { return { width: 36, height: 36 }; },
};
const hoverRule = (selectorText, opacity) => ({
  selectorText,
  style: {
    getPropertyValue(prop) { return prop === 'opacity' ? opacity : ''; },
    getPropertyPriority() { return ''; },
  },
});
global.window = { matchMedia() { return { matches: true }; } };
global.document = {
  querySelector(selector) { return selector === '#target' ? target : null; },
  styleSheets: [{ cssRules: [
    hoverRule('.social__link:hover::before', '0'),
    hoverRule('.social__link:hover::after', '1'),
  ] }],
};
global.getComputedStyle = (_el, pseudo) => makeComputedStyle(Boolean(pseudo));
global.CSS = { supports() { return true; } };
"""
    proc = subprocess.run(
        ["node"],
        input=f"{harness}\nconst result = {source};\nprocess.stdout.write(result);\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["before_hover_styles"]["opacity"] == "0"
    assert payload["after_hover_styles"]["opacity"] == "1"



def test_fix19_scaffold_to_jsx_emits_hover_rules() -> None:
    """Fix 19 — scaffold-to-jsx.sh must turn hover_styles into a CSS rule
    via an auto-generated class id (h_N) appended to the node's className,
    plus a <style> block at the top of the component body so :hover works
    at runtime.
    """
    helper = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "lib"
        / "scaffold_to_jsx.py"
    )
    text = helper.read_text(encoding="utf-8")
    assert "hover_rules" in text, (
        "scaffold_to_jsx.py must thread a hover_rules collector through render() (Fix 19)"
    )
    assert 'selector = f".{hov_id}:hover"' in text, (
        "standard hover rules must target the generated h_<index> class"
    )
    assert 'css_parts.append(f"{selector} {{ {decl_text} }}")' in text, (
        "the generated hover selector must be emitted with its declarations"
    )
    assert "h_" in text, "auto-generated class ids must use h_<index> form"
