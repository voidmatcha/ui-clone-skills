"""Session-scoped hook activation in hooks/shim.sh.

The plugin stays enabled globally, so its hooks must reach Python only for the
session that owns an active ui-clone run. Any other session — even in a folder
holding tmp/ref leftovers — has to exit in the shell fast path. A fake `uv` on
PATH records every module invocation, so "Python was not started" is asserted
directly rather than inferred from empty output.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHIM = REPO / "hooks" / "shim.sh"
SESSION = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"
SESSIONS_DIR = ".ui-re-" + "sessions"
ACTIVE_MARKER = ".ui-re-" + "active"


def _digest(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


@pytest.fixture
def fake_uv(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    log = tmp_path / "uv-calls.log"
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        f'printf "%s\\n" "${{UI_CLONE_SESSION_UNCLAIMED:-}}" > "{tmp_path}/uv-unclaimed.txt"\n'
        f'cat > "{tmp_path}/uv-stdin.txt"\n'
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def _run(project: Path, fake_bin: Path, module: str, payload: str) -> list[str]:
    env = {k: v for k, v in os.environ.items() if k not in {"CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID"}}
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["CLAUDE_PROJECT_DIR"] = str(project)
    result = subprocess.run(
        ["bash", str(SHIM), module],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(project),
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    log = fake_bin.parent / "uv-calls.log"
    return log.read_text().splitlines() if log.is_file() else []


def _stdin_seen(fake_bin: Path) -> str:
    return (fake_bin.parent / "uv-stdin.txt").read_text()


def _project(tmp_path: Path, *, run_state: bool = True) -> Path:
    project = tmp_path / "proj"
    ref = project / "tmp" / "ref" / "site"
    ref.mkdir(parents=True)
    if run_state:
        (ref / "pipeline-state.json").write_text("{}")
    return project


def _claim(project: Path, session_id: str) -> None:
    d = project / "tmp" / ".ui-re-sessions"
    d.mkdir(parents=True, exist_ok=True)
    (d / _digest(session_id)).write_text("")


def _bash(session_id: str | None, command: str = "ls") -> str:
    data: dict[str, object] = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    if session_id is not None:
        data["session_id"] = session_id
    return json.dumps(data)


def _prompt(session_id: str, prompt: str) -> str:
    return json.dumps(
        {"hook_event_name": "UserPromptSubmit", "session_id": session_id, "prompt": prompt}
    )


def _skill(session_id: str, skill: str) -> str:
    return json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "session_id": session_id,
            "tool_name": "Skill",
            "tool_input": {"skill": skill},
        }
    )


def test_non_owning_session_in_ref_folder_skips_python(tmp_path: Path, fake_uv: Path) -> None:
    project = _project(tmp_path)
    _claim(project, OTHER)
    for module in ("ui_clone.hooks.pre_bash", "ui_clone.hooks.section_gate", "ui_clone.hooks.session_resume"):
        assert _run(project, fake_uv, module, _bash(SESSION)) == []


def test_session_with_no_ui_clone_state_skips_python(tmp_path: Path, fake_uv: Path) -> None:
    project = tmp_path / "plain"
    project.mkdir()
    assert _run(project, fake_uv, "ui_clone.hooks.pre_bash", _bash(SESSION)) == []


def test_owning_session_proceeds_with_payload_unchanged(tmp_path: Path, fake_uv: Path) -> None:
    project = _project(tmp_path)
    _claim(project, SESSION)
    payload = _bash(SESSION, "echo hi")
    calls = _run(project, fake_uv, "ui_clone.hooks.pre_bash", payload)
    assert len(calls) == 1 and calls[0].endswith("python -m ui_clone.hooks.pre_bash")
    assert json.loads(_stdin_seen(fake_uv)) == json.loads(payload)


@pytest.mark.parametrize(
    "prompt",
    [
        "/ui-clone-skills:ui-reverse-engineering https://example.org",
        "use ui-clone-skills:ui-capture on https://example.org",
        "/visual-debug the hero",
        "$ui-reverse-engineering https://example.org",
    ],
)
def test_claiming_prompt_proceeds_and_records_ownership(
    tmp_path: Path, fake_uv: Path, prompt: str
) -> None:
    project = tmp_path / "fresh"
    project.mkdir()
    calls = _run(project, fake_uv, "ui_clone.hooks.claude_continuation", _prompt(SESSION, prompt))
    assert len(calls) == 1
    assert (project / "tmp" / ".ui-re-sessions" / _digest(SESSION)).is_file()


@pytest.mark.parametrize(
    "skill",
    ["ui-clone-skills:ui-reverse-engineering", "ui-clone-skills:visual-debug", "ui-capture"],
)
def test_claiming_skill_payload_proceeds(tmp_path: Path, fake_uv: Path, skill: str) -> None:
    project = tmp_path / "fresh"
    project.mkdir()
    assert len(_run(project, fake_uv, "ui_clone.hooks.claude_continuation", _skill(SESSION, skill))) == 1
    assert (project / "tmp" / ".ui-re-sessions" / _digest(SESSION)).is_file()


def test_pipeline_command_claims_for_hosts_without_skill_events(tmp_path: Path, fake_uv: Path) -> None:
    project = _project(tmp_path)
    cmd = "python -m ui_clone.pipeline https://example.org site s status --json"
    assert len(_run(project, fake_uv, "ui_clone.hooks.pre_bash", _bash(SESSION, cmd))) == 1
    # The claim sticks: the next unrelated command from this session proceeds.
    assert len(_run(project, fake_uv, "ui_clone.hooks.pre_bash", _bash(SESSION))) == 2


@pytest.mark.parametrize(
    "payload",
    [
        _prompt(SESSION, "fix the typo in skills/visual-debug/SKILL.md"),
        _skill(SESSION, "ui-clone-skills:something-else"),
        json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "session_id": SESSION,
                "tool_name": "Write",
                "tool_input": {"file_path": "a.md", "content": "python -m ui_clone.pipeline"},
            }
        ),
    ],
)
def test_non_claiming_mentions_do_not_claim(tmp_path: Path, fake_uv: Path, payload: str) -> None:
    project = _project(tmp_path)
    assert _run(project, fake_uv, "ui_clone.hooks.pre_generate", payload) == []
    assert not (project / "tmp" / ".ui-re-sessions").exists()


def test_missing_session_id_keeps_folder_based_fallback(tmp_path: Path, fake_uv: Path) -> None:
    project = _project(tmp_path)
    _claim(project, OTHER)
    assert len(_run(project, fake_uv, "ui_clone.hooks.pre_bash", _bash(None))) == 1
    plain = tmp_path / "plain"
    plain.mkdir()
    (fake_uv.parent / "uv-calls.log").unlink()
    assert _run(plain, fake_uv, "ui_clone.hooks.pre_bash", _bash(None)) == []


def test_stale_ownership_without_run_state_does_not_activate(tmp_path: Path, fake_uv: Path) -> None:
    project = _project(tmp_path, run_state=False)
    _claim(project, SESSION)
    assert _run(project, fake_uv, "ui_clone.hooks.section_gate", _bash(SESSION)) == []
    (project / "tmp" / "ref" / "site").rmdir()
    assert _run(project, fake_uv, "ui_clone.hooks.section_gate", _bash(SESSION)) == []


@pytest.mark.parametrize("marker", [".ui-re-active", "element-target.json", "extracted.json"])
def test_other_run_state_markers_keep_owner_active(tmp_path: Path, fake_uv: Path, marker: str) -> None:
    project = _project(tmp_path, run_state=False)
    (project / "tmp" / "ref" / "site" / marker).write_text("{}")
    _claim(project, SESSION)
    assert len(_run(project, fake_uv, "ui_clone.hooks.section_gate", _bash(SESSION))) == 1


def test_ref_level_session_marker_activates(tmp_path: Path, fake_uv: Path) -> None:
    project = _project(tmp_path)
    marker_dir = project / "tmp" / "ref" / "site" / ".ui-re-sessions"
    marker_dir.mkdir()
    (marker_dir / f"{_digest(SESSION)}.json").write_text("{}")
    assert len(_run(project, fake_uv, "ui_clone.hooks.post_verify", _bash(SESSION))) == 1


def test_external_browse_crumb_is_session_scoped(tmp_path: Path, fake_uv: Path) -> None:
    project = tmp_path / "scratch"
    crumbs = project / "tmp" / ".ui-re-external-browse"
    crumbs.mkdir(parents=True)
    (crumbs / f"{_digest(OTHER)}.json").write_text("{}")
    assert _run(project, fake_uv, "ui_clone.hooks.pre_generate", _bash(SESSION)) == []
    (crumbs / f"{_digest(SESSION)}.json").write_text("{}")
    assert len(_run(project, fake_uv, "ui_clone.hooks.pre_generate", _bash(SESSION))) == 1


def test_external_open_payload_reaches_python_for_any_session(tmp_path: Path, fake_uv: Path) -> None:
    project = tmp_path / "scratch"
    project.mkdir()
    cmd = "agent-browser open https://example.org --session s"
    assert len(_run(project, fake_uv, "ui_clone.hooks.pre_bash", _bash(SESSION, cmd))) == 1


def test_continuation_receipt_activates_only_its_own_session(tmp_path: Path, fake_uv: Path) -> None:
    project = tmp_path / "proj"
    receipts = project / ".ui-re-continuation"
    receipts.mkdir(parents=True)
    (receipts / f"{OTHER}.json").write_text("{}")
    module = "ui_clone.hooks.claude_continuation"
    assert _run(project, fake_uv, module, _prompt(SESSION, "hello")) == []
    (receipts / f"{SESSION}.json").write_text("{}")
    assert len(_run(project, fake_uv, module, _prompt(SESSION, "hello"))) == 1
    # A receipt admits only the continuation module, not the enforcement stack.
    assert len(_run(project, fake_uv, "ui_clone.hooks.pre_bash", _bash(SESSION))) == 1


def test_shell_hash_matches_python_marker_digest(tmp_path: Path, fake_uv: Path) -> None:
    project = tmp_path / "fresh"
    project.mkdir()
    sid = "codex-thread.abc_123"
    _run(project, fake_uv, "ui_clone.hooks.pre_bash", _prompt(sid, "/ui-capture https://example.org"))
    assert [p.name for p in (project / "tmp" / ".ui-re-sessions").iterdir()] == [_digest(sid)]


@pytest.mark.parametrize(
    "command",
    [
        "node bin/ui-clone pipeline https://example.org site s status --json",
        'node "$PLUGIN_ROOT/bin/ui-clone" gate tmp/ref/site all',
        "ui-clone gate tmp/ref/site all",
        "cd web && ui-clone https://example.org site s next --json",
        "UI_CLONE_CLI_PYTHON_DIRECT=1 ui-clone state terminal tmp/ref/site --status incomplete",
        "npx -y ui-clone-cli@latest pipeline https://example.org site s status --json",
        "ui-clone goal tmp/ref/site --json",
        "ui-clone scoped-check tmp/ref/site --json",
        "python3 -m ui_clone.gate tmp/ref/site all",
        "uv run python -m ui_clone https://example.org site s status --json",
        "python -m ui_clone.goal tmp/ref/site",
        "python -m ui_clone.scoped_diff tmp/ref/site --json",
        'bash "$SCRIPTS_DIR/section-compare.sh" https://a http://b s tmp/ref/site',
        "bash ${VISUAL_DEBUG_SCRIPTS_DIR}/batch-compare.sh a b",
        "bash skills/visual-debug/scripts/ae-compare.sh a.png b.png",
    ],
)
def test_cli_and_visual_debug_commands_claim(tmp_path: Path, fake_uv: Path, command: str) -> None:
    project = tmp_path / "fresh"
    project.mkdir()
    assert len(_run(project, fake_uv, "ui_clone.hooks.pre_bash", _bash(SESSION, command))) == 1
    assert (project / "tmp" / SESSIONS_DIR / _digest(SESSION)).is_file()


@pytest.mark.parametrize(
    "command",
    [
        "ui-clone hooks status --json",
        "ui-clone --help",
        "node bin/ui-clone help",
        "echo ui-clone gate",
        "grep -n ui-clone README.md",
        "python -m ui_clone.hooks.pre_bash",
        "python -m ui_clone.metrics x",
        "uv run python -m pytest tests/test_gate.py",
        "cat skills/visual-debug/scripts/ae-compare.sh",
        "bash scripts/ci/ci-local.sh --quiet",
    ],
)
def test_non_run_commands_do_not_claim(tmp_path: Path, fake_uv: Path, command: str) -> None:
    project = _project(tmp_path)
    assert _run(project, fake_uv, "ui_clone.hooks.pre_bash", _bash(SESSION, command)) == []
    assert not (project / "tmp" / SESSIONS_DIR).exists()


def _session_start(session_id: str) -> str:
    return json.dumps({"hook_event_name": "SessionStart", "session_id": session_id, "source": "clear"})


def test_unowned_session_resume_shows_wip_notice_without_claiming(tmp_path: Path, fake_uv: Path) -> None:
    project = _project(tmp_path)
    (project / "tmp" / "ref" / "site" / ACTIVE_MARKER).write_text("")
    _claim(project, OTHER)
    calls = _run(project, fake_uv, "ui_clone.hooks.session_resume", _session_start(SESSION))
    assert len(calls) == 1
    assert (fake_uv.parent / "uv-unclaimed.txt").read_text().strip() == "1"
    # No auto-claim: the enforcement stack stays off for this session.
    assert not (project / "tmp" / SESSIONS_DIR / _digest(SESSION)).exists()
    assert len(_run(project, fake_uv, "ui_clone.hooks.pre_bash", _bash(SESSION))) == 1


def test_owned_session_resume_is_not_marked_unclaimed(tmp_path: Path, fake_uv: Path) -> None:
    project = _project(tmp_path)
    (project / "tmp" / "ref" / "site" / ACTIVE_MARKER).write_text("")
    _claim(project, SESSION)
    assert len(_run(project, fake_uv, "ui_clone.hooks.session_resume", _session_start(SESSION))) == 1
    assert (fake_uv.parent / "uv-unclaimed.txt").read_text().strip() == ""


def test_unowned_session_resume_without_active_marker_skips(tmp_path: Path, fake_uv: Path) -> None:
    project = _project(tmp_path)  # pipeline-state.json only, no WIP marker
    assert _run(project, fake_uv, "ui_clone.hooks.session_resume", _session_start(SESSION)) == []


@pytest.mark.parametrize("with_ref", [False, True])
@pytest.mark.parametrize("tool", ["Write", "Bash"])
def test_large_payload_fast_path_is_linear(tmp_path: Path, fake_uv: Path, tool: str, with_ref: bool) -> None:
    project = _project(tmp_path) if with_ref else tmp_path / "plain"
    project.mkdir(parents=True, exist_ok=True)
    body = "agent-browser open foo " * 20000  # ~460KB, never followed by http
    key = "content" if tool == "Write" else "command"
    payload = json.dumps(
        {"hook_event_name": "PreToolUse", "session_id": SESSION, "tool_name": tool, "tool_input": {key: body}}
    )
    start = time.monotonic()
    assert _run(project, fake_uv, "ui_clone.hooks.pre_generate", payload) == []
    assert time.monotonic() - start < 1.0


def test_external_open_still_detected_in_large_payload(tmp_path: Path, fake_uv: Path) -> None:
    project = tmp_path / "scratch"
    project.mkdir()
    cmd = "echo " + "x" * 400000 + "; agent-browser --session s open https://example.org"
    assert len(_run(project, fake_uv, "ui_clone.hooks.pre_bash", _bash(SESSION, cmd))) == 1


def test_user_decide_prompt_reaches_the_recorder_from_any_session(
    tmp_path: Path, fake_uv: Path
) -> None:
    project = _project(tmp_path)
    _claim(project, OTHER)
    decide = _prompt(SESSION, "ui-clone decide feconf/paid-font-abc proceed free substitute ok")
    assert _run(project, fake_uv, "ui_clone.hooks.claude_continuation", decide) != []
    assert not (project / "tmp" / ".ui-re-sessions" / _digest(SESSION)).exists(), "must not claim"
    # Other hook modules and non-decide prompts from a non-owner still skip.
    log = fake_uv.parent / "uv-calls.log"
    log.unlink()
    assert _run(project, fake_uv, "ui_clone.hooks.pre_bash", decide) == []
    chat = _prompt(SESSION, "what does ui-clone do?")
    assert _run(project, fake_uv, "ui_clone.hooks.claude_continuation", chat) == []


@pytest.mark.parametrize(
    "command",
    [
        "cd /repo\nnode bin/ui-clone gate tmp/ref/site all",
        "set -e\nbash skills/visual-debug/scripts/ae-compare.sh a.png b.png",
        "set -e\r\npython3 -m ui_clone.gate tmp/ref/site all",
        "echo start;\tui-clone gate tmp/ref/site all",
        "bash -lc 'cd /repo && node bin/ui-clone gate tmp/ref/site all'",
        "x=$(ui-clone state show tmp/ref/site) && echo \"$x\"",
    ],
)
def test_escaped_newline_and_wrappers_are_command_positions(
    tmp_path: Path, fake_uv: Path, command: str
) -> None:
    # A JSON newline is the two characters `\n`; a command after it must claim.
    project = tmp_path / "fresh"
    project.mkdir()
    assert len(_run(project, fake_uv, "ui_clone.hooks.pre_bash", _bash(SESSION, command))) == 1
    assert (project / "tmp" / SESSIONS_DIR / _digest(SESSION)).is_file()


@pytest.mark.parametrize(
    "command",
    [
        'grep -rn "python -m ui_clone.gate" docs',
        'echo "ui-clone gate tmp/ref/site all"',
        "echo 'node bin/ui-clone pipeline https://example.org'",
        "bash -n skills/visual-debug/scripts/ae-compare.sh",
        'bash -n "$SCRIPTS_DIR/section-compare.sh"',
        'cat "$SCRIPTS_DIR/section-compare.sh"',
        'git commit -m "run python -m ui_clone.gate first"',
    ],
)
def test_non_command_position_mentions_do_not_claim(tmp_path: Path, fake_uv: Path, command: str) -> None:
    project = _project(tmp_path)
    assert _run(project, fake_uv, "ui_clone.hooks.pre_bash", _bash(SESSION, command)) == []
    assert not (project / "tmp" / SESSIONS_DIR).exists()


def _bash_payload(event: str, command: str, **extra: object) -> str:
    data: dict[str, object] = {
        "hook_event_name": event,
        "session_id": SESSION,
        "tool_name": "Bash",
        "tool_input": {"command": command, **extra},
    }
    return json.dumps(data)


def test_only_the_pre_tool_use_command_field_claims(tmp_path: Path, fake_uv: Path) -> None:
    project = _project(tmp_path)
    # A description or a PostToolUse response mentioning a run command is not a claim.
    described = _bash_payload("PreToolUse", "ls", description="then node bin/ui-clone gate x all")
    assert _run(project, fake_uv, "ui_clone.hooks.pre_bash", described) == []
    post = json.dumps(
        {
            "hook_event_name": "PostToolUse",
            "session_id": SESSION,
            "tool_name": "Bash",
            "tool_input": {"command": "cat notes.txt"},
            "tool_response": {"stdout": "python -m ui_clone.gate tmp/ref/site all\n"},
        }
    )
    assert _run(project, fake_uv, "ui_clone.hooks.post_bash", post) == []
    real = _bash_payload("PostToolUse", "python -m ui_clone.gate tmp/ref/site all")
    assert _run(project, fake_uv, "ui_clone.hooks.post_bash", real) == []
    assert not (project / "tmp" / SESSIONS_DIR).exists()
    # An escaped quote inside the command does not end the extracted string.
    quoted = _bash_payload("PreToolUse", 'echo "a \\" b" && node bin/ui-clone gate x all')
    assert len(_run(project, fake_uv, "ui_clone.hooks.pre_bash", quoted)) == 1


def test_multi_megabyte_bash_command_claim_check_stays_linear(tmp_path: Path, fake_uv: Path) -> None:
    project = _project(tmp_path)
    body = 'echo "a\\"b" ; grep -n ui-clone x | ' * 60000  # ~2MB of separators and escaped quotes
    start = time.monotonic()
    assert _run(project, fake_uv, "ui_clone.hooks.pre_bash", _bash(SESSION, body)) == []
    assert time.monotonic() - start < 3.0


# Regression: the strict command-position grammar missed documented runs. A
# missed claim turns enforcement off, so any run pattern now claims unless its
# segment starts with a clearly non-executing command.
@pytest.mark.parametrize(
    "command",
    [
        'uv run --project "$UI_CLONE_ROOT" --no-dev --frozen python -m ui_clone.pipeline '
        "https://example.org site s status --json",
        "PYTHONPATH=/a uv run --project /a python -m ui_clone.gate tmp/ref/site all",
        "bash -lc 'node bin/ui-clone gate tmp/ref/x all'",
        'sh -c "node bin/ui-clone gate tmp/ref/x all"',
        'bash -lc "python3 -m ui_clone.pipeline status"',
        "for c in a b; do python -m ui_clone.gate tmp/ref/$c all; done",
        "if test -d tmp/ref/x; then python -m ui_clone.gate tmp/ref/x all; fi",
        "{ python -m ui_clone.gate tmp/ref/x all; }",
        "! python -m ui_clone.gate tmp/ref/x all",
        "sudo python -m ui_clone.gate tmp/ref/x all",
        "echo tmp/ref/x | xargs node bin/ui-clone gate",
        '"${CLAUDE_PLUGIN_ROOT}"/bin/ui-clone gate tmp/ref/x all',
        "grep -q ok log.txt; python -m ui_clone.gate tmp/ref/x all",
        "echo ui-clone gate; python -m ui_clone.goal tmp/ref/x",
    ],
)
def test_documented_run_forms_claim(tmp_path: Path, fake_uv: Path, command: str) -> None:
    project = tmp_path / "fresh"
    project.mkdir()
    assert len(_run(project, fake_uv, "ui_clone.hooks.pre_bash", _bash(SESSION, command))) == 1
    assert (project / "tmp" / SESSIONS_DIR / _digest(SESSION)).is_file()


def _codex(tool: str, key: str, value: object, event: str = "PreToolUse") -> str:
    return json.dumps(
        {"hook_event_name": event, "session_id": SESSION, "tool_name": tool, "tool_input": {key: value}}
    )


@pytest.mark.parametrize(
    "payload",
    [
        _codex("exec_command", "cmd", "python -m ui_clone.gate tmp/ref/x all"),
        _codex("exec_command", "cmd", ["bash", "-lc", "node bin/ui-clone gate tmp/ref/x all"]),
        _codex("shell", "command", ["bash", "-lc", "cd /repo && python3 -m ui_clone.pipeline status"]),
        _codex("Bash", "command", ["node", "bin/ui-clone", "gate", "tmp/ref/x", "all"]),
        _codex("exec_command", "cmd", ["python", "-m", "ui_clone.gate", "tmp/ref/x", "all"]),
    ],
)
def test_codex_cmd_and_argv_payloads_claim(tmp_path: Path, fake_uv: Path, payload: str) -> None:
    project = tmp_path / "fresh"
    project.mkdir()
    assert len(_run(project, fake_uv, "ui_clone.hooks.pre_bash", payload)) == 1
    assert (project / "tmp" / SESSIONS_DIR / _digest(SESSION)).is_file()


@pytest.mark.parametrize(
    "payload",
    [
        _bash(SESSION, "printf 'x\\nnode bin/ui-clone gate x all' > notes.txt"),
        _bash(SESSION, "# python -m ui_clone.gate tmp/ref/x all"),
        _bash(SESSION, "ls  # then node bin/ui-clone gate x all"),
        _bash(SESSION, "rg -n 'ui-clone gate' docs | head; sed -n 1p skills/visual-debug/scripts/a.sh"),
        _bash(SESSION, "git log --oneline -S 'python -m ui_clone.gate'"),
        _codex("exec_command", "cmd", ["grep", "-rn", "python -m ui_clone.gate", "docs"]),
        _codex("exec_command", "cmd", "python -m ui_clone.gate x all", event="PostToolUse"),
    ],
)
def test_non_executing_segments_and_post_events_do_not_claim(
    tmp_path: Path, fake_uv: Path, payload: str
) -> None:
    project = _project(tmp_path)
    assert _run(project, fake_uv, "ui_clone.hooks.pre_bash", payload) == []
    assert not (project / "tmp" / SESSIONS_DIR).exists()


@pytest.mark.parametrize(
    "command",
    [
        'grep -n "ui-clone gate" x | ' * 70000,  # ~2MB of excluded mentions
        "printf '" + "x\\n" * 500000 + "ui-clone gate'",  # ~1.5MB of shell-literal \n
        "cat <<EOF\n" + 'a\\b "q" ui-clone\n' * 100000 + "EOF",
    ],
    # Short ids: a multi-MB id lands in PYTEST_CURRENT_TEST and overflows exec.
    ids=["grep-mentions", "printf-literal-newlines", "heredoc"],
)
def test_multi_megabyte_mentions_stay_linear(tmp_path: Path, fake_uv: Path, command: str) -> None:
    project = _project(tmp_path)
    for payload in (_bash(SESSION, command), _codex("exec_command", "cmd", ["bash", "-lc", command])):
        start = time.monotonic()
        assert _run(project, fake_uv, "ui_clone.hooks.pre_bash", payload) == []
        assert time.monotonic() - start < 3.0


@pytest.mark.parametrize(
    "command",
    ["bash -lc 'echo node bin/ui-clone gate x all'", 'sh -c "grep -n ui-clone.gate x; cat $SCRIPTS_DIR/a.sh"'],
)
def test_shell_c_wrapper_of_non_executing_command_does_not_claim(
    tmp_path: Path, fake_uv: Path, command: str
) -> None:
    project = _project(tmp_path)
    assert _run(project, fake_uv, "ui_clone.hooks.pre_bash", _bash(SESSION, command)) == []
    assert not (project / "tmp" / SESSIONS_DIR).exists()
