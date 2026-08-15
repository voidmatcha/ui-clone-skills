import json
import stat
import subprocess
import sys
import types
from pathlib import Path
from typing import cast

import pytest

from ui_clone import claude_continuation as cc

SESSION = "fe87fc97-d23a-496e-b13a-5ca5ab651f0d"
CRON_ID = "cron-001_OK"


def read_raw(project: Path, session: str = SESSION) -> dict[str, object]:
    return cast(dict[str, object], json.loads(cc.receipt_path(project, session).read_text()))


def test_pending_receipt_is_atomic_private_idempotent_and_gitignored(tmp_path: Path) -> None:
    receipt = cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)

    path = cc.receipt_path(tmp_path, SESSION)
    assert path == tmp_path / cc.RECEIPT_DIR / f"{SESSION}.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert receipt["state"] == cc.STATE_PENDING
    assert receipt["leaseTag"].startswith("UI_RE_CONTINUATION:")
    assert cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL) == receipt
    assert cc.load_receipt(tmp_path, SESSION) == receipt
    assert not list((tmp_path / cc.RECEIPT_DIR).glob("*.tmp"))

    gitignore = Path.cwd() / ".gitignore"
    assert f"{cc.RECEIPT_DIR}/" in gitignore.read_text()


def test_now_uses_timezone_utc_for_python39_compatibility(monkeypatch: pytest.MonkeyPatch) -> None:
    real_dt = cc.dt

    class DateTimeWithoutModuleUTC:
        @classmethod
        def now(cls, tz: object) -> object:
            assert tz is fake_dt.timezone.utc
            return real_dt.datetime.now(real_dt.timezone.utc)

    fake_dt = types.SimpleNamespace(
        datetime=DateTimeWithoutModuleUTC,
        timezone=types.SimpleNamespace(utc=object()),
    )
    monkeypatch.setattr(cc, "dt", fake_dt)

    assert cc._now().endswith("Z")


@pytest.mark.parametrize(
    "token",
    ["../escape", "a/b", "", ".", "..", "bad space", "semi;colon", "한글"],
)
def test_receipt_path_rejects_unsafe_session_tokens(tmp_path: Path, token: str) -> None:
    with pytest.raises(cc.ContinuationError):
        cc.receipt_path(tmp_path, token)


def test_load_rejects_invalid_or_mismatched_receipts_without_clobbering(tmp_path: Path) -> None:
    path = cc.receipt_path(tmp_path, SESSION)
    path.parent.mkdir()
    path.write_text("{not json")
    assert cc.load_receipt(tmp_path, SESSION) is None
    assert path.read_text() == "{not json"

    other = cc.create_pending(tmp_path, "other-session", cc.UI_RE_SKILL)
    path.write_text(json.dumps({**other, "sessionId": "wrong"}))
    assert cc.load_receipt(tmp_path, SESSION) is None
    assert read_raw(tmp_path)["sessionId"] == "wrong"


def test_create_pending_rejects_invalid_existing_receipt_without_clobbering(tmp_path: Path) -> None:
    path = cc.receipt_path(tmp_path, SESSION)
    path.parent.mkdir()
    path.write_text("{not json")

    with pytest.raises(cc.ContinuationError):
        cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)

    assert path.read_text() == "{not json"


@pytest.mark.parametrize(
    ("competing_state", "updates"),
    [
        (cc.STATE_ACTIVE, {"cronId": CRON_ID}),
        (cc.STATE_COMPLETE, {"outcome": "done"}),
        (cc.STATE_TERMINAL, {"terminalState": {"status": "failed", "category": "canonical"}}),
    ],
)
def test_create_pending_stale_missing_writer_cannot_overwrite_existing_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    competing_state: str,
    updates: dict[str, object],
) -> None:
    real_write_unlocked = cc._write_receipt_unlocked

    def racing_write_unlocked(
        project: Path, session_id: str, receipt: dict[str, object]
    ) -> dict[str, object]:
        if session_id == SESSION and receipt.get("state") == cc.STATE_PENDING:
            competing = cc._new_receipt(project, session_id)
            competing.update(updates)
            competing["state"] = competing_state
            competing["updatedAt"] = cc._now()
            real_write_unlocked(project, session_id, competing)
        return real_write_unlocked(project, session_id, receipt)

    monkeypatch.setattr(cc, "_write_receipt_unlocked", racing_write_unlocked)

    with pytest.raises(cc.ContinuationError, match="receipt changed concurrently"):
        cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)

    assert read_raw(tmp_path)["state"] == competing_state


