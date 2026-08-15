# Claude Stop-Triggered Continuation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Claude's activation-time recurring continuation lease with a schema-v2, Stop-triggered, session-scoped one-shot that wakes exactly once while leaving Codex unchanged.

**Architecture:** The core module owns atomic receipt state and exact scheduler identity; the Claude hook adapter translates host events; the Stop hook is the only arming edge; and the Pre-Bash hook prevents pipeline ownership while a scheduler transition is unresolved. Canonical `ui_clone.goal` and `verify-stamp.json` remain the only completion authority.

**Tech Stack:** Python 3.11, pytest, Claude Code hook JSON, shell installation scripts, Purplemux as a display-only acceptance backend.

---

## File Structure

- `ui_clone/claude_continuation.py`: schema-v2 receipt validation, atomic state transitions, one-shot prompt/input construction, cron reconciliation, goal finalization, and operator CLI.
- `ui_clone/hooks/claude_continuation.py`: exact Claude `UserPromptSubmit`, `Skill`, `CronCreate`, `CronList`, and `CronDelete` event handling.
- `ui_clone/hooks/section_gate.py`: first incomplete Stop arming and final receipt reconciliation.
- `ui_clone/hooks/pre_bash_rules/dispatcher.py`: continuation-state ownership guard for UI-clone Bash commands.
- `tests/test_claude_continuation.py`: core state-machine and CLI contract tests.
- `tests/hooks/test_claude_continuation.py`: Claude event adapter tests.
- `tests/hooks/test_section_gate.py`: Stop-boundary arming and release tests.
- `tests/hooks/test_pre_bash.py`: Bash allow/block matrix tests.
- `tests/test_hook_manifest_parity.py`: Claude-only route and Codex non-regression ratchet.
- `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.codex-plugin/plugin.json`, `pyproject.toml`, `package.json`, `ui_clone/__init__.py`: synchronized cache-busting version bump from `0.7.31` to `0.7.32`.

Do not edit or stage the unrelated user-owned files `skills/visual-debug/scripts/lib/emit_scroll_helpers.py` and `tests/test_emit_scroll_helpers.py`.

### Task 1: Implement the schema-v2 one-shot core

**Files:**
- Modify: `ui_clone/claude_continuation.py`
- Test: `tests/test_claude_continuation.py`

- [ ] **Step 1: Replace the v1 state assertions with failing v2 transition tests**

Add helpers and tests that express the complete desired state machine:

```python
def arm_receipt(project: Path, session: str = SESSION) -> dict[str, object]:
    cc.activate(project, session, cc.UI_RE_SKILL)
    ref = project / "tmp" / "ref" / "demo"
    ref.mkdir(parents=True, exist_ok=True)
    cc.bind_ref(project, session, ref)
    return cast(dict[str, object], cc.arm(project, session))


def armed_receipt(project: Path, session: str = SESSION) -> dict[str, object]:
    arm_receipt(project, session)
    return cast(dict[str, object], cc.mark_armed(project, session, CRON_ID))


def test_activation_creates_private_schema_v2_running_receipt_without_cron(tmp_path: Path) -> None:
    receipt = cc.activate(tmp_path, SESSION, cc.UI_RE_SKILL)
    assert receipt["schemaVersion"] == 2
    assert receipt["state"] == cc.STATE_RUNNING
    assert "cronId" not in receipt
    assert cc.activate(tmp_path, SESSION, cc.UI_RE_SKILL) == receipt
    assert stat.S_IMODE(cc.receipt_path(tmp_path, SESSION).stat().st_mode) == 0o600


def test_one_shot_transitions_are_narrow_and_clear_cron_identity(tmp_path: Path) -> None:
    running = cc.activate(tmp_path, SESSION, cc.UI_RE_SKILL)
    ref = tmp_path / "tmp" / "ref" / "demo"
    ref.mkdir(parents=True)
    cc.bind_ref(tmp_path, SESSION, ref)
    arming = cc.arm(tmp_path, SESSION)
    assert arming["state"] == cc.STATE_ARMING
    armed = cc.mark_armed(tmp_path, SESSION, CRON_ID)
    assert armed["state"] == cc.STATE_ARMED
    assert armed["cronId"] == CRON_ID
    wake = cc.continuation_prompt(armed)
    resumed = cc.accept_wake(tmp_path, SESSION, wake)
    assert resumed["state"] == cc.STATE_RUNNING
    assert "cronId" not in resumed
    with pytest.raises(cc.ContinuationError):
        cc.mark_armed(tmp_path, SESSION, "cron-new")
    assert running["leaseTag"] == resumed["leaseTag"]


def test_manual_prompt_delete_returns_canceling_to_running(tmp_path: Path) -> None:
    armed = armed_receipt(tmp_path)
    canceling = cc.begin_manual_resume(tmp_path, SESSION)
    assert canceling["state"] == cc.STATE_CANCELING
    assert canceling["cronId"] == armed["cronId"]
    running = cc.finish_owned_delete(tmp_path, SESSION)
    assert running["state"] == cc.STATE_RUNNING
    assert "cronId" not in running


def test_owned_delete_from_armed_is_explicit_pause(tmp_path: Path) -> None:
    armed_receipt(tmp_path)
    paused = cc.finish_owned_delete(tmp_path, SESSION)
    assert paused["state"] == cc.STATE_PAUSED
    assert "cronId" not in paused


def test_schema_v1_receipt_is_not_reinterpreted(tmp_path: Path) -> None:
    path = cc.receipt_path(tmp_path, SESSION)
    path.parent.mkdir(mode=0o700)
    path.write_text(json.dumps({"schemaVersion": 1, "sessionId": SESSION}), encoding="utf-8")
    assert cc.load_receipt(tmp_path, SESSION) is None
    with pytest.raises(cc.ContinuationError, match="invalid existing receipt"):
        cc.activate(tmp_path, SESSION, cc.UI_RE_SKILL)
```

