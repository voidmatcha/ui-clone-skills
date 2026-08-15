# Claude Stop-Triggered Continuation Lease

## Problem

UI reverse-engineering runs are long, gate-driven tasks. Codex currently tends to keep one active task open until the run reaches its terminal objective, but Claude Code can finish an assistant turn while the UI-clone pipeline is still incomplete. The existing `Stop` hook can block that first premature stop and return an actionable reason. On the resulting continuation, Claude Code sends `stop_hook_active: true`; blocking again can create repeated Stop-hook churn and eventually hits the host's consecutive-block cap. The hook therefore releases the second stop, which can leave an ordinary interactive Claude session idle with no valid `verify-stamp.json`.

The two hosts must not be forced into one supervisor model. The current Codex Realfood run uses one long active task and the repository's `goal --check-done` completion contract; it does not need another wake-up loop. Claude needs only a session-native way to enqueue another turn after it actually crosses an incomplete Stop boundary.

The first implementation armed a recurring one-minute task when the skill was invoked. Live acceptance disproved its key assumption: Claude Code can consider the model idle while a background Bash pipeline is still alive, so the recurring task injected visible scheduled turns during legitimate work. The ownership checks prevented duplicate pipeline commands, but they could not prevent wake churn because the cron already existed. Increasing the interval would only reduce the symptom. The lease must therefore be edge-triggered by an incomplete Stop, not level-triggered by elapsed time throughout the run.

## Decision

Add a Claude Code-only, session-scoped continuation lease backed by Claude's `CronCreate`, `CronList`, and `CronDelete` tools. Skill activation records a running receipt but creates no scheduled task. Only the first incomplete `Stop` arms one non-durable, non-recurring task for the next minute boundary. The one-shot auto-deletes when it fires, and its exact tagged `UserPromptSubmit` event moves the receipt back to running before any pipeline action. The lease is a turn-boundary wake-up mechanism, not a poller and not a second goal system. Every wake-up turn delegates completion and next-step decisions to the existing UI-clone CLI:

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
6. Keep zero scheduled continuation jobs while Claude is actively working, including while a foreground or background pipeline owner is still attached to the current turn.

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
- **Continuation lease**: one session receipt plus, only after an incomplete Stop, at most one session-scoped one-shot Claude scheduled task.
- **Lease tag**: a stable identifier derived from the Claude session ID and project-local run identity. It is included in the scheduled prompt and used for idempotent lookup and deletion.
- **Wake-up turn**: the user-like prompt produced by the armed one-shot after the preceding assistant turn has ended.

## Architecture

```text
interactive Claude session
        |
        | invokes ui-reverse-engineering Skill
        v
Claude-only hook adapter
        |
        | records running receipt; creates no cron
        v
normal pipeline work
        |
        | incomplete Stop
        v
Stop hook blocks once and requests exact CronCreate
        |
        | recurring=false, session-scoped, non-durable
        v
armed one-shot -> current turn ends -> one-shot fires and auto-deletes
        |
        | exact tagged UserPromptSubmit transitions armed -> running
        v
UI-clone goal oracle -> status/next/report -> normal pipeline work
```

The display layer only launches and displays the ordinary Claude process. Purplemux, cmux, tmux, and a plain terminal all behave the same because none of them receives pipeline decisions or synthetic keystrokes.

## Host Boundary

The shared skill and pipeline continue to define *what* constitutes progress and completion. Claude-specific behavior lives behind Claude's hook manifest:

1. `hooks/hooks.json` registers Claude-only hook events for direct prompt submission, `Skill`, `CronCreate`, `CronList`, and `CronDelete` lifecycles.
2. A Claude continuation hook module owns run activation, Stop-triggered arming, wake validation, and operational receipts.
3. Existing shared gates continue to run for Claude and Codex. The Codex manifest receives no continuation hooks.
4. `section_gate.py` may read Claude's `session_crons` payload as an optional capability signal, but its canonical gate decisions remain host-neutral.

This keeps the three public skills equivalent across hosts while allowing the Claude host adapter to compensate for Claude's turn boundary.

## Proposed Change Surface

The implementation is intentionally limited to the Claude host adapter and operational state around it:

