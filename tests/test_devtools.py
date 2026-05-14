"""
Tests for ui_clone.hooks.devtools_errors
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from ui_clone.hooks.devtools_errors import (
    _collect_errors,
    _extract_session_name,
    _fix_hint,
    _is_suppressed,
)

# ---------------------------------------------------------------------------
# _extract_session_name
# ---------------------------------------------------------------------------


def test_extract_session_name_basic() -> None:
    assert _extract_session_name("agent-browser --session my-sess eval '...'") == "my-sess"


def test_extract_session_name_quoted() -> None:
    assert _extract_session_name('agent-browser --session "my session" eval') == "my session"


def test_extract_session_name_missing() -> None:
    assert _extract_session_name("agent-browser eval 'something'") is None


def test_extract_session_name_with_other_flags() -> None:
    cmd = "agent-browser --session ref-session close"
    assert _extract_session_name(cmd) == "ref-session"


def test_extract_session_name_single_quoted() -> None:
    assert _extract_session_name("agent-browser --session 'my session' eval") == "my session"


def test_extract_session_name_at_end_of_string() -> None:
    assert _extract_session_name("agent-browser --session trailing") == "trailing"


def test_extract_session_name_with_hyphens_and_numbers() -> None:
    assert _extract_session_name("agent-browser --session ref-2026-05") == "ref-2026-05"


def test_extract_session_name_empty_quotes() -> None:
    """Empty double-quoted session name — unquoted regex grabs '""' as a token."""
    result = _extract_session_name('agent-browser --session "" eval')
    # The double-quoted regex requires [^"]+ (at least 1 char), so it falls through
    # to the unquoted regex which grabs '""' as a non-whitespace token.
    assert result == '""'


# ---------------------------------------------------------------------------
# _is_suppressed
# ---------------------------------------------------------------------------


def test_suppressed_resize_observer() -> None:
    assert _is_suppressed("ResizeObserver loop limit exceeded")


def test_suppressed_hmr() -> None:
    assert _is_suppressed("[HMR] waiting for update signal")


def test_suppressed_ad_blocker() -> None:
    assert _is_suppressed("net::ERR_BLOCKED_BY_CLIENT")


def test_not_suppressed_real_error() -> None:
    assert not _is_suppressed("TypeError: Cannot read properties of null")


# ---------------------------------------------------------------------------
# _fix_hint
# ---------------------------------------------------------------------------


def test_fix_hint_undefined() -> None:
    msg = "ReferenceError: myVar is not defined"
    assert "import" in _fix_hint(msg) or "scope" in _fix_hint(msg)


def test_fix_hint_hydration() -> None:
    msg = "Hydration failed because the initial UI does not match"
    assert "SSR" in _fix_hint(msg) or "useEffect" in _fix_hint(msg)


def test_fix_hint_network() -> None:
    msg = "Failed to fetch https://api.example.com/data"
    assert "CORS" in _fix_hint(msg) or "network" in _fix_hint(msg).lower()


def test_fix_hint_chunk_load() -> None:
    msg = "ChunkLoadError: Loading chunk 42 failed"
    assert "chunk" in _fix_hint(msg).lower() or "build" in _fix_hint(msg).lower()


def test_fix_hint_unknown_falls_back() -> None:
    msg = "Some totally unknown error that matches nothing"
    hint = _fix_hint(msg)
    assert "DevTools" in hint or "console" in hint


# ---------------------------------------------------------------------------
# _collect_errors — with mocked agent-browser
# ---------------------------------------------------------------------------


def _make_agent_browser_mock(outputs: dict[str, str]) -> Any:
    """Return a mock for subprocess.run that maps JS snippet substrings to outputs."""

    def fake_run(cmd: Any, **kwargs: Any) -> Any:
        js_arg = cmd[-1] if cmd else ""
        for key, output in outputs.items():
            if key in js_arg:
                m = MagicMock()
                m.returncode = 0
                m.stdout = output
                return m
        m = MagicMock()
        m.returncode = 1
        m.stdout = ""
        return m

    return fake_run


def test_collect_errors_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """No errors in browser → empty list."""
    payload = json.dumps({"errors": [], "count": 0})
    monkeypatch.setattr(
        "subprocess.run",
        _make_agent_browser_mock({"__uiSkillsErrors": payload}),
    )
    result = _collect_errors("test-session")
    assert result == []


def test_collect_errors_returns_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Errors present → returned as list."""
    errors = [
        {
            "type": "uncaught",
            "message": "TypeError: x is not defined",
            "source": "app.js",
            "line": 10,
        },
        {"type": "console.error", "message": "Failed to fetch /api/data"},
    ]
    payload = json.dumps({"errors": errors, "count": 2})
    monkeypatch.setattr(
        "subprocess.run",
        _make_agent_browser_mock({"__uiSkillsErrors": payload}),
    )
    result = _collect_errors("test-session")
    assert len(result) == 2
    assert result[0]["type"] == "uncaught"


