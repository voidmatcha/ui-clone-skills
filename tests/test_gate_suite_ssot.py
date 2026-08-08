"""Single-source-of-truth regression tests for gate/terminal vocabularies.

Pins the constants introduced to collapse hand-copied literals:
- TERMINAL_STATUSES: writer CLI (state.main) and Stop-hook enforcer must share it
- POST_IMPL_VERIFY_GATES: verify-stamp writer and Stop-hook enforcer must share it
- doc enumerations (SKILL.md router) must not drift from GATE_ORDER
- no repair message may reference the unpublished `npx ui-clone` package name
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ui_clone import state
from ui_clone.hooks import section_gate
from ui_clone.pipeline_phases import verify
from ui_clone.state import GATE_ORDER, POST_IMPL_VERIFY_GATES, TERMINAL_STATUSES

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_terminal_statuses_shared_between_writer_and_enforcer() -> None:
    assert section_gate.TERMINAL_STATUSES is state.TERMINAL_STATUSES


def test_post_impl_verify_gates_shared_between_writer_and_enforcer() -> None:
    assert verify.POST_IMPL_VERIFY_GATES is state.POST_IMPL_VERIFY_GATES
    assert section_gate.POST_IMPL_VERIFY_GATES is state.POST_IMPL_VERIFY_GATES


def test_post_impl_verify_gates_subset_of_gate_order() -> None:
    assert set(POST_IMPL_VERIFY_GATES) <= set(GATE_ORDER)


def test_terminal_cli_rejects_invalid_status(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        state.main([
            "terminal", str(tmp_path),
            "--status", "fail",  # typo'd status must be rejected at the CLI
            "--category", "test", "--reason", "test",
        ])
    assert exc.value.code == 2


def test_terminal_cli_accepts_every_canonical_status(tmp_path: Path) -> None:
    for status in TERMINAL_STATUSES:
        ref_dir = tmp_path / status
        ref_dir.mkdir()
        rc = state.main([
            "terminal", str(ref_dir),
            "--status", status,
            "--category", "test", "--reason", "test",
        ])
        assert rc == 0
        persisted = json.loads((ref_dir / "pipeline-state.json").read_text())
        assert persisted["terminal_state"]["status"] == status


def test_skill_md_router_enumerates_full_gate_order() -> None:
    skill_md = (REPO_ROOT / "skills/ui-reverse-engineering/SKILL.md").read_text()
    router_lines = [
        line for line in skill_md.splitlines()
        if "State names come from `GATE_ORDER`" in line
    ]
    assert router_lines, "SKILL.md smart state router enumeration not found"
    for gate in GATE_ORDER:
        assert f"`{gate}`" in router_lines[0], (
            f"SKILL.md router enumeration is missing gate {gate!r}; "
            "it must match ui_clone.state.GATE_ORDER"
        )


def test_no_repair_message_references_unpublished_npx_package() -> None:
    # `npx ui-clone <...>` resolves to the unpublished registry package
    # 'ui-clone' (squattable); only `npx ui-clone-cli` or python -m forms
    # are valid in agent-facing strings.
    dead_pattern = re.compile(r"npx ui-clone (?!-)")
    offenders: list[str] = []
    for py in (REPO_ROOT / "ui_clone").rglob("*.py"):
        for n, line in enumerate(py.read_text().splitlines(), 1):
            if dead_pattern.search(line):
                offenders.append(f"{py.relative_to(REPO_ROOT)}:{n}")
    assert not offenders, f"dead `npx ui-clone` references: {offenders}"


def test_all_version_fields_match() -> None:
    versions = {
        ".claude-plugin/plugin.json": json.loads(
            (REPO_ROOT / ".claude-plugin/plugin.json").read_text())["version"],
        ".claude-plugin/marketplace.json": json.loads(
            (REPO_ROOT / ".claude-plugin/marketplace.json").read_text())["plugins"][0]["version"],
        ".codex-plugin/plugin.json": json.loads(
            (REPO_ROOT / ".codex-plugin/plugin.json").read_text())["version"],
        "package.json": json.loads(
            (REPO_ROOT / "package.json").read_text())["version"],
        "pyproject.toml": re.search(
            r'^version\s*=\s*"([^"]+)"',
            (REPO_ROOT / "pyproject.toml").read_text(), re.M).group(1),  # type: ignore[union-attr]
        "ui_clone/__init__.py": re.search(
            r'__version__\s*=\s*"([^"]+)"',
            (REPO_ROOT / "ui_clone/__init__.py").read_text()).group(1),  # type: ignore[union-attr]
    }
    assert len(set(versions.values())) == 1, f"version drift: {versions}"


def test_terminal_cli_choices_render_in_usage_error(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ui_clone.state", "terminal", str(tmp_path),
         "--status", "bogus", "--category", "c", "--reason", "r"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert result.returncode == 2
    for status in TERMINAL_STATUSES:
        assert status in result.stderr
