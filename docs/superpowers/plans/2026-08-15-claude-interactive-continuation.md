# Claude Interactive Continuation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep ordinary interactive Claude UI-clone work moving across idle turn
boundaries with one session-scoped Cron lease, and keep both Claude and Codex in
active rework after a recoverable canonical verify failure.

**Architecture:** A host-neutral core module stores one validated, atomic receipt per Claude session and owns all state transitions. A Claude-only hook adapter translates direct `UserPromptSubmit` slash commands, fallback `Skill` tool calls, and Cron lifecycle payloads into that state machine; the existing Bash and Stop gates only enforce or reconcile the receipt around the existing pipeline goal. The implementation is installed as a versioned local plugin, then accepted in fresh Claude and Codex Purplemux tabs from separate external run directories.

**Tech Stack:** Python 3.9, Claude Code hook JSON, pytest, Ruff, mypy, Bash install tooling, Purplemux CLI.

---

## File Structure

- Create `ui_clone/claude_continuation.py` for receipt storage, validation, prompt construction, reconciliation, goal classification, and the operator CLI.
- Create `ui_clone/hooks/claude_continuation.py` for Claude hook input/output adaptation only.
- Create `tests/test_claude_continuation.py` for the pure state machine and CLI.
- Create `tests/hooks/test_claude_continuation.py` for hook payload contracts.
- Modify `ui_clone/hooks/pre_bash_rules/dispatcher.py` and `tests/hooks/test_pre_bash.py` for pending-lease enforcement and ref binding.
- Modify `ui_clone/hooks/section_gate.py` and `tests/hooks/test_section_gate.py` for Stop snapshot reconciliation and bootstrap guidance.
- Modify `ui_clone/pipeline_phases/verify.py`, `ui_clone/claude_continuation.py`,
  and their tests so ordinary verify failures remain active while existing
  hard-cap/unclonable outcomes stay terminal.
- Modify `ui_clone/check_inputs.py`, capture-artifact inventory validation, and
  tests so renamed reference frames invalidate and fail the compact required check.
- Modify `ui_clone/hooks/pre_bash_rules/bash_write.py` and tests so direct
  generation-plan provenance rewrites are blocked.
- Modify `hooks/hooks.json` and `tests/test_hook_manifest_parity.py` for the exact Claude-only `UserPromptSubmit` and tool lifecycle routes.
- Modify `.gitignore` so operational receipts never become source or verification inputs.
- Modify the six synchronized version files to release the new Claude cache content under a fresh version, because Claude caches plugin content by version.

### Task 1: Build the receipt state machine and CLI

**Files:**
- Create: `ui_clone/claude_continuation.py`
- Create: `tests/test_claude_continuation.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing receipt-path and atomic-storage tests**

```python
def test_pending_receipt_is_private_atomic_and_idempotent(tmp_path: Path) -> None:
    first = create_pending(tmp_path, SESSION_ID, UI_RE_SKILL)
    second = create_pending(tmp_path, SESSION_ID, UI_RE_SKILL)
    path = receipt_path(tmp_path, SESSION_ID)
    assert first == second
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob("*.tmp.*"))


@pytest.mark.parametrize("session_id", ["../escape", "", "a/b", "a b"])
def test_receipt_path_rejects_unsafe_session_ids(tmp_path: Path, session_id: str) -> None:
    with pytest.raises(ValueError):
        receipt_path(tmp_path, session_id)
