from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ui_clone import claude_continuation as cc

_SCHEDULE = "* * * * *"
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


def _bootstrap_prompt(receipt: Mapping[str, Any], session_id: str) -> str:
    tag = receipt["leaseTag"]
    status_command = (
        f"python -m ui_clone.claude_continuation status --session-id {session_id} --cwd . --json"
    )
    return (
        f"[[{tag}]]\n"
        "Claude UI reverse-engineering continuation lease bootstrap.\n"
        "This scheduled task is session-scoped and non-durable. On wake-up, first run: "
        f"{status_command}.\n"
        "Parse that JSON receipt; load exactly one bound refDir from the receipt, validate "
        "that it is a non-empty relative path with no .. components, and reject missing, "
        "ambiguous, absolute, or escaping refs. Treat the loaded receipt refDir as "
        "<bound-ref>; do not use any other ref.\n"
        "If the receipt is pending, paused, complete, terminal, or unsupported, do not start "
        "pipeline work; delete the owned task when the receipt says it is paused or final.\n"
        "Only if the receipt state is active, run the canonical goal --check-done command: "
        "python -m ui_clone.goal <bound-ref> --check-done.\n"
        "If complete, delete the owned scheduled task and report the verify-stamp evidence.\n"
        "If terminal or authority is required, delete the owned scheduled task and report the "
        "blocker.\n"
        "If incomplete, read these in order before acting: "
        "python -m ui_clone.pipeline <bound-ref> status --json; "
        "python -m ui_clone.pipeline <bound-ref> next --json; "
        "python -m ui_clone.pipeline <bound-ref> report --for-llm.\n"
        "Execute the reported next required action while preserving normal gates."
    )


def _cron_create_input(receipt: Mapping[str, Any], session_id: str) -> dict[str, object]:
    return {
        "cron": _SCHEDULE,
        "prompt": _bootstrap_prompt(receipt, session_id),
        "recurring": True,
        "durable": False,
    }


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
    if value == slash or (value.startswith(slash) and value[len(slash)].isspace()):
        return True
    command_xml = (
        f"<command-message>{cc.UI_RE_SKILL}</command-message>\n"
        f"<command-name>/{cc.UI_RE_SKILL}</command-name>"
    )
    if not value.startswith(command_xml):
        return False
    tail = value[len(command_xml) :]
    return tail == "" or tail.startswith("\n<command-args>")


def _user_prompt(payload: Mapping[str, object]) -> str | None:
    prompt = payload.get("prompt")
    if isinstance(prompt, str):
        return prompt
    legacy = payload.get("user_prompt")
    if isinstance(legacy, str):
        return legacy
    return None


def _create_context(receipt: Mapping[str, Any], session_id: str) -> str:
    return (
        "Before starting substantive UI reverse-engineering pipeline work, create exactly one "
        "Claude scheduled task with CronCreate using this exact input:\n"
        f"{_json_block(_cron_create_input(receipt, session_id))}\n"
        "Wait for CronCreate to succeed so the continuation receipt can become active."
    )


def _correction_context(receipt: Mapping[str, Any], session_id: str) -> str:
    return (
        "Replace the continuation CronCreate input with this exact session-scoped, "
        "non-durable one-minute task:\n"
        f"{_json_block(_cron_create_input(receipt, session_id))}"
    )


def _cronlist_context() -> str:
    return (
        "CronCreate did not return a supported structured cron id. Call CronList once now; "
        "the continuation hook will activate the receipt only if exactly one structured row "
        "contains the full continuation lease tag. If CronList cannot prove one exact match, "
        "mark the continuation capability unsupported before pipeline work."
    )


def _unsupported_context(reason: str) -> str:
    return (
        "Continuation lease registration failed closed. Do not start pipeline work under an "
        "assumed lease. Mark this Claude continuation capability unsupported with "
        "python -m ui_clone.claude_continuation mark-unsupported --session-id <session> "
        f"--cwd . --reason {_json_block(reason)}"
    )


def _paused_delete_context() -> str:
    return (
        "This is a paused continuation receipt. Delete the still-present owned scheduled task "
        "and stop without running pipeline work."
    )


def _is_exact_croncreate(
    receipt: Mapping[str, Any], session_id: str, tool_input: Mapping[str, object]
) -> bool:
    return dict(tool_input) == _cron_create_input(receipt, session_id)