Replace recurring-row fixtures with `"recurring": False` and add:

```python
def one_shot_row(receipt: Mapping[str, object], cron_id: str = CRON_ID) -> dict[str, object]:
    return {
        "id": cron_id,
        "schedule": "* * * * *",
        "recurring": False,
        "prompt": cc.continuation_prompt(receipt),
    }


def test_one_shot_reconciliation_distinguishes_every_snapshot_class(tmp_path: Path) -> None:
    arming = arm_receipt(tmp_path)
    assert cc.reconcile_cron_snapshot(tmp_path, SESSION, {"session_crons": "bad"}).status == "unavailable"
    assert cc.reconcile_cron_snapshot(tmp_path, SESSION, {"session_crons": [{"id": CRON_ID}]}).status == "unavailable"
    assert cc.reconcile_cron_snapshot(tmp_path, SESSION, {"session_crons": []}).status == "absent"
    armed = cc.reconcile_cron_snapshot(
        tmp_path, SESSION, {"session_crons": [one_shot_row(arming)]}
    )
    assert armed.status == cc.STATE_ARMED
    with pytest.raises(cc.ContinuationError, match="duplicate"):
        cc.reconcile_cron_snapshot(tmp_path, SESSION, {"session_crons": [
            one_shot_row(arming, "cron-a"), one_shot_row(arming, "cron-b")
        ]})
    paused = cc.reconcile_cron_snapshot(tmp_path, SESSION, {"session_crons": []})
    assert paused.status == cc.STATE_PAUSED
    assert cc.reconcile_cron_snapshot(
        tmp_path, SESSION, {"session_crons": [one_shot_row(arming)]}
    ).status == cc.STATE_PAUSED


def test_running_empty_snapshot_is_normal_and_canceling_absence_pauses(tmp_path: Path) -> None:
    running = cc.activate(tmp_path, SESSION, cc.UI_RE_SKILL)
    assert cc.reconcile_cron_snapshot(tmp_path, SESSION, []).receipt == running
    ref = tmp_path / "tmp" / "ref" / "demo"
    ref.mkdir(parents=True)
    cc.bind_ref(tmp_path, SESSION, ref)
    cc.arm(tmp_path, SESSION)
    cc.mark_armed(tmp_path, SESSION, CRON_ID)
    cc.begin_manual_resume(tmp_path, SESSION)
    assert cc.reconcile_cron_snapshot(tmp_path, SESSION, []).status == cc.STATE_PAUSED
```

- [ ] **Step 2: Run the focused core tests and confirm RED**

Run:

```bash
uv run python -m pytest tests/test_claude_continuation.py -q
```

Expected: failures name missing `STATE_RUNNING`, `activate`, `arm`, `mark_armed`, `accept_wake`, `begin_manual_resume`, and `finish_owned_delete`, and existing recurring assertions fail.

- [ ] **Step 3: Implement the minimal schema-v2 state machine**

Replace the state constants and schema version with:

```python
STATE_RUNNING = "running"
STATE_ARMING = "arming"
STATE_ARMED = "armed"
STATE_CANCELING = "canceling"
STATE_PAUSED = "paused"
STATE_COMPLETE = "complete"
STATE_TERMINAL = "terminal"
STATE_UNSUPPORTED = "unsupported"
STATES = {
    STATE_RUNNING,
    STATE_ARMING,
    STATE_ARMED,
    STATE_CANCELING,
    STATE_PAUSED,
    STATE_COMPLETE,
    STATE_TERMINAL,
    STATE_UNSUPPORTED,
}
_SCHEMA_VERSION = 2
_FINAL_STATES = {STATE_COMPLETE, STATE_TERMINAL, STATE_UNSUPPORTED}
```

Enforce `cronId` as an invariant of only `armed` and `canceling`, make `_replace_state` remove update keys whose value is `None`, create new receipts in `running`, and add these transition functions:

