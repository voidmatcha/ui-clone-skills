"""Off-pipeline clone guard (omx postmortem).

A session that browses an EXTERNAL site via agent-browser and then writes
component files without a ref dir (or under a TERMINAL ref) is doing
clone-shaped work outside the pipeline — it must be routed INTO the
pipeline instead of shipping a smoke-tested static approximation.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ui_clone.hooks._common import has_external_browse, mark_external_browse

from ._helpers import _populate_pre_generate_artifacts, _set_pre_generate_passed_state, run_hook

MODULE = "ui_clone.hooks.pre_generate"
SESSION = "offpipe-test-session"


def _crumb(base: Path, session_id: str = SESSION) -> None:
    digest = hashlib.sha256(session_id.encode()).hexdigest()
    d = base / "tmp" / ".ui-re-external-browse"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{digest}.json").write_text(json.dumps({"url": "https://example.org"}))


def _payload(file_path: str) -> str:
    return json.dumps({
        "tool_name": "Write",
        "tool_input": {"file_path": file_path},
        "session_id": SESSION,
    })


def test_mark_external_browse_records_external_and_skips_local(tmp_path: Path) -> None:
    mark_external_browse(
        "agent-browser --session s open https://realfood.gov/", tmp_path, SESSION
    )
    assert has_external_browse(tmp_path, SESSION)
    other = tmp_path / "local"
    other.mkdir()
    mark_external_browse(
        "agent-browser --session s open http://localhost:5183/", other, SESSION
    )
    assert not has_external_browse(other, SESSION)


def test_component_write_after_external_browse_without_ref_dir_blocks(tmp_path: Path) -> None:
    (tmp_path / "tmp" / "ref").mkdir(parents=True)  # project shape, no components
    _crumb(tmp_path)
    result = run_hook(
        MODULE,
        stdin_data=_payload(str(tmp_path / "src/components/Hero.tsx")),
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    assert result.returncode == 0
    data = json.loads(result.stdout.strip())
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "outside the pipeline" in data["reason"]


def test_escape_hatch_allows_offpipeline_write(tmp_path: Path) -> None:
    (tmp_path / "tmp" / "ref").mkdir(parents=True)
    _crumb(tmp_path)
    result = run_hook(
        MODULE,
        stdin_data=_payload(str(tmp_path / "src/components/Hero.tsx")),
        env={
            "CLAUDE_PROJECT_DIR": str(tmp_path),
            "UI_RE_ALLOW_OFFPIPELINE": "1",
        },
    )
    assert result.returncode == 0
    assert not result.stdout.strip(), result.stdout


def test_no_external_browse_no_ref_dir_stays_silent(tmp_path: Path) -> None:
    (tmp_path / "tmp" / "ref").mkdir(parents=True)
    result = run_hook(
        MODULE,
        stdin_data=_payload(str(tmp_path / "src/components/Hero.tsx")),
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    assert result.returncode == 0
    assert not result.stdout.strip(), result.stdout


def test_terminal_ref_with_external_browse_blocks_new_clone_work(tmp_path: Path) -> None:
    ref_dir = tmp_path / "tmp" / "ref" / "old-clone"
    ref_dir.mkdir(parents=True)
    (ref_dir / ".ui-re-active").write_text("")  # recognized by find_ref_dir
    (ref_dir / "pipeline-state.json").write_text(json.dumps({
        "component": "old-clone",
        "current_gate": "post-implement",
        "completed_steps": ["reference"],
        "terminal_state": {
            "status": "incomplete",
            "category": "harvested-failure",
            "reason": "prior run closed",
        },
    }))
    _crumb(tmp_path)
    result = run_hook(
        MODULE,
        stdin_data=_payload(str(tmp_path / "src/components/Hero.tsx")),
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    assert result.returncode == 0
    data = json.loads(result.stdout.strip())
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "TERMINAL" in data["reason"]


def test_recoverable_canonical_verify_ref_allows_rework(tmp_path: Path) -> None:
    ref_dir = tmp_path / "tmp" / "ref" / "old-clone"
    ref_dir.mkdir(parents=True)
    (ref_dir / ".ui-re-active").write_text("")
    _populate_pre_generate_artifacts(ref_dir)
    _set_pre_generate_passed_state(ref_dir)
    state = json.loads((ref_dir / "pipeline-state.json").read_text())
    state["terminalState"] = {
        "status": "failed",
        "category": "canonical-verify-failed",
        "reason": "verify failed; rework allowed",
    }
    (ref_dir / "pipeline-state.json").write_text(json.dumps(state))
    _crumb(tmp_path)

    result = run_hook(
        MODULE,
        stdin_data=_payload(str(tmp_path / "src/components/Hero.tsx")),
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )

    assert result.returncode == 0
    assert not result.stdout.strip(), result.stdout
