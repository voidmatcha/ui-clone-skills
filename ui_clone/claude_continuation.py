from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ui_clone.state import PipelineState, is_authoritative_terminal_state

UI_RE_SKILL = "ui-clone-skills:ui-reverse-engineering"
RECEIPT_DIR = ".ui-re-continuation"

STATE_PENDING = "pending"
STATE_ACTIVE = "active"
STATE_PAUSED = "paused"
STATE_COMPLETE = "complete"
STATE_TERMINAL = "terminal"
STATE_UNSUPPORTED = "unsupported"
STATES = {
    STATE_PENDING,
    STATE_ACTIVE,
    STATE_PAUSED,
    STATE_COMPLETE,
    STATE_TERMINAL,
    STATE_UNSUPPORTED,
}

_SCHEMA_VERSION = 1
_HOST = "claude-code"
_SCHEDULE = "* * * * *"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_FINAL_STATES = {STATE_COMPLETE, STATE_TERMINAL, STATE_UNSUPPORTED}


class ContinuationError(ValueError):
    """Raised when continuation receipt input or state is invalid."""


@dataclasses.dataclass(frozen=True)
class ReconcileResult:
    status: str
    receipt: dict[str, Any] | None = None
    cron_id: str | None = None
    reason: str | None = None


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(  # noqa: UP017 - Python 3.9 lacks datetime.UTC.
        microsecond=0
    ).isoformat().replace("+00:00", "Z")


def _validate_token(token: str, *, label: str) -> str:
    if not isinstance(token, str) or not token or token in {".", ".."}:
        raise ContinuationError(f"invalid {label}")
    if not (_UUID_RE.fullmatch(token) or _TOKEN_RE.fullmatch(token)):
        raise ContinuationError(f"invalid {label}")
    return token


def _validate_state(state: object) -> str:
    if not isinstance(state, str) or state not in STATES:
        raise ContinuationError("invalid receipt state")
    return state


def _validate_cron_id(cron_id: object) -> str | None:
    if not isinstance(cron_id, str):
        return None
    try:
        return _validate_token(cron_id, label="cron id")
    except ContinuationError:
        return None


def _project_root(project: Path) -> Path:
    return Path(project).resolve()


def receipt_path(project: Path, session_id: str) -> Path:
    session = _validate_token(session_id, label="session id")
    return _project_root(project) / RECEIPT_DIR / f"{session}.json"


def _lock_path(project: Path, session_id: str) -> Path:
    session = _validate_token(session_id, label="session id")
    return _project_root(project) / RECEIPT_DIR / f"{session}.lock"


def _lease_tag(project: Path, session_id: str) -> str:
    digest = hashlib.sha256(
        f"{_project_root(project)}\0{_validate_token(session_id, label='session id')}".encode()
    ).hexdigest()[:24]
    return f"UI_RE_CONTINUATION:{digest}"


