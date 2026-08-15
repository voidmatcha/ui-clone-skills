from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from tests.hooks._helpers import run_hook
from ui_clone import claude_continuation as cc
from ui_clone.hooks import claude_continuation as hook

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


def cron_input(receipt: dict[str, object]) -> dict[str, object]:
    return {
        "cron": "* * * * *",
        "prompt": hook._bootstrap_prompt(receipt, SESSION),
        "recurring": True,
        "durable": False,
    }


def cron_row(receipt: dict[str, object], cron_id: str = CRON_ID) -> dict[str, object]:
    return {
        "id": cron_id,
        "schedule": "* * * * *",
        "recurring": True,
        "prompt": f"wake [[{receipt['leaseTag']}]]",
    }


def test_matching_skill_creates_pending_receipt_and_requests_cron(tmp_path: Path) -> None:
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
    assert receipt["state"] == cc.STATE_PENDING
    ctx = context(out)
    assert "CronCreate" in ctx
    assert '"cron": "* * * * *"' in ctx
    assert '"recurring": true' in ctx
    assert '"durable": false' in ctx
    assert f"[[{receipt['leaseTag']}]]" in ctx


def test_shim_allows_continuation_bootstrap_before_tmp_ref_exists(tmp_path: Path) -> None:
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
    receipt = cc.load_receipt(tmp_path, SESSION)
    assert receipt is not None
    assert receipt["state"] == cc.STATE_PENDING
    ctx = context(result.stdout.strip())
    assert "CronCreate" in ctx
    assert f"[[{receipt['leaseTag']}]]" in ctx


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


def test_pre_croncreate_accepts_exact_continuation_input(tmp_path: Path) -> None:
    receipt = cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)

    out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PreToolUse",
            tool="CronCreate",
            tool_input=cron_input(receipt),
        ),
    )

    assert out == ""


def test_pre_croncreate_correction_for_owned_malformed_input(tmp_path: Path) -> None:
    receipt = cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)

    out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PreToolUse",
            tool="CronCreate",
            tool_input={
                "cron": "*/5 * * * *",
                "prompt": f"wake [[{receipt['leaseTag']}]]",
                "recurring": True,
                "durable": False,
            },
        ),
    )

    ctx = context(out)
    assert "Replace the continuation CronCreate input" in ctx
    assert '"cron": "* * * * *"' in ctx


def test_pre_unowned_croncreate_and_delete_are_noops(tmp_path: Path) -> None:
    cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)

    create_out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PreToolUse",
            tool="CronCreate",
            tool_input={"cron": "* * * * *", "prompt": "unrelated", "recurring": True},
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


def test_failed_or_unstructured_create_never_activates(tmp_path: Path) -> None:
    receipt = cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)

    out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronCreate",
            tool_input=cron_input(receipt),
            tool_response="created maybe",
        ),
    )

    assert cc.load_receipt(tmp_path, SESSION)["state"] == cc.STATE_PENDING  # type: ignore[index]
    assert "CronList" in context(out)


def test_post_croncreate_structured_id_activates(tmp_path: Path) -> None:
    receipt = cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)

    out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronCreate",
            tool_input=cron_input(receipt),
            tool_response={"id": CRON_ID},
        ),
    )

    assert out == ""
    current = cc.load_receipt(tmp_path, SESSION)
    assert current is not None
    assert current["state"] == cc.STATE_ACTIVE
    assert current["cronId"] == CRON_ID


def test_post_unrelated_croncreate_structured_id_is_noop(tmp_path: Path) -> None:
    cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)

    out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronCreate",
            tool_input={"cron": "* * * * *", "prompt": "unrelated", "recurring": True},
            tool_response={"id": CRON_ID},
        ),
    )

    assert out == ""
    receipt = cc.load_receipt(tmp_path, SESSION)
    assert receipt is not None
    assert receipt["state"] == cc.STATE_PENDING
    assert "cronId" not in receipt


