from __future__ import annotations

from ui_clone import measure


def test_locked_defaults_exposed_for_audit() -> None:
    """The LOCKED_DEFAULTS dict must be importable so tooling/docs can
    surface which env vars are pinned without parsing source.
    """
    assert "EXCLUDE_DYNAMIC" in measure.LOCKED_DEFAULTS
    assert measure.LOCKED_DEFAULTS["EXCLUDE_DYNAMIC"] == "1"
    assert "SECTION_THRESHOLD" in measure.LOCKED_DEFAULTS
    assert measure.LOCKED_DEFAULTS["SECTION_THRESHOLD"] == "2000"

