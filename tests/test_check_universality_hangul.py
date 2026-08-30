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
HELPER = ROOT / "scripts" / "ci" / "check_universality.py"


def test_check_universality_uses_standalone_python_scanner() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "python3 - <<'PY'" not in source
    assert 'python3 "$REPO_ROOT/scripts/ci/check_universality.py"' in source


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

    assert proc.returncode == 1
    assert "scripts/tool.py:1" in proc.stdout
    assert "tests/fixture.py" not in proc.stdout


def test_universality_scanner_reports_loop_labels_on_all_hosts(
    tmp_path: Path,
) -> None:
    production = tmp_path / "ui_clone" / "gate.py"
    production.parent.mkdir(parents=True)
    production.write_text("# observed in loop-129\n", encoding="utf-8")

    proc = subprocess.run(
        ["python3", str(HELPER), str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert proc.returncode == 1
    assert "Per-loop finding labels" in proc.stdout
    assert "ui_clone/gate.py:1" in proc.stdout


def test_universality_scanner_ignores_svg_path_line_commands(
    tmp_path: Path,
) -> None:
    production = tmp_path / "skills" / "visual-debug" / "scripts" / "tool.sh"
    production.parent.mkdir(parents=True)
    production.write_text(
        '# sample path "M10 5 L20 15" vs "M11 5 L20 16"\n',
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["python3", str(HELPER), str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert proc.returncode == 0
    assert "Per-loop finding labels" not in proc.stdout


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
        Callable[[Path], dict[str, list[str]]],
        runpy.run_path(str(HELPER))["find_hits"],
    )

    assert all(rule_hits == [] for rule_hits in find_hits(tmp_path).values())


def test_universality_scanners_ignore_tokensave_runtime_state(
    tmp_path: Path,
) -> None:
    """Machine-local TokenSave metadata is not part of the shipped surface."""
    scanner_source = HELPER.read_text(encoding="utf-8")
    assert '".tokensave"' in scanner_source

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
