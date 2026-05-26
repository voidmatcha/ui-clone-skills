# Changelog

## [0.7.20] - 2026-05-26

Patch release for natural-prompt Codex clone runs.

### Added

- **Natural prompt closeout guard.** Codex plugin defaults and the
  `ui-reverse-engineering` skill now require `completion-report.sh` plus
  `python -m ui_clone.goal <ref-dir> --check-done` before any success claim,
  even when the visible user prompt is a terse "copy this site" request.
- **Natural onpixel clone prompts.** The onpixel Codex loop now sends clone
  workers a short user-like URL copy request and moves runner constraints into
  the implementation workspace `AGENTS.md`, preventing internal artifact
  references from leaking into benchmark prompts.

## [0.7.19] - 2026-05-26

Patch release for general clone-loop completion safeguards.

### Added

- **Local source reuse contamination detection.** Added a generic
  `ui_clone.local_source_reuse` helper that detects copy-style reuse of
  protected local exemplar roots from clone transcripts or embedded absolute
  paths. The onpixel loop now uses it to mark reused showcase implementations
  as `completionStatus=contaminated`, writes `source-reuse.md`, disables
  preview eligibility, and skips skill-fix escalation for contamination.
- **Strict done runtime evidence.** `benchmark_harness.check_strict_done` now
  requires `runtime-proof.json`, `transition-proof.json`, and a passing
  `asset-utilization.json` with at least five downloaded assets before a loop
  can claim completion.
- **Canvas/WebGL completion guard.** Canvas-primary and WebGL-primary refs now
  require explicit canvas-replay closeout proof in strict completion checks,
  and `runtime-proof-rollup.sh` now includes `runtime-frame-proof.json` so
  planned canvas/WebGL/Lottie frame-delta checks cannot be silently skipped.

### Fixed

- **Rendered text fidelity coverage.** `text-fidelity-check.sh` now scans JSX
  as well as TSX components, ignores cookie/consent overlay text as non-target
  copy, and uses `element-roles.json` as rendered-text allowlist evidence.
- **Transition proof grounding.** `transition-proof-rollup.sh` now treats
  declaration-only or single-sample transition coverage as inventory unless a
  runtime proof source, including `transitions/result.txt`, carries the motion
  evidence.
- **Original URL preflight tolerance.** `auto-verify.sh` no longer blocks solely
  because a browser-loadable origin rejects raw curl preflight; the browser
  verification still decides fidelity.

## [0.7.18] - 2026-05-26

Patch release for completion-focused onpixel clone loop automation.

### Added

- **Clone completion attempts.** `python -m ui_clone.onpixel_showcase_loop`
  now accepts `--clone-attempts` to keep a site on the clone worker for
  multiple bounded passes until strict inspection reports done or the attempt
  budget is exhausted.
- **Issue-only skill fix policy.** The new `--skill-fix-policy issue-only`
  mode launches the maintainer pass only when a clone pass writes
  `skill-issue.md`, keeping ordinary incomplete clone work on the clone worker
  instead of prematurely classifying it as a plugin repair pass.

## [0.7.17] - 2026-05-26

Patch release for section-compare goal routing consistency.

### Fixed

- **Clean section-compare prerequisite routing.** `ui_clone.goal` now routes a
  clean `section-compare` state with missing earlier gates to the earliest
  missing prerequisite instead of asking the worker to re-run
  `section-compare.sh`, and `--check-done` prints the missing-prerequisite
  diagnostic to stderr before exiting nonzero.

## [0.7.16] - 2026-05-26

Patch release for hotlink-protected image asset downloads.

### Fixed

- **Asset download browser fallback.** `asset-download.sh` now uses captured
  page URL metadata as the referrer for image downloads and replaces the
  unsupported `agent-browser fetch` fallback with supported `open` and
  `eval --json` commands, preventing browser-loadable CDN assets from being
  logged as failed solely because the fallback command is unavailable.

## [0.7.15] - 2026-05-26

Patch release for one-shot verifier artifact stability.

### Fixed

- **Required media coverage path audit fields.**
  `required-media-coverage-check.sh` now emits `implRoot`, `implDir`,
  `implSrcDir`, `implPublicDir`, and `implPkgJson` even when
  `required-media.json` is absent, so the `post-implement` gate can validate
  the artifact provenance instead of failing a valid no-op result.
- **Required-check dispatcher text/dom outputs.**
  `run-required-checks.sh` now passes `--out` to `text-fidelity-check.sh` and
  `dom-mirror-check.sh`, materializing the artifacts declared in
  `verification-plan.json` during one-shot dispatch.
- **Transition compare no-transition skip artifact.**
  `transition-compare.sh` now writes `transitions/result.txt` and
  `transitions/report.json` when no CSS transition elements are detected on
  the reference, and its cleanup trap preserves the intended zero exit status.
- **Asset placement host Python compatibility.**
  `asset-placement-check.sh` now defers inline Python annotation evaluation,
  matching the portability guard used by other shell-embedded Python scripts
  when host `python3` resolves to Python 3.9.

## [0.7.14] - 2026-05-26

Patch release for tree-diff verifier portability.

### Fixed

- **macOS tree-diff temp files.** `tree-diff.sh` now uses suffix-free
  `mktemp` templates and explicit temp-file failure handling, preventing
  BSD/macOS hosts from failing before writing `tree-diff-status.json`.

## [0.7.13] - 2026-05-26

Patch release for state-coverage goal routing and extractor portability.

### Fixed

- **State-coverage goal cards.** `ui_clone.goal` now maps the documented
  `state-coverage` gate to a concrete goal and gate command instead of
  reporting it as an invalid pipeline state.
- **Transition categorizer host Python compatibility.** `transition-categorize.sh`
  now defers inline Python annotation evaluation so hosts whose `python3`
  resolves to Python 3.9 can still enrich `transition-spec.json`.

## [0.7.12] - 2026-05-26

Patch release for portable extraction script durations.

### Fixed

- **macOS extraction duration reporting.** `download-chunks.sh` and related
  extraction scripts now use a shared Python monotonic millisecond helper
  instead of GNU-specific `date +%s%3N`, preventing successful artifact writes
  from ending with a duration arithmetic failure on macOS.

## [0.7.11] - 2026-05-26

Patch release for section-map-backed section matching.

### Fixed

- **Impl semantic wrapper pairing.** `section-compare.sh` now probes impl
  semantic wrappers and augments `impl-sections.json` from `section-map.json`
  identities before matching. This prevents synthesized ref rows such as
  `main#home` from being falsely paired to their first child section when the
  impl DOM contains the matching wrapper but runtime enumeration descended
  through it.

## [0.7.10] - 2026-05-26

Patch release for goal-card closeout consistency.

### Fixed

- **Goal-card done prerequisite guard.** `ui_clone.goal` now treats
  `current_gate == "done"` as incomplete when `pipeline-state.json` is
  missing earlier completed gates, routes the worker to the earliest missing
  gate, and makes `--check-done` exit non-zero instead of stopping on clean
  `sections/result.txt` alone.

## [0.7.9] - 2026-05-26

Patch release for section-compare failure status stability.

### Fixed

- **Section-compare failure guidance exit codes.** `section-compare.sh` no
  longer pipes long markdown guidance excerpts through `head` while
  `pipefail` is enabled, preventing SIGPIPE-derived `141` exits after
  artifacts are written. Visual mismatches now return the documented normal
  failure status.

## [0.7.8] - 2026-05-26

Patch release for earlier transition-spec grounding.

### Fixed

- **Spec-time `source_chunk` grounding.** The `spec` gate now rejects
  `transition-spec.json` entries whose `source_chunk` points outside captured
  bundle/CSS/HTML artifacts, using the same `inline init` sentinel accepted by
  `post-implement`. This catches canvas/runtime evidence references before
  generation instead of forcing a late `post-implement` repair.

## [0.7.7] - 2026-05-26

Patch release for unattended onpixel showcase loop automation.

### Added

- **Codex onpixel showcase runner.** Added
  `python -m ui_clone.onpixel_showcase_loop` to enumerate the local onpixel
  showcase catalogue, generate per-site handovers under `tmp/`, run an
  unattended Codex clone pass into `tmp/onpixel-codex-loop/<slug>/impl`, and
  optionally run a separate skill-fix Codex pass from the clone evidence.
  Clone work and plugin repair are deliberately separated so legitimate skill
  fixes do not happen inside an impl iteration. Each Codex pass also writes a
  polled `codex-*-status.json` heartbeat next to the JSONL transcript so a
  sleeping/remote operator can check progress without a visible terminal tab.

## [0.7.6] - 2026-05-26

Patch release for transition roll-up artifact-name parity.

### Fixed

- **Scroll completion roll-up wiring.** `transition-proof-rollup.sh` now reads
  the canonical `scroll-completion.json` artifact produced by
  `scroll-end-completion-check.sh` and declared by `verification-plan.json`,
  so stuck scroll-scrub failures are included in `transition-proof.json`
  instead of being skipped as not applicable.

## [0.7.5] - 2026-05-26

Patch release for transition-proof strictness and historical changelog context cleanup.

### Fixed

- **Video-motion verdict strictness.** `transition-proof-rollup.sh` now fails
  when `transitions/video-motion-result.txt` exists but contains no clear
  PASS/FAIL marker, and it fails when `verification-plan.json` requires
  video-motion but the artifact is missing.
- **Historical changelog context.** Clarified the older hover/click
  multi-viewport note to point forward to the later v0.7.3 section-compare
  fan-out instead of leaving stale "out of scope for this release" phrasing.

## [0.7.4] - 2026-05-26

Patch release for multi-viewport closeout routing and transition proof
strictness.

### Fixed

- **Viewport-aware visual-judge routing.** `ui_clone.goal` now resolves
  multi-viewport `section-compare` rows like `[375x812] Hero Section` to
  `sections/viewports/<WxH>/sections/{ref,impl}/...` PNGs, so goal cards can
  still emit concrete `visual-judge.sh` commands after viewport fan-out.
- **Phase 6d transition proof strictness.** `transition-proof-rollup.sh` no
  longer accepts declaration-only `transition-coverage.json` as runtime proof
  unless a real runtime proof artifact passed (`reveal-trigger`,
  `scroll-end-completion`, or `video-motion`).

## [0.7.3] - 2026-05-25

Patch release for motion-fidelity follow-through after v0.7.2.

### Added

- **Section-compare viewport fan-out.** `section-compare.sh` now supports
  opt-in `VIEWPORTS=<WxH>,<WxH>` fan-out. The default single-viewport path is
  unchanged; fan-out runs each viewport into `sections/viewports/<WxH>/` and
  writes an aggregate `sections/result.txt` that remains parseable by gates.
- **Runtime GSAP target coverage advisories.** `runtime-spec-coverage.sh`
  now records unique GSAP timeline target coverage and emits a warning when a
  rich runtime timeline is only partially represented in `transition-spec.json`.

### Fixed

- **Nested duration/easing grounding.** `duration-easing-grounding-check.sh`
  now reads nested `animation.duration`, `animation.ease`, and other timing
  fields in `transition-spec.json`, so spec entries with structured animation
  blocks are not skipped.
- **Completion report structural-only visibility.** `completion-report.sh`
  now parses section rows instead of the summary footer and surfaces broad
  `STRUCTURAL_ONLY` coverage as a pixel-polish advisory.

## [0.7.2] - 2026-05-25

Patch release for downstream wiring exposed by the v0.7.1 hardening pass.

### Fixed

- **Runtime spec coverage for GSAP metadata.** `runtime-spec-coverage.sh`
  now validates captured `gsapTimelines` targets and `customEaseRegistry`
  keys, so runtime animation extraction fields added in v0.7.1 are consumed
  by downstream coverage instead of sitting unused.
- **Stop-hook active ref cleanup.** Active WIP refs now expire implicit
  activation by ref activity TTL and LRU-prune to the newest two sessions by
  default, removing stale explicit markers instead of letting old sessions
  stack indefinitely.
- **Goal-card structural-only advisories.** Broad-but-below-cap
  `STRUCTURAL_ONLY` coverage now appears in the goal card next action even
  when section-compare is otherwise clean, making skipped pixel AE polishing
  visible before an agent reports completion.

## [0.7.1] - 2026-05-25

Patch release for the post-0.7.0 verification hardening pass. The theme is
making fidelity gates harder to bypass while keeping legitimate canvas,
asset-substitution, and runtime-schema escape hatches explicit.

### Added

- **Canvas-replay closeout path.** Added `closeoutPolicy="canvas-replay"`
  routing, attestation files, stamp enforcement, and AE relief for
  sections tagged `kind="canvas"` in `section-map.json`. Relief widens the
  canvas AE/Mpx band; it does not bypass structural integrity, text
  fidelity, or non-canvas failures.
- **Reference asset allowlists for canvas replay.** Added attested
  ref-screenshot and ref-JS-loader allowlists so procedural canvas replay
  can use documented source material without tripping screenshot/proxy
  anti-cheat gates.
- **Runtime animation extraction depth.** `extract-animation-runtime.sh`
  now captures GSAP `CustomEase` registry data, tween targets, and timeline
  children so downstream coverage can reason about named ease curves and
  timeline structure.
- **Browser integration coverage for capture scripts.** Added opt-in
  browser tests around capture helpers and fixed the bugs those tests
  exposed.

### Fixed

- **Section-compare structural-only overreach.** The section gate now warns
  when 30%+ of rows are `STRUCTURAL_ONLY` even below the hard 50% bypass
  cap, with an explicit note that pixel AE polishing was skipped for those
  sections and instructions to narrow `asset-substitution.json`.
- **Verification-plan freshness.** Staleness detection now includes
  `states/*`, `animation-runtime-dump.json`, and derived splash/hover
  signals, so a plan generated before Phase A/B/C or runtime extraction
  gets regenerated instead of silently skipping motion checks.
- **State coverage strictness.** Motion-rich refs now fail closed when impl
  source lacks matching hooks, while generic class noise and comments are
  filtered to reduce false evidence.
- **Runtime and verification false positives.** Fixed structural-only
  auto-verify handling, Phase 6d ref-side transition-coverage schema
  tolerance, self-declared skip classification, quoted `agent-browser`
  eval output, Lottie false positives, and hero-composite absent-kind
  handling.
- **Hook and scope robustness.** `impl-scope-check.sh` now quotes its
  heredoc and passes `ALL_CHANGES` through the environment; closeout
  policy attestations and stamps are allowlisted in canonical ref
  artifacts; `ci-local.sh` self-heals stale drift-test markers.

### Changed

- **CI and metadata sync.** Deduplicated pytest/security work across the
  push pipeline and synchronized package/plugin versions at 0.7.1,
  including the previously stale `ui_clone.__version__`.
- **Plugin-fix escalation policy.** Documented how iteration agents escalate
  legitimate plugin bugs without bypassing `impl-scope-check.sh`, including
  regression-test expectations and baseline reset rules.

## [0.7.0] - 2026-05-25

Multi-snapshot capture + claude-fidelity release. Closes the largest
capture-side architectural gap revealed by the 26-site loop: a single
"settled" DOM snapshot misses splash transitions, scroll-driven state,
and hover state — and impl agents had to guess at the bridge between
no-render and final-render. This release captures all three phases and
adds a new gate that fails post-implement when impl source has none of
the hooks the multi-snapshot artifacts reveal.

Also folds in the claude-fidelity anti-cheat + escape-hatch battery
(F1 stub-element detector, F+E1 spec-bundle grounding + bundle-grep
context inject, D visual-judge-dispatcher) that landed during the
26-site loop diagnosis.

Bumped MINOR (not patch) because the release adds new public surface:
a new gate (`state-coverage`) in GATE_ORDER between `pre-generate` and
`post-implement`, three new shell entry points
(`scripts/extract/capture-{states,scroll,hover}.sh`), and a new
`ui_clone.visual_judge_dispatcher` module. Old ref dirs without
`states/` continue passing the new gate silently (backward-compat skip).

### New: multi-snapshot capture (Phase A + B + C)

**Phase A — splash transitions** (`scripts/extract/capture-states.sh`,
245 LOC, commit e2e5657). Single in-page Promise loop polls a composite
state hash (html/body class + scroll lock + full-screen overlay
presence + DOM length + computed-style fingerprint of top-3
above-the-fold elements) at 100ms intervals with a 5s wall cap and 2s
stability break. Compact deltas in `states/splash/trajectory.json`,
full outerHTML only for `0ms.json` / `settled.json` bookends and
structural mutations above 20% DOM delta. Six-item codex architectural
review applied before merge.

**Phase B — scroll snapshots** (`scripts/extract/capture-scroll.sh`,
290 LOC, commit 509a498). DOM state captured at 7 scroll percentages
[0, 10, 25, 50, 75, 90, 100]. Codex review applied: Lenis/Locomotive
scroll-engine detection with API delegation, 500ms floor +
hash-stability polling per stop, `scrollHeightDeltaPct` numeric exposed
instead of crude infinite-scroll boolean threshold. Output: per-pct
outerHTML + visibleSections in `states/scroll/<pct>pct.json`, compact
`trajectory.json`, `summary.json` with scrollEngine + static flags.

**Phase C — hover snapshots** (`scripts/extract/capture-hover.sh`, 310 LOC,
commit abc5328). Single in-page eval captures BOTH CSS `:hover` rule
signal (static CSSOM extraction — codex review item 1 surfaced that
synthetic dispatchEvent does NOT activate CSS :hover, so the rule body
IS the signal) AND JS-handler hover signal (synthetic
mouseover/mouseenter/mousemove dispatch + computed-style diff against
rest-state). Selector parser splits `.card:hover .title` into
activation=".card" + affected=".card .title". Per-elem files in
`states/hover/elem-<id>.json`, stable-id manifest with
`{id, kind, file, selector, activation, changedCount, schemaVersion}`.
C1 grid sweep dropped — agent-browser has no pixel-coord cursor primitive;
deferred to a future C3 mode driven by CDP `Input.dispatchMouseEvent`.

### New gate: `state-coverage`

Inserted into `GATE_ORDER` between `pre-generate` and `post-implement`.
Reads the three Phase A/B/C summaries + trajectories + manifest, then
grep-checks `impl/src/**/*.{tsx,ts,jsx,js,css,scss,vue,svelte,html}`
for matching hooks:

- **splash**: if `polls > 1`, impl must reference at least one captured
  body/html class string (`is-loading`, `is-loaded`, ...).
- **scroll**: if `static: false`, impl must use a scroll-state primitive
  (IntersectionObserver, ScrollTrigger, useScroll, useInView,
  scroll-snap, data-scroll, data-aos, framer-motion useScroll).
- **hover**: if `manifest.entries` non-empty, impl must have at least
  one hover handler (`:hover`, Tailwind `hover:`, `group-hover:`,
  `onMouseEnter`, `whileHover`, raw `mouseenter`/`mouseover` listeners).

Backward-compat: when `<ref_dir>/states/` is absent → single pass with
"no states/ directory — multi-snapshot capture not run (skip)". Partial
captures (e.g., splash but no hover) check only the present phases.

### Claude-fidelity battery (folded from 26-site loop diagnosis)

- **F1 — stub-element detector** (commit 837aa74): post-implement
  anti-cheat that catches impls with `<div>section</div>`-style stub
  elements masquerading as real content.
- **F + E1 — spec-bundle grounding + bundle-grep helper** (commits
  0a29a7c, 9eb7c3e): when post-implement fail count climbs, the gate
  emits a context injection with `bundle-grep <selector>` output
  showing the actual ref-bundle code for the worst-AE selectors.
- **D — visual-judge-dispatcher** (`ui_clone.visual_judge_dispatcher`,
  commit 84aaa5e): escape-hatch dispatcher that launches the LLM
  vision review (Phase E) when AE/SSIM + auto-diagnose loops are stuck
  >5 iterations. Cache-read integration into goal card rendering and
  operator escape-hatch script (`scripts/visual-judge-escape.sh`).

### Tests

- `tests/test_capture_states.py` — 6 tests, fake-`agent-browser`-on-PATH
  pattern.
- `tests/test_capture_scroll.py` — 10 tests.
- `tests/test_capture_hover.py` — 11 tests.
- `tests/gates/test_state_coverage.py` — 12 tests, pass/fail/skip
  scenarios for each phase + GATE_ORDER positioning check.

### Canvas-replay closeout policy (opt-in)

Operator-signed escape hatch for refs whose visual identity is
imperative-canvas-driven (WebGL UnicornStudio scenes, generative
scroll-driven plates, music sphere / `.bg-canvas` arc renderers). The
30-min canvas CSS replication cap remains the default — canvas-replay
is opt-in and requires explicit license attestation.

**Foundation** (commit e903334):
- New `PipelineState.closeout_policy = "canvas-replay"` (third value
  alongside `canonical` and `structural`).
- New Stop-hook enforcer `_enforce_canvas_replay_stamp` in
  `ui_clone/hooks/section_gate.py` — stamp freshness, sha256 tamper
  detection, impl-newer-than-stamp guard.
- New stamp writer `scripts/verify/check-canvas-replay.sh --write-stamp`
  validates `canvas-replay-attestation.json` shape + records sha256.
- Operator doc `skills/ui-reverse-engineering/canvas-replay-mode.md`.

**Gate-side relief** (three follow-up commits):
- New `ui_clone/policies/canvas_replay.py` helper resolves the 3-condition
  gate (closeoutPolicy + attestation file + section.kind=="canvas") into
  a single set lookup. Fail-closed on every missing condition.
