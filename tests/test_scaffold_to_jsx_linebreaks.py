from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def test_newline_in_text_becomes_br(tmp_path: Path) -> None:
    """Text captured with a line break (\\n, from a <br> in the ref) must render
    as a real <br /> so multi-line copy is not run together
    ('America is sick.The data is clear.')."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "children": [
                {"tag": "p", "text": "America is sick.\nThe data is clear."},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = "".join(
        p.read_text() for p in (impl / "src" / "components").glob("*.tsx"))
    assert "America is sick." in blob and "The data is clear." in blob
    assert "<br" in blob, "captured line break must render as <br />, not run together"
