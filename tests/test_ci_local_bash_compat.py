"""Regression coverage for local CI shell compatibility and parity isolation."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI_LOCAL = ROOT / "scripts" / "ci" / "ci-local.sh"
TEST_PARITY = ROOT / "scripts" / "ci" / "test-parity.sh"


def test_ci_local_scopes_bash_compat_to_pytest_children() -> None:
    source = CI_LOCAL.read_text(encoding="utf-8")

    assert "export BASH_COMPAT" not in source
    assert 'PYTEST_ENV=(env "BASH_COMPAT=${UI_CLONE_TEST_BASH_COMPAT:-5.0}")' in source
    assert source.count('"${PYTEST_ENV[@]}" uv run python -m pytest tests/ -q') == 2
    assert source.index("PYTEST_ENV=()") > source.index('PATH="$(dirname "$BASH_BIN"):$PATH"')
    assert source.index("PYTEST_ENV=()") < source.index('# 1. Tests')
    assert source.index("# 2. Type check") > source.rindex(
        '"${PYTEST_ENV[@]}" uv run python -m pytest tests/ -q'
    )


def test_ci_local_caps_default_pytest_workers_under_shared_host_load() -> None:
    source = CI_LOCAL.read_text(encoding="utf-8")

    assert "min(os.cpu_count() or 1, 4)" in source
    assert (
        'PYTEST_WORKERS="${UI_CLONE_PYTEST_WORKERS:-$DEFAULT_PYTEST_WORKERS}"'
        in source
    )


def test_ci_local_quiet_mode_preserves_failure_output() -> None:
    source = CI_LOCAL.read_text(encoding="utf-8")

    assert "run_quiet()" in source
    assert 'cat "$log_path" >&2' in source
    assert 'run_quiet "tests" "${PYTEST_ENV[@]}" uv run python -m pytest' in source
    assert '>/dev/null 2>&1 || fail "tests"' not in source


def test_ci_local_skip_never_discards_dirty_tracked_files(tmp_path: Path) -> None:
    """Even the emergency skip path must preserve user-owned working-tree bytes."""
    repo = tmp_path / "repo"
    script = repo / "scripts" / "ci" / "ci-local.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(CI_LOCAL, script)

    agents = repo / "AGENTS.md"
    agents.write_text("generated policy\n", encoding="utf-8")
    manifests = [
        repo / ".codex-plugin" / "plugin.json",
        repo / ".claude-plugin" / "plugin.json",
        repo / ".claude-plugin" / "marketplace.json",
    ]
    for manifest in manifests:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text('{"version": "0.7.41"}\n', encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=10)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, timeout=10)

    dirty_agents = "legitimate user note mentioning drift-test\n"
    dirty_manifest = '{"version": "0.7.41", "draft": '
    agents.write_text(dirty_agents, encoding="utf-8")
    manifests[0].write_text(dirty_manifest, encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(script)],
        cwd=repo,
        env={**os.environ, "UI_RE_SKIP_CI_LOCAL": "1"},
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert agents.read_text(encoding="utf-8") == dirty_agents
    assert manifests[0].read_text(encoding="utf-8") == dirty_manifest


def _make_parity_repo(tmp_path: Path, *, slow_guard: bool = False) -> Path:
    repo = tmp_path / "parity-repo"
    script = repo / "scripts" / "ci" / "test-parity.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(TEST_PARITY, script)

    (repo / "AGENTS.md").write_text("generated policy\n", encoding="utf-8")
    codex_manifest = repo / ".codex-plugin" / "plugin.json"
    claude_manifest = repo / ".claude-plugin" / "plugin.json"
    codex_manifest.parent.mkdir(parents=True)
    claude_manifest.parent.mkdir(parents=True)
    codex_manifest.write_text('{"version": "0.7.41"}\n', encoding="utf-8")
    claude_manifest.write_text('{"version": "0.7.41"}\n', encoding="utf-8")

    slow = (
        "if [ -n \"${PARITY_NESTED_PID_FILE:-}\" ]; then\n"
        "  (sleep 30) &\n"
        "  echo \"$!\" > \"$PARITY_NESTED_PID_FILE\"\n"
        "  wait \"$!\"\n"
        "else\n"
        "  sleep 30\n"
        "fi\n"
        if slow_guard
        else ""
    )
    security = repo / "scripts" / "ci" / "pre-push-security.sh"
    security.write_text(
        "#!/usr/bin/env bash\n"
        + slow
        + "if grep -Eq 'AKIA|sk-ant-|github_pat_|sk_live_|gho_|glpat-|xapp-|_authToken=|npm_' AGENTS.md; then echo 'Potential secret'; exit 1; fi\n"
        + "if grep -q '\"9\\.9\\.9\"' .codex-plugin/plugin.json; then echo 'version mismatch'; exit 1; fi\n"
        + "if ! python3 -c 'import json; json.load(open(\".claude-plugin/plugin.json\"))' 2>/dev/null; then echo 'invalid JSON'; exit 1; fi\n"
        + "exit 0\n",
        encoding="utf-8",
    )
    review = repo / "scripts" / "ci" / "review.sh"
    review.write_text(
        "#!/usr/bin/env bash\n"
        "if python3 -c 'from pathlib import Path; raise SystemExit(0 if any(ord(c) > 127 for c in Path(\"AGENTS.md\").read_text()) else 1)'; then echo 'Non-English (Hangul) text found'; exit 1; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    security.chmod(0o755)
    review.chmod(0o755)

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=10)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, timeout=10)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Parity Test",
            "-c",
            "user.email=parity@example.test",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=repo,
        check=True,
        timeout=10,
    )
    return repo


def _worktree_paths(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return [line.removeprefix("worktree ") for line in result.stdout.splitlines() if line.startswith("worktree ")]


def _process_is_alive(pid: int) -> bool:
    return (
        subprocess.run(
            ["ps", "-p", str(pid), "-o", "pid="],
            capture_output=True,
            text=True,
            timeout=5,
        ).returncode
        == 0
    )


def _wait_for_process_exit(pid: int, *, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while _process_is_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _process_is_alive(pid)


def test_parity_mutations_run_only_in_an_isolated_worktree(tmp_path: Path) -> None:
    repo = _make_parity_repo(tmp_path)
    agents = repo / "AGENTS.md"
    dirty_agents = "user-owned dirty policy note\n"
    agents.write_text(dirty_agents, encoding="utf-8")
    (repo / "untracked-proof.txt").write_text("present\n", encoding="utf-8")
    security = repo / "scripts" / "ci" / "pre-push-security.sh"
    security.write_text(
        security.read_text(encoding="utf-8").replace(
            "exit 0\n",
            "test -f untracked-proof.txt || { echo 'missing untracked overlay'; exit 2; }\n"
            "exit 0\n",
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", "scripts/ci/test-parity.sh"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Drift smoke: 13 passed, 0 failed" in proc.stdout
    assert agents.read_text(encoding="utf-8") == dirty_agents
    assert _worktree_paths(repo) == [str(repo)]


def test_parity_rejects_forged_child_mode_without_mutating_checkout(tmp_path: Path) -> None:
    repo = _make_parity_repo(tmp_path)
    agents = repo / "AGENTS.md"
    dirty_agents = "user-owned dirty policy note\n"
    agents.write_text(dirty_agents, encoding="utf-8")

    proc = subprocess.run(
        ["bash", "scripts/ci/test-parity.sh"],
        cwd=repo,
        env={**os.environ, "UI_CLONE_PARITY_CHILD_ROOT": str(repo)},
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert proc.returncode != 0
    assert "isolated child" in proc.stderr
    assert agents.read_text(encoding="utf-8") == dirty_agents


def test_parity_rejects_linked_worktree_child_mode_without_capability(tmp_path: Path) -> None:
    repo = _make_parity_repo(tmp_path)
    linked = tmp_path / "linked-parity-worktree"
    subprocess.run(
        ["git", "worktree", "add", "--detach", "--quiet", str(linked), "HEAD"],
        cwd=repo,
        check=True,
        timeout=10,
    )
    agents = linked / "AGENTS.md"
    original_agents = agents.read_text(encoding="utf-8")
    try:
        proc = subprocess.run(
            ["bash", "scripts/ci/test-parity.sh"],
            cwd=linked,
            env={**os.environ, "UI_CLONE_PARITY_CHILD_ROOT": str(linked)},
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert proc.returncode != 0
        assert "isolated child capability missing" in proc.stderr
        assert agents.read_text(encoding="utf-8") == original_agents
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(linked)],
            cwd=repo,
            check=False,
            timeout=10,
        )


def test_parity_rejects_symlink_mutation_target(tmp_path: Path) -> None:
    repo = _make_parity_repo(tmp_path)
    outside = tmp_path / "outside-policy"
    outside.write_text("outside bytes\n", encoding="utf-8")
    agents = repo / "AGENTS.md"
    agents.unlink()
    agents.symlink_to(outside)

    proc = subprocess.run(
        ["bash", "scripts/ci/test-parity.sh"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert proc.returncode != 0
    assert "unsafe mutation target" in proc.stderr
    assert outside.read_text(encoding="utf-8") == "outside bytes\n"
    assert _worktree_paths(repo) == [str(repo)]


def test_parity_term_stops_child_and_removes_worktree(tmp_path: Path) -> None:
    repo = _make_parity_repo(tmp_path, slow_guard=True)
    nested_pid_file = tmp_path / "nested.pid"
    proc = subprocess.Popen(
        ["bash", "scripts/ci/test-parity.sh"],
        cwd=repo,
        env={**os.environ, "PARITY_NESTED_PID_FILE": str(nested_pid_file)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while (
            (len(_worktree_paths(repo)) == 1 or not nested_pid_file.exists())
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert len(_worktree_paths(repo)) == 2
        assert nested_pid_file.exists()

        proc.terminate()
        stdout, stderr = proc.communicate(timeout=10)

        assert proc.returncode == 143, stdout + stderr
        assert _worktree_paths(repo) == [str(repo)]
        nested_pid = int(nested_pid_file.read_text(encoding="utf-8"))
        _wait_for_process_exit(nested_pid)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)
        if nested_pid_file.exists():
            nested_pid = int(nested_pid_file.read_text(encoding="utf-8"))
            if _process_is_alive(nested_pid):
                subprocess.run(["kill", str(nested_pid)], check=False, timeout=5)