def test_load_receipt_rejects_mismatched_well_formed_lease_tag_without_clobbering(
    tmp_path: Path,
) -> None:
    receipt = cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    path = cc.receipt_path(tmp_path, SESSION)
    tampered = {**receipt, "leaseTag": "UI_RE_CONTINUATION:wellformedbutwrong"}
    path.write_text(json.dumps(tampered))

    with pytest.raises(cc.ContinuationError):
        cc.load_receipt(tmp_path, SESSION)

    assert json.loads(path.read_text())["leaseTag"] == "UI_RE_CONTINUATION:wellformedbutwrong"


def test_state_transitions_are_narrow_and_immutable(tmp_path: Path) -> None:
    pending = cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    active = cc.mark_active(tmp_path, SESSION, CRON_ID)
    assert active["state"] == cc.STATE_ACTIVE
    assert active["cronId"] == CRON_ID

    ref = tmp_path / "tmp" / "ref" / "demo"
    bound = cc.bind_ref(tmp_path, SESSION, ref)
    assert bound["refDir"] == "tmp/ref/demo"
    assert cc.bind_ref(tmp_path, SESSION, ref) == bound
    with pytest.raises(cc.ContinuationError):
        cc.bind_ref(tmp_path, SESSION, tmp_path / "other")

    paused = cc.pause(tmp_path, SESSION)
    assert paused["state"] == cc.STATE_PAUSED
    with pytest.raises(cc.ContinuationError):
        cc.mark_active(tmp_path, SESSION, "cron-new")
    assert read_raw(tmp_path)["state"] == cc.STATE_PAUSED

    refreshed = cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    assert refreshed["state"] == cc.STATE_PENDING
    assert refreshed["leaseTag"] == pending["leaseTag"]
    unsupported = cc.mark_unsupported(tmp_path, SESSION, "CronCreate unavailable")
    assert unsupported["state"] == cc.STATE_UNSUPPORTED
    assert unsupported["reason"] == "CronCreate unavailable"
    with pytest.raises(cc.ContinuationError):
        cc.pause(tmp_path, SESSION)


def test_stale_transition_cannot_overwrite_terminal_receipt(tmp_path: Path) -> None:
    cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    active = cc.mark_active(tmp_path, SESSION, CRON_ID)
    terminal = cc._replace_state(
        tmp_path,
        SESSION,
        active,
        cc.STATE_TERMINAL,
        terminalState={"status": "failed", "category": "canonical"},
    )

    with pytest.raises(cc.ContinuationError):
        cc._replace_state(tmp_path, SESSION, active, cc.STATE_PAUSED)

    assert read_raw(tmp_path)["state"] == cc.STATE_TERMINAL
    assert read_raw(tmp_path)["updatedAt"] == terminal["updatedAt"]


def test_mark_unsupported_requires_pending_receipt(tmp_path: Path) -> None:
    with pytest.raises(cc.ContinuationError):
        cc.mark_unsupported(tmp_path, SESSION, "missing cron")

    cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    cc.mark_active(tmp_path, SESSION, CRON_ID)
    with pytest.raises(cc.ContinuationError):
        cc.mark_unsupported(tmp_path, SESSION, "too late")
    assert read_raw(tmp_path)["state"] == cc.STATE_ACTIVE


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"id": CRON_ID}, CRON_ID),
        ({"cron": {"id": CRON_ID}}, CRON_ID),
        ([{"id": CRON_ID, "schedule": "* * * * *", "recurring": True, "prompt": "x [[UI_RE_CONTINUATION:abc]]"}], CRON_ID),
        ({"crons": [{"id": CRON_ID, "schedule": "* * * * *", "recurring": True, "prompt": "x [[UI_RE_CONTINUATION:abc]]"}]}, CRON_ID),
        ({"text": f"created {CRON_ID}"}, None),
        ({"id": "../bad"}, None),
    ],
)
def test_extract_created_cron_id_accepts_only_structured_valid_shapes(
    response: object, expected: str | None
) -> None:
    assert cc.extract_created_cron_id(response, "UI_RE_CONTINUATION:abc") == expected


