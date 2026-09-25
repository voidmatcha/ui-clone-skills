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
`uv run --project <package-root> --no-dev --frozen python -m ...`, pointed at
the shared hook venv (`UV_PROJECT_ENVIRONMENT`, same one `hooks/shim.sh`
uses — see the [install guide](../README_detail/install.md#shared-hook-venv)),
and falls back to `python3` with `PYTHONPATH=<package-root>`. Set
`UI_CLONE_CLI_PYTHON_DIRECT=1` to skip uv entirely and dispatch via `python3`
with `PYTHONPATH` (the agent-readable actions — status/next/report/state —
are stdlib-only and need no scientific dependencies).

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

### Section selection

Section-only and element-only clones are supported through the scoped path in
`skills/ui-reverse-engineering/operational-rules.md` (Scope adjustments by
request shape), not through this CLI. The pipeline has no section-selector
option: its component argument names the run and does not restrict extraction
or coverage to a DOM subtree, and verification scope (`desktop` / `all`)
concerns responsive layouts. Do not route a section-only request into the
automatic full-page pipeline or remove captured sections to make its gates pass;
a `verify` result here is page-level evidence, not scoped completion.

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

## Scoped check

```bash
node bin/ui-clone scoped-check <ref-dir> [--impl-root <dir>] [--json]
node "$PLUGIN_ROOT/bin/ui-clone" scoped-check "$(pwd)/tmp/ref/<target>" [--json]   # from a clone project
python -m ui_clone.scoped_check <ref-dir> [--impl-root <dir>] [--json]
```

Completion command for a scoped clone (section-only, element-only, or a
trigger-opened modal/drawer; a ref dir with a script-produced
`element-target.json` and no page-level marker), which the page-level
`verify` / `completion-report.sh --check` / `goal --check-done` cannot certify.
Exit codes: 0=PASS, 1=BLOCKED, 2=usage error. It passes only when:

- the installed producer files hash as the shipped release manifest
  `ui_clone/scoped_producers.sha256.json` (`python -m ui_clone.scoped_producers
  --check`; `producers-modified` otherwise);
- `element-target.json` is a valid `element-evidence.sh` record (schemaVersion
  2: `matchCount` 1, `bbox` at least 8×8 CSS px, `visible`; an older record
  fails `element-target-schema` and must be re-probed) and the dir has no
  `.ui-re-active`, `extracted.json`, or `pipeline-state.json`. The pass output
  and JSON `target` carry the resolved selector, match count, and bbox;
- `frames/ref/` and `frames/impl/` hold the same non-empty set of images, and
  each side's `capture-manifest.json` (schemaVersion 2, written only by
  `scripts/extract/element-state-capture.sh`; an older manifest fails
  `<side>-manifest-schema` and must be re-captured) lists every frame with a
  sha256 that still matches (recomputed), a `producer` record from the
  recorder CLI whose driver and module sha256 equal the release manifest, the
  `resourceOrigins` the page had loaded, per-clip target sanity, and the
  side's `codeResources` (normalized script/stylesheet URLs: query and
  fragment stripped, content-hash file-name segments collapsed). Ref entries
  must carry the reference origin from `element-target.json`; impl entries
  must carry a different origin (the local implementation), no non-media
  resource from the reference host or its subdomains (images, video, and
  fonts are the preserved asset URLs and stay allowed), and no code resource
  in the reference inventory or from a non-first-party origin that served
  reference code (`impl-loads-reference-code`; third-party code the reference
  never loaded, such as analytics, is not affected), so a reference frame
  copied, linked, or captured from the reference site — or from a local
  proxy, iframe, or page running the reference bundles from any host — into
  `frames/impl/` fails. Impl frames that predate `element-target.json` fail;
