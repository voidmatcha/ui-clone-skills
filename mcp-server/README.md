# mcp-server — Motion forensics MCP server

A Model Context Protocol (MCP) server that exposes the ui-clone-skills engine to any MCP-aware host: **Cursor, Cline, Windsurf, Continue, Zed**, and direct API clients.

## Why this exists

ui-clone-skills ships as a Claude Code + Codex plugin first. The MCP server is the second surface — it gives every MCP-aware host the same four jobs against a live URL without re-implementing the engine.

| Tool | Job |
|---|---|
| `decode` | Analyse motion + build of a URL (educational, no code emit) |
| `clone` | Generate React + Tailwind components against the captured DOM |
| `verify` | AE/SSIM + motion-parity score against an existing impl |
| `extract` | Raw JSON dump of structure / styles / animations / bundles |

## Status

**Scaffold — Step B of the positioning rollout.** Tool surface is wired but each tool currently shells out to the underlying `ui_clone.pipeline` CLI. Future commits will swap the shell-outs for direct in-process calls into the engine module.

## Install

From repo root:

```bash
uv sync --group mcp
```

That pulls `mcp` (Anthropic's official Python SDK) plus the existing `ui-clone-skills` engine deps.

## Run

```bash
uv run python -m mcp_server
```

The server listens on stdio (canonical MCP transport). Configure your host to launch it:

### Cursor

`~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "ui-clone-skills": {
      "command": "uv",
      "args": ["run", "python", "-m", "mcp_server"],
      "cwd": "/path/to/ui-clone-skills"
    }
  }
}
```

### Cline / Continue / Windsurf

See each tool's MCP config docs — the launcher block above is portable.

## Roadmap

- [ ] Swap shell-out → in-process engine call for each tool
- [ ] Add `decode-receipt` tool that wraps `build-decode-receipt.sh`
- [ ] Add `progress://` resource for streaming pipeline state during long extractions
- [ ] Add prompt templates for each of the four jobs

See `pyproject.toml` `[dependency-groups]` `mcp` for the Python deps.