- `hooks/hooks.json`: add the Claude-only direct prompt, `Skill`, and Cron lifecycle routes.
- `.gitignore`: ignore `.ui-re-continuation/` operational receipts.
- `ui_clone/claude_continuation.py`: own receipt validation, atomic storage, state transitions, prompt construction, and the operator CLI.
- `ui_clone/hooks/claude_continuation.py`: adapt Claude hook payloads to the core state machine.
- `ui_clone/hooks/pre_bash_rules/dispatcher.py`: bind the first resolved ref directory and prevent substantive work while a Stop-triggered create or manual-resume delete is unresolved.
- `ui_clone/hooks/section_gate.py`: classify incomplete Stop boundaries, request exactly one one-shot, and reconcile an optional, valid `session_crons` snapshot.
- `tests/test_claude_continuation.py` and `tests/hooks/test_claude_continuation.py`: cover the core state machine and Claude payload adapter.
- `tests/test_hook_manifest_parity.py`: ratchet the new routes as an explicit Claude-only tool surface.

`hooks/codex-hooks.json`, the public skill documents, the domain goal, and pipeline completion semantics do not change.

## Hook Bindings and Payload Contracts

One hook module, `ui_clone.hooks.claude_continuation`, dispatches by `hook_event_name` and `tool_name`. `hooks/hooks.json` adds exactly these entries:

| Hook event | Matcher | Responsibility |
| --- | --- | --- |
| `UserPromptSubmit` | none | Detect the exact UI reverse-engineering slash command, the exact tagged one-shot wake prompt, or a manual prompt that arrives while a one-shot is armed. |
| `PreToolUse` | `Skill` | Fallback for programmatic skill-tool invocations that expose the exact UI reverse-engineering skill identity. |
| `PreToolUse` | `CronCreate|CronDelete` | Validate only the exact Stop-triggered one-shot create or an owned manual-resume delete; unrelated cron operations are no-ops. |
| `PostToolUse` | `CronCreate|CronList|CronDelete` | Reconcile successful tool results with the receipt state machine. |

The adapter reads the documented common fields `session_id`, `cwd`, and `hook_event_name`. `UserPromptSubmit` additionally reads `prompt`; missing or non-string prompt content is a no-op that creates no receipt. Tool lifecycle hooks read `tool_name` and `tool_input`; `PostToolUse` additionally requires `tool_response` and `tool_use_id`. A missing or wrong-typed common or required tool field produces no state transition and returns narrow corrective context; it never fabricates success.

The accepted continuation `CronCreate` input is exact and may be issued only while the receipt is `arming`:

```json
{
  "cron": "* * * * *",
  "prompt": "<immutable continuation prompt containing the exact lease tag>",
  "recurring": false,
  "durable": false
}
```

An ordinary interactive Claude Code v2.1.233 capability probe established the required host behavior: the exact `recurring: false` input returned one structured job ID, fired once at the next minute boundary, and then `CronList` returned `No scheduled jobs`. The task auto-deleted before its wake-up turn inspected the scheduler. The design therefore does not require a self-delete call on the normal wake path.

`CronDelete` accepts a continuation transition only when `tool_input.id` is a validated string equal to the receipt's `cronId`. Other deletions remain outside this feature. `CronList` is not bound on `PreToolUse` because it is read-only; its successful `PostToolUse` output is used only for reconciliation.

`PostToolUse` runs only after a successful tool call. A failed create therefore leaves `arming`; a failed manual-resume delete leaves `canceling`; the normal tool error remains visible to Claude. Cron IDs are never recovered with a free-form text regex. The result normalizer accepts only:

1. `tool_response.id` as a validated cron ID,
2. `tool_response.cron.id` as a validated cron ID, or
3. exactly one structured CronList row from a top-level response list or `tool_response.crons` list whose complete prompt contains the exact delimited lease tag.

If `CronCreate` returns no supported structured ID, the receipt remains `arming` and additional context requires one `CronList` call. If that list also cannot yield exactly one matching structured row, registration fails closed and the adapter instructs Claude to mark the capability `unsupported`; the Stop boundary may release, but no automatic continuation is claimed.

The manifest parity test separates shared enforcement routes from this intentional host adapter. It adds an exact `CLAUDE_CONTINUATION_ROUTES` tuple set for the event, module, and matcher triples above, adds the continuation module's matcher-intent tokens, filters those tuples before comparing the existing shared topology, and asserts that the Codex manifest contains none of them. `UserPromptSubmit` is a Claude-only lifecycle event for this adapter and is registered only in `hooks/hooks.json`.

