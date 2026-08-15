"""Regression coverage for scroll-range drift during trajectory capture."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "transition-trajectory-compare.sh"


def test_trajectory_realigns_fraction_after_layout_settles() -> None:
    """Lazy layout and scroll anchoring can move the achieved sample point.

    Each side must be scrolled once to activate layout, then have its live
    range re-read and the requested fraction re-applied before capture.
    """
    source = SCRIPT.read_text(encoding="utf-8")

    assert "settle_scroll_fraction()" in source
    helper = source.split("settle_scroll_fraction()", 1)[1].split("\n}", 1)[0]
    assert "for _pass in 1 2" in helper
    assert helper.count("scrollHeight - window.innerHeight") == 2
    assert helper.count("window.scrollTo") == 2
    assert source.count('settle_scroll_fraction "$pct"') == 2
