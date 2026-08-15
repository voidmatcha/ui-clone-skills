# Claude Interactive Continuation Lease

## Problem

UI reverse-engineering runs are long, gate-driven tasks. Codex currently tends to keep one active task open until the run reaches its terminal objective, but Claude Code can finish an assistant turn while the UI-clone pipeline is still incomplete. The existing `Stop` hook can block that first premature stop and return an actionable reason. On the resulting continuation, Claude Code sends `stop_hook_active: true`; blocking again can create repeated Stop-hook churn and eventually hits the host's consecutive-block cap. The hook therefore releases the second stop, which can leave an ordinary interactive Claude session idle with no valid `verify-stamp.json`.

The two hosts must not be forced into one supervisor model. The current Codex Realfood run uses one long active task and the repository's `goal --check-done` completion contract; it does not need another wake-up loop. Claude needs only a session-native way to enqueue another turn after it becomes idle.

## Decision

Add a Claude Code-only, session-scoped continuation lease backed by Claude's `CronCreate`, `CronList`, and `CronDelete` tools. The lease is a wake-up mechanism, not a second goal system. Every scheduled turn delegates completion and next-step decisions to the existing UI-clone CLI:

- `python -m ui_clone.goal <ref-dir> --check-done`
- `python -m ui_clone.pipeline ... status --json`
- `python -m ui_clone.pipeline ... next --json`
- `python -m ui_clone.pipeline ... report --for-llm`

Codex receives no scheduler or host-specific driver. The shared Stop lifecycle
still changes so a recoverable verify failure cannot release either host early.
Display backends remain unchanged.

## Goals

1. Preserve the normal interactive `claude` TUI as the default user experience.
2. Re-enter the same Claude session after a premature incomplete turn without `-p`, TTY key injection, Purplemux, or cmux control.
3. Use the canonical UI-clone goal and verify stamp as the only completion authority.
4. Avoid concurrent turns, duplicate scheduled jobs, unbounded Stop-hook blocks, and infinite no-progress loops.
5. Allow an explicit user pause and stop automatically on completion or an
   authoritative abort, while keeping ordinary canonical verify failures in
   active rework.

## Non-goals

- Do not add a universal agent supervisor.
- Do not add a Codex-specific supervisor. The shared lifecycle fix may prevent
  Codex from treating a recoverable verify failure as a terminal Stop release.
- Do not keep work running after the Claude session is closed. A background session is a separate future mode.
- Do not make Purplemux or cmux decide pipeline state.
- Do not put Claude-specific execution instructions in the shared public skill documents.
- Do not treat a scheduled task, assistant summary, build, preview URL, or HTTP response as completion evidence.

## Terminology

- **Domain goal**: the repository-owned `ui_clone.goal` completion contract. This remains the source of truth for both hosts.
- **Continuation lease**: one recurring, session-scoped Claude scheduled task that wakes an idle interactive session and checks the domain goal.
- **Lease tag**: a stable identifier derived from the Claude session ID and project-local run identity. It is included in the scheduled prompt and used for idempotent lookup and deletion.
- **Wake-up turn**: the user-like scheduled prompt that Claude Code enqueues only when the session is idle.

## Architecture

```text
interactive Claude session
        |
        | invokes ui-reverse-engineering Skill
        v
Claude-only hook adapter
        |
        | requires one session continuation lease
        v
CronCreate(recurring, session-scoped, non-durable)
        |
        | fires only when the session is idle
        v
UI-clone goal oracle
   |            |                 |
   | done       | incomplete      | terminal / authority required
   v            v                 v
CronDelete   status/next/report  CronDelete
complete     execute next work   report blocker
```

The display layer only launches and displays the ordinary Claude process. Purplemux, cmux, tmux, and a plain terminal all behave the same because none of them receives pipeline decisions or synthetic keystrokes.

## Host Boundary

The shared skill and pipeline continue to define *what* constitutes progress and completion. Claude-specific behavior lives behind Claude's hook manifest:

