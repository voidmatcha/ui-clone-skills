"""Tests for the npm `ui-clone` wrapper."""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN = REPO_ROOT / "bin" / "ui-clone"


def _node_available() -> bool:
    return subprocess.run(
        ["node", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0


@pytest.mark.skipif(not _node_available(), reason="node is required for npm wrapper tests")
def test_ui_clone_wrapper_help() -> None:
    result = subprocess.run(["node", str(BIN), "--help"], capture_output=True, text=True)

    assert result.returncode == 0
    assert "ui-clone — agent-readable" in result.stdout
    assert "status --json" in result.stdout
    assert "state terminal" in result.stdout


@pytest.mark.skipif(not _node_available(), reason="node is required for npm wrapper tests")
def test_ui_clone_wrapper_works_through_symlink(tmp_path: Path) -> None:
    link = tmp_path / "ui-clone"
    link.symlink_to(BIN)

    result = subprocess.run([str(link), "--help"], capture_output=True, text=True)

    assert result.returncode == 0
    assert "npx ui-clone-cli" in result.stdout
    assert "ui-clone pipeline" in result.stdout


def test_ui_clone_wrapper_pipeline_shorthand_status_json(tmp_path: Path) -> None:
    run_dir = tmp_path / ".ui-clone" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "pipeline-state.json").write_text(
        json.dumps(
            {
                "component": "run-1",
                "completed_steps": ["reference"],
                "current_gate": "extraction",
                "terminalState": {
                    "status": "incomplete",
                    "category": "hardening-probe-incomplete",
                    "gate": "extraction",
                    "reason": "wrapper smoke",
                    "recorded_at": "2026-01-01T00:00:00Z",
                },
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "UI_CLONE_CLI_PYTHON_DIRECT": "1",
        "PYTHONPATH": str(REPO_ROOT),
    }

    result = subprocess.run(
        ["node", str(BIN), "https://example.com", "run-1", "sess", "status", "--json"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "incomplete"
    assert payload["layout"] == "agent-run"
    assert payload["verify_stamp"]["success_only"] is True


def test_package_json_bin_and_version_match_pyproject() -> None:
    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert package["name"] == "ui-clone-cli"
    assert package["bin"]["ui-clone"] == "bin/ui-clone"
    assert package["version"] == pyproject["project"]["version"]


# ── CLI contract: every Usage form printed by --help must be runnable ──────
# (exit code 2 = argparse usage error; the audit found two help lines that
# failed when copy-pasted, and the only working `gate` form was undocumented).


@pytest.mark.skipif(not _node_available(), reason="node is required for npm wrapper tests")
def test_help_documents_gate_verb_not_broken_bare_shorthand() -> None:
    result = subprocess.run(["node", str(BIN), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "ui-clone gate <ref-dir> <gate-name>" in result.stdout
    assert "ui-clone goal <ref-dir>" in result.stdout
    # The old bare two-positional gate shorthand mis-dispatched to
    # ui_clone.pipeline and always argparse-errored; it must stay undocumented.
    assert "ui-clone <ref-dir> <gate-name>" not in result.stdout
    # state terminal Usage must include the required --reason flag.
    assert "--reason <reason>" in result.stdout


@pytest.mark.skipif(not _node_available(), reason="node is required for npm wrapper tests")
def test_gate_verb_form_does_not_argparse_error(tmp_path: Path) -> None:
    ref_dir = tmp_path / "tmp" / "ref" / "contract"
    ref_dir.mkdir(parents=True)
    result = subprocess.run(
        ["node", str(BIN), "gate", str(ref_dir), "reference", "--json"],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=120,
    )
    assert result.returncode != 2, result.stderr[-500:]


@pytest.mark.skipif(not _node_available(), reason="node is required for npm wrapper tests")
def test_goal_verb_form_does_not_argparse_error(tmp_path: Path) -> None:
    ref_dir = tmp_path / "tmp" / "ref" / "contract"
    ref_dir.mkdir(parents=True)
    result = subprocess.run(
        ["node", str(BIN), "goal", str(ref_dir), "--json"],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=120,
    )
    assert result.returncode != 2, result.stderr[-500:]


@pytest.mark.skipif(not _node_available(), reason="node is required for npm wrapper tests")
def test_state_terminal_usage_form_succeeds(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    result = subprocess.run(
        ["node", str(BIN), "state", "terminal", str(ref_dir),
         "--status", "incomplete",
         "--category", "contract-test",
         "--reason", "cli contract test",
         "--gate", "section-compare"],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-500:]
    persisted = json.loads((ref_dir / "pipeline-state.json").read_text())
    assert persisted["terminal_state"]["status"] == "incomplete"