```

- [ ] **Step 2: Run the tests and record the expected RED result**

Run: `uv run python -m pytest tests/test_claude_continuation.py -q`

Expected: collection fails because `ui_clone.claude_continuation` does not exist.

- [ ] **Step 3: Implement the validated receipt model and locked atomic writer**

Create `UI_RE_SKILL = "ui-clone-skills:ui-reverse-engineering"`, `RECEIPT_DIR = ".ui-re-continuation"`, and a `ReceiptState` literal containing only `pending`, `active`, `paused`, `complete`, `terminal`, and `unsupported`. Implement these exact public signatures: `receipt_path(project_root: Path, session_id: str) -> Path`, `load_receipt(project_root: Path, session_id: str) -> dict[str, object] | None`, `create_pending(project_root: Path, session_id: str, skill: str) -> dict[str, object]`, `bind_ref(project_root: Path, session_id: str, ref_dir: Path) -> dict[str, object]`, `mark_active(project_root: Path, session_id: str, cron_id: str) -> dict[str, object]`, `mark_unsupported(project_root: Path, session_id: str, reason: str) -> dict[str, object]`, and `pause(project_root: Path, session_id: str) -> dict[str, object]`.

Use `fcntl.flock` on a sibling lock, write a mode-`0600` same-directory temporary file, flush and `os.fsync`, then `os.replace`. Reject invalid JSON, unknown states, a mismatched embedded session ID, traversal, and unsupported transitions without replacing the prior receipt. Add `.ui-re-continuation/` to `.gitignore`.

- [ ] **Step 4: Add failing Cron normalization and transition tests**

```python
def test_empty_snapshot_pauses_active_but_malformed_snapshot_is_unavailable(tmp_path: Path) -> None:
    create_pending(tmp_path, SESSION_ID, UI_RE_SKILL)
    mark_active(tmp_path, SESSION_ID, "cron-1")
    assert reconcile_cron_snapshot(tmp_path, SESSION_ID, []).state == "paused"

    mark_active(tmp_path, SESSION_ID, "cron-1")
    malformed = [{"id": 3, "schedule": None, "recurring": "yes", "prompt": []}]
    assert reconcile_cron_snapshot(tmp_path, SESSION_ID, malformed).availability == "unavailable"
    assert load_receipt(tmp_path, SESSION_ID)["state"] == "active"


def test_duplicate_tagged_jobs_fail_closed(tmp_path: Path) -> None:
    receipt = create_pending(tmp_path, SESSION_ID, UI_RE_SKILL)
    rows = [cron_row("cron-1", receipt["leaseTag"]), cron_row("cron-2", receipt["leaseTag"])]
    with pytest.raises(DuplicateLeaseError):
        reconcile_cron_snapshot(tmp_path, SESSION_ID, rows)
```

- [ ] **Step 5: Implement structured Cron parsing, the immutable prompt, and goal-owned deletion outcomes**

Implement these exact public signatures: `extract_created_cron_id(tool_response: object, lease_tag: str) -> str | None`, `reconcile_cron_snapshot(project_root: Path, session_id: str, raw_snapshot: object) -> ReconcileResult`, `continuation_prompt(receipt: Mapping[str, object]) -> str`, and `owned_delete_outcome(project_root: Path, session_id: str) -> dict[str, object]`.

Accept only `tool_response.id`, `tool_response.cron.id`, a top-level structured CronList array, or `tool_response.crons`. Match the full `[[UI_RE_CONTINUATION:<digest>]]` tag, `schedule == "* * * * *"`, and `recurring is True`. Use `build_goal_card_data(ref_dir)` plus canonical stamp semantics: done becomes `complete`, an abort banner or authoritative terminal state becomes `terminal`, and everything else becomes `paused`.

The prompt test must assert the immutable text checks the current receipt state first, self-deletes without pipeline work when paused, resolves exactly one bound `refDir`, runs `python -m ui_clone.goal <ref> --check-done` before new work, deletes itself on complete/terminal/authority-required outcomes, and otherwise reads `status --json`, `next --json`, and `report --for-llm` before executing the reported next action. It must not contain the source URL, arbitrary user arguments, `-p`, `--resume`, Purplemux, or cmux instructions.

- [ ] **Step 6: Add and implement the exact operator CLI**

Test and implement:

```text
python -m ui_clone.claude_continuation create-pending --session-id ID --cwd DIR --skill NAME
python -m ui_clone.claude_continuation bind-ref --session-id ID --cwd DIR --ref-dir REF
python -m ui_clone.claude_continuation mark-unsupported --session-id ID --cwd DIR --reason TEXT
python -m ui_clone.claude_continuation pause --session-id ID --cwd DIR
python -m ui_clone.claude_continuation status --session-id ID --cwd DIR --json
```

The CLI returns `2` for validation or state errors, prints JSON only for `status --json`, and exposes no `complete` command.

- [ ] **Step 7: Run the core tests, Ruff, and mypy**

Run:

```bash
uv run python -m pytest tests/test_claude_continuation.py -q
uv run ruff check ui_clone/claude_continuation.py tests/test_claude_continuation.py
uv run mypy ui_clone/claude_continuation.py
```

Expected: all commands exit `0`.

### Task 2: Adapt Claude prompt, Skill, and Cron payloads

**Files:**
- Create: `ui_clone/hooks/claude_continuation.py`
- Create: `tests/hooks/test_claude_continuation.py`

- [ ] **Step 1: Write failing direct slash-command, Skill fallback, and Cron hook tests**

```python
def test_user_prompt_slash_command_creates_pending_receipt_and_requests_cron(tmp_path: Path) -> None:
    result = run_hook(tmp_path, event="UserPromptSubmit", prompt=(
        "/ui-clone-skills:ui-reverse-engineering https://example.com/"
    ))
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "CronCreate" in context
    assert load_receipt(tmp_path, SESSION_ID)["state"] == "pending"


