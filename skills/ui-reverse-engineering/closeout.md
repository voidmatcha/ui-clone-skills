# Closeout — Step 9

Read when preparing completion or running an unattended benchmark, not on every
inner repair iteration. All runtime requirements are conditional on observed evidence.

## Runtime-fidelity completion contract

Completion requires static visual, runtime media, transition, state-machine,
and no-cheat checks to pass. `section-compare`, build success, HTTP 200, source
strings, or implementation-only screenshots never establish completion.

Before reporting success:

- require browser-measured `runtime-proof.json` and `transition-proof.json` with
  `status=pass`; a measurement-free pass is invalid;
- require **scroll-scrubbed Lottie frame control** and **scroll state-machine**
  proof whenever `window.scrollTo`, `scrollYProgress`, `setTimeout`, `velocity`,
  or a guard ref is observed: prove `initial → active/expanded → settled/returned`;
- reject copied Swiper classes without Swiper runtime or evidence-backed
  sizing/translate behavior;
- never force `is-active` / `is-visible` / `is-show` globally to fake a final
  transition state;
- reject direct reference JS/CSS/iframe loading, screenshot-as-page rendering,
  forced final-state classes, and captured whole-document mirrors;
- run `pipeline ... verify`, `completion-report.sh --check`, and
  `ui_clone.goal --check-done`; report `INCOMPLETE` with the failing artifact
  whenever any command is non-zero; and
- use the canonical tier, dependency, and gate-to-artifact mapping in repository
  `docs/gates.md` instead of maintaining a second gate inventory here.