def test_collect_errors_suppresses_noise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Suppressed errors (ResizeObserver, HMR) are filtered out."""
    errors = [
        {"type": "uncaught", "message": "ResizeObserver loop limit exceeded"},
        {"type": "console.error", "message": "[HMR] waiting for update"},
        {"type": "uncaught", "message": "ReferenceError: myFunc is not defined"},
    ]
    payload = json.dumps({"errors": errors, "count": 3})
    monkeypatch.setattr(
        "subprocess.run",
        _make_agent_browser_mock({"__uiSkillsErrors": payload}),
    )
    result = _collect_errors("test-session")
    assert len(result) == 1
    assert "myFunc" in result[0]["message"]


def test_collect_errors_agent_browser_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """agent-browser not installed → returns empty list (no crash)."""

    def raise_fnf(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("agent-browser not found")

    monkeypatch.setattr("subprocess.run", raise_fnf)
    result = _collect_errors("test-session")
    assert result == []


def test_collect_errors_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed JSON from agent-browser → returns empty list."""
    monkeypatch.setattr(
        "subprocess.run",
        _make_agent_browser_mock({"__uiSkillsErrors": "not valid json {{{"}),
    )
    result = _collect_errors("test-session")
    assert result == []


# ---------------------------------------------------------------------------
# Hook main() — subprocess integration
# ---------------------------------------------------------------------------


def run_hook(stdin_data: str = "", env: dict | None = None) -> Any:
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", "ui_clone.hooks.devtools_errors"],
        input=stdin_data,
        capture_output=True,
        text=True,
        env=merged_env,
    )


def test_hook_no_wip_marker_exits_0(tmp_path: Path) -> None:
    """No WIP marker → exits 0 immediately."""
    sr = tmp_path / "tmp" / "ref"
    sr.mkdir(parents=True)
    ref = sr / "comp"
    ref.mkdir()
    # No .ui-re-active marker
    stdin = json.dumps({"tool_input": {"command": "agent-browser --session foo eval '1'"}})
    result = run_hook(stdin, env={"CLAUDE_PROJECT_DIR": str(tmp_path)})
    assert result.returncode == 0


def test_hook_no_session_in_command_exits_0(tmp_path: Path) -> None:
    """Bash command without --session → exits 0 (not a browser command)."""
    sr = tmp_path / "tmp" / "ref"
    sr.mkdir(parents=True)
    ref = sr / "comp"
    ref.mkdir()
    (ref / ".ui-re-active").touch()
    stdin = json.dumps({"tool_input": {"command": "npm run dev"}})
    result = run_hook(stdin, env={"CLAUDE_PROJECT_DIR": str(tmp_path)})
    assert result.returncode == 0


