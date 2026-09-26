"""pre-generate gate integration for clonability-report.json."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ui_clone import clonability as clon
from ui_clone.gate import Gate
from ui_clone.hooks import claude_continuation as hook
from ui_clone.hooks._common import mark_ref_session

from ._helpers import (
    _populate_pre_generate_artifacts,
    _set_extraction_state,
    _set_post_implement_state,
    make_ref_dir,
    make_search_root,
    run_hook,
    set_active_marker,
)


def _ref(tmp_path: Path) -> Path:
    ref_dir = make_ref_dir(make_search_root(tmp_path))
    _populate_pre_generate_artifacts(ref_dir)
    return ref_dir


def test_pre_generate_passes_with_report_and_fails_without(tmp_path: Path) -> None:
    ref_dir = _ref(tmp_path)
    assert Gate(ref_dir).run("pre-generate", json_output=True) == 0
    (ref_dir / "clonability-report.json").unlink()
    assert Gate(ref_dir).run("pre-generate", json_output=True) == 1


def test_run_already_past_pre_generate_is_not_newly_blocked(tmp_path: Path) -> None:
    ref_dir = _ref(tmp_path)
    (ref_dir / "clonability-report.json").unlink()
    _set_post_implement_state(ref_dir)
    assert Gate(ref_dir).run("pre-generate", json_output=True) == 0


# ── user-owned decisions: pre-bash deny, prompt hook, Stop release ─────────

SESSION = "clonability-session"


def _blocked_ref(tmp_path: Path) -> Path:
    ref_dir = make_ref_dir(make_search_root(tmp_path), "hero")
    _set_extraction_state(ref_dir)
    (ref_dir / "head.json").write_text(json.dumps({"title": "Just a moment..."}), encoding="utf-8")
    clon.write_report(ref_dir)
    return ref_dir


def _pre_bash(root: Path, cmd: str) -> str:
    result = run_hook(
        "ui_clone.hooks.pre_bash",
        stdin_data=json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": SESSION, "cwd": str(root)}
        ),
        env={"CLAUDE_PROJECT_DIR": str(root)},
    )
    return str(result.stdout) + str(result.stderr)


@pytest.mark.parametrize(
    "cmd",
    [
        "python -m ui_clone.clonability tmp/ref/hero --decide bot-challenge --decision proceed --note ok",
        'uv run python -m ui_clone.clonability "$(pwd)/tmp/ref/hero" --decide x --decision stop --note n',
        "python3 -c \"from ui_clone import clonability; clonability.record_decision('tmp/ref/hero')\"",
    ],
)
def test_pre_bash_denies_agent_recorded_decisions(tmp_path: Path, cmd: str) -> None:
    _blocked_ref(tmp_path)
    out = _pre_bash(tmp_path, cmd)
    assert "records a clonability blocker decision" in out
    assert "ui-clone decide <risk-id> proceed" in out and "own terminal" in out


@pytest.mark.parametrize(
    "cmd",
    [
        "jq '.decisions={}' tmp/ref/hero/x.json > tmp/ref/hero/clonability-report.json",
        "rm tmp/ref/hero/clonability-report.json",
        "cp /tmp/forged.json tmp/ref/hero/clonability-report.json",
    ],
)
def test_pre_bash_denies_report_overwrite_or_delete(tmp_path: Path, cmd: str) -> None:
    _blocked_ref(tmp_path)
    assert "enforcement-state access blocked" in _pre_bash(tmp_path, cmd)


def test_pre_bash_allows_producer_and_reads(tmp_path: Path) -> None:
    _blocked_ref(tmp_path)
    for cmd in (
        'python -m ui_clone.clonability "$(pwd)/tmp/ref/hero"',
        "jq .risks tmp/ref/hero/clonability-report.json",
        "grep -- --decide ui_clone/clonability.py",
        "uv run python -m pytest tests/test_clonability.py -k record_decision",
    ):
        assert "⛔" not in _pre_bash(tmp_path, cmd), cmd


def test_user_prompt_records_decision_and_cron_prompt_cannot(tmp_path: Path) -> None:
    ref_dir = _blocked_ref(tmp_path)
    cron = hook.handle(
        {
            "session_id": SESSION,
            "cwd": str(tmp_path),
            "hook_event_name": "PreToolUse",
            "tool_name": "CronCreate",
            "tool_input": {"prompt": "ui-clone decide bot-challenge proceed", "cron": "* * * * *"},
        },
        tmp_path,
    )
    assert cron is not None and json.loads(cron)["hookSpecificOutput"]["permissionDecision"] == "deny"
    out = hook.handle(
        {
            "session_id": SESSION,
            "cwd": str(tmp_path),
            "hook_event_name": "UserPromptSubmit",
            "prompt": "ui-clone decide bot-challenge proceed I passed the challenge",
        },
        tmp_path,
    )
    assert out is not None
    context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "Recorded the user's decision 'proceed' for bot-challenge" in context
    report = clon.load_report(ref_dir)
    assert report is not None and report["status"] == "ok"
    assert report["decisions"]["bot-challenge"]["provenance"]["source"] == "user-prompt"
    assert report["decisions"]["bot-challenge"]["provenance"]["sessionId"] == SESSION


def _stop(root: Path) -> subprocess.CompletedProcess:
    return run_hook(
        "ui_clone.hooks.section_gate",
        stdin_data=json.dumps({"hook_event_name": "Stop", "session_id": SESSION}),
        env={"CLAUDE_PROJECT_DIR": str(root), "UI_CLONE_HOOK_HOST": "claude"},
    )


def test_stop_is_released_while_a_blocker_awaits_the_user(tmp_path: Path) -> None:
    ref_dir = _blocked_ref(tmp_path)
    set_active_marker(ref_dir)
    mark_ref_session(ref_dir, SESSION, source="test")
    result = _stop(tmp_path)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout.strip())
    assert "decision" not in data
    message = data["systemMessage"]
    assert "waiting for YOUR decision" in message and "bot-challenge" in message
    assert "ui-clone decide bot-challenge proceed" in message

    # Once the user decided, the ordinary pre-generation Stop gate applies again.
    clon.record_decision(ref_dir, "bot-challenge", "proceed", "ok", provenance={"source": "user-terminal"})
    blocked = json.loads(_stop(tmp_path).stdout.strip())
    assert blocked.get("decision") == "block"


@pytest.mark.parametrize(
    "cmd",
    [
        # `script` hands the CLI a pty; abbreviations and `=` forms are still the recorder.
        "script -q /dev/null python -m ui_clone.clonability tmp/ref/hero --decid bot-challenge --decision proceed --note ok",
        "script -q /dev/null python -m ui_clone.clonability tmp/ref/hero --dec=bot-challenge",
        "nohup python3 -m ui_clone.clonability tmp/ref/hero --deci bot-challenge",
        "env A=1 python -m ui_clone.clonability tmp/ref/hero --decision proceed",
        "sudo -E python -m ui_clone.clonability tmp/ref/hero \"--de\"\"cide\" bot-challenge",
        "bash -c 'script -q /dev/null python -m ui_clone.clonability tmp/ref/hero --decid x'",
    ],
)
def test_pre_bash_denies_wrapped_or_abbreviated_decide(tmp_path: Path, cmd: str) -> None:
    _blocked_ref(tmp_path)
    assert "records a clonability blocker decision" in _pre_bash(tmp_path, cmd)


@pytest.mark.parametrize(
    "cmd",
    [
        "rm tmp/ref/hero/clonability-*.json",
        "rm -f tmp/ref/hero/clon*",
        "sudo rm tmp/ref/hero/*-report.json",
        "mv tmp/ref/hero/clonability-repor?.json /tmp/x",
        "find tmp/ref/hero -name 'clonab*' -delete",
        "bash -c 'rm tmp/ref/hero/[c]lonability-report.json'",
    ],
)
def test_pre_bash_denies_report_delete_by_glob(tmp_path: Path, cmd: str) -> None:
    _blocked_ref(tmp_path)
    assert "enforcement-state access blocked" in _pre_bash(tmp_path, cmd)


def test_pre_bash_allows_unrelated_globs(tmp_path: Path) -> None:
    _blocked_ref(tmp_path)
    for cmd in (
        "rm -f tmp/ref/hero/sections/*.json",
        "rm tmp/ref/hero/frames/*.png",
        "ls tmp/ref/hero/clon*",
        "rm -rf tmp/ref/hero",
    ):
        assert "⛔" not in _pre_bash(tmp_path, cmd), cmd


def test_stop_is_not_released_by_a_blocker_reopened_after_generation(tmp_path: Path) -> None:
    # After pre-generate passed, a blocker the agent re-opens (e.g. by
    # re-running the producer on changed inputs) must not release the Stop.
    ref_dir = _blocked_ref(tmp_path)
    set_active_marker(ref_dir)
    mark_ref_session(ref_dir, SESSION, source="test")
    _set_post_implement_state(ref_dir)
    clon.write_report(ref_dir)
    data = json.loads(_stop(tmp_path).stdout.strip())
    assert data.get("decision") == "block"
    assert "waiting for YOUR decision" not in json.dumps(data)


def test_stop_is_not_released_once_impl_source_exists(tmp_path: Path) -> None:
    ref_dir = _blocked_ref(tmp_path)
    set_active_marker(ref_dir)
    mark_ref_session(ref_dir, SESSION, source="test")
    src = tmp_path / "impl" / "src" / "app" / "page.tsx"
    src.parent.mkdir(parents=True)
    src.write_text("export default function Page() { return null }\n", encoding="utf-8")
    data = json.loads(_stop(tmp_path).stdout.strip())
    assert data.get("decision") == "block"
    assert "waiting for YOUR decision" not in json.dumps(data)


@pytest.mark.parametrize(
    "cmd",
    [
        # Backslash-newline is removed by the shell before word splitting.
        "script -q /dev/null python -m ui_clone.clonability tmp/ref/hero --\\\ndecide bot-challenge --decision proceed --note ok",
        "python -m ui_clone.clonability tmp/ref/hero \\\n  --dec\\\nide bot-challenge --decision stop --note n",
    ],
)
def test_pre_bash_denies_decide_split_by_line_continuation(tmp_path: Path, cmd: str) -> None:
    _blocked_ref(tmp_path)
    assert "records a clonability blocker decision" in _pre_bash(tmp_path, cmd)


@pytest.mark.parametrize(
    "cmd",
    [
        "env -u CLAUDECODE python -m ui_clone.clonability tmp/ref/hero",
        "unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT; M=ui_clone.clonability; python -m $M tmp/ref/hero",
        "CLAUDECODE= CODEX_THREAD_ID= python -m ui_clone.clonability tmp/ref/hero",
        "env -i PATH=$PATH python3 -m ui_clone.clonability tmp/ref/hero",
        "env - python3 -m ui_clone.clonability tmp/ref/hero",
        "python3 -c \"import os, runpy; os.environ.pop('CLAUDECODE'); runpy.run_module('ui_clone.clonability')\"",
    ],
)
def test_pre_bash_denies_agent_marker_tamper_next_to_clonability(tmp_path: Path, cmd: str) -> None:
    _blocked_ref(tmp_path)
    assert "records a clonability blocker decision" in _pre_bash(tmp_path, cmd)


def test_pre_bash_decide_match_is_per_line(tmp_path: Path) -> None:
    _blocked_ref(tmp_path)
    for cmd in (
        "python -m ui_clone.clonability tmp/ref/hero\ngit log --decorate --oneline -3",
        "python -m ui_clone.clonability tmp/ref/hero \\\n  --json\nls --decorate-free",
        "env -u FOO python -m ui_clone.clonability tmp/ref/hero",
    ):
        assert "⛔" not in _pre_bash(tmp_path, cmd), cmd