## Run Activation

### Primary path: direct slash-command invocation

Live Claude Code sessions deliver a direct slash command as a `UserPromptSubmit` event, not necessarily as a `PreToolUse:Skill` call. The primary Claude activation route therefore inspects the submitted prompt before tool selection. It matches only an exact start token:

```text
/ui-clone-skills:ui-reverse-engineering
```

The token must be followed by whitespace or the end of the prompt. Prose mentions, shell-style `$ui-clone-skills:ui-reverse-engineering`, partial prefixes, and other skill names are no-ops. A match creates a `running` receipt scoped to the current session. It does not create a cron and does not delay substantive pipeline work.

### Fallback path: Skill tool invocation

Some Claude or compatibility surfaces may still call the `Skill` tool with `skill: "ui-clone-skills:ui-reverse-engineering"`. A Claude-only `PreToolUse:Skill` hook keeps that path idempotent and uses the same receipt activation logic. It is not the primary route for ordinary interactive slash commands.

The required activation and Stop sequence is:

1. Create or reuse one `running` receipt for the explicit skill invocation.
2. Proceed with the normal UI reverse-engineering skill while no continuation cron exists.
3. On the first incomplete `Stop`, atomically move `running` to `arming`, block once, and require the exact non-recurring `CronCreate` input.
4. Let `PostToolUse:CronCreate` record the structured job ID and move `arming` to `armed`.
5. End the current assistant turn without starting more pipeline work.
6. When the one-shot fires and auto-deletes, accept only the exact immutable tagged wake prompt and atomically move `armed` back to `running` before checking the goal.

Stop discovery normally starts from the shared active marker and implementation ownership scan. Extraction-only phases intentionally have neither `.ui-re-active` nor an implementation directory because that marker belongs to the first implementation write. If the normal scan is empty, an exact validated Claude receipt binding may therefore supply the single candidate ref after project-local path and freshness validation. This fallback is session-scoped and Claude-only; it does not broaden Codex or shared active-run discovery.

A pre-Bash guard permits normal pipeline work in `running`. It blocks continuation-owned substantive commands in `arming`, `armed`, `canceling`, or `paused`, where work must wait for the scheduler transition or explicit reactivation. It permits an explicit `unsupported` receipt when the host genuinely lacks scheduled-task tools; unsupported hosts retain the current one-nudge Stop behavior instead of entering a broken enforcement loop.

The guard permits the receipt control CLI and unrelated Bash. It does not block continuation-owned pipeline work in `running`, `complete`, `terminal`, or `unsupported`. A fresh direct slash command or fallback Skill-tool invocation may replace `paused` with a new `running` receipt only because that invocation is an explicit user request.

### Fallback path: active run without a lease

Natural-language or legacy activation may reach an active UI-clone run without the Skill hook receipt. On the first incomplete `Stop` event, `section_gate.py` creates a `running` receipt, binds the one unambiguous ref, moves it to `arming`, and returns the same exact one-shot request before the normal gate failure and next action. This is a recovery path, not the primary activation route.

When `stop_hook_active` is true, the hook still releases the stop. An `armed` receipt proves the one-shot exists. An `arming` receipt at that second boundary proves registration was not established; the hook must not claim automatic continuation and leaves a precise unsupported or paused receipt for explicit recovery. It never uses repeated blocking as the continuation mechanism.

## Operational Receipt

The lease needs a small operational receipt because cron creation is a host tool action that the pipeline cannot otherwise verify. Store it at `.ui-re-continuation/<session-id>.json` under the project root, then bind its `refDir` field when the first pipeline command resolves the ref directory. The runtime directory is ignored by Git and never participates in clone-source or verification fingerprints.

```json
{
  "schemaVersion": 2,
  "host": "claude-code",
  "sessionId": "fe87fc97-d23a-496e-b13a-5ca5ab651f0d",
  "skill": "ui-clone-skills:ui-reverse-engineering",
  "state": "armed",
  "leaseTag": "UI_RE_CONTINUATION:<opaque-run-id>",
  "cronId": "cron-opaque-id",
  "refDir": "tmp/ref/<component>",
  "createdAt": "2026-08-15T00:00:00Z",
  "updatedAt": "2026-08-15T00:00:00Z"
}
```

