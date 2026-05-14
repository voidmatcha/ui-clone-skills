"""Integration tests: CLI → Python gate/pipeline → exit code."""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def test_gate_cli_reference_missing_dir(tmp_path: Path) -> None:
    """Direct CLI: missing ref dir → exit 1 (BLOCKED)."""
    result = subprocess.run(
        [sys.executable, "-m", "ui_clone.gate", str(tmp_path / "nonexistent"), "reference"],
        capture_output=True,
        cwd=PROJECT_ROOT,
    )
    assert result.returncode == 1


def test_gate_cli_json_output(tmp_path: Path) -> None:
    """--json flag: outputs valid JSON with expected keys."""
    import json

    result = subprocess.run(
        [sys.executable, "-m", "ui_clone.gate", str(tmp_path), "reference", "--json"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    data = json.loads(result.stdout)
    assert "passed" in data
    assert "fail_count" in data
    assert "failures" in data


def test_gate_cli_all_gate(tmp_path: Path) -> None:
    """'all' gate: runs all checks without crash, exits 1 on empty dir."""
    result = subprocess.run(
        [sys.executable, "-m", "ui_clone.gate", str(tmp_path), "all"],
        capture_output=True,
        cwd=PROJECT_ROOT,
    )
    assert result.returncode == 1


def test_pipeline_cli_help() -> None:
    """Pipeline CLI: --help exits 0."""
    result = subprocess.run(
        [sys.executable, "-m", "ui_clone.pipeline", "--help"],
        capture_output=True,
        cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0


def test_no_python3_inline_in_scripts() -> None:
    """No python3 -c inline JSON in bash scripts (replaced by Python modules)."""
    import re

    pattern = re.compile(r'python3\s+-c\s+["\']import json')
    for script in [
        PROJECT_ROOT / "hooks" / "shim.sh",
    ]:
        if script.exists():
            content = script.read_text()
            assert not pattern.search(content), f"{script.name} still has python3 -c inline"