def test_skill_tool_fallback_creates_pending_receipt_and_requests_cron(tmp_path: Path) -> None:
    result = run_hook(tmp_path, event="PreToolUse", tool="Skill", tool_input={
        "skill": "ui-clone-skills:ui-reverse-engineering",
        "args": "https://example.com/",
    })
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "CronCreate" in context
    assert load_receipt(tmp_path, SESSION_ID)["state"] == "pending"


def test_failed_or_unstructured_create_never_activates(tmp_path: Path) -> None:
    create_pending(tmp_path, SESSION_ID, UI_RE_SKILL)
    run_hook(tmp_path, event="PostToolUse", tool="CronCreate", tool_response="created maybe")
    assert load_receipt(tmp_path, SESSION_ID)["state"] == "pending"
```

Also cover direct slash-command near misses as no-ops: prose mentions, `$ui-clone-skills:ui-reverse-engineering`, partial prefixes, and other skills. Cover unrelated skills/crons as no-ops, exact create input validation, CronList recovery, owned/unowned delete, missing required payload fields, and successful delete classification.

- [ ] **Step 2: Run the hook module and record RED**

Run: `uv run python -m pytest tests/hooks/test_claude_continuation.py -q`

Expected: collection fails because the hook module does not exist.

- [ ] **Step 3: Implement one event/tool dispatcher**

Implement `handle(payload: dict[str, object], project_root: Path) -> str | None` as the pure adapter entry and `main() -> None` as the stdin/stdout shell entry.

Read `session_id`, `cwd`, and `hook_event_name` for every event. For `UserPromptSubmit`, read `prompt` and match only the exact-start `/ui-clone-skills:ui-reverse-engineering` token followed by whitespace or end-of-prompt. For tool lifecycle hooks, read `tool_name` and `tool_input`; PostToolUse additionally requires `tool_response` and `tool_use_id`. Emit Claude `hookSpecificOutput.additionalContext` for bootstrap or correction. Validate continuation-owned `CronCreate` and `CronDelete`; do not block or mutate unrelated cron operations. PostToolUse success is the only create/delete transition surface.

- [ ] **Step 4: Run the hook tests and static checks**

```bash
uv run python -m pytest tests/hooks/test_claude_continuation.py -q
uv run ruff check ui_clone/hooks/claude_continuation.py tests/hooks/test_claude_continuation.py
uv run mypy ui_clone/hooks/claude_continuation.py
```

Expected: all commands exit `0`.

### Task 3: Register Claude-only routes without weakening manifest parity

**Files:**
- Modify: `hooks/hooks.json`
- Modify: `tests/test_hook_manifest_parity.py`

- [ ] **Step 1: Add failing parity assertions for the exact routes**

```python
CLAUDE_CONTINUATION_ROUTES = {
    ("UserPromptSubmit", "claude_continuation", None),
    ("PreToolUse", "claude_continuation", "Skill"),
    ("PreToolUse", "claude_continuation", "CronCreate|CronDelete"),
    ("PostToolUse", "claude_continuation", "CronCreate|CronList|CronDelete"),
}
```

Filter these tuples before comparing the existing shared topology, pin the no-matcher `UserPromptSubmit` lifecycle route, retain the tool matcher-intent tokens for the Cron and Skill routes, and assert no continuation route appears in `hooks/codex-hooks.json`.

- [ ] **Step 2: Run the parity test and confirm RED**

Run: `uv run python -m pytest tests/test_hook_manifest_parity.py -q`

Expected: the Claude manifest lacks the required direct prompt route and tool-route tuples.

- [ ] **Step 3: Add the four hook entries to `hooks/hooks.json`**

Every command must use the existing root fallback expression and route through:

```text
bash "$_R/hooks/shim.sh" ui_clone.hooks.claude_continuation
```

Do not modify `hooks/codex-hooks.json`.

- [ ] **Step 4: Re-run parity and JSON validation**

```bash
uv run python -m pytest tests/test_hook_manifest_parity.py -q
python -m json.tool hooks/hooks.json >/dev/null
```

Expected: both commands exit `0`.

### Task 4: Enforce pending leases before pipeline start and bind the ref

**Files:**
- Modify: `ui_clone/hooks/pre_bash_rules/dispatcher.py`
- Modify: `tests/hooks/test_pre_bash.py`

- [ ] **Step 1: Write failing pending/active/paused/control-command tests**

```python
def test_pipeline_start_is_denied_until_pending_lease_activates(tmp_path: Path) -> None:
    create_pending(tmp_path, SESSION_ID, UI_RE_SKILL)
    result = run_pre_bash(tmp_path, pipeline_run_command(), session_id=SESSION_ID)
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("state", ["active", "paused", "unsupported"])
def test_nonpending_receipt_does_not_block_pipeline(tmp_path: Path, state: str) -> None:
    seed_receipt_state(tmp_path, SESSION_ID, state)
    result = run_pre_bash(tmp_path, pipeline_run_command(), session_id=SESSION_ID)
    if result.stdout.strip():
        output = json.loads(result.stdout)
        assert output.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