```python
def activate(project: Path, session_id: str, skill: str) -> dict[str, Any]:
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
    if state == STATE_RUNNING:
        return current
    if state == STATE_PAUSED:
        return _replace_state(
            project, session_id, current, STATE_RUNNING, cronId=None, reason=None
        )
    raise ContinuationError(f"cannot activate {state} receipt")


def arm(project: Path, session_id: str) -> dict[str, Any]:
    current = load_receipt(project, session_id)
    if current is None:
        raise ContinuationError("missing receipt")
    state = _validate_state(current.get("state"))
    if state == STATE_RUNNING:
        _require_single_ref(current)
        return _replace_state(project, session_id, current, STATE_ARMING)
    if state == STATE_ARMING:
        return current
    raise ContinuationError(f"cannot arm {state} receipt")


def mark_armed(project: Path, session_id: str, cron_id: str) -> dict[str, Any]:
    cron = _validate_cron_id(cron_id)
    if cron is None:
        raise ContinuationError("invalid cron id")
    current = load_receipt(project, session_id)
    if current is None:
        raise ContinuationError("missing receipt")
    state = _validate_state(current.get("state"))
    if state == STATE_ARMING:
        return _replace_state(project, session_id, current, STATE_ARMED, cronId=cron)
    if state == STATE_ARMED and current.get("cronId") == cron:
        return current
    raise ContinuationError(f"cannot mark {state} receipt armed")


def begin_manual_resume(project: Path, session_id: str) -> dict[str, Any]:
    current = load_receipt(project, session_id)
    if current is None:
        raise ContinuationError("missing receipt")
    state = _validate_state(current.get("state"))
    if state == STATE_ARMED:
        return _replace_state(project, session_id, current, STATE_CANCELING)
    if state == STATE_CANCELING:
        return current
    raise ContinuationError(f"cannot begin manual resume from {state}")


def finish_owned_delete(project: Path, session_id: str) -> dict[str, Any]:
    current = load_receipt(project, session_id)
    if current is None:
        raise ContinuationError("missing receipt")
    state = _validate_state(current.get("state"))
    if state == STATE_CANCELING:
        return _replace_state(project, session_id, current, STATE_RUNNING, cronId=None)
    if state == STATE_ARMED:
        return _replace_state(project, session_id, current, STATE_PAUSED, cronId=None)
    raise ContinuationError(f"cannot finish owned delete from {state}")


def accept_wake(project: Path, session_id: str, prompt: str) -> dict[str, Any]:
    current = load_receipt(project, session_id)
    if current is None:
        raise ContinuationError("missing receipt")
    state = _validate_state(current.get("state"))
    if state != STATE_ARMED:
        raise ContinuationError(f"cannot accept wake from {state}")
    if prompt != continuation_prompt(current):
        raise ContinuationError("continuation wake prompt mismatch")
    return _replace_state(project, session_id, current, STATE_RUNNING, cronId=None)
```

Make `continuation_prompt()` deterministic for `arming`, `armed`, and `canceling` and independent of state and cron ID:

```python
def continuation_prompt(receipt: Mapping[str, Any]) -> str:
    state = _validate_state(receipt.get("state"))
    if state not in {STATE_ARMING, STATE_ARMED, STATE_CANCELING}:
        raise ContinuationError("continuation prompt requires scheduler-owned receipt")
    tag = str(receipt["leaseTag"])
    session_id = _validate_token(str(receipt["sessionId"]), label="session id")
    ref = _require_single_ref(receipt)
    return (
        f"[[{tag}]]\n"
        "Claude UI reverse-engineering continuation wake-up.\n"
        f"Session: {session_id}\n"
        f"Ref: {ref}\n"
        "The UserPromptSubmit hook must validate this entire immutable prompt before work.\n"
        "Run the canonical goal first: python -m ui_clone.goal "
        f"{ref} --check-done.\n"
        "If incomplete, read status --json, next --json, and report --for-llm for the bound ref.\n"
        "Execute the one reported next action. If its exact pipeline owner is already alive, "
        "attach to or wait on that owner and never start a duplicate."
    )


def cron_create_input(receipt: Mapping[str, Any]) -> dict[str, object]:
    if _validate_state(receipt.get("state")) != STATE_ARMING:
        raise ContinuationError("CronCreate input requires arming receipt")
    return {
        "cron": _SCHEDULE,
        "prompt": continuation_prompt(receipt),
        "recurring": False,
        "durable": False,
    }
```

Change row validation to require `recurring is False`. Its result branch must be:

```python
if cron_ids:
    if state == STATE_ARMING:
        receipt = mark_armed(project, session_id, cron_ids[0])
        return ReconcileResult(STATE_ARMED, receipt, cron_ids[0])
    if state in {STATE_ARMED, STATE_CANCELING}:
        return ReconcileResult(state, current, cron_ids[0])
    return ReconcileResult("unexpected", current, cron_ids[0])
if state in {STATE_ARMED, STATE_CANCELING}:
    receipt = _replace_state(project, session_id, current, STATE_PAUSED, cronId=None)
    return ReconcileResult(STATE_PAUSED, receipt)
return ReconcileResult("absent", current)
```

Replace delete-time goal classification with `refresh_goal_state(project, session_id)`. Reuse the existing subprocess invocation and terminal-state loader, but apply this exact return-code policy:

```python
if result.returncode == 0:
    return _replace_state(
        project,
        session_id,
        current,
        STATE_COMPLETE,
        cronId=None,
        outcome="canonical goal --check-done passed",
    )
if result.returncode == 1:
    return current
terminal = _terminal_state(ref_dir)
if terminal is None:
    terminal = {
        "status": "aborted",
        "category": "goal-abort",
        "reason": (result.stderr or result.stdout or "goal --check-done aborted").strip(),
    }
return _replace_state(
    project, session_id, current, STATE_TERMINAL, cronId=None, terminalState=terminal
)
```

Expose CLI verbs `activate`, `bind-ref`, `arm`, `mark-unsupported`, `pause`, and `status`; `pause` moves `running -> paused` directly and rejects scheduler-owned states until the owned `CronDelete` completes.

- [ ] **Step 4: Run the core tests and static checks until GREEN**

Run:

```bash
uv run python -m pytest tests/test_claude_continuation.py -q
uv run ruff check ui_clone/claude_continuation.py tests/test_claude_continuation.py
uv run mypy ui_clone/claude_continuation.py tests/test_claude_continuation.py
git diff --check -- ui_clone/claude_continuation.py tests/test_claude_continuation.py
```

