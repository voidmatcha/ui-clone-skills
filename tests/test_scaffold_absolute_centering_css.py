"""Release captured absolute centering insets to mirrored CSS.

getComputedStyle resolves an authored `top:50%;left:50%;transform:translate(-50%,-50%)`
centered absolute element to viewport-specific px insets plus a matrix
translation. Baking those inline wins over the mirrored CSS and freezes the
element at the capture viewport. When the ref CSS declares the centering trio,
the scaffold must release the captured insets/transform unless they were
author-inline on the source element.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def _emit(tmp_path: Path, *, inline_props: list[str] | None = None) -> str:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "css").mkdir()
    (ref / "css" / "main.css").write_text(
        ".style_imgWrapper__AFuB_{position:absolute;top:50%;left:50%;"
        "transform:translate(-50%,-50%)}",
        encoding="utf-8",
    )
    structure = {
        "tag": "body",
        "styles": {"width": "1440px"},
        "children": [
            {
                "tag": "section",
                "class": "hero",
                "styles": {"position": "relative"},
                "children": [
                    {
                        "tag": "div",
                        "class": "style_imgWrapper__AFuB_",
                        "inlineProps": inline_props or [],
                        "styles": {
                            "position": "absolute",
                            "top": "420px",
                            "left": "720px",
                            "right": "592px",
                            "bottom": "416px",
                            "width": "128px",
                            "height": "64px",
                            "transform": "matrix(1, 0, 0, 1, -64, -32)",
                        },
                    }
                ],
            }
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((impl / "src" / "components").glob("*.tsx"))
    )


def test_absolute_centering_insets_and_matrix_released_to_css(tmp_path: Path) -> None:
    blob = _emit(tmp_path)
    line = next(line for line in blob.splitlines() if "style_imgWrapper__AFuB_" in line)
    assert 'position: "absolute"' in line, line
    assert 'width: "128px"' in line, line
    assert 'height: "64px"' in line, line
    assert "top:" not in line, line
    assert "left:" not in line, line
    assert "right:" not in line, line
    assert "bottom:" not in line, line
    assert "transform:" not in line, line


def test_absolute_centering_author_inline_props_preserved(tmp_path: Path) -> None:
    blob = _emit(tmp_path, inline_props=["top", "left", "transform"])
    line = next(line for line in blob.splitlines() if "style_imgWrapper__AFuB_" in line)
    assert 'top: "420px"' in line, line
    assert 'left: "720px"' in line, line
    assert 'transform: "matrix(1, 0, 0, 1, -64, -32)"' in line, line
    assert "right:" not in line, line
    assert "bottom:" not in line, line
