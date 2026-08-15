from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from ui_clone import claude_continuation as cc
from ui_clone.hooks import claude_continuation as hook

from ._helpers import run_hook

MODULE = "ui_clone.hooks.claude_continuation"
ROOT = Path(__file__).resolve().parents[2]
SHIM = ROOT / "hooks" / "shim.sh"
SESSION = "fe87fc97-d23a-496e-b13a-5ca5ab651f0d"
CRON_ID = "cron-001_OK"


def payload(
    *,
    project: Path,
    event: str,
    tool: str,
    tool_input: dict[str, Any] | None = None,
    tool_response: object = None,
    session_id: str = SESSION,
    include_post_fields: bool = False,
) -> dict[str, object]:
    data: dict[str, object] = {
        "session_id": session_id,
        "cwd": str(project),
        "hook_event_name": event,
        "tool_name": tool,
        "tool_input": tool_input or {},
    }
    if include_post_fields or event == "PostToolUse":
        data["tool_response"] = tool_response
        data["tool_use_id"] = "toolu_123"
    return data


def user_prompt_payload(
    *,
    project: Path,
    prompt: object | None = None,
    user_prompt: object | None = None,
    session_id: str = SESSION,
) -> dict[str, object]:
    data: dict[str, object] = {
        "session_id": session_id,
        "cwd": str(project),
        "hook_event_name": "UserPromptSubmit",
    }
    if prompt is not None:
        data["prompt"] = prompt
    if user_prompt is not None:
        data["user_prompt"] = user_prompt
    return data


def run_adapter(project: Path, data: dict[str, object]) -> str:
    result = run_hook(MODULE, stdin_data=json.dumps(data), env={"CLAUDE_PROJECT_DIR": str(project)})
    assert result.returncode == 0, result.stderr
    return cast(str, result.stdout).strip()


def run_shim_adapter(project: Path, data: dict[str, object]) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project)}
    return subprocess.run(
        ["bash", str(SHIM), MODULE],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        env=env,
        cwd=project,
    )


def context(stdout: str) -> str:
    parsed = cast(dict[str, Any], json.loads(stdout))
    hook_output = cast(dict[str, Any], parsed["hookSpecificOutput"])
    return cast(str, hook_output["additionalContext"])


def hook_event_name(stdout: str) -> str:
    parsed = cast(dict[str, Any], json.loads(stdout))
    hook_output = cast(dict[str, Any], parsed["hookSpecificOutput"])
    return cast(str, hook_output["hookEventName"])


def croncreate_input_from_context(stdout: str) -> dict[str, object]:
    ctx = context(stdout)
    start = ctx.index("{")
    value, _ = json.JSONDecoder().raw_decode(ctx[start:])
    return cast(dict[str, object], value)


def make_arming(project: Path) -> dict[str, object]:
    cc.activate(project, SESSION, cc.UI_RE_SKILL)
    ref = project / "tmp" / "ref" / "demo"
    ref.mkdir(parents=True, exist_ok=True)
    cc.bind_ref(project, SESSION, ref)
    return cast(dict[str, object], cc.arm(project, SESSION))


def make_armed(project: Path) -> dict[str, object]:
    make_arming(project)
    return cast(dict[str, object], cc.mark_armed(project, SESSION, CRON_ID))


def cron_row(receipt: dict[str, object], cron_id: str = CRON_ID) -> dict[str, object]:
    return {
        "id": cron_id,
        "schedule": "* * * * *",
        "recurring": False,
        "prompt": cc.continuation_prompt(receipt),
    }


def test_matching_skill_activation_creates_running_without_cron_context(tmp_path: Path) -> None:
    out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PreToolUse",
            tool="Skill",
            tool_input={"skill": cc.UI_RE_SKILL},
        ),
    )

    receipt = cc.load_receipt(tmp_path, SESSION)
    assert receipt is not None
    assert receipt["state"] == cc.STATE_RUNNING
    assert "cronId" not in receipt
    assert out == ""


def test_userprompt_raw_slash_skill_creates_running_without_cron_context(tmp_path: Path) -> None:
    out = run_adapter(
        tmp_path,
        user_prompt_payload(
            project=tmp_path,
            prompt="/ui-clone-skills:ui-reverse-engineering https://realfood.gov/",
        ),
    )

    receipt = cc.load_receipt(tmp_path, SESSION)
    assert receipt is not None
    assert receipt["state"] == cc.STATE_RUNNING
    assert "cronId" not in receipt
    assert out == ""


