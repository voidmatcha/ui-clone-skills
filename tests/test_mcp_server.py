"""Tests for the Step B MCP server scaffold.

Exercises the tool registry, dispatch, and the deferred-import of the
MCP SDK in run(). Each handler shells out to ui_clone.pipeline; we
mock subprocess in dispatch tests so the suite doesn't actually run
the full pipeline.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp_server import server  # noqa: E402


def test_list_tools_returns_four_jobs() -> None:
    tools = server.list_tools()
    names = {t["name"] for t in tools}
    assert names == {"decode", "clone", "verify", "extract"}


def test_each_tool_has_description_and_schema() -> None:
    for tool in server.list_tools():
        assert isinstance(tool["description"], str) and len(tool["description"]) > 40
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert "url" in schema["properties"]
        assert "url" in schema["required"]


def test_descriptions_lead_with_motion_forensics_framing() -> None:
    """Motion-forensics metaphor should appear in at least the decode tool."""
    decode = next(t for t in server.list_tools() if t["name"] == "decode")
    assert "motion forensics" in decode["description"].lower()


def test_call_tool_unknown_returns_error() -> None:
    result = server.call_tool("nonsense", {"url": "https://x.test"})
    assert "error" in result
    assert "Unknown tool" in result["error"]


def test_call_tool_invalid_args_returns_error() -> None:
    """Missing required arg should return error, not raise."""
    result = server.call_tool("decode", {})  # missing url
    assert "error" in result
    assert "Invalid arguments" in result["error"]


@mock.patch.object(server, "_run_pipeline", return_value={
    "exitCode": 0, "stdout": "OK\n", "stderr": "",
})
def test_call_tool_decode_dispatches_to_pipeline(mock_run: mock.Mock) -> None:
    result = server.call_tool("decode", {"url": "https://example.com"})
    assert result["url"] == "https://example.com"
    assert result["component"] == "decode"
    assert result["exitCode"] == 0
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "https://example.com" in args
    assert "decode" in args


@mock.patch.object(server, "_run_pipeline", return_value={
    "exitCode": 0, "stdout": "", "stderr": "",
})
def test_call_tool_verify_takes_impl_url(mock_run: mock.Mock) -> None:
    result = server.call_tool("verify", {
        "url": "https://example.com",
        "impl_url": "http://localhost:5173",
    })
    assert result["implUrl"] == "http://localhost:5173"
    mock_run.assert_called_once()


def test_run_raises_when_mcp_sdk_missing() -> None:
    """When the optional `mcp` SDK isn't installed, run() raises
    ImportError with the install command in the message."""
    with mock.patch.dict("sys.modules", {"mcp": None, "mcp.server.fastmcp": None}):
        try:
            import mcp.server.fastmcp  # noqa: F401
        except ImportError:
            # Confirms the SDK isn't present in this test env (expected).
            with pytest.raises(ImportError, match="uv sync --group mcp"):
                server.run()
        else:
            pytest.skip("MCP SDK actually present; skip this negative test")
