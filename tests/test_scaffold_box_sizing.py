"""scaffold-to-jsx box-sizing fidelity (navercorp B1).

A captured px `height` came from getComputedStyle on a border-box element, so the
padding lives INSIDE the height. Re-emitting that height under the content-box
default adds the padding on top, inflating every padded section by padT+padB
(B1: each navercorp section grew by exactly its vertical padding). extract-dom now
captures box-sizing, and the transpiler emits it faithfully for BOTH border-box
and content-box sources. It does NOT guess: a pre-fix capture that lacks box-sizing
is left as-is (assuming border-box would shrink a real content-box element — codex).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def _run(tmp_path: Path, sections: list[dict]) -> str:
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    children = [{"tag": "section", "class": s["cls"], "styles": s["styles"],
                 "children": [{"tag": "p", "text": s["cls"]}]} for s in sections]
    (ref / "structure.json").write_text(
        json.dumps({"tag": "body", "class": "", "styles": {}, "children": children}),
        encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": i, "tag": "section", "cls": s["cls"]} for i, s in enumerate(sections)]}),
        encoding="utf-8")
    (impl / "package.json").write_text('{"name":"i","dependencies":{}}', encoding="utf-8")
    proc = subprocess.run(["bash", str(SCRIPT), str(ref), str(impl)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in sorted((impl / "src" / "components").glob("*.tsx")))


def test_captured_box_sizing_emitted_faithfully(tmp_path: Path) -> None:
    blob = _run(tmp_path, [
        {"cls": "hero", "styles": {"height": "500px", "padding": "50px",
                                   "box-sizing": "border-box"}},
    ])
    assert 'boxSizing: "border-box"' in blob


def test_captured_content_box_emitted_faithfully(tmp_path: Path) -> None:
    # a captured content-box element is emitted as content-box (its captured
    # height is the CONTENT height; padding is added on top → correct outer box).
    blob = _run(tmp_path, [
        {"cls": "cbox", "styles": {"height": "300px", "padding": "20px",
                                   "box-sizing": "content-box"}},
    ])
    assert 'boxSizing: "content-box"' in blob


def test_legacy_capture_without_box_sizing_not_forced(tmp_path: Path) -> None:
    # codex regression guard: a pre-fix capture (no box-sizing) with a px height +
    # padding must NOT be forced to border-box — the source sizing mode is unknown
    # and assuming border-box would shrink a genuine content-box element. The fix
    # is a fresh capture (which records box-sizing), not a transpile-side guess.
    blob = _run(tmp_path, [
        {"cls": "legacy", "styles": {"height": "400px", "padding": "40px"}},
    ])
    assert "boxSizing" not in blob
