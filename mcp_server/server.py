"""MCP server implementation.

Exposes four tools (decode / clone / verify / extract) that wrap the
ui-clone-skills engine. Each tool currently shells out to the
ui_clone.pipeline CLI — future commits will switch to in-process calls.

The MCP Python SDK is an optional dependency (group `mcp`). When it
isn't installed, `run()` raises a clear ImportError pointing at the
install command. Import failures during module load are deferred to
`run()` so unit tests can mock the SDK without needing it installed
locally.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_pipeline(args: list[str]) -> dict[str, Any]:
    """Invoke `uv run python -m ui_clone.pipeline ...` and return a
    dict with stdout / stderr / exit code. Tool callers translate this
    into the structured MCP response.
    """
    cmd = ["uv", "run", "python", "-m", "ui_clone.pipeline", *args]
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return {
        "exitCode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


# ── Tool handlers ──────────────────────────────────────────────────────


def tool_decode(url: str, component: str = "decode") -> dict[str, Any]:
    """Analyse motion + build of a URL. Educational — no code emit.

    Runs the extraction phases of ui_clone.pipeline (reference, extraction,
    bundle, paid-features, spec) and returns the resulting
    transition-spec.json + external-sdks.json summary. Stops before any
    code-generation phase.
    """
    result = _run_pipeline([url, component, "decode", "extract", "spec"])
    return {
        "url": url,
        "component": component,
        "summary": "Decode pipeline run. See stdout/stderr for details.",
        **result,
    }


def tool_clone(url: str, component: str = "clone") -> dict[str, Any]:
    """Generate React + Tailwind components against the captured DOM.

    Runs the full pipeline through pre-generate. Caller is responsible
    for implementing components in scratch/<component>/impl/ and then
    invoking `verify` separately.
    """
    result = _run_pipeline([url, component, "clone", "pre-generate"])
    return {
        "url": url,
        "component": component,
        "summary": "Clone pipeline run. Implement impl/ then call verify.",
        **result,
    }


def tool_verify(url: str, impl_url: str, component: str = "verify") -> dict[str, Any]:
    """Score impl_url against url with AE/SSIM + motion-parity gates."""
    result = _run_pipeline([url, component, "verify", "section-compare", impl_url])
    return {
        "url": url,
        "implUrl": impl_url,
        "component": component,
        "summary": "Verify gate suite run. See stderr for failed gates.",
        **result,
    }


def tool_extract(url: str, component: str = "extract") -> dict[str, Any]:
    """Raw JSON dump of structure / styles / animations / bundles."""
    result = _run_pipeline([url, component, "extract", "extraction"])
    return {
        "url": url,
        "component": component,
        "summary": "Raw extraction complete. See tmp/ref/<component>/ for artifacts.",
        **result,
    }


_TOOLS: dict[str, dict[str, Any]] = {
    "decode": {
        "description": (
            "Motion forensics — analyse motion + build of a live URL. "
            "Educational; no code emitted. Returns detected animation "
            "libraries, scroll engine, transition spec, and bundle "
            "evidence."
        ),
        "handler": tool_decode,
        "schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Live URL to decode."},
                "component": {
                    "type": "string",
                    "description": "Optional name for the ref dir (default: 'decode').",
                },
            },
            "required": ["url"],
        },
    },
    "clone": {
        "description": (
            "Generate React + Tailwind components against the captured DOM "
            "of a live URL. Runs ui-clone-skills pipeline through "
            "pre-generate; downstream code emission is the caller's job."
        ),
        "handler": tool_clone,
        "schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Live URL to clone."},
                "component": {"type": "string", "description": "Ref dir name."},
            },
            "required": ["url"],
        },
    },
    "verify": {
        "description": (
            "Score an existing impl against a reference URL with AE/SSIM "
            "+ motion-parity gates. Returns per-gate pass/fail and "
            "section-compare AE numbers."
        ),
        "handler": tool_verify,
        "schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Reference URL."},
                "impl_url": {"type": "string", "description": "Impl URL (e.g., http://localhost:5173)."},
                "component": {"type": "string", "description": "Ref dir name."},
            },
            "required": ["url", "impl_url"],
        },
    },
    "extract": {
        "description": (
            "Raw JSON dump of structure / styles / animations / bundles "
            "from a live URL. Returns artifacts under tmp/ref/<component>/."
        ),
        "handler": tool_extract,
        "schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Live URL."},
                "component": {"type": "string", "description": "Ref dir name."},
            },
            "required": ["url"],
        },
    },
}


def list_tools() -> list[dict[str, Any]]:
    """Public tool registry for hosts that want the metadata without
    starting the server (e.g., docs generation, tests)."""
    return [
        {
            "name": name,
            "description": spec["description"],
            "inputSchema": spec["schema"],
        }
        for name, spec in _TOOLS.items()
    ]


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Invoke a tool by name. Returns the raw handler result.

    Public function so tests can exercise the dispatch logic without
    the full MCP transport.
    """
    spec = _TOOLS.get(name)
    if spec is None:
        return {"error": f"Unknown tool: {name}"}
    handler = spec["handler"]
    try:
        return handler(**arguments)
    except TypeError as exc:
        return {"error": f"Invalid arguments for {name}: {exc}"}


def run() -> None:
    """Start the MCP stdio server.

    Imports the MCP SDK lazily so unit tests can mock the dispatcher
    without needing the SDK installed.
    """
    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(
            "MCP SDK not installed. Run: uv sync --group mcp"
        ) from exc

    app = FastMCP("ui-clone-skills")

    for name, spec in _TOOLS.items():
        # FastMCP introspects the handler signature for the schema,
        # so we register the handler directly with its docstring.
        app.tool(name=name, description=spec["description"])(spec["handler"])

    app.run()
