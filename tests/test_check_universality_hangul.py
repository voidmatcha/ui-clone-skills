"""Tests for the standalone universality Hangul scanner."""

from __future__ import annotations

import runpy
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci" / "check-universality.sh"
HELPER = ROOT / "scripts" / "ci" / "check_universality_hangul.py"


def test_check_universality_uses_standalone_hangul_scanner() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "python3 - <<'PY'" not in source
    assert 'python3 "$REPO_ROOT/scripts/ci/check_universality_hangul.py"' in source


def test_hangul_scanner_reports_production_source_only(tmp_path: Path) -> None:
    production = tmp_path / "scripts" / "tool.py"
    production.parent.mkdir(parents=True)
    production.write_text("# 잘못된 주석\n", encoding="utf-8")
    fixture = tmp_path / "tests" / "fixture.py"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("# 허용된 테스트 문구\n", encoding="utf-8")

    proc = subprocess.run(
        ["python3", str(HELPER), str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert proc.returncode == 0
    assert "scripts/tool.py:1" in proc.stdout
    assert "tests/fixture.py" not in proc.stdout


def test_hangul_scanner_prunes_excluded_directories_before_file_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    excluded = tmp_path / "node_modules"
    excluded.mkdir()
    (excluded / "trap.py").write_text("# ignored\n", encoding="utf-8")

    original_is_file = Path.is_file

    def guarded_is_file(path: Path) -> bool:
        try:
            relative = path.relative_to(tmp_path)
        except ValueError:
            return original_is_file(path)
        if "node_modules" in relative.parts:
            raise AssertionError("excluded directory contents were traversed")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", guarded_is_file)
    find_hits = cast(
        Callable[[Path], list[str]],
        runpy.run_path(str(HELPER))["find_hits"],
    )

    assert find_hits(tmp_path) == []


def test_universality_scanners_ignore_tokensave_runtime_state(
    tmp_path: Path,
) -> None:
    """Machine-local TokenSave metadata is not part of the shipped surface."""
    shell_source = SCRIPT.read_text(encoding="utf-8")
    assert "--exclude-dir=.tokensave" in shell_source

    runtime_file = tmp_path / ".tokensave" / "runtime.py"
    runtime_file.parent.mkdir()
    runtime_file.write_text("# 로컬 절대 경로 메타데이터\n", encoding="utf-8")

    proc = subprocess.run(
        ["python3", str(HELPER), str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert proc.returncode == 0
    assert ".tokensave/runtime.py" not in proc.stdout
