from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_frozen_wrapper_retries_pass1_with_fresh_session(tmp_path: Path) -> None:
    """A poisoned browser session can make pass 1 exit before ref crops exist.

    The wrapper must not convert one transient pass-1 materialization miss into
    the final verdict. It should retry once with a never-reused session family,
    then continue through calib and measurement without pre-closing guessed
    session names.
    """
    root = _project_root()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    wrapper = scripts / "section-compare-frozen.sh"
    shutil.copy2(
        root / "skills" / "visual-debug" / "scripts" / "section-compare-frozen.sh",
        wrapper,
    )

    calls = tmp_path / "section-compare-calls.txt"
    stub = scripts / "section-compare.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'echo "$3" >> "{calls}"\n'
        'session="$3"\n'
        'out="$4"\n'
        'mkdir -p "$out/sections"\n'
        'case "$session" in\n'
        '  *-base-a2)\n'
        '    mkdir -p "$out/sections/ref"\n'
        '    printf "[]" > "$out/sections/ref-sections.json"\n'
        '    printf "png" > "$out/sections/ref/hero.png"\n'
        '    exit 1\n'
        '    ;;\n'
        '  *-base-a1)\n'
        '    echo "simulated poisoned browser session"\n'
        '    exit 1\n'
        '    ;;\n'
        '  *-cal)\n'
        '    mkdir -p "$out/sections/impl"\n'
        '    printf "png" > "$out/sections/impl/hero.png"\n'
        '    exit 0\n'
        '    ;;\n'
        '  *-run)\n'
        '    printf "final verdict\\n" > "$out/sections/result.txt"\n'
        '    exit 0\n'
        '    ;;\n'
        'esac\n'
        'echo "unexpected session $session" >&2\n'
        'exit 2\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)

    out = tmp_path / "out"
    env = os.environ.copy()
    env["SECTION_FROZEN_RUN_NONCE"] = "testnonce"
    proc = subprocess.run(
        [
            "bash",
            str(wrapper),
            "https://ref.test",
            "http://impl.test",
            "frozen-test",
            str(out),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (out / "sections" / "result.txt").read_text() == "final verdict\n"
    assert (out / "sections" / "ref-calib" / "hero.png").is_file()
    session_calls = calls.read_text().splitlines()
    assert [
        next(suffix for suffix in ("base-a1", "base-a2", "cal", "run") if name.endswith(suffix))
        for name in session_calls
    ] == [
        "base-a1",
        "base-a2",
        "cal",
        "run",
    ]
    assert all(name.startswith("scf-frozen-tes-") for name in session_calls)
    assert len(set(session_calls)) == 4
    assert "pass 1 attempt 1 did not materialize" in proc.stderr
    assert "_close_section_sessions" not in wrapper.read_text(encoding="utf-8")
    assert (
        'RECATCH_REF=1 SECTION_SKIP_IMPL_RESIZE=1'
        in wrapper.read_text(encoding="utf-8")
    )


def test_frozen_wrapper_promotes_ref_self_compare_impl_path_baseline(
    tmp_path: Path,
) -> None:
    root = _project_root()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    wrapper = scripts / "section-compare-frozen.sh"
    shutil.copy2(
        root / "skills" / "visual-debug" / "scripts" / "section-compare-frozen.sh",
        wrapper,
    )

    observed_pixels = tmp_path / "observed-pixels.txt"
    observed_sections = tmp_path / "observed-sections.json"
    stub = scripts / "section-compare.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'session="$3"\n'
        'out="$4"\n'
        'mkdir -p "$out/sections"\n'
        'case "$session" in\n'
        '  *-base-a1)\n'
        '    mkdir -p "$out/sections/ref" "$out/sections/impl"\n'
        '    printf "[{\\"className\\":\\"direct-ref\\"}]" > "$out/sections/ref-sections.json"\n'
        '    printf "[{\\"className\\":\\"impl-path-ref\\"}]" > "$out/sections/impl-sections.json"\n'
        '    printf "direct-ref-pixels" > "$out/sections/ref/hero.png"\n'
        '    printf "impl-path-ref-pixels" > "$out/sections/impl/hero.png"\n'
        '    ;;\n'
        '  *-cal)\n'
        f'    cp "$out/sections/ref/hero.png" "{observed_pixels}"\n'
        f'    cp "$out/sections/ref-sections.json" "{observed_sections}"\n'
        '    mkdir -p "$out/sections/impl"\n'
        '    printf "calibration" > "$out/sections/impl/hero.png"\n'
        '    ;;\n'
        '  *-run) printf "final verdict\\n" > "$out/sections/result.txt" ;;\n'
        'esac\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)

    out = tmp_path / "out"
    env = os.environ.copy()
    env["SECTION_FROZEN_RUN_NONCE"] = "promote-baseline"
    proc = subprocess.run(
        [
            "bash",
            str(wrapper),
            "https://ref.test",
            "http://impl.test",
            "frozen-promote",
            str(out),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert observed_pixels.read_text() == "impl-path-ref-pixels"
    assert "impl-path-ref" in observed_sections.read_text()
    assert "promoted pass-1 impl-path self-capture" in proc.stdout


def test_frozen_wrapper_bounds_long_canonical_sessions_and_cleans_exact_prefixes(
    tmp_path: Path,
) -> None:
    root = _project_root()
    sandbox = tmp_path / "repo"
    scripts = sandbox / "skills" / "visual-debug" / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / "section-compare-frozen.sh"
    shutil.copy2(
        root / "skills" / "visual-debug" / "scripts" / "section-compare-frozen.sh",
        wrapper,
    )

    calls = tmp_path / "calls.txt"
    cleanup_calls = tmp_path / "cleanup-calls.txt"
    stub = scripts / "section-compare.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'echo "$3" >> "{calls}"\n'
        'session="$3"\n'
        'out="$4"\n'
        'mkdir -p "$out/sections"\n'
        'case "$session" in\n'
        '  *-base-a1) exit 1 ;;\n'
        '  *-base-a2)\n'
        '    mkdir -p "$out/sections/ref"\n'
        '    printf "[]" > "$out/sections/ref-sections.json"\n'
        '    printf "png" > "$out/sections/ref/hero.png"\n'
        '    ;;\n'
        '  *-cal)\n'
        '    mkdir -p "$out/sections/impl"\n'
        '    printf "png" > "$out/sections/impl/hero.png"\n'
        '    ;;\n'
        '  *-run) printf "final verdict\\n" > "$out/sections/result.txt" ;;\n'
        '  *) exit 2 ;;\n'
        'esac\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)

    cleanup = sandbox / "scripts" / "verify" / "cleanup-sessions.sh"
    cleanup.parent.mkdir(parents=True)
    cleanup.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$1" >> "{cleanup_calls}"\n',
        encoding="utf-8",
    )
    cleanup.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    agent_browser = bin_dir / "agent-browser"
    agent_browser.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    agent_browser.chmod(0o755)

    long_session = (
        "dogfood-docs-canonical-final3-20260730-section-compare-frozen-"
        "comprehensive-verification"
    )
    all_calls: list[list[str]] = []
    for nonce in ("canonical-run-one", "canonical-run-two"):
        out = tmp_path / f"out-{nonce}"
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
        env["SECTION_FROZEN_RUN_NONCE"] = nonce
        proc = subprocess.run(
            [
                "bash",
                str(wrapper),
                "https://ref.test",
                "http://impl.test",
                long_session,
                str(out),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (out / "sections" / "result.txt").is_file()
        run_calls = calls.read_text().splitlines()[-4:]
        all_calls.append(run_calls)

    flattened = [name for run_calls in all_calls for name in run_calls]
    assert len(set(flattened)) == 8
    assert all(name.startswith("scf-dogfood-do-") for name in flattened)
    assert all(len(f"{name}-1920x1080-sc-impl") < 64 for name in flattened)
    assert set(all_calls[0]).isdisjoint(all_calls[1])

    cleaned = cleanup_calls.read_text().splitlines()
    expected_cleaned = [
        name
        for run_calls in all_calls
        for name in run_calls
    ]
    assert cleaned == expected_cleaned


def test_frozen_wrapper_fails_closed_when_owned_cleanup_fails(
    tmp_path: Path,
) -> None:
    root = _project_root()
    sandbox = tmp_path / "repo"
    scripts = sandbox / "skills" / "visual-debug" / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / "section-compare-frozen.sh"
    shutil.copy2(
        root / "skills" / "visual-debug" / "scripts" / "section-compare-frozen.sh",
        wrapper,
    )

    calls = tmp_path / "calls.txt"
    section_compare = scripts / "section-compare.sh"
    section_compare.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$3" >> "{calls}"\n'
        'mkdir -p "$4/sections/ref"\n'
        'printf "[]" > "$4/sections/ref-sections.json"\n'
        'printf "png" > "$4/sections/ref/hero.png"\n',
        encoding="utf-8",
    )
    section_compare.chmod(0o755)

    cleanup = sandbox / "scripts" / "verify" / "cleanup-sessions.sh"
    cleanup.parent.mkdir(parents=True)
    cleanup.write_text(
        "#!/usr/bin/env bash\n"
        'echo "simulated cleanup did not settle for $1" >&2\n'
        "exit 9\n",
        encoding="utf-8",
    )
    cleanup.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    agent_browser = bin_dir / "agent-browser"
    agent_browser.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    agent_browser.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["SECTION_FROZEN_RUN_NONCE"] = "cleanup-failure"
    proc = subprocess.run(
        [
            "bash",
            str(wrapper),
            "https://ref.test",
            "http://impl.test",
            "frozen-cleanup-test",
            str(tmp_path / "out"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "cleanup failed for pass 1 attempt 1" in proc.stderr
    assert "simulated cleanup did not settle" in proc.stderr
    assert len(calls.read_text().splitlines()) == 1


def _run_frozen_with_measurement_cleanup_failure(
    tmp_path: Path,
    *,
    measurement_exit: int,
) -> subprocess.CompletedProcess[str]:
    root = _project_root()
    sandbox = tmp_path / "repo"
    scripts = sandbox / "skills" / "visual-debug" / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / "section-compare-frozen.sh"
    shutil.copy2(
        root / "skills" / "visual-debug" / "scripts" / "section-compare-frozen.sh",
        wrapper,
    )

    section_compare = scripts / "section-compare.sh"
    section_compare.write_text(
        "#!/usr/bin/env bash\n"
        'session="$3"\n'
        'out="$4"\n'
        'mkdir -p "$out/sections"\n'
        'case "$session" in\n'
        '  *-base-a1)\n'
        '    mkdir -p "$out/sections/ref"\n'
        '    printf "[]" > "$out/sections/ref-sections.json"\n'
        '    printf "png" > "$out/sections/ref/hero.png"\n'
        "    ;;\n"
        '  *-cal)\n'
        '    mkdir -p "$out/sections/impl"\n'
        '    printf "png" > "$out/sections/impl/hero.png"\n'
        "    ;;\n"
        f"  *-run) exit {measurement_exit} ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    section_compare.chmod(0o755)

    cleanup = sandbox / "scripts" / "verify" / "cleanup-sessions.sh"
    cleanup.parent.mkdir(parents=True)
    cleanup.write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        '  *-run)\n'
        '    echo "simulated measurement cleanup failure for $1" >&2\n'
        "    exit 8\n"
        "    ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    cleanup.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    agent_browser = bin_dir / "agent-browser"
    agent_browser.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    agent_browser.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["SECTION_FROZEN_RUN_NONCE"] = "measurement-cleanup-failure"
    return subprocess.run(
        [
            "bash",
            str(wrapper),
            "https://ref.test",
            "http://impl.test",
            "frozen-measurement-cleanup-test",
            str(tmp_path / "out"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_frozen_measurement_cleanup_failure_after_success_is_infrastructure_error(
    tmp_path: Path,
) -> None:
    proc = _run_frozen_with_measurement_cleanup_failure(
        tmp_path,
        measurement_exit=0,
    )

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "cleanup failed for pass 2b" in proc.stderr
    assert "simulated measurement cleanup failure" in proc.stderr


def test_frozen_measurement_failure_wins_when_cleanup_also_fails(
    tmp_path: Path,
) -> None:
    proc = _run_frozen_with_measurement_cleanup_failure(
        tmp_path,
        measurement_exit=1,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "cleanup failed for pass 2b" in proc.stderr
    assert "simulated measurement cleanup failure" in proc.stderr


def _run_viewport_with_failing_cleanup(
    tmp_path: Path,
    *,
    comparison_exit: int,
) -> subprocess.CompletedProcess[str]:
    root = _project_root()
    sandbox = tmp_path / "repo"
    scripts = sandbox / "skills" / "visual-debug" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(
        root / "skills" / "visual-debug" / "scripts" / "section-compare.sh",
        scripts / "section-compare.sh",
    )
    shutil.copytree(
        root / "skills" / "visual-debug" / "scripts" / "lib",
        scripts / "lib",
    )

    inner = tmp_path / "section-inner.sh"
    inner.write_text(
        "#!/usr/bin/env bash\n"
        'mkdir -p "$4/sections"\n'
        'printf "| stub | 0 | 0 | ok | ✅ |\\n" > "$4/sections/result.txt"\n'
        f"exit {comparison_exit}\n",
        encoding="utf-8",
    )
    inner.chmod(0o755)

    cleanup = sandbox / "scripts" / "verify" / "cleanup-sessions.sh"
    cleanup.parent.mkdir(parents=True)
    cleanup.write_text(
        "#!/usr/bin/env bash\n"
        'echo "simulated viewport cleanup failure for $1" >&2\n'
        "exit 7\n",
        encoding="utf-8",
    )
    cleanup.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    agent_browser = bin_dir / "agent-browser"
    agent_browser.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    agent_browser.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "SECTION_COMPARE_INNER_CMD": str(inner),
            "VIEWPORTS": "375x812",
        }
    )
    return subprocess.run(
        [
            "bash",
            str(scripts / "section-compare.sh"),
            "https://ref.test",
            "http://impl.test",
            "viewport-cleanup-test",
            str(tmp_path / "out"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_viewport_wrapper_fails_closed_when_cleanup_fails_after_success(
    tmp_path: Path,
) -> None:
    proc = _run_viewport_with_failing_cleanup(tmp_path, comparison_exit=0)

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "cleanup failed for viewport 375x812" in proc.stderr
    assert "simulated viewport cleanup failure" in proc.stderr


def test_viewport_wrapper_preserves_comparison_failure_when_cleanup_also_fails(
    tmp_path: Path,
) -> None:
    proc = _run_viewport_with_failing_cleanup(tmp_path, comparison_exit=1)

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "cleanup failed for viewport 375x812" in proc.stderr
    assert "simulated viewport cleanup failure" in proc.stderr


def test_viewport_setup_failure_is_written_to_per_viewport_log(
    tmp_path: Path,
) -> None:
    root = _project_root()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "agent-browser-calls.txt"
    agent_browser = bin_dir / "agent-browser"
    agent_browser.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{calls}"\n'
        'case "$*" in\n'
        '  *" set viewport "*)\n'
        '    echo "simulated socket path too long during viewport setup" >&2\n'
        "    exit 73\n"
        "    ;;\n"
        '  *"session list"*) echo "No active sessions" ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    agent_browser.chmod(0o755)

    out = tmp_path / "viewport-output"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "VIEWPORTS": "375x812",
            "WAIT_REF": "0",
            "WAIT_IMPL": "0",
            "WAIT_LAZY_LOAD": "0",
        }
    )
    proc = subprocess.run(
        [
            "/bin/bash",
            str(
                root
                / "skills"
                / "visual-debug"
                / "scripts"
                / "section-compare.sh"
            ),
            "https://ref.test",
            "http://impl.test",
            "viewport-diagnostic",
            str(out),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode != 0, proc.stdout + proc.stderr
    log = (
        out
        / "sections"
        / "viewports"
        / "375x812"
        / "section-compare.log"
    ).read_text()
    assert "simulated socket path too long during viewport setup" in log
    assert (
        "ERROR: failed to set reference viewport 375x812 for session "
        "'viewport-diagnostic-375x812-sc-ref'"
    ) in log
    assert not any(
        "viewport-diagnostic-375x812-sc-impl" in call
        for call in calls.read_text().splitlines()
    )
