"""The compact-spec jq recipe in component-generation.md must only drop a style
key when its value equals that property's CSS initial value. Dropping by value
alone turned `display: none` and `top: 0px` into "missing = initial", which
told the generator hidden or pinned elements were visible and unpositioned."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "skills" / "ui-reverse-engineering" / "component-generation.md"


def _recipe() -> str:
    match = re.search(r"jq -c '(def lean:.*?)' \\\n", DOC.read_text(encoding="utf-8"), re.S)
    assert match, "compact-spec jq recipe not found in component-generation.md"
    return match.group(1)


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
def test_lean_recipe_keeps_non_initial_values_and_truncates_data_uris(tmp_path: Path) -> None:
    section = {
        "section": {"styles": {"display": "none", "top": "0px", "margin": "0px", "gap": "normal"}},
        "children": [
            {
                "text": "Hello",
                "styles": {
                    "pointerEvents": "none",
                    "color": "rgba(0, 0, 0, 0)",
                    "backgroundColor": "rgba(0, 0, 0, 0)",
                    "transform": "none",
                },
            }
        ],
        "media": [{"src": "data:image/png;base64," + "A" * 500}, {"src": "/hero.jpg"}],
    }
    path = tmp_path / "section.json"
    path.write_text(json.dumps(section), encoding="utf-8")

    out = json.loads(
        subprocess.run(
            ["jq", "-c", _recipe(), str(path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
    )

    assert out["section"]["styles"] == {"display": "none", "top": "0px"}
    assert out["children"][0]["styles"] == {"pointerEvents": "none", "color": "rgba(0, 0, 0, 0)"}
    assert out["children"][0]["text"] == "Hello"
    assert out["media"][0]["src"].endswith("chars)") and len(out["media"][0]["src"]) < 80
    assert out["media"][1]["src"] == "/hero.jpg"
