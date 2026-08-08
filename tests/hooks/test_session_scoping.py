"""Session-scoping for the Stop / declare-done gates (fix #2).

`should_enforce_ref_for_session` decides whether THIS hook session must enforce a
given active ref. The recurrence bug: with NO session id in the payload the gate
fell back to enforce-everything, so a completely unrelated tab was blocked by
every accumulated WIP ref. Fix: with no session id, keep the legacy fail-closed
behavior ONLY for refs with no session owner, but skip refs whose session
markers all belong to OTHER (identifiable) sessions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ui_clone.hooks._common import (
    mark_ref_session,
    ref_has_session_markers,
    should_enforce_ref_for_session,
)


def _ref(tmp_path: Path) -> Path:
    d = tmp_path / "ref"
    d.mkdir()
    return d


def test_no_sid_ref_owned_by_other_session_is_skipped(tmp_path: Path) -> None:
    ref = _ref(tmp_path)
    mark_ref_session(ref, "some-other-tab-session", source="pre_generate")
    assert ref_has_session_markers(ref) is True
    # No session id for THIS hook invocation → an unrelated tab must not be
    # forced to finish another tab's clone.
    assert should_enforce_ref_for_session(ref, "") is False


def test_no_sid_unowned_ref_still_enforced(tmp_path: Path) -> None:
    ref = _ref(tmp_path)
    # No session markers at all → legacy fail-closed preserved (could be this
    # session's own pre-session-id work; a real in-progress clone).
    assert should_enforce_ref_for_session(ref, "") is True


def test_no_sid_override_forces_enforcement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = _ref(tmp_path)
    mark_ref_session(ref, "other-session", source="pre_generate")
    monkeypatch.setenv("UI_RE_ENFORCE_UNOWNED_ACTIVE", "1")
    assert should_enforce_ref_for_session(ref, "") is True


def test_sid_touched_is_enforced(tmp_path: Path) -> None:
    ref = _ref(tmp_path)
    mark_ref_session(ref, "my-session", source="pre_generate")
    assert should_enforce_ref_for_session(ref, "my-session") is True


def test_sid_untouched_not_enforced_without_override(tmp_path: Path) -> None:
    ref = _ref(tmp_path)
    mark_ref_session(ref, "other-session", source="pre_generate")
    assert should_enforce_ref_for_session(ref, "my-session") is False
