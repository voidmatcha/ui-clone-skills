from __future__ import annotations

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from ui_clone.shell import _bash_major, bash_bin

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "extract" / "extract-asset-metadata.sh"

# extract-asset-metadata.sh (like every script the pipeline dispatches) is run
# through ui_clone.shell.bash_bin() — a bash >= 4 binary — in production. macOS
# ships bash 3.2 as the bare `bash`, whose parser chokes on the script's nested
# command-substitution + quoted heredoc. Invoke the SAME bash production uses so
# this local test mirrors GitHub Actions (ubuntu bash 5) instead of diverging on
# a stale interpreter.
_BASH = bash_bin()


class _CssHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = b":root { --brand-green: #00c73c; }\n.hero { color: var(--brand-green); }\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/css")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _server_base_url() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CssHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{str(host)}:{port}"


def _write_fake_agent_browser(bin_dir: Path, base_url: str = "https://example.com") -> None:
    payload = {
        "schemaVersion": 1,
        "head": {
            "schemaVersion": 1,
            "url": f"{base_url}/",
            "title": "Example",
            "viewport": "width=device-width, initial-scale=1",
            "favicon": f"{base_url}/favicon.ico",
            "stylesheets": [f"{base_url}/assets/app.css?v=1"],
        },
        "visibleImages": [
            {
                "type": "image",
                "src": f"{base_url}/hero.png",
                "element": "img.hero",
                "top": 12,
                "left": 34,
                "width": 640,
                "height": 320,
            }
        ],
        "fonts": {
            "schemaVersion": 1,
            "faces": [
                {
                    "family": "Example Sans",
                    "weight": "400",
                    "style": "normal",
                    "urls": [f"{base_url}/font.woff2"],
                }
            ],
            "loadedFonts": [{"family": "Example Sans", "status": "loaded"}],
            "resourceUrls": [f"{base_url}/font.woff2"],
        },
    }
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        f"print(json.dumps({payload!r}))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)


def _write_fake_agent_browser_private_css(bin_dir: Path) -> None:
    payload = {
        "schemaVersion": 1,
        "head": {
            "schemaVersion": 1,
            "url": "https://example.com/",
            "title": "Example",
            "viewport": "width=device-width, initial-scale=1",
            "favicon": "",
            "stylesheets": ["https://169.254.169.254/latest/meta-data.css"],
        },
        "visibleImages": [],
        "fonts": {"schemaVersion": 1, "faces": [], "loadedFonts": [], "resourceUrls": []},
    }
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        f"print(json.dumps({payload!r}))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)


def _write_fake_curl(bin_dir: Path) -> None:
    fake = bin_dir / "curl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "out=''\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = '-o' ]; then out=\"$2\"; shift 2; continue; fi\n"
        "  shift\n"
        "done\n"
        "mkdir -p \"$(dirname \"$out\")\"\n"
        "printf ':root { --brand-green: #00c73c; }\\n.hero { color: var(--brand-green); }\\n' > \"$out\"\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)


def test_extract_asset_metadata_writes_canonical_step_2_5_artifacts(tmp_path: Path) -> None:
    """Step 2.5 script writes the asset artifacts required by extraction gate."""
    ref = tmp_path / "ref"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    server, base_url = _server_base_url()
    _write_fake_agent_browser(bin_dir, base_url)

    if _bash_major(_BASH) < 4:
        pytest.skip("requires bash >= 4 (none found); production dispatches via bash_bin()")

    env = os.environ.copy()
    env["PYTHON_BIN"] = sys.executable
    env["UI_CLONE_RESOURCE_MIRROR_ALLOW_PRIVATE"] = "1"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    try:
        proc = subprocess.run(
            [_BASH, str(SCRIPT), "asset-meta", str(ref), f"{base_url}/"],
            capture_output=True,
            text=True,
            env=env,
            cwd=ROOT,
            timeout=10,
        )
    finally:
        server.shutdown()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = json.loads(proc.stdout[proc.stdout.rfind("{\n  \"status\""):])
    assert summary["phase"] == "asset-metadata"

    head = json.loads((ref / "head.json").read_text(encoding="utf-8"))
    assert head["title"] == "Example"
    assert head["cssDownloads"][0]["status"] == "downloaded"
    fonts = json.loads((ref / "fonts.json").read_text(encoding="utf-8"))
    assert fonts["faces"][0]["family"] == "Example Sans"
    visible = json.loads((ref / "visible-images.json").read_text(encoding="utf-8"))
    assert visible["images"][0]["src"] == f"{base_url}/hero.png"
    assert "--brand-green: #00c73c" in (ref / "css" / "variables.txt").read_text(
        encoding="utf-8"
    )


def test_extract_asset_metadata_blocks_private_stylesheet_before_curl(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_agent_browser_private_css(bin_dir)
    marker = tmp_path / "curl-called"
    fake_curl = bin_dir / "curl"
    fake_curl.write_text(
        "#!/usr/bin/env bash\n"
        f"touch '{marker}'\n"
        "exit 91\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    if _bash_major(_BASH) < 4:
        pytest.skip("requires bash >= 4 (none found); production dispatches via bash_bin()")

    env = os.environ.copy()
    env["PYTHON_BIN"] = sys.executable
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    proc = subprocess.run(
        [_BASH, str(SCRIPT), "asset-meta", str(ref), "https://example.com/"],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not marker.exists()
    head = json.loads((ref / "head.json").read_text(encoding="utf-8"))
    assert head["cssDownloads"][0]["status"] == "blocked"
    assert head["cssDownloads"][0]["reason"] == "ssrf-blocked"