- resting-state clips (`idle`, `active`, `before`, `mid`, `after`, `open`) have
  AE 0 (recomputed; size mismatch fails). Motion sequences (`frame-NNNN`,
  `open-NNNN`, `close-NNNN`) pass the page-level video criteria of
  `scripts/verify/video-transition-compare.sh` / `lib/frame-align.sh`:
  first-change alignment, arc within 18 frames, per-frame SSIM ≥ 0.90 with a
  ±1 frame jitter retry. Sequence verdicts are cached by frame content hash in
  `.scoped-check-cache.json` (about 2 s cold for two 180-frame 400×300
  sequences, under 0.1 s cached), so the Stop hook stays cheap;
- `pixel-perfect-diff.json` was produced by the `python -m ui_clone.scoped_diff
  <ref-dir>` CLI (`schemaVersion` 3, `producer`, `producerRecord` with
  `entry: "cli"` and the argv, property-list fingerprint of
  `ui_clone.computed_style_diff`, the `computed-diff.sh` list, and a
  `recordSha256` self checksum that an edit breaks). Every input it
  fingerprints (`element-target.json`, both manifests, every clip and
  `<state>.computed.json`, the no-cheat outputs) and every source (`implFiles`,
  files named after the target, and the app entry files under `--impl-root`,
  default the project root above `tmp/ref/`) must still hash the same, every
  current clip must be covered, `result` must be `pass` with `mismatches: 0`,
  and every resting-state row must pass with `ae` 0 and a computed-style diff
  — the target plus its element subtree (up to 40 descendants matched by
  structural path `tag[i]/tag[j]`, index among element siblings; a node on one
  side only or a differing descendant count is a named structural mismatch) —
  that re-runs empty here;
- implementation provenance (`ui_clone.scoped_provenance`): the recorded
  page-level `proxy-mirror-check` and `bundle-paste-check` verdicts are `pass`
  (their outputs are fingerprinted), and the fingerprinted sources, re-scanned
  here, carry no reference-host load (`src`/`href`/`url()`/`@import`/`fetch`/
  `import` naming the reference host or a subdomain), no
  `document.documentElement.outerHTML` mirror, no `?raw` HTML mount, and no
  upstream proxy;
- trigger-opened UI (`open.webm`/`close.webm`, `open*`/`close*` frames, or a
  `dialog` role on the probed element) has both recordings, `open.png`,
  `open-NNNN.png` and `close-NNNN.png` on both sides, and a passing `open` row.

JSON keys: `status` (`passed` | `failed`), `failures[]` (`code`, `reason`),
`target` (`selector`, `match_count`, `bbox`, `url`),
`trigger_opened`, `frames_compared`, `sequences` (per prefix: `pass`,
`aligned`, `ref_arc`, `impl_arc`, `min_ssim`), `impl_sources`, `impl_root`,
`ref_dir`, `next_action`. The Stop hook and the commit/push/PR guard block a
scoped run this session wrote components for (or any owned scoped run once it
has clone-shaped writes) until this exits 0, with the page-level retry cap and
repeat-message behavior. `pixel-perfect-diff.json`, `capture-manifest.json`,
and `.scoped-check-cache.json` are hook-protected like `element-target.json`
(no tool or shell writes, including Python file APIs in `-c` programs and
heredocs, `node -e` / `perl -e` / `ruby -e` / stdin programs, escaped quotes,
and nested `bash -c` / `eval` programs); `ui_clone.element_capture` may not be
driven by hand, and `element_capture` / `scoped_diff` / `scoped_frames` may
not be imported from a shell program or written into a script in a clone
project (the Bash and Write/Edit hooks deny both; `ui-clone scoped-diff` /
`python -m ui_clone.scoped_diff` and `scoped_check` stay allowed). Running a script
outside the plugin whose content names one of these files or producers
(`bash forge.sh`, `node x.js`, `./x.py`) is denied by the Bash hook, which
reads the file before it runs.

