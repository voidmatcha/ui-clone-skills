"""Regression: hover-state-compare must honour `affectedTargetAbsent`.

The live-capture bridge records `affectedTargetAbsent` (in place of
`affectedTarget`) when a hover rule's descendant is rendered nowhere in the
reference document and the activation was measured in its own right.
`affected_selector_for_hover` used to ignore that field, fall through to
`hover-css-rules.json`, and re-derive the absent descendant — so
`video-transition-compare` compared a target that is not in the document,
reported `no-affected-match`, and the real activation rule went unmeasured.

The function is exercised as bash, exactly as the script calls it, with the
environment it reads (`REF_DIR`, `HOVER_CSS`).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"

ACTIVATION = ".header .nav__link"
DESCENDANT = ".header .nav__link .en"
HOVER_CSS = {
    "rules": [
        {
            "selector": ".header .nav__link:hover .en",
            "activation": ACTIVATION,
            "affected": DESCENDANT,
        }
    ]
}


def _function_source() -> str:
    code = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"^affected_selector_for_hover\(\) \{.*?^\}\n", code, re.S | re.M)
    assert m, "affected_selector_for_hover not found in hover-state-compare.sh"
    return m.group(0)


def _affected_selector(tmp_path: Path, spec: dict, activation: str = ACTIVATION) -> tuple[str, int]:
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "transition-spec.json").write_text(json.dumps(spec), encoding="utf-8")
    hover_css = ref_dir / "hover-css-rules.json"
    hover_css.write_text(json.dumps(HOVER_CSS), encoding="utf-8")
    proc = subprocess.run(
        ["bash", "-c", _function_source() + '\naffected_selector_for_hover "$1"', "_", activation],
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "REF_DIR": str(ref_dir),
            "HOVER_CSS": str(hover_css),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.stdout.strip(), proc.returncode


def test_affected_target_absent_suppresses_hover_css_rederivation(tmp_path: Path) -> None:
    """The measured-absent descendant must not be re-derived from hover-css-rules."""
    spec = {
        "transitions": [
            {
                "id": "hover-nav-link",
                "trigger": "hover",
                "target": ACTIVATION,
                "affectedTargetAbsent": DESCENDANT,
            }
        ]
    }
    stdout, code = _affected_selector(tmp_path, spec)
    assert code == 0
    assert stdout == "", f"absent descendant was re-derived: {stdout!r}"


def test_affected_target_is_still_honoured(tmp_path: Path) -> None:
    """Control: a pinned descendant is still the measurement scope."""
    spec = {
        "transitions": [
            {
                "id": "hover-nav-link",
                "trigger": "hover",
                "target": ACTIVATION,
                "affectedTarget": DESCENDANT,
            }
        ]
    }
    stdout, code = _affected_selector(tmp_path, spec)
    assert code == 0
    assert stdout == DESCENDANT


def test_no_spec_verdict_still_falls_back_to_hover_css_rules(tmp_path: Path) -> None:
    """Control: an entry that says nothing about the descendant keeps the fallback."""
    spec = {"transitions": [{"id": "hover-nav-link", "trigger": "hover", "target": ACTIVATION}]}
    stdout, code = _affected_selector(tmp_path, spec)
    assert code == 0
    assert stdout == DESCENDANT
