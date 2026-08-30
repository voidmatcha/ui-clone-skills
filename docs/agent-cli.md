# Agent-readable CLI

`ui-clone` is the preferred agent and human entrypoint for pipeline state.
The older `python -m ui_clone.*` commands remain supported for hooks and
compatibility.

## Install / run

```bash
npx ui-clone-cli --help
```

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
npx ui-clone-cli pipeline <url> <component-or-run-dir> <session> status --json
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
npx ui-clone-cli <url> <component-or-run-dir> <session> status --json
```

## Next action

```bash
npx ui-clone-cli pipeline <url> <component-or-run-dir> <session> next --json
```

Use this to resume interrupted work. It prints the current gate, terminal
state if any, next action, and safe read paths.

## LLM report

```bash
npx ui-clone-cli pipeline <url> <component-or-run-dir> <session> report --for-llm
```

Use this for compact handoff context. Prefer it over grepping large raw
artifacts.

## Verify

```bash
npx ui-clone-cli pipeline <url> <component-or-run-dir> <session> verify --json
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
npx ui-clone-cli gate <ref-dir> <gate-name> [--json]
```

Runs a single gate (any name from `ui_clone.state.GATE_ORDER`, or `all`)
against an evidence directory. Exit codes: 0=PASS, 1=BLOCKED, 2=usage error.

## Goal

```bash
npx ui-clone-cli goal <ref-dir> [--json]
```

Prints the goal card (target, progress, hard-cap state) for a run.

## Bounded logs for agent hosts

Pipeline `run` and `verify` keep full subprocess output under
`tmp/ref/<component>/logs/` and print concise status lines by default. This keeps
Codex/Claude transcripts small enough to resume safely after long browser,
capture, or gate runs.

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
npx ui-clone-cli state terminal <ref-dir> \
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
