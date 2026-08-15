from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ui_clone import claude_continuation as cc

_EVENT_PRE = "PreToolUse"
_EVENT_POST = "PostToolUse"
_EVENT_USER_PROMPT = "UserPromptSubmit"


def _emit_context(event: str, message: str) -> str:
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": message,
            }
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _require_str(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise cc.ContinuationError(f"missing required field: {field}")
    return value


def _require_dict(payload: Mapping[str, object], field: str) -> dict[str, object]:
    value = payload.get(field)
    if not isinstance(value, dict):
        raise cc.ContinuationError(f"missing required field: {field}")
    return dict(value)


def _require_present(payload: Mapping[str, object], field: str) -> object:
    if field not in payload:
        raise cc.ContinuationError(f"missing required field: {field}")
    return payload[field]


def _receipt(project_root: Path, session_id: str) -> dict[str, Any] | None:
    return cc.load_receipt(project_root, session_id)


def _tagged(receipt: Mapping[str, Any], value: object) -> bool:
    tag = receipt.get("leaseTag")
    return isinstance(tag, str) and isinstance(value, str) and f"[[{tag}]]" in value


def _json_block(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _skill_name(tool_input: Mapping[str, object]) -> str | None:
    for key in ("skill", "name"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value
    return None


def _is_ui_re_user_prompt(value: str) -> bool:
    slash = f"/{cc.UI_RE_SKILL}"
    return value == slash or (value.startswith(slash) and value[len(slash)].isspace())


def _user_prompt(payload: Mapping[str, object]) -> str | None:
    prompt = payload.get("prompt")
    if isinstance(prompt, str):
        return prompt
    legacy = payload.get("user_prompt")
    if isinstance(legacy, str):
        return legacy
    return None


def _correction_context(receipt: Mapping[str, Any]) -> str:
    return (
        "Replace the continuation CronCreate input with this exact one-shot task:\n"
        f"{_json_block(cc.cron_create_input(receipt))}"
    )


def _cronlist_context() -> str:
    return (
        "CronCreate did not return a supported structured cron id. Call CronList once now; "
        "the continuation hook will arm the receipt only if exactly one structured row "
        "contains the full continuation lease tag. If CronList cannot prove one exact match, "
        "mark the continuation capability unsupported."
    )


def _unsupported_context(reason: str) -> str:
    return (
        "Continuation lease registration failed closed. Do not assume automatic continuation. "
        "Mark this Claude continuation capability unsupported with "
        "python -m ui_clone.claude_continuation mark-unsupported --session-id <session> "
        f"--cwd . --reason {_json_block(reason)}"
    )


def _armed_context() -> str:
    return (
        "The owned one-shot continuation is armed; end the current assistant turn now. "
        "Do not run any further pipeline work in this turn; the exact tagged wake will resume it."
    )


def _wake_context(receipt: Mapping[str, Any]) -> str:
    return (
        "Exact one-shot wake accepted; the receipt is running and its auto-deleted cron id is "
        "cleared. Follow this validated contract now:\n"
        f"{cc.continuation_prompt(receipt)}"
    )


def _stale_wake_context() -> str:
    return (
        "Ignore this stale one-shot wake: manual resume already moved the receipt to canceling. "
        "Do not execute any pipeline work from this prompt."
    )


def _manual_resume_context(receipt: Mapping[str, Any]) -> str:
    return (
        "A manual user prompt arrived while the one-shot was armed. Before pipeline work, delete "
        "the exact owned task with CronDelete using this input:\n"
        f"{_json_block({'id': receipt['cronId']})}\n"
        "Continue the current user request only after successful deletion returns the receipt to running."
    )


def _delete_failure_context(tool_response: object) -> str:
    detail = tool_response if isinstance(tool_response, dict) else {"response": tool_response}
    return (
        "CronDelete did not return the supported successful response; receipt left unchanged: "
        f"{_json_block(detail)}"
    )


def _is_exact_croncreate(
    receipt: Mapping[str, Any], tool_input: Mapping[str, object]
) -> bool:
    if receipt.get("state") != cc.STATE_ARMING:
        return False
    return dict(tool_input) == cc.cron_create_input(receipt)


def _pre_skill(
    project_root: Path, session_id: str, tool_input: Mapping[str, object]
) -> str | None:
    if _skill_name(tool_input) != cc.UI_RE_SKILL:
        return None
    cc.activate(project_root, session_id, cc.UI_RE_SKILL)
    return None


def _userprompt_event(
    project_root: Path, session_id: str, payload: Mapping[str, object]
) -> str | None:
    prompt = _user_prompt(payload)
    if prompt is None:
        return None
    receipt = _receipt(project_root, session_id)
    state = receipt.get("state") if receipt is not None else None
    if receipt is not None and state in {cc.STATE_ARMED, cc.STATE_CANCELING}:
        expected = cc.continuation_prompt(receipt)
        if prompt == expected:
            if state == cc.STATE_ARMED:
                cc.accept_wake(project_root, session_id, prompt)
                return _emit_context(_EVENT_USER_PROMPT, _wake_context(receipt))
            return _emit_context(_EVENT_USER_PROMPT, _stale_wake_context())
    if receipt is not None and state == cc.STATE_ARMED:
        canceling = cc.begin_manual_resume(project_root, session_id)
        return _emit_context(_EVENT_USER_PROMPT, _manual_resume_context(canceling))
    if _is_ui_re_user_prompt(prompt):
        cc.activate(project_root, session_id, cc.UI_RE_SKILL)
    return None


def _pre_croncreate(
    project_root: Path, session_id: str, tool_input: Mapping[str, object]
) -> str | None:
    receipt = _receipt(project_root, session_id)
    if receipt is None or not _tagged(receipt, tool_input.get("prompt")):
        return None
    if _is_exact_croncreate(receipt, tool_input):
        return None
    if receipt.get("state") != cc.STATE_ARMING:
        return _emit_context(
            _EVENT_PRE,
            f"Continuation CronCreate is not allowed from {receipt.get('state')}; do not create it.",
        )
    return _emit_context(_EVENT_PRE, _correction_context(receipt))


def _pre_crondelete(
    project_root: Path, session_id: str, tool_input: Mapping[str, object]
) -> str | None:
    receipt = _receipt(project_root, session_id)
    if receipt is None or tool_input.get("id") != receipt.get("cronId"):
        return None
    return None


def _post_croncreate(
    project_root: Path,
    session_id: str,
    tool_input: Mapping[str, object],
    tool_response: object,
) -> str | None:
    receipt = _receipt(project_root, session_id)
    if receipt is None or not _tagged(receipt, tool_input.get("prompt")):
        return None
    if not _is_exact_croncreate(receipt, tool_input):
        if receipt.get("state") != cc.STATE_ARMING:
            return None
        return _emit_context(_EVENT_POST, _correction_context(receipt))
    try:
        cron_id = cc.extract_created_cron_id(tool_response, str(receipt["leaseTag"]))
    except cc.ContinuationError as exc:
        return _emit_context(_EVENT_POST, _unsupported_context(str(exc)))
    if cron_id is None:
        return _emit_context(_EVENT_POST, _cronlist_context())
    cc.mark_armed(project_root, session_id, cron_id)
    return _emit_context(_EVENT_POST, _armed_context())


def _post_cronlist(project_root: Path, session_id: str, tool_response: object) -> str | None:
    receipt = _receipt(project_root, session_id)
    if receipt is None:
        return None
    try:
        result = cc.reconcile_cron_snapshot(project_root, session_id, tool_response)
    except cc.ContinuationError as exc:
        return _emit_context(_EVENT_POST, _unsupported_context(str(exc)))
    if receipt.get("state") == cc.STATE_ARMING and result.status == cc.STATE_ARMED:
        return _emit_context(_EVENT_POST, _armed_context())
    if receipt.get("state") == cc.STATE_ARMING and result.status in {
        "absent",
        "unavailable",
        "unexpected",
    }:
        return _emit_context(
            _EVENT_POST,
            _unsupported_context(result.reason or f"cron list {result.status}"),
        )
    if result.status == "unexpected":
        return _emit_context(_EVENT_POST, _unsupported_context("unexpected owned cron row"))
    return None


def _post_crondelete(
    project_root: Path,
    session_id: str,
    tool_input: Mapping[str, object],
    tool_response: object,
) -> str | None:
    receipt = _receipt(project_root, session_id)
    if receipt is None or tool_input.get("id") != receipt.get("cronId"):
        return None
    if not (isinstance(tool_response, dict) and tool_response == {"ok": True}):
        return _emit_context(_EVENT_POST, _delete_failure_context(tool_response))
    cc.finish_owned_delete(project_root, session_id)
    return None


def _handle(payload: dict[str, object], project_root: Path) -> str | None:
    session_id = _require_str(payload, "session_id")
    _require_str(payload, "cwd")
    event = _require_str(payload, "hook_event_name")
    root = Path(project_root).resolve()

    if event == _EVENT_USER_PROMPT:
        return _userprompt_event(root, session_id, payload)

    tool = _require_str(payload, "tool_name")
    tool_input = _require_dict(payload, "tool_input")

    if event == _EVENT_PRE and tool == "Skill":
        return _pre_skill(root, session_id, tool_input)
    if event == _EVENT_PRE and tool == "CronCreate":
        return _pre_croncreate(root, session_id, tool_input)
    if event == _EVENT_PRE and tool == "CronDelete":
        return _pre_crondelete(root, session_id, tool_input)

    if event == _EVENT_POST:
        tool_response = _require_present(payload, "tool_response")
        _require_str(payload, "tool_use_id")
        if tool == "CronCreate":
            return _post_croncreate(root, session_id, tool_input, tool_response)
        if tool == "CronList":
            return _post_cronlist(root, session_id, tool_response)
        if tool == "CronDelete":
            return _post_crondelete(root, session_id, tool_input, tool_response)
    return None


def handle(payload: dict[str, object], project_root: Path) -> str | None:
    try:
        return _handle(payload, project_root)
    except cc.ContinuationError as exc:
        event = payload.get("hook_event_name")
        return _emit_context(event if isinstance(event, str) else _EVENT_PRE, str(exc))


def main() -> None:
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    if not raw.strip():
        return
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise cc.ContinuationError("hook payload must be an object")
        cwd = payload.get("cwd")
        project_root = Path(cwd).resolve() if isinstance(cwd, str) and cwd else Path.cwd()
        output = handle(payload, project_root)
    except (json.JSONDecodeError, cc.ContinuationError) as exc:
        output = _emit_context(_EVENT_PRE, str(exc))
    if output:
        print(output)


if __name__ == "__main__":
    main()