def test_hook_always_exits_0(tmp_path: Path) -> None:
    """Hook is advisory — always exits 0 even when it encounters errors."""
    sr = tmp_path / "tmp" / "ref"
    sr.mkdir(parents=True)
    ref = sr / "comp"
    ref.mkdir()
    (ref / ".ui-re-active").touch()
    # agent-browser not available in test env → will return empty errors → exit 0
    stdin = json.dumps({"tool_input": {"command": "agent-browser --session my-sess eval '...'"}})
    result = run_hook(stdin, env={"CLAUDE_PROJECT_DIR": str(tmp_path)})
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Hook main() — output formatting with mocked errors
# ---------------------------------------------------------------------------


class TestDevtoolsMainOutput:
    """Tests for main() output formatting when errors are present."""

    def test_errors_printed_with_fix_hints(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When errors exist, main() prints formatted error lines with fix hints."""
        import importlib
        import io
        from unittest.mock import patch

        import ui_clone.hooks.devtools_errors as mod

        importlib.reload(mod)

        # Mock _collect_errors to return errors without needing agent-browser
        fake_errors = [
            {"type": "uncaught", "message": "ReferenceError: x is not defined", "source": "app.js", "line": 42},
            {"type": "console.error", "message": "Failed to fetch https://api.example.com"},
        ]
        monkeypatch.setattr(mod, "_collect_errors", lambda session: fake_errors)

        # Set up the environment so main() reaches _collect_errors
        ref_dir: Path | None = None

        def fake_find_project_root() -> Path:
            assert ref_dir is not None
            return ref_dir.parent.parent.parent

        import tempfile
        with tempfile.TemporaryDirectory() as td:
            from pathlib import Path
            base = Path(td)
            sr = base / "tmp" / "ref"
            sr.mkdir(parents=True)
            rd = sr / "comp"
            rd.mkdir()
            (rd / ".ui-re-active").touch()
            (rd / "extracted.json").write_text('{"sections": []}')
            ref_dir = rd

            monkeypatch.setattr(mod, "_find_project_root", fake_find_project_root)

            captured = io.StringIO()
            stdin_data = json.dumps({"tool_input": {"command": "agent-browser --session test-sess eval '1'"}})

            try:
                with patch("sys.stdin", io.StringIO(stdin_data)):
                    with patch("sys.stdout", captured):
                        mod.main()
            except SystemExit:
                pass

        output = captured.getvalue()
        # Should contain error count
        assert "2 console error" in output
        # Should contain the error messages
        assert "is not defined" in output
        assert "Failed to fetch" in output
        # Should contain fix hints
        assert "→" in output  # fix hint arrow

    def test_more_than_10_errors_shows_truncation_notice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When >10 errors, prints truncation notice."""
        import importlib
        import io
        from unittest.mock import patch

        import ui_clone.hooks.devtools_errors as mod

        importlib.reload(mod)

        fake_errors = [
            {"type": "uncaught", "message": f"Error #{i}"} for i in range(15)
        ]
        monkeypatch.setattr(mod, "_collect_errors", lambda session: fake_errors)

        import tempfile
        ref_dir: Path | None = None

        def fake_find_project_root() -> Path:
            assert ref_dir is not None
            return ref_dir.parent.parent.parent

        with tempfile.TemporaryDirectory() as td:
            from pathlib import Path
            base = Path(td)
            sr = base / "tmp" / "ref"
            sr.mkdir(parents=True)
            rd = sr / "comp"
            rd.mkdir()
            (rd / ".ui-re-active").touch()
            (rd / "extracted.json").write_text('{"sections": []}')
            ref_dir = rd

            monkeypatch.setattr(mod, "_find_project_root", fake_find_project_root)

            captured = io.StringIO()
            stdin_data = json.dumps({"tool_input": {"command": "agent-browser --session test-sess eval '1'"}})

            try:
                with patch("sys.stdin", io.StringIO(stdin_data)):
                    with patch("sys.stdout", captured):
                        mod.main()
            except SystemExit:
                pass

        output = captured.getvalue()
        assert "15 console error" in output
        assert "5 more" in output  # "and 5 more errors"
