# Agent-readable CLI

`ui-clone` is the preferred agent and human entrypoint for pipeline state.
The older `python -m ui_clone.*` commands remain supported for hooks and
compatibility.

## Install / run

From a checkout, use the local bin directly:

```bash
node bin/ui-clone --help
```

The wrapper dispatches to the Python modules in this package. It prefers
`uv run --project <package-root> python -m ...` and falls back to `python3`
with `PYTHONPATH=<package-root>`. Set `UI_CLONE_CLI_PYTHON_DIRECT=1` to skip
uv entirely and dispatch via `python3` with `PYTHONPATH` (the agent-readable
actions — status/next/report/state — are stdlib-only and need no scientific
dependencies).

> **Local-first while npm publishing is paused:** the registry copy of
> `ui-clone-cli` may lag this checkout. During local development prefer
> `node bin/ui-clone ...` (or `python -m ui_clone.*`) over `npx ui-clone-cli`,
> which resolves to the published version unless the package is npm-linked.
> Use `npx ui-clone-cli --help` only when intentionally testing the published
> package compatibility surface.

## Codex project hooks

The globally enabled Codex plugin is skills-only, so ui-clone hooks do not run
in unrelated sessions. The `ui-reverse-engineering` skill checks the current
workspace automatically and configures the canonical six routes on first use.
Use these commands when managing the boundary directly:

```bash
ui-clone hooks status --project-root <path> --json
ui-clone hooks enable --project-root <path>
ui-clone hooks disable --project-root <path>
```

Without `--project-root`, the CLI uses the current Git root. `enable` merges
only ui-clone-owned entries into `<project>/.codex/hooks.json`; `disable`
removes only those entries. Foreign hooks, metadata, and hook state survive the
round trip. Writes are backed up and atomic, and malformed JSON is left
untouched.

`status --json` reports `active`, `parity`, `routeCount`,
`canonicalRouteCount`, `trust`, and `nextStep`. `active` means the on-disk
project manifest exactly matches the canonical ui-clone route set; Codex trust
is a separate host decision. After the first enable or a manifest change,
review `/hooks` if prompted and start a fresh session.

## Pipeline status

```bash
node bin/ui-clone pipeline <url> <component-or-run-dir> <session> status --json
```

Use this before reading raw artifacts. The JSON response includes:

- `status`: `active`, `verified`, `needs_verify_stamp`, or terminal status.
- `run_dir` / `ref_dir`: canonical evidence directory.
- `impl_dir`: implementation directory when discoverable.
- `current_gate`, `completed_steps`, `remaining`.
- `read_for_llm`: small files safe for agents to read.
- `do_not_read`: raw DOM/style/screenshot/video directories to avoid.

The pipeline command also supports shorthand:

```bash
node bin/ui-clone <url> <component-or-run-dir> <session> status --json
```

## Next action

### Browser identity for manual retries

The pipeline exports a browser namespace and color scheme to its child
processes. Those exports do not propagate back to the calling shell. Before
running a manual helper against a session created by the driver, restore the
same values in that shell:

```bash
SESSION='<same-project-name-used-by-the-driver>'
: "${AGENT_BROWSER_NAMESPACE:=ui-clone-$(printf '%s' "$SESSION" | cksum | awk '{print $1}')}"
: "${AGENT_BROWSER_COLOR_SCHEME:=light}"
export AGENT_BROWSER_NAMESPACE AGENT_BROWSER_COLOR_SCHEME
```

If the original run used explicit overrides, export those exact values instead.
Changing launch settings can restart the browser; a matching session name alone
does not preserve its page. Redirect receipts also bind to the namespace and
session, so keep `capture-navigation.json` in the reference directory when
retrying `inline-scripts.sh` or `element-evidence.sh`. If element evidence is
written outside that directory, pass the receipt path as its fifth argument.

### Resume the pipeline

```bash
node bin/ui-clone pipeline <url> <component-or-run-dir> <session> next --json
```

Use this to resume interrupted work. It prints the current gate, terminal
state if any, next action, and safe read paths.

## LLM report

