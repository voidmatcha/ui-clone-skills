"""Tests for ui_clone.driver_session — concurrent-safe .driver-session.id writer.

Single-writer assumption was the source of multiple-driver stomping (observed
2026-05-24: a second driver session overwrote the first's marker, breaking
the Stop-hook bypass). The Stop hook reader (`section_gate._is_driver_session`)
already supports newline-delimited sets; this module is the missing writer
that gives the marker append-if-missing semantics under a file lock.
"""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from ui_clone.driver_session import (
    MARKER_FILENAME,
    register,
)


def _read_marker(project_root: Path) -> list[str]:
    marker = project_root / MARKER_FILENAME
    if not marker.is_file():
        return []
    return [
        line.strip()
        for line in marker.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_register_creates_marker_with_single_id(tmp_path: Path) -> None:
    """Fresh repo (no existing marker) — register writes a one-line file."""
    register("session-alpha", project_root=tmp_path)
    assert _read_marker(tmp_path) == ["session-alpha"]


def test_register_appends_new_id_to_existing_marker(tmp_path: Path) -> None:
    """Existing marker with one ID + register a new ID → marker has both IDs.
    This is the core append-if-missing semantics the multi-driver fix needs."""
    (tmp_path / MARKER_FILENAME).write_text("session-alpha\n", encoding="utf-8")
    register("session-beta", project_root=tmp_path)
    assert _read_marker(tmp_path) == ["session-alpha", "session-beta"]


def test_register_is_idempotent(tmp_path: Path) -> None:
    """Registering an ID that's already in the marker is a no-op (no duplicate
    line). The Stop-hook reader uses set membership so duplicates would not
    cause a bug, but a tidy marker is easier to audit."""
    (tmp_path / MARKER_FILENAME).write_text("session-alpha\n", encoding="utf-8")
    register("session-alpha", project_root=tmp_path)
    assert _read_marker(tmp_path) == ["session-alpha"]


def test_register_preserves_existing_ids_in_order(tmp_path: Path) -> None:
    """When the marker already lists [A, B, C], appending D gives [A, B, C, D].
    Order matters only for human readability — the reader uses set membership."""
    (tmp_path / MARKER_FILENAME).write_text(
        "session-a\nsession-b\nsession-c\n", encoding="utf-8"
    )
    register("session-d", project_root=tmp_path)
    assert _read_marker(tmp_path) == ["session-a", "session-b", "session-c", "session-d"]


def test_register_skips_blank_lines_in_existing_marker(tmp_path: Path) -> None:
    """A marker with whitespace-only lines (e.g. trailing blank from echo)
    should not produce a blank entry in the set. Defensive against hand-edits."""
    (tmp_path / MARKER_FILENAME).write_text(
        "session-a\n\n   \nsession-b\n", encoding="utf-8"
    )
    register("session-c", project_root=tmp_path)
    assert _read_marker(tmp_path) == ["session-a", "session-b", "session-c"]


def test_register_rejects_empty_session_id(tmp_path: Path) -> None:
    """Empty or whitespace-only session IDs would create a blank line on disk
    that the Stop-hook reader filters out — but the call is meaningless. Fail
    loud so the operator notices a wiring bug (missing $CLAUDE_CODE_SESSION_ID)."""
    with pytest.raises(ValueError):
        register("", project_root=tmp_path)
    with pytest.raises(ValueError):
        register("   ", project_root=tmp_path)


def _concurrent_register_worker(args: tuple[str, str]) -> None:
    """Module-level worker for multiprocessing — must be top-level to pickle."""
    session_id, project_root_str = args
    register(session_id, project_root=Path(project_root_str))


def test_register_is_concurrency_safe(tmp_path: Path) -> None:
    """10 concurrent processes each register a distinct session ID.
    All 10 must end up in the marker — atomic-rename alone would race
    (each process reads the same old set, computes its own union, and only
    one rename survives → 9 IDs lost). The fcntl.flock guard prevents this."""
    ids = [f"session-{i:02d}" for i in range(10)]
    args = [(sid, str(tmp_path)) for sid in ids]

    # Use spawn context so the child interpreter doesn't inherit pytest state.
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(processes=10) as pool:
        pool.map(_concurrent_register_worker, args)

    final = _read_marker(tmp_path)
    assert sorted(final) == sorted(ids), (
        f"Concurrent register lost IDs. expected {sorted(ids)}, got {sorted(final)}"
    )


def test_register_defaults_to_env_var_when_session_id_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If session_id is omitted, register reads $CLAUDE_CODE_SESSION_ID.
    This matches the Stop-hook reader's fallback in section_gate._is_driver_session."""
    from ui_clone.driver_session import register_from_env

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "env-session-xyz")
    register_from_env(project_root=tmp_path)
    assert _read_marker(tmp_path) == ["env-session-xyz"]


def test_register_from_env_fails_when_env_var_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No session_id arg + no $CLAUDE_CODE_SESSION_ID → fail loud, not silently
    write an empty marker."""
    from ui_clone.driver_session import register_from_env

    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    with pytest.raises(ValueError):
        register_from_env(project_root=tmp_path)
