"""ae-compare.sh must fail closed on compare errors and normalize quantum AE."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "ae-compare.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _run(tmp_path: Path, compare_body: str, identify_body: str | None = None) -> subprocess.CompletedProcess[str]:
    ref = tmp_path / "ref.png"
    impl = tmp_path / "impl.png"
    ref.write_bytes(b"x")
    impl.write_bytes(b"x")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "identify",
        identify_body
        or """#!/usr/bin/env bash
case "$2" in
  "%wx%h") printf '30x30' ;;
  "%w") printf '30' ;;
  "%h") printf '30' ;;
esac
""",
    )
    _write_executable(bin_dir / "convert", '#!/usr/bin/env bash\ntouch "${@: -1}"\n')
    _write_executable(bin_dir / "compare", compare_body)
    # The quantum probe prefers `magick compare`; route it to the fake compare.
    _write_executable(
        bin_dir / "magick",
        '#!/usr/bin/env bash\nif [ "$1" = compare ]; then shift; exec "$(dirname "$0")/compare" "$@"; fi\n'
        'touch "${@: -1}"\n',
    )
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env.update({"LC_ALL": "C", "LANG": "C"})
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        env=env, capture_output=True, text=True, timeout=20, check=False,
    )


def test_compare_error_text_is_error_not_pass(tmp_path: Path) -> None:
    proc = _run(
        tmp_path,
        """#!/usr/bin/env bash
echo "compare: improper image header 'ref.png' @ error/png.c/ReadPNGImage/4092." >&2
exit 2
""",
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "STATUS=ERROR" in proc.stdout
    assert "STATUS=PASS" not in proc.stdout


def test_non_numeric_output_with_exit_one_is_error(tmp_path: Path) -> None:
    proc = _run(tmp_path, "#!/usr/bin/env bash\necho 'unable to open image' >&2\nexit 1\n")
    assert proc.returncode == 2
    assert "STATUS=ERROR" in proc.stdout


def test_unreadable_image_dimensions_is_error(tmp_path: Path) -> None:
    proc = _run(
        tmp_path,
        "#!/usr/bin/env bash\necho 0 >&2\nexit 0\n",
        identify_body="#!/usr/bin/env bash\nexit 1\n",
    )
    assert proc.returncode == 2
    assert "STATUS=ERROR" in proc.stdout


def test_quantum_scaled_ae_is_normalized(tmp_path: Path) -> None:
    # IM7 HDRI: 4-pixel probe reports 4*65535; real diff of 100 px reports 100*65535.
    proc = _run(
        tmp_path,
        """#!/usr/bin/env bash
if [ "${@: -1}" = "null:" ]; then v=4; else v=100; fi
echo "$((v * 65535))" >&2
exit 1
""",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "AE=100 STATUS=PASS" in proc.stdout
