import json
import os
import subprocess
from pathlib import Path
from typing import Any, cast


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _json_tail(stdout: str) -> dict[str, Any]:
    marker = '{\n  "status"'
    start = stdout.rfind(marker)
    assert start != -1, stdout
    return cast(dict[str, Any], json.loads(stdout[start:]))


def test_download_chunks_uses_numeric_duration_when_date_has_no_milliseconds(
    tmp_path: Path,
) -> None:
    """macOS date prints a literal N for %3N; duration JSON must still work."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_date = fake_bin / "date"
    fake_date.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"${1:-}\" = '+%s%3N' ]; then\n"
        "  printf '17797345763N\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exec /bin/date \"$@\"\n",
        encoding="utf-8",
    )
    fake_date.chmod(0o755)

    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        "#!/usr/bin/env bash\n"
        "out=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = '-o' ]; then out=\"$2\"; shift 2; continue; fi\n"
        "  shift\n"
        "done\n"
        "if [ -z \"$out\" ]; then exit 2; fi\n"
        "python3 - \"$out\" <<'PY'\n"
        "from pathlib import Path\n"
        "import sys\n"
        "body = 'gsap.to(\".hero\", { duration: 1, ease: \"power2.out\" });\\n' * 20\n"
        "Path(sys.argv[1]).write_text(body, encoding='utf-8')\n"
        "PY\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    root = _project_root()
    ref = tmp_path / "ref"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "extract" / "download-chunks.sh"),
            str(ref),
            "-",
        ],
        input='["https://example.test/static/app.js"]',
        capture_output=True,
        text=True,
        timeout=20,
        cwd=root,
        env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = _json_tail(proc.stdout)
    assert payload["status"] == "pass"
    assert isinstance(payload["duration_ms"], int)
    assert payload["duration_ms"] >= 0
    assert (ref / "bundle-analysis.json").is_file()
    assert (ref / "bundle-map.json").is_file()