Allowed states are `running`, `arming`, `armed`, `canceling`, `paused`, `complete`, `terminal`, and `unsupported`. `cronId` is present only while one exact scheduler row must still exist (`armed` or `canceling`). A `running` receipt has no continuation cron. Writes are atomic. The receipt is operational evidence only: it cannot make an incomplete pipeline complete and is not part of the canonical verify-stamp fingerprint.

The session ID is validated as a UUID or a conservative `[A-Za-z0-9._-]` token before it becomes a filename. `ui_clone.claude_continuation` writes a same-directory temporary file with mode `0600`, flushes it, and replaces the receipt with `os.replace`; readers reject invalid JSON, a mismatched `sessionId`, unknown states, and path traversal. The `.ui-re-continuation/` directory is Git-ignored and excluded from verification input discovery.

Hooks call the core functions directly. The matching operator and test surface is:

```text
python -m ui_clone.claude_continuation activate --session-id ID --cwd DIR --skill NAME
python -m ui_clone.claude_continuation bind-ref --session-id ID --cwd DIR --ref-dir REF
python -m ui_clone.claude_continuation arm --session-id ID --cwd DIR
python -m ui_clone.claude_continuation mark-unsupported --session-id ID --cwd DIR --reason TEXT
python -m ui_clone.claude_continuation pause --session-id ID --cwd DIR
python -m ui_clone.claude_continuation status --session-id ID --cwd DIR --json
```

`pause` may directly pause `running`, where no scheduler job exists. Pausing `armed` requires `CronDelete` followed by its successful PostToolUse transition. `canceling` is reserved for manual resume; its successful delete always returns to `running`, after which a changed user intent may pause without a scheduler job. Receipt-only repair never pretends a scheduler job was deleted. No CLI verb can set `complete`; only a successful canonical `goal --check-done` evaluation can do that.

State transitions are deliberately narrow:

