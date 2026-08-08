"""scaffold-to-jsx preserves a captured height:0 collapse (navercorp C1 +54px).

A visually-hidden skip-nav ul collapses to height:0 in the live page (its links
are position:absolute off-screen, so the ul has no in-flow content height). If
the transpiler converts that 0 height to a min-height:0 floor (the growable-
content path), the empty <li> line-boxes render at content height and push the
whole page down — navercorp's ul.skip rendered 54px tall, offsetting every
section +54 and saturating section-compare AE. The captured height:0 must be
emitted as a hard height:0 so the box collapses exactly as the reference frame.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def _run(tmp_path: Path, section: dict) -> str:
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(
        json.dumps({"tag": "body", "class": "", "display": "block",
                    "styles": {}, "children": [section]}),
        encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": section["tag"], "cls": section["class"]}]}),
        encoding="utf-8")
    (impl / "package.json").write_text('{"name":"i","dependencies":{}}', encoding="utf-8")
    proc = subprocess.run(["bash", str(SCRIPT), str(ref), str(impl)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in sorted((impl / "src" / "components").glob("*.tsx")))


def test_zero_height_collapse_emitted_hard(tmp_path: Path) -> None:
    # a skip-nav ul: captured height:0, an in-flow li child with an absolute link.
    blob = _run(tmp_path, {
        "tag": "ul", "class": "skip", "display": "block",
        "styles": {"height": "0px", "width": "1440px"},
        "children": [
            {"tag": "li", "class": "", "display": "list-item",
             "styles": {"height": "0px"},
             "children": [{"tag": "a", "class": "", "display": "block",
                           "styles": {"position": "absolute", "top": "-100px"},
                           "text": "Skip to content"}]},
        ],
    })
    # the ul keeps a hard height:0, NOT a min-height:0 floor (which would let the
    # li line-boxes push the page down).
    assert 'height: "0px"' in blob, "captured height:0 must be emitted as a hard height:0"
    assert 'minHeight: "0px"' not in blob, "height:0 must NOT be converted to a min-height floor"


def test_nonzero_height_still_unfreezes_to_minheight(tmp_path: Path) -> None:
    # regression guard: a growable non-zero height still converts to min-height
    # (the Fix 20/21 unfreeze) so text can reflow taller without clipping.
    blob = _run(tmp_path, {
        "tag": "div", "class": "hero", "display": "block",
        "styles": {"height": "500px"},
        "children": [{"tag": "h1", "text": "Title"}],
    })
    assert 'minHeight: "500px"' in blob
    assert 'height: "500px"' not in blob
