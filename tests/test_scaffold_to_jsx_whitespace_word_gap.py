from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def _run(ref: Path, impl: Path) -> str:
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return "".join(
        p.read_text() for p in (impl / "src" / "components").glob("*.tsx"))


def test_whitespace_only_text_node_keeps_its_space(tmp_path: Path) -> None:
    """A captured text node that is ONLY whitespace is the inter-word gap of a
    per-character split heading ('Real' ' ' 'Food' ' ' 'can').

    It loses that space twice: JSX trims a whitespace-only text child at the
    line boundary, and a `display:block` flex item collapses a lone
    leading/trailing space to zero advance width. Either failure renders
    'RealFoodcan'. The emit must survive both.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "children": [
                {"tag": "div", "class": "line", "styles": {"display": "flex"},
                 "children": [
                     {"tag": "span", "class": "w", "text": "Real"},
                     {"tag": "span", "styles": {"display": "block"},
                      "text": " "},
                     {"tag": "span", "class": "w", "text": "Food"},
                 ]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8")
    blob = _run(ref, impl)

    assert "Real" in blob and "Food" in blob
    # The gap span must not emit a bare whitespace child (JSX drops it).
    assert not re.search(r">\s</span>", blob), (
        "whitespace-only text child emitted verbatim — JSX trims it away, "
        "gluing the split words together")
    assert '{"\\u0020"}' in blob, (
        "gap span must emit the space as an escaped JS string expression")
    # And the space must not collapse inside the block box.
    assert "whiteSpace" in blob, (
        "gap span needs white-space:pre or the lone space collapses to 0px")


def test_nbsp_gap_survives_swc_whitespace_trim(tmp_path: Path) -> None:
    """The realfood split heading captures its word gaps as U+00A0. Next.js
    compiles JSX with SWC, whose whitespace trim follows Unicode White_Space
    and therefore eats an nbsp-only child just like a plain space. The gap must
    survive as an escaped expression, and keep its exact codepoint."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "children": [
                {"tag": "div", "class": "line", "styles": {"display": "flex"},
                 "children": [
                     {"tag": "span", "class": "w", "text": "Real"},
                     {"tag": "span", "styles": {"display": "block"},
                      "text": " "},
                     {"tag": "span", "class": "w", "text": "Food"},
                 ]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8")
    blob = _run(ref, impl)

    assert " </span>" not in blob, (
        "bare nbsp text child is trimmed away by SWC")
    assert '{"\\u00a0"}' in blob, "nbsp gap must keep its exact codepoint"