Evidence ledger (`ui_clone.scoped_ledger`, `.scoped-evidence-ledger.json`
under the ref dir, hook-protected like the evidence): after every Bash
command, the PostToolUse hook (`post_verify`, Claude and Codex) checks whether
the command text is exactly one canonical producer invocation —
`element-evidence.sh`, `element-state-capture.sh clip|video`,
`node <plugin-root>/bin/ui-clone scoped-diff`, or `python -m
ui_clone.scoped_diff` (optionally `uv run [--project <plugin-root>]`), alone:
no `;`/`&&`/`|`/newline/redirect/subshell, no `PATH`/`PYTHONPATH`/`UV_*`-style
override or root-variable prefix, the script's sha256 equal to the release
manifest, no `ui_clone/` directory shadowing the installed package — and if
so records the sha256 of the evidence file that producer owns. Before
splitting the text the hook expands only `$PLUGIN_ROOT` / `$CLAUDE_PLUGIN_ROOT`
/ `$CODEX_PLUGIN_ROOT` (and `${...}`), to the plugin root the hook runs from
(a value the hook's own environment gives that variable must resolve there),
and `$(pwd)` / `$PWD` / `${PWD}`, to the command's working directory; any
other variable, `$(...)`, or backtick leaves the command unrecorded, as does a
`uv run` option other than value-less flags, `--directory`, and a `--project`
naming the plugin root. The documented clone-project forms, which
`tests/hooks/test_scoped_ledger_docs.py` keeps in sync with the parser:

```bash
bash "$PLUGIN_ROOT/scripts/extract/element-evidence.sh" <session> <page-url> "<target-selector>" "$(pwd)/tmp/ref/<target>/element-target.json"
bash "$PLUGIN_ROOT/scripts/extract/element-state-capture.sh" clip <session> <page-url> "<target-selector>" "$(pwd)/tmp/ref/<target>" <ref|impl> <state>
bash "$PLUGIN_ROOT/scripts/extract/element-state-capture.sh" video <session> <page-url> "$(pwd)/tmp/ref/<target>" <ref|impl> tmp/ref/<target>/<clip>.webm <prefix>
node "$PLUGIN_ROOT/bin/ui-clone" scoped-diff "$(pwd)/tmp/ref/<target>"
```

`scoped_check`
requires the current hash of `element-target.json`, both
`capture-manifest.json` files, and `pixel-perfect-diff.json` to appear under
its producer (`evidence-unledgered`, `evidence-ledger-missing`). Evidence
therefore has to be produced one command per Bash call with the hooks
installed; a producer chained with other commands, run from a script, or run
on a host without the hooks yields files the checker refuses.

Threat model and limits: the records are tamper-evident, not
cryptographic. They stop a lazy or over-eager agent using the natural tools
(shell redirects, inline programs in any quoting, hand-edited JSON, a forge
script run from the shell, a proxy or iframe of the reference, a re-used
reference bundle, an edited producer module). Out of scope, as for every
other hook-protected artifact: a script the hooks never see that edits the
ledger itself (a plain file next to the evidence — the guards deny naming it
on a command line or in a script the hook can read, not an unseen write), an
interpreter or `bash` shadowed on `PATH` by an earlier command, an
obfuscated path built from variables, or a reproduced checksum scheme plus a
forged release manifest. Still trusted: that the one visible element
`element-target.json` names is the one the user meant (the pass output
surfaces it for confirmation), that a resource the reference fetched from an
origin that served no code by kind, extension, or reported content type
(`PerformanceResourceTiming.contentType`, empty on browsers without it) is
not part of its runtime, and that sources outside the fingerprinted set
(component files, files named after the target, the app entry files) do not
proxy the site; `html-paste-check` and `css-mirror-check` do not run for
scoped clones because they need page-level artifacts (`dom-scaffold.json`,
`bundle-map.json`, `bundles/`).

## Scoped producers manifest

```bash
python -m ui_clone.scoped_producers --check    # installed producers match the release manifest
python -m ui_clone.scoped_producers --write    # maintainers only: regenerate after editing a producer
```

`ui_clone/scoped_producers.sha256.json` lists the sha256 of the scoped-evidence
producers (`ui_clone/computed_style_diff.py`, `element_capture.py`,
`scoped_diff.py`, `scoped_frames.py`, `scoped_provenance.py`,
`scripts/extract/element-evidence.sh`, `element-state-capture.sh`). Hashes are
of content, not paths, so a version bump or a plugin cache directory leaves
them unchanged. `scoped_check` requires the hashes in the evidence, in the
manifest, and of the installed files to agree; `scripts/ci/review.sh`
(`review_checks.py scoped-producers`) and `tests/test_scoped_producers.py`
fail when a producer changes without `--write`. The Bash hook denies `--write`
outside a checkout of this plugin.

## Scoped diff

```bash
node "$PLUGIN_ROOT/bin/ui-clone" scoped-diff "$(pwd)/tmp/ref/<target>" [--impl-root <dir>] [--impl-files <path> ...] [--json]
python -m ui_clone.scoped_diff <ref-dir> [--impl-root <dir>] [--impl-files <path> ...] [--json]
```

The first form runs from a clone project (the wrapper puts the plugin root on
`PYTHONPATH`; `[tool.uv] package = false` keeps `ui_clone` out of the venv);
the second only from a checkout of this plugin. Producer of `pixel-perfect-diff.json` (comparison-fix.md Phase D, element
scope). Needs no browser: it reads the clips and `<state>.computed.json`
records captured by `element-state-capture.sh` on both sides, computes clip
AE and the computed-style mismatches per resting state for the target and
its recorded subtree (rows carry the node `path`, `""` for the target), runs
the page-level `proxy-mirror-check.sh` and `bundle-paste-check.sh` against
the implementation root and scans the fingerprinted sources for reference
loads (`noCheat`), and writes the rows with provenance (producer record, input
and source hashes, self checksum). Exit codes: 0 = pass, 1 = fail (artifact
written so the rows can be read), 2 = usage / inputs missing. Only this CLI
produces a record `scoped_check` accepts; `build()` called from an import
records `entry: "api"` and is rejected.

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

- `UI_RE_STOP_RETRY_CAP=<n>` — how many times the SAME Stop block may repeat
  before the run is handed back (default 3). The counter is keyed by session,
  ref dir, and a signature of the failure, so progress or a different failure
  starts a fresh streak and only a genuinely repeating block spends the budget.
  When it is spent the stop is allowed — the loop stays bounded — but the
  hand-back arrives as a `systemMessage` naming the failing gate, and pipeline
  state is untouched, so nothing downstream reads the ref as complete.
  Raising this makes a wedged run loop longer before it hands back; setting it
  to `0` restores the pre-cap behaviour of blocking indefinitely.

  If a block repeats and you just want it to stop firing on an ABANDONED ref,
  the narrow move is to remove that ref's activation marker — the captured
  artifacts and the implementation source are untouched:

      rm tmp/ref/<component>/.ui-re-active

  Run it yourself; the agent is refused on that path because the marker is
  enforcement state. Left alone the marker expires on its own after
  `UI_RE_STALE_DAYS` (default 3).

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
Full runs record `status: failed` with `failedChecks` when checks fail,
`status: setup-failed` for dispatcher setup errors, and `status: interrupted`
when an active dispatcher exits before reaching a verdict. Closeout reports these
states separately so a failed full run is not mistaken for a partial repair run.

Known failures enter a targeted repair loop before another full `auto-verify.sh`
run: inspect failing rows, fix the cause, and rerun the failed check with its
dependencies. A build pass is not runtime evidence. The umbrella exits failed
after a failed required-check dispatch instead of starting further visual capture.
Content and runtime checks precede geometry and section comparison. Exhaustive
transition firing, trajectory/state sweeps, and frame comparisons depend on that
section evidence. These prerequisites also apply to targeted dispatch selection.
Failed prerequisites
block dependent captures while independent diagnostics may continue. Final
full-scope verification remains mandatory after repair. For section-compare
specifically, "failed" here means it did not MATERIALIZE evidence at all
(missing or unparseable `sections/result.txt`) — a genuine section pixel FAIL
with real evidence still lets motion checks dispatch; the clone's canonical
pass/fail verdict is unaffected and still comes from the post-implement gate.



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