def _request_cron(project_root: Path, session_id: str, event: str) -> str | None:
    receipt = cc.create_pending(project_root, session_id, cc.UI_RE_SKILL)
    if receipt.get("state") == cc.STATE_ACTIVE:
        return None
    return _emit_context(event, _create_context(receipt, session_id))


def _pre_skill(project_root: Path, session_id: str, tool_input: Mapping[str, object]) -> str | None:
    if _skill_name(tool_input) != cc.UI_RE_SKILL:
        return None
    return _request_cron(project_root, session_id, _EVENT_PRE)


def _userprompt_skill(
    project_root: Path, session_id: str, payload: Mapping[str, object]
) -> str | None:
    prompt = _user_prompt(payload)
    if prompt is None or not _is_ui_re_user_prompt(prompt):
        return None
    return _request_cron(project_root, session_id, _EVENT_USER_PROMPT)


def _pre_croncreate(
    project_root: Path, session_id: str, tool_input: Mapping[str, object]
) -> str | None:
    receipt = _receipt(project_root, session_id)
    if receipt is None or not _tagged(receipt, tool_input.get("prompt")):
        return None
    if _is_exact_croncreate(receipt, session_id, tool_input):
        return None
    return _emit_context(_EVENT_PRE, _correction_context(receipt, session_id))


def _pre_crondelete(
    project_root: Path, session_id: str, tool_input: Mapping[str, object]
) -> str | None:
    receipt = _receipt(project_root, session_id)
    if receipt is None or tool_input.get("id") != receipt.get("cronId"):
        return None
    if receipt.get("state") == cc.STATE_PAUSED:
        return _emit_context(_EVENT_PRE, _paused_delete_context())
    return None


def _post_croncreate(
    project_root: Path,
    session_id: str,
    tool_input: Mapping[str, object],
    tool_response: object,
) -> str | None:
    receipt = _receipt(project_root, session_id)
    if receipt is None:
        return None
    if not _tagged(receipt, tool_input.get("prompt")):
        return None
    if not _is_exact_croncreate(receipt, session_id, tool_input):
        return _emit_context(_EVENT_POST, _correction_context(receipt, session_id))
    try:
        cron_id = cc.extract_created_cron_id(tool_response, str(receipt["leaseTag"]))
    except cc.ContinuationError as exc:
        return _emit_context(_EVENT_POST, _unsupported_context(str(exc)))
    if cron_id is None:
        return _emit_context(_EVENT_POST, _cronlist_context())
    cc.mark_active(project_root, session_id, cron_id)
    return None


def _post_cronlist(project_root: Path, session_id: str, tool_response: object) -> str | None:
    receipt = _receipt(project_root, session_id)
    if receipt is None:
        return None
    try:
        result = cc.reconcile_cron_snapshot(project_root, session_id, tool_response)
    except cc.ContinuationError as exc:
        return _emit_context(_EVENT_POST, _unsupported_context(str(exc)))
    if result.status == "active":
        return None
    if result.status == "missing":
        return None
    if result.status == "unavailable" and receipt.get("state") == cc.STATE_PENDING:
        return _emit_context(
            _EVENT_POST,
            _unsupported_context(result.reason or "cron list unavailable"),
        )
    if result.status == "unavailable":
        return None
    if result.status in {
        cc.STATE_PAUSED,
        cc.STATE_COMPLETE,
        cc.STATE_TERMINAL,
        cc.STATE_UNSUPPORTED,
    }:
        return None
    return _emit_context(_EVENT_POST, _unsupported_context(result.reason or result.status))


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
        detail = tool_response if isinstance(tool_response, dict) else {"response": tool_response}
        return _emit_context(
            _EVENT_POST,
            "CronDelete did not return the supported successful response; "
            f"receipt left unchanged: {_json_block(detail)}",
        )
    cc.owned_delete_outcome(project_root, session_id)
    return None


def _handle(payload: dict[str, object], project_root: Path) -> str | None:
    session_id = _require_str(payload, "session_id")
    _require_str(payload, "cwd")
    event = _require_str(payload, "hook_event_name")
    root = Path(project_root).resolve()

    if event == _EVENT_USER_PROMPT:
        return _userprompt_skill(root, session_id, payload)

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
