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


def _resolve_transcript(
    *, home: Path, loop_dir: str, transcript_env: str | None
) -> subprocess.CompletedProcess[str]:
    """Run the transcript-selection block (CLAUDE_PROJECT_KEY .. TRANSCRIPT) in isolation."""
    source = SCRIPT.read_text()
    criteria = source.split("# 6. Criteria evaluation + report", 1)[1]
    block = criteria[criteria.index("CLAUDE_PROJECT_KEY=") : criteria.index("\npython3 - ")]
    env = {**os.environ, "HOME": str(home), "LOOPDIR": loop_dir, "N": "7"}
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("UI_CLONE_TRANSCRIPT", None)
    if transcript_env is not None:
        env["UI_CLONE_TRANSCRIPT"] = transcript_env
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail\n{block}\nprintf "%s" "${{TRANSCRIPT:-}}"'],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


def test_unreadable_transcript_override_warns_and_skips_instead_of_falling_back(
    tmp_path: Path,
) -> None:
    loop_dir = "/work/ui-clone-skills/scratch/loop-7"
    claude_dir = tmp_path / ".claude" / "projects" / "-work-ui-clone-skills-scratch-loop-7"
    claude_dir.mkdir(parents=True)
    fallback = claude_dir / "session.jsonl"
    fallback.write_text("{}\n")

    # Unset: the derived Claude Code transcript is used.
    derived = _resolve_transcript(home=tmp_path, loop_dir=loop_dir, transcript_env=None)
    assert derived.returncode == 0, derived.stderr
    assert derived.stdout == str(fallback)

    # Set but unreadable: warn on stderr, skip, and never fall back.
    missing = tmp_path / "nope.jsonl"
    proc = _resolve_transcript(home=tmp_path, loop_dir=loop_dir, transcript_env=str(missing))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    assert "UI_CLONE_TRANSCRIPT" in proc.stderr and "not readable" in proc.stderr
    assert str(missing) in proc.stderr

    # Set and readable: used verbatim.
    explicit = tmp_path / "explicit.jsonl"
    explicit.write_text("{}\n")
    proc = _resolve_transcript(home=tmp_path, loop_dir=loop_dir, transcript_env=str(explicit))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == str(explicit)
    assert proc.stderr == ""


def test_transcript_dir_is_derived_from_the_runtime_loop_path(tmp_path: Path) -> None:
    loop_dir = "/Users/tester/Workspace/renamed ui-clone-skills/scratch/loop-7"

    actual = _resolve_transcript_dir(loop_dir, home=tmp_path, n=7)

    assert actual == (
        f"{tmp_path}/.claude/projects/"
        "-Users-tester-Workspace-renamed-ui-clone-skills-scratch-loop-7"
    )
    assert "Documents-ui-skills" not in SCRIPT.read_text()
