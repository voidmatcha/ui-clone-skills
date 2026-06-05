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

def _write_success_curl(bin_dir: Path) -> None:
    """Fake curl that 'downloads' by writing bytes to -o and reporting 200."""
    fake = bin_dir / "curl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "out=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = '-o' ]; then out=\"$2\"; shift 2; continue; fi\n"
        "  shift\n"
        "done\n"
        "if [ -n \"$out\" ]; then head -c 800 /dev/zero | tr '\\0' 'W' > \"$out\"; fi\n"
        "printf '200'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)


def test_asset_download_harvests_image_urls_from_structure(tmp_path: Path) -> None:
    """Images referenced in the captured DOM (structure.json) but missing from
    visible-images.json must still be downloaded. RealFood: intro/ images appear
    in structure.json but were never captured as 'visible', so they 404'd."""
    ref = tmp_path / "ref"
    public = tmp_path / "impl" / "public"
    ref.mkdir()
    (ref / "head.json").write_text(json.dumps({"url": "https://realfood.gov/"}), encoding="utf-8")
    # visible-images has only the pyramid image, NOT the intro one.
    (ref / "visible-images.json").write_text(
        json.dumps([
            {"src": "https://realfood.gov/cdn-cgi/image/width=3840,quality=90,format=auto,fit=scale-down/images/pyramid/broccoli.webp"},
        ]),
        encoding="utf-8",
    )
    # structure.json (captured DOM) references an intro/ image absent above,
    # as a ROOT-RELATIVE cdn-cgi src (realfood's real form) — must be resolved
    # against the origin from head.json and downloaded.
    (ref / "structure.json").write_text(
        json.dumps({
            "tag": "body",
            "children": [
                {"tag": "img", "src": "/cdn-cgi/image/width=2048,quality=90,format=auto,fit=scale-down/images/intro/broccoli.webp"},
                # width-variant of the visible pyramid image → same local dest,
                # must be counted as a present success, not drag the rate down.
                {"tag": "img", "src": "/cdn-cgi/image/width=1080,quality=90,format=auto,fit=scale-down/images/pyramid/broccoli.webp"},
            ],
        }),
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_success_curl(bin_dir)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(public)],
        capture_output=True, text=True, env=env, timeout=20, cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # both the visible pyramid image AND the structure-only intro image land.
    assert (public / "images" / "pyramid" / "broccoli.webp").exists()
    assert (public / "images" / "intro" / "broccoli.webp").exists(), \
        "intro/ image referenced in structure.json must be downloaded"
    # width-variant duplicates resolve to an existing file (skipped-exists) and
    # must not be scored as failures — a present file is a success.
    log = json.loads((ref / "download-log.json").read_text(encoding="utf-8"))
    assert log["failed"] == 0
    assert log["successRate"] == 100.0, "skipped-existing files must count as success"
