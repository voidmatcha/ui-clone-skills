from __future__ import annotations

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "extract" / "asset-download.sh"


class _BytesHandler(BaseHTTPRequestHandler):
    body = b"W" * 1500

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        reported_length = (
            30 * 1024 * 1024 if self.path.endswith(".mp4") else len(self.body)
        )
        self.send_header("Content-Length", str(reported_length))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _server_base_url() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _BytesHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{str(host)}:{port}"


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


def test_asset_download_uses_guarded_downloader(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    public = tmp_path / "impl" / "public"
    ref.mkdir()
    server, base_url = _server_base_url()
    try:
        (ref / "head.json").write_text(
            json.dumps({"url": f"{base_url}/"}),
            encoding="utf-8",
        )
        (ref / "visible-images.json").write_text(
            json.dumps([
                {"src": f"{base_url}/assets/hero.webp"},
            ]),
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["PYTHON_BIN"] = sys.executable
        env["UI_CLONE_RESOURCE_MIRROR_ALLOW_PRIVATE"] = "1"
        proc = subprocess.run(
            ["bash", str(SCRIPT), str(ref), str(public)],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
            cwd=ROOT,
        )
    finally:
        server.shutdown()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    dest = public / "assets" / "hero.webp"
    assert dest.read_bytes() == _BytesHandler.body

    log = json.loads((ref / "download-log.json").read_text(encoding="utf-8"))
    assert log["succeeded"] == 1
    assert log["failed"] == 0
    assert log["attempts"][0]["via"] == "guarded-urlopen"


def test_asset_download_blocks_decoded_path_traversal(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    public = tmp_path / "impl" / "public"
    ref.mkdir()
    (ref / "visible-images.json").write_text(
        json.dumps([
            {"src": "https://cdn.example/%2e%2e/escaped.txt"},
        ]),
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_success_curl(bin_dir)
    env = os.environ.copy()
    env["PYTHON_BIN"] = sys.executable
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
    assert not (tmp_path / "impl" / "escaped.txt").exists()
    log = json.loads((ref / "download-log.json").read_text(encoding="utf-8"))
    assert log["attempts"][0]["status"] == "blocked"
    assert log["attempts"][0]["reason"] == "unsafe-destination"


def test_asset_download_blocks_symlink_escape(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    public = tmp_path / "impl" / "public"
    outside = tmp_path / "outside"
    ref.mkdir()
    public.mkdir(parents=True)
    outside.mkdir()
    (public / "assets").symlink_to(outside, target_is_directory=True)
    (ref / "visible-images.json").write_text(
        json.dumps([{"src": "https://cdn.example/assets/escaped.txt"}]),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(public)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHON_BIN": sys.executable},
        timeout=20,
        cwd=ROOT,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (outside / "escaped.txt").exists()
    log = json.loads((ref / "download-log.json").read_text(encoding="utf-8"))
    assert log["attempts"][0]["status"] == "blocked"
    assert log["attempts"][0]["reason"] == "unsafe-destination"


def test_asset_download_blocks_link_local_url_before_curl(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    public = tmp_path / "impl" / "public"
    ref.mkdir()
    (ref / "visible-images.json").write_text(
        json.dumps([
            {"src": "http://169.254.169.254/latest/meta-data/iam"},
        ]),
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "curl-called"
    fake = bin_dir / "curl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"touch '{marker}'\n"
        "exit 91\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = os.environ.copy()
    env["PYTHON_BIN"] = sys.executable
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
    log = json.loads((ref / "download-log.json").read_text(encoding="utf-8"))
    assert log["attempts"][0]["status"] == "blocked"
    assert log["attempts"][0]["reason"] == "ssrf-blocked"


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


def test_asset_download_mirrors_required_lottie_without_visible_images(tmp_path: Path) -> None:
    """Bundle-discovered Lottie JSON is required media even when it is never a
    visible DOM image; asset-download must not skip when visible-images.json is
    absent."""
    ref = tmp_path / "ref"
    public = tmp_path / "impl" / "public"
    ref.mkdir()
    server, base_url = _server_base_url()
    try:
        (ref / "head.json").write_text(
            json.dumps({"url": f"{base_url}/"}),
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

        env = os.environ.copy()
        env["PYTHON_BIN"] = sys.executable
        env["UI_CLONE_RESOURCE_MIRROR_ALLOW_PRIVATE"] = "1"
        proc = subprocess.run(
            ["bash", str(SCRIPT), str(ref), str(public)],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
            cwd=ROOT,
        )
    finally:
        server.shutdown()

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
    server, base_url = _server_base_url()
    try:
        (ref / "head.json").write_text(
            json.dumps({"url": f"{base_url}/"}),
            encoding="utf-8",
        )
        (ref / "required-media.json").write_text(
            json.dumps({
                "schemaVersion": 1,
                "videos": [{"src": f"{base_url}/assets/media/hero.mp4"}],
                "lottie": [],
                "svgs": [],
            }),
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["PYTHON_BIN"] = sys.executable
        env["UI_CLONE_RESOURCE_MIRROR_ALLOW_PRIVATE"] = "1"
        proc = subprocess.run(
            ["bash", str(SCRIPT), str(ref), str(public)],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
            cwd=ROOT,
        )
    finally:
        server.shutdown()

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
    server, base_url = _server_base_url()
    (ref / "head.json").write_text(json.dumps({"url": f"{base_url}/"}), encoding="utf-8")
    (ref / "visible-images.json").write_text("[]", encoding="utf-8")
    (oembed_dir / "oembed-44bfb360.json").write_text(
        json.dumps({
            "video_id": 949632393,
            "thumbnail_url": f"{base_url}/video/1857665641-d_1280",
        }),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PYTHON_BIN"] = sys.executable
    env["UI_CLONE_RESOURCE_MIRROR_ALLOW_PRIVATE"] = "1"
    try:
        proc = subprocess.run(
            ["bash", str(SCRIPT), str(ref), str(public)],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
            cwd=ROOT,
        )
    finally:
        server.shutdown()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (public / "videos" / "vimeo-949632393.jpg").exists()
    log = json.loads((ref / "download-log.json").read_text(encoding="utf-8"))
    assert log["totalAttempted"] == 1
    assert log["succeeded"] == 1
    assert log["failed"] == 0
    assert log["attempts"][0]["dest"].endswith("public/videos/vimeo-949632393.jpg")
    assert log["attempts"][0]["via"] == "guarded-urlopen"


def test_asset_download_harvests_vimeo_progressive_signed_mp4(tmp_path: Path) -> None:
    """Vimeo progressive URLs include a signed query escaped inside captured
    JSON; truncating at `.mp4` drops the token and 403s."""
    ref = tmp_path / "ref"
    public = tmp_path / "impl" / "public"
    ref.mkdir()
    server, base_url = _server_base_url()
    signed_url = (
        f"{base_url}/progressive_redirect/playback/949632393/"
        "rendition/1080p/file.mp4%20%281080p%29.mp4"
        "?loc=external&oauth2_token_id=1747418641&signature=abc123"
    )
    try:
        (ref / "head.json").write_text(
            json.dumps({"url": f"{base_url}/"}),
            encoding="utf-8",
        )
        (ref / "visible-images.json").write_text("[]", encoding="utf-8")
        (ref / "required-media.json").write_text(
            json.dumps({
                "schemaVersion": 1,
                "videos": [{"src": signed_url}],
                "lottie": [],
                "svgs": [],
            }),
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["PYTHON_BIN"] = sys.executable
        env["UI_CLONE_RESOURCE_MIRROR_ALLOW_PRIVATE"] = "1"
        proc = subprocess.run(
            ["bash", str(SCRIPT), str(ref), str(public)],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
            cwd=ROOT,
        )
    finally:
        server.shutdown()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (public / "videos" / "file.mp4 (1080p).mp4").exists()
    log = json.loads((ref / "download-log.json").read_text(encoding="utf-8"))
    assert log["totalAttempted"] == 1
    assert log["failed"] == 0
    assert log["attempts"][0]["dest"].endswith("public/videos/file.mp4 (1080p).mp4")
    assert "?loc=external&oauth2_token_id=1747418641&signature=abc123" in log["attempts"][0]["url"]
    assert "%20%281080p%29.mp4" in log["attempts"][0]["url"]


def test_asset_download_harvests_image_urls_from_structure(tmp_path: Path) -> None:
    """Images referenced in the captured DOM (structure.json) but missing from
    visible-images.json must still be downloaded. RealFood: intro/ images appear
    in structure.json but were never captured as 'visible', so they 404'd."""
    ref = tmp_path / "ref"
    public = tmp_path / "impl" / "public"
    ref.mkdir()
    server, base_url = _server_base_url()
    try:
        (ref / "head.json").write_text(json.dumps({"url": f"{base_url}/"}), encoding="utf-8")
        (ref / "visible-images.json").write_text(
            json.dumps([
                {"src": f"{base_url}/cdn-cgi/image/width=3840,quality=90,format=auto,fit=scale-down/images/pyramid/broccoli.webp"},
            ]),
            encoding="utf-8",
        )
        (ref / "structure.json").write_text(
            json.dumps({
                "tag": "body",
                "children": [
                    {"tag": "img", "src": "/cdn-cgi/image/width=2048,quality=90,format=auto,fit=scale-down/images/intro/broccoli.webp"},
                    {"tag": "img", "src": "/cdn-cgi/image/width=1080,quality=90,format=auto,fit=scale-down/images/pyramid/broccoli.webp"},
                ],
            }),
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["PYTHON_BIN"] = sys.executable
        env["UI_CLONE_RESOURCE_MIRROR_ALLOW_PRIVATE"] = "1"
        proc = subprocess.run(
            ["bash", str(SCRIPT), str(ref), str(public)],
            capture_output=True, text=True, env=env, timeout=20, cwd=ROOT,
        )
    finally:
        server.shutdown()
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
    server, base_url = _server_base_url()
    try:
        (ref / "head.json").write_text(json.dumps({"url": f"{base_url}/"}), encoding="utf-8")
        (ref / "visible-images.json").write_text(
            json.dumps([{"src": f"{base_url}/images/logo.png"}]),
            encoding="utf-8",
        )
        full = (
            f"{base_url}/MjAySEG1AA/"
            "MDAxHASHaa.tok1AAAA.tok2BBBB.PNG/photo_wide.png?type=w960"
        )
        (ref / "structure.json").write_text(
            json.dumps({"tag": "body", "children": [{"tag": "img", "src": full}]}),
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["PYTHON_BIN"] = sys.executable
        env["UI_CLONE_RESOURCE_MIRROR_ALLOW_PRIVATE"] = "1"
        proc = subprocess.run(
            ["bash", str(SCRIPT), str(ref), str(public)],
            capture_output=True, text=True, env=env, timeout=20, cwd=ROOT,
        )
    finally:
        server.shutdown()
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