1. `hooks/hooks.json` registers Claude-only hook events for direct prompt submission, `Skill`, `CronCreate`, `CronList`, and `CronDelete` lifecycles.
2. A Claude continuation hook module owns lease bootstrap, validation, and operational receipts.
3. Existing shared gates continue to run for Claude and Codex. The Codex manifest receives no continuation hooks.
4. `section_gate.py` may read Claude's `session_crons` payload as an optional capability signal, but its canonical gate decisions remain host-neutral.

This keeps the three public skills equivalent across hosts while allowing the Claude host adapter to compensate for Claude's turn boundary.

## Proposed Change Surface

The implementation is intentionally limited to the Claude host adapter and operational state around it:

- `hooks/hooks.json`: add the Claude-only direct prompt, `Skill`, and Cron lifecycle routes.
- `.gitignore`: ignore `.ui-re-continuation/` operational receipts.
- `ui_clone/claude_continuation.py`: own receipt validation, atomic storage, state transitions, prompt construction, and the operator CLI.
- `ui_clone/hooks/claude_continuation.py`: adapt Claude hook payloads to the core state machine.
- `ui_clone/hooks/pre_bash_rules/dispatcher.py`: enforce the pending-lease guard and bind the first resolved ref directory.
- `ui_clone/hooks/section_gate.py`: reconcile an incomplete Stop payload with an optional, valid `session_crons` snapshot.
- `tests/test_claude_continuation.py` and `tests/hooks/test_claude_continuation.py`: cover the core state machine and Claude payload adapter.
- `tests/test_hook_manifest_parity.py`: ratchet the new routes as an explicit Claude-only tool surface.

`hooks/codex-hooks.json`, the public skill documents, the domain goal, and pipeline completion semantics do not change.

## Hook Bindings and Payload Contracts

One hook module, `ui_clone.hooks.claude_continuation`, dispatches by `hook_event_name` and `tool_name`. `hooks/hooks.json` adds exactly these entries:

| Hook event | Matcher | Responsibility |
| --- | --- | --- |
| `UserPromptSubmit` | none | Detect a direct slash-command invocation of the exact UI reverse-engineering skill and create an idempotent pending receipt before Claude decides whether to call a tool. |
| `PreToolUse` | `Skill` | Fallback for programmatic skill-tool invocations that expose the exact UI reverse-engineering skill identity. |
| `PreToolUse` | `CronCreate|CronDelete` | Validate only continuation-owned create/delete operations; unrelated cron operations are no-ops. |
| `PostToolUse` | `CronCreate|CronList|CronDelete` | Reconcile successful tool results with the receipt state machine. |

The adapter reads the documented common fields `session_id`, `cwd`, and `hook_event_name`. `UserPromptSubmit` additionally reads `prompt`; missing or non-string prompt content is a no-op that creates no receipt. Tool lifecycle hooks read `tool_name` and `tool_input`; `PostToolUse` additionally requires `tool_response` and `tool_use_id`. A missing or wrong-typed common or required tool field produces no state transition and returns narrow corrective context; it never fabricates success.

The accepted continuation `CronCreate` input is exact:

```json
{
  "cron": "* * * * *",
  "prompt": "<immutable continuation prompt containing the exact lease tag>",
  "recurring": true,
  "durable": false
}
```

`CronDelete` accepts a continuation transition only when `tool_input.id` is a validated string equal to the receipt's `cronId`. Other deletions remain outside this feature. `CronList` is not bound on `PreToolUse` because it is read-only; its successful `PostToolUse` output is used only for reconciliation.

`PostToolUse` runs only after a successful tool call. A failed create therefore leaves `pending`; a failed delete leaves the prior state unchanged and the normal tool error remains visible to Claude. Cron IDs are never recovered with a free-form text regex. The result normalizer accepts only:

1. `tool_response.id` as a validated cron ID,
2. `tool_response.cron.id` as a validated cron ID, or
3. exactly one structured CronList row from a top-level response list or `tool_response.crons` list whose complete prompt contains the exact delimited lease tag.

If `CronCreate` returns no supported structured ID, the receipt remains `pending` and additional context requires one `CronList` call. If that list also cannot yield exactly one matching structured row, registration fails closed and the adapter instructs Claude to mark the capability `unsupported`; pipeline work does not silently proceed under an assumed lease.

