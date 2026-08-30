import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, cast


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _json_tail(stdout: str) -> dict[str, Any]:
    marker = '{\n  "status"'
    start = stdout.rfind(marker)
    assert start != -1, stdout
    return cast(dict[str, Any], json.loads(stdout[start:]))


class _ChunkHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = ('gsap.to(".hero", { duration: 1, ease: "power2.out" });\n' * 20).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _server_base_url() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ChunkHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{str(host)}:{port}"


def test_download_chunks_uses_numeric_duration_when_date_has_no_milliseconds(
    tmp_path: Path,
) -> None:
    """macOS date prints a literal N for %3N; duration JSON must still work."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_date = fake_bin / "date"
    fake_date.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"${1:-}\" = '+%s%3N' ]; then\n"
        "  printf '17797345763N\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exec /bin/date \"$@\"\n",
        encoding="utf-8",
    )
    fake_date.chmod(0o755)

    root = _project_root()
    ref = tmp_path / "ref"
    server, base_url = _server_base_url()
    env = os.environ.copy()
    env["PYTHON_BIN"] = sys.executable
    env["UI_CLONE_RESOURCE_MIRROR_ALLOW_PRIVATE"] = "1"
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    try:
        proc = subprocess.run(
            [
                "bash",
                str(root / "scripts" / "extract" / "download-chunks.sh"),
                str(ref),
                "-",
            ],
            input=f'["{base_url}/static/app.js"]',
            capture_output=True,
            text=True,
            timeout=20,
            cwd=root,
            env=env,
        )
    finally:
        server.shutdown()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = _json_tail(proc.stdout)
    assert payload["status"] == "pass"
    assert isinstance(payload["duration_ms"], int)
    assert payload["duration_ms"] >= 0
    assert (ref / "bundle-analysis.json").is_file()
    assert (ref / "bundle-map.json").is_file()


def test_download_chunks_blocks_link_local_url_before_curl(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "curl-called"
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        "#!/usr/bin/env bash\n"
        f"touch '{marker}'\n"
        "exit 91\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    root = _project_root()
    ref = tmp_path / "ref"
    env = os.environ.copy()
    env["PYTHON_BIN"] = sys.executable
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "extract" / "download-chunks.sh"),
            str(ref),
            "-",
        ],
        input='["https://169.254.169.254/static/app.js"]',
        capture_output=True,
        text=True,
        timeout=20,
        cwd=root,
        env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not marker.exists()
    assert "ssrf-blocked" in proc.stderr
    assert not (ref / "bundles" / "app.js").exists()