```bash
node bin/ui-clone pipeline <url> <component-or-run-dir> <session> report --for-llm
```

Use this for compact handoff context. Prefer it over grepping large raw
artifacts.

## Verify

```bash
node bin/ui-clone pipeline <url> <component-or-run-dir> <session> verify --json
```

`verify --json` runs post-implementation gates and returns machine-readable
results. On success it creates `verify-stamp.json`. On failure it writes
verify reports/logs and records `terminalState`; it does **not** create a fake
success stamp.

JSON keys (`status` is always `passed` or `failed` — no other spellings):

- `status`: `passed` | `failed`.
- `reason`: present on early-exit failures (impl missing, quick-tier plan).
- `failed_gates`, `gate_exit_codes`: per-gate results on gate failures.
- `gates_passed`: closeout suite on success.
- `verify_stamp`: `{ path, created, success_only }` — always present.
- `next_action`: machine-readable recovery hint.

## Gate

```bash
node bin/ui-clone gate <ref-dir> <gate-name> [--json]
```

Runs a single gate (any name from `ui_clone.state.GATE_ORDER`, or `all`)
against an evidence directory. Exit codes: 0=PASS, 1=BLOCKED, 2=usage error.

## Goal

```bash
node bin/ui-clone goal <ref-dir> [--json]
```

Prints the goal card (target, progress, hard-cap state) for a run.

## Bounded logs for agent hosts

When `pipeline run` includes Phase 1, provisional or failed reference evidence
returns nonzero unless Phase 2 is scheduled later in that invocation. Phase 2
must repair the evidence and pass the reference gate; a missing screenshot
baseline or an older completed reference gate cannot waive that requirement.
Resuming Phase 2 on its own also rechecks current reference evidence whenever
the five-screenshot baseline exists or a prior reference completion is recorded.
A prior completion with fewer than five baseline screenshots fails the resume;
restore the baseline before continuing.
Phase 1 status still requires the full-scroll video separately from transition
evidence, which may use verified PNG pairs or an inventory-backed MP4.

Pipeline `run` and `verify` keep full subprocess output under
`tmp/ref/<component>/logs/` and print concise status lines by default. This keeps
Codex/Claude transcripts small enough to resume safely after long browser,
capture, or gate runs.
Each invocation also preserves an immutable log under the scope's `attempts/`
directory; the stable log filename continues to show the latest attempt.
Earlier failures therefore remain available after a retry.

- `UI_CLONE_LOG_TAIL_LINES=120` controls failure-tail echo length.
- `UI_CLONE_LOG_TAIL_LINES=0` prints log paths only.
- `UI_CLONE_ECHO_SUCCESS_OUTPUT=1` also prints bounded tails for successful
  steps; leave unset for normal agent sessions.

Prefer reading the referenced log file with `tail -n 120` or `rg` instead of
printing full logs into the chat transcript.

## Terminal failed / incomplete state

Use terminal state when an evidence run is intentionally over but not
verified:

```bash
node bin/ui-clone state terminal <ref-dir> \
  --status incomplete \
  --category hardening-probe-incomplete \
  --gate section-compare \
  --reason "canonical verify failed; evidence preserved"
```

Allowed `--status` values:

- `failed`
- `incomplete`
- `unclonable`
- `abandoned`

`--category` is a free-form evidence category. Do not use terminal state to
claim success; success remains `verify-stamp.json` only.

## Layout compatibility

Phase 1 supports both layouts:

- Legacy: `tmp/ref/<component>`
- Agent-first: `.ui-clone/runs/<id>`

Passing an existing run directory or a run id under `.ui-clone/runs/` resolves
to that directory. Automatic migration/copying of existing evidence is out of
scope.

## Escape hatches (HUMANS only)

These environment variables disable enforcement and are intended for a human
operator or CI to set via host settings — never for an agent to set mid-run.
The hook deny messages deliberately do not advertise them: setting them to get
past a gate voids the measurement signal the gate exists to produce.