The manifest parity test separates shared enforcement routes from this intentional host adapter. It adds an exact `CLAUDE_CONTINUATION_ROUTES` tuple set for the event, module, and matcher triples above, adds the continuation module's matcher-intent tokens, filters those tuples before comparing the existing shared topology, and asserts that the Codex manifest contains none of them. `UserPromptSubmit` is a Claude-only lifecycle event for this adapter and is registered only in `hooks/hooks.json`.

## Lease Bootstrap

### Primary path: direct slash-command invocation

Live Claude Code sessions deliver a direct slash command as a `UserPromptSubmit` event, not necessarily as a `PreToolUse:Skill` call. The primary Claude activation route therefore inspects the submitted prompt before tool selection. It matches only an exact start token:

```text
/ui-clone-skills:ui-reverse-engineering
```

The token must be followed by whitespace or the end of the prompt. Prose mentions, shell-style `$ui-clone-skills:ui-reverse-engineering`, partial prefixes, and other skill names are no-ops. A match creates a pending lease receipt scoped to the current session and adds context requiring Claude to establish the continuation lease before substantive pipeline work.

### Fallback path: Skill tool invocation

Some Claude or compatibility surfaces may still call the `Skill` tool with `skill: "ui-clone-skills:ui-reverse-engineering"`. A Claude-only `PreToolUse:Skill` hook keeps that path idempotent and uses the same receipt bootstrap logic. It is not the primary route for ordinary interactive slash commands.

The required bootstrap sequence is:

1. Resolve `CronCreate` if it is deferred.
2. Create exactly one recurring one-minute scheduled prompt with the generated lease tag.
3. Keep the task session-scoped and non-durable.
4. Let the Claude-only `PostToolUse:CronCreate` hook record the returned cron ID and mark the pending receipt active.
5. Proceed with the normal UI reverse-engineering skill.

A pre-Bash guard blocks the first pipeline-start command while a supported Claude session still has a pending, unregistered lease. It permits an explicit `unsupported` receipt when the host genuinely lacks scheduled-task tools; unsupported hosts retain the current one-nudge Stop behavior instead of entering a broken enforcement loop.

The guard permits the receipt control CLI itself and does not apply to `active`, `paused`, `complete`, `terminal`, or `unsupported` receipts. A fresh direct slash command or fallback Skill-tool invocation may replace `paused` with a new `pending` lease only because that invocation is an explicit user request.

### Fallback path: active run without a lease

Natural-language or legacy activation may reach an active UI-clone run without the Skill hook receipt. On the first incomplete `Stop` event, `section_gate.py` checks the optional `session_crons` list. If no matching lease exists, the block reason first requires lease creation, then gives the normal gate failure and next action. This is a recovery path, not the primary bootstrap.

When `stop_hook_active` is true, the hook still releases the stop. It never uses repeated blocking as the continuation mechanism.

## Operational Receipt

The lease needs a small operational receipt because cron creation is a host tool action that the pipeline cannot otherwise verify. Store it at `.ui-re-continuation/<session-id>.json` under the project root, then bind its `refDir` field when the first pipeline command resolves the ref directory. The runtime directory is ignored by Git and never participates in clone-source or verification fingerprints.

```json
{
  "schemaVersion": 1,
  "host": "claude-code",
  "sessionId": "fe87fc97-d23a-496e-b13a-5ca5ab651f0d",
  "skill": "ui-clone-skills:ui-reverse-engineering",
  "state": "active",
  "leaseTag": "UI_RE_CONTINUATION:<opaque-run-id>",
  "cronId": "cron-opaque-id",
  "refDir": "tmp/ref/<component>",
  "createdAt": "2026-08-15T00:00:00Z",
  "updatedAt": "2026-08-15T00:00:00Z"
}
```

Allowed states are `pending`, `active`, `paused`, `complete`, `terminal`, and `unsupported`. Writes are atomic. The receipt is operational evidence only: it cannot make an incomplete pipeline complete and is not part of the canonical verify-stamp fingerprint.