def _validate_receipt(
    raw: object, session_id: str, project: Path | None = None
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("schemaVersion") != _SCHEMA_VERSION:
        return None
    if raw.get("host") != _HOST or raw.get("skill") != UI_RE_SKILL:
        return None
    if raw.get("sessionId") != session_id:
        return None
    try:
        _validate_state(raw.get("state"))
        _validate_token(str(raw.get("sessionId")), label="session id")
        tag = raw.get("leaseTag")
        if not isinstance(tag, str) or not tag.startswith("UI_RE_CONTINUATION:"):
            return None
        cron_id = raw.get("cronId")
        if cron_id is not None and _validate_cron_id(cron_id) is None:
            return None
        ref_dir = raw.get("refDir")
        if ref_dir is not None:
            _safe_ref_dir(ref_dir)
    except ContinuationError:
        return None
    if project is not None and tag != _lease_tag(project, session_id):
        raise ContinuationError("receipt lease tag does not match project and session")
    return dict(raw)


def _read_receipt_unlocked(project: Path, session_id: str) -> dict[str, Any] | None:
    path = receipt_path(project, session_id)
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return _validate_receipt(raw, session_id, project)


@contextmanager
def _locked_session(project: Path, session_id: str) -> Iterator[None]:
    path = receipt_path(project, session_id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = _lock_path(project, session_id)
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def load_receipt(project: Path, session_id: str) -> dict[str, Any] | None:
    with _locked_session(project, session_id):
        return _read_receipt_unlocked(project, session_id)


def _write_receipt_unlocked(
    project: Path, session_id: str, receipt: Mapping[str, Any]
) -> dict[str, Any]:
    path = receipt_path(project, session_id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    current = _read_receipt_unlocked(project, session_id)
    if (
        current is not None
        and receipt.get("state") == STATE_PENDING
        and current.get("state") in {STATE_ACTIVE, STATE_COMPLETE, STATE_TERMINAL}
    ):
        raise ContinuationError("receipt changed concurrently")
    payload = json.dumps(dict(receipt), sort_keys=True, indent=2) + "\n"
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=path.parent,
            prefix=f".{path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_name = tmp.name
            os.chmod(tmp_name, 0o600)
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
    return dict(receipt)


def _write_receipt(project: Path, session_id: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
    with _locked_session(project, session_id):
        return _write_receipt_unlocked(project, session_id, receipt)


def _ensure_current_unmodified(
    project: Path, session_id: str, expected: Mapping[str, Any]
) -> None:
    current = _read_receipt_unlocked(project, session_id)
    if current is None:
        raise ContinuationError("missing receipt")
    for key in ("state", "updatedAt", "leaseTag", "cronId", "refDir"):
        if current.get(key) != expected.get(key):
            raise ContinuationError("receipt changed concurrently")


def _new_receipt(project: Path, session_id: str) -> dict[str, Any]:
    now = _now()
    return {
        "schemaVersion": _SCHEMA_VERSION,
        "host": _HOST,
        "sessionId": session_id,
        "skill": UI_RE_SKILL,
        "state": STATE_PENDING,
        "leaseTag": _lease_tag(project, session_id),
        "createdAt": now,
        "updatedAt": now,
    }


def _replace_state(
    project: Path,
    session_id: str,
    receipt: Mapping[str, Any],
    state: str,
    **updates: Any,
) -> dict[str, Any]:
    _validate_state(state)
    next_receipt = dict(receipt)
    next_receipt.update(updates)
    next_receipt["state"] = state
    next_receipt["updatedAt"] = _now()
    validated = _validate_receipt(next_receipt, session_id, project)
    if validated is None:
        raise ContinuationError("invalid receipt update")
    with _locked_session(project, session_id):
        _ensure_current_unmodified(project, session_id, receipt)
        return _write_receipt_unlocked(project, session_id, validated)


def create_pending(project: Path, session_id: str, skill: str) -> dict[str, Any]:
    if skill != UI_RE_SKILL:
        raise ContinuationError("unsupported skill")
    _validate_token(session_id, label="session id")
    with _locked_session(project, session_id):
        current = _read_receipt_unlocked(project, session_id)
        if current is None:
            if receipt_path(project, session_id).exists():
                raise ContinuationError("invalid existing receipt")
            return _write_receipt_unlocked(project, session_id, _new_receipt(project, session_id))
    state = _validate_state(current.get("state"))
    if state in {STATE_PENDING, STATE_ACTIVE}:
        return current
    if state == STATE_PAUSED:
        return _replace_state(project, session_id, current, STATE_PENDING, cronId=None)
    raise ContinuationError(f"cannot create pending receipt from {state}")


def _safe_ref_dir(ref_dir: object) -> str:
    if not isinstance(ref_dir, str) or not ref_dir:
        raise ContinuationError("invalid ref dir")
    path = Path(ref_dir)
    if path.is_absolute() or ".." in path.parts:
        raise ContinuationError("invalid ref dir")
    return path.as_posix()


def _relative_ref(project: Path, ref_dir: Path) -> str:
    project_root = _project_root(project)
    resolved = Path(ref_dir).resolve()
    try:
        relative = resolved.relative_to(project_root)
    except ValueError as exc:
        raise ContinuationError("ref dir must be inside project") from exc
    return _safe_ref_dir(relative.as_posix())


def bind_ref(project: Path, session_id: str, ref_dir: Path) -> dict[str, Any]:
    current = load_receipt(project, session_id)
    if current is None:
        raise ContinuationError("missing receipt")
    state = _validate_state(current.get("state"))
    if state in _FINAL_STATES:
        raise ContinuationError(f"cannot bind ref for {state} receipt")
    ref = _relative_ref(project, ref_dir)
    existing = current.get("refDir")
    if existing is not None and existing != ref:
        raise ContinuationError("receipt already bound to a different ref")
    if existing == ref:
        return current
    return _replace_state(project, session_id, current, state, refDir=ref)


def mark_active(project: Path, session_id: str, cron_id: str) -> dict[str, Any]:
    cron = _validate_cron_id(cron_id)
    if cron is None:
        raise ContinuationError("invalid cron id")
    current = load_receipt(project, session_id)
    if current is None:
        raise ContinuationError("missing receipt")
    state = _validate_state(current.get("state"))
    if state == STATE_PENDING:
        return _replace_state(project, session_id, current, STATE_ACTIVE, cronId=cron)
    if state == STATE_ACTIVE and current.get("cronId") == cron:
        return current
    raise ContinuationError(f"cannot activate {state} receipt")


def mark_unsupported(project: Path, session_id: str, reason: str) -> dict[str, Any]:
    if not isinstance(reason, str) or not reason.strip():
        raise ContinuationError("unsupported reason is required")
    current = load_receipt(project, session_id)
    if current is None:
        raise ContinuationError("missing receipt")
    if current.get("state") != STATE_PENDING:
        raise ContinuationError("only pending receipts can be unsupported")
    return _replace_state(project, session_id, current, STATE_UNSUPPORTED, reason=reason.strip())


def pause(project: Path, session_id: str) -> dict[str, Any]:
    current = load_receipt(project, session_id)
    if current is None:
        raise ContinuationError("missing receipt")
    state = _validate_state(current.get("state"))
    if state in {STATE_PENDING, STATE_ACTIVE}:
        return _replace_state(project, session_id, current, STATE_PAUSED)
    if state == STATE_PAUSED:
        return current
    raise ContinuationError(f"cannot pause {state} receipt")


def _row_match(row: Mapping[str, Any], lease_tag: str, cron_id: str | None) -> str | None:
    row_id = _validate_cron_id(row.get("id"))
    if row_id is None:
        return None
    if row.get("schedule") != _SCHEDULE or row.get("recurring") is not True:
        return None
    prompt = row.get("prompt")
    if not isinstance(prompt, str):
        return None
    if cron_id is not None and row_id == cron_id:
        return row_id
    if f"[[{lease_tag}]]" in prompt:
        return row_id
    return None


def _valid_rows(rows: list[object]) -> list[Mapping[str, Any]]:
    valid: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if (
            _validate_cron_id(row.get("id")) is None
            or row.get("schedule") != _SCHEDULE
            or row.get("recurring") is not True
            or not isinstance(row.get("prompt"), str)
        ):
            continue
        valid.append(row)
    return valid


def _cron_rows(snapshot: object) -> tuple[str, list[object] | None]:
    if isinstance(snapshot, list):
        return "available", snapshot
    if isinstance(snapshot, Mapping):
        if isinstance(snapshot.get("session_crons"), list):
            return "available", list(snapshot["session_crons"])
        if "session_crons" in snapshot:
            return "unavailable", None
        if isinstance(snapshot.get("crons"), list):
            return "available", list(snapshot["crons"])
        if "crons" in snapshot:
            return "unavailable", None
    return "unavailable", None


def extract_created_cron_id(response: object, lease_tag: str) -> str | None:
    if isinstance(response, Mapping):
        direct = _validate_cron_id(response.get("id"))
        if direct is not None:
            return direct
        cron = response.get("cron")
        if isinstance(cron, Mapping):
            nested = _validate_cron_id(cron.get("id"))
            if nested is not None:
                return nested
        rows_obj: object | None = response.get("crons")
    else:
        rows_obj = response
    if not isinstance(rows_obj, list):
        return None
    valid = _valid_rows(rows_obj)
    matches = [_row_match(row, lease_tag, None) for row in valid]
    cron_ids = [cron_id for cron_id in matches if cron_id is not None]
    if len(cron_ids) > 1:
        raise ContinuationError("duplicate continuation cron rows")
    return cron_ids[0] if cron_ids else None


def reconcile_cron_snapshot(project: Path, session_id: str, snapshot: object) -> ReconcileResult:
    current = load_receipt(project, session_id)
    if current is None:
        return ReconcileResult("missing")
    state = _validate_state(current.get("state"))
    availability, rows = _cron_rows(snapshot)
    if availability == "unavailable" or rows is None:
        return ReconcileResult("unavailable", current)
    valid = _valid_rows(rows)
    if rows and not valid:
        return ReconcileResult("unavailable", current, reason="all cron rows malformed")
    tag = str(current["leaseTag"])
    cron_id = current.get("cronId") if isinstance(current.get("cronId"), str) else None
    matches = [_row_match(row, tag, cron_id) for row in valid]
    cron_ids = [match for match in matches if match is not None]
    if len(cron_ids) > 1:
        raise ContinuationError("duplicate continuation crons")
    if cron_ids:
        if state == STATE_PENDING:
            receipt = mark_active(project, session_id, cron_ids[0])
            return ReconcileResult("active", receipt, cron_ids[0])
        if state == STATE_ACTIVE:
            return ReconcileResult("active", current, cron_ids[0])
        if state == STATE_PAUSED:
            return ReconcileResult("paused", current, cron_ids[0])
        return ReconcileResult(state, current)
    if state == STATE_ACTIVE:
        receipt = pause(project, session_id)
        return ReconcileResult("paused", receipt)
    return ReconcileResult("absent", current)


def _require_single_ref(receipt: Mapping[str, Any]) -> str:
    ref = receipt.get("refDir")
    if ref is None:
        raise ContinuationError("receipt has no bound ref")
    return _safe_ref_dir(ref)


def continuation_prompt(receipt: Mapping[str, Any]) -> str:
    state = _validate_state(receipt.get("state"))
    tag = receipt.get("leaseTag")
    session_id = receipt.get("sessionId")
    cron_id = receipt.get("cronId")
    if not isinstance(tag, str) or not tag.startswith("UI_RE_CONTINUATION:"):
        raise ContinuationError("invalid lease tag")
    if not isinstance(session_id, str):
        raise ContinuationError("invalid session id")
    _validate_token(session_id, label="session id")
    if cron_id is not None and _validate_cron_id(cron_id) is None:
        raise ContinuationError("invalid cron id")
    if state == STATE_PAUSED:
        return (
            f"[[{tag}]]\n"
            "This continuation receipt is paused. Delete this scheduled task if it is still present, "
            "then stop without running pipeline work."
        )
    if state != STATE_ACTIVE:
        raise ContinuationError("continuation prompt requires active receipt")
    ref = _require_single_ref(receipt)
    status_command = (
        "python -m ui_clone.claude_continuation status "
        f"--session-id {session_id} --cwd . --json"
    )
    return (
        f"[[{tag}]]\n"
        "Claude UI reverse-engineering continuation wake-up.\n"
        f"Session: {session_id}\n"
        f"Ref: {ref}\n"
        f"First run the continuation control check: {status_command}.\n"
        "If receipt state is paused, complete, terminal, or unsupported, "
        "delete the owned scheduled task and perform no pipeline work.\n"
        "Only if the receipt state is active, continue with the exact bound ref above.\n"
        "Then run the canonical goal --check-done command: python -m ui_clone.goal "
        f"{ref} --check-done.\n"
        "If complete, delete the owned scheduled task and report the verify-stamp evidence.\n"
        "If terminal or authority is required, delete the owned scheduled task and report the blocker.\n"
        "If incomplete, read these in order before acting: python -m ui_clone.pipeline "
        f"{ref} status --json; python -m ui_clone.pipeline {ref} next --json; "
        f"python -m ui_clone.pipeline {ref} report --for-llm.\n"
        "Execute the reported next required action while preserving normal gates."
    )


def _ref_path(project: Path, receipt: Mapping[str, Any]) -> Path:
    return _project_root(project) / _require_single_ref(receipt)


def _terminal_state(ref_dir: Path) -> dict[str, Any] | None:
    state = PipelineState.load(ref_dir)
    if state.load_failed:
        raise ContinuationError("pipeline state load failed")
    terminal = state.terminal_state
    return terminal if is_authoritative_terminal_state(terminal) else None


def _set_delete_state(project: Path, session_id: str, state: str, **updates: Any) -> dict[str, Any]:
    current = load_receipt(project, session_id)
    if current is None:
        raise ContinuationError("missing receipt")
    current_state = _validate_state(current.get("state"))
    if current_state not in {STATE_PENDING, STATE_ACTIVE}:
        raise ContinuationError(f"cannot mark delete outcome from {current_state}")
    return _replace_state(project, session_id, current, state, **updates)


def owned_delete_outcome(project: Path, session_id: str) -> dict[str, Any]:
    current = load_receipt(project, session_id)
    if current is None:
        raise ContinuationError("missing receipt")
    ref_dir = _ref_path(project, current)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ui_clone.goal", str(ref_dir), "--check-done"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise ContinuationError(f"goal --check-done failed: {exc}") from exc
    if result.returncode not in {0, 1, 2}:
        raise ContinuationError(
            f"unexpected goal --check-done return code: {result.returncode}"
        )
    if result.returncode == 0:
        return _set_delete_state(
            project,
            session_id,
            STATE_COMPLETE,
            outcome="canonical goal --check-done passed",
        )
    try:
        terminal = _terminal_state(ref_dir)
    except OSError as exc:
        raise ContinuationError(f"pipeline state read failed: {exc}") from exc
    if terminal:
        return _set_delete_state(project, session_id, STATE_TERMINAL, terminalState=terminal)
    if result.returncode == 2:
        return _set_delete_state(
            project,
            session_id,
            STATE_TERMINAL,
            terminalState={
                "status": "aborted",
                "category": "goal-abort",
                "reason": (result.stderr or result.stdout or "goal --check-done aborted").strip(),
            },
        )
    return _set_delete_state(
        project,
        session_id,
        STATE_PAUSED,
        goalReturnCode=result.returncode,
    )


def _print_json(payload: object) -> None:
    print(json.dumps(payload, sort_keys=True))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Claude UI-RE continuation receipt operations")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--session-id", required=True)
        p.add_argument("--cwd", required=True, type=Path)

    create = sub.add_parser("create-pending")
    add_common(create)
    create.add_argument("--skill", required=True)

    bind = sub.add_parser("bind-ref")
    add_common(bind)
    bind.add_argument("--ref-dir", required=True, type=Path)

    unsupported = sub.add_parser("mark-unsupported")
    add_common(unsupported)
    unsupported.add_argument("--reason", required=True)

    add_common(sub.add_parser("pause"))

    status = sub.add_parser("status")
    add_common(status)
    status.add_argument("--json", action="store_true", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "create-pending":
            receipt = create_pending(args.cwd, args.session_id, args.skill)
        elif args.command == "bind-ref":
            receipt = bind_ref(args.cwd, args.session_id, args.ref_dir)
        elif args.command == "mark-unsupported":
            receipt = mark_unsupported(args.cwd, args.session_id, args.reason)
        elif args.command == "pause":
            receipt = pause(args.cwd, args.session_id)
        elif args.command == "status":
            loaded = load_receipt(args.cwd, args.session_id)
            receipt = loaded if loaded is not None else {"state": None}
        else:
            parser.error("unknown command")
    except ContinuationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_json(receipt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
