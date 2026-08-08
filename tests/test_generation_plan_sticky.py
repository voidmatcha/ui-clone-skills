from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "extract" / "generation-plan.sh"


def test_sticky_strategy_populated_from_elements(tmp_path: Path) -> None:
    """sticky-elements.json is `{elements:[...]}` and entries key the class as
    `className`. generation-plan must populate stickyStrategy from it (it was
    treating the dict as a list and reading `cls`, so stickyStrategy stayed []
    and the agent improvised sticky onto whole sections)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    out = ref / "generation-plan.json"
    (ref / "structure.json").write_text(
        json.dumps({"tag": "body", "children": []}), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": []}), encoding="utf-8")
    (ref / "sticky-elements.json").write_text(json.dumps({"elements": [
        {"tag": "div", "className": "nav_nav__E77In", "position": "fixed",
         "top": "0px", "zIndex": "100000"},
    ]}), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref)],
        capture_output=True, text=True, timeout=120)
    assert out.is_file(), proc.stdout + proc.stderr
    plan = json.loads(out.read_text())
    ss = plan.get("stickyStrategy")
    assert ss, f"stickyStrategy must be populated from sticky-elements: {ss}"
    blob = json.dumps(ss)
    assert "nav_nav__E77In" in blob, f"selector must use className: {ss}"
    assert ss[0].get("position") == "fixed"