Expected: all commands exit `0` with no failures.

- [ ] **Step 5: Commit the core state machine only**

```bash
git add ui_clone/claude_continuation.py tests/test_claude_continuation.py
git commit -m "Make Claude continuation edge-triggered" \
  -m $'Constraint: One-shot jobs auto-delete before the wake turn inspects CronList.\nRejected: Preserve pending/active aliases | schema-v1 receipts must not be silently reinterpreted.\nConfidence: high\nScope-risk: moderate\nDirective: Keep cronId exclusive to armed and canceling receipts.\nTested: Core continuation pytest, Ruff, mypy, and diff check.\nNot-tested: Claude hook and Stop integration are covered by later tasks.'
```

### Task 2: Adapt Claude lifecycle hooks to exact one-shot events

**Files:**
- Modify: `ui_clone/hooks/claude_continuation.py`
- Test: `tests/hooks/test_claude_continuation.py`

- [ ] **Step 1: Write failing adapter tests for activation, wake, manual resume, and delete**

Replace activation-time CronCreate expectations and add:

```python
def make_arming(project: Path) -> dict[str, object]:
    cc.activate(project, SESSION, cc.UI_RE_SKILL)
    ref = project / "tmp" / "ref" / "demo"
    ref.mkdir(parents=True, exist_ok=True)
    cc.bind_ref(project, SESSION, ref)
    return cast(dict[str, object], cc.arm(project, SESSION))


def make_armed(project: Path) -> dict[str, object]:
    make_arming(project)
    return cast(dict[str, object], cc.mark_armed(project, SESSION, CRON_ID))


def test_skill_activation_creates_running_without_cron_context(tmp_path: Path) -> None:
    out = run_adapter(tmp_path, user_prompt_payload(
        project=tmp_path,
        prompt="/ui-clone-skills:ui-reverse-engineering https://example.com/",
    ))
    receipt = cc.load_receipt(tmp_path, SESSION)
    assert receipt is not None
    assert receipt["state"] == cc.STATE_RUNNING
    assert out == ""


def test_arming_accepts_only_exact_nonrecurring_create(tmp_path: Path) -> None:
    receipt = make_arming(tmp_path)
    exact = cc.cron_create_input(receipt)
    assert exact["recurring"] is False
    assert run_adapter(tmp_path, payload(
        project=tmp_path,
        event="PreToolUse",
        tool="CronCreate",
        tool_input=exact,
    )) == ""


def test_successful_create_arms_and_requires_immediate_turn_end(tmp_path: Path) -> None:
    receipt = make_arming(tmp_path)
    out = run_adapter(tmp_path, payload(
        project=tmp_path,
        event="PostToolUse",
        tool="CronCreate",
        tool_input=cc.cron_create_input(receipt),
        tool_response={"id": CRON_ID},
    ))
    assert cc.load_receipt(tmp_path, SESSION)["state"] == cc.STATE_ARMED
    assert "end the current assistant turn" in context(out)
    assert "pipeline work" in context(out)


def test_exact_wake_resumes_but_altered_wake_does_not(tmp_path: Path) -> None:
    armed = make_armed(tmp_path)
    wake = cc.continuation_prompt(armed)
    assert run_adapter(tmp_path, user_prompt_payload(project=tmp_path, prompt=wake))
    assert cc.load_receipt(tmp_path, SESSION)["state"] == cc.STATE_RUNNING


def test_manual_prompt_cancels_armed_job_before_work(tmp_path: Path) -> None:
    make_armed(tmp_path)
    out = run_adapter(tmp_path, user_prompt_payload(project=tmp_path, prompt="continue with the fix"))
    assert cc.load_receipt(tmp_path, SESSION)["state"] == cc.STATE_CANCELING
    assert json.dumps({"id": CRON_ID}, sort_keys=True, indent=2) in context(out)


@pytest.mark.parametrize(("before", "after"), [
    (cc.STATE_CANCELING, cc.STATE_RUNNING),
    (cc.STATE_ARMED, cc.STATE_PAUSED),
])
def test_owned_delete_success_uses_state_specific_outcome(
    tmp_path: Path, before: str, after: str
) -> None:
    make_armed(tmp_path)
    if before == cc.STATE_CANCELING:
        cc.begin_manual_resume(tmp_path, SESSION)
    run_adapter(tmp_path, payload(
        project=tmp_path,
        event="PostToolUse",
        tool="CronDelete",
        tool_input={"id": CRON_ID},
        tool_response={"ok": True},
    ))
    assert cc.load_receipt(tmp_path, SESSION)["state"] == after
```

Add a race regression proving an exact wake observed from `canceling` emits no pipeline instruction and leaves `canceling` unchanged.

```python
def test_exact_wake_loses_to_manual_resume_after_canceling_transition(tmp_path: Path) -> None:
    armed = make_armed(tmp_path)
    wake = cc.continuation_prompt(armed)
    cc.begin_manual_resume(tmp_path, SESSION)
    out = run_adapter(tmp_path, user_prompt_payload(project=tmp_path, prompt=wake))
    receipt = cc.load_receipt(tmp_path, SESSION)
    assert receipt is not None
    assert receipt["state"] == cc.STATE_CANCELING
    assert "stale one-shot wake" in context(out)
    assert "goal --check-done" not in context(out)
```

