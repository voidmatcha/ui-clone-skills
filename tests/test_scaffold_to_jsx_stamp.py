from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def test_emits_scaffold_base_stamp_and_text(tmp_path: Path) -> None:
    """The deterministic transpiler must write a proof-of-run stamp and emit
    the scaffold text verbatim so the generation flow can rely on it as the
    text-complete base."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(
        json.dumps({
            "tag": "body",
            "children": [
                {"tag": "section", "class": "hero", "children": [
                    {"tag": "h1", "text": "Real Food Wins"},
                    {"tag": "p", "text": "2 servings per day."},
                ]},
            ],
        }),
        encoding="utf-8",
    )
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    stamp_path = ref / "scaffold-base-stamp.json"
    assert stamp_path.is_file(), "scaffold-base-stamp.json must be written"
    stamp = json.loads(stamp_path.read_text())
    assert stamp["componentsWritten"] >= 1
    assert len(stamp["structureSha256"]) == 64
    blob = "".join(
        p.read_text() for p in (impl / "src" / "components").glob("*.tsx")
    )
    assert "Real Food Wins" in blob and "2 servings per day." in blob
