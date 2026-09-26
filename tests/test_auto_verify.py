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
    _write_executable(fake_bin / "bash", """#!/bin/bash
case "$1" in */run-required-checks.sh) exit 0 ;; esac
exec /bin/bash "$@"
""")

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


def test_auto_verify_runs_prerequisites_before_completion(tmp_path: Path) -> None:
    import shutil

    repo = tmp_path / "repo"
    scripts = repo / "scripts" / "verify"
    scripts.mkdir(parents=True)
    shutil.copy(_project_root() / "scripts/verify/auto-verify.sh", scripts)
    _write_executable(scripts / "run-required-checks.sh", '''#!/usr/bin/env bash
printf 'required\n' >> "$CALLS"
exit "${REQUIRED_EXIT:-0}"
''')
    visual = tmp_path / "visual"
    visual.mkdir()
    (visual / "ae-compare.sh").touch()
    _write_executable(visual / "layout-health-check.sh", '''#!/usr/bin/env bash
printf 'layout\n' >> "$CALLS"
exit 0
''')
    ref = tmp_path / "ref"
    (ref / "sections").mkdir(parents=True)
    (ref / "sections/result.txt").write_text("**Result: 1 PASS, 0 FAIL**\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "curl", "#!/usr/bin/env bash\nprintf 200\n")
    _write_executable(fake_bin / "agent-browser", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "sleep", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "uv", '''#!/usr/bin/env bash
printf '%s\n' "${@: -1}" >> "$CALLS"
if [ "${@: -1}" = state-coverage ]; then
  [ ! -f "${@: -2:1}/visual-debug-stamp.json" ] || exit 99
  exit "${STATE_EXIT:-0}"
fi
exit 0
''')
    calls = tmp_path / "calls"
    env = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ["PATH"],
               CALLS=str(calls), VISUAL_DEBUG_SCRIPTS_DIR=str(visual))
    for state_exit, required_exit, expected in [
        (0, 0, ["state-coverage", "required", "post-implement"]),
        (1, 0, ["state-coverage"]),
        (0, 1, ["state-coverage", "required"]),
    ]:
        calls.write_text("")
        if required_exit:
            (ref / "sections/result.txt").unlink()
        (ref / "visual-debug-stamp.json").write_text(json.dumps({"passed": True}))
        proc = subprocess.run(
            ["bash", str(scripts / "auto-verify.sh"), "test", "https://ref.test/",
             "https://impl.test/", str(ref)],
            env=dict(env, STATE_EXIT=str(state_exit), REQUIRED_EXIT=str(required_exit)),
            capture_output=True, text=True, timeout=15,
        )
        assert calls.read_text().splitlines() == expected, proc.stdout + proc.stderr
        assert proc.returncode == (1 if state_exit or required_exit else 0)
        stamp = json.loads((ref / "visual-debug-stamp.json").read_text())
        assert stamp["passed"] is (not (state_exit or required_exit))
        assert not stamp.get("provisional")


def test_auto_verify_fallback_fails_missing_impl_and_zero_compared(tmp_path: Path) -> None:
    """Fallback AE loop: missing impl captures fail; nothing compared never passes."""
    import shutil

    repo = tmp_path / "repo"
    scripts = repo / "scripts" / "verify"
    scripts.mkdir(parents=True)
    shutil.copy(_project_root() / "scripts/verify/auto-verify.sh", scripts)
    _write_executable(scripts / "run-required-checks.sh", "#!/usr/bin/env bash\nexit 0\n")
    visual = tmp_path / "visual"
    visual.mkdir()
    # Would PASS every pair; batch-compare.sh is absent so the fallback runs.
    _write_executable(visual / "ae-compare.sh", "#!/usr/bin/env bash\necho 'AE=0 STATUS=PASS'\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "curl", "#!/usr/bin/env bash\nprintf 200\n")
    # Never writes impl screenshots.
    _write_executable(fake_bin / "agent-browser", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "sleep", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "uv", "#!/usr/bin/env bash\nexit 0\n")
    env = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ["PATH"],
               VISUAL_DEBUG_SCRIPTS_DIR=str(visual))

    for with_refs, marker in [(True, "impl screenshot missing"),
                              (False, "No screenshots compared")]:
        ref = tmp_path / ("ref-with" if with_refs else "ref-empty")
        (ref / "static" / "ref").mkdir(parents=True)
        if with_refs:
            (ref / "static" / "ref" / "0pct.png").write_bytes(b"png")
        proc = subprocess.run(
            ["bash", str(scripts / "auto-verify.sh"), "test", "https://ref.test/",
             "https://impl.test/", str(ref)],
            env=env, capture_output=True, text=True, timeout=30,
        )
        assert marker in proc.stdout, proc.stdout + proc.stderr
        assert "screenshots PASS" not in proc.stdout
        assert proc.returncode == 1, proc.stdout + proc.stderr
        stamp = json.loads((ref / "visual-debug-stamp.json").read_text())
        assert stamp["passed"] is False