The session ID is validated as a UUID or a conservative `[A-Za-z0-9._-]` token before it becomes a filename. `ui_clone.claude_continuation` writes a same-directory temporary file with mode `0600`, flushes it, and replaces the receipt with `os.replace`; readers reject invalid JSON, a mismatched `sessionId`, unknown states, and path traversal. The `.ui-re-continuation/` directory is Git-ignored and excluded from verification input discovery.

Hooks call the core functions directly. The matching operator and test surface is:

```text
python -m ui_clone.claude_continuation create-pending --session-id ID --cwd DIR --skill NAME
python -m ui_clone.claude_continuation bind-ref --session-id ID --cwd DIR --ref-dir REF
python -m ui_clone.claude_continuation mark-unsupported --session-id ID --cwd DIR --reason TEXT
python -m ui_clone.claude_continuation pause --session-id ID --cwd DIR
python -m ui_clone.claude_continuation status --session-id ID --cwd DIR --json
```

`pause` is receipt-only repair and does not pretend to delete a scheduler job. The normal interactive pause sequence is `CronDelete` followed by its successful PostToolUse transition. If a still-present job encounters a `paused` receipt, its prompt must delete itself without performing pipeline work. No CLI verb can set `complete`; only a successful canonical `goal --check-done` evaluation can do that.

State transitions are deliberately narrow:

| Trigger | Allowed current state | Next state |
| --- | --- | --- |
| Matching `UserPromptSubmit` slash command or fallback `PreToolUse:Skill` | no receipt | `pending` |
| Matching `UserPromptSubmit` slash command or fallback `PreToolUse:Skill` | `active` | unchanged |
| Matching `UserPromptSubmit` slash command or fallback `PreToolUse:Skill` | `paused` | `pending`, because the invocation is explicit user intent |
| Successful `CronCreate` or one exact CronList match | `pending` | `active` with the validated cron ID |
| Successful owned `CronDelete` and canonical goal passes | `active` or `pending` | `complete` |
| Successful owned `CronDelete` and `goal --check-done` aborts (`2`) | `active` or `pending` | `terminal` |
| Successful owned `CronDelete` while incomplete | `active` or `pending` | `paused` |
| Valid cron snapshot proves the owned job absent | `active` | `paused` |
| `mark-unsupported` with a recorded capability reason | `pending` | `unsupported` |

`complete` and `terminal` are immutable within the session. A `paused` receipt is never reactivated by list reconciliation, Stop fallback, or a scheduled prompt. Invalid transitions are rejected and leave the prior receipt intact.

The lease tag and prompt must not interpolate arbitrary user text. They use validated session and project identifiers so a URL or prompt cannot inject scheduled instructions.

## Cron Snapshot Reconciliation

The Stop hook may receive the documented optional snapshot shape:

```json
{
  "session_crons": [
    {
      "id": "cron-001",
      "schedule": "* * * * *",
      "recurring": true,
      "prompt": "... [[UI_RE_CONTINUATION:<opaque-run-id>]] ..."
    }
  ]
}
```

Absent `session_crons` and a non-list value mean the capability is unavailable, not an empty list. An actual empty list is a valid snapshot proving that no session cron exists. Rows missing a validated `id`, string `schedule`, boolean `recurring`, or string `prompt` are malformed and ignored; a non-empty snapshot in which every row is malformed is unavailable rather than proof of absence. A matching row must have `recurring: true`, the expected one-minute schedule, and either the receipt's exact `cronId` or the full delimited tag `[[UI_RE_CONTINUATION:<opaque-run-id>]]` in its prompt. Arbitrary prompt substrings never establish ownership.

One exact matching row can activate a pending receipt or confirm an active one. Multiple matching rows are a duplicate-lease failure and are never collapsed by choosing one silently. With a valid snapshot, an `active` receipt whose job is absent becomes `paused`; this treats UI deletion as user intent and prevents automatic recreation. A `paused` receipt never becomes active through reconciliation, even if a stale job is still visible: that job must be deleted. An unavailable snapshot causes no state change.