def test_userprompt_command_xml_is_not_exact_start_slash_activation(tmp_path: Path) -> None:
    out = run_adapter(
        tmp_path,
        user_prompt_payload(
            project=tmp_path,
            user_prompt=(
                "<command-message>ui-clone-skills:ui-reverse-engineering</command-message>\n"
                "<command-name>/ui-clone-skills:ui-reverse-engineering</command-name>\n"
                "<command-args>https://realfood.gov/</command-args>"
            ),
        ),
    )

    assert out == ""
    assert cc.load_receipt(tmp_path, SESSION) is None


@pytest.mark.parametrize(
    "prompt",
    [
        "please run /ui-clone-skills:ui-reverse-engineering https://realfood.gov/",
        "$ui-clone-skills:ui-reverse-engineering https://realfood.gov/",
        "/ui-clone-skills:ui-reverse-engineerings https://realfood.gov/",
        "/ui-clone-skills:visual-debug https://realfood.gov/",
        " /ui-clone-skills:ui-reverse-engineering https://realfood.gov/",
        "<command-name>/ui-clone-skills:ui-reverse-engineering</command-name>",
        "",
    ],
)
def test_userprompt_non_exact_skill_invocations_are_noops(tmp_path: Path, prompt: str) -> None:
    out = run_adapter(tmp_path, user_prompt_payload(project=tmp_path, prompt=prompt))

    assert out == ""
    assert cc.load_receipt(tmp_path, SESSION) is None


def test_userprompt_missing_or_invalid_prompt_is_noop(tmp_path: Path) -> None:
    assert run_adapter(tmp_path, user_prompt_payload(project=tmp_path)) == ""
    assert run_adapter(tmp_path, user_prompt_payload(project=tmp_path, prompt={"bad": True})) == ""
    assert cc.load_receipt(tmp_path, SESSION) is None


def test_userprompt_malformed_payload_error_preserves_hook_event_name(tmp_path: Path) -> None:
    result = run_hook(
        MODULE,
        stdin_data=json.dumps(
            {
                "session_id": "../../../bad",
                "cwd": str(tmp_path),
                "hook_event_name": "UserPromptSubmit",
                "prompt": f"/{cc.UI_RE_SKILL} https://realfood.gov/",
            }
        ),
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )

    assert result.returncode == 0
    out = result.stdout.strip()
    assert hook_event_name(out) == "UserPromptSubmit"
    assert "invalid session id" in context(out)


def test_malformed_json_error_defaults_to_pretooluse_hook_event(tmp_path: Path) -> None:
    result = run_hook(MODULE, stdin_data="{not json", env={"CLAUDE_PROJECT_DIR": str(tmp_path)})

    assert result.returncode == 0
    out = result.stdout.strip()
    assert hook_event_name(out) == "PreToolUse"
    assert "Expecting property name" in context(out)


def test_shim_allows_activation_before_tmp_ref_exists(tmp_path: Path) -> None:
    result = run_shim_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PreToolUse",
            tool="Skill",
            tool_input={"skill": cc.UI_RE_SKILL},
        ),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
    receipt = cc.load_receipt(tmp_path, SESSION)
    assert receipt is not None
    assert receipt["state"] == cc.STATE_RUNNING


def test_unrelated_skill_is_noop(tmp_path: Path) -> None:
    out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PreToolUse",
            tool="Skill",
            tool_input={"skill": "ui-clone-skills:visual-debug"},
        ),
    )

    assert out == ""
    assert cc.load_receipt(tmp_path, SESSION) is None


def test_pre_croncreate_accepts_only_exact_nonrecurring_input(tmp_path: Path) -> None:
    arming = make_arming(tmp_path)
    exact = cc.cron_create_input(arming)

    assert exact["recurring"] is False
    assert run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PreToolUse",
            tool="CronCreate",
            tool_input=exact,
        ),
    ) == ""


def test_pre_croncreate_corrects_tagged_malformed_input(tmp_path: Path) -> None:
    arming = make_arming(tmp_path)
    malformed = {
        **cc.cron_create_input(arming),
        "cron": "*/5 * * * *",
        "recurring": True,
    }

    out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PreToolUse",
            tool="CronCreate",
            tool_input=malformed,
        ),
    )

    assert croncreate_input_from_context(out) == cc.cron_create_input(arming)
    assert cc.load_receipt(tmp_path, SESSION)["state"] == cc.STATE_ARMING  # type: ignore[index]


def test_pre_unowned_croncreate_and_delete_are_noops(tmp_path: Path) -> None:
    make_arming(tmp_path)

    create_out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PreToolUse",
            tool="CronCreate",
            tool_input={"cron": "* * * * *", "prompt": "unrelated", "recurring": False},
        ),
    )
    delete_out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PreToolUse",
            tool="CronDelete",
            tool_input={"id": "other-cron"},
        ),
    )

    assert create_out == ""
    assert delete_out == ""


