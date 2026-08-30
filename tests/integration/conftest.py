"""Shared fixtures for opt-in integration tests of capture-{states,scroll,hover}.sh.

These tests are gated behind UI_CLONE_INTEGRATION=1 because each test spins up
a real Chrome session via agent-browser, costing 5-20s per test. Regular
`pytest` runs skip the whole `tests/integration/` package.

Usage:
    UI_CLONE_INTEGRATION=1 uv run pytest tests/integration/

Design notes:
- HTTP server uses `ThreadingHTTPServer` on port=0 (kernel-assigned free port)
  so parallel pytest workers and concurrent local dev servers don't collide.
- One server per session (pytest session scope) — opening + closing Chrome on
  every test already dominates wall time, and the fixture HTML is read-only.
- subprocess timeouts are intentionally generous (60s) — capture-states.sh has
  a 5s in-page wall-clock cap and capture-hover.sh can take 12.5s worst-case
  per the script header. Set them well above worst-case to avoid spurious
  timeouts on a busy machine.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


_INTEGRATION_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip integration items unless UI_CLONE_INTEGRATION=1.

    pytest collects this conftest for the whole repo, so `items` includes
    tests outside `tests/integration/`. Filter by path before adding the
    skip marker — otherwise we'd skip every test in the repo.

    Module-level `pytest.skip(allow_module_level=True)` would also work, but
    duplicating it across three files invites drift. Centralizing here keeps
    the gate single-source.
    """
    integration_enabled = os.environ.get("UI_CLONE_INTEGRATION") == "1"
    skip_marker = pytest.mark.skip(
        reason="opt-in integration test — set UI_CLONE_INTEGRATION=1 to run",
    )
    # Each integration test drives a real Chrome session and uses its own
    # explicit subprocess timeouts. Keep those contracts local by disabling the
    # repo-wide timeout when integration items run.
    disable_timeout_marker = pytest.mark.timeout(0)
    for item in items:
        try:
            item_path = Path(str(item.fspath))
        except Exception:
            continue
        if _INTEGRATION_DIR not in item_path.parents:
            continue
        if integration_enabled:
            item.add_marker(disable_timeout_marker)
        else:
            item.add_marker(skip_marker)


class _QuietHandler(SimpleHTTPRequestHandler):
    """Suppress per-request stderr logging — the fixture server fires a lot of
    requests per test and the noise drowns out real failures.
    """

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


@pytest.fixture(scope="session")
def http_server() -> Iterator[str]:
    """Serve `tests/integration/fixtures/` over HTTP on a kernel-assigned port.

    Yields the base URL (with trailing slash) so tests can simply append the
    fixture filename: `f"{http_server}splash.html"`.
    """
    if not FIXTURES_DIR.is_dir():
        pytest.fail(f"fixtures dir missing: {FIXTURES_DIR}")

    # SimpleHTTPRequestHandler serves files relative to cwd; bind a closure
    # that pins the directory to FIXTURES_DIR so cwd changes during the test
    # session don't break us.
    def handler_factory(*args: object, **kwargs: object) -> _QuietHandler:
        return _QuietHandler(*args, directory=str(FIXTURES_DIR), **kwargs)  # type: ignore[arg-type]

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory)
    port = server.server_address[1]
    thread = threading.Thread(
        target=server.serve_forever, name=f"fixture-http-{port}", daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the repo root — used to locate scripts/extract/."""
    return REPO_ROOT