## Scheduled Prompt Contract

Every wake-up prompt follows the same deterministic contract:

1. Identify itself using the lease tag.
2. Resolve exactly one active ref directory bound to the current session. Zero or multiple matches fail closed.
3. Run `goal --check-done` before doing any new work.
4. If complete, delete the matching cron job, mark the receipt `complete`, and report canonical completion evidence.
5. If `goal --check-done` returns `2` for a hard-cap/unclonable abort, delete the
   job, mark the receipt `terminal`, and report the terminal cause. A normal
   canonical verify failure returns `1`, remains incomplete, and must continue.
6. If user authority, credentials, or an irreversible decision are required, delete the job, mark it `paused`, and state the blocker.
7. If incomplete, read `status --json`, `next --json`, and `report --for-llm`, then execute the next required action. A status-only summary is not a valid scheduled turn.
8. Preserve all normal gates, fail counts, iteration caps, and verification requirements. The lease never bypasses or resets them.

The one-minute schedule is a wake-up latency, not a polling load. Claude scheduled tasks run only while the session is active and idle. If a due time passes while Claude is busy, the host enqueues one run when it next becomes idle rather than building a backlog.

## Stop-hook Interaction

The Stop hook retains its existing safety role:

- First incomplete stop: block once with the concrete gate reason and next action. If the lease is missing, require bootstrap in the same reason.
- Continuation-generated stop (`stop_hook_active: true`): release immediately to avoid consecutive-block churn.
- Idle session with active lease: the next scheduled prompt re-enters the same session and checks the domain goal.
- Completed or authoritative-abort run: allow stop and ensure the lease is removed.

The lease therefore owns *when to wake up*; the Stop hook and pipeline own *whether stopping is valid*.

## Pause, Resume, and Session Lifecycle

- The user can pause through the normal Claude task UI or a small continuation control command. `CronDelete` marks an incomplete lease `paused`, preventing automatic recreation.
- A paused lease resumes only through an explicit user request or a fresh skill invocation.
- Closing Claude stops session-scoped scheduled execution. No external daemon remains.
- Resuming the same Claude session may restore its unexpired scheduled task. `SessionStart` and `session_resume.py` re-bind the WIP context. If a valid cron snapshot proves the receipt's active job is missing, the receipt becomes `paused`; repair requires the next explicit continuation or fresh skill invocation.
- Starting a new conversation does not inherit the old lease automatically. The new session must deliberately adopt the active ref.
- User interrupts are respected. Stop hooks do not run on an interrupt, and a documented pause action must remove the scheduled job if the user wants the still-open session to remain idle.

## Failure Handling

The design fails closed without trapping the user:

- Missing `CronCreate`: record `unsupported`, retain existing Stop semantics, and explain that automatic interactive continuation is unavailable.
- Malformed or duplicate cron: reject registration and require deletion of every continuation-owned duplicate before an explicit recreation; never choose one job silently.
- Missing or ambiguous ref binding: delete the lease and report the ambiguity instead of choosing a run.
- Stale cron ID: reconcile with `CronList`. If a valid list proves the job is absent, mark the receipt `paused` and require explicit reactivation; do not guess whether the host lost the job or the user deleted it intentionally.
- Canonical verify failure: keep `verify-report.json`, logs, and the non-zero
  command result, but do not mint a Stop-releasing terminal state. Continue from
  the current gate and surface the report through `status` / `next` /
  `report --for-llm`.
- Repeated gate failure: rely on existing pipeline fail counts and hard caps.
  Only the existing hard-cap/unclonable path becomes terminal; do not add a
  second retry counter.
- Session close or process crash: no background continuation. Resume remains an explicit session action.

## Alternatives Rejected

### External `claude -p --resume` driver

This is suitable for CI and headless harnesses, but it replaces the normal interactive TUI and is not the default user workflow.

### Repeated Stop-hook blocks

Claude enforces a consecutive Stop-block cap, and this repository already observed hours of churn from repeated blocking. The re-entrancy guard remains.

### Purplemux or cmux key injection