def test_post_unstructured_create_keeps_arming_and_requests_one_cronlist(tmp_path: Path) -> None:
    arming = make_arming(tmp_path)

    out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronCreate",
            tool_input=cc.cron_create_input(arming),
            tool_response="created maybe",
        ),
    )

    assert cc.load_receipt(tmp_path, SESSION)["state"] == cc.STATE_ARMING  # type: ignore[index]
    assert "CronList" in context(out)


def test_post_structured_create_arms_and_requires_immediate_turn_end(tmp_path: Path) -> None:
    arming = make_arming(tmp_path)

    out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronCreate",
            tool_input=cc.cron_create_input(arming),
            tool_response={"id": CRON_ID},
        ),
    )

    current = cc.load_receipt(tmp_path, SESSION)
    assert current is not None
    assert current["state"] == cc.STATE_ARMED
    assert current["cronId"] == CRON_ID
    assert "end the current assistant turn" in context(out)
    assert "pipeline work" in context(out)


def test_post_unrelated_croncreate_structured_id_is_noop(tmp_path: Path) -> None:
    make_arming(tmp_path)

    out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronCreate",
            tool_input={"cron": "* * * * *", "prompt": "unrelated", "recurring": False},
            tool_response={"id": CRON_ID},
        ),
    )

    assert out == ""
    current = cc.load_receipt(tmp_path, SESSION)
    assert current is not None
    assert current["state"] == cc.STATE_ARMING
    assert "cronId" not in current


def test_cronlist_recovers_one_match_and_rejects_zero_or_duplicates(tmp_path: Path) -> None:
    arming = make_arming(tmp_path)

    absent = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronList",
            tool_response={"crons": []},
        ),
    )
    assert "unsupported" in context(absent)
    assert cc.load_receipt(tmp_path, SESSION)["state"] == cc.STATE_ARMING  # type: ignore[index]

    one = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronList",
            tool_response={"crons": [cron_row(arming)]},
        ),
    )
    assert "end the current assistant turn" in context(one)
    assert "pipeline work" in context(one)
    assert cc.load_receipt(tmp_path, SESSION)["state"] == cc.STATE_ARMED  # type: ignore[index]

    project = tmp_path / "duplicate"
    duplicate_arming = make_arming(project)
    duplicate = run_adapter(
        project,
        payload(
            project=project,
            event="PostToolUse",
            tool="CronList",
            tool_response={
                "crons": [cron_row(duplicate_arming, "cron-a"), cron_row(duplicate_arming, "cron-b")]
            },
        ),
    )
    assert "duplicate" in context(duplicate).lower()
    assert cc.load_receipt(project, SESSION)["state"] == cc.STATE_ARMING  # type: ignore[index]


def test_cronlist_unavailable_or_malformed_arming_fails_closed(tmp_path: Path) -> None:
    arming = make_arming(tmp_path)

    unavailable = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronList",
            tool_response={"session_crons": "unknown"},
        ),
    )
    assert "unsupported" in context(unavailable)
    assert cc.load_receipt(tmp_path, SESSION)["state"] == cc.STATE_ARMING  # type: ignore[index]

    malformed = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronList",
            tool_response={"crons": [{"id": 3, "prompt": f"[[{arming['leaseTag']}]]"}]},
        ),
    )
    assert "unsupported" in context(malformed)
    assert "malformed" in context(malformed)
    assert cc.load_receipt(tmp_path, SESSION)["state"] == cc.STATE_ARMING  # type: ignore[index]


def test_exact_wake_resumes_before_pipeline_context(tmp_path: Path) -> None:
    armed = make_armed(tmp_path)
    wake = cc.continuation_prompt(armed)

    out = run_adapter(tmp_path, user_prompt_payload(project=tmp_path, prompt=wake))

    current = cc.load_receipt(tmp_path, SESSION)
    assert current is not None
    assert current["state"] == cc.STATE_RUNNING
    assert "cronId" not in current
    assert "goal --check-done" in context(out)


def test_altered_wake_does_not_resume_and_requires_owned_delete(tmp_path: Path) -> None:
    armed = make_armed(tmp_path)
    altered = cc.continuation_prompt(armed) + "\nchanged"

    out = run_adapter(tmp_path, user_prompt_payload(project=tmp_path, prompt=altered))

    current = cc.load_receipt(tmp_path, SESSION)
    assert current is not None
    assert current["state"] == cc.STATE_CANCELING
    assert json.dumps({"id": CRON_ID}, sort_keys=True) in context(out)
    assert "goal --check-done" not in context(out)