```

Assert that the five `python -m ui_clone.claude_continuation` control commands remain allowed and that the first resolved pipeline command binds `refDir` using `target_ref_dir_for_ui_re_command`.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `uv run python -m pytest tests/hooks/test_pre_bash.py -k continuation -q`

Expected: pending pipeline execution is not denied and the receipt remains unbound.

- [ ] **Step 3: Add one early continuation guard in `main()`**

After extracting `cmd`, `project_root`, `payload_cwd`, and `session_id`, load only that session's receipt. Permit control CLI commands. Deny UI-RE execution commands when state is `pending`; otherwise bind the first concrete target ref and continue through all existing guards. Do not inspect or block sessions without a receipt.

- [ ] **Step 4: Run focused and full pre-Bash tests**

```bash
uv run python -m pytest tests/hooks/test_pre_bash.py -k continuation -q
uv run python -m pytest tests/hooks/test_pre_bash.py -q
```

Expected: all commands exit `0`.

### Task 5: Reconcile Stop snapshots and preserve the one-block guard

**Files:**
- Modify: `ui_clone/hooks/section_gate.py`
- Modify: `tests/hooks/test_section_gate.py`

- [ ] **Step 1: Add failing Stop payload regressions**

```python
def test_valid_empty_session_crons_pauses_missing_active_job(tmp_path: Path) -> None:
    seed_active_receipt(tmp_path)
    run_stop(tmp_path, {"session_id": SESSION_ID, "session_crons": []})
    assert load_receipt(tmp_path, SESSION_ID)["state"] == "paused"