- `UI_RE_SKIP_BASH_GATE=1` — disables ALL pre_bash write/scaffold/mirror guards
  for the command. Use only when a human has confirmed the blocked command is
  legitimate non-clone work; the artifact/forgery guards no longer apply.
- `UI_RE_ALLOW_OFFPIPELINE=1` — releases the off-pipeline clone guard (the Stop /
  pre_generate block that fires when a session browsed an external site and wrote
  clone-shaped files without a `tmp/ref/<component>` evidence dir). Set it only
  for genuinely non-clone work; clone work outside the pipeline ships unverified.

- `UI_RE_HEADLESS_DRIVER=1` — set by `ui_clone.benchmark_harness` on the
  `claude --print` child. Demotes the section_gate **Stop** block to a stderr
  advisory, because a Stop block under `--print` ends the turn with no printed
  answer and the reason only lands on the next iteration — the driver already
  re-runs the same gates between iterations, so nothing is enforced by the
  block that is not enforced anyway. It does NOT relax `pre_bash`: that deny
  arrives as a tool result mid-turn and remains the only guard against
  committing an unverified clone inside a round. Do not set it for an
  interactive session — there the block is the only thing the agent sees.

If a gate is blocking legitimate clone work, the fix is to re-run the canonical
check (the deny message names it), not to set these. For genuinely non-clone
work, a human decides — ask the user before setting either flag.

## Desktop scope and repair iterations

Implementation preserves source responsive CSS and structural variants from the start.
`generation-plan.json.responsiveImplementation` is independent of verification scope;
its policy is not proof that unmeasured layouts pass.

`verification-plan.sh <ref-dir> --scope=desktop` is the default: one detailed
representative viewport plus cheap live probes inside the inferred desktop band.
Use `--scope=all` for an explicitly requested responsive sweep. Completion reports
and stamps retain the scope; desktop completion prompts for additional layouts.

For repair runs, set `UI_CLONE_ITERATION_CHECKS=id,id` or
`UI_CLONE_CHANGED_FILES=/path/to/newline-separated-impl-relative-paths` when invoking
`run-required-checks.sh`. Dependency checks remain selected. Unknown inputs are
conservative. Unset both for final dispatch; `iteration-receipt.json` is a dispatch
receipt, never completion evidence. Partial, dry, failed, or unfinished dispatch receipts block canonical closeout.
Only a successful full dispatch records `status: completed`; canonical gates still
validate all artifacts before stamping completion.

Known failures enter a targeted repair loop before another full `auto-verify.sh`
run: inspect failing rows, fix the cause, and rerun the failed check with its
dependencies. A build pass is not runtime evidence. The umbrella exits failed
after a failed required-check dispatch instead of starting further visual capture.
Content and runtime checks precede geometry and section comparison. Exhaustive
transition firing, trajectory/state sweeps, and frame comparisons depend on that
section evidence. These prerequisites also apply to targeted dispatch selection.
Failed prerequisites
block dependent captures while independent diagnostics may continue. Final
full-scope verification remains mandatory after repair.

Trajectory sampling uses captured changing intervals in addition to global page
fractions. Missing target ranges produce `transitions/trajectory-sampling.json`
with an error and the selectors requiring capture; recover that evidence rather
than reducing the target set. Additional local samples compare settled geometry,
while optional full-frame diagnostics remain at the global sample positions.

Per-check `iteration-retries/` receipts classify failures and stop two identical
failures with unchanged inputs from causing another expensive blind retry.
After diagnosing an external condition, remove the named per-check retry receipt
to retry; do not remove failing evidence or weaken required checks.

## Worker routing and bounded recovery

Specialized role installation does not prove host runtime availability. If a role
is absent or rejected, use a generic native worker with the same shared contract
and explicit artifact ownership; remember the rejection for this session. Only
use the bounded inline fallback when delegation itself is unavailable or the work
cannot run independently. Keep all gate requirements unchanged.

Use `pipeline ... next --json` and `pipeline ... report --for-llm` before reopening
large source artifacts. A schemaVersion 1 base generation plan still needs enrichment;
inspect the exact pre-generate failures and complete that work before implementation.
