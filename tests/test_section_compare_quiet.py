"""SECTION_COMPARE_QUIET=1 keeps progress chatter out of the agent's context.

Quiet mode must not change what the compare measures, the artifacts it writes,
or its exit code: it only diverts the progress log to
<dir>/sections/section-compare.log and prints the result table, verdict lines,
exit code, and paths.
"""

import os
import subprocess
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "visual-debug"
    / "scripts"
    / "section-compare.sh"
)

_ROW = "| hero | 0 | 0 | ok | ✅ |"
_SUMMARY = "**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY, 0 UNMEASURED**"


def _stub_env(tmp_path: Path, inner_exit: int) -> dict[str, str]:
    """Drive the VIEWPORTS fan-out through a stub inner runner (no browser)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    agent_browser = bin_dir / "agent-browser"
    agent_browser.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    agent_browser.chmod(0o755)
    inner = tmp_path / "inner.sh"
    inner.write_text(
        "#!/usr/bin/env bash\n"
        'mkdir -p "$4/sections"\n'
        "for i in $(seq 1 40); do echo \"▸ progress step $i\"; done\n"
        "{\n"
        "  echo '| Section | AE | AE/Mpx | Severity | Status |'\n"
        "  echo '|---------|-----|--------|----------|--------|'\n"
        f"  echo '{_ROW}'\n"
        "  echo ''\n"
        f"  echo '{_SUMMARY}'\n"
        '} > "$4/sections/result.txt"\n'
        "echo '  ✓ Section-compare passed — Stop hook will record completion on next write.'\n"
        f"exit {inner_exit}\n",
        encoding="utf-8",
    )
    inner.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["VIEWPORTS"] = "1440x900"
    env["SECTION_COMPARE_INNER_CMD"] = str(inner)
    return env


def _run(ref: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            str(_SCRIPT),
            "https://ref.example",
            "https://impl.example",
            "quiet-session",
            str(ref),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


def test_quiet_mode_prints_result_table_and_paths_only(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    env = _stub_env(tmp_path, inner_exit=0)
    env["SECTION_COMPARE_QUIET"] = "1"
    proc = _run(ref, env)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[1440x900] hero" in proc.stdout
    assert "Result: 1 PASS" in proc.stdout
    assert "section-compare exit: 0" in proc.stdout
    assert str(ref / "sections" / "result.txt") in proc.stdout
    # Progress lines are diverted to the log, not stdout.
    assert "▸ section-compare viewport" not in proc.stdout
    log = ref / "sections" / "section-compare.log"
    assert "▸ section-compare viewport 1440x900" in log.read_text(encoding="utf-8")
    # The per-viewport inner run stays verbose in its own log.
    inner_log = ref / "sections" / "viewports" / "1440x900" / "section-compare.log"
    assert "▸ progress step 40" in inner_log.read_text(encoding="utf-8")
    assert not list((ref / "sections").glob(".quiet-stamp.*"))


def test_quiet_mode_preserves_failing_exit_code(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    env = _stub_env(tmp_path, inner_exit=1)
    env["SECTION_COMPARE_QUIET"] = "1"
    proc = _run(ref, env)

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "section-compare exit: 1" in proc.stdout


def test_default_mode_output_is_unchanged(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    env = _stub_env(tmp_path, inner_exit=0)
    env.pop("SECTION_COMPARE_QUIET", None)
    proc = _run(ref, env)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "▸ section-compare viewport 1440x900" in proc.stdout
    assert "section-compare exit:" not in proc.stdout
    assert not (ref / "sections" / "section-compare.log").exists()