@pytest.mark.parametrize("payload", [
    {"session_id": SESSION_ID},
    {"session_id": SESSION_ID, "session_crons": "unknown"},
    {"session_id": SESSION_ID, "session_crons": [{"id": 3}]},
])
def test_malformed_or_absent_snapshot_does_not_pause(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    seed_active_receipt(tmp_path)
    run_stop(tmp_path, payload)
    assert load_receipt(tmp_path, SESSION_ID)["state"] == "active"


def test_first_incomplete_stop_without_lease_requires_bootstrap(tmp_path: Path) -> None:
    seed_incomplete_ref(tmp_path, SESSION_ID)
    result = run_stop(tmp_path, {"session_id": SESSION_ID})
    reason = json.loads(result.stdout)["reason"]
    assert "CronCreate" in reason
    assert "continuation lease" in reason


def test_reentrant_stop_still_releases_without_recreating_paused_lease(tmp_path: Path) -> None:
    receipt = seed_paused_receipt(tmp_path)
    payload = {
        "session_id": SESSION_ID,
        "stop_hook_active": True,
        "session_crons": [cron_row("cron-stale", receipt["leaseTag"])],
    }
    result = run_stop(tmp_path, payload)
    assert result.stdout == ""
    assert load_receipt(tmp_path, SESSION_ID)["state"] == "paused"
```

Cover exact one-match confirmation, duplicate failure guidance, unrelated rows, and a paused receipt that never reactivates.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `uv run python -m pytest tests/hooks/test_section_gate.py -k continuation -q`

Expected: no receipt reconciliation or lease bootstrap guidance exists.

- [ ] **Step 3: Integrate reconciliation after payload parsing**

Keep `stop_hook_active` as an immediate release. For a normal Stop, reconcile a valid `session_crons` snapshot before finding active refs. Prefix the existing first incomplete block with lease bootstrap guidance when there is no receipt or it remains pending. Never repair `paused`, never infer an empty snapshot from absent/malformed data, and never change canonical gate results.

- [ ] **Step 4: Run focused and full Stop tests**

```bash
uv run python -m pytest tests/hooks/test_section_gate.py -k continuation -q
uv run python -m pytest tests/hooks/test_section_gate.py -q
```

Expected: all commands exit `0`.

### Task 5A: Keep recoverable verify failures in active rework

**Files:**
- Modify: `ui_clone/pipeline_phases/verify.py`
- Modify: `ui_clone/claude_continuation.py`
- Modify: `ui_clone/hooks/section_gate.py`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_claude_continuation.py`
- Modify: `tests/hooks/test_section_gate.py`

- [ ] Reproduce that `canonical-verify-failed` releases Stop and terminalizes a
  Claude receipt after the first fixable gate failure.
- [ ] Stop writing `canonical-verify-failed` as terminal lifecycle state. Preserve
  `verify-report.json`, gate logs, non-zero status, and success-only stamp behavior.
- [ ] Treat legacy `failed/canonical-verify-failed` state as recoverable in Stop
  and continuation classification. Preserve hard-cap/unclonable terminalization.
- [ ] Run focused pipeline, continuation, goal, state, and Stop regressions.

### Task 5B: Detect stale transition reference-frame inventories early

**Files:**
- Modify: `ui_clone/check_inputs.py`
- Modify: `skills/visual-debug/scripts/capture-artifact-inventory-check.sh`
- Modify: `tests/test_capture_artifact_inventory.py`
- Modify: `tests/gates/test_check_inputs.py`

- [ ] Reproduce that renaming `static/ref/*.png` neither changes the compact
  required-check hash nor fails dispatch-only inventory validation.
- [ ] Validate transition-spec reference frame paths and include only the frame
  directories read by that validator in the check fingerprint.

### Task 5C: Protect generated plan provenance from manual rewrites

**Files:**
- Modify: `ui_clone/hooks/pre_bash_rules/bash_write.py`
- Modify: `tests/hooks/test_pre_bash.py`

- [ ] Deny direct natural-tool writes/removals of the exact
  `generation-plan.json` basename while allowing the canonical generation script
  and unrelated filenames.

### Task 6: Release, review, and verify the implementation

**Files:**
- Modify: `.claude-plugin/plugin.json`
- Modify: `.claude-plugin/marketplace.json`
- Modify: `.codex-plugin/plugin.json`
- Modify: `pyproject.toml`
- Modify: `package.json`
- Modify: `ui_clone/__init__.py`
- Review: all files changed in Tasks 1-5

- [ ] **Step 1: Bump all six synchronized versions to one fresh cache version**

Run: `bash scripts/ci/pre-push-security.sh`

Expected: all six authoritative version fields match the same new version so Claude installs a fresh per-version cache.

- [ ] **Step 2: Run the focused implementation suite**

```bash
uv run python -m pytest \
  tests/test_claude_continuation.py \
  tests/hooks/test_claude_continuation.py \
  tests/hooks/test_pre_bash.py \
  tests/hooks/test_section_gate.py \
  tests/test_hook_manifest_parity.py -q
uv run ruff check ui_clone/claude_continuation.py ui_clone/hooks/claude_continuation.py tests/test_claude_continuation.py tests/hooks/test_claude_continuation.py
uv run mypy ui_clone/claude_continuation.py ui_clone/hooks/claude_continuation.py
git diff --check
```

Expected: all commands exit `0`.

- [ ] **Step 3: Run an independent code review and reproduce any blocker before editing**

Review state-machine transitions, tool-response ambiguity, path validation, paused-state reactivation, Codex isolation, and Stop-hook churn. Fix only reproduced findings and rerun the affected tests.

- [ ] **Step 4: Run the repository-required gates**

```bash
bash scripts/ci/ci-local.sh > /tmp/ui-clone-continuation-ci.log 2>&1
bash scripts/ci/pre-push-security.sh > /tmp/ui-clone-continuation-security.log 2>&1
tail -40 /tmp/ui-clone-continuation-ci.log
tail -40 /tmp/ui-clone-continuation-security.log
```

Expected: CI reports zero failures and security reports zero blockers. Preserve the unrelated dirty `emit_scroll_helpers` pair.

- [ ] **Step 5: Commit only the intended implementation with a Lore message**

Stage the continuation modules, tests, manifests, ignore rule, version files, and this plan. Do not stage `skills/visual-debug/scripts/lib/emit_scroll_helpers.py` or `tests/test_emit_scroll_helpers.py`.

### Task 7: Install and verify the local plugin outside the checkout

**Files:**
- Runtime install surfaces only; no source edits.

- [ ] **Step 1: Install the verified worktree for both hosts**

Run: `./install.sh --yes --no-deps`

Expected: Claude cache delivery, Claude plugin enablement, Codex projection, Codex hook merge, and the hook delivery probe all pass for the freshly synchronized version.

- [ ] **Step 2: Verify delivery by content, not plugin-list presence alone**

```bash
claude plugin list | rg 'ui-clone-skills@voidmatcha'
codex plugin list | rg 'ui-clone-skills@local.*installed'
CLAUDE_UI_RE_VERSION="$(python - <<'PY'
import tomllib
from pathlib import Path
print(tomllib.loads(Path('pyproject.toml').read_text())['project']['version'])
PY
)"
rg -n 'UserPromptSubmit|CronCreate\|CronDelete|CronCreate\|CronList\|CronDelete' \
  "$HOME/.claude/plugins/cache/voidmatcha/ui-clone-skills/$CLAUDE_UI_RE_VERSION/hooks/hooks.json"
test -f "$HOME/.claude/plugins/cache/voidmatcha/ui-clone-skills/$CLAUDE_UI_RE_VERSION/ui_clone/claude_continuation.py"
```

Expected: the installed Claude cache contains the exact direct prompt route, Cron routes, and core module.

### Task 8: Recoverably clean the old runs and launch fresh Purplemux sessions

**Files:**
- Recoverable runtime cleanup and Purplemux state only; no source edits.

- [ ] **Step 1: Re-resolve live ownership before cleanup**

List Purplemux tabs and processes, then prove the old Claude and Codex tabs point
at their respective `<site>-<host>-<date>` run directories. Close only those two
exact tabs; do not touch unrelated Purplemux tabs.

- [ ] **Step 2: Move only validated old temporary clone artifacts to Trash**

After checking each resolved path is neither empty nor broad, use `/usr/bin/trash`
for the exact old Claude and Codex `tmp` directories. Skip any legacy scratch
reference unless current ownership and timestamps still prove that it belongs to
one of these runs.

```text
<run-root>/<site>-claude-<date>/tmp
<run-root>/<site>-codex-<date>/tmp
<legacy-scratch-root>/ref/<site>
```

Skip the third path unless current ownership/timestamps still identify it as one
of the old site artifacts. Verify each selected path is absent afterward and
report that it is recoverable from Trash.

- [ ] **Step 3: Create unique external run directories and Purplemux tabs**

Use the selected Purplemux workspace, two unique external run directories, and
new terminal tabs named `<site>-claude-continuation` and
`<site>-codex-continuation`.

- [ ] **Step 4: Start ordinary interactive Claude and Codex with clean skill requests**

Start exactly `claude`, wait for its input prompt, then send only:

```text
/ui-clone-skills:ui-reverse-engineering https://example.com/
```

Do not use `-p`, `--resume`, a handoff prompt, cmux, or synthetic supervisor instructions.

Start exactly `codex --yolo` in the Codex run directory, wait for its input
prompt, then send only:

```text
$ui-clone-skills:ui-reverse-engineering https://example.com/
```

Do not use cmux, `codex exec`, a handoff prompt, or a synthetic supervisor.

- [ ] **Step 5: Verify fresh activation and continuation bootstrap**

Capture both tab outputs. Assert both plain skill invocations are visible and both
fresh run directories create a new `tmp/ref`; additionally prove the Claude
transcript records the slash command as `UserPromptSubmit` command content, exactly
one Claude Cron lease exists, and an `active`
`.ui-re-continuation/<session-id>.json` exists. Stop monitoring once these
start-of-run receipts are proven; canonical clone completion remains owned by the
launched interactive sessions.
