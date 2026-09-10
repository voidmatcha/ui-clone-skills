"""Run the real substitution setup without starting browser comparisons."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/visual-debug/scripts/section-compare.sh"


def _run(tmp_path: Path, substitution: dict, *, paid: bool = True,
         viewport: bool = False, local_paid: bool | None = None) -> str:
    root = tmp_path / "ref"
    root.mkdir()
    target = root / "viewports/390x844" if viewport else root
    target.mkdir(parents=True, exist_ok=True)
    (root / "asset-substitution.json").write_text(json.dumps(substitution))
    (root / "paid-features.json").write_text(json.dumps({"paidFonts": ["Exat"] if paid else []}))
    if local_paid is not None:
        (target / "paid-features.json").write_text(json.dumps({"paidFonts": ["Exat"] if local_paid else []}))
    source = SCRIPT.read_text()
    block = source.split('# ── Asset substitution mode ──', 1)[1].split(
        '# Motion-critical sections cannot', 1)[0]
    result = subprocess.run(
        ["bash", "-c", block + '\nprintf "RESULT:%s|%s|%s\\n" "$SUBSTITUTION_PATTERNS" "$SUBSTITUTION_ALL" "$SUBSTITUTION_AUTO"'],
        env={**os.environ, "DIR": str(target), "REF_ROOT_DIR": str(root)},
        text=True, capture_output=True, timeout=10, check=True,
    )
    return result.stdout


@pytest.mark.parametrize("value", [[], None, "*"])
def test_explicit_empty_or_invalid_patterns_never_enable_wildcard(tmp_path: Path, value: object) -> None:
    out = _run(tmp_path, {"fonts": [{"replacement": "Inter"}], "structuralOnlySections": value})
    assert "RESULT:|0|0" in out
    assert "MISSING" not in out


def test_missing_patterns_retains_paid_legacy_fallback(tmp_path: Path) -> None:
    out = _run(tmp_path, {"fonts": [{"replacement": "Inter"}]})
    assert "RESULT:*|1|1" in out
    assert "MISSING" in out


def test_explicit_sections_remain_scoped_without_paid_evidence(tmp_path: Path) -> None:
    out = _run(tmp_path, {"fonts": [{}], "structuralOnlySections": ["hero", "resources"]}, paid=False)
    assert "RESULT:hero resources|0|0" in out


def test_viewport_uses_root_paid_evidence(tmp_path: Path) -> None:
    out = _run(tmp_path, {"structuralOnlySections": ["*"]}, viewport=True)
    assert "RESULT:*|1|0" in out
    assert "REJECTED" not in out


def test_viewport_local_paid_evidence_takes_precedence(tmp_path: Path) -> None:
    out = _run(tmp_path, {"structuralOnlySections": ["*"]}, viewport=True, local_paid=False)
    assert "RESULT:|0|0" in out
    assert "REJECTED" in out


def test_missing_patterns_cannot_bypass_paid_evidence(tmp_path: Path) -> None:
    out = _run(tmp_path, {"fonts": [{}]}, paid=False)
    assert "RESULT:|0|1" in out
    assert "REJECTED" in out
