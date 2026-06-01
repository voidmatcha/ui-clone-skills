from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "extract" / "extract-section-html.sh"

# DOM stub that drives the real extraction IIFE through Node. The sub-pixel
# geometry below is the point: a faithful extractor must NOT discard the
# fractional part (a token-system width of 199.84px is real, not 200px).
DOM_STUB = r"""
const STYLE = {
  display: "block", position: "static",
  fontSize: "16px", fontWeight: "400", fontFamily: "Arial",
  color: "rgb(0,0,0)", backgroundColor: "rgba(0,0,0,0)",
  padding: "0px", margin: "0px", borderRadius: "0px", border: "none",
  backdropFilter: "none", overflow: "visible", opacity: "1",
  flexDirection: "row", justifyContent: "flex-start", alignItems: "stretch",
  gap: "0px", gridTemplateColumns: "none", transform: "none",
  backgroundImage: "none",
};

function makeEl(tag, cls, rect, children, text) {
  return {
    tagName: tag,
    id: "",
    className: cls,
    textContent: text || "",
    children: children || [],
    getBoundingClientRect: () => rect,
    querySelectorAll: () => [],
  };
}

const child = makeEl("DIV", "box",
  { width: 199.84, height: 40.4, left: 12.34, top: 50.6 }, [], "Child");
const section = makeEl("SECTION", "hero",
  { width: 1200.4, height: 600.84, left: 0.3, top: 100.84 }, [child], "Hero");

global.window = { scrollY: 0 };
global.getComputedStyle = () => STYLE;
global.document = {
  querySelectorAll: () => [section],
  querySelector: () => null,
  body: { children: [] },
};
"""


def _extract_iife(script_text: str) -> str:
    marker = 'eval "'
    start = script_text.index(marker) + len(marker)
    end = script_text.index('" 2>/dev/null || echo "[]")', start)
    return script_text[start:end]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_extract_section_html_keeps_subpixel_geometry(tmp_path: Path) -> None:
    iife = _extract_iife(SCRIPT.read_text(encoding="utf-8"))

    harness = tmp_path / "harness.js"
    harness.write_text(DOM_STUB + "\nconsole.log(" + iife + ");\n", encoding="utf-8")

    proc = subprocess.run(
        ["node", str(harness)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    sections = json.loads(proc.stdout.strip())
    assert sections, f"expected one section, got: {proc.stdout!r}"
    sec = sections[0]

    # Section rect must retain sub-pixel precision, not be integer-rounded.
    rect = sec["rect"]
    assert rect["width"] == pytest.approx(1200.4), rect
    assert rect["height"] == pytest.approx(600.84), rect
    assert rect["top"] == pytest.approx(100.84), rect

    # Per-element geometry (section + child) must retain decimals too.
    sstyles = sec["section"]["styles"]
    assert sstyles["width"] == pytest.approx(1200.4), sstyles
    assert sstyles["y"] == pytest.approx(100.84), sstyles

    child = sec["children"][0]["styles"]
    assert child["width"] == pytest.approx(199.84), child
    assert child["x"] == pytest.approx(12.34), child
    assert child["y"] == pytest.approx(50.6), child