- [ ] **Step 2: Run adapter tests and confirm RED**

Run:

```bash
uv run python -m pytest tests/hooks/test_claude_continuation.py -q
```

Expected: activation still requests recurring CronCreate, exact wake is unrecognized, and manual resume/delete state assertions fail.

- [ ] **Step 3: Implement exact Claude event routing**

Use `cc.cron_create_input()` instead of an adapter-owned recurring payload. Implement `UserPromptSubmit` routing in this order:

```python
state = receipt.get("state") if receipt is not None else None
if state in {cc.STATE_ARMED, cc.STATE_CANCELING} and prompt == cc.continuation_prompt(receipt):
    if receipt["state"] == cc.STATE_ARMED:
        cc.accept_wake(project_root, session_id, prompt)
        return _emit_context(_EVENT_USER_PROMPT, _wake_context(receipt, session_id))
    if receipt["state"] == cc.STATE_CANCELING:
        return _emit_context(_EVENT_USER_PROMPT, _stale_wake_context(receipt))
if receipt is not None and receipt["state"] == cc.STATE_ARMED:
    canceling = cc.begin_manual_resume(project_root, session_id)
    return _emit_context(_EVENT_USER_PROMPT, _manual_resume_context(canceling))
if _is_ui_re_user_prompt(prompt):
    cc.activate(project_root, session_id, cc.UI_RE_SKILL)
return None
```

Make PostToolUse `CronCreate` call `mark_armed()` and return immediate-turn-end context. Make PostToolUse `CronDelete` call `finish_owned_delete()`. Preserve structured-only cron ID extraction, unrelated cron no-ops, exact tag matching, and fail-closed malformed payload behavior.

- [ ] **Step 4: Run adapter and core suites until GREEN**

```bash
uv run python -m pytest tests/test_claude_continuation.py tests/hooks/test_claude_continuation.py -q
uv run ruff check ui_clone/hooks/claude_continuation.py tests/hooks/test_claude_continuation.py
uv run mypy ui_clone/hooks/claude_continuation.py tests/hooks/test_claude_continuation.py
git diff --check -- ui_clone/hooks/claude_continuation.py tests/hooks/test_claude_continuation.py
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit the Claude adapter**

```bash
git add ui_clone/hooks/claude_continuation.py tests/hooks/test_claude_continuation.py
git commit -m "Wake Claude only from an owned one-shot" \
  -m $'Constraint: A scheduled wake is delivered as UserPromptSubmit after its job auto-deletes.\nRejected: Poll CronList from every user prompt | exact prompt identity is sufficient and avoids false ownership.\nConfidence: high\nScope-risk: moderate\nDirective: Altered wake prompts and canceling-state races must never execute pipeline work.\nTested: Claude continuation adapter and core suites, Ruff, mypy, and diff check.\nNot-tested: Stop and Pre-Bash integration are covered by later tasks.'
```

### Task 3: Make incomplete Stop the only arming edge

**Files:**
- Modify: `ui_clone/hooks/section_gate.py`
- Test: `tests/hooks/test_section_gate.py`

- [ ] **Step 1: Write failing Stop-boundary tests**

Replace v1 snapshot fixtures with non-recurring rows and add:

```python
import subprocess

from ui_clone.hooks import section_gate


def test_first_incomplete_stop_creates_binds_and_arms_one_shot(self, tmp_path: Path) -> None:
    data, ref_dir = self._blocking_stop(tmp_path, session_crons=[])
    receipt = cc.load_receipt(tmp_path, self.SESSION_ID)
    assert receipt is not None
    assert receipt["state"] == cc.STATE_ARMING
    assert receipt["refDir"] == ref_dir.relative_to(tmp_path).as_posix()
    assert cc.cron_create_input(receipt)["recurring"] is False
    assert json.dumps(cc.cron_create_input(receipt), sort_keys=True) in str(data["reason"])


def test_running_receipt_has_no_stop_prefix_until_gate_is_incomplete(
    self, tmp_path: Path
) -> None:
    cc.activate(tmp_path, self.SESSION_ID, cc.UI_RE_SKILL)
    data, _ = self._blocking_stop(tmp_path, session_crons=[])
    assert cc.load_receipt(tmp_path, self.SESSION_ID)["state"] == cc.STATE_ARMING
    assert "recurring\": false" in str(data["reason"])


def test_armed_receipt_does_not_create_a_second_job(self, tmp_path: Path) -> None:
    cc.activate(tmp_path, self.SESSION_ID, cc.UI_RE_SKILL)
    cc.arm(tmp_path, self.SESSION_ID)
    cc.mark_armed(tmp_path, self.SESSION_ID, self.CRON_ID)
    data, _ = self._blocking_stop(tmp_path, session_crons=[self._cron_row(tmp_path)])
    assert str(data["reason"]).count("CronCreate") == 0
    assert "end the current assistant turn" in str(data["reason"])


