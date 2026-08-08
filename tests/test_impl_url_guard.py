from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify" / "impl-url-guard.sh"


@pytest.fixture
def local_server(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    impl = tmp_path / "impl"
    impl.mkdir()
    code = """
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import sys
server = ThreadingHTTPServer(('127.0.0.1', 0), SimpleHTTPRequestHandler)
print(server.server_address[1], flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=impl,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    port_line = proc.stdout.readline().strip()
    try:
        yield impl, f"http://127.0.0.1:{int(port_line)}/"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_impl_url_guard_passes_when_port_cwd_matches_impl_root(
    tmp_path: Path, local_server: tuple[Path, str]
) -> None:
    if shutil.which("lsof") is None:
        pytest.skip("lsof required for local port cwd guard")
    impl, url = local_server
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), url],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads((ref / "impl-url-guard.json").read_text(encoding="utf-8"))
    assert payload["status"] == "pass"
    assert payload["portRouting"]["mismatch"] is False


def test_impl_url_guard_fails_when_port_cwd_differs_from_impl_root(
    tmp_path: Path, local_server: tuple[Path, str]
) -> None:
    if shutil.which("lsof") is None:
        pytest.skip("lsof required for local port cwd guard")
    _impl, url = local_server
    expected = tmp_path / "expected-impl"
    expected.mkdir()
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / ".impl-root").write_text(str(expected) + "\n", encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), url],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert proc.returncode == 1
    payload = json.loads((ref / "impl-url-guard.json").read_text(encoding="utf-8"))
    assert payload["status"] == "fail"
    assert payload["portRouting"]["mismatch"] is True
