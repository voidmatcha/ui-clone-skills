"""Transpiler must emit interleaved text nodes in DOM order (defect #1).

A node whose direct text nodes are INTERLEAVED between inline element children
(e.g. navercorp's stock ticker `<span class="num-info">(<span class="blind">
상승</span><span class="num">6,900</span> <span class="percent">3.74%</span>)
</span>`) loses text-node POSITION at capture: extract-dom's directText joins
the direct text nodes into a single `text` ("()" — the two parens) and stores
the live-rendered order as `textFull` ("상승 6,900 (3.74%)"). The text-fidelity
GATE already keys on `textFull` and its faithful-impl reference renders the
fragments in DOM order (`treating <span>chronic disease</span>—much`) — but the
scaffold-to-jsx `render()` itself IGNORES `textFull` and HOISTS the merged
`text` to a single leading node before all children, emitting the parens as a
"()" prefix instead of wrapping `.percent`.

This is the transpiler side of the mid-text-span family (loop-e2e-9). The
correct reusable fix (option B) is to capture text-node POSITIONS in extract-dom
(emit them as ordered pseudo-children) and have render() emit them interleaved.
A simpler "drop the merged text when textFull present" is WRONG: it silently
deletes substantive copy in the general case ("treating —much" carries real
words, not just decorative punctuation).

Option B landed (charter execution after the nvti/ebpb campaign): render()
now interleaves via capture-side textSeq when present, falling back to
textFull alignment for older captures, with legacy hoist as the ambiguity
fallback. The xfail branch below is retained as a fixture-drift guard but the
desired-order assertion is now the live contract.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def _scaffold_component(tmp_path: Path, structure: dict, sections: list[str],
                        needle: str) -> str:
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps(structure, ensure_ascii=False),
                                        encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": i, "tag": "section", "cls": s} for i, s in enumerate(sections)]}),
        encoding="utf-8")
    (impl / "package.json").write_text('{"name":"i","dependencies":{}}', encoding="utf-8")
    proc = subprocess.run(["bash", str(SCRIPT), str(ref), str(impl)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for tsx in (impl / "src").rglob("*.tsx"):
        body = tsx.read_text(encoding="utf-8")
        if needle in body:
            return body
    raise AssertionError(f"no component contained {needle!r}")


_TICKER = {
    "tag": "body", "class": "", "styles": {"width": "1440px"}, "children": [
        {"tag": "section", "class": "s-tick", "styles": {}, "children": [
            {"tag": "span", "class": "num-info", "text": "()",
             "textFull": "상승 6,900 (3.74%)", "styles": {}, "children": [
                 {"tag": "span", "class": "blind", "text": "상승", "children": []},
                 {"tag": "span", "class": "num", "text": "6,900",
                  "styles": {"margin": "0px 4px 0px 0px"}, "children": []},
                 {"tag": "span", "class": "percent", "text": "3.74%", "children": []},
             ]},
        ]},
    ],
}


def test_interleaved_text_not_hoisted_before_children(tmp_path: Path) -> None:
    # Setup runs OUTSIDE the xfail: a scaffold error, a missing num-info
    # component, or a stale fixture must surface as a REAL failure, not be masked
    # as XFAIL (codex).
    comp = _scaffold_component(tmp_path, _TICKER, ["s-tick"], "num-info")
    # Desired (option B): the parens render AROUND `.percent` in DOM order —
    #   <span className="num">6,900</span>(<span className="percent">3.74%</span>)
    # Asserting the required ORDER (not merely the absence of the old "()"-prefix
    # shape) locks the documented behavior: the rejected "drop the merged text"
    # shortcut would also remove the "()" prefix but LOSE the parens, and must not
    # make this test pass (codex).
    # (?:\s|\{' '\})* — option-B emits DOM whitespace as explicit {' '}
    # expressions (JSX collapses bare inter-element whitespace), so the space
    # between 6,900 and the paren legitimately renders as {' '}( .
    _gap = r"(?:\s|\{' '\})*"
    desired = re.search(
        r'<span className="num"[^>]*>6,900</span>' + _gap +
        r'\(' + _gap + r'<span className="percent">3\.74%</span>' + _gap + r'\)',
        comp)
    if desired is None:
        # Current transpiler hoists the merged "()" before children. Confirm it is
        # exactly that known mis-ordered shape (else the fixture drifted → real
        # failure), then xfail only the fidelity gap until option-B lands.
        hoisted = re.search(r'className="num-info"[^>]*>\s*\(\)\s*\n\s*<span', comp)
        assert hoisted is not None, (
            "interleaved text is neither correctly ordered nor in the known "
            "mis-ordered '()'-prefix shape — fixture may be stale:\n" + comp)
        pytest.xfail("#1 option-B not implemented: render() hoists merged "
                     "interleaved text ('()') before children instead of emitting "
                     "the text nodes in DOM position around .percent")
    # option B implemented — lock the interleaved order so it cannot regress.
    assert desired, comp


def test_text_seq_preferred_over_textfull_alignment(tmp_path: Path) -> None:
    # Capture-side textSeq is EXACT (no alignment heuristics) and must win.
    # Fixture: "treating <span>chronic disease</span>—much" (loop-e2e-9).
    structure = {
        "tag": "body", "class": "", "styles": {}, "children": [
            {"tag": "section", "class": "s-mid", "styles": {}, "children": [
                {"tag": "p", "class": "mid-text", "text": "treating —much",
                 "textFull": "treating chronic disease—much",
                 "textSeq": ["treating ", 0, "—much"],
                 "styles": {}, "children": [
                     {"tag": "span", "class": "em", "text": "chronic disease",
                      "children": []},
                 ]},
            ]},
        ],
    }
    comp = _scaffold_component(tmp_path, structure, ["s-mid"], "mid-text")
    gap = r"(?:\s|\{' '\})*"
    assert re.search(
        r"treating" + gap +
        r'<span className="em">chronic disease</span>' + gap + r"—much",
        comp), comp
    # The old hoisted shape ("treating —much" merged before the span) is gone.
    assert "treating —much" not in comp, comp


def test_ambiguous_alignment_falls_back_to_legacy_hoist(tmp_path: Path) -> None:
    # No textSeq and a child text that does NOT appear in textFull → alignment
    # must refuse (return None) and the legacy hoist emit, never text loss.
    structure = {
        "tag": "body", "class": "", "styles": {}, "children": [
            {"tag": "section", "class": "s-amb", "styles": {}, "children": [
                {"tag": "span", "class": "amb", "text": "()",
                 "textFull": "totally different rendered text",
                 "styles": {}, "children": [
                     {"tag": "span", "class": "inner", "text": "6,900",
                      "children": []},
                 ]},
            ]},
        ],
    }
    comp = _scaffold_component(tmp_path, structure, ["s-amb"], "amb")
    # Legacy shape: merged text first, then the child — both still present.
    assert "()" in comp, comp
    assert 'className="inner"' in comp, comp
    assert comp.index("()") < comp.index('className="inner"'), comp


def test_extract_dom_captures_text_seq() -> None:
    # Capture-side lockstep: extract-dom must build textSeq (strings +
    # child indexes) and store it only when the element-child walk matched
    # children 1:1 (the ei === kids.length guard).
    body = (ROOT / "skills" / "visual-debug" / "scripts" / "lib" /
            "extract-dom.js").read_text(encoding="utf-8")
    assert "out.textSeq = seq" in body
    assert "ei === kids.length" in body