def test_reconcile_cron_snapshot_distinguishes_unavailable_absent_match_and_duplicates(
    tmp_path: Path,
) -> None:
    receipt = cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    tag = receipt["leaseTag"]

    unavailable = cc.reconcile_cron_snapshot(tmp_path, SESSION, {"session_crons": "nope"})
    assert unavailable.status == "unavailable"
    assert read_raw(tmp_path)["state"] == cc.STATE_PENDING

    malformed = cc.reconcile_cron_snapshot(tmp_path, SESSION, {"session_crons": [{"id": CRON_ID}]})
    assert malformed.status == "unavailable"

    absent_pending = cc.reconcile_cron_snapshot(tmp_path, SESSION, {"session_crons": []})
    assert absent_pending.status == "absent"
    assert read_raw(tmp_path)["state"] == cc.STATE_PENDING

    active = cc.reconcile_cron_snapshot(
        tmp_path,
        SESSION,
        {"session_crons": [{"id": CRON_ID, "schedule": "* * * * *", "recurring": True, "prompt": f"go [[{tag}]]"}]},
    )
    assert active.status == "active"
    assert active.cron_id == CRON_ID
    assert read_raw(tmp_path)["state"] == cc.STATE_ACTIVE

    with pytest.raises(cc.ContinuationError):
        cc.reconcile_cron_snapshot(
            tmp_path,
            SESSION,
            {"session_crons": [
                {"id": CRON_ID, "schedule": "* * * * *", "recurring": True, "prompt": f"go [[{tag}]]"},
                {"id": "cron-002", "schedule": "* * * * *", "recurring": True, "prompt": f"go [[{tag}]]"},
            ]},
        )

    paused = cc.reconcile_cron_snapshot(tmp_path, SESSION, {"session_crons": []})
    assert paused.status == "paused"
    assert read_raw(tmp_path)["state"] == cc.STATE_PAUSED
    stale = cc.reconcile_cron_snapshot(
        tmp_path,
        SESSION,
        {"session_crons": [{"id": CRON_ID, "schedule": "* * * * *", "recurring": True, "prompt": f"go [[{tag}]]"}]},
    )
    assert stale.status == "paused"
    assert read_raw(tmp_path)["state"] == cc.STATE_PAUSED


def test_continuation_prompt_has_stable_tag_and_no_user_text_or_forbidden_surfaces(tmp_path: Path) -> None:
    receipt = cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    cc.mark_active(tmp_path, SESSION, CRON_ID)
    bound = cc.bind_ref(tmp_path, SESSION, tmp_path / "tmp" / "ref" / "demo")
    prompt = cc.continuation_prompt({**bound, "sourceUrl": "https://example.test/secret prompt"})
    status_command = (
        "python -m ui_clone.claude_continuation status "
        f"--session-id {SESSION} --cwd . --json"
    )

    assert f"[[{receipt['leaseTag']}]]" in prompt
    assert "https://example.test" not in prompt
    assert "secret prompt" not in prompt
    assert status_command in prompt
    assert prompt.index(status_command) < prompt.index("goal --check-done")
    assert "If receipt state is paused, complete, terminal, or unsupported" in prompt
    assert "delete the owned scheduled task" in prompt
    assert "perform no pipeline work" in prompt
    assert "Only if the receipt state is active" in prompt
    assert "goal --check-done" in prompt
    assert "status --json" in prompt
    assert "next --json" in prompt
    assert "report --for-llm" in prompt
    assert "-p" not in prompt
    assert "--resume" not in prompt
    assert "Purplemux" not in prompt
    assert "cmux" not in prompt


def test_continuation_prompt_requires_active_bound_receipt(tmp_path: Path) -> None:
    receipt = cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    with pytest.raises(cc.ContinuationError):
        cc.continuation_prompt(receipt)
    active = cc.mark_active(tmp_path, SESSION, CRON_ID)
    with pytest.raises(cc.ContinuationError):
        cc.continuation_prompt(active)


def test_paused_continuation_prompt_self_deletes_without_pipeline_work(tmp_path: Path) -> None:
    cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    cc.mark_active(tmp_path, SESSION, CRON_ID)
    paused = cc.pause(tmp_path, SESSION)

    prompt = cc.continuation_prompt(paused)

    assert "paused" in prompt
    assert "Delete this scheduled task" in prompt
    assert "pipeline work" in prompt
    assert "goal --check-done" not in prompt