For unattended benchmark loops, keep natural user prompts free of gate coaching
and store comparable evidence in the active ref directory or benchmark history.
The detailed runner contract remains in [Agent-driven loop](#agent-driven-loop) below.

## Completion criteria

**Do not claim done until all three commands exit 0:**

```bash
python -m ui_clone.pipeline <url> <component> <session> verify
bash scripts/verify/completion-report.sh --check <ref-dir> <impl-root>
python -m ui_clone.goal <ref-dir> --check-done
```

The verify path must cover static, responsive, asset/font/media, interaction,
transition, runtime, and no-cheat evidence declared by
`verification-plan.json`. Missing required artifacts, a non-`done`
`current_gate`, failed section/transition rows, or an implementation that merely
builds or serves HTTP is `INCOMPLETE`. Whole-document HTML mirrors and direct
reference-runtime loading are invalid implementations. See repository
`docs/gates.md` for the canonical gate and artifact contract.

## Agent-driven loop

This skill is auto-loaded into Claude Code (with `--plugin-dir`) and Codex sessions, so prompts can be terse. The agent drives the loop inside a single session, iterating against `python -m ui_clone.goal <ref-dir> --check-done` until it exits 0. `ui_clone/hooks/section_gate.py` (Stop hook) emits gate-specific failure diagnostics on every exit attempt so the agent sees what is still blocking.

- **Natural user prompts stay natural.** When benchmarking or dogfooding real
  usage, send only the user's visible request (for example: `Copy <URL> as
  closely as possible, including transitions. Make it runnable locally.`). Do
  not inject internal artifacts, gate names, ref-dir paths, or operator notes
  into that prompt. Put runner constraints in project instructions, plugin
  defaults, or harness metadata instead.
- **Natural prompt closeout guard:** even when the visible request is terse,
  a clone/same-as-original request cannot be reported as done until the agent
  runs both `bash scripts/verify/completion-report.sh --check <ref-dir>
  <impl-root>` and `python -m ui_clone.goal <ref-dir> --check-done`. If either
  command reports missing artifacts, failed section rows, missing runtime /
  transition proofs, `current_gate != "done"`, or a non-zero exit, the response
  must start with a standalone `INCOMPLETE` line and list the blockers.
  Manual screenshots, build success, HTTP 200, a page title, local smoke
  checks, implementation-only runtime checks, CLI `task_complete`, "Worked for",
  "Total cost", or a closed tab are supplementary evidence only; they never
  substitute for the completion report and goal exit code.
- **Machine-readable loop closeout:** unattended drivers may count a run as
  success only when the final response begins with a standalone `DONE` line
  after both closeout commands above exit 0. When either command is missing or
  non-zero, first line must be `INCOMPLETE`; include `current_gate`, the failing
  artifact/gate, and the next command to run. Do not lead with `Implemented`,
  `Finished`, `functional clone`, `known limitation`, a dev-server URL, HTTP
  200, build success, or smoke-check bullets when `current_gate != "done"` or
  any section / transition / runtime proof is failing. Required visual/runtime
  gate failures are blockers, not limitations.
- **Do not turn parent-repo WIP into a user choice.** `impl-scope` snapshots
  files that were already dirty at the iteration baseline and ignores them
  only while their content is unchanged. If `impl-scope` still fails, report
  `INCOMPLETE` with the changed paths and fix/revert clone-caused edits inside
  the iteration; do not ask the user to stash, revert, or approve unrelated
  working-tree cleanup just to satisfy a clone gate.
- If a natural prompt run creates a local preview for the user, bind it to
  `0.0.0.0` when the dev server supports it. A preview bound only to
  `127.0.0.1` is local-only evidence and should not be presented as an
  externally reachable preview.
- For a preview that must be reachable beyond the local machine, bind the
  server to a reachable interface (or use whatever preview/tunnel workflow the
  host environment provides) and report a receipt: the exact URL(s) you
  verified, how each was verified (an HTTP status or rendered check from the
  network the user will use), and the bound interface/port. A URL you did not
  verify from the far side is not a receipt. Do not repurpose the application
  listener as an ad-hoc tunnel or serve mapping; whatever workflow provides the
  mapping owns port selection, mapping identity, and two-sided verification.
- **Unattended no-choice contract:** in any non-interactive or pre-authorized
  automation context, do not ask the user to choose between approaches, approve
  a retry, or pick a blocker. The run has already granted permission for safe
  reversible work. If multiple paths are viable, choose the
  one most directly supported by current artifacts and gate output, then verify.
  Default priority is: recover missing canonical artifacts; fix runtime-env /
  no-cheat blockers; fix asset/font/media blockers; run section/sticky/transition
  comparisons; then make the smallest measured implementation edit. If a gate is
  structurally blocked by parent-repo state or unstable live-reference motion,
  record that evidence and continue with the next clone-local, measurable gate;
  do not emit "your call", "tell me which", "need a decision", or equivalent
  choice prompts. Stop only for destructive/external actions (credentials, paid
  licenses, deleting unrelated user work) or a documented unclonable condition.
- **Claude Code:** open with `claude --plugin-dir "$(pwd)"`, then prompt: `Drive the ui-clone-skills pipeline for <ref-dir> until python -m ui_clone.goal <ref-dir> --check-done exits 0.`
- **Codex (interactive):** in the REPL (Codex CLI ≥ 0.128.0, `[features] goals = true` in `~/.codex/config.toml`), run `/goal Drive the ui-clone-skills pipeline for <ref-dir> until python -m ui_clone.goal <ref-dir> --check-done exits 0.` Codex Goal handles plan → execute → verify → repeat natively against AGENTS.md context.
- **Unattended / headless / CI:** `python -m ui_clone.benchmark_harness <ref-dir> --orig-url <url> --impl-url <url> ...` wraps `claude --print` per-iter with focused prompts and Python-side stop checks.

All paths exit on `python -m ui_clone.goal <ref-dir> --check-done` exit codes:
- `0` — pipeline DONE (`current_gate == done` AND `sections/result.txt` clean).
- `2` — ABORT (`pipeline-state.json.unclonable_reasons[]` non-empty: paid font with no substitution, DRM canvas, auth-gated content).
- non-zero otherwise — keep iterating.

When the goal card emits a `STUCK` banner (the active gate has failed ≥3 consecutive runs), route into `diagnosis.md` / `patterns.md` / `visual-debug/SKILL.md` before retrying the same action. When acting as that worker:

1. Dismiss modals/overlays before capture
2. Always capture ref frames and compare — "already implemented" is not grounds for skipping
3. Ref frames to `tmp/ref/<c>/frames/ref/` once; impl frames to `frames/impl/` after each change
4. Iterate until 100% visual match. All values from measurements — no guessing.
