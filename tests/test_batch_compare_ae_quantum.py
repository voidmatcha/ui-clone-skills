"""Regression tests for ImageMagick AE normalization in batch-compare."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "batch-compare.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _run_batch_compare(tmp_path: Path, divisor: int) -> subprocess.CompletedProcess[str]:
    capture_dir = tmp_path / "capture"
    ref_dir = capture_dir / "static" / "ref"
    impl_dir = capture_dir / "static" / "impl"
    ref_dir.mkdir(parents=True)
    impl_dir.mkdir(parents=True)
    (ref_dir / "0pct.png").touch()
    (impl_dir / "0pct.png").touch()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "identify",
        """#!/usr/bin/env bash
case "$2" in
  "%wx%h") printf '30x30' ;;
  "%w") printf '30' ;;
  "%h") printf '30' ;;
esac
""",
    )
    _write_executable(
        bin_dir / "convert",
        """#!/usr/bin/env bash
touch "${@: -1}"
""",
    )
    _write_executable(
        bin_dir / "compare",
        f"""#!/usr/bin/env bash
if [[ "$*" == *"-extract"* ]]; then
  case "$*" in
    *"+0+0"*) value=100 ;;
    *"+0+10"*) value=400 ;;
    *"+0+20"*) value=200 ;;
  esac
elif [ "${{@: -1}}" = "null:" ]; then
  value=4
else
  value=600
fi
printf '%s\\n' "$((value * {divisor}))" >&2
exit 1
""",
    )
    _write_executable(
        bin_dir / "magick",
        f"""#!/usr/bin/env bash
if [ "$1" = "compare" ]; then
  printf '%s\\n' "$((4 * {divisor}))" >&2
  exit 1
fi
touch "${{@: -1}}"
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env.update({"LC_ALL": "C", "LC_CTYPE": "C", "LANG": "C"})
    return subprocess.run(
        ["bash", str(SCRIPT), str(capture_dir), "500"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


@pytest.mark.parametrize("divisor", [1, 255, 65535], ids=["raw", "q8", "q16"])
def test_normalizes_total_and_per_third_ae(tmp_path: Path, divisor: int) -> None:
    result = _run_batch_compare(tmp_path, divisor)

    assert result.returncode == 1
    assert "| 0pct | 600 | 500 | ❌ | mid (400) |" in result.stdout, result.stderr
    if divisor > 1:
        assert str(600 * divisor) not in result.stdout
        assert str(400 * divisor) not in result.stdout