def test_post_tagged_malformed_croncreate_keeps_pending_and_corrects(tmp_path: Path) -> None:
    receipt = cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)

    out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronCreate",
            tool_input={
                "cron": "*/5 * * * *",
                "prompt": f"wake [[{receipt['leaseTag']}]]",
                "recurring": True,
                "durable": False,
            },
            tool_response={"id": CRON_ID},
        ),
    )

    assert "Replace the continuation CronCreate input" in context(out)
    current = cc.load_receipt(tmp_path, SESSION)
    assert current is not None
    assert current["state"] == cc.STATE_PENDING
    assert "cronId" not in current


def test_cronlist_recovers_zero_and_duplicates(tmp_path: Path) -> None:
    receipt = cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)

    assert "unsupported" in context(
        run_adapter(
            tmp_path,
            payload(
                project=tmp_path,
                event="PostToolUse",
                tool="CronList",
                tool_response={"crons": []},
            ),
        )
    )
    assert cc.load_receipt(tmp_path, SESSION)["state"] == cc.STATE_PENDING  # type: ignore[index]

    out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronList",
            tool_response={"crons": [cron_row(receipt)]},
        ),
    )
    assert out == ""
    assert cc.load_receipt(tmp_path, SESSION)["state"] == cc.STATE_ACTIVE  # type: ignore[index]

    cc.pause(tmp_path, SESSION)
    cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    dup_out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronList",
            tool_response={"crons": [cron_row(receipt, "cron-a"), cron_row(receipt, "cron-b")]},
        ),
    )
    assert "duplicate" in context(dup_out).lower()
    assert cc.load_receipt(tmp_path, SESSION)["state"] == cc.STATE_PENDING  # type: ignore[index]


def test_pending_cronlist_unavailable_or_malformed_fails_closed(tmp_path: Path) -> None:
    receipt = cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)

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
    assert cc.load_receipt(tmp_path, SESSION)["state"] == cc.STATE_PENDING  # type: ignore[index]

    malformed = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronList",
            tool_response={"crons": [{"id": 3, "prompt": f"[[{receipt['leaseTag']}]]"}]},
        ),
    )
    assert "unsupported" in context(malformed)
    assert "malformed" in context(malformed)
    assert cc.load_receipt(tmp_path, SESSION)["state"] == cc.STATE_PENDING  # type: ignore[index]


def test_crondelete_owned_success_classifies_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    cc.mark_active(tmp_path, SESSION, CRON_ID)
    calls: list[tuple[Path, str]] = []

    def fake_delete(project: Path, session_id: str) -> dict[str, object]:
        calls.append((project, session_id))
        return {**receipt, "state": cc.STATE_PAUSED}

    monkeypatch.setattr(cc, "owned_delete_outcome", fake_delete)

    out = hook.handle(
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronDelete",
            tool_input={"id": CRON_ID},
            tool_response={"ok": True},
        ),
        tmp_path,
    )

    assert out is None
    assert calls == [(tmp_path.resolve(), SESSION)]


def test_crondelete_unowned_success_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    cc.mark_active(tmp_path, SESSION, CRON_ID)

    def fail_delete(project: Path, session_id: str) -> dict[str, object]:
        raise AssertionError("unowned delete should not classify")

    monkeypatch.setattr(cc, "owned_delete_outcome", fail_delete)

    out = hook.handle(
        payload(
            project=tmp_path,
            event="PostToolUse",
            tool="CronDelete",
            tool_input={"id": "other-cron"},
            tool_response={"ok": True},
        ),
        tmp_path,
    )

    assert out is None


def test_post_crondelete_missing_response_does_not_classify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    cc.mark_active(tmp_path, SESSION, CRON_ID)

    def fail_delete(project: Path, session_id: str) -> dict[str, object]:
        raise AssertionError("missing response should not classify")

    monkeypatch.setattr(cc, "owned_delete_outcome", fail_delete)
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
    assert cc.load_receipt(tmp_path, SESSION)["state"] == cc.STATE_ACTIVE  # type: ignore[index]


def test_pre_crondelete_paused_job_self_delete_guidance(tmp_path: Path) -> None:
    cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    cc.mark_active(tmp_path, SESSION, CRON_ID)
    cc.pause(tmp_path, SESSION)

    out = run_adapter(
        tmp_path,
        payload(
            project=tmp_path,
            event="PreToolUse",
            tool="CronDelete",
            tool_input={"id": CRON_ID},
        ),
    )

    assert "paused continuation receipt" in context(out)


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