def test_manual_prompt_cancels_armed_job_before_work(tmp_path: Path) -> None:
    make_armed(tmp_path)

    out = run_adapter(
        tmp_path,
        user_prompt_payload(project=tmp_path, prompt="continue with the fix"),
    )

    current = cc.load_receipt(tmp_path, SESSION)
    assert current is not None
    assert current["state"] == cc.STATE_CANCELING
    assert json.dumps({"id": CRON_ID}, sort_keys=True) in context(out)
    assert "pipeline work" in context(out)


def test_exact_wake_loses_to_manual_resume_after_canceling_transition(tmp_path: Path) -> None:
    armed = make_armed(tmp_path)
    wake = cc.continuation_prompt(armed)
    cc.begin_manual_resume(tmp_path, SESSION)

    out = run_adapter(tmp_path, user_prompt_payload(project=tmp_path, prompt=wake))

    current = cc.load_receipt(tmp_path, SESSION)
    assert current is not None
    assert current["state"] == cc.STATE_CANCELING
    assert "stale one-shot wake" in context(out)
    assert "goal --check-done" not in context(out)


@pytest.mark.parametrize(
    ("before", "after"),
    [(cc.STATE_CANCELING, cc.STATE_RUNNING), (cc.STATE_ARMED, cc.STATE_PAUSED)],
)
def test_owned_delete_success_uses_state_specific_outcome(
    tmp_path: Path, before: str, after: str
) -> None:
    make_armed(tmp_path)
    if before == cc.STATE_CANCELING:
        cc.begin_manual_resume(tmp_path, SESSION)

    out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronDelete",
            tool_input={"id": CRON_ID},
            tool_response={"ok": True},
        ),
    )

    assert out == ""
    assert cc.load_receipt(tmp_path, SESSION)["state"] == after  # type: ignore[index]


def test_unowned_crondelete_success_is_noop(tmp_path: Path) -> None:
    make_armed(tmp_path)

    out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronDelete",
            tool_input={"id": "other-cron"},
            tool_response={"ok": True},
        ),
    )

    assert out == ""
    assert cc.load_receipt(tmp_path, SESSION)["state"] == cc.STATE_ARMED  # type: ignore[index]


@pytest.mark.parametrize(
    ("tool_response", "expected_context"),
    [({"error": "CronDelete failed"}, "CronDelete failed"), ({}, "supported successful response")],
)
def test_unsuccessful_owned_crondelete_preserves_canceling(
    tmp_path: Path, tool_response: dict[str, object], expected_context: str
) -> None:
    make_armed(tmp_path)
    cc.begin_manual_resume(tmp_path, SESSION)

    out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronDelete",
            tool_input={"id": CRON_ID},
            tool_response=tool_response,
        ),
    )

    current = cc.load_receipt(tmp_path, SESSION)
    assert current is not None
    assert current["state"] == cc.STATE_CANCELING
    assert current["cronId"] == CRON_ID
    assert expected_context in context(out)


def test_post_crondelete_missing_response_does_not_transition(tmp_path: Path) -> None:
    make_armed(tmp_path)
    cc.begin_manual_resume(tmp_path, SESSION)
    data = payload(
        project=tmp_path,
        event="PostToolUse",
        tool="CronDelete",
        tool_input={"id": CRON_ID},
        tool_response={"ok": True},
    )
    del data["tool_response"]

    out = hook.handle(data, tmp_path)

    assert out is not None
    assert "missing required field: tool_response" in context(out)
    assert cc.load_receipt(tmp_path, SESSION)["state"] == cc.STATE_CANCELING  # type: ignore[index]


def test_valid_cronlist_absence_pauses_armed_without_context(tmp_path: Path) -> None:
    make_armed(tmp_path)

    out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronList",
            tool_response={"crons": []},
        ),
    )

    assert out == ""
    assert cc.load_receipt(tmp_path, SESSION)["state"] == cc.STATE_PAUSED  # type: ignore[index]


def test_missing_required_payload_fields_fail_closed(tmp_path: Path) -> None:
    result = run_hook(
        MODULE,
        stdin_data=json.dumps(
            {
                "session_id": SESSION,
                "cwd": str(tmp_path),
                "hook_event_name": "PostToolUse",
                "tool_name": "CronCreate",
                "tool_input": {},
                "tool_response": {"id": CRON_ID},
            }
        ),
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )

    assert result.returncode == 0
    assert "missing required field: tool_use_id" in context(result.stdout.strip())