Synthetic input couples correctness to a display backend, focus, pane identity, and terminal timing. Display backends do not decide pipeline progress.

### New universal goal service

The repository already has the canonical domain goal. Codex currently continues within one active task without a separate goal-tool record. Adding another cross-host goal layer would duplicate state without solving the Claude idle-turn boundary.

### Background Claude session by default

Background sessions are useful when the terminal may close, but they change ownership and visibility. The approved default is continuation only while the user's interactive session remains open.

## Verification

### Unit and contract tests

1. The Claude `UserPromptSubmit` hook matches only an exact-start `/ui-clone-skills:ui-reverse-engineering` slash command and does not affect prose mentions, `$` Codex syntax, partial prefixes, or unrelated skills.
2. The fallback Claude `PreToolUse:Skill` hook matches only `ui-clone-skills:ui-reverse-engineering` and does not affect unrelated skills.
3. Lease registration is idempotent by session and tag; duplicate matching jobs fail closed until all are deleted and one is explicitly recreated.
4. Pipeline start is blocked for `pending` supported leases and allowed for `active`, `paused`, or explicitly `unsupported` receipts according to policy.
5. A Stop payload with a matching `session_crons` entry does not request a duplicate lease.
6. A first incomplete Stop without a lease includes bootstrap guidance; `stop_hook_active: true` still releases.
7. `CronDelete` produces `complete` only when `goal --check-done` passes, `terminal`
   only when it returns abort (`2`), and `paused` for an explicit incomplete delete.
8. A canonical verify failure leaves the lease active, blocks a normal Stop, and
   remains discoverable through `verify-report.json`; a hard-cap/unclonable
   failure still terminalizes.
9. Claude-only manifest changes do not alter the Codex hook manifest or shared gate routing.
10. Receipt paths, tags, and prompt arguments reject traversal and arbitrary prompt interpolation.
11. Absent, malformed, empty, one-match, no-match, and duplicate `session_crons` snapshots exercise their distinct unavailable, absent, active, paused, and failure outcomes.
12. Supported and unsupported `tool_response` shapes prove that no free-form cron ID is inferred and no failed tool call advances state.

### Interactive acceptance

1. Start ordinary interactive `claude` in a fresh fixture directory.
2. Invoke `/ui-clone-skills:ui-reverse-engineering <fixture-url>`.
3. Verify exactly one tagged session cron and one active receipt exist before pipeline work advances.
4. Force an incomplete assistant stop and observe the existing one-time Stop block.
5. Allow the next stop; within one schedule interval, verify a scheduled prompt runs in the same session ID.
6. Verify the wake-up turn reads the pipeline CLI and executes the reported next action.
7. Make the fixture canonically complete; verify the cron is deleted and the receipt becomes `complete`.
8. Pause an incomplete fixture; verify no later wake-up occurs.
9. Run the existing Codex hook parity and pipeline suites to prove Codex behavior is unchanged.

## Compatibility

- No new dependency is introduced.
- Existing Claude versions without scheduled-task tools keep the current one-nudge behavior with an explicit unsupported receipt.
- Existing headless benchmark and `-p` paths remain separate and unchanged.
- Existing pipeline state, gate failures, and verify-stamp semantics remain authoritative.
- Existing Purplemux, cmux, and plain-terminal launch commands continue to work because the continuation lease lives inside the Claude session.

## References

- [Claude Code hooks](https://code.claude.com/docs/en/hooks.md): `Stop`, `stop_hook_active`, tool lifecycle hooks, and `session_crons` payload semantics.
- [Claude Code scheduled tasks](https://code.claude.com/docs/en/scheduled-tasks.md): session-scoped `/loop` and cron execution while the session is active and idle.

## Stop Condition

Implementation is complete only when ordinary interactive Claude can invoke the
skill, establish exactly one session-scoped continuation lease, survive an
incomplete turn boundary, resume the same session while idle, continue after a
recoverable canonical verify failure, and remove the lease on canonical
completion, hard-cap/unclonable abort, or explicit pause. An ordinary interactive
Codex run must likewise remain blocked from ending after a recoverable verify
failure. Repository CI and security gates must pass.