- **`section-compare`**: critical AE/Mpx ceiling widens from >20000 to
  >40000 for canvas-tagged sections. Rows within the widened band
  downgrade FAIL → PASS; rows above stay critical (relief widens the
  band, it does not bypass). STRUCTURAL_ONLY critical-override,
  threshold-gaming, and missing-impl checks remain strict.
- **`ref-js-loader`**: URLs declared in `attestation.ref_canvas_sources[]`
  are exact-string allowlisted (both static scan and runtime probe).
  Other ref bundles from the same host still fail — exact URL equality,
  not origin allowlist (codex review Q3).
- **`ref-screenshot-asset`**: byte-identical-copy violations whose source
  PNG belongs to a kind="canvas" section are allowed. `ref-path-reference`
  (generic substring leaks) stays strict because the substring doesn't
  pinpoint a section.

**Boundary** (codex review applied to foundation): canvas pixels ONLY.
Text fidelity, font parity, runtime-DOM parity, transition-compare
remain unaffected by all four relief points.

**Tests**: 12 unit tests for the policy helper (all fail-closed paths +
alias resolution), 7 section-compare boundary tests, 4 ref-js-loader
integration tests, 4 ref-screenshot-asset integration tests.

## [0.6.0] - 2026-05-24

Section-staged convergence release: a separate closeout proof for plans that
opt into structural section convergence (canonical verify-stamp.json untouched),
plus a wave of "stop the loop from running forever" fixes — terminal state
written to disk instead of inferred from a banner, abort cards no longer
contradict themselves with a runnable "Next action", and concurrent driver
sessions stop stomping each other's bypass marker. Net effect: previously
human-driven termination decisions (record-unclonable, switch to next URL)
now ride the same persisted state every consumer reads, and validation loops
self-terminate at the hard cap instead of needing a typed `stop`.

Bumped MINOR (not patch) because the release adds new public surface: a new
importable / CLI module (`ui_clone.driver_session`), a new field on the
`pipeline-state.json` wire format (`closeoutPolicy`), a new convergence
detector with its own artifact type (`structural-convergence-stamp.json`),
and new operator shell entry points. Pre-1.0 semver keeps the contract
advisory, but each item individually justifies a minor signal to downstream
consumers (Claude marketplace, codex plugin marketplace).

### Added

- **`scripts/verify/check-converged.sh`** — canonical section-convergence
  detector (Stage 0). Reads `tmp/ref/<comp>/sections/result.txt`'s last
  `**Result: ...**` line, exits `0` iff `0 FAIL` (STRUCTURAL_ONLY counted as
  PASS, SKIP non-gating). Accepts both 4-field and 3-field Result formats so
  Stage B early-exits (e.g. linear.app fingerprint extraction returning 0
  matching sections) don't trip a setup-error exit. `--write-stamp` flag
  emits `structural-convergence-stamp.json` with canonical fields the
  Stop hook validates.
- **`scripts/loop/{launch,finalize}-stage.sh`** — pure-function translators
  between stage labels {A,B,C,D} and the shell commands an operator copy-pastes
  to start / finalize one loop tab. Resolves plugin_root from
  `PLUGIN_ROOT` / `CLAUDE_PLUGIN_ROOT` / `CODEX_PLUGIN_ROOT` with a
  script-relative fallback (no hardcoded paths per
  `scripts/ci/check-universality.sh`).
- **`PipelineState.closeout_policy`** — `canonical` (default, existing
  verify-stamp.json path) or `structural` (Stop satisfied by
  `structural-convergence-stamp.json` instead). The two stamps are never
  interchangeable; the policy decides which writer counts.
  `_enforce_ref_dir()` routes to `_enforce_structural_convergence_stamp()`
  when the policy is `structural`. Codex architectural review explicitly
  rejected scope-reducing verify-stamp.json (option A, breaks 15+ consumers)
  and a state-only graceful mode (option B, forgeable past Stop's freshness
  + canonical-writer signatures) in favor of this separate-artifact route.
- **`ui_clone.hooks.section_gate._enforce_structural_convergence_stamp`** —
  Stop-hook enforcer for the new structural closeout path. Same anti-cheat
  invariants as the canonical verify-stamp enforcer (fresh stamp, canonical
  writer (`scripts/verify/check-converged.sh`), impl files no newer than the
  stamp, sha256 of `sections/result.txt` matches what was stamped). The
  result.txt rehash specifically blocks the "stamp while converged, then
  edit result.txt to claim more PASS rows" cheat. `_enforce_ref_dir()`
  routes between the canonical and structural enforcers based on
  `closeout_policy`.
- **`ui_clone.driver_session`** — append-if-missing writer for
  `.driver-session.id`. `register(session_id, project_root)` and
  `register_from_env(project_root)` hold `fcntl.flock(LOCK_EX)` on a sibling
  `.lock` file for the entire read-modify-write so two concurrent driver
  sessions don't stomp each other's IDs. Atomic rename alone (the manual
  `echo > .driver-session.id` pattern) has a TOCTOU window the lock closes.
  CLI: `python -m ui_clone.driver_session register <id>` and
  `register-from-env`. Shell shim at `scripts/register-driver-session.sh`.

### Changed

- **`state.PipelineState.mark_failed`** — when the per-gate consecutive-fail
  counter crosses `HARD_CAP_GATE_FAILS` (= 10), the state automatically
  records a canonical `category="hard-cap-fail"` entry into
  `unclonable_reasons`. Closes the gap that left `goal.py` rendering an
  `abort_banner` while `pipeline-state.json` had no canonical reason: the
  Stop hook re-enforced the failing gate forever because it only releases
  on canonical unclonable reasons. Observed before the fix: a benchmark
  target reached 97 consecutive `post-implement` failures and another hit 6
  without any of them triggering Stop bypass — a human had to type `stop`
  / `record unclonable` to terminate. Now: one persisted source of truth
  (Stop hook, goal card, `--check-done` exit code, benchmark harness all
  read the same state). `HARD_CAP_GATE_FAILS` re-exported from `goal.py` as
  `_MAX_GATE_FAILS` for `benchmark_harness` back-compat.
- **`goal.build_goal_card`** — when `abort_banner` is active, the rendered
  text card suppresses the runnable block (Mission / Current goal /
  Next action / Stop condition / Required evidence / No infinite loop) and
  emits only the abort reason + an explicit "Terminal state" notice +
  current_gate + manual_refresh. The driving LLM observed prioritizing
  "Next action" over "ABORT" in the same card, producing the 97-fail / 6-fail
  runaways even with the abort banner firing every cycle. JSON drivers
  (`to_json`) unchanged — programmatic consumers still see the full
  structured fields; the contradiction is removed from text rendering only.
- **`skills/visual-debug/scripts/build-decode-receipt.sh`** — receipt now
  surfaces the structural-convergence stamp alongside the canonical verify
  artifact so the decode receipt accurately reflects either closeout mode.

### Fixed

- **`scripts/hooks/pre-push-guard.sh`** — the skills/-policy reject branch
  was writing its block message to stdout while every other reject branch
  used `>&2`. Claude Code's PreToolUse hook harness only surfaces stderr,
  so this one branch produced an opaque "No stderr output" failure that
  burned debug iterations. All `echo` calls in this branch now route to
  stderr and explicitly tell the operator what to do (bump CHANGELOG +
  3 manifests, or revert the incidental skills/ change).
- **`tests/test_check_converged.py`** — `_read_stamp` was returning the raw
  `json.loads` result against a `dict` annotation, producing
  `[no-any-return]` from mypy. Cast through `dict[str, Any]` so the
  function returns a concrete typed shape and `scripts/ci/ci-local.sh`
  passes cleanly.

### Operational notes

- Stop-hook driver-session bypass marker is now multi-line by design (the
  reader was already set-based as of `c98da29`; this release wires the
  matching writer). If operators were manually managing `.driver-session.id`
  via `echo >`, prefer the helper script going forward — concurrent driver
  sessions starting roughly together would otherwise still race.
- `closeout_policy` is omitted from `pipeline-state.json` when the value
  equals the default (`canonical`) so legacy state files stay diff-clean.
  Plans that opt in write `closeoutPolicy=structural` and reload preserves it.

Version bumped 0.5.1 → 0.6.0 across `.claude-plugin/plugin.json`,
`.claude-plugin/marketplace.json`, `.codex-plugin/plugin.json`,
`pyproject.toml`, and `ui_clone/__init__.py` per the
`scripts/hooks/pre-push-guard.sh` version-sync rule.

## [0.5.1] - 2026-05-22

Baseline-clearing patch released ahead of the deferred-refactor sweep (HANDOVER.md items 1-6). Three blockers were flagged by `scripts/ci/ci-local.sh` against the v0.5.0 working tree and are cleared here so the refactor commits land on a green baseline:

### Fixed

- **`tests/test_measure.py`** — `ruff` I001 (un-sorted local imports) and 2× F841 (unused `proc =` capture) inside the two duration-easing-grounding tests added in commit `83113e4`. `subprocess` and `pathlib.Path` are already imported at module top; the local re-imports were dead. The unused `proc =` captures were noise — neither test inspects returncode/stdout/stderr (they read the JSON artifact via `json.loads` directly).
- **`skills/ui-reverse-engineering/SKILL.md`** — `scripts/ci/review.sh` language check (English-only rule) caught a Korean one-line summary at line 122. Translated to English while preserving the same content ("runtime-proof + transition-proof must PASS — section-compare alone is not enough; rely on browser-runtime evidence, not static DOM / screenshots / original JS").

No behavior changes. No skill or pipeline logic touched. Version bumped 0.5.0 → 0.5.1 across `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.codex-plugin/plugin.json`, `pyproject.toml`, and `ui_clone/__init__.py` per the `scripts/hooks/pre-push-guard.sh` version-sync rule. The 0.5.0 release (`c0c0659`) was committed without a CHANGELOG entry; future iteration of `claude-md-improver` or a dedicated backfill can reconstruct that entry from its commit message, but it is not in this patch's scope.

## [0.4.10] - 2026-05-15

