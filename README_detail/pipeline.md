# Pipeline — hooks, goal-driven continuation, and the gate system

## Pipeline hooks (automatic)

Hooks register automatically through the host manifest when supported: `hooks/hooks.json` for Claude Code and `hooks/codex-hooks.json` for Codex. All hooks route through a single `hooks/shim.sh` that fast-skips when no `tmp/ref/` directory exists.

| Hook module | Event | Purpose |
|------|-------|---------|
| `ui_clone.hooks.pre_generate` | `PreToolUse` (Write/Edit) | Blocks component writes until extraction completes. Creates `.ui-re-active` on first passing gate (activation site for the rest of the chain). Demotes state + invalidates `sections/result.txt` on post-`done` component edits |
| `ui_clone.hooks.pre_bash` | `PreToolUse` (Bash) | Two checks. (1) Blocks declaration-of-done bash commands (`git commit`, `git push`, `gh pr create/merge/close`) when verification is incomplete. (2) Blocks Bash redirects/streams that write to component files (`cat > Foo.tsx`, `tee Foo.tsx`, `sed -i ... Foo.tsx`) when extraction is incomplete — symmetrical with the `Edit/Write` gate so shell-redirect bypass is closed. Read-only commands pass through. Bypass: `UI_RE_SKIP_BASH_GATE=1` |
| `ui_clone.hooks.post_verify` | `PostToolUse` (Bash) | Warns on completion signals if verification hasn't run |
| `ui_clone.hooks.devtools_errors` | `PostToolUse` (Bash) | Checks browser devtools for console errors after each Bash call |
| `ui_clone.hooks.section_gate` | `Stop` | Blocks finishing if the current gate hasn't passed. Marker persists past section-compare; `current_gate == "done"` is the canonical complete signal |
| `ui_clone.hooks.session_resume` | `SessionStart`, `PostCompact` | Reinjects the verification checklist into context after a session resume or context compact (empirical: 73% of past verification skips happened within 20 min of a `compact_boundary`). Skipped when state is `done` |

## Goal-driven continuation

`ui-clone-skills` supports Ralph-Wiggum-style continuation through a host-neutral **goal card** instead of an infinite loop. The card is derived from `tmp/ref/<component>/pipeline-state.json` / `PipelineState` and states the mission, current goal, next action, stop condition, and required evidence for the current gate.

```bash
python -m ui_clone.goal <ref-dir>
```

Automatic continuation: the agent drives the loop inside a single Claude Code or Codex session — there is no external scheduler / background daemon. Open the session with the plugin loaded, give it the goal, and let the agent iterate against `python -m ui_clone.goal <ref-dir> --check-done` until that exits 0. `ui_clone/hooks/section_gate.py` (Stop hook) emits gate-specific failure diagnostics so the agent sees exactly which artifact / check is still blocking on every exit attempt.

**Claude Code (recommended):**

```text
claude --plugin-dir "$(pwd)"
> Drive the ui-clone-skills pipeline for tmp/ref/<component>. Each
> iteration: run `python -m ui_clone.goal tmp/ref/<component>`, execute
> exactly the Next action, then re-check with
> `python -m ui_clone.goal tmp/ref/<component> --check-done`. Stop
> only when --check-done exits 0.
```

The `ui-reverse-engineering` skill is auto-loaded so the prompt does not need to re-embed the full pipeline briefing — just declare the ref dir and the stop condition. For unattended headless / CI runs see `ui_clone/benchmark_harness.py` (Python wrapper around `claude --print`).

**Codex (recommended, interactive):** Codex CLI ≥ 0.128.0 ships a native [Goal](https://developers.openai.com/codex/use-cases/follow-goals) feature that drives plan → execute → verify → repeat against the same `python -m ui_clone.goal <ref-dir> --check-done` stop condition — no external loop needed.

```toml
# ~/.codex/config.toml — enable once, restart Codex
[features]
goals = true
plugin_hooks = true
```

In the Codex REPL, run a one-line `/goal` invocation (the `ui-reverse-engineering` skill ships an `AGENTS.md` block that Codex auto-loads, so the goal prompt doesn't re-embed the full pipeline briefing): `/goal Drive the ui-clone-skills pipeline for tmp/ref/<component> until python -m ui_clone.goal tmp/ref/<component> --check-done exits 0. Never declare completion until the exit code is 0.` Use `/goal pause` to narrow scope mid-run, `/goal resume` to continue.

For real-use dogfooding, keep the visible user prompt natural: `Copy <URL> as
closely as possible, including transitions. Make it runnable locally.` Do not
append runner notes, artifact paths, or gate instructions to that prompt. The
plugin defaults and project instructions still require a strict closeout: run
`scripts/verify/completion-report.sh <ref-dir> <impl-root>` and
`python -m ui_clone.goal <ref-dir> --check-done` before any success claim. If
either command fails or reports missing runtime/transition proof, report
`INCOMPLETE` instead of done.

The stop condition is bounded: stop when `current_gate == "done"` and `sections/result.txt` has no `FAIL` or `MISSING impl` lines. SessionStart/PostCompact hooks inject the active goal card, and the Stop gate includes the same card when blocking so the next action is explicit.

When an iteration exposes a plugin bug, do not let the iterator edit gate
code inline. Escalate via [Plugin Code Edits During Clone Iteration](./plugin-code-edits-during-iteration.md):
land the plugin fix separately, verify it, reset the iteration baseline, then
resume the clone.

## Gate system (Python)

The `ui_clone/` package (Python 3.11+, managed by `uv`) provides pipeline gates, dependency tracking (DAG-based staleness detection), multiscale SSIM comparison, and viewport-relative CSS severity scoring.

```bash
# Gate validation
python -m ui_clone.gate <ref-dir> <gate> [--json]
# Gates: reference | extraction | bundle | paid-features | spec | pre-generate | post-implement | boundary | font-parity | section-compare | all
# Exit:  0=PASS  1=BLOCKED  2=usage error

# Pipeline status
python -m ui_clone.pipeline <url> <component> <session> status [--json]

# Current bounded goal card
python -m ui_clone.goal <ref-dir>

# Loop-exit signal: exit 0 only if current_gate == "done" AND section-compare is clean.
# Use as the while-loop predicate for external drivers (Ralph loop, codex exec, etc.).
python -m ui_clone.goal <ref-dir> --check-done
```
