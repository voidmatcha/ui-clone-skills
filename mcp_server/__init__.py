"""mcp_server — Motion forensics MCP server exposing the ui-clone-skills
engine to MCP-aware hosts (Cursor, Cline, Windsurf, Continue, Zed).

This package is opt-in. Install with: `uv sync --group mcp`.

Status: scaffold (Step B of the positioning rollout). The four jobs are
wired (decode / clone / verify / extract) but currently shell out to
the underlying ui_clone.pipeline CLI. Future commits will swap to
in-process engine calls.
"""

from __future__ import annotations

__all__ = ["main"]


def main() -> None:
    """Entry point for `python -m mcp_server`."""
    from . import server
    server.run()
