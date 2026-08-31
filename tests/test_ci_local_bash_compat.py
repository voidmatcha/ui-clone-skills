"""Regression coverage for pytest-scoped Bash heredoc compatibility."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI_LOCAL = ROOT / "scripts" / "ci" / "ci-local.sh"


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
