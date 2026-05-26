from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "extract" / "asset-download.sh"


def _write_fake_curl(bin_dir: Path) -> None:
    fake = bin_dir / "curl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "out=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = '-o' ]; then out=\"$2\"; shift 2; continue; fi\n"
        "  shift\n"
        "done\n"
        "if [ -n \"$out\" ]; then printf 'forbidden' > \"$out\"; fi\n"
        "printf '403'\n"
        "printf 'curl: (22) The requested URL returned error: 403\\n' >&2\n"
        "exit 22\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)


def _write_fake_agent_browser(bin_dir: Path, calls_log: Path, body: bytes) -> None:
    payload = base64.b64encode(body).decode("ascii")
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> '{calls_log}'\n"
        "args=\"$*\"\n"
        "case \" $args \" in\n"
        "  *' fetch '*) printf 'Unknown command: fetch\\n' >&2; exit 2 ;;\n"
        "  *' open '*) exit 0 ;;\n"
        "  *' wait '*) exit 0 ;;\n"
        "  *' eval '*)\n"
        "    python3 - <<'PY'\n"
        "import json\n"
        "print(json.dumps({\n"
        "    'success': True,\n"
        "    'data': {'result': {\n"
        "        'ok': True,\n"
        "        'status': 200,\n"
        "        'contentType': 'image/webp',\n"
        f"        'bodyBase64': '{payload}',\n"
        "    }},\n"
        "    'error': None,\n"
        "}))\n"
        "PY\n"
        "    exit 0 ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)


def test_asset_download_uses_supported_browser_fallback_after_curl_403(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    public = tmp_path / "impl" / "public"
    ref.mkdir()
    expected = b"webp-bytes-from-browser"
    (ref / "head.json").write_text(
        json.dumps({"url": "https://source.example/"}),
        encoding="utf-8",
    )
    (ref / "visible-images.json").write_text(
        json.dumps([
            {"src": "https://cdn.example/assets/hero.webp"},
        ]),
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls_log = tmp_path / "agent-browser-calls.log"
    _write_fake_curl(bin_dir)
    _write_fake_agent_browser(bin_dir, calls_log, expected)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(public)],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        cwd=ROOT,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    dest = public / "assets" / "hero.webp"
    assert dest.read_bytes() == expected

    log = json.loads((ref / "download-log.json").read_text(encoding="utf-8"))
    assert log["succeeded"] == 1
    assert log["failed"] == 0
    assert log["attempts"][0]["via"] == "agent-browser-eval"

    calls = calls_log.read_text(encoding="utf-8")
    assert " fetch " not in f" {calls} "
    assert " open " in f" {calls} "
    assert " eval " in f" {calls} "