def test_completed_stop_refreshes_receipt_from_canonical_goal(
    self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref_dir = tmp_path / "tmp" / "ref" / "demo"
    ref_dir.mkdir(parents=True)
    cc.activate(tmp_path, self.SESSION_ID, cc.UI_RE_SKILL)
    cc.bind_ref(tmp_path, self.SESSION_ID, ref_dir)
    monkeypatch.setattr(cc.subprocess, "run", lambda cmd, **kwargs: subprocess.CompletedProcess(
        cmd, 0, stdout="DONE verify-stamp.json", stderr=""
    ))
    section_gate._refresh_continuation_final(
        tmp_path, self.SESSION_ID, ref_dir
    )
    receipt = cc.load_receipt(tmp_path, self.SESSION_ID)
    assert receipt is not None
    assert receipt["state"] == cc.STATE_COMPLETE
```

Preserve regressions that `stop_hook_active: true` releases without re-arming and duplicate one-shot rows fail closed with deletion guidance.

- [ ] **Step 2: Run Stop tests and confirm RED**

```bash
uv run python -m pytest tests/hooks/test_section_gate.py -q
```

Expected: first Stop still requests the v1 recurring bootstrap without atomically entering `arming`, and completion does not finalize the receipt.

- [ ] **Step 3: Implement Stop-triggered arming and finalization**

Change `_continuation_stop_prefix` to receive the exact `ref_dir`. For an incomplete gate:

```python
receipt = _continuation.load_receipt(project_root, session_id)
if receipt is None and not _continuation.receipt_path(project_root, session_id).exists():
    receipt = _continuation.activate(project_root, session_id, _continuation.UI_RE_SKILL)
receipt = _continuation.bind_ref(project_root, session_id, ref_dir)
if receipt["state"] == _continuation.STATE_RUNNING:
    receipt = _continuation.arm(project_root, session_id)
```

Emit the exact sorted JSON from `cron_create_input(receipt)` only in `arming`. In `armed`, instruct Claude to end the current turn without more pipeline work. In `canceling` or `paused`, require the owned delete or explicit reactivation; in `unsupported`, retain the ordinary one-nudge gate message without claiming automatic continuation.

Add `_refresh_continuation_final(project_root, session_id, ref_dir)` to load the receipt, compare its safe bound ref to `ref_dir.relative_to(project_root)`, and call `refresh_goal_state()` only from `running` or `arming`. Call it when a scoped ref passes the existing Stop enforcement. Preserve the existing `stop_hook_active` immediate-release rule and all host-neutral verification semantics.

- [ ] **Step 4: Run Stop, core, and adapter suites until GREEN**

```bash
uv run python -m pytest tests/hooks/test_section_gate.py tests/test_claude_continuation.py tests/hooks/test_claude_continuation.py -q
uv run ruff check ui_clone/hooks/section_gate.py tests/hooks/test_section_gate.py
uv run mypy ui_clone/hooks/section_gate.py tests/hooks/test_section_gate.py
git diff --check -- ui_clone/hooks/section_gate.py tests/hooks/test_section_gate.py
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit the Stop integration**

```bash
git add ui_clone/hooks/section_gate.py tests/hooks/test_section_gate.py
git commit -m "Arm Claude continuation only after an incomplete Stop" \
  -m $'Constraint: Stop-hook re-entry must release after one block.\nRejected: Arm at skill activation | background owners can remain alive across model-idle minute boundaries.\nConfidence: high\nScope-risk: moderate\nDirective: Never emit a second CronCreate for armed, canceling, paused, or unsupported receipts.\nTested: Stop, continuation core, and adapter suites plus Ruff, mypy, and diff check.\nNot-tested: Installed interactive Claude behavior is covered by final acceptance.'
```

### Task 4: Block pipeline ownership during scheduler transitions

**Files:**
- Modify: `ui_clone/hooks/pre_bash_rules/dispatcher.py`
- Test: `tests/hooks/test_pre_bash.py`

- [ ] **Step 1: Write the failing Bash state matrix**

Replace hand-written schema-v1 fixtures with valid schema-v2 receipts and add:

```python
@pytest.mark.parametrize("state", ["arming", "armed", "canceling", "paused"])
def test_continuation_scheduler_states_block_owned_pipeline_commands(
    self, tmp_path: Path, state: str
) -> None:
    session_id = f"session-{state}"
    receipt = cc.activate(tmp_path, session_id, cc.UI_RE_SKILL)
    if state != "paused":
        ref = tmp_path / "tmp" / "ref" / f"target-{state}"
        ref.mkdir(parents=True, exist_ok=True)
        cc.bind_ref(tmp_path, session_id, ref)
        receipt = cc.arm(tmp_path, session_id)
    if state in {"armed", "canceling"}:
        receipt = cc.mark_armed(tmp_path, session_id, "cron-owned")
    if state == "canceling":
        receipt = cc.begin_manual_resume(tmp_path, session_id)
    if state == "paused":
        receipt = cc.pause(tmp_path, session_id)
    result = run_hook(self.MODULE, stdin_data=_bash_input_with_session(
        _pipeline_run_command("target-ref"), session_id, tmp_path
    ), env={"CLAUDE_PROJECT_DIR": str(tmp_path)})
    assert json.loads(result.stdout)["decision"] == "block"


@pytest.mark.parametrize("state", ["running", "unsupported", "complete", "terminal"])
def test_non_scheduler_states_do_not_block_owned_pipeline_commands(
    self, tmp_path: Path, state: str
) -> None:
    session_id = f"session-{state}"
    receipt = cc.activate(tmp_path, session_id, cc.UI_RE_SKILL)
    if state == "unsupported":
        ref = tmp_path / "tmp" / "ref" / f"target-{state}"
        ref.mkdir(parents=True, exist_ok=True)
        cc.bind_ref(tmp_path, session_id, ref)
        cc.arm(tmp_path, session_id)
        cc.mark_unsupported(tmp_path, session_id, "CronCreate unavailable")
    elif state == "complete":
        cc._replace_state(tmp_path, session_id, receipt, cc.STATE_COMPLETE,
                          outcome="canonical goal --check-done passed")
    elif state == "terminal":
        cc._replace_state(
            tmp_path,
            session_id,
            receipt,
            cc.STATE_TERMINAL,
            terminalState={"status": "aborted", "category": "goal-abort"},
        )
    result = run_hook(self.MODULE, stdin_data=_bash_input_with_session(
        _pipeline_run_command(f"target-{state}"), session_id, tmp_path
    ), env={"CLAUDE_PROJECT_DIR": str(tmp_path)})
    assert result.returncode == 0
    assert "continuation" not in result.stdout.lower()
```

Keep the corrupt-receipt control bypass explicit:

```python
@pytest.mark.parametrize(
    "subcommand", ["activate", "bind-ref", "arm", "mark-unsupported", "pause", "status"]
)
def test_schema_v2_continuation_control_commands_are_always_allowed(
    self, tmp_path: Path, subcommand: str
) -> None:
    session_id = "claude-session"
    path = _continuation_receipt_path(tmp_path, session_id)
    path.parent.mkdir(parents=True)
    path.write_text("{not-json", encoding="utf-8")
    result = run_hook(
        self.MODULE,
        stdin_data=_bash_input_with_session(
            f"python -m ui_clone.claude_continuation {subcommand}", session_id, tmp_path
        ),
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""
```

- [ ] **Step 2: Run Pre-Bash tests and confirm RED**

```bash
uv run python -m pytest tests/hooks/test_pre_bash.py -q
```

Expected: only legacy `pending` is blocked, schema-v2 receipts are rejected or incorrectly allowed, and the control-command list is stale.

- [ ] **Step 3: Implement the state matrix**

Set:

```python
_CONTINUATION_BLOCKED_STATES = {"arming", "armed", "canceling", "paused"}
_CONTINUATION_CONTROL_SUBCOMMANDS = {
    "activate", "bind-ref", "arm", "mark-unsupported", "pause", "status"
}
```

In `_guard_claude_continuation`, block only continuation-owned UI-clone commands when the validated receipt state is in `_CONTINUATION_BLOCKED_STATES`. Allow and bind the first exact ref only in `running`; allow `unsupported`, `complete`, and `terminal` without scheduler claims. Preserve corrupt-receipt fail-closed behavior, other-session isolation, unrelated Bash no-ops, and ref mismatch protection.

- [ ] **Step 4: Run the four continuation integration modules until GREEN**

```bash
uv run python -m pytest tests/hooks/test_pre_bash.py tests/hooks/test_section_gate.py tests/hooks/test_claude_continuation.py tests/test_claude_continuation.py -q
uv run ruff check ui_clone/hooks/pre_bash_rules/dispatcher.py tests/hooks/test_pre_bash.py
uv run mypy ui_clone/hooks/pre_bash_rules/dispatcher.py tests/hooks/test_pre_bash.py
git diff --check -- ui_clone/hooks/pre_bash_rules/dispatcher.py tests/hooks/test_pre_bash.py
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit the Pre-Bash guard**

```bash
git add ui_clone/hooks/pre_bash_rules/dispatcher.py tests/hooks/test_pre_bash.py
git commit -m "Serialize Claude pipeline ownership around one-shot state" \
  -m $'Constraint: A successful create must end the turn before any further pipeline command.\nRejected: Block every receipt state | running and unsupported hosts must retain normal pipeline execution.\nConfidence: high\nScope-risk: narrow\nDirective: Unrelated Bash and continuation control commands remain outside this ownership guard.\nTested: Continuation integration modules, Ruff, mypy, and diff check.\nNot-tested: Host installation is covered by final delivery checks.'
```

### Task 5: Ratchet host isolation and bump the install version

**Files:**
- Modify: `.claude-plugin/plugin.json`
- Modify: `.claude-plugin/marketplace.json`
- Modify: `.codex-plugin/plugin.json`
- Modify: `pyproject.toml`
- Modify: `package.json`
- Modify: `ui_clone/__init__.py`
- Verify unchanged: `tests/test_hook_manifest_parity.py`
- Verify unchanged: `hooks/hooks.json`
- Verify unchanged: `hooks/codex-hooks.json`

- [ ] **Step 1: Verify the existing parity ratchet before touching delivery metadata**

Keep the exact route set:

```python
CLAUDE_CONTINUATION_ROUTES = {
    ("UserPromptSubmit", "claude_continuation", ""),
    ("PreToolUse", "claude_continuation", "Skill"),
    ("PreToolUse", "claude_continuation", "CronCreate|CronDelete"),
    ("PostToolUse", "claude_continuation", "CronCreate|CronList|CronDelete"),
}
```

The existing test must assert the Claude manifest contains all four and the Codex manifest contains none. Do not edit either hook manifest unless this exact assertion fails.

- [ ] **Step 2: Run the parity test before the version bump**

```bash
uv run python -m pytest tests/test_hook_manifest_parity.py -q
```

Expected: route isolation passes with no source change required.

- [ ] **Step 3: Bump all six synchronized versions to `0.7.32`**

Change only the version field in each listed file. The bump is mandatory because Claude caches plugin contents per version.

- [ ] **Step 4: Run host and version checks**

```bash
uv run python -m pytest tests/test_hook_manifest_parity.py tests/test_codex_hooks_install.py -q
bash scripts/ci/pre-push-security.sh
git diff --check -- .claude-plugin/plugin.json .claude-plugin/marketplace.json .codex-plugin/plugin.json pyproject.toml package.json ui_clone/__init__.py
```

Expected: synchronized `0.7.32`, Claude-only continuation routes, unchanged Codex continuation topology, and zero security blockers.

- [ ] **Step 5: Commit the delivery version**

```bash
git add .claude-plugin/plugin.json .claude-plugin/marketplace.json .codex-plugin/plugin.json pyproject.toml package.json ui_clone/__init__.py
git commit -m "Deliver the one-shot continuation contract to fresh sessions" \
  -m $'Constraint: Claude plugin caches are immutable per manifest version.\nRejected: Reuse 0.7.31 | fresh sessions would load stale recurring hook code.\nConfidence: high\nScope-risk: narrow\nDirective: Keep all six version carriers synchronized and Codex free of Claude continuation routes.\nTested: Manifest parity, Codex hook install tests, security checks, and diff check.\nNot-tested: Live host cache delivery is covered by final installation acceptance.'
```

### Task 6: Verify, install, and prove fresh-session behavior

**Files:**
- Acceptance repair: `ui_clone/hooks/section_gate.py`, `tests/hooks/test_section_gate.py`
- Cache-busting delivery: all six version carriers, plus `uv.lock`
- Preserve: `.handover/` and temporary acceptance artifacts until the result is reported.

- [ ] **Step 1: Run the complete repository verification gates**

```bash
bash scripts/ci/ci-local.sh > /tmp/ui-clone-one-shot-ci.log 2>&1
bash scripts/ci/pre-push-security.sh > /tmp/ui-clone-one-shot-security.log 2>&1
tail -40 /tmp/ui-clone-one-shot-ci.log
tail -40 /tmp/ui-clone-one-shot-security.log
```

Expected: both commands exit `0`, with zero pytest, mypy, Ruff, shell, review, security, cross-reference, or version-sync failures.

- [ ] **Step 2: Review the complete implementation twice**

Dispatch one read-only spec-compliance reviewer against the approved design and one code-quality reviewer after spec approval. Resolve every finding and re-run the affected focused suite before continuing.

- [ ] **Step 3: Install both host projections from this worktree**

```bash
bash install.sh
```

Expected: Claude plugin `ui-clone-skills@voidmatcha` installs at `0.7.33`, the cache delivery probe finds the continuation hook files, the Codex projection and merged hooks verify, and the install marker points to this worktree. The patch version is required because the first live acceptance exposed a markerless bound-ref Stop gap after `0.7.32` had already entered Claude's immutable cache.

- [ ] **Step 4: Start fresh ordinary Claude and Codex tabs through Purplemux**

Use `local-skills:handover` only as the display/handshake adapter. Read `references/display-adapter-contract.md` and `references/purplemux-display.md`, create two new terminal tabs in the verified Purplemux workspace, launch foreground `claude` and `codex --yolo`, and send bounded acceptance prompts. Do not use `claude -p`, cmux, synthetic terminal keystroke loops, or an external supervisor.

Start the deterministic local fixture first:

```bash
python -m http.server 48731 --bind 127.0.0.1 --directory tests/integration/fixtures \
  > /tmp/ui-clone-one-shot-fixture.log 2>&1 &
UI_RE_FIXTURE_PID=$!
curl -fsS http://127.0.0.1:48731/splash.html > /dev/null
```

Claude acceptance prompt:

```text
/ui-clone-skills:ui-reverse-engineering http://127.0.0.1:48731/splash.html
Remain in the ordinary interactive session. Confirm activation creates a schema-v2 running receipt with no cron. Keep one background pipeline owner alive across a minute boundary, then attempt an incomplete Stop so the hook arms exactly one recurring:false, durable:false task. End the turn after CronCreate. On the tagged wake, confirm the receipt returns to running and CronList contains no owned job. Do not claim clone completion.
```

Codex acceptance prompt:

```text
Inspect the installed ui-clone hook topology and run the focused Codex manifest/install smoke tests. Confirm no Claude continuation receipt or Cron lifecycle route is created for this Codex session. Do not modify source files.
```

- [ ] **Step 5: Collect acceptance evidence and stop only on the full contract**

Required Claude evidence:

```text
activation receipt state=running, schemaVersion=2, cronId absent
CronList before incomplete Stop: no owned continuation job
first incomplete Stop receipt state=arming
CronCreate input recurring=false and durable=false
PostToolUse receipt state=armed with exactly one cronId
one tagged wake only
wake receipt state=running with cronId absent
CronList after wake: no owned continuation job
```

Required Codex evidence:

```text
hooks/codex-hooks.json has no UserPromptSubmit or Cron lifecycle continuation route
tests/test_hook_manifest_parity.py passes
tests/test_codex_hooks_install.py passes
no .ui-re-continuation receipt is created for the Codex session
```

If any evidence is missing, classify the failure, add a failing regression test, fix through RED/GREEN, repeat the full verification and reinstall, and use fresh tabs for the next acceptance attempt.

After acceptance, run `kill "$UI_RE_FIXTURE_PID"` for the exact fixture server created by Step 4 and leave the new Claude/Codex tabs open for the user unless they explicitly request closure.