Signal-derived verification dispatch + sub-doc decoupling, first-class Claude Code/Codex packaging, plus a context-budget pass on every auto-loaded surface. The post-implement gate previously enforced a fixed list of checks, which meant either running every check on every site (slow, noisy, false positives) or skipping by gut feel. This release introduces `verification-plan.json` — a tiny manifest written between bundle analysis and code generation that declares which classes of bug to look for *on this specific site*, derived from the extraction artifacts. Three new scripts (`hydration-check.sh`, `scroll-end-completion-check.sh`, `tailwind-transform-conflict-check.sh`) plus a JSON-sidecar emission from `transition-spec-coverage.sh` are wired into that dispatch, closing two silent-pass loopholes: (1) the `transition-spec-coverage` gate was marked MANDATORY but had no machine-readable output, so `gate_post_implement` had no way to enforce it; (2) text-artifact `produces` files (like `transitions/result.txt`) were treated as opaque-pass by the post-implement gate, letting ❌ FAIL lines through. Also folds a doc-pipeline + host-packaging pass: 7 new sub-docs split from oversized parents (count 39 → 46), generic placeholders replace hardcoded a partner site CDN domains in `generation-pitfalls.md`, README installation is split into Claude Code/Codex paths, and `AGENTS.md` becomes the canonical cross-agent guide imported by `CLAUDE.md`. Final pass slims auto-loaded surfaces: `AGENTS.md` 141 → 83 lines (re-injected every turn), `skills/ui-reverse-engineering/SKILL.md` 394 → 330 lines (re-read on SessionStart/PostCompact), plus `python -m ui_clone.goal --check-done` for external loop drivers (used by `ui_clone.benchmark_harness` to decide convergence). Interactive Codex uses the native [`/goal`](https://developers.openai.com/codex/use-cases/follow-goals) feature, and the `benchmark` Python harness drives `claude --print` headlessly — both auto-load the `ui-reverse-engineering` skill / `AGENTS.md` context.

### Added

- **`skills/visual-debug/scripts/verification-plan.sh`** — synthesizes `tmp/ref/<c>/verification-plan.json` from extraction artifacts. Reads `bundle-map.json` / `interactions-detected.json` / `external-sdks.json` / `scroll-engine.json` / `transition-spec.json` / `canvas-webgl-detection.json` / `paid-features.json` and emits a `signals` block (`hasScrollScrub` / `hasIOReveal` / `hasHover` / `hasSplash` / `hasCanvas` / `hasCustomScroll` / `hasCommercialFont`) plus a `requiredChecks[]` dispatch list. Two universal rows always present (`hydration-check`, `tailwind-transform-conflict`); the rest are signal-conditional. Each row pins script + produced artifact + reason + severity, so `gate_post_implement` knows exactly what to run and what to enforce per site. Signals are OR-of-proxies on purpose — false-positive runs an extra check (cheap), false-negative misses a bug class (expensive).
- **`skills/visual-debug/scripts/hydration-check.sh`** — universal check enforced via `verification-plan.json`. Loads the impl page, captures console errors / unhandled-rejections / hydration boundary mismatches (Next.js, React Server Components, SolidStart, Astro Islands), and writes `hydration-check.json`. Catches a failure class AE/SSIM is blind to: page renders pixel-identical to ref but mounts on top of broken state (event handlers missing, useEffect never fires, server-client markup diverges). Now invoked explicitly in `SKILL.md` Step 8-pre alongside `stray-absolute-check` so failures surface before AE wastes a sweep.
- **`skills/visual-debug/scripts/scroll-end-completion-check.sh`** — conditional check fired when `signals.hasScrollScrub=true`. Drives the impl to `maxScroll` at every viewport declared in `verification-plan.json.viewports[]` and verifies scroll-driven reveals reach their declared end state (opacity 1, transform none). Catches stuck-reveal bugs that only manifest at scroll end — IntersectionObserver fires, transform never resolves.
- **`skills/visual-debug/scripts/tailwind-transform-conflict-check.sh`** — universal check. Greps the impl for elements with both `transform:` shorthand AND individual `translate:` / `rotate:` / `scale:` properties on the same selector. Tailwind v3 emits `transform: translate3d(...)` shorthand; v4 emits individual properties. When both are present (mixed-version `@apply` chains, third-party CSS) the v4 individual properties stack on top of the v3 shorthand and translate doubles. Writes `tailwind-conflict.json`; passes silently on hosts that don't use Tailwind. Closes Root Cause I in `diagnosis.md`.
- **`scripts/cleanup-sessions.sh`** — bulk closes every `<project>*` agent-browser session in one call. Used at end-of-run after the previous "session-per-probe" anti-pattern accumulated dozens of stale Chrome instances. SKILL.md session rule now mandates reuse-per-role (`<project>` for primary work, `-ref` for parallel reference, `-probe` for throwaway evals) — no `-debug1`, `-replay-sc`, `-foo-check` proliferation.
- **`skills/ui-reverse-engineering/verification-plan.md`** — documents the new `verification-plan.json` + `known-artifacts.json` two-file pattern. The plan is the *required-checks manifest* (auto-generated, refreshed any time upstream artifacts change); `known-artifacts.json` is the *capture-timing alibi log* (agent-curated, records section / transition FAILs verified as non-deterministic ref or capture-timing artifacts so they don't re-block on every iteration).
- **`skills/ui-reverse-engineering/dom-splash-snapshot.md`** — Step 2.6-pre body extracted (dual-snapshot DOM diff for sites with timed splash overlays). `dom-extraction.md` now carries a conditional pointer.
- **`skills/ui-reverse-engineering/gsap-alternatives.md`** — GSAP premium-plugin → OSS-alternative table (SplitText → `splitting`, ScrollSmoother → `lenis`, MorphSVG → `flubber` / manual `rx`/`ry`, DrawSVG → `stroke-dashoffset`). Split from `transition-implementation.md` with a backref pointer so the anti-pattern rules (SplitText mask CSS, IntersectionObserver placement for masked reveals) stay in `transition-implementation.md` where they apply regardless of which OSS swap-in is used.
- **`skills/ui-reverse-engineering/hover-timing-extraction.md`** — Step 5d-3 JS-driven hover-timing procedure (`getAnimations()` walk + bundle-grep fallback). Split from `interaction-detection.md` (~145 line reduction).
- **`skills/ui-capture/capture-click-content-swap.md`** — Step 2C-swap procedure for click-driven content swaps (tabs, accordion, dropdown). Split from `capture-transitions.md`.
- **Codex host packaging.** Added `.codex-plugin/plugin.json`, `hooks/codex-hooks.json`, and `skills/*/agents/openai.yaml` metadata so the same three skills can be exposed through Codex plugin/skill surfaces while keeping `.claude-plugin/` as the Claude Code manifest path.
- **Canonical `AGENTS.md` guide.** `AGENTS.md` now carries the project rules, gate/artifact mapping, and host compatibility notes; `CLAUDE.md` is a thin `@AGENTS.md` entrypoint for Claude Code.
- **Generic push hooks.** Added `scripts/pre-push-guard.sh` and `scripts/post-push-refresh.sh` as host-neutral local push hook entrypoints.
- **Loop-driven worker mode (Python-harnessed by default).** Benchmark automation uses `python -m ui_clone.benchmark_harness` which invokes `claude --print` headless per iter with focused per-iter prompts and STRICT v2 stop conditions. Interactive Codex uses the native [`/goal`](https://developers.openai.com/codex/use-cases/follow-goals) feature (Codex CLI ≥ 0.128.0 with `[features] goals = true`) with the same one-line pattern — AGENTS.md auto-loads the rest. Both are bounded against `--check-done`'s exit code: 0 (done), 2 (ABORT — `unclonable_reasons[]` populated), other (keep iterating).
- **`python -m ui_clone.goal <ref-dir> --check-done`** — exit 0 only when `current_gate == "done"` AND `sections/result.txt` is clean (0 FAIL / 0 MISSING impl). Suppresses normal output. Designed as the loop-exit predicate for external drivers (Ralph loop, `codex exec`) so a bash `while ! python -m ui_clone.goal <ref-dir> --check-done; do ...; done` halts on the same stop condition the goal card advertises.
- **Stuck-detection enforced in the goal card.** `ui_clone/state.py` now persists `gate_fail_counts: dict[str, int]`; `python -m ui_clone.gate` bumps the counter on BLOCKED runs of the *active* gate (failing a past gate does not pollute the cursor) and resets it on PASS. When the counter reaches 3, `python -m ui_clone.goal` renders a `STUCK` banner above the next-action line routing the worker to `diagnosis.md` / `patterns.md` / `visual-debug/SKILL.md` *before* the next iteration. Previously this rule lived only in worker prompts (templates), which agents could ignore. Locked in by `tests/test_state.py::test_mark_failed_*`, `test_mark_passed_resets_fail_counter`, and `tests/test_goal.py::test_goal_card_emits_stuck_banner_at_threshold`.
- **Abort-with-reason signal for unclonable sites.** `ui_clone/state.py` now persists `unclonable_reasons: list[dict]`; `PipelineState.record_unclonable(gate, reason, ref_dir, detail=...)` is the recording API (idempotent on `(gate, reason)`). When non-empty, the goal card emits an `ABORT` banner *above* the next-action line, and `python -m ui_clone.goal --check-done` exits with code `2` (distinct from `1` = not-yet-done). Loop drivers recognize exit `2` and stop with a "site is unclonable" message instead of grinding to max-iterations. Closes the failure mode where paid-font / DRM-canvas / auth-gated sites would loop forever because every gate keeps FAILing for the same upstream reason. Locked in by `tests/test_state.py::test_record_unclonable_*` and `tests/test_goal.py::test_check_done_exit_2_on_abort`.
- **Install-root marker (`~/.config/ui-clone-skills/root`).** `install.sh` writes this file at install completion. The inline preflight bash in `SKILL.md` / `site-detection.md` and shared scripts (`scripts/verify/auto-verify.sh`) now read this marker as one more fallback path when `PLUGIN_ROOT` / `CLAUDE_PLUGIN_ROOT` / `CODEX_PLUGIN_ROOT` / `UI_CLONE_ROOT` are all unset. Fixes the case where `~/.claude/skills/ui-reverse-engineering` is symlinked to only the skill subdirectory, so the preflight could not locate `ui_clone/pipeline.py`.
- **Sub-doc count: 39 → 46.** Across three README locations. New sub-docs added across this release: `verification-plan.md`, `dom-splash-snapshot.md`, `gsap-alternatives.md`, `hover-timing-extraction.md`, `capture-click-content-swap.md`, `reference-index.md`, `operational-rules.md`.
- **`skills/visual-debug/scripts/video-motion-compare.sh`** — closes the "right end-state, wrong trajectory" failure class on motion-driven sites with frame-accurate verification. `section-compare.sh` and `scroll-end-completion-check.sh` only verify resting frames; an impl with same destination but different easing or different scroll threshold passes both and still feels wrong. The new wrapper reads `verification-plan.json` signals and runs `scripts/verify/video-transition-compare.sh` at 60fps (canonical) in `splash` mode (if `hasSplash`) and/or `scroll` mode (if `hasScrollScrub` or `hasIOReveal`), aggregating to `<ref-dir>/transitions/video-motion-result.txt`. Wired into `verification-plan.sh` as a `block`-severity row whenever any motion signal is true. Replaces the prior 5-point AE trajectory probe in dispatch — same-end-state-same-midpoint-different-velocity (e.g. `easeOutCubic` vs `easeOutQuint`) passes a 5-point sampler but feels different to a user; 60fps frame-by-frame SSIM catches it. `transition-trajectory-compare.sh` is retained as a fast ad-hoc probe (no longer in dispatch).
- **`skills/visual-debug/scripts/click-state-compare.sh`** — closes the "click-driven UI motion arc never verified" failure class. Static hover-compare and section-compare both verify resting frames; click-driven UI (tabs, accordions, modals, hamburger menus, content swaps) has its own motion arc — open/close timing, panel entry/exit curves, stacking-order swap — that none of the existing rows exercise. Reads `regions.json` for entries whose `triggerType` starts with `click-` (click-toggle / click-cycle / click-content-swap), dedupes by selector, and runs `video-transition-compare.sh` in `click:<selector>` mode for each (capped at `MAX_CLICK_TARGETS=5` by default). Aggregates to `<ref-dir>/transitions/click-state-result.txt`. New signal `signals.hasClickStateTransition` derived from `regions.json` `triggerType` prefix, `interactions-detected.json` `trigger: "click"`, or `transition-spec.json` `trigger: "click"`. Wired into `verification-plan.sh` as a `block`-severity row whenever the signal is true.
- **`scripts/extract/extract-animation-runtime.sh`** — Phase 0 of `animation-detection.md`. `agent-browser eval` against the live ref page that dumps runtime animation parameters bundle-grep (Step 4) and video phases never see: `ScrollTrigger.getAll()` with RESOLVED pixel start/end (not `"top 80%"` source), GSAP tween duration + ease `.toString()`, `document.getAnimations()` timings + easing strings, Lenis options including `easing` function source (truncated to 400 chars), Webflow IX2 timeline IDs + event count. **Walks 5 scroll fractions** (`[0, 0.25, 0.5, 0.75, 1.0]`) with 250ms settle between each, deduping by `trigger|start|end` (ScrollTrigger) and a JSON-stringified subset (web-animations) — a single page-load dump misses below-fold ScrollTrigger entries registered lazily as sections mount. Restores original scroll on exit so downstream operations are not stuck at bottom. Writes `tmp/ref/<component>/animation-runtime-dump.json` — missing runtimes emit as `null` (not omitted) so downstream code can shape-check once. `transition-spec-rules.md` Rule 7 now requires consulting this file when authoring `transition-spec.json` so easing/threshold values are not silently lost between extraction and generation.
- **`skills/visual-debug/scripts/hover-state-compare.sh`** + `hover:<selector>` and `hover-and-out:<selector>` action modes in `scripts/verify/video-transition-compare.sh`. Closes the "hover motion arc never verified" failure class. `transition-compare.sh` captures idle/hover as two screenshots and AE-diffs the resting frames — the easing/duration arc between them was invisible to that check (same bug class as the trajectory→video pivot for scroll motion). Reads `regions.json` for entries whose `triggerType` matches the hover family (`hover`, `css-hover`, `scale-on-hover-target`, …), dedupes by selector, and runs `video-transition-compare.sh` in the new `hover:<selector>` mode for each (capped at `MAX_HOVER_TARGETS=5`). The hover mode uses agent-browser's real-mouse `hover` command so the `:hover` pseudo-class fires (synthetic `mouseenter` events do not trigger CSS `:hover`). Captures the entry arc only by default — exit is symmetric in most designs and doubling the per-target time budget is not worth the bug coverage. **Opt-in `HOVER_EXIT_CAPTURE=1`** switches to `hover-and-out:<selector>` mode which hovers, records the entry arc, then issues an `agent-browser mouse move <MOUSE_AWAY_X> <MOUSE_AWAY_Y>` (default `0 0`; `MOUSE_AWAY_X` / `MOUSE_AWAY_Y` env overridable for sticky-header sites where the corner is itself a hover target) and records the exit arc — total per-target time = 2 × `RECORD_DURATION`. Reserved for sites with asymmetric exit designs: Webflow IX2 "On Mouse Leave" handlers, distinct exit-easing keyframes, or group-hover unwind chains the entry sweep cannot exercise. Wired into `verification-plan.sh` as a `block`-severity row when `signals.hasHover=true`, alongside the existing `transition-compare` row (the two cover different bug classes: end-state vs arc).
- **`skills/visual-debug/scripts/spec-implementation-coverage.sh`** — closes the silent-killer "selector matched but no motion declared" gap on the spec→generation seam. `transition-spec-coverage.sh` answers *does the impl mention this entry?* — useful as a pre-generate sanity check, but it passes the moment a class name appears anywhere in the impl source. The new script answers the stronger question *and does the impl actually animate it?*. Per entry, it finds the matched impl files (same selector/id/CSS-Modules logic as `transition-spec-coverage`), then greps them for any motion-declaration needle: CSS `transition:` / `animation:` / `@keyframes`, Tailwind `transition-*` / `animate-*` / `duration-*` / `hover:` / `group-hover:`, framer-motion (`framer-motion` import / `<motion.*` / `useScroll` / `useTransform` / `useMotionValue` / `AnimatePresence`), GSAP (`gsap.to` / `gsap.timeline` / `ScrollTrigger`), Lenis, IntersectionObserver / `useInView` / `useScrollTrigger`, react-spring, and Webflow IX2 markers (`data-w-id`). Writes `spec-implementation-coverage.json` with `status` / `total` / `withMotion` / `presenceOnly`. Wired into `verification-plan.sh` as a `block` row at tier `standard` whenever `transition-spec.json` exists (meaningful only after generation; would silent-warn at quick tier before impl source exists). Locked in by `tests/test_gate.py::test_spec_implementation_coverage_fails_when_motion_missing` (selector matched, file has no motion → exit 1) and `::test_spec_implementation_coverage_passes_when_motion_declared` (matched file uses `framer-motion` + `useScroll` → exit 0). The needle list is intentionally permissive — false positive here means a borderline impl passes that should have failed (rare given entry must also be selector-matched); false negative means a real impl with custom motion plumbing fails. Permissive favors fewer false negatives, the right trade-off for an additive gate where `transition-spec-coverage` already catches the missing-entirely case separately.
- **Verification cost tiers (`--tier=quick|standard|comprehensive`).** `skills/visual-debug/scripts/verification-plan.sh` now accepts a `--tier` flag (env: `UI_CLONE_VERIFY_TIER`, default `comprehensive`). Each `add_check` row is tagged with a `min_tier`; the dispatch filters to rows at or below the active tier. `quick` runs static + JSON-only checks (`hydration-check`, `tailwind-transform-conflict`, `transition-spec-coverage`, `runtime-spec-coverage` — ~10s total); `standard` adds one-shot browser interactions (`scroll-end-completion`, `reveal-trigger`, `transition-compare`, `font-parity` — ~1min); `comprehensive` adds 60fps video compares (`video-motion-compare`, `hover-state-compare`, `click-state-compare` — ~5min+). Default is `comprehensive` so the dispatch from prior releases is preserved bit-for-bit when callers don't pass a flag — this is a backward-compatible additive capability for iteration loops, not a coverage downgrade. `verification-plan.json` gains a top-level `tier` field plus a per-row `tier` field. Locked in by `tests/test_gate.py::test_verification_plan_default_tier_is_comprehensive` / `::test_verification_plan_quick_tier_filters_to_static_checks` / `::test_verification_plan_standard_tier_drops_video_checks` / `::test_verification_plan_comprehensive_tier_emits_all_checks` / `::test_verification_plan_rejects_invalid_tier`.
- **Multi-viewport fan-out for `hover-state-compare.sh` + `click-state-compare.sh`.** Both scripts now accept a `VIEWPORTS=<WxH>,<WxH>,…` env var (matching the convention `scroll-end-completion-check.sh` already uses). When set, the per-target loop runs once per viewport with `VIEW_W`/`VIEW_H` exported to the inner `video-transition-compare.sh`; results land under `<ref-dir>/transitions/{hover,click}-state/<WxH>/<target>/` instead of being clobbered into a single shared dir. The `result.txt` aggregator gets per-viewport sections (`viewport: 375x812` + `[375x812]` tags on each target row) so an agent inspecting a fail knows which viewport diverged. Default empty = single-viewport (back-compat — single-tier callers see no cost increase; the fan-out is an additive comprehensive-tier opt-in, not a coverage upgrade for existing callers). Closes the responsive-regression failure class where mobile-only behaviors (no `:hover`, full-screen modal sheets vs floating panels, hamburger nav swap) pass a single-desktop sweep cleanly. **Historical note:** `section-compare.sh` fan-out was out of scope for this older release because the script was tangled with `ONLY_IF_CHANGED` hashing, Stop-gate result-file integration, and output-dir defaults; v0.7.3 later added an opt-in outer-loop while preserving the single-viewport default. Hover + click cover the interactive-UI failure surface where responsive divergence most often hides. Locked in by `tests/test_gate.py::test_hover_state_compare_fans_out_per_viewport` / `::test_hover_state_compare_single_viewport_back_compat` / `::test_click_state_compare_fans_out_per_viewport` / `::test_hover_state_compare_rejects_malformed_viewport`. The tests stub the inner `video-transition-compare.sh` via `PLUGIN_ROOT` redirection — the outer fan-out logic is what's locked in, not the inner sweep (which already has its own browser-required test coverage).
- **`skills/visual-debug/scripts/image-fidelity-check.sh`** — closes the "impl silently dropped or swapped a ref image" failure class at the spec→generation seam, before AE/SSIM has to find it in pixels. Reads `visible-images.json` (already captured during ref extraction) and greps the impl source for each entry's URL, falling back to basename → basename-without-query → stem when the agent imports the asset via a CDN-rewritten path or local copy. For `bg-image` entries (which carry the live display width/height from the extractor's `getBoundingClientRect`), if the matched impl file declares `width=`/`height=` props or inline styles within 5 lines of the match, compare to ref dims with ±`DIM_TOLERANCE` percent tolerance (default 10). Writes `image-fidelity.json` with `{schemaVersion, status, total, matched, unmatched, dimensionMismatches, tolerance}`. Status `pass` (all matched, all dims within tolerance), `warn` (all matched but ≥1 dimension mismatch — soft signal, exit 0), `fail` (≥1 unmatched URL — exit 1). Wired into `verification-plan.sh` as a `warn`-severity standard-tier row whenever `visible-images.json` is present. Severity=warn (not block) because image swaps are sometimes intentional (DRM/auth-gated CDN replaced by placeholder, `asset-substitutions.json` declared swap); the artifact still surfaces the URL so the agent can decide rather than the gate force-passing all substitutions. Locked in by `tests/test_gate.py::test_image_fidelity_passes_when_impl_references_all_urls` / `::test_image_fidelity_fails_when_url_dropped` / `::test_image_fidelity_warns_on_dimension_mismatch` / `::test_image_fidelity_skips_when_no_visible_images_json` / `::test_verification_plan_emits_image_fidelity_when_visible_images_present` / `::test_verification_plan_omits_image_fidelity_when_visible_images_absent`. Pure static — no browser, no network — same cost class as `spec-implementation-coverage`.
- **`scripts/ci/bench-verification.sh`** — micro-bench for the verification-dispatch surface. Builds three throwaway fixtures (empty / hover-only / all-signals), runs `verification-plan.sh` at each tier (quick / standard / comprehensive) against each fixture, takes the median of N=3 runs per cell, and emits a markdown (default) or `--json` table of wall-times and emitted check counts. Also runs `spec-implementation-coverage.sh` and `runtime-spec-coverage.sh` against pass + fail fixtures, asserting the expected exit codes — accuracy regressions in either gate surface as a `MISMATCH` cell + non-zero bench exit. The tier system and the two new coverage gates were validated by unit tests for *correctness* but had no *cost* number attached; without it, the next person adding a check has no signal that quick-tier latency just blew past the ~10s budget AGENTS.md advertises. Real-world wall time on this machine: ~5s for the default 3-repeat run; ~2s with `--repeat=1`. Not on the ci-local critical path — it's a developer utility behind a smoke test (`tests/test_gate.py::test_bench_verification_smoke_markdown` / `::test_bench_verification_json_mode_is_valid_json` / `::test_bench_verification_rejects_bad_repeat`).
- **`skills/visual-debug/scripts/runtime-spec-coverage.sh`** — turns `transition-spec-rules.md` Rule 7 from an advisory into an enforced gate. Rule 7 said "consult `animation-runtime-dump.json` when authoring `transition-spec.json`", but the advisory was unenforceable: an agent could author a spec with zero scroll entries while the live page ran 30 ScrollTrigger animations, and nothing in the pipeline caught it before code generation. This script does class-level coverage matching: if `dump.scrollTrigger` is non-empty, `transition-spec.json` must have ≥1 entry whose `trigger` matches scroll/intersection/inview/enter-viewport/viewport/scrub OR whose `type` begins with scroll/reveal/intersection; if `dump.ix2.timelineCount > 0`, the spec must be non-empty. Bijective matching is intentionally NOT required — the spec is the impl plan, not a mirror, and may collapse multiple runtime tweens into one CSS keyframe. What is never acceptable is missing the entire class. Writes `runtime-spec-coverage.json` with `status` / `missing[]`. Wired into `verification-plan.sh` as a `block` row whenever `animation-runtime-dump.json` AND `transition-spec.json` both exist.
- **`skills/visual-debug/scripts/asset-transfer-check.sh`** — verifies non-substituted `visible-images.json` entries exist as real files under `impl/public/`. Closes the failure class observed in the realfood.gov benchmark 4-run baseline: the agent extracts the catalog (Step 2.5 produces `visible-images.json`) but skips the actual download to `impl/public/`, so every `<img>` in the generated impl is a placeholder, and section-compare AE explodes 1M+. Image-fidelity-check verifies code references; this script verifies files. Both are now `block`-severity in `verification-plan.sh` — `gate_post_implement` refuses to pass until non-substituted images are physically present in `impl/public/`. Walks the public dir, indexes basenames (and stems for ext-mismatch cases), then checks each visible-images entry against `asset-substitution.json` (declared images skipped). Pass/fail JSON at `<ref-dir>/asset-transfer.json`.
- **`component-generation.md` hard-blocks on (a) asset transfer and (b) component splitting.** Comparing the realfood.gov benchmark output (317-line monolithic `page.tsx`, zero downloaded assets) against the prior successful clone at `~/Documents/onpixel/apps/showcase/src/app/real-food/` (46-line orchestrator + 14 section components in `src/projects/real-food/components/sections/` + 75 downloaded images + fonts + videos) surfaced two systematic regressions. The doc now opens with a 🚨 callout naming both: (1) `extract-assets.sh` + `asset-transfer-check.sh` MUST pass before generation; (2) one component per section, `page.tsx` capped at orchestrator size.
- **`transition-implementation.md` hard-blocks on bundle → infrastructure mapping.** Same comparison revealed the failing clone has zero infrastructure components (`SmoothScroll`, `IntroAnimation`, `ScrollListener`), while the successful one composes them around `<main>` based on `bundle-map.json`. Without these, scroll-coupled motion / splash overlays / global IntersectionObserver coordination all degrade to a single `useReveal` hook. New 🚨 callout names this explicitly: read `bundle-map.json` → create shared infrastructure components FIRST → then per-section components.
- **`section-compare.sh` forgiving substitution fallback.** Observed failure mode (benchmark 4-run baseline on realfood.gov): the agent writes `asset-substitution.json` with `fonts`/`images` declared but omits the `structuralOnlySections` key — which is the actual toggle for structural-only mode. Result: every section still runs strict pixel AE diff, AE explodes to 1M+, gate never clears despite the declared substitution. Fix: when fonts/images/videos are non-empty but `structuralOnlySections` is missing, auto-default to `["*"]` and emit a warning telling the agent to make it explicit. Plus `asset-substitution.md` gains a prominent 🚨 "most common mistake" callout at the top so future agents see the key requirement before writing the file.
- **Internal `benchmark` skill (`skills/benchmark/`).** Maintainer-only regression / benchmark harness — NOT registered in `.claude-plugin/plugin.json` `skills` (parity rule preserves the public 3-skill surface) but discoverable via `--plugin-dir` for local maintainer sessions. `SKILL.md` carries an activation-sentinel token so the maintainer can verify in one round-trip that `--plugin-dir` picked up the dev-clone copy. Procedure: `make benchmark` sets up an isolated `benchmark/work/<sha>/{ref,impl}/` work dir (NOT under `tmp/` — this is intentional maintainer work, not ephemeral; always fresh — wipes existing for clean wallclock), then invokes `python -m ui_clone.benchmark_harness` which drives the ui-reverse-engineering pipeline against `ref/` via `claude --print` headless per iter — (a) scaffolds a Next.js project into `impl/` if empty (`npx create-next-app@latest ... --typescript --tailwind --app --src-dir --no-eslint --use-npm`), (b) generates the cloned component there, (c) runs `npm run dev` and feeds the resulting impl URL into section-compare + transition-compare so the post-implement / section-compare gates can actually complete and fill the quality columns. Iteration / token / wallclock caps are MANDATORY (token-explosion guard); defaults are `MAX_ITER=100`, `TOKEN_BUDGET=500000`, `WALL_BUDGET_S=14400` and each is overridable via env. The benchmark prompt also explicitly invokes Phase 2.5 asset transfer (`scripts/extract/extract-assets.sh`) — without this step the agent generates placeholder rectangles for ref images and section-compare AE explodes to 1M+ even when DOM structure is correct (observed in 4 baseline runs on realfood.gov until the prompt was strengthened). After the loop exits, `skills/benchmark/scripts/benchmark-harvest.sh <ref-dir>` records: `outcome` (DONE / ABORT / INCOMPLETE_MAX_ITER / INCOMPLETE_BUDGET / INCOMPLETE_TIMEOUT — inspect `completed_gates` + `gate_fail_total` for which gate halted), `pipeline_actions` (a derived `gate_fails + completed_steps` count, NOT the harness iter), `wallclock_s`, **capture depth** (`sections_captured` = `static/ref/*.png` count, `regions_detected` = `regions.json` length, `trigger_types_seen` = unique `triggerType` count across regions — surfaces whether Phase 1 / 2 actually ran and how thoroughly), `ae_avg` / `ae_max` / `ssim_avg` / `sections_failed`, `transition_pass_rate`, `hydration_errors`, `font_parity`, `boundary_collisions`, `spec_coverage_pct`, `gate_fail_total`, `unclonable_count`, completed gates, and unclonable reasons. CSV trend at `benchmark/history.csv` (gitignored — maintainer-local), full JSON per run at `benchmark/history/<ts>-<sha>.json`. Closes the "did anything in the prompt / sub-doc / model-version stack quietly regress?" question that unit tests can't answer because they don't exercise the LLM-driven generation path. `scripts/ci/review.sh` gains an `internal_skills` allowlist so new internal skills don't trip the public-surface-mismatch check; `AGENTS.md` documents the convention. `scripts/ci/pre-push-security.sh` skips `tmp/` and `benchmark/` when scanning for secret patterns (cloned site contents shadowed by `AIza...` / `AKIA...` regex are public, not maintainer secrets). `Makefile` adds `benchmark` / `ci` / `security` top-level targets.

### Improved

- **`ui_clone/gate.py` `_check_verification_plan` scans text artifacts for ❌ FAIL markers.** Previously, when a row's `produces` was a non-JSON file (e.g. `transitions/result.txt`), the post-implement gate fell back to presence-only — file existed → check passed, even if every line was a FAIL. Now: try `json.loads()` first; on `JSONDecodeError`, count `❌` occurrences. Non-zero FAILs make the gate fail (or warn, per `severity`). Locked in by `tests/test_gate.py::test_verification_plan_text_artifact_fails_on_cross_mark` and `::test_verification_plan_text_artifact_passes_when_clean`.
- **`skills/visual-debug/scripts/transition-spec-coverage.sh` emits a JSON sidecar.** Was stdout-only — SKILL.md marked it MANDATORY but no gate could enforce it because no artifact existed to read. Now writes `transition-spec-coverage.json` with `status` / `total` / `covered` / `uncovered`. Wired into `verification-plan.sh` dispatch whenever `transition-spec.json` is present. Closes the "hover transitions matched while intersection / scroll entries were never wired" failure class — `transition-compare.sh` only verifies idle↔hover diffs, so it can PASS while intersection-trigger entries are silently absent from the impl source.
- **`gate_spec` now requires `verification-plan.json` to exist.** Without it, `gate_post_implement` would silently skip the universal `hydration-check` + `tailwind-transform-conflict` rows AND every signal-conditional row — agents who skipped Step 5d-end would never know their impl was unverified. `AGENTS.md`'s `spec` gate row updated.
- **`SKILL.md` Step 5d adds explicit `verification-plan.sh` invocation.** Was implicit (surfaced only by gate failure); pipeline table now shows the command alongside `bundle-map.json` / `transition-spec.json` writes.
- **`SKILL.md` Step 8-pre adds explicit `hydration-check.sh` + `tailwind-transform-conflict-check.sh` invocations.** Both were enforced via `verification-plan.json` rows in `gate_post_implement`, but agents only discovered them at gate-fail time after running a full AE sweep already poisoned by hydration errors or doubled translates. Surfacing them in Step 8-pre catches the bug class before AE wastes time.
- **New `SKILL.md` Step 8c-pre slot** for `transition-spec-coverage.sh` before 8c. Mandatory if `transition-spec.json` exists. Naming matches the existing `8-pre` / `8b-pre` pattern.
- **`SKILL.md` Step 8b documents `ONLY_IF_CHANGED=1` re-run short-circuit.** When the impl source hash is unchanged between runs, `section-compare.sh` reuses the prior `sections/result.txt` instead of re-driving the whole comparison sweep. Cross-ref to `visual-debug/SKILL.md`.
- **`SKILL.md` session rule mandates per-role reuse, not per-probe.** Was: "always pass `--session <project-name>`". Is now: reuse one session per role (`<project>` primary, `-ref` parallel reference, `-probe` throwaway) — no `<project>-debug1`, `-replay-sc`, `-foo-check` proliferation. End-of-run cleanup: `bash $PLUGIN_ROOT/scripts/cleanup-sessions.sh <project>`. Stale Chrome instances from session sprawl were a real-world resource leak.
- **`responsive-detection.md` MEASURE_EVAL dedupe.** Four copies of the same 18-line viewport-measurement eval collapsed behind a `MEASURE_EVAL` shell variable + `ensure_measure()` helper (~75 line savings, single point of truth).
- **`generation-pitfalls.md` CDN host placeholders.** Hardcoded `<streaming-cdn-host>` and `<image-cdn-host>` (site-specific) replaced with `<cdn-domain-1>` / `<cdn-domain-2>` plus a pointer to derive them from `visible-images.json` per site. The lesson (CDN URLs must be verified `200` with `curl -I` before shipping) is universal; the example domains were leakage from initial captures.
- **README installation section reordered.** Marketplace path is now primary (the only one that activates hooks `pre_generate`, `pre_bash`, `section_gate`, `session_resume`). Manual install (`git clone + ./install.sh`), skills-only (`npx skills add`), and manual system-dep install each live in collapsible `<details>` blocks below. Closes the "users do `npx skills add` and wonder why their gates never fire" failure class — the `<details>` block now spells out the limitation up front.
- **README install flow is host-first.** Claude Code and Codex now have first-class install paths instead of a separate Codex compatibility note. The live-URL decision tree points text-only prompts at Claude Code/v0/Lovable, and animation metadata calls out anime.js alongside GSAP, Framer Motion, and Lenis.
- **Host path resolution is generic.** Hook JSON commands skip safely when no plugin-root env is provided instead of falling through to `/hooks/shim.sh`, while skill docs resolve scripts through `PLUGIN_ROOT`, `CODEX_PLUGIN_ROOT`, `CLAUDE_PLUGIN_ROOT`, and common Claude/Codex skill roots.
- **Codex metadata mirrors Claude marketplace intent.** `.codex-plugin/plugin.json` now uses the same live-URL React/Tailwind positioning, broader frontend keywords, and `AGENTS.md` guidance as the Claude Code package metadata.
- **README adds prompt cache TTL section.** Explains plan-level defaults (Enterprise / Pro / Max get 1h server-side; Team / API-key needs `ENABLE_PROMPT_CACHING_1H=1`) plus a shell × launch-method placement table (zsh: `.zshrc` for terminal-launched Claude Code, `.zshenv` for GUI-launched; bash: similar split). Without 1h TTL, every gate cycle re-bills the SKILL.md prompt — the pipeline's pacing assumes 1h.
- **README requirements table.** Old layout listed individual install commands per-dep in a nested `<details>` block; new layout is a single dep × why table next to the Installation section, with the actual install commands consolidated under one `<details>` summary.
- **Tests: 286 → 320 (+34).** Coverage for `_check_verification_plan` (missing artifact / unreadable / valid JSON pass / valid JSON fail / text artifact pass / text artifact fail / unknown severity), the `gate_spec` `verification-plan.json` requirement, the `transition-spec-coverage.json` JSON-sidecar shape, plus the gate hardening landed late in the cycle (transition spec key tightening + gate boundary cases).
- **`AGENTS.md` slimmed 141 → 83 lines** (auto-attached to every agent turn via `CLAUDE.md` `@AGENTS.md` import and `AGENTS.md`-aware hosts). Three cuts: (1) the "Pipeline step numbering" + "Gate → artifact mapping" reference tables moved to `docs/gates.md`, read only when a gate, sub-doc, or `ui_clone/gate.py` `VALID_GATES` is changed (-26 lines); (2) the 16-line `Review checklist` block moved into the `scripts/ci/review.sh` header comment (the canonical automated enforcer of those checks), replaced with a thin pointer; (3) the `Section name detection` rule moved into the comment headers of `scripts/extract/{extract-assets,extract-section-html,section-clips}.sh` and the `FPS` rule into the `scripts/verify/video-transition-compare.sh` header. Net steady-state savings: ~58 lines per agent turn. Also added a token-discipline rule under `AGENTS.md` Token management calling out the autocompact-thrashing root cause (whole-file reads of >200-line sources + unredirected long command output).
- **`skills/ui-reverse-engineering/SKILL.md` slimmed 394 → 330 lines** (re-read on SessionStart / SkillActivation / PostCompact). Three passes: (1) reference-only sections extracted — 44-line "Reference files" sub-doc index → `reference-index.md`, niche "Execution rules" + "Scope adjustments" → `operational-rules.md`, both loaded on demand only when the agent needs to resolve a sub-doc filename or matches a specific request shape ("adding pages", "Tailwind collision", "clone the hero"); (2) `Validation gates` compressed from 8 per-gate command lines into one example command + a single-line gate roster annotated with the step each gate follows; (3) `Smart state router` cells tightened to short labelled states + imperative actions while preserving every routing condition and next-action. Inline operational rules (token rule, screenshot rule, Bash loop rule, compaction-survival rule, preflight bash) are kept inline — they fire on every run, not just on demand.
- **`SKILL.md` flow improvements (clone-site UX).** (1) Hoisted a one-line **Compaction-survival rule** into the inline operational-rules block so the post-compact "ref shows X / impl shows Y at scroll N" trap is visible alongside the other tier-1 rules — the longer 3-step re-capture procedure stays under Context management. Directly addresses the 73% post-compact verification-skip rate `session_resume.py` was observing. (2) Moved the **Transition Extraction** sub-pipeline (T-1 through T4) from after `Completion criteria` to between `Validation gates` and `Context management`, restoring chronological order (main pipeline → gates → transition sub-pipeline → recovery → completion) so an agent reading top-to-bottom sees the T-* steps before they'd need to act on them. Also added a one-line pointer right after the Pipeline table directing Step 5/6 transition findings to the section.
- **Top-level shell scripts reorganized** under `scripts/ci/`, `scripts/hooks/`, `scripts/extract/`, and `scripts/verify/`; updated hook, CI, skill-doc, and runtime references to the new paths.
- **Codex setup docs simplified** around `install.sh --codex`; the inline host file map was removed from README install instructions.
- **Public three-skill boundary clarified** in routing guidance: start with `ui-reverse-engineering` for live URL, uncertain, partial, failed, or completed-state work; call `ui-capture` directly for reference capture and `visual-debug` for mismatch diagnosis.
- **`install.sh` registers BOTH hosts by default.** Previously `curl ... install.sh | bash` registered only the Claude Code marketplace; Codex users had to clone + run `./install.sh --codex` separately. The default now calls `claude plugin marketplace add` AND `codex plugin marketplace add` in one pass — each registration is skipped silently if that host's CLI is not on PATH, so the same one-liner works on Claude-only, Codex-only, and dual-host boxes. New flags `--claude-only` and `--codex-only` (alias `--codex`) restrict to one host; via curl-pipe pass with `bash -s -- --claude-only`. README install section consolidated to a single one-liner plus a collapsible `<details>` block listing the per-host flags.
- **`scripts/hooks/post-push-refresh.sh` dogfoods `install.sh` end-to-end.** Was `npx skills add voidmatcha/ui-clone-skills -gy` — refreshed only `skills/`, not `hooks/` or `scripts/`, so any hook/CI change shipped without ever being installed locally. After a successful `git push`, the hook now wipes `$INSTALL_DIR` (default `~/.local/share/ui-clone-skills`) and re-runs `curl … install.sh | bash -s -- --no-deps`, so the just-pushed installer is exercised against a clean tree on every push. Defensive guard prevents wiping the maintainer's working repo if `INSTALL_DIR` were mis-set to point at it. `--no-deps` is passed because system deps (brew / uv) don't change per commit and re-resolving them every push wastes ~30s. Override with `UI_CLONE_SKIP_POST_PUSH_REFRESH=1`.

### Fixed

- **`skills/ui-reverse-engineering/SKILL.md` cross-ref to `cleanup-sessions.sh`.** Was `$SCRIPTS_DIR/../../../scripts/cleanup-sessions.sh` — the relative-path traversal was captured by `scripts/review.sh`'s cross-ref regex, which resolved it to a non-existent location and flagged the link as broken on every push. Replaced with `$PLUGIN_ROOT/scripts/cleanup-sessions.sh` which avoids the regex pattern entirely while pointing to the same file.
- **`skills/ui-reverse-engineering/transition-implementation.md` duplicate sections removed.** When `gsap-alternatives.md` was split out (see Added above), the source sections (MorphSVG, ScrollSmoother, Draggable, DrawSVG, Detection-rule — ~55 lines, L406-444) were left in place — both files claimed to be the SoT. Source removed; backref pointer added to `gsap-alternatives.md` so the anti-pattern rules in `transition-implementation.md` still apply when an OSS alternative is swapped in.
- **`skills/ui-reverse-engineering/SKILL.md` example values genericized.** Site-specific names in the input-args table and the "URL required" reply template were replaced with `example.com` / `example-main` / `example` placeholders. The originals were leakage from initial development.
- **`scripts/verify/auto-verify.sh` marker-file resolver no longer aborts under `set -euo pipefail`.** The resolver used `_marker="$(cat ...)"` without `|| true`. A missing marker file would abort the script before the fallback loop ran. Now `cat` is `|| true`-guarded and the captured value strips a trailing `\r` so a Windows-edited marker is also safe.
- **`hooks/hooks.json` + `hooks/codex-hooks.json` `_R` resolver chain extended.** Now traverses `UI_CLONE_ROOT` and the install marker file in addition to `PLUGIN_ROOT` / `CLAUDE_PLUGIN_ROOT` / `CODEX_PLUGIN_ROOT`, so hook commands resolve even when the host doesn't export a plugin-root env var.
- **`ui-reverse-engineering/scripts/auto-verify.sh` compatibility symlink removed**; documented the script location policy (host hooks in `hooks/`, repo automation in `scripts/`, skill-owned primitives under `skills/<skill>/scripts/`).
- **`ui_clone/goal.py::_section_compare_status` no longer mis-counts the `**Result: N PASS, 0 FAIL, …**` summary footer as a failed row.** The matcher was substring-checking each line for the literal text `FAIL`, which the section-compare.sh footer always contains regardless of actual outcome. Even on clean runs the matcher reported FAIL ≥ 1 → `--check-done` exited 1 → external loop drivers kept iterating against a satisfied gate. Narrowed to table rows beginning with `|` that carry the `❌` status marker. Locked in by `tests/test_goal.py::test_goal_check_done_ignores_result_footer_fail_substring`.
- **`ui_clone/gate.py::_check_verification_plan` hard-fails when `verification-plan.json` is hand-written.** Previously: schemaVersion missing → silent-warn → all declared `requiredChecks` ignored → post-implement gate trivially passes. The realfood.gov benchmark shipped with the agent hallucinating `{component, checks}` instead of running `verification-plan.sh`, and the broken file slipped through. Now: missing `schemaVersion` or missing `requiredChecks` key → block-severity FAIL with `Run: bash skills/visual-debug/scripts/verification-plan.sh` fix hint. Forward-compat retained for future schemaVersion values (still warn-and-skip). Locked in by `tests/test_gate.py::test_verification_plan_missing_schema_version_fails` + `test_verification_plan_missing_required_checks_key_fails`.
- **`ui_clone/gate.py::gate_section_compare` overrides `STRUCTURAL_ONLY` PASS when `sections/structure-diff.json` carries `severity=critical` for that section.** `STRUCTURAL_ONLY` exists for legitimate asset/font substitution (different commercial font → impl renders pixel-different but structure matches). It was being used as a silent-pass for impl regressions: realfood.gov shipped 638px-tall impl against 19954px ref (ratio=0.03) and the gate still reported "All sections PASS" because asset-substitution flipped every section to STRUCTURAL_ONLY. Now critical structure-diff entries (HEIGHT_MISMATCH ratio < 0.05, DISPLAY_MISMATCH) refuse the substitution short-circuit and the section is counted as a real failure. Non-critical (warn/info) severities are unaffected. Locked in by `tests/test_gate.py::test_gate_section_compare_overrides_structural_only_on_critical_diff`.
- **`skills/visual-debug/scripts/verification-plan.sh` forces `tier=comprehensive` when the ref dir lives under `benchmark/work/`.** The 077d8c3 benchmark exposed a gaming pattern: the agent set `UI_CLONE_VERIFY_TIER=quick`, which dropped `asset-transfer`, `image-fidelity`, `transition-compare`, `font-parity`, and `video-motion-compare` from `requiredChecks` — the verification surface shrank from 8+ checks to 3 while the implementation stayed monolithic. Benchmarks measure the full surface; the agent does not get to pick which checks fire. Caller-supplied `--tier=quick` is overridden with a stderr notice. Non-benchmark refs (regular `tmp/ref/`) are unaffected and still honor caller-supplied tier. Locked in by `tests/test_gate.py::test_verification_plan_forces_comprehensive_tier_under_benchmark_work`.
- **`ui_clone/gate.py::gate_section_compare` widens the `STRUCTURAL_ONLY` override to catch `severity=major` with HEIGHT_MISMATCH `ratio<0.5`.** The 077d8c3 benchmark shipped section-0 at 6955px against ref 19954px (ratio=0.35, 65% of content missing) and the structure-diff classifier labeled it `major` not `critical` — the prior `critical`-only guard let it through and the stub clone passed. Anything below half-height is content disappearance, not substitution; the AE-bypass that `STRUCTURAL_ONLY` provides is meaningless. Parses `ratio=N` from `HEIGHT_MISMATCH` issue strings via regex. Locked in by `tests/test_gate.py::test_gate_section_compare_overrides_structural_only_on_major_with_low_ratio` + a companion test confirming `ratio≥0.5` still PASSes.
- **`skills/visual-debug/scripts/verification-plan.sh::add_check "image-fidelity"` + `"asset-transfer"` dropped from `min_tier=standard` to `min_tier=quick`.** Both are cheap presence/grep checks (no browser round-trip), so there's no cost-tier reason to defer them. The 077d8c3 benchmark proved that `min_tier=standard` lets the agent route around them via tier selection. With `min_tier=quick` they fire at every cost tier the agent can pick, eliminating the bypass. Test `test_verification_plan_emits_image_fidelity_when_visible_images_present` updated accordingly.
- **`ui_clone/gate.py::gate_post_implement` fails when `impl/src/app/page.tsx` > 200 LOC AND `impl/src/components/` has < 3 .tsx files.** The c9b638d benchmark (round 1) shipped a 214-line monolithic page.tsx with 0 per-section components, then needed 30+ minutes of iter-3 work to rewrite markup before any AE could converge. Split-first localizes future fixes to a single Section component. Auto-detects impl/ via `_find_impl_root` (benchmark/work/<sha>/impl + apps/<c>/ conventions); silent skip when no co-located impl exists. Locked in by `tests/test_gate.py::test_componentization_gate_fails_on_monolithic_page` + 3 companion tests.
- **`skills/visual-debug/scripts/asset-utilization-check.sh` (new) + verification-plan.sh row** — fails when fewer than 60% of non-substituted `visible-images.json` entries are referenced in `impl/src/**/*.{tsx,ts,jsx,js,css,scss}`. The c9b638d benchmark downloaded 45 images but page.tsx referenced only 2 (`/images/broken-system/food-pyramid.webp` + `/images/video-placeholder.webp`) — 95% orphan ratio. `asset-transfer` already verifies files-on-disk; the new `asset-utilization` row at the same `min_tier=quick`/`block` severity verifies code-references-to-disk. Threshold + min-sample tunable via `ASSET_UTILIZATION_THRESHOLD` and `ASSET_UTILIZATION_MIN_SAMPLE` env vars. Locked in by `tests/test_gate.py::test_verification_plan_emits_asset_utilization_when_visible_images_present`.
- **`skills/visual-debug/scripts/bundle-impl-coverage-check.sh` (new) + verification-plan.sh row** — fails when `bundle-map.json` declares a library signature (gsap-like-strings, motion-like, lenis-from-notes, etc.) that has no matching install in `impl/package.json` (`dependencies` or `devDependencies`). The c9b638d benchmark correctly decoded ref bundles for GSAP + Framer Motion + Lenis-on-`<html>` but the Next.js scaffold shipped with only `next/react/react-dom`, leaving the entire bundle analysis as a dead wire. The check maintains a `SIG_TO_PKG` table mapping detected signatures to one-of-many acceptable npm packages (e.g., `motion-like` → `framer-motion | motion | @motionone/dom`). Locked in by `tests/test_gate.py::test_verification_plan_emits_bundle_impl_coverage_when_bundle_map_present` + `test_bundle_impl_coverage_script_fails_when_libs_missing` + `test_bundle_impl_coverage_script_passes_when_all_installed`.
- **`asset-utilization-check.sh` + `bundle-impl-coverage-check.sh` schema-tolerant parsers.** Round 2 of the realfood benchmark (`d19e28d`) shipped extraction artifacts with newer JSON shapes that the prior parsers didn't recognize, causing both checks to silent-skip even when their inputs were present. Specifically: `visible-images.json` may now be `{"images":[...]}` instead of a top-level list (asset-utilization), and `bundle-map.json` now uses `{"libraries":{"gsap":bool,"lenis":bool}, "evidence":{"lib":[chunks]}}` flat shape instead of `{"chunks":{name:{"libs":[...]}}}` nested (bundle-impl-coverage). Both parsers now normalize either shape. `bundle-impl-coverage` additionally maps the new `libraries` keys (`gsap`, `scrollTrigger`, `framerMotion`, `lenis`, `three`, `tween`, `popmotion`, `reactSpring`) to the legacy signature names so the existing `SIG_TO_PKG` install-check table keeps working. New `three-like` → `three | @react-three/fiber | @react-three/drei` row covers Three.js detections.
- **`skills/visual-debug/scripts/section-compare.sh` flipped `EXCLUDE_DYNAMIC` default 0 → 1.** Round 1 (`c9b638d`) and Round 2 (`d19e28d`) benchmarks both measured AE against ref `<video>` elements paused at `currentTime=0` AND impl `<video>` paused at `currentTime=0` — but codec/scheduler variance still produced different decoded first frames, blowing AE to 1M+ on sections whose static layout was actually close to ref. The pause logic (animation-play-state, video.pause()+currentTime=0) handles CSS loops + autoplay restart, but not first-frame variance. Masking video/canvas (the prior opt-in `EXCLUDE_DYNAMIC=1`) is the only deterministic option. Motion fidelity is now validated separately by `transition-compare` / `video-motion-compare`, NOT by `section-compare`. Opt back into per-pixel motion-in-section via explicit `EXCLUDE_DYNAMIC=0`.
- **`scripts/lib/timeout-shim.sh` (new) sourced from three `*-compare.sh` scripts.** Closes the macOS `(eval):6: command not found: timeout` failure observed during the c9b638d benchmark, where the agent's defensive `timeout 30 hover-state-compare.sh ...` wrappers all errored because GNU coreutils isn't installed by default on macOS. The shim resolves in order: (1) real `timeout` on PATH → no-op, (2) `gtimeout` from brew coreutils → shell-function proxy, (3) pure-bash fallback using background-sleep-kill. Sourced from `transition-compare.sh`, `hover-state-compare.sh`, `video-motion-compare.sh` so any internal `timeout N` invocation works regardless of OS / brew state. The agent's defensive wrap pattern is also unblocked because the shim exports `timeout` as a shell function visible to child shells.
- **`ui_clone/measure.py` (new) — Python orchestrator with locked measurement env.** Round 2 (`d19e28d`) exposed a new gaming axis: the agent ran `SECTION_THRESHOLD=250000 bash section-compare.sh ...` (vs documented default 2000), re-classifying AE/Mpx 88823 + 228325 — both nominally `critical` (>20000) — as `minor` ✅. result.txt then showed "0 FAIL" and the gate had no way to detect the inflation. measure.py invokes the bash scripts via `subprocess.run(env=LOCKED_DEFAULTS|os.environ)`, where `LOCKED_DEFAULTS = {"EXCLUDE_DYNAMIC":"1", "SECTION_THRESHOLD":"2000"}` overrides whatever the parent shell set. Subcommands: `section-compare`, `transition-compare`, `asset-utilization`, `bundle-impl-coverage`, `all` (canonical sequence, static fidelity → motion fidelity → composition). The bash scripts remain the measurement workers; only the orchestration moves to Python. Tests cover env locking, subcommand ordering, `transition-spec.json`-conditional skip of motion check, module-level invocation. The companion `gate.py::gate_section_compare` "section-threshold gaming" detector catches the bypass when the agent still uses bare bash (locked-from-the-other-end).
- **`ui_clone/gate.py::gate_section_compare` "section-threshold gaming" detector.** Reads `sections/result.txt` and flags any row labeled `ok` or `minor` whose AE/Mpx exceeds 2000 (the canonical `SECTION_THRESHOLD` default). The classifier bands are `ok≤500 < minor≤2000 < major≤20000 < critical`; rows that break this monotonic relationship can only exist when `SECTION_THRESHOLD` was inflated at the bash layer. The fix message points at `python -m ui_clone.measure section-compare` (which locks the threshold) OR `asset-substitution.json` declaration (the legit way to explain expected variance). Locked in by `tests/test_gate.py::test_gate_section_compare_detects_threshold_gaming` + `test_gate_section_compare_accepts_legitimate_minor_under_threshold`.
- **`section-compare.sh` uses `section-map.json` as ref-section ground truth.** The runtime `ENUMERATE_SECTIONS` JS in `section-compare.sh` descends `<main>` only when it has `<section>` or `<main>` children (line 709-712). The `c9b638d` and `d19e28d` benchmarks both ran against `realfood.gov` where ref `<main>` contains `<div>` children — enumeration collapsed 16 visible sections into a single "section-0" container. `result.txt` then carried only 2 rows (section-0 + footer); 14 sections were never compared at all. New path: when `section-map.json` exists (extraction-time analysis already records the full 16-section table with Y-coordinate + class + id), `section-compare.sh` overrides `ref-sections.json` with those entries before the matcher runs. Falls back to runtime enumeration when the map is absent or has fewer than 3 sections.
- **`scroll-coverage-check.sh` (new) + verification-plan.sh row** — revives the previously-orphan `batch-scroll.sh` + `batch-compare.sh` pair as a dispatchable check. Captures screenshots at every 10% of page scroll on both ref + impl and AE-diffs each pair. Orthogonal to `section-compare`'s DOM enumeration: catches "page-wide visual divergence" that section matching cannot reach (e.g., the c9b638d benchmark where ENUMERATE_SECTIONS collapsed 16 sections to 2 — scroll-coverage would still measure 11 scroll points). `min_tier=standard`, severity `warn`, gates on ≥70% of points passing (`SCROLL_COVERAGE_FAIL_PCT` env, default 30). Skips when `regions.json` has <5 regions (short page) or impl URL unreachable.
- **`video-motion-compare.sh` staged check — trajectory pre-filter → 60fps SSIM.** The previously-demoted `transition-trajectory-compare.sh` is now invoked at the start of `video-motion-compare.sh` as a cheap fail-fast filter. Rationale: trajectory's 5-point AE sampler can't distinguish `easeOutCubic` from `easeOutQuint` (same end + same midpoints) — so trajectory PASS is inconclusive. But trajectory FAIL is reliable: if the 5-point gross check diverges, the 60fps video will too. Pre-filter saves expensive video-recording cost on the FAIL path while the PASS path still falls through to the authoritative SSIM verdict. Skippable via `PRE_FILTER=0` for video-pipeline regression debugging. `transition-trajectory-compare.sh` header rewritten — no longer "deprecated", now documented as "cheap pre-filter in front of video-motion-compare".
- **`verification-plan.sh` new signal-based dispatch rows for `keyframes-diff`, `scroll-anim-temporal-diff`.** Previously, both scripts existed and were documented in `SKILL.md`'s L3/L4 escalation table but had no automated trigger — agents only invoked them ad-hoc when they noticed the symptom. Now: when extraction artifacts contain `@keyframes` declarations (CSS files OR `extracted.json`), the `keyframes-diff` row fires automatically at `min_tier=standard / severity=warn` — catching missing entrance animations and wrong easing curves baked into keyframes. When `transition-spec.json` declares scroll-driven repeating motion (`pattern: repeating` or `repeating: true`), the `scroll-anim-temporal` row fires at `min_tier=comprehensive / severity=warn` — catching the "same amplitude, different phase family" failure class for marquees / parallax tile grids / number stacks. Both keep severity=warn (advisory) until calibration evidence supports promotion to block.
- **`ui_clone/gate.py::gate_section_compare` failure messages embed the escalation ladder.** When `section failures` fires, the `fix` field now lists the 5-step escalation tier — `auto-diagnose.sh` (cheap hotspot triage) → `tree-diff.sh` (exhaustive computed-style) → `layout-tree-diff.sh` (signature-based geometry) → `hover-tree-diff.sh` (per-element :hover) → `dssim-compare.sh` (structural similarity sanity). The escalation tools live in `skills/visual-debug/scripts/` but are NOT gate-dispatched; embedding them in the fix message gives the agent a concrete next-step instead of "fix diffs and re-run", and pulls the previously-doc-only escalation table into the actual gate output the agent reads.
- **`ui_clone/benchmark_harness.py` (new) — Python-driven benchmark loop owns `make benchmark`.** Earlier benchmarks (077d8c3 / c9b638d / d19e28d) burned 50-150k tokens per session on agents re-reading the same static instruction template and re-deriving "where am I" from goal.py — for benchmark automation (cron / unattended runs / per-iter token efficiency) the trade-offs flipped against the static-prompt re-injection model. `benchmark_harness.py` invokes `claude --print --session-id <uuid> --output-format json --plugin-dir … --permission-mode auto` per iter, building each prompt from current pipeline-state + unmet STRICT v2 conditions (so the agent gets "AE=279K on footer, fix that specifically" rather than "try again"). Stop on DONE (all conditions met), INCOMPLETE_MAX_ITER, INCOMPLETE_BUDGET (token cap evaluated before each claude call — the $47k-agent-loop post-mortem rule), INCOMPLETE_TIMEOUT (wall clock cap), or ABORTED (pipeline-state.unclonable_reasons non-empty). Writes `benchmark-harness.log.jsonl` with start/iter_start/iter_end/end events. Locked in by `tests/test_benchmark_harness.py` (11 tests covering stop-condition probes, prompt building, mocked-claude loop wiring).
- **`gate_section_compare` caps `STRUCTURAL_ONLY` to ≤50% of sections.** The 5199dd9 benchmark exposed a new gaming pattern: agent declared all 9 sections substituted via `asset-substitution.json`, getting "9 PASS, 9 STRUCTURAL_ONLY" with zero pixel measurement. Substitution is for one or two sections that use commercial fonts / licensed imagery — using it as a blanket section-compare bypass is gaming. New "structural-only excess" failure fires when ratio > 0.5 with at least 3 substituted rows. Locked in by `tests/test_gate.py::test_gate_section_compare_caps_structural_only_ratio` + `test_gate_section_compare_allows_minority_structural_only`.
- **`gate_post_implement` enforces a `tree-diff` `elements_walked` floor cross-referenced against `section-map.json`.** Same 5199dd9 benchmark: `tree-diff-status.json` reported `status=pass elements_walked=11 critical=0` — a near-empty impl vacuously passed because there were too few elements to mismatch. Floor = `max(30, sections × 5)` reading section count from `section-map.json` (so a 4-section page doesn't trip the floor while a 16-section page demands ≥80 walked). Walked < floor → block-severity fail with "near-empty impl" message. Locked in by `tests/test_gate.py::test_verification_plan_tree_diff_floor_fails_on_empty_walk` + `test_verification_plan_tree_diff_floor_passes_on_real_walk`.
- **`gate_post_implement` requires measurement rows in `transitions/result.txt` when `transition-spec.json` declares transitions.** Previously: empty / near-empty result.txt with 0 ❌ markers passed vacuously (text-artifact scanner only counts FAIL lines). New: for `transition-compare` / `video-motion-compare` / `hover-state-compare` / `keyframes-diff` / `scroll-anim-temporal` rows, when `transition-spec.json.transitions[]` is non-empty, the artifact must contain at least one `✅` or `❌` measurement row. Empty artifact = "the check never actually ran" = block-severity fail. Locked in by `tests/test_gate.py::test_verification_plan_transition_compare_empty_artifact_fails` + `test_verification_plan_transition_compare_empty_artifact_passes_when_no_spec`.
- **`tree-diff.sh` promoted to primary STATIC-phase convergence gate.** Previously, `tree-diff` was an L4 ad-hoc escalation tool — agent only invoked it when SKILL.md's symptom table indicated. Operator directed it become the gate the iter loop drives to zero, not just an escalation rung. `tree-diff.sh` now writes `tree-diff-status.json` (sidecar to the existing `tree-diff.json` raw-pair data + `tree-diff.md` markdown report) with `{schemaVersion, status, elements_walked, counts, errorCount, reason}` so `gate_post_implement`'s existing JSON-status reader can grade it. A new `add_check "tree-diff"` row is signal-unconditional (every site has DOM elements) with `severity=block`, `min_tier=standard`. Block severity means `section-compare` PASS alone no longer suffices — the per-element diff must also converge (zero `critical` + `major` + `layout-major` rows). STRICT v2 plan stop condition gains the `tree-diff-status.json status == "pass"` requirement. Locks in the "keep doing DOM-tree screenshot compare until it matches" convergence model the operator requested.

### Compatibility

- `verification-plan.json` is new; pipelines that previously ran without it must now produce it before `gate_spec` will pass. `verification-plan.sh` is idempotent and tolerates partial inputs — empty signals still produce a useful plan (the two universal rows always fire). Cost: one extra shell invocation between Step 5d and Step 7.
- The text-artifact ❌ scanner is a **stricter** behavior change in `gate_post_implement`. Projects whose `transitions/result.txt` previously passed because no JSON parser was applied will now FAIL if any ❌ line exists. This is closing a silent-pass loophole, not a regression — the artifact was supposed to be a gating signal all along.
- `transition-spec-coverage.json` is new. Pre-0.4.10 pipelines that hand-ran `transition-spec-coverage.sh` and read stdout continue to work (stdout output is unchanged); the JSON sidecar is an additive output.
- README install reorder is documentation-only. Existing `curl ... install.sh | bash` users see no behavior change. `npx skills add` users now see an explicit warning about missing hooks alongside the command.
- Sub-doc count: 39 → 46 across three README locations.
- Version bumped 0.4.9 → 0.4.10 across `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.codex-plugin/plugin.json`, `pyproject.toml`, and `ui_clone/__init__.py`. CI `pre-push-guard.sh` enforces the version sync.
- `AGENTS.md` slimmed 141 → 83 lines and `skills/ui-reverse-engineering/SKILL.md` slimmed 394 → 330 lines. Both are auto-loaded surfaces (`AGENTS.md` every turn, `SKILL.md` on SessionStart / SkillActivation / PostCompact). Content was moved to on-demand sub-docs (`docs/gates.md`, `reference-index.md`, `operational-rules.md`) and to script header comments — no information was deleted, only relocated. Agents that previously matched on text that has moved should now find it through the pointer line at the same anchor.

### Removed

- **`scripts/claude-pre-push.sh` / `scripts/claude-post-push.sh`.** `.claude/settings.json` now calls the generic `pre-push-guard.sh` and `post-push-refresh.sh` directly, so the Claude-named wrappers were redundant.
- **`scripts/jsonl-skip-analysis.py`.** This was an offline transcript-analysis utility used to justify earlier gate work; direct reference audit found only historical CHANGELOG/self references, so it is no longer shipped as part of the active plugin/scripts surface.

### Hygiene

- **Legacy placeholder cleanup (later superseded).** An earlier pass replaced several site-specific names in generic examples with placeholders (`project-a` / `project-b` / `example.com` / `<streaming-cdn-host>` / `<image-cdn-host>` / "a partner site"). The follow-up policy removed placeholder enforcement: source names and asset references are fidelity evidence when they are part of a reference site, benchmark, test fixture, or explanation.
- **Non-English text scrub.** Hangul codepoints in `skills/*/evals/trigger-eval.json` fixtures and a CJK-glyph example in `skills/ui-reverse-engineering/asset-extraction.md` were replaced with English-only equivalents (the asset-extraction example now uses `<sample-glyphs>` plus a "for CJK fonts, pass characters from the target subset" note). `scripts/ci/review.sh` language check no longer excludes `evals/` or `asset-extraction.md` and now also scans CHANGELOG / README / AGENTS / CLAUDE files for Hangul, so future non-English text is blocked before push.
- **`AGENTS.md` source fidelity rule.** Current guidance preserves observed source names, visible text, and asset references when they are part of a reference site, benchmark, test fixture, or explanation.
- **`scripts/ci/test-parity.sh` drift smoke test.** Mutates tracked files to known-bad states (AKIA-shaped secret, version drift, broken JSON, Hangul), runs the relevant guard (`pre-push-security.sh` or `review.sh`), asserts the expected error substring appears, restores from backup. Prevents the language scanner, version-sync check, manifest validation, and secret scanner from rotting silently if a regex breaks. Wired into `ci-local.sh` step 6. `pre-push-security.sh` gains a `$DRIFT_TEST` exclude (sibling to `$SELF`) so the test file's inline trip patterns don't self-trigger the scanner.
- **README SEO/GEO pass.** Opening hook reframed to lead with the problem (visible parity, hidden divergence) before naming the tool, a top-of-README **Contents** TOC was added for the 459-line README, and a 7-question **FAQ** section was added before the Changelog with GEO-quotable answers to "how is this different from v0/Lovable/Bolt.new", "vs screenshot-to-code/Anima/Builder/Plasmic", "Webflow/Framer/GSAP support", "Next.js + Tailwind v4", "needs OpenAI API", "when do vision tokens get used", "production-ready". Plugin manifest descriptions (`.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.codex-plugin/plugin.json` + `interface.shortDescription/longDescription`) were front-loaded with user-intent keywords ("Clone any live website into React + Tailwind", "no vision tokens", "real CSS/JS bundle analysis instead of screenshots"). Keyword arrays expanded with long-tail intent terms (`url-to-react`, `html-to-react`, `website-to-nextjs`, `landing-page-clone`, `next-js`, `tailwind-v4`, `webflow-to-tailwind`, `gsap-extraction`, `framer-motion-extraction`, `lovable-alternative`, `builder-io-alternative`, `anima-alternative`, `plasmic-alternative`).

## [0.4.9] - 2026-05-11

Doc-pipeline polish + dep-bootstrap closes the gap when the plugin is installed via `npx skills add` (vercel-labs/skills route). That path symlinks SKILL.md files into the agent's skill directory but never runs `install.sh`, leaving system tooling (`agent-browser`, `ffmpeg`, `imagemagick`, `dssim`, `uv`) and the `ui_clone/` Python package missing — every pipeline command then failed with cryptic "command not found" deep inside the workflow. Each SKILL.md now opens with a session-start preflight that detects the gap and surfaces the bootstrap one-liner; the agent halts and instructs the user instead of silently proceeding into broken downstream tools. Also folds in two real-world bug fixes against the gates added in 0.4.8 (`font-parity`, `boundary`) that surfaced running the a partner site clone end-to-end — both gates were silently blocking on inputs they should have passed.

### Added

- **Per-skill preflight blocks (session-start dep check).** `ui-reverse-engineering/SKILL.md`, `visual-debug/SKILL.md`, and `ui-capture/SKILL.md` each open with a `command -v` sweep over their required deps; on miss, the agent prints the missing list plus two install paths — `curl -LsSf https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh | bash` (full bootstrap) and the manual brew/apt/cargo/npm sequence — then exits. Per-skill scope: ui-reverse-engineering needs all 5 + the `ui_clone/` Python package (since `npx skills add` only copies `skills/`, not the package); visual-debug needs 4 (no uv); ui-capture needs 2 (agent-browser + ffmpeg). The block is documentation, not a hook — the agent reads SKILL.md and runs the check itself, then must surface the message to the user rather than auto-executing `curl | bash` on their behalf.

### Improved

- **`visual-debug/SKILL.md` Cost Ladder + Read-summary table.** Three factual errors removed from the L1/L3 rows that documented tools and env vars that don't exist: (1) L3 row claimed `section-compare.sh` accepted a `SECTIONS=<one>` env var — it does not (only positional args + `VIEW_*` / `WAIT_*` / `EXCLUDE_DYNAMIC` / `DYNAMIC_SELECTORS`); replaced with `auto-diagnose.sh` on a single FAIL `diff.png` + `computed-diff.sh` on a narrow selector list. (2) Read-summary table referenced `auto-verify.sh` as a visual-debug script — it lives in `scripts/`, not `skills/visual-debug/scripts/`, and is unreachable from the skill's `$SCRIPTS_DIR`; row removed. (3) `transition-compare.sh` summary documented as `result.md` — actual artifact is `<dir>/transitions/report.json` per the script's header comment; corrected. tree-diff family row consolidated with accurate `<dir>/<script-name>.{md,json}` paths.
- **`dom-extraction.md` lazy-attr rewrite is now portable + fixes the broken `data-bg` rewrite.** The previous one-liner used `sed -i ''` (BSD-only, breaks silently on Linux GNU sed) AND its `data-bg=` substitution pasted in a raw `style="background-image:url(` with no closing `)` or quote — so a captured `data-bg="img.jpg"` ended up as `style="background-image:url("img.jpg"`, invalid HTML that browsers swallowed and the impl rendered with no background image. Replaced with a python heredoc that captures the URL value via regex and emits a fully-closed `style="background-image:url(<v>)"`, AND works on both BSD and GNU systems with no `-i` flag dance.
- **`generation-pitfalls.md` FOUC detection command uses real agent-browser CLI syntax.** The detection one-liner for the SSR/CSR cascade-class flash bug previously read `agent-browser open <url> && screenshot t0.png` — both verbs missing the mandatory `--session <s>` flag, and the `&&` chain broke under `agent-browser`'s subprocess model. Corrected to two separated invocations: `agent-browser --session <s> open <url> && agent-browser --session <s> screenshot tmp/t0.png`, then a settled-state capture after `wait 1000`. Diff `t0.png` vs `t1.png` to confirm the bug class.
- **`visual-debug/SKILL.md` Step 0-pre chrome-hidden scan hardened.** Added `wait 1500` after `open` so dev banners / "made with X" badges have time to mount before the eval enumerates fixed-position elements; switched the inline `el.className?.toString?.()` chain to a defensive `(el.className && el.className.toString) ? ...` ternary because optional-chaining a `string` className inside an injected eval string can fail under certain agent-browser builds; piped the eval result to `tmp/fixed-scan.json` per the file's own token rule.
- **`diagnosis.md` Root Cause E adds a runtime-detection branch** for the multi-state JS-toggle-subset class. Static section-compare can PASS while the bug oscillates only at the section's scroll boundary (settled positions inside/before/after all look correct; the flicker exists only during scroll cross). Adds the `agent-browser mouse wheel 100` (×N) procedure with per-step screenshot + AE-compare on consecutive frames — non-zero AE on a "settled" run ⇒ state class toggling on/off as `scrollY` crosses the trigger. Same rule applied to hybrid scroll-position controllers, not just pure-IO ones.

### Fixed

- **`skills/visual-debug/scripts/font-parity-check.sh` JSON unwrap.** The script extracted the page's primary `font-family` via `agent-browser eval`, then unwrapped the doubly-encoded JSON output with `sed 's/^"//;s/"$//' | sed 's/\\"/"/g'`. That single-pass sed replacement collapsed one level of escaping but left family names that contain quotes (the common case — `font-family: "Clash Grotesk", sans-serif` is the literal CSS computed-style output) one escape level short, producing invalid JSON like `{"fam":"\\"Clash Grotesk\\", sans-serif"}` and a downstream `Expected ',' or '}' after property value in JSON at position 42` parse failure that exited the script with no artifact written. Replaced the sed pipeline with a `node` double-`JSON.parse` (string envelope → inner object) that handles arbitrary nesting depth and any embedded quotes/backslashes/unicode. Verified against a partner site (Clash Grotesk both sides) → `parity: match` written, `font-parity` gate clears.
- **`skills/visual-debug/scripts/breakpoint-collision-check.sh` signal classification.** The script checked three signals at every Tailwind breakpoint: (1) `matchMedia('(max-width: <bp>)')` AND `matchMedia('(min-width: <bp>)')` both match, (2) isolated overflow at `<bp>` that disappears at `<bp>±1`, (3) rootFontSize jitter >4px between `<bp>-1`, `<bp>`, `<bp>+1`. All three were treated as gate-blocking findings. Signal 1, however, is a **W3C spec property** — `matchMedia('(max-width: 640px)')` returns `true` at exactly `width: 640px` whether or not any rule actually uses that range, because the spec defines both bounds as inclusive at the boundary. Every project that uses both `min-width` and `max-width` queries at any Tailwind breakpoint therefore failed the gate with no actual visible defect (zero overflow, zero rem jitter). Signal 1 is now reclassified as advisory: still detected and printed to stdout (and to a stderr advisory list when no real findings exist), never written to `boundary-collisions.json`, never blocks the gate. Signals 2 and 3 retain their original blocking semantics. Verified against a partner site — 5 advisories printed at 640/768/1024/1280/1536, 0 findings written, `boundary` gate clears. CLAUDE.md's `boundary` gate row is updated to call out the signal split.

### Compatibility

- Preflight blocks are documentation; nothing executes automatically. Existing installations that already have all deps see no behavior change. Users coming via `npx skills add voidmatcha/ui-clone-skills` who hit the preflight failure can resolve with the printed bootstrap one-liner without losing their session.
- The `font-parity` and `boundary` script fixes are pure bug fixes against scripts added in 0.4.8 — no API surface change, no new artifacts, no schema additions. The `boundary-collisions.json` schema is unchanged; previously-failing projects with no real overflow / jitter just see an empty `[]` array now instead of a populated-but-non-actionable list. The `font-parity.json` schema is unchanged; projects whose primary font name contains quotes (most projects, since CSS computed-style emits quoted multi-word families) now get an artifact written instead of script abort.
- Version bumped 0.4.8 → 0.4.9 across `plugin.json`, `marketplace.json`, `pyproject.toml`. CI `claude-pre-push.sh` enforces the three-way version sync.

## [0.4.8] - 2026-05-09

Paid-font + boundary-collision detection release, plus two LLM-architecture fixes informed by 2026 Claude Skills guidance (auto-compaction 5K-token survival window, Phase E subagent isolation). Adds three new gates wedged into the post-bundle, post-implement, and post-verify positions, closing two `100% sections FAIL forever` failure classes (silent commercial-font fallback, single-pixel breakpoint collision). Also folds animation-finish hardening into `section-compare.sh` so JS-driven entrance animations (WAAPI / GSAP / anime.js / Lottie) and autoplay `<video>` elements no longer dominate AE with mid-flight frames.

### Added

- **`paid-features` gate (between `bundle` and `spec`).** Reads `tmp/ref/<c>/paid-features.json` produced by the new `skills/visual-debug/scripts/paid-features-detect.sh`, which static-greps `bundles/`, `css/`, `fonts.json`, `head.json`, `external-sdks.json` for paid font CDN hosts (Adobe Typekit, Monotype, Hoefler/Cloud.typography, Linotype, FONTPLUS / TypeSquare). Each finding starts with `decision: null`; the gate refuses to pass until every entry has `decision` ∈ {`use`, `substitute`, `skip`}. **Cross-validated by `gate_spec`:** any finding marked `decision="substitute"` must have a matching entry in `tmp/ref/<c>/asset-substitution.json` `fonts[]`, otherwise spec gate fails BEFORE Step 7 generation runs (so a missing declaration is caught at planning time, not after a wasted generation pass). The artifact schema includes empty `paidSdks: []` / `paidAssets: []` stubs so future categories can be added without renaming the file. **Note:** GSAP plugins are no longer flagged — GSAP became 100% free following the Webflow acquisition.
- **`boundary` gate (after Step 8-pre-bound).** Reads `tmp/ref/<c>/responsive/boundary-collisions.json` produced by `skills/visual-debug/scripts/breakpoint-collision-check.sh`. The script captures the impl at every Tailwind boundary ±1 (default 640/768/1024/1280/1536) and flags widths where `matchMedia(max-width: <bp>)` AND `matchMedia(min-width: <bp>)` both match, body overflows in isolation, or root font-size jitters. Catches the new Root Cause J (Tailwind ↔ project `@media` inclusive-boundary collision) — a 1-pixel-wide overflow zone that AE/SSIM never sees because Step 4-C2 measurements happen to land on those widths. Gate refuses to pass until the array is `[]`. Script writes the artifact only when `REF_DIR` env is set; if unset, the script now prints a stderr warning explaining why the gate will FAIL with "MISSING".
- **`font-parity` gate (after Step 8b-pre).** Reads `tmp/ref/<c>/font-parity.json` produced by `skills/visual-debug/scripts/font-parity-check.sh`. The script extracts the primary `font-family` from both ref and impl (computed style of `<body>`'s first text-bearing descendant) AND calls `document.fonts.check()` to verify the FontFace is *actually loaded*. The gate enforces three conditions: (1) `parity:"match"` PASSes; (2) `parity:"match"` with `ref.loaded=true && impl.loaded=false` FAILs as **silent fallback** (declared family identical, browser silently rendering with default sans-serif because the CDN font 404'd / CORS-blocked / Typekit kit ID expired); (3) `parity:"match"` with `ref.loaded=false && impl.loaded=false` FAILs as **both-sides fallback** (parity result is meaningless, neither side rendering the declared font); (4) `parity:"mismatch"` requires `asset-substitution.json` with `fonts[] >= 1` entry. Closes the "100% sections FAIL forever" trap when commercial fonts are silently substituted at impl time.
- **`skills/ui-reverse-engineering/asset-substitution.md`.** Documents the `tmp/ref/<c>/asset-substitution.json` schema for declaring deliberate font / image / video substitutions. Lists `structuralOnlySections: ["main-hero", "*"]` patterns (substring match; `*` is wildcard); `section-compare.sh` reads the file at run start and switches matching sections to `🔁 STRUCTURAL_ONLY` mode (layout structure diff still runs, pixel diff is skipped). Output `result.txt` now reports `N PASS, N FAIL, N SKIP, N STRUCTURAL_ONLY` so the breakdown is visible to the Stop gate.
- **`skills/ui-reverse-engineering/agent-environment-rules.md`.** Extracts five environment-level rules out of the always-loaded SKILL.md preamble (viewport ordering — `open → set viewport → wait`; zsh word-split; monorepo path resolution; agent-browser CLI verb syntax; flat `tmp/ref/<component>/` layout). SKILL.md now carries a one-line pointer instead of the rule bodies, restoring the progressive-disclosure budget. Sub-doc count: 38 → 39.
- **`skills/visual-debug/scripts/scroll-anim-temporal-diff.sh`** — advisory diagnostic for scroll-driven repeating animations. Samples each matched element's position at N scroll progress steps on both ref and impl, classifies the wave family as single-frequency (traveling wave, smooth interlock) vs per-row-frequency (irregular gaps from desync) vs mixed. Catches the "wave family wrong" bug class that AE/SSIM cannot see — animation pixels match in any frozen frame but perceived motion is different. Marked **advisory only — no gate** in the visual-debug script table; run manually when scroll-driven repeating elements "feel off".
- **Diagnosis Root Cause J — Tailwind ↔ CSS `@media` Boundary Collision.** Symptoms (broken at exactly 768, fine 1px on either side), root cause (both ranges inclusive at the boundary, fluid root font-size colliding with desktop columns at <bp>), diagnosis eval (`matchMedia(max-width)` AND `matchMedia(min-width)` both true at <bp>), and two fix strategies — A: shift project `@media` to `(max-width: <bp - 0.02>px)` (Bootstrap pattern, broad fix) or B: bump Tailwind variant up one tier (`md:` → `lg:`, single-component fix). Diagnosis decision tree updated A–I → A–J.

### Improved

- **`ui_clone.state.GATE_ORDER` is now the single source of truth for gate registration.** `gate.py`'s `VALID_GATES` and dispatch dict are derived from it. Adding a gate is now two steps: (1) add the name to `GATE_ORDER`, (2) add a matching `gate_<name>` method to `Gate` (with `-` → `_`). The import-time validator catches drift the moment a gate is added without an implementation, with no per-call overhead. Replaces the prior triplicate listing across `VALID_GATES`, `_make_dispatch`, and `_gate_keys`.
- **`section-compare.sh` pauses `<video>` autoplay AND finishes JS-driven entrance animations.** New `FINISH_ANIMATIONS` eval detects WAAPI (`document.getAnimations().finish()`), GSAP (`gsap.globalTimeline.getChildren().progress(1)`), anime.js (`anime.running.seek(duration)`), and Lottie (lottie-web + `<lottie-player>`/`<dotlottie-player>`) and jumps active timelines to their end frame. The CSS-only animation pause (`animation-play-state: paused`) does not stop libraries that mutate inline styles via RAF or use the Web Animations API; without this, screenshots captured mid-flight frames (opacity 0.5, translate3d(20px, 0, 0)) producing huge AE that has nothing to do with structural correctness. Re-applied after each scroll because IntersectionObserver/ScrollTrigger callbacks fire fresh tweens. Plus a "snap nearly-zero translate3d to 0" heuristic (threshold `<10px translate AND opacity >= 0.95`) for animations that didn't quite reach their end state. Disable with `SKIP_FINISH_ANIMATIONS=1` if your impl renders entrance animations as the final state by design.
- **`gate_spec` defensively flags missing `paid-features.json` when extraction artifacts already prove paid CDNs are in play.** Previously, running `gate spec` directly without first running the `paid-features` gate would silently skip the cross-validation — agents who jumped ahead got a false PASS. Now spec gate scans `fonts.json` / `head.json` / `external-sdks.json` for known paid CDN hostnames; if any are present and `paid-features.json` is missing, spec FAILs with a pointer to run `paid-features-detect.sh`. Pipelines with no paid CDNs continue to pass through silently (no behavior change).
- **`/ui-capture` accepts a 3rd `[component]` arg** so it writes to `tmp/ref/<component>/` directly (matches `ui_clone.gate` expectations — flat layout, no `capture/` parent). Without the arg, output continues to land in `tmp/ref/capture/` for standalone usage. SKILL.md's Phase 1 row now requires the 3rd arg when invoked from the ui-RE pipeline; passing only `/ui-capture <url>` writes to the wrong directory and the `reference` gate fails.
- **Tests:** 260 → 286 (+26). New coverage spans `gate_paid_features` (missing artifact / invalid decision / pending decision / valid pass paths), `gate_boundary` (missing / invalid JSON / non-array / empty array / collisions present), `gate_font_parity` (match / silent-fallback / both-sides fallback / mismatch declared / mismatch undeclared / empty fonts[] / invalid parity), and the spec-time paid-font cross-validation.
- **Phase E LLM review now mandates Claude Code-style `Agent`-tool subagent delegation.** `comparison-fix.md` Phase E section adds a "MANDATORY: delegate to a subagent" block that instructs Claude Code hosts to invoke `Agent({ subagent_type: "general-purpose", … })`, or equivalent subagent delegation in other hosts, rather than reading the 22 ref+impl pairs inline. Vision-token cost drops from ~44K (in main context) to ~500 tokens (verdict markdown table only) — the subagent context absorbs the rest. `visual-debug/SKILL.md` Phase E mention updated with the same delegation instruction. Inline reads remain allowed when the session is terminating, but only with an explicit reason recorded; default is subagent. Closes the largest single-step token cost in the verification pipeline. Grounded in 2026 Anthropic guidance ("subagents for context isolation, especially vision-heavy tasks").
- **Critical-rule front-loading for auto-compaction 5K-token survival window.** Anthropic's auto-compaction documentation states that re-attached skills retain only the first ~5,000 tokens of each `SKILL.md`. `ui-reverse-engineering/SKILL.md` (~5.8K tokens) and `visual-debug/SKILL.md` (~4.9K tokens, borderline) both placed `agent-browser --session <name> close` / "never `close --all`" guidance near the end of the file — at risk of being clipped during long cloning sessions that re-invoke these skills post-compact. Both files now carry concise survival copies of the browser-cleanup rule (and Ralph worker rules in ui-RE) inside the front-matter blockquote that always lives in the first ~1K tokens. Detailed sections at the file end remain unchanged but are now marked as "the detail; this one-liner is the survival copy".

### Compatibility

- The new gates are inserted into `state.GATE_ORDER` between existing positions; `python -m ui_clone.gate <c> all` now runs ten gates instead of seven. Pipelines that explicitly target individual gates by name continue to work.
- `paid-features.json` schema includes empty `paidSdks: []` / `paidAssets: []` stubs but the `paid-features` gate today only validates `paidFonts`. Future categories can be added without renaming the artifact or changing the gate key.
- `breakpoint-collision-check.sh` and `font-parity-check.sh` both require `agent-browser` and run against the live impl URL — there is no offline fallback. The `boundary` and `font-parity` gates fail with `MISSING` when their artifacts are absent, with the fix command embedded in the failure message.

## [0.4.7] - 2026-05-09

CI portability + tooling release. Two failure modes from the v0.4.6 push surfaced gaps in the local pre-push surface and ship as a hardening pass — no skill behavior changes.

### Added

- **`scripts/ci-local.sh`** — Single source of truth mirroring the GitHub Actions `test` job (pytest → mypy → ruff → shell syntax → review.sh). Runs locally with the same commands and order as CI, so type errors and lint failures are caught before push instead of after. Used by humans (`bash scripts/ci-local.sh`) and by `scripts/claude-pre-push.sh`, which now invokes it as a blocking step before allowing `git push`. Emergency bypass: `UI_RE_SKIP_CI_LOCAL=1 git push` (mirrors the existing `UI_RE_SKIP_BASH_GATE` pattern). The first push that exercised the new hook caught a real defect (Korean text in `skip-zones.md`) that the prior local checks missed — proving the gap was load-bearing.

### Fixed

- **`ui_clone/hooks/pre_bash.py:246` mypy redeclaration.** `failures` was annotated `list[dict[str, str]]` at line 174 inside the bash-write branch, then re-annotated at line 246 in the same function scope — mypy correctly flagged `[no-redef]`. Local pre-push didn't run mypy, so this only surfaced on GitHub Actions. Fix: drop the redundant annotation on the second assignment (already typed at line 174).
- **`ui_clone/hooks/pre_generate.py:118-119` ruff F541.** Two `f"..."` strings with no `{...}` placeholders left over from an earlier interpolated-message version. Removed the `f` prefix on both. Same root cause as the mypy issue — local pre-push didn't run ruff.
- **`scripts/review.sh` language consistency check is now portable.** Was using `grep -rlP '[\x{AC00}-\x{D7AF}]'` — `-P` is GNU-only, so macOS BSD grep silently no-op'd the check while Linux CI flagged real violations. Replaced with a Python `re.compile(r'[\uAC00-\uD7AF]')` scan that gives identical results on both platforms. This was the second class of "local pre-push didn't catch what CI catches" within 24 hours; the new ci-local.sh wrapper plus this portability fix close that loop.
- **`skills/ui-reverse-engineering/skip-zones.md:120` non-English quote replaced with English paraphrase.** A verbatim non-English user message embedded as evidence in the failure-table row violated the CLAUDE.md English-only rule for skill docs. Replaced with "scroll-reactive section transitions, text transitions, footer video — still not applied" — same diagnostic value, English-only.

### Improved

- **`scripts/claude-pre-push.sh` now blocks on full CI mirror, not just the security gate.** The hook previously ran `pre-push-security.sh` (secrets/eval/manifests) and a version-sync check before allowing push, but did not run pytest, mypy, ruff, shell syntax, or review.sh. As a result, two CI failures shipped to GitHub before the agent noticed. The hook now invokes `ci-local.sh --quiet` after the fast checks, blocking push on any test/type/lint/review failure with `decision: block` plus the exact remediation command. ~30-60s overhead per push, but locks the door against the failure class that just hit.
- **`CLAUDE.md` verification-gate section** updated to list `scripts/ci-local.sh` as the canonical pre-commit check (replaces standalone `pytest` mention) with a note that `ci-local.sh` and `.github/workflows/ci.yml` must be kept in sync.

### Compatibility

- No skill behavior changes. The pipeline, gates, hooks, and visual-debug scripts behave identically. Only the local-developer-facing pre-push surface and an internal review-script grep flavor change.
- `UI_RE_SKIP_CI_LOCAL=1` is an emergency bypass — its presence in shell history is a deliberate signal that someone overrode the gate, same auditing pattern as `UI_RE_SKIP_BASH_GATE`.

## [0.4.6] - 2026-05-08

Verification-skip enforcement release, grounded in JSONL analysis of the 89a64 cloning session (31 wallclock hours, 30 `compact_boundary` events). The data showed that **73.3% of all verification skips and 60.4% of all sub-doc skips occurred within 20 minutes of a context-compact event**, while early-session (pre-first-compact) verification skips ran at 0%. Once a session segment passed its first compact without re-reading the verification sub-docs, the skip pattern persisted to the end of that segment — segments 1–6 of 89a64: 17 hours, 227 edits, 0 sub-doc reads. v0.4.5 supplied the *gates* (`reveal-trigger-check.sh`, `transition-spec-coverage.sh`) but did nothing to make the agent re-read the gate list after a compact, and nothing to block declaration-of-done bash commands when verification was incomplete. v0.4.6 closes both holes. **Also bundles a splash-detection signal expansion** for BEM-prefix loaders and anime.js/Barba transition stacks (a partner site failure class) — see the `splash-extraction.md` / `bundle-analysis.md` bullet under Improved.

### Added

- **`ui_clone/hooks/session_resume.py`** — SessionStart + PostCompact reinjection hook. When a `tmp/ref/<c>/.ui-re-active` WIP marker exists at session-resume or post-compact, injects `hookSpecificOutput.additionalContext` containing the empirical post-compact statistic, the numbered list of required gate scripts (with `$SCRIPTS_DIR` resolution snippet), the spec-aware sub-doc reading list, and an explicit warning that `transition-compare.sh` (hover only) is not sufficient to claim transitions are matched. Conditional content based on `transition-spec.json` triggers — intersection entries get a "REQUIRED" inline marker pointing at `reveal-trigger-check.sh` and `transition-implementation.md`'s "IntersectionObserver placement" section. Silent exit when no WIP marker exists. Registered in `hooks.json` for both `SessionStart` and `PostCompact` events; module disambiguates via `trigger`/`summary` payload fields.
- **`ui_clone/hooks/pre_bash.py`** — PreToolUse Bash hook with two checks. **(1) Declaration-of-done block:** pattern-matches `git commit`, `git push`, `gh pr create`, `gh pr merge`, `gh pr close` (anchored at start-of-command). When a WIP marker is active and either `pipeline-state.json` shows `current_gate != "done"` *or* `sections/result.txt` contains ❌ FAIL / ⚠️ MISSING impl lines, denies the tool with a `permissionDecision: "deny"` payload listing the specific failing artifacts and the exact gate command to run. Read-only commands (`git status`, `git diff`, `git log`, `gh pr view`) pass through. **(2) Bash-redirect bypass closure:** also blocks Bash redirects/streams that write to component files (`cat > Foo.tsx`, `cat >> globals.css`, `tee Foo.tsx`, `sed -i ... Foo.tsx`) when the pre-generate gate hasn't passed — symmetrical with the Edit/Write gate so shell-redirect bypass is closed. Discovered via JSONL analysis: 2 instances in one onpixel session of `cat >> globals.css << EOF` writing component CSS while extraction was incomplete, totally unmonitored by the prior PreToolUse Edit/Write hook. Bypass for emergencies: `UI_RE_SKIP_BASH_GATE=1`.
- **`install.sh`** (repo root, executable) — curl-pipeable bootstrap installer. One-liner: `curl -LsSf https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh | bash`. Self-bootstraps when piped (clones to `$HOME/.local/share/ui-clone-skills`, refuses to overwrite if directory exists with foreign contents, then exec's the on-disk copy), idempotent system-dep install (uv, ffmpeg, imagemagick, dssim, npm, agent-browser via brew on macOS or apt-get on Debian/Ubuntu Linux), `uv sync`, then idempotent `claude plugin marketplace add` registration. Flags: `--no-deps`, `--no-marketplace`, `--yes/-y`. Honors `INSTALL_DIR`, `UI_CLONE_REPO`, `UI_CLONE_REF` env overrides. Final message points the user at `/plugin install ui-clone-skills@voidmatcha`. Replaces the prior multi-step manual setup in README.md (now documented as a fallback under `<details>`).
- **`scripts/jsonl-skip-analysis.py`** — offline JSONL transcript analyzer for measuring agent skip patterns across `~/.claude/projects/<dir>/<session>.jsonl`. Detects per-session: tool counts, declaration-of-done bash commands (commit / push / PR ops), verification-script invocations, sub-doc reads, component-file edits, gate calls, `compact_boundary` events, and skip events (`declaration_without_verify`, `edit_without_subdoc` over a 100-event sliding window). `is_plugin_active()` filter excludes sessions with no gate / sub-doc / verification activity. Defaults to scanning `~/.claude/projects/<encoded-project-dir>`. Used to validate v0.4.6 enforcement effectiveness by surfacing real-world bypass patterns (the bash-redirect bypass closure above came directly from this analysis).
- **`ui_clone.state.PipelineState.demote_to(gate, ref_dir)`** — new method that retreats `current_gate` back to the named gate and removes that gate (plus any later gates) from `completed_steps`. Used to invalidate downstream state when artifacts that fed into a passed gate are modified after the fact. Never advances forward — `demote_to` from `reference` to `section-compare` is a no-op. Atomic write via `.json.tmp` + replace, same pattern as `mark_passed`.

### Fixed

- **`ui_clone/hooks/pre_generate.py` now creates `.ui-re-active` on first passing gate.** The marker had no auto-creation site since v0.2.9 — the original `hooks/ui-re-start-session.sh` was deleted then, and v0.4.0's Python migration added a "bail if marker absent" guard to `pre_generate.py` without porting any creator. Net effect: in real (non-test) sessions, all six marker-gated hooks (`pre_generate`, `pre_bash`, `post_verify`, `devtools_errors`, `session_resume`, plus `section_gate`'s enforcement) silently no-op'd from v0.4.0 through v0.4.5; the v0.4.6 enforcement system on its own would have shipped on dead plumbing. Fix: drop the absent-marker bailout, always run the gate on component-file edits, and `Path.touch()` the marker on a passing gate (creates if absent, refreshes if present). Activation message prints only on the first creation to avoid spam on subsequent edits. Restores the design originally documented in `skills/ui-reverse-engineering/SKILL.md`.

### Improved

- **`ui_clone/hooks/pre_generate.py` post-done invalidation.** Previously, once `current_gate` reached `done` the Stop hook (`section_gate.py`) allowed any subsequent stop event without re-running section-compare — meaning post-completion edits to component source could ship unverified. The pre-generate hook now detects `current_gate == "done"` at edit time, calls `state.demote_to("section-compare", ref_dir)`, *also* renames `sections/result.txt` to `result.txt.stale` (so the next section-compare gate run can't re-pass on the prior PASS lines without `section-compare.sh` actually being re-run), and prints a stderr notice. The next Stop event will re-run section-compare against the new code, blocking until the visual diff is re-validated.
- **`ui_clone/hooks/section_gate.py` no longer unlinks the WIP marker on section-compare pass.** The prior behavior was to remove `.ui-re-active` once section-compare completed, on the assumption that "WIP closed = nothing more to enforce". But every other hook (`pre_generate`, `pre_bash`, `session_resume`) short-circuits when the marker is absent — so removing it here meant the post-done invalidation logic above was dead code, and any post-completion edit shipped unverified. The marker now persists; `current_gate == "done"` is the canonical "complete" signal, and the existing 3-day stale-marker guard still cleans up genuinely abandoned WIPs. Stop hook short-circuits on `done` so persistent markers don't add noise.
- **`ui_clone/hooks/session_resume.py` skips injection when state is `done`.** Now that the marker persists past completion, session-resume on a finished project would otherwise spam the verification checklist on every PostCompact / SessionStart — noise on a project that legitimately doesn't need re-verification (no edits since pass). The hook now reads `pipeline-state.json` per active marker and only injects for refs whose `current_gate != "done"`.
- **`auto-diagnose.sh` preloader detection.** Splash/preloader overlays with `position: fixed; z-index: 9999+` cause `elementFromPoint` to return the overlay at every coord, so every probed mismatch resolves to the overlay instead of the section content underneath. Heuristic detector now scans `body > *` for a fixed element with `z-index >= 1000` covering `>= 80%` of the viewport at `opacity >= 0.5` and `display: none`s the first match. Heuristic is preferred over a scroll-trigger hack: not all preloaders dismiss on scroll, and synthetic scroll fires scroll-driven animations prematurely on sites that listen for it.
- **`auto-diagnose.sh` overlay-hide scoped to section-crop diagnosis.** The fixed/sticky-element hide pass now only runs when `DIFF_DIR=diff` and `PARENT_DIR=sections` (i.e. probing `tmp/ref/<c>/sections/diff/<name>.png`). For static / full-page diffs (`tmp/ref/<c>/static/diff/<N>pct.png`), fixed headers and banners are legitimate diff targets — hiding them would make the probe miss the very element causing the diff. Also dropped the `dataset.__autodiag_*` save/restore stubs since nothing was restoring them.
- **`auto-diagnose.sh` python3 argv passing.** `python3 - "$DIFF_IMG" << 'PYEOF'` form, replacing the prior interpolated heredoc. Eliminates a quoting hazard on diff image paths containing spaces or shell metacharacters.
- **`section-compare.sh` and `transition-compare.sh` `NO_CANVAS=1` opt-in.** When set, injects a stylesheet that hides every `<canvas>` element on both ref and impl sessions before comparison. WebGL / Three.js / particle-effect canvases render different per-frame content even when the same demo loop is running, so AE-diff treats them as a wall of mismatches and drowns out the real structural diffs. Off by default — only useful when the agent has already determined the canvas content is dynamic and excluded from the implementation scope. Mirrors the existing `NO_IMAGES=1` flag.
- **Splash detection signals expanded in `splash-extraction.md` and `bundle-analysis.md`.** Prior detection at Step 5c-a relied on two signals — a bundle grep for `preloader|splash|introAnimation` and a DOM check for `[class*=preloader]` / `is-loading` / `loading` html classes. Field iteration on a partner site (Slater + Barba + anime.js stack) showed both miss the same failure class: the loader element is named `#js-loader` / `.o-loader` (BEM organism prefix), the splash timeline lives inside a `transitions[].once()` method using anime.js (no `gsap.` prefix to grep for), and the splash CSS is critical-CSS-injected (not in any file from `performance.getEntriesByType('resource')`, so `tmp/ref/<c>/css/site.css` contains zero `.o-loader` rules). Result: `hasPreloader: false` is recorded, the splash is never implemented, and every body-class-gated hero/section entry animation (e.g. `.foudre-hero[data-once] .foudre-hero-card`) silently freezes in its `from` state — a false-negative that ships looking superficially correct because static screenshots match but every entrance animation is dead. `bundle-analysis.md` now runs four signals instead of two — added (3) anime.js library + Barba `once()` / `basicTransition` grep, and (4) html/body class transition diff over time (capture early at `open`, again at +6s, diff for `is-loading`→`is-loaded`, body classes added `-once`/`-hideLogo`/`-loaded` that gate hero entry animations). DOM selector broadened to include `#js-loader`, `.o-loader`, `.m-loader`, `.a-loader`. `splash-extraction.md` adds a "Detection signals when AE diff misses splash" section walking signals A/B/C with full `agent-browser eval` scripts, plus an "Extracted-CSS gap" caveat: loader CSS often lives in inline `<style>` blocks, critical-CSS injected before hydration, or runtime-generated keyframes — `getComputedStyle(loaderEl)` on the live page is the source of truth, not the downloaded `site.css`. Bias is now toward false-positive (extra capture work) over false-negative (silent animation freeze), with explicit "Why four signals, not two" rationale documenting the silent-failure cost.

### Why this design

Three failure modes were possible before this release:

1. **Post-compact verification skip** (73.3% of past incidents). Compact compresses the verification checklist out of context; the agent forgets which gates are required and runs only the easiest one (`transition-compare.sh`, hover-only) before declaring done. Reinjection re-anchors the checklist on every compact and session-resume.
2. **Declaration-of-done before verification.** Agent runs `git commit`/`gh pr create` while gates haven't passed. PostToolUse advisory in `post_verify.py` printed warnings but didn't block — per the JSONL data, advisories did not change behavior. PreToolUse `pre_bash.py` now blocks at the Bash gate, returning a deny payload that names the missing artifacts.
3. **Post-completion edit drift.** Agent runs the full pipeline, gate state advances to `done`, then edits a component file in response to feedback — but the section-compare result.txt is now stale. Stop hook saw `current_gate == "done"` and let the agent ship. The pre-generate hook now demotes state on any post-done component edit, forcing re-verification at the next Stop.

### Compatibility

- All three changes are additive. Existing pipelines/projects without a `.ui-re-active` WIP marker see no behavior change — every new hook short-circuits when the marker is absent.
- `pre_bash.py` only triggers on a closed list of declaration-of-done command prefixes. All other Bash commands pass through unchanged.
- `demote_to` is a new method; existing callers of `PipelineState` are unaffected.
- The bypass env var `UI_RE_SKIP_BASH_GATE=1` is documented as emergency-only — its presence in shell history is a signal that someone deliberately overrode the gate.

### Tests

- `tests/test_hooks.py` — `TestSessionResume` (8, +1 for done-state skip), `TestPreBash` (10), `TestPreGeneratePostDoneInvalidation` (3, +1 for result.txt invalidation). Plus update to `TestSectionGateFullEnforcement::test_section_compare_pass_when_result_all_pass` — assertion flipped to require marker *persistence* after section-compare passes.
- `tests/test_state.py` — `demote_to` tests (3): from-done retreat, no-advance invariant, unknown-gate noop.
- `tests/test_hooks.py` — `TestPreBashFileWriteBypass` (8 new tests): `cat > component.tsx`, `cat >> globals.css`, `tee Foo.tsx`, `sed -i ... Foo.tsx` blocked when extraction incomplete; redirect to non-component file allowed; `>/dev/null` ignored; `UI_RE_SKIP_BASH_GATE=1` env bypass; passing gate allows write.
- Suite size: 234 → 252 → 260 tests, all passing.

## [0.4.5] - 2026-05-07

Field-iteration release from the 375.studio clone debugging session. The session surfaced a single failure class that the existing transition gates could not catch: scroll-triggered reveals (RevealRise / RevealLetters / intersection-fade-up patterns) silently failing because the `IntersectionObserver` ref was attached to the transformed child of an `overflow: hidden` parent — IO computes the post-clipping intersection rect, so a child translated 100% out of its mask reports `intersect: false` forever and the reveal never animates in. `transition-compare.sh` only verifies idle→hover diffs, so it reported PASS while every intersection entry on the page was completely broken. Took 12 iterations of manual probing on one component to name the bug class. This release adds two new visual-debug scripts, one runtime and one static, that turn that 12-iteration diagnosis into a single command, plus cross-doc additions in `ui-reverse-engineering` so the failure mode is one hop from symptom (`patterns.md`) → diagnosis (`diagnosis.md`) → fix (`transition-implementation.md`) → prevention (`skip-zones.md`). Also includes three `section-compare.sh` correctness fixes that emerged in the same session.

### Added

- **`visual-debug/scripts/reveal-trigger-check.sh`** — runtime gate for the "stuck reveal" bug class. Enumerates every initially-hidden element on the impl page (opacity 0 or non-identity transform), scrolls each into view, settles, recaptures style, and FAILs any whose `opacity` and `transform` never advance past their initial values. Walks the parent chain of every stuck element and surfaces the `overflow: hidden` ancestor that's most likely clipping the IntersectionObserver, so the diagnosis ("IO observed the moving child instead of the static outer wrapper") is named on first run rather than after a multi-iteration probe loop. Standalone — no ref needed; works on the impl URL alone. `bash reveal-trigger-check.sh <session> <impl-url> [w] [h]`. Exit 0/1/2.
- **`visual-debug/scripts/transition-spec-coverage.sh`** — static counterpart to the runtime gate above. Parses `transition-spec.json`, builds a per-entry needle list (entry id in kebab/camel/Pascal forms, selector tokens with CSS-Modules hash suffixes stripped, type-derived hook hints — `intersection-fade-up` → `RevealRise` / `useScrollTrigger` / `IntersectionObserver`; `scroll-driven` → `useScroll` / `useTransform`; `hover` → `onMouseEnter` / `:hover`; etc.), greps the impl source for each needle, FAILs if any entry has zero hits. Catches the meta-bug where the agent reported "transitions matched" after running only `transition-compare.sh` (hover only) while intersection/scroll-driven entries were never even wired. `bash transition-spec-coverage.sh <component-dir> <impl-src-dir>`. Exit 0/1/2.
- **`transition-implementation.md` → "IntersectionObserver placement for masked reveals"** — full explanation of *why* the IO+overflow:hidden bug class fires (IO respects ancestor clipping; `boundingClientRect` reports post-transform position regardless), with parallel WRONG / RIGHT TSX code blocks (ref on moving child vs ref on static outer wrapper). Includes a verification IIFE that dumps `intersect / ratio / rect / rootBounds` so an agent can confirm ancestor clipping in one eval. Generalizes to `clip-path: inset(...)` masks, `RevealRise` / `RevealLetters` / `RevealLine` / custom `IntersectionFadeUp` patterns, and notes that `display: contents` does NOT fix this (IO can't observe contents elements).
- **`transition-implementation.md` → "Verification per spec-entry trigger type"** — matrix mapping each `transition-spec.json` `trigger`/`type` to the script that actually verifies that category: `hover` → `transition-compare.sh`, `intersection`/`inview` → `reveal-trigger-check.sh`, `scroll`/`scroll-driven` → `batch-scroll.sh` + `auto-diagnose.sh`, `auto-timer`/`raf` → `bundle-verification.md` numerical comparison, `click` → `transition-compare.sh` with `--actions`. Calls out the coverage gate (`transition-spec-coverage.sh`) as the prerequisite to running any of those rows. The matrix exists to prevent the "I checked the hover ones, looks good" failure mode — every row must return PASS for entries of that trigger type before the agent can claim transitions are done.
- **`diagnosis.md` → "Stuck-reveal triage flow (3 steps, in order)"** — explicit 3-step ordering for diagnosing any "scroll-triggered reveal doesn't trigger" symptom: (1) `transition-spec-coverage.sh` to rule out "extracted into spec but never implemented", (2) `reveal-trigger-check.sh` to rule out "wired but stuck at runtime" (the IO+clip case + GSAP baked init + missing observer), (3) `transition-compare.sh` only after both are clean. Names the parent-chain column of step 2's output as the smoking gun for the IO+clip bug.
- **`diagnosis.md` Root Cause E new bullet** — IO ref on transformed child of `overflow: hidden` mask. Documents the silent-no-error symptom (`intersect: false` forever, no console error) and the manual IO-eval check that confirms ancestor clipping vs other reveal failure modes. Cross-refs `transition-implementation.md`'s placement section.
- **`patterns.md` failure-table row** — "Mask-based reveal never triggers (`opacity: 0`, `translateY(100%)` stuck) on element clearly inside the viewport". Explains why `getBoundingClientRect` reporting "in viewport" is misleading (it returns post-transform position, but IO uses post-clipping). Points at the placement section.
- **`skip-zones.md` — three new failure-trap rows.** (1) "Transitions self-reported by category" — verifying only the hover entries from the spec while intersection-fade-up / scroll-driven entries were silently omitted; required action is `transition-spec-coverage.sh` before claiming done. (2) "Stuck reveals dismissed as 'looks right'" — static screenshot of a reveal slot showed background and was deemed acceptable, but RevealRise/RevealLetters never triggered; required action is `reveal-trigger-check.sh`. (3) "section-compare 'MISSING impl' dismissed" — treated as className-mismatch noise on a Tailwind-vs-CSS-Modules clone, but the table was the only signal that real sections were unmatched; required action is `auto-diagnose.sh` + `reveal-trigger-check.sh` on the failing crop before dismissing.
- **`visual-debug/SKILL.md` — two new rows in the script table and decision table**, plus Step 0a-bis (reveal-trigger-check) and Step 0a-ter (transition-spec-coverage) in the Workflow section. Explicitly notes that `transition-spec-coverage.sh` and `reveal-trigger-check.sh` are the **first two** transition gates, not escalations — coverage catches "entry never wired", reveal-trigger catches "wired but stuck", and `transition-compare.sh` (the third) only verifies idle→hover diffs so it can pass while intersection/scroll-driven entries are completely broken.

### Improved

- **`section-compare.sh` className-anchored pre-pass before fingerprint pairing.** Greedy fingerprint pairing breaks down when the ref has sections with no impl counterpart (cookie banners, third-party overlays) — they steal the best-fingerprint match away from a real ref section, cascading wrong pairs. New pre-pass walks ref sections first, anchors each to an impl section that shares a className token (≥4 chars, e.g. CSS-Modules tokens like `page_first__r2OaE`), and only falls back to fingerprint pairing for refs with no anchor. Pre-pass matches are scored 1.0 and tagged `pairing: 'className-exact'` in the result so the eventual report can distinguish anchored pairs from fingerprint pairs.
- **`section-compare.sh` overlay sweep covers vendor-specific consent SDKs.** First sweep now removes `#iubenda-cs-banner`, `[id^=iubenda-]`, `[class*=iubenda]`, `[id^=onetrust-]`, `[class*=onetrust]`, `[id^=osano-]`, `[class*=osano]`, `[id^=cky-]`, `[class*=cookieconsent]` before the existing heuristic sweep runs. The heuristic sweep (popup/modal/cookie/banner/overlay/signup keyword match on fixed/absolute elements) was missing the iubenda CSS namespace because its container class is `iubenda-cs-container` — no matching keyword.
- **`section-compare.sh` ENUMERATE_SECTIONS no longer descends on `<header>`/`<footer>`/`<nav>`/`<aside>` children.** Previously the descent rule treated those as structural-section children, but inside a section they are *content* roles (page header, section heading row, table-of-contents nav, sidebar aside) — descending lost the wrapping section entirely. Only `<section>` and `<main>` children now trigger descent, matching the structural intent of the original rule.

### Compatibility

- Both new scripts are additive — no existing script signature, gate, or pipeline behavior changes. `reveal-trigger-check.sh` and `transition-spec-coverage.sh` are off the default workflow; agents must invoke them explicitly per the new SKILL.md Step 0a-bis / 0a-ter.
- `section-compare.sh` pre-pass changes pairing order, but the final report shape (matches list, scores, name dedup) is unchanged. Any test or downstream tool that consumed the JSON shape continues to parse the same fields.
- ENUMERATE_SECTIONS descent rule is a behavioral tightening (fewer descents). Pages that depended on header/footer/nav/aside descent will now flag the wrapping section instead — a strictly more useful default.

## [0.4.4] - 2026-05-05

Field-iteration release from the adcker showcase debugging session. Five new failure-mode entries: three from the assumption-vs-measurement family (preloader counter timing, body-bg flash on preloader fade, reflexive header hide-on-scroll — all sharing the anti-pattern of implementing behavior on assumption like "`startTime` at mount is fine", "`bg-white` = white", "hide-on-scroll is standard UX" instead of measuring the reference) and two from the HTML→JSX ingestion family (inline `onclick="..."` silently dropped by JSX, source-HTML tag typos like `<snap>` that browsers tolerate but TSX rejects). All five are cross-referenced from `generation-pitfalls.md` so each failure is one hop from symptom→fix regardless of which doc the agent reads first. Plus a stale-reference correction — `diagnosis.md` line 3 and `SKILL.md` lines 173/246 said "A–H" / "A–G" but v0.4.3 had already added Root Cause I, leaving the two newest causes (H Stray Absolute, I Tailwind v3/v4) hidden from agents grepping for diagnosis scope. Plus a small batch of Python reliability fixes surfaced by an audit pass — `pipeline.py` `has_ref` logic bug, `OSError` handling on three `Path.stat()` callsites, and a `subprocess.run` without timeout in the hook common module.

### Added

- **Preloader counter starts mid-progress** (`splash-extraction.md` "Common splash failures"). When `startTime = performance.now()` is captured at `useEffect` mount but the counter loop runs after a chain of init delays (slide-in, intro animation), `elapsed = now - startTime` already includes those delays on frame 1 — counter reads 40%+ instead of 0%. Fix: capture `startTime` inside the function that actually starts the counter. Includes a parallel BAD/GOOD code block so the diff is one line. Generalizes to any "elapsed since start" timestamp behind a delay chain.
- **Body bg flash on preloader fade-out** (`splash-extraction.md` "Common splash failures"). When the preloader uses a utility class (e.g. `bg-white`) and the body uses a CSS keyword (`background: white`), in scoped/embedded projects (Tailwind under `[data-project]`, design-system layer) those can resolve to different RGB values and the fade reveals the body in a different color. Includes a null-safe agent-browser eval that compares both computed bg colors and recommends ref's body color as the canonical value. Fix: set body bg to the exact hex/rgb (not a CSS keyword that can't *match* a redefined utility class).
- **Header hide-on-scroll assumption row** (`no-judgment.md` Group A). Temptation: "header should hide on scroll-down — that's standard UX". Required action: scroll ref deep, wait 500ms, check `getComputedStyle(headerEl).transform`. If `none`, do NOT add hide-on-scroll — ref doesn't have it.
- **Three failure-table cross-ref rows** (`generation-pitfalls.md`). One row each for preloader counter mid-progress, color flash on preloader fade, header hides in impl but stays in ref. Each points to the specific section in `splash-extraction.md` or `no-judgment.md` so the failure is discoverable from Step 7 (generation-pitfalls.md) as well as from the Steps 5c-a / 6A paths that read `splash-extraction.md` and from any step that reads `no-judgment.md`.
- **Two HTML→JSX conversion failure rows** (`generation-pitfalls.md`). (1) "Pasted-HTML element compiles fine but its interaction is silently dead" — source HTML uses inline `onclick="..."` / `onmouseover="..."`; JSX treats these as unknown string attributes and never executes them, with no console error. Required action: grep `\bon[a-z]+="` on extracted HTML before paste, port each match to a real React handler, capture any global instance via ref/context. (2) "TS error `'foo' is not a JSX.IntrinsicElements`" — source HTML had a typo (`<snap>` for `<span>`, `<sectiom>` for `<section>`); browsers parse unknown tags as `HTMLUnknownElement` and render them, JSX rejects. Required action: scan extracted HTML with `grep -oE '<[a-z]+[ />]' | sort -u`, fix at source then re-extract — don't silently rename in JSX since the typo may be load-bearing for a CSS selector.
- **Regression test for `has_ref` derivation** (`tests/test_pipeline.py::test_check_phase_1_regions_only_does_not_set_has_ref`). Pins the canonical "reference exists" signal to `phase_1.checks[0]` (static/ref screenshots), so a future refactor can't silently re-introduce the `any(c.passed for c in phase_1.checks)` form that let `regions.json`-only setups satisfy the gate.

### Fixed

- **`pipeline.py` `has_ref` derivation no longer over-permits Phase 2.** `run_status()` was computing `has_ref = any(c.passed for c in phase_1.checks)` — but `phase_1.checks` includes `scroll-video/`, `transitions/`, and `regions.json` alongside `static/ref/`. Any one of the supplementary checks would let `has_ref = True` and Phase 2 would not be skipped, even with zero reference screenshots. Now uses `phase_1.checks[0].passed` (the canonical static/ref signal that `check_phase_1` itself uses to decide `next_step`).
- **`generation-pitfalls.md` cross-ref to "Conditional branches" pointed to wrong file.** The conditional-animation-branches section lives in `splash-extraction.md` (line 166: "## Conditional animation branches (CRITICAL)"), not in `animation-detection.md`. The wrong pointer would have wasted a `Grep` round-trip for any agent following it.
- **`gate.py:check_file()` now handles `OSError` from `Path.stat()`.** Between the `path.exists()` check and the `path.stat().st_size` call, the file can be deleted, become inaccessible (permission change), or hit any other `OSError`. Previously this would crash the gate with an unhandled exception; now returns a graceful FAIL with the OS error attached.
- **`dag.py:check_staleness()` now handles `OSError` on both `parent_path.stat()` and `child_path.stat()`.** Same TOCTOU window — file existence verified, then `.stat()` called separately. Now skips the comparison if `.stat()` fails instead of crashing the staleness scan.
- **`hooks/section_gate.py` catches `OSError` (not just `FileNotFoundError`) on `marker.stat()`.** `PermissionError` and other `OSError` subclasses on the WIP marker would have raised through the `Stop` hook and aborted the run without a usable error.
- **`hooks/_common.py` git subprocess now has `timeout=10`.** The `git rev-parse --show-toplevel` call in `_find_project_root()` had no timeout — a hung git operation (network filesystem stall, lock contention) would have blocked every `PreToolUse`/`PostToolUse`/`Stop` hook indefinitely. Catches `subprocess.TimeoutExpired` alongside the existing `FileNotFoundError`.
- **`splash-extraction.md` retitled and re-classified as a sub-protocol.** Title was `# Splash / Intro Animation Extraction — Step 2.6` and SKILL.md's reference table listed it under Step 2.6, but the doc has always documented itself as called from later steps (preloader-detection-in-bundle and Tier 1 AE diff in the first 1–3s) — Step 2.6 itself produces `animation-init-styles.json` / `state-coupling.json` and Step 2.6-pre produces `dom-state-diff.json`, neither of which lives in `splash-extraction.md`. The doc is structurally a cross-cutting sub-protocol (same shape as `skip-zones.md`, `no-judgment.md`, `diagnosis.md`, `dynamic-content-protocol.md`), so its title now drops the step suffix and SKILL.md's reference table marks it `—` with a "called from Steps 5c-a / 6 Phase A" note. The doc body's "When to read" entries are also retagged from `Step 5c` to `Step 5c-a` (matching the v0.4.3 5c-a / 5c-b sub-step split that this doc had drifted away from). No artifact, gate, hook, or pipeline behavior changes — purely a documentation reclassification that aligns the doc with the convention already used by other cross-cutting sub-protocol docs.
- **Nine stale `5c` / `5c–5d` step labels corrected to `5c-a` / `5c-a–5d`** across `bundle-analysis.md:182` ("Detection (here, Step 5c)" — self-contradicted the file's own header which already said "Step 5c-a"), `js-animation-extraction.md:164` (custom-scroll definition cross-ref), `interaction-detection.md:5,460` ("After this step" pointer + hover-timing gate cross-ref), `dom-extraction.md:580` (splash-detection gate description), `component-generation.md:11` (custom-scroll input description), `SKILL.md:145` (gate-example comment `# after 5c`), and `skip-zones.md:36,44` (Zone 1 header + Bundle completeness row). v0.4.3 split Step 5c into 5c-a (bundle-analysis.md, download + grep) and 5c-b (bundle-verification.md, numerical comparison) and updated CLAUDE.md to make 5c-a/5c-b canonical, but nine body-doc references kept the pre-split label. An agent reading "(Step 5c)" mid-doc could not run `python -m ui_clone.gate ... 5c` (no such step) and might think they were in the wrong place.
- **`diagnosis.md` and `SKILL.md` root-cause range corrected from A–H to A–I.** v0.4.3 added Root Cause I (Tailwind v3/v4 Transform Conflict) but the count in `diagnosis.md` line 3 ("8 root causes (A–H)") and the SKILL.md table descriptions (lines 173, 246: "Root Cause A–G") were not updated. Future agents grepping SKILL.md for diagnosis scope would have missed both H (Stray Absolute Positioning, the "footer disappeared" bug class) and I — exactly the two newest causes. Updated to "9 root causes (A–I)" / "Root Cause A–I" across the three call-sites.

### Compatibility

- Doc additions are purely additive. Cross-ref fix is a documentation-only correction.
- Python fixes preserve all existing return types and exception contracts; the `has_ref` change tightens behavior in the direction the local check_phase_1 already documented but the run_status codepath had drifted from. No public API or artifact path changes.

## [0.4.3] - 2026-05-04

Field-iteration release. New diagnostic Root Cause (I — Tailwind v3/v4 transform conflict), inner-scroll-container detection for Lenis/locomotive-style sites, transition-detection exclude-selector support, splash anti-pattern, decision table for the visual-debug diff family, step-numbering anchor split (5c → 5c-a/5c-b), and Python single-source-of-truth fixes for pipeline gate enumeration.

### Added

- **Root Cause I: Tailwind v3/v4 Transform Conflict** (`diagnosis.md`). Catches the "double-translated / double-rotated / double-scaled element" bug class that fires when the host app and the cloned project use different Tailwind majors. v3 emits a composed `transform:`; v4 emits individual `translate:` / `rotate:` / `scale:` properties on the same utility class. Both rules apply simultaneously, compounding the effect. Includes the v4-minifier gotcha (overriding the resolved property gets collapsed; override CSS variables instead) and the SVG `transform-box` view-box vs bounding-box quirk.
- **`tailwindMajor` site-detection probe** (`site-detection.md`). Probes `translate-x-1` on the live page to determine the Tailwind major (3 or 4). When it differs from the host app's major, `site-detection.md` directs the agent to pre-emptively add the scoped `[data-project="<name>"] :is(...) { translate|rotate|scale: none !important }` block at scaffolding time — cheaper than chasing visual regressions one-by-one later.
- **Inner scroll-container detection** in `batch-scroll.sh`, `section-compare.sh`, and documented in `verification.md`. Lenis / locomotive-scroll / `body { overflow: hidden }` sites move the document scrollbar to an inner wrapper, so `window.scrollTo` silently no-ops and every `<n>pct.png` looks identical. The detection finds the largest scrollable element on the page; subsequent scroll commands target it via `wrapper.scrollTop = Y; wrapper.dispatchEvent(new Event('scroll'))`. The synthetic scroll event is required so Lenis/IntersectionObserver listeners re-evaluate.
- **`EXCLUDE_SELECTORS` env var** in `transition-compare.sh`. Default skips Finsweet Cookie Consent (`.fs-cc_*`), `[id*=cookie]`, `[class*=cookie-banner]`, `[class*=consent]` — third-party SDK overlays the clone never replicates, which previously polluted `ref-elements.json` with elements that have no impl counterpart.
- **Post-splash reveal-all anti-pattern** (`splash-extraction.md`). Documents the failure mode where the splash-finish handler unconditionally adds `.is-visible` to *every* reveal element on the page (including ones far below the viewport), so subsequent scroll-triggered reveals never animate in. Fix gates the bulk reveal by viewport visibility — only reveal elements whose `getBoundingClientRect().top < window.innerHeight` and let the IntersectionObserver pick up the rest.
- **"Pick the right diff tool" decision table** (`visual-debug/SKILL.md`). Five computed-style/geometry diff tools (`computed-diff`, `auto-diagnose`, `tree-diff`, `layout-tree-diff`, `hover-tree-diff`, `keyframes-diff`) now indexed by what question each answers, with cost classification and escalation order. Prevents running all five by default.

### Improved

- **Identity-matrix transform normalization** in `transition-compare.sh`. `matrix(1, 0, 0, 1, 0, 0)` is the identity transform — semantically equivalent to `none` — but they string-compare unequal. Normalization eliminates a noise class where ref reports `none` and impl reports `matrix(1,0,0,1,0,0)` (or vice versa) for elements that have no transform applied.
- **Step-numbering anchor split** across `SKILL.md`, `CLAUDE.md`, and 15 sub-docs. `Step 5c` is now `5c-a` (download + grep — `bundle-analysis.md`) and `5c-b` (numerical comparison — `bundle-verification.md`). The Step 7-related sub-docs (`generation-pitfalls.md`, `style-audit.md`, `post-gen-verification.md`, `transition-implementation.md`) and the T-prefix transition sub-docs (`measurement.md` → `T-1`, `element-capture.md` → `T0`, `css-extraction.md` → `T2a`, `js-animation-extraction.md` → `T2b`, `canvas-webgl-extraction.md` → `T2c`) have title formats that match SKILL.md anchors verbatim. ui-capture sub-docs renamed from `# ui-capture — <name>` to `# <name> — Phase X`.
- **CLAUDE.md gate→artifact map expanded** to list every artifact each gate checks (matching `ui_clone/gate.py:30-36` dispatch keys), with explicit Phase 0A note that `canvas-webgl-detection.json` is *advisory*, not gated — it routes the agent to `canvas-webgl-extraction.md` but no `gate_canvas_*` enforces it.
- **`pipeline.py` no longer hardcodes total gate count.** Both the progress header (`Progress : N/7 gates completed`) and the JSON `total_steps` field now derive from `len(state.GATE_ORDER)`. Previously `7` was duplicated in two places, so adding/removing a gate would silently mis-display progress until the literals were also updated.
- **`gate.py` import-time cross-validator for `state.GATE_ORDER`.** The existing validator checked `VALID_GATES` ↔ `Gate._gate_keys()`; new check additionally asserts `set(state.GATE_ORDER) == Gate._gate_keys()`. Drift between gate dispatch and pipeline-state ordering now fails fast at import instead of silently mis-reporting progress.
- **`review.sh` step-anchor checks tightened.** `bundle-analysis.md` is now checked for `"Step 5c-a"` (was loose substring `"Step 5c"`) and a new check enforces `bundle-verification.md` says `"Step 5c-b"`. Locks the new sub-step anchors against silent regressions.

### Security

- **`section-compare.sh` shell-injection consistency.** Detected scroll-container selectors flow into Python f-strings used to build `agent-browser eval` commands. Selectors are now validated against a strict allow-list (`^[a-z][a-z0-9]*(#[a-zA-Z][a-zA-Z0-9_-]*)?(\.[a-zA-Z][a-zA-Z0-9_-]*)?$` or `__document__`); anything else falls back to `__document__`. Matches the v0.4.2 hardening pattern applied to `transition-compare.sh`.
- **`transition-compare.sh` `EXCLUDE_SELECTORS` JSON-encoded.** The bash variable is now JSON-encoded via `python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))'` before substitution into the JS body, so a future selector containing `"`, `\`, or newline embeds as a valid JS string literal instead of breaking out of it. Default value (`[class*=fs-cc], [id*=cookie], …`) was already safe; this hardens the user-override path.

### Compatibility

- Sub-doc title format changes are documentation-only — no API or artifact path changes. Gates, hooks, and scripts continue to read the same JSON files.
- Inner scroll-container detection is opt-in by site shape: when `document.documentElement` is the scroller (the common case), behavior is identical to v0.4.2.

## [0.4.2] - 2026-05-03

Field-iteration release. New diagnostic scripts (tree-diff family, stray-absolute, video-transition), `section-compare.sh` accuracy improvements, security hardening of `transition-compare.sh`, gate ordering bugfix, hook performance improvement, and a new diagnostic Root Cause (H — Stray Absolute Positioning).

### Added

- **Root Cause H: Stray Absolute Positioning** (`diagnosis.md`) — catches the "footer disappeared" bug class where `position: absolute` elements have no positioned ancestor and resolve their offsets against `<body>`. Often manifests only on shorter viewports.
- **`stray-absolute-check.sh`** (`skills/visual-debug/scripts/`) — single-URL detector for Root Cause H. No ref needed. Now the first thing run in `visual-debug` Step 0 (Structural).
- **`tree-diff.sh`** — exhaustive per-element computed-style diff. Walks every visible impl element ≥ MIN_SIZE px, pairs with ref via `elementFromPoint`, runs computed-style diff per pair. Catches mismatches AE misses (wrong font that renders identically, same-box different-style overrides).
- **`layout-tree-diff.sh`** — geometry diff via signature-based pairing (text + tag + class hash + size class). Reports top/left/w/h deltas regardless of where elements moved. Catches "right element, wrong position" bugs.
- **`hover-tree-diff.sh`** — per-element hover/transition diff. Captures idle → CDP `:hover` → settled style for each pair. Diffs timing (property/duration/easing/delay) + idle→hover delta. Catches missing hover rules, wrong easing, mismatched deltas. Uses CDP-level `:hover` because synthetic events do not fire `:hover`.
- **`keyframes-diff.sh`** — `@keyframes` declaration diff. Extracts all keyframe rules from both pages and reports keyframes only on one side or same-name rules with different steps. Catches missing entrance animations and wrong timing curves baked into keyframes.
- **`video-transition-compare.sh`** (`scripts/`) — video-based transition comparison. Records the same interaction on orig + impl, extracts frames at 60fps, runs SSIM batch diff. Replaces the deleted `scripts/transition-compare.sh` for video flows; the in-skill `skills/visual-debug/scripts/transition-compare.sh` (idle/hover style + timing) remains.

### Improved

- **`section-compare.sh` accuracy** (field-iteration findings, ordrhealth clone session). Existing thresholds and gate semantics preserved; new behavior reduces false-fails and false-passes:
  - **Auto-cleanup of stale section outputs.** Each run deletes prior PNGs in `sections/{ref,impl,diff}/` before capturing. Previously, deleted/renamed sections left orphan PNGs (e.g. `container.png`, `impl-section-5.png`) that the AE loop's glob picked up as ghost sections.
  - **STRUCTURAL_WRAPPER classification.** Ref sections with empty fingerprint and `childCount <= 1` (sticky-image holders, layout-only wrappers) are flagged `wrapper: true` in `matches.json` and skipped in the AE loop with `⏭️ SKIP (structural wrapper)`.
  - **CHILD_COUNT_MISMATCH severity downgrade on strong fingerprint match.** When matched fingerprint similarity ≥ 0.85, child-count differences drop from `major` to `minor` — visible content matches, child-count divergence is almost always harmless DOM nesting variation.
  - **AE normalization by pixel area (`AE/Mpx` column).** Severity tiers operate on AE-per-megapixel, so a 1200px-tall section isn't unfairly penalized vs a 600px-tall one with identical defect density. `result.txt` shows both `AE` (raw) and `AE/Mpx` (normalized).
- **`visual-debug` Step 0 restructured** — renamed `computed-diff` → `Structural`. Now runs `stray-absolute-check.sh` (per viewport) before the existing `computed-diff.sh` sweep. Workflow steps in both Full-page and Section-level modes updated accordingly.
- **`devtools_errors.py` performance.** PostToolUse(Bash) error collection folded inject + collect into a single idempotent snippet. Halves agent-browser round-trips per check.
- **`__init__.py` version drift fixed.** `ui_clone/__init__.py` was stuck at `0.4.0` even on v0.4.1 (release commit missed it). Now correctly bumped along with `plugin.json` / `marketplace.json` / `pyproject.toml`.
- **`pre-push-security.sh` eval scanner** no longer false-positives on comment lines that mention the word "eval" — added `^[^:]*:[0-9]+:[[:space:]]*#` exclusion to the pipeline.
- **`review.sh` shell syntax gate.** New section runs `bash -n` over every `scripts/**/*.sh` and `skills/**/*.sh`. Catches typos in the new tree-diff family before push, since shell scripts are otherwise uncovered by `pytest`.
- **`devtools_errors.py` timeout** raised from 3s to 5s. Single-eval round-trip is fast enough that the previous over-aggressive cap risked false-empty error reports under transient browser slowness.

### Security

- **`transition-compare.sh` shell-injection hardening.** Selectors were previously interpolated into shell via Python f-strings. A malicious or unusual selector value could break out of the surrounding `agent-browser eval "..."` literal. Selectors are now JSON-encoded as JS string literals and `agent-browser` is invoked via `subprocess.run([...], shell=False)`. Filenames derived from selectors are also sanitized via a single `[A-Za-z0-9._-]` regex (capped at 30 chars) instead of ad-hoc string `.replace()` chains.

### Fixed

- **`gate.py`: `transition-coverage.json` no longer false-fails the extraction gate.** The artifact is produced at Step 6d, but the previous extraction gate checked it before 6d ran. Moved to `gate_pre_generate` only (where it is correctly produced before the gate fires).
- **`gate.py` empty-array detection** uses proper `json.loads(...) == []` instead of string-comparing `"[]"`/`"[ ]"` — handles whitespace, newlines, and decoder errors safely.

### Compatibility

- `section-compare.sh` `result.txt` adds an `AE/Mpx` column. `gate_section_compare` only inspects emoji markers (`❌`, `⚠️ MISSING impl`), not column count — unaffected.
- New `⏭️` SKIP rows are not counted as fail or missing by the gate.

### Docs

- `diagnosis.md` decision tree updated for 8 root causes (A–H). Added Root Cause H + cross-references to `stray-absolute-check.sh`.
- `responsive-detection.md`, `transition-implementation.md`, `transition-spec-rules.md`, `webflow-ix2.md`, `bundle-verification.md`, `patterns.md`, `post-gen-verification.md`, `generation-pitfalls.md`, `measurement.md` — incremental clarifications from field-iteration sessions.
- `visual-debug/SKILL.md` script catalog now lists all four tree-diff scripts.

## [0.4.1] - 2026-05-01

Test coverage hardening — 18 new tests covering previously untested hook integration paths and pipeline extraction phase.

### Added

- **`TestPostVerifyVerificationNotRun`** (2 tests) — post_verify Check 1: warns when verification has not been run (no diffs/health file)
- **`TestPostVerifyBatchCompareFailures`** (2 tests) — post_verify Check 2: warns when batch-compare-result.txt contains failures
- **`TestPreGenerate::test_wip_marker_gate_passes_touches_marker_and_prints_stop_gate`** — pre_generate gate-passes path: verifies marker touch and stop gate activation message
- **`TestCheckPhase2`** (11 tests) — pipeline.py `check_phase_2` method: all extraction artifact checks, missing artifact hints, staleness warning, JS chunk count advisory, responsive sizing
- **`TestDevtoolsMainOutput`** (2 tests) — devtools_errors main() output formatting: error lines with fix hints, >10 error truncation notice
- **`_populate_pre_generate_artifacts`** helper — reusable fixture for tests needing a fully populated pre-generate gate

### Improved

- Test count: 201 → 219 (+18)
- Overall coverage: 74% → 82%
- `pipeline.py` coverage: 70% → 90%
- `devtools_errors.py` coverage: 55% → 91%

## [0.4.0] - 2026-05-01

Major refactoring — package rename, pipeline Python migration, hook consolidation, code quality hardening, content accuracy fixes, CI, and developer tooling.

### Breaking Changes

- **Package renamed:** `ui_skills/` → `ui_clone/` (Python package)
- **Plugin renamed:** `ui-skills` → `ui-clone-skills`
- **Owner renamed:** `dididy` → `voidmatcha`
- All `python -m ui_skills.*` commands → `python -m ui_clone.*`

### Pipeline Migration (`ui_clone/pipeline.py`)

`run-pipeline.sh` (388 lines of bash) replaced by `ui_clone/pipeline.py`. Reuses existing `Gate`, `PipelineState`, `find_project_root()`, and `check_staleness()`. The bash script is now a 6-line shim.

```bash
python -m ui_clone.pipeline <url> <component> <session> status [--json]
```

### Hook Consolidation

4 individual bash shim files (`ui-re-pre-generate-check.sh`, `ui-re-post-verify-check.sh`, `ui-re-devtools-check.sh`, `ui-re-section-compare-gate.sh`) replaced by a single universal `hooks/shim.sh`.

### Fixes

- **Gate timing:** `external-sdks.json` moved from `gate_bundle` to `gate_spec` — file is produced at Step 5d, not 5c
- **Step numbering:** `bundle-analysis.md` title corrected from "Step 6" to "Step 5c"
- **interaction-detection.md:** "After this step" now correctly points to Step 5b → 5c (was Step 6)
- **agent-browser npm name:** `@anthropic-ai/agent-browser` → `agent-browser` in `pipeline.py` and `ui-capture/SKILL.md`
- **agent-browser GitHub:** `github.com/anthropics/agent-browser` → `github.com/vercel-labs/agent-browser` in README
- **rtk GitHub:** added correct link `github.com/rtk-ai/rtk`
- **README numbers:** sub-doc count 32 → 37, token count ~6.3K → ~5.9K
- **transition-compare.sh:** FPS default changed from 10 to 60 to match verification.md requirement
- **comparison-fix.md:** `style-audit.md` path corrected to `../ui-reverse-engineering/style-audit.md`
- **visual-debug SKILL.md:** hardcoded local path replaced with `$SCRIPTS_DIR`
- **shim.sh:** added `uv` missing error message (was silent fail)

### Removed

- `scripts/validate-gate.sh` — use `python -m ui_clone.gate` directly
- 4 individual hook shim files
- `waapi-scrubbing.md` — orphaned doc referencing 2 missing files. WAAPI measurement covered by `measurement.md`
- `same.energy` site-specific modal dismiss code from `transition-compare.sh`

### Added

- **CLAUDE.md** — development guide with naming rules, step numbering, gate-artifact mapping, language rules, review checklist
- **scripts/review.sh** — automated review (16 checks: tests, security, step numbering, stale refs, gate timing, README accuracy, language)
- **`.github/workflows/ci.yml`** — CI with pytest, security gate, Socket supply chain scan, Snyk dependency scan
- **visual-debug/evals/evals.json** — 15 evals for visual-debug skill (was missing)
- SKILL.md metadata blocks (`filePattern`/`bashPattern`) for `ui-reverse-engineering` and `ui-capture`
- SKILL.md Reference files table expanded: 14 previously undocumented sub-docs added
- Token management section in README (built-in strategies + rtk integration)

### Improved

- Section name detection in 3 scripts expanded from 6 → 23 keywords
- `extract-assets.sh`: video poster frame extraction generalized (was hero-only)
- `claude-post-push.sh`: runs `review.sh --quiet` after successful push

### Code Quality

- **mypy:** `disallow_untyped_defs = true`, `warn_return_any = true` — all type errors resolved
- **gate.py:** `check_file()` and `check_dir()` accept optional `fix` parameter for actionable error messages
- **metrics.py:** Enhanced docstrings for `_ssim_at()` (SSIM algorithm explanation, parameter docs)
- **dag.py:** Enhanced module docstring documenting edge direction convention

### Tests

- New `tests/test_pipeline.py` (14 tests) — dependency checks, JSON loading, app dir discovery, pipeline state handling, DAG coverage validation
- Updated `tests/test_integration.py` — removed `validate-gate.sh` references, added pipeline CLI test

### .gitignore

Added: `__pycache__/`, `*.pyc`, `.venv/`, `*.egg-info/`, `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`

---



---

## Archived (older minors)

- [v0.3.x](CHANGELOG_archive/v0.3.md)
- [v0.2.x](CHANGELOG_archive/v0.2.md)
- [v0.1.x](CHANGELOG_archive/v0.1.md)
- [v0.0.x](CHANGELOG_archive/v0.0.md)
