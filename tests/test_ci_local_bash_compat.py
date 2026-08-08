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
