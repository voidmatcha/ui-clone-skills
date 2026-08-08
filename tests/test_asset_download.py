from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "extract" / "asset-download.sh"


def test_asset_download_shell_avoids_large_python_heredoc() -> None:
    shell = SCRIPT.read_text(encoding="utf-8")
    helper = SCRIPT.parent / "asset_download.py"

    assert "<<" not in shell
    assert '"$PYTHON_BIN" "$SCRIPT_DIR/asset_download.py"' in shell
    assert '"$REPO_ROOT/.venv/bin/python3"' in shell
    assert helper.is_file()


def test_asset_download_prefers_repo_venv_over_path_python(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    public = tmp_path / "impl" / "public"
    ref.mkdir()
    (ref / "visible-images.json").write_text("[]", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "path-python-used"
    fake_python = bin_dir / "python3"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        f"touch '{marker}'\n"
        "exit 91\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    env = os.environ.copy()
    env.pop("PYTHON_BIN", None)
    env.pop("VIRTUAL_ENV", None)
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
    assert not marker.exists()


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


def _write_success_curl_with_args_log(bin_dir: Path, args_log: Path) -> None:
    """Fake curl that records arguments before writing bytes to -o."""
    fake = bin_dir / "curl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> '{args_log}'\n"
        "out=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = '-o' ]; then out=\"$2\"; shift 2; continue; fi\n"
        "  shift\n"
        "done\n"
        "if [ -n \"$out\" ]; then head -c 800 /dev/zero | tr '\\0' 'J' > \"$out\"; fi\n"
        "printf '200'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)


def test_asset_download_mirrors_required_lottie_without_visible_images(tmp_path: Path) -> None:
    """Bundle-discovered Lottie JSON is required media even when it is never a
    visible DOM image; asset-download must not skip when visible-images.json is
    absent."""
    ref = tmp_path / "ref"
    public = tmp_path / "impl" / "public"
    ref.mkdir()
    (ref / "head.json").write_text(
        json.dumps({"url": "https://reference.example/"}),
        encoding="utf-8",
    )
    (ref / "required-media.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "videos": [],
            "lottie": [{"path": "/img/lottie/reference-main-intro.json"}],
            "svgs": [],
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
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        cwd=ROOT,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (public / "img" / "lottie" / "reference-main-intro.json").exists()
    log = json.loads((ref / "download-log.json").read_text(encoding="utf-8"))
    assert log["totalAttempted"] == 1
    assert log["succeeded"] == 1
    assert log["failed"] == 0


def test_asset_download_mirrors_required_video_to_scaffold_path(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    public = tmp_path / "impl" / "public"
    ref.mkdir()
    (ref / "head.json").write_text(
        json.dumps({"url": "https://reference.example/"}),
        encoding="utf-8",
    )
    (ref / "required-media.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "videos": [{"src": "https://cdn.example/assets/media/hero.mp4"}],
            "lottie": [],
            "svgs": [],
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
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        cwd=ROOT,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (public / "videos" / "hero.mp4").exists()
    log = json.loads((ref / "download-log.json").read_text(encoding="utf-8"))
    assert log["attempts"][0]["dest"].endswith("public/videos/hero.mp4")


def test_asset_download_harvests_vimeo_oembed_thumbnail(tmp_path: Path) -> None:
    """Vimeo embeds are localized to <video poster=/videos/vimeo-<id>.jpg>;
    asset-download must mirror the oEmbed thumbnail into that stable path even
    when the hero iframe is not reported as a visible image."""
    ref = tmp_path / "ref"
    public = tmp_path / "impl" / "public"
    oembed_dir = ref / "resources" / "vimeo.com" / "api"
    oembed_dir.mkdir(parents=True)
    (ref / "head.json").write_text(
        json.dumps({"url": "https://playbook.example/"}),
        encoding="utf-8",
    )
    (ref / "visible-images.json").write_text("[]", encoding="utf-8")
    (oembed_dir / "oembed-44bfb360.json").write_text(
        json.dumps({
            "video_id": 949632393,
            "thumbnail_url": "https://i.vimeocdn.com/video/1857665641-d_1280",
        }),
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    args_log = tmp_path / "curl-args.log"
    _write_success_curl_with_args_log(bin_dir, args_log)
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
    assert (public / "videos" / "vimeo-949632393.jpg").exists()
    log = json.loads((ref / "download-log.json").read_text(encoding="utf-8"))
    assert log["totalAttempted"] == 1
    assert log["succeeded"] == 1
    assert log["failed"] == 0
    assert log["attempts"][0]["dest"].endswith("public/videos/vimeo-949632393.jpg")
    curl_args = args_log.read_text(encoding="utf-8")
    assert "Accept: image/jpeg,image/*;q=0.9,*/*;q=0.8" in curl_args
    assert "image/avif" not in curl_args


def test_asset_download_harvests_vimeo_progressive_signed_mp4(tmp_path: Path) -> None:
    """Vimeo progressive URLs include a signed query escaped inside captured
    JSON; truncating at `.mp4` drops the token and 403s."""
    ref = tmp_path / "ref"
    public = tmp_path / "impl" / "public"
    ref.mkdir()
    signed_url = (
        "https://player.vimeo.com/progressive_redirect/playback/949632393/"
        "rendition/1080p/file.mp4%20%281080p%29.mp4"
        "?loc=external\\\\u0026oauth2_token_id=1747418641"
        "\\\\u0026signature=abc123\\\\\\"
    )
    (ref / "head.json").write_text(
        json.dumps({"url": "https://playbook.example/"}),
        encoding="utf-8",
    )
    (ref / "visible-images.json").write_text("[]", encoding="utf-8")
    (ref / "structure.json").write_text(
        '{"tag":"iframe","src":"' + signed_url + '"}',
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    args_log = tmp_path / "curl-args.log"
    _write_success_curl_with_args_log(bin_dir, args_log)
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
    assert (public / "videos" / "vimeo-949632393.mp4").exists()
    log = json.loads((ref / "download-log.json").read_text(encoding="utf-8"))
    assert log["totalAttempted"] == 1
    assert log["failed"] == 0
    assert log["attempts"][0]["dest"].endswith("public/videos/vimeo-949632393.mp4")
    curl_args = args_log.read_text(encoding="utf-8")
    assert "https://player.vimeo.com/progressive_redirect/playback/949632393/" in curl_args
    assert "?loc=external&oauth2_token_id=1747418641&signature=abc123" in curl_args
    assert "signature=abc123\\" not in curl_args
    assert "%20%281080p%29.mp4" in curl_args


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


def test_asset_download_harvests_url_with_decoy_middle_extension(tmp_path: Path) -> None:
    """A CDN image URL whose MIDDLE path segment ends in a decoy `.PNG` before
    the real trailing `/<file>.png` segment must be harvested in full. The DOM-
    harvest regex must be greedy: a non-greedy quantifier truncates the URL at
    the first (fake) extension, downloading to the wrong path so the served
    <img src> (which keeps the full path) 404s. Placeholder host; the bug is in
    the URL *shape*, not the host."""
    ref = tmp_path / "ref"
    public = tmp_path / "impl" / "public"
    ref.mkdir()
    (ref / "head.json").write_text(json.dumps({"url": "https://ref.example/"}), encoding="utf-8")
    # a visible image so the script does not early-SKIP; the URL under test is
    # the structure-only one harvested by the DOM regex.
    (ref / "visible-images.json").write_text(
        json.dumps([{"src": "https://cdn.example.net/images/logo.png"}]),
        encoding="utf-8",
    )
    full = (
        "https://cdn.example.net/MjAySEG1AA/"
        "MDAxHASHaa.tok1AAAA.tok2BBBB.PNG/photo_wide.png?type=w960"
    )
    (ref / "structure.json").write_text(
        json.dumps({"tag": "body", "children": [{"tag": "img", "src": full}]}),
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
    # The file must land at the FULL path (query stripped) — exactly what the
    # scaffold's rewrite_asset_url emits as the <img src>.
    dest = public / "MjAySEG1AA" / "MDAxHASHaa.tok1AAAA.tok2BBBB.PNG" / "photo_wide.png"
    assert dest.exists(), (
        "decoy-middle-extension URL must download to its full path, not a "
        "truncated one"
    )
    log = json.loads((ref / "download-log.json").read_text(encoding="utf-8"))
    assert log["failed"] == 0
    assert log["successRate"] == 100.0, "skipped-existing files must count as success"
