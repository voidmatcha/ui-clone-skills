"""Hook recognizer parity for CLI-wrapper command forms.

AGENTS.md/docs present `ui-clone ...` / `npx ui-clone-cli ...` as equivalent
to `python -m ui_clone.*`, but only the python-module form used to produce
session-ownership markers — wrapper-driven sessions silently lost Stop-hook
scoping. These tests pin the wrapper forms to the same ref-dir resolution.
"""

from __future__ import annotations

from pathlib import Path

from ui_clone.hooks._common import target_ref_dir_for_ui_re_command

ROOT = Path("/repo")


def test_npx_pipeline_verify_resolves_ref_dir() -> None:
    assert target_ref_dir_for_ui_re_command(
        "npx ui-clone-cli pipeline https://x.com hero sess verify --json", ROOT
    ) == ROOT / "tmp" / "ref" / "hero"


def test_bare_wrapper_pipeline_shorthand_resolves_ref_dir() -> None:
    assert target_ref_dir_for_ui_re_command(
        "ui-clone https://x.com hero sess run --phases 0A,1,2", ROOT
    ) == ROOT / "tmp" / "ref" / "hero"


def test_node_bin_gate_form_resolves_ref_dir(tmp_path: Path) -> None:
    ref_dir = tmp_path / "tmp" / "ref" / "hero"
    ref_dir.mkdir(parents=True)
    assert target_ref_dir_for_ui_re_command(
        f"node bin/ui-clone gate {ref_dir} section-compare --json", ROOT
    ) == ref_dir


def test_state_terminal_form_resolves_ref_dir(tmp_path: Path) -> None:
    ref_dir = tmp_path / "tmp" / "ref" / "hero"
    ref_dir.mkdir(parents=True)
    assert target_ref_dir_for_ui_re_command(
        f"ui-clone state terminal {ref_dir} --status incomplete "
        "--category x --reason y",
        ROOT,
    ) == ref_dir


def test_env_assignment_prefix_is_allowed() -> None:
    assert target_ref_dir_for_ui_re_command(
        "UI_CLONE_VERIFY_TIER=standard ui-clone pipeline https://x.com hero sess verify",
        ROOT,
    ) == ROOT / "tmp" / "ref" / "hero"


def test_mere_mention_of_wrapper_is_not_an_execution() -> None:
    assert target_ref_dir_for_ui_re_command("cat ui-clone", ROOT) is None
    assert target_ref_dir_for_ui_re_command(
        "echo ui-clone pipeline https://x.com hero sess run", ROOT
    ) is None
