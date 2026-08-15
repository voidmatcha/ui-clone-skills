"""Regression: trajectory-compare masks time-coupled dynamic content.

loop-e2e-5: all 5 trajectory points exceeded the AE ceiling on a clone whose
masked section-compare was 14/14 PASS — video frames and timer carousels
diverge by PHASE at any matched scroll fraction. The trajectory probe must
mask the same dynamic families section-compare masks (base media tags +
transition-spec dynamic:true targets), identically on both sides.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "transition-trajectory-compare.sh"


def test_mask_block_present_and_two_sided() -> None:
    code = SCRIPT.read_text(encoding="utf-8")
    assert "__traj-mask__" in code
    assert code.count('eval "$MASK_JS"') == 2, "mask must be injected in BOTH sessions"
    for selector in ("canvas", "video", "iframe", "nav"):
        assert selector in code
    assert '[class*=\\"slideshow\\"]' in code
    assert '[class*=\\"carousel\\"]' in code
    assert 'section:has(canvas)' in code
    assert 'img:has(+ video)' in code
    assert 'video + img' in code


def test_dynamic_targets_derived_from_spec(tmp_path: Path) -> None:
    code = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"EXTRA_DYN=\$\(python3 - .*?<<'PY'.*?\n(.*?)\nPY\n", code, re.S)
    assert m, "dynamic-target derivation heredoc not found"
    spec = {"transitions": [
        {"id": "a", "target": ".dga_hero_video__SoTy9 video", "dynamic": True},
        {"id": "b", "target": ".static_thing__x", "dynamic": False},
    ]}
    p = tmp_path / "transition-spec.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-c", m.group(1), str(p)],
        capture_output=True, text=True, check=True,
    )
    out = proc.stdout.strip()
    assert ".dga_hero_video__SoTy9 video" in out
    assert ".static_thing__x" not in out
