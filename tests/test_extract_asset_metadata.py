from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

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


def _write_fake_agent_browser(bin_dir: Path) -> None:
    payload = {
        "schemaVersion": 1,
        "head": {
            "schemaVersion": 1,
            "url": "https://example.com/",
            "title": "Example",
            "viewport": "width=device-width, initial-scale=1",
            "favicon": "https://example.com/favicon.ico",
            "stylesheets": ["https://example.com/assets/app.css?v=1"],
        },
        "visibleImages": [
            {
                "type": "image",
                "src": "https://example.com/hero.png",
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
                    "urls": ["https://example.com/font.woff2"],
                }
            ],
            "loadedFonts": [{"family": "Example Sans", "status": "loaded"}],
            "resourceUrls": ["https://example.com/font.woff2"],
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
    _write_fake_agent_browser(bin_dir)
    _write_fake_curl(bin_dir)

    if _bash_major(_BASH) < 4:
        pytest.skip("requires bash >= 4 (none found); production dispatches via bash_bin()")

    env = os.environ.copy()
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
    summary = json.loads(proc.stdout[proc.stdout.rfind("{\n  \"status\""):])
    assert summary["phase"] == "asset-metadata"

    head = json.loads((ref / "head.json").read_text(encoding="utf-8"))
    assert head["title"] == "Example"
    assert head["cssDownloads"][0]["status"] == "downloaded"
    fonts = json.loads((ref / "fonts.json").read_text(encoding="utf-8"))
    assert fonts["faces"][0]["family"] == "Example Sans"
    visible = json.loads((ref / "visible-images.json").read_text(encoding="utf-8"))
    assert visible["images"][0]["src"] == "https://example.com/hero.png"
    assert "--brand-green: #00c73c" in (ref / "css" / "variables.txt").read_text(
        encoding="utf-8"
    )