def test_owned_delete_outcome_uses_goal_before_terminal_or_pause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = tmp_path / "tmp" / "ref" / "demo"
    ref.mkdir(parents=True)
    cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    cc.mark_active(tmp_path, SESSION, CRON_ID)
    cc.bind_ref(tmp_path, SESSION, ref)

    calls: list[list[str]] = []

    def goal_done(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="DONE verify-stamp.json", stderr="")

    monkeypatch.setattr(cc.subprocess, "run", goal_done)
    complete = cc.owned_delete_outcome(tmp_path, SESSION)
    assert complete["state"] == cc.STATE_COMPLETE
    assert calls and calls[0][-1] == "--check-done"

    cc.create_pending(tmp_path, "terminal-session", cc.UI_RE_SKILL)
    cc.mark_active(tmp_path, "terminal-session", CRON_ID)
    cc.bind_ref(tmp_path, "terminal-session", ref)
    (ref / "pipeline-state.json").write_text(
        json.dumps({"component": "demo", "terminalState": {"status": "failed", "category": "x"}})
    )

    def goal_incomplete(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 2, stdout="INCOMPLETE", stderr="")

    monkeypatch.setattr(cc.subprocess, "run", goal_incomplete)
    terminal = cc.owned_delete_outcome(tmp_path, "terminal-session")
    assert terminal["state"] == cc.STATE_TERMINAL

    cc.create_pending(tmp_path, "pause-session", cc.UI_RE_SKILL)
    cc.mark_active(tmp_path, "pause-session", CRON_ID)
    cc.bind_ref(tmp_path, "pause-session", ref)
    (ref / "pipeline-state.json").write_text(json.dumps({"component": "demo"}))
    monkeypatch.setattr(
        cc.subprocess,
        "run",
        lambda cmd, **_: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="INCOMPLETE"),
    )
    paused = cc.owned_delete_outcome(tmp_path, "pause-session")
    assert paused["state"] == cc.STATE_PAUSED

    cc.create_pending(tmp_path, "abort-session", cc.UI_RE_SKILL)
    cc.mark_active(tmp_path, "abort-session", CRON_ID)
    cc.bind_ref(tmp_path, "abort-session", ref)

    def goal_abort(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="ABORT: terminal")

    monkeypatch.setattr(cc.subprocess, "run", goal_abort)
    abort = cc.owned_delete_outcome(tmp_path, "abort-session")
    assert abort["state"] == cc.STATE_TERMINAL
    assert abort["terminalState"]["category"] == "goal-abort"


