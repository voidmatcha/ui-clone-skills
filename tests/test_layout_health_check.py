from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAYOUT_HEALTH_CHECK = ROOT / "skills" / "visual-debug" / "scripts" / "layout-health-check.sh"


def test_layout_health_check_detects_desktop_horizontal_overflow() -> None:
    script = LAYOUT_HEALTH_CHECK.read_text(encoding="utf-8")

    assert "scrollWidth" in script
    assert "overflowPx" in script
    assert "HORIZONTAL OVERFLOW" in script


def test_layout_health_check_compares_overflow_against_reference_tolerance() -> None:
    script = LAYOUT_HEALTH_CHECK.read_text(encoding="utf-8")

    assert "origOverflow" in script
    assert "implOverflow" in script
    assert "origOverflow + 4" in script
