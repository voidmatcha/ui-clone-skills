import json
import os
import subprocess
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def test_auto_verify_does_not_block_on_original_curl_403(tmp_path: Path) -> None:
    """Some browser-loadable origins reject raw curl preflight requests."""
    root = _project_root()
    ref = tmp_path / "ref"
    sections = ref / "sections"
    sections.mkdir(parents=True)
    (ref / "asset-substitution.json").write_text(json.dumps({
        "structuralOnlySections": ["hero"],
    }))
    (sections / "result.txt").write_text(
        "**Result: 1 PASS, 0 FAIL, 0 SKIP, 1 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    impl = tmp_path / "impl"
    impl.mkdir()
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")

    server_code = """
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

server = ThreadingHTTPServer(("127.0.0.1", 0), SimpleHTTPRequestHandler)
print(server.server_address[1], flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
"""
    server_proc = subprocess.Popen(
        [sys.executable, "-c", server_code],
        cwd=impl,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert server_proc.stdout is not None
    port = int(server_proc.stdout.readline().strip())

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "curl",
        """#!/usr/bin/env bash
url="${@: -1}"
case "$url" in
  https://readymag.com/*) printf "403" ;;
  *) printf "200" ;;
esac
""",
    )
    _write_executable(fake_bin / "agent-browser", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "uv", "#!/usr/bin/env bash\nexit 0\n")

    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    env["PLUGIN_ROOT"] = str(root)

    try:
        proc = subprocess.run(
            [
                "bash",
                str(root / "scripts" / "verify" / "auto-verify.sh"),
                "readymag-auto",
                "https://readymag.com/",
                f"http://127.0.0.1:{port}/",
                str(ref),
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "WARN" in proc.stdout
    stamp = json.loads((ref / "visual-debug-stamp.json").read_text())
    assert stamp["stampedBy"] == "scripts/verify/auto-verify.sh"
