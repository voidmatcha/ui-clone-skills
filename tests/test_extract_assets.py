from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "extract" / "extract-assets.sh"


def _write_fake_agent_browser(bin_dir: Path) -> None:
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "args=\"$*\"\n"
        "case \" $args \" in\n"
        "  *' close '*) exit 0 ;;\n"
        "  *' eval '*querySelectorAll\\\\\\('video'\\\\\\)* )\n"
        "    printf '%s\\n' '[{\"index\":0,\"section\":\"hero\",\"currentSrc\":\"https://cdn.example/hero.mp4\",\"sources\":[{\"src\":\"https://cdn.example/hero.mp4\",\"type\":\"video/mp4\"}]}]'\n"
        "    exit 0 ;;\n"
        "  *' eval '*fontURLs* )\n"
        "    printf '%s\\n' '[\"https://fonts.example/slow.woff2\"]'\n"
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
    _write_fake_agent_browser(bin_dir)
    _write_slow_curl(bin_dir)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["UI_CLONE_EXTRACT_ASSETS_VIDEO_TIMEOUT"] = "0.05"
    env["UI_CLONE_EXTRACT_ASSETS_FONT_TIMEOUT"] = "0.05"

    proc = subprocess.run(
        ["bash", str(SCRIPT), "extract-timeout", str(ref), str(public)],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "TIMEOUT" in proc.stderr
    assert (ref / "assets").is_dir()
    assert (public / "videos").is_dir()
    assert (public / "fonts").is_dir()

    summary = _summary_json(proc.stdout)
    assert summary["status"] == "pass"
    assert summary["phase"] == "assets"
    assert summary["data"] == {"assets": []}