| Trigger | Allowed current state | Next state |
| --- | --- | --- |
| Matching skill activation | no receipt or `paused` | `running` with no cron |
| Matching skill activation | `running` | unchanged |
| First incomplete `Stop` | `running` | `arming` and one exact create request |
| Successful `CronCreate` or one exact CronList match | `arming` | `armed` with the validated cron ID |
| Exact immutable tagged wake prompt | `armed` | `running`, clearing the auto-deleted cron ID |
| Manual user prompt before the one-shot fires | `armed` | `canceling` and one exact delete request |
| Successful owned `CronDelete` after manual resume | `canceling` | `running` |
| Explicit pause with no job | `running` | `paused` |
| Successful owned `CronDelete` for explicit pause | `armed` | `paused` |
| Canonical goal passes | `running`, `arming`, or wake-transitioned `running` | `complete` |
| `goal --check-done` aborts (`2`) | `running`, `arming`, or wake-transitioned `running` | `terminal` |
| Valid cron snapshot proves the owned job absent without an exact wake prompt | `armed` or `canceling` | `paused` |
| `mark-unsupported` with a recorded capability reason | `arming` | `unsupported` |

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
      "recurring": false,
      "prompt": "... [[UI_RE_CONTINUATION:<opaque-run-id>]] ..."
    }
  ]
}
```

Absent `session_crons` and a non-list value mean the capability is unavailable, not an empty list. An actual empty list is a valid snapshot proving that no session cron exists. Rows missing a validated `id`, string `schedule`, boolean `recurring`, or string `prompt` are malformed and ignored; a non-empty snapshot in which every row is malformed is unavailable rather than proof of absence. A matching row must have `recurring: false`, the expected next-minute schedule, and either the receipt's exact `cronId` or the full delimited tag `[[UI_RE_CONTINUATION:<opaque-run-id>]]` in its prompt. Arbitrary prompt substrings never establish ownership.

One exact matching row can move `arming` to `armed` or confirm `armed`/`canceling`. Multiple matching rows are a duplicate-lease failure and are never collapsed by choosing one silently. With a valid snapshot, an `armed` or `canceling` receipt whose job is absent becomes `paused` unless the exact immutable wake prompt is the current event; that prompt is positive evidence that auto-deletion occurred and instead moves `armed` to `running`. A `paused` receipt never becomes running through reconciliation. An unavailable snapshot causes no state change.

## Scheduled Prompt Contract

Every wake-up prompt follows the same deterministic contract:

1. Identify itself using the lease tag.
2. Let the `UserPromptSubmit` hook prove exact prompt equality, require `armed`, atomically move to `running`, and clear the cron ID that the host already auto-deleted.
3. Resolve exactly one active ref directory bound to the current session. Zero or multiple matches fail closed.
4. Run `goal --check-done` before doing any new work.
5. If complete, mark the receipt `complete` and report canonical completion evidence.
6. If `goal --check-done` returns `2` for a hard-cap/unclonable abort, mark the
   receipt `terminal` and report the terminal cause. A normal
   canonical verify failure returns `1`, remains incomplete, and must continue.
7. If user authority, credentials, or an irreversible decision are required, mark the receipt `paused` and state the blocker.
8. If incomplete, read `status --json`, `next --json`, and `report --for-llm`, then execute the next required action. If an exact pipeline owner is already alive, attach to or wait on that owner instead of starting a duplicate or ending with a status-only summary.
9. Preserve all normal gates, fail counts, iteration caps, and verification requirements. The lease never bypasses or resets them.

The next-minute schedule is a one-shot wake latency, not a polling interval. No continuation task exists before an incomplete Stop or after the one-shot fires. Background work can still outlive an assistant turn, so a wake that finds an exact live owner must wait on it; it must not create another pipeline owner.

## Stop-hook Interaction

The Stop hook retains its existing safety role:

- First incomplete stop from `running`: move to `arming`, block once with the exact one-shot create request, then include the concrete gate reason and next action.
- Successful create: move to `armed`, tell Claude to end the turn immediately, and perform no further pipeline work in that turn.
- Continuation-generated stop (`stop_hook_active: true`): release immediately to avoid consecutive-block churn. Only `armed` proves automatic continuation; unresolved `arming` is reported honestly.
- Completed or authoritative-abort run: allow stop and mark the receipt final. No scheduler deletion is needed in `running` because no job exists.

The Stop edge owns *when to arm*; the one-shot owns one wake; the pipeline owns *whether stopping is valid*.

## Pause, Resume, and Session Lifecycle

- The user can pause through the normal Claude task UI or a small continuation control command. `running` pauses directly because it has no cron; `armed` pauses only after a successful owned `CronDelete`. `canceling` has one meaning—manual resume—and its delete returns to `running` before any later pause.
- An ordinary manual prompt that arrives while `armed` is an explicit resume, not a wake. Its `UserPromptSubmit` transition moves to `canceling`, requires the exact owned `CronDelete`, and only the successful delete moves back to `running`. If the one-shot fires during that race, the exact wake prompt observes `canceling` and performs no pipeline work.
- A paused lease resumes only through an explicit user request or a fresh skill invocation.
- Closing Claude stops session-scoped scheduled execution. No external daemon remains.
- Resuming the same Claude session may restore an unexpired armed one-shot. `SessionStart` and `session_resume.py` re-bind the WIP context. If a valid cron snapshot proves an `armed` or `canceling` job is missing without the exact wake prompt, the receipt becomes `paused`; repair requires the next explicit continuation or fresh skill invocation.
- Starting a new conversation does not inherit the old lease automatically. The new session must deliberately adopt the active ref.
- User interrupts are respected. Stop hooks do not run on an interrupt, so an interrupt from `running` leaves no latent scheduler job. A documented pause action must remove a job only if the user interrupts during the brief `armed` or `canceling` window.
- Already-running schema-v1 recurring leases are not migrated in place. They remain owned by the Claude process and plugin version that created them and disappear when that session closes or completes its existing delete path. Schema v2 applies to fresh skill activation after installation; acceptance must use a fresh session.

## Failure Handling

The design fails closed without trapping the user:

- Missing `CronCreate`: move `arming` to `unsupported`, retain existing Stop semantics, and explain that automatic interactive continuation is unavailable.
- Malformed or duplicate cron: reject registration and require deletion of every continuation-owned duplicate before an explicit recreation; never choose one job silently.
- Missing or ambiguous ref binding: delete the lease and report the ambiguity instead of choosing a run.
- Stale cron ID: reconcile with `CronList`. If a valid list proves an `armed` or `canceling` job is absent without the exact wake prompt, mark the receipt `paused` and require explicit reactivation; do not guess whether the host lost the job or the user deleted it intentionally.
- Wake/manual-resume race: serialize receipt transitions under the existing per-session file lock. Exact wake wins only from `armed`; manual resume wins by moving `armed` to `canceling`. A wake observed from `canceling` is a no-op, so both event orders preserve one pipeline owner.
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

### Activation-time recurring cron

Live acceptance showed that Claude may enqueue recurring tasks while a background Bash owner is still alive. Ownership checks prevented duplicate commands but could not remove visible scheduled-turn churn. A longer interval merely trades noise for recovery latency. The design therefore creates no cron at activation and arms one non-recurring task only at an incomplete Stop boundary.

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
3. Skill activation creates `running` with no CronCreate context, and ordinary pipeline commands remain allowed in `running`.
4. The first incomplete Stop is the only path from `running` to `arming`; it emits the exact `recurring: false`, `durable: false` create input and blocks only once. A pre-generation gate that passes while Stop is inspecting it may advance `pipeline-state.json`, but cannot release Stop without an implementation and canonical completion evidence.
5. Successful structured `CronCreate` moves `arming` to `armed`; malformed or failed create output never advances state, and a duplicate matching row fails closed.
6. The exact immutable tagged wake prompt moves `armed` to `running` and clears the auto-deleted cron ID. A prose mention, wrong tag, altered prompt, or wake observed from another state performs no pipeline work.
7. Empty `session_crons` after an exact wake is normal auto-deletion, while empty `session_crons` observed from `armed` without that wake marks the receipt paused.
8. A manual prompt from `armed` moves to `canceling`, requires the exact owned `CronDelete`, and successful deletion returns to `running`; a racing wake from `canceling` is a no-op.
9. Continuation-owned substantive Bash is blocked in `arming`, `armed`, `canceling`, and `paused`, but allowed in `running` and explicitly `unsupported` according to policy; unrelated Bash and receipt control remain unaffected.
10. `stop_hook_active: true` still releases. `armed` proves automatic continuation; unresolved `arming` is never reported as protected.
11. A canonical verify failure leaves the receipt running, blocks a normal Stop, and
   remains discoverable through `verify-report.json`; a hard-cap/unclonable
   failure still terminalizes.
12. Claude-only manifest changes do not alter the Codex hook manifest or shared gate routing.
13. Receipt paths, tags, and prompt arguments reject traversal and arbitrary prompt interpolation.
14. Absent, malformed, empty, one-match, no-match, and duplicate `session_crons` snapshots exercise their distinct unavailable, absent, armed, paused, and failure outcomes.
15. Supported and unsupported `tool_response` shapes prove that no free-form cron ID is inferred and no failed tool call advances state.
16. Schema-v1 active recurring receipts are not silently reinterpreted as schema-v2 one-shots.

### Interactive acceptance

1. Start ordinary interactive `claude` in a fresh fixture directory.
2. Invoke `/ui-clone-skills:ui-reverse-engineering <fixture-url>`.
3. Verify the receipt is `running`, no session cron exists, and pipeline work advances normally.
4. Keep a background pipeline owner alive long enough to cross a minute boundary and verify no scheduled turn appears before Stop.
5. Force an incomplete assistant stop and verify the Stop hook requests exactly one non-recurring, non-durable one-shot.
6. Verify the structured create result moves the receipt to `armed`, then allow the current turn to end.
7. Within one minute boundary, verify exactly one tagged prompt runs in the same session ID, the receipt returns to `running`, and `CronList` contains no owned job.
8. Verify the wake-up turn reads the pipeline CLI and executes or waits on exactly one reported next action.
9. Arm another one-shot, submit a manual prompt before it fires, and verify owned deletion prevents the stale wake from doing pipeline work.
10. Make the fixture canonically complete and verify the receipt becomes `complete` with no scheduler job.
11. Pause an incomplete fixture in both `running` and `armed`; verify no later wake-up performs work.
12. Run the existing Codex hook parity and pipeline suites to prove Codex behavior is unchanged.

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
skill, perform active work with zero continuation crons, arm exactly one
session-scoped one-shot only at an incomplete Stop, resume the same session once,
continue after a recoverable canonical verify failure, and retain no scheduler
job on wake, canonical completion, hard-cap/unclonable abort, or explicit pause.
A background pipeline that crosses a minute boundary before Stop must produce no
scheduled turn. An ordinary interactive
Codex run must likewise remain blocked from ending after a recoverable verify
failure. Repository CI and security gates must pass.