def test_owned_delete_outcome_pauses_on_goal_rc1_with_legacy_canonical_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = tmp_path / "tmp" / "ref" / "demo"
    ref.mkdir(parents=True)
    cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    cc.mark_active(tmp_path, SESSION, CRON_ID)
    cc.bind_ref(tmp_path, SESSION, ref)
    (ref / "pipeline-state.json").write_text(
        json.dumps(
            {
                "component": "demo",
                "terminalState": {
                    "status": "failed",
                    "category": "canonical-verify-failed",
                    "reason": "verify failed",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cc.subprocess,
        "run",
        lambda cmd, **_: subprocess.CompletedProcess(cmd, 1, stdout="INCOMPLETE", stderr=""),
    )

    paused = cc.owned_delete_outcome(tmp_path, SESSION)

    assert paused["state"] == cc.STATE_PAUSED
    assert "terminalState" not in paused


def test_owned_delete_outcome_goal_rc2_with_unclonable_stays_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = tmp_path / "tmp" / "ref" / "demo"
    ref.mkdir(parents=True)
    cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    cc.mark_active(tmp_path, SESSION, CRON_ID)
    cc.bind_ref(tmp_path, SESSION, ref)
    (ref / "pipeline-state.json").write_text(
        json.dumps(
            {
                "component": "demo",
                "terminalState": {
                    "status": "unclonable",
                    "category": "hard-cap-fail",
                    "reason": "hard cap reached",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cc.subprocess,
        "run",
        lambda cmd, **_: subprocess.CompletedProcess(cmd, 2, stdout="", stderr="ABORT"),
    )

    terminal = cc.owned_delete_outcome(tmp_path, SESSION)

    assert terminal["state"] == cc.STATE_TERMINAL
    assert terminal["terminalState"]["status"] == "unclonable"
    assert terminal["terminalState"]["category"] == "hard-cap-fail"


def test_owned_delete_outcome_preserves_receipt_when_terminal_state_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = tmp_path / "tmp" / "ref" / "demo"
    ref.mkdir(parents=True)
    cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    cc.mark_active(tmp_path, SESSION, CRON_ID)
    before = cc.bind_ref(tmp_path, SESSION, ref)

    monkeypatch.setattr(
        cc.subprocess,
        "run",
        lambda cmd, **_: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="INCOMPLETE"),
    )
    monkeypatch.setattr(
        cc.PipelineState,
        "load",
        classmethod(lambda cls, ref_dir: (_ for _ in ()).throw(OSError("state unreadable"))),
    )

    with pytest.raises(cc.ContinuationError, match="state unreadable"):
        cc.owned_delete_outcome(tmp_path, SESSION)

    assert read_raw(tmp_path) == before


def test_owned_delete_outcome_preserves_receipt_when_pipeline_state_load_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = tmp_path / "tmp" / "ref" / "demo"
    ref.mkdir(parents=True)
    cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    cc.mark_active(tmp_path, SESSION, CRON_ID)
    before = cc.bind_ref(tmp_path, SESSION, ref)
    (ref / "pipeline-state.json").write_text("{not json", encoding="utf-8")

    monkeypatch.setattr(
        cc.subprocess,
        "run",
        lambda cmd, **_: subprocess.CompletedProcess(
            cmd,
            1,
            stdout="INCOMPLETE",
            stderr="pipeline-state.json is corrupt and quarantined",
        ),
    )

    with pytest.raises(cc.ContinuationError, match="pipeline state load failed"):
        cc.owned_delete_outcome(tmp_path, SESSION)

    assert read_raw(tmp_path) == before


def test_owned_delete_outcome_preserves_receipt_when_goal_classification_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = tmp_path / "tmp" / "ref" / "demo"
    ref.mkdir(parents=True)
    cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    cc.mark_active(tmp_path, SESSION, CRON_ID)
    before = cc.bind_ref(tmp_path, SESSION, ref)

    def fail_goal(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise OSError("goal unavailable")

    monkeypatch.setattr(cc.subprocess, "run", fail_goal)

    with pytest.raises(cc.ContinuationError, match="goal unavailable"):
        cc.owned_delete_outcome(tmp_path, SESSION)

    assert read_raw(tmp_path) == before


def test_owned_delete_outcome_preserves_receipt_on_unexpected_goal_return_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = tmp_path / "tmp" / "ref" / "demo"
    ref.mkdir(parents=True)
    cc.create_pending(tmp_path, SESSION, cc.UI_RE_SKILL)
    cc.mark_active(tmp_path, SESSION, CRON_ID)
    before = cc.bind_ref(tmp_path, SESSION, ref)

    monkeypatch.setattr(
        cc.subprocess,
        "run",
        lambda cmd, **_: subprocess.CompletedProcess(cmd, 99, stdout="", stderr="unknown"),
    )

    with pytest.raises(cc.ContinuationError, match="unexpected goal --check-done return code"):
        cc.owned_delete_outcome(tmp_path, SESSION)

    assert read_raw(tmp_path) == before


def test_cli_verbs_status_and_validation_errors(tmp_path: Path) -> None:
    module = [sys.executable, "-m", "ui_clone.claude_continuation"]
    create = subprocess.run(
        module + [
            "create-pending",
            "--session-id",
            SESSION,
            "--cwd",
            str(tmp_path),
            "--skill",
            cc.UI_RE_SKILL,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert create.returncode == 0, create.stderr

    status = subprocess.run(
        module + ["status", "--session-id", SESSION, "--cwd", str(tmp_path), "--json"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert status.returncode == 0
    assert json.loads(status.stdout)["state"] == cc.STATE_PENDING

    bind = subprocess.run(
        module + ["bind-ref", "--session-id", SESSION, "--cwd", str(tmp_path), "--ref-dir", str(tmp_path / "ref")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert bind.returncode == 0

    invalid = subprocess.run(
        module + ["pause", "--session-id", "../bad", "--cwd", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert invalid.returncode == 2
