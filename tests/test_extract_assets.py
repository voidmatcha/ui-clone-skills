from __future__ import annotations

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from time import sleep

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "extract" / "extract-assets.sh"


class _SlowHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        sleep(0.2)
        body = b"asset" * 300
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _server_base_url() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{str(host)}:{port}"


def _write_fake_agent_browser(bin_dir: Path, base_url: str = "https://cdn.example") -> None:
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "args=\"$*\"\n"
        "case \" $args \" in\n"
        "  *' close '*) exit 0 ;;\n"
        "  *' eval '*querySelectorAll\\\\\\('video'\\\\\\)* )\n"
        f"    printf '%s\\n' '[{{\"index\":0,\"section\":\"hero\",\"currentSrc\":\"{base_url}/hero.mp4\",\"sources\":[{{\"src\":\"{base_url}/hero.mp4\",\"type\":\"video/mp4\"}}]}}]'\n"
        "    exit 0 ;;\n"
        "  *' eval '*fontURLs* )\n"
        f"    printf '%s\\n' '[\"{base_url}/slow.woff2\"]'\n"
        "    exit 0 ;;\n"
        "esac\n"
        "printf '[]\\n'\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)


def _write_fake_agent_browser_private_assets(bin_dir: Path) -> None:
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "args=\"$*\"\n"
        "case \" $args \" in\n"
        "  *' close '*) exit 0 ;;\n"
        "  *' eval '*querySelectorAll\\\\\\('video'\\\\\\)* )\n"
        "    printf '%s\\n' '[{\"index\":0,\"section\":\"hero\",\"currentSrc\":\"https://169.254.169.254/latest/video.mp4\",\"sources\":[{\"src\":\"https://169.254.169.254/latest/video.mp4\",\"type\":\"video/mp4\"}]}]'\n"
        "    exit 0 ;;\n"
        "  *' eval '*fontURLs* )\n"
        "    printf '%s\\n' '[\"https://169.254.169.254/latest/font.woff2\"]'\n"
        "    exit 0 ;;\n"
        "esac\n"
        "printf '[]\\n'\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)


def _write_slow_curl(bin_dir: Path) -> None:
    fake = bin_dir / "curl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "sleep 0.2\n"
        "out=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = '-o' ]; then out=\"$2\"; shift 2; continue; fi\n"
        "  shift\n"
        "done\n"
        "if [ -n \"$out\" ]; then printf 'partial' > \"$out\"; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)


def _summary_json(stdout: str) -> dict[str, object]:
    marker = '{\n  "status": "pass"'
    start = stdout.rfind(marker)
    assert start != -1, stdout
    parsed: dict[str, object] = json.loads(stdout[start:])
    return parsed


def test_extract_assets_timeout_does_not_skip_final_summary(tmp_path: Path) -> None:
    """Slow optional video/font downloads must not abort the extraction script.

    The final JSON summary is consumed by pipeline wrappers. A timed-out font or
    video transfer is recoverable, so the script must report the failed asset
    and still complete instead of exiting under `set -euo pipefail`.
    """
    ref = tmp_path / "ref"
    public = tmp_path / "impl" / "public"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    server, base_url = _server_base_url()
    _write_fake_agent_browser(bin_dir, base_url)

    env = os.environ.copy()
    env["PYTHON_BIN"] = sys.executable
    env["UI_CLONE_RESOURCE_MIRROR_ALLOW_PRIVATE"] = "1"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["UI_CLONE_EXTRACT_ASSETS_VIDEO_TIMEOUT"] = "0.05"
    env["UI_CLONE_EXTRACT_ASSETS_FONT_TIMEOUT"] = "0.05"

    try:
        proc = subprocess.run(
            ["bash", str(SCRIPT), "extract-timeout", str(ref), str(public)],
            capture_output=True,
            text=True,
            env=env,
            cwd=ROOT,
            timeout=10,
        )
    finally:
        server.shutdown()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "TIMEOUT" in proc.stderr
    assert (ref / "assets").is_dir()
    assert (public / "videos").is_dir()
    assert (public / "fonts").is_dir()

    summary = _summary_json(proc.stdout)
    assert summary["status"] == "pass"
    assert summary["phase"] == "assets"
    assert summary["data"] == {"assets": []}


def test_extract_assets_blocks_private_media_before_curl(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    public = tmp_path / "impl" / "public"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_agent_browser_private_assets(bin_dir)
    marker = tmp_path / "curl-called"
    fake_curl = bin_dir / "curl"
    fake_curl.write_text(
        "#!/usr/bin/env bash\n"
        f"touch '{marker}'\n"
        "exit 91\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    env = os.environ.copy()
    env["PYTHON_BIN"] = sys.executable
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    proc = subprocess.run(
        ["bash", str(SCRIPT), "extract-private", str(ref), str(public)],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not marker.exists()
    assert "ssrf-blocked" in proc.stderr
    assert not (public / "videos" / "hero-bg.mp4").exists()
    assert not (public / "fonts" / "unknown-400.woff2").exists()
