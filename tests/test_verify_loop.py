from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify" / "verify-loop.sh"


def _resolve_transcript_dir(loop_dir: str, *, home: Path, n: int) -> str:
    source = SCRIPT.read_text()
    criteria = source.split("# 6. Criteria evaluation + report", 1)[1].split(
        "TRANSCRIPT=", 1
    )[0]
    assignments = "\n".join(
        line
        for line in criteria.splitlines()
        if line.startswith(("CLAUDE_", "TS_DIR="))
    )
    proc = subprocess.run(
        [
            "bash",
            "-c",
            f'set -euo pipefail\n{assignments}\nprintf "%s" "$TS_DIR"',
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HOME": str(home),
            "LOOPDIR": loop_dir,
            "N": str(n),
        },
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_transcript_dir_is_derived_from_the_runtime_loop_path(tmp_path: Path) -> None:
    loop_dir = "/Users/tester/Workspace/renamed ui-clone-skills/scratch/loop-7"

    actual = _resolve_transcript_dir(loop_dir, home=tmp_path, n=7)

    assert actual == (
        f"{tmp_path}/.claude/projects/"
        "-Users-tester-Workspace-renamed-ui-clone-skills-scratch-loop-7"
    )
    assert "Documents-ui-skills" not in SCRIPT.read_text()
