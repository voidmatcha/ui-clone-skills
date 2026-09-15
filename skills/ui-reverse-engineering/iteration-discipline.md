# Visual-debug iteration discipline

**Audience**: anyone (host-agnostic) repairing generated content, structure, visuals, or behavior during Phase 7.

- **Claude Code path**: invoked via `visual-debug-iterator` sub-agent (`.claude-plugin/agents/visual-debug-iterator.md`). The sub-agent reads this file as its operational contract. The sub-agent's `disallowedTools` field enforces the vision-free rule by blocking `Read(*.png)` etc.
- **Codex native path**: invoked via the `visual-debug-iterator` native subagent (`.codex/agents/visual-debug-iterator.toml`) when Codex/OMX subagent routing is available. The vision-free rule is policy in the TOML instructions: do not read PNG/JPG/WebP/GIF files.
- **Inline fallback**: if a host has no delegated-worker surface, perform the same work in the main context and state that fallback explicitly. The vision-free rule still applies.

## Pre-condition

A generated implementation has a failed content, structure, runtime, or visual check. Read that check's artifact before editing. Pixel-specific rules below apply when `section-compare.sh` or `tree-diff.sh` reports FAIL; they do not require pixel capture to validate a text or runtime repair.

## Inputs

- `tmp/ref/<component>/sections/result.txt` — section-compare table (PASS / FAIL / STRUCTURAL_ONLY / SKIP per section)
- `tmp/ref/<component>/sections/matches.json` — pair-by-position matches with score
- `tmp/ref/<component>/tree-diff.json` + `tree-diff-status.json` — DOM walk diffs (counts, top critical/major/layout-major rows)
- `tmp/ref/<component>/transitions/` — hover/scroll/timer transition compare outputs
- `tmp/ref/<component>/generation-plan.json` — current contract (consult for sticky strategy, library set, signature effects)
- `tmp/ref/<component>/source-forensics.json` — optional source-backed guidance returned by the `source-forensics` worker after raw HTML/CSS/JS fallback
- `impl/src/` — the implementation source (read freely; edit only what the failing row points at)

## First generated draft

Before exhaustive motion checks, compare preserved text/media and section ownership
against the captured scaffold and inventories. Use the existing text-fidelity,
runtime-text-sequence, asset, and structure checks from the verification plan.
For a missing text row, trace live reference -> captured node -> generated node ->
rendered state. Recover extraction loss, restore generation loss, or fix visibility
at its captured trigger; do not duplicate hidden variants just to increase counts.

Check representative sections at the same viewport and scroll state after these
foundations pass. Early geometry is a diagnostic for missing/collapsed structure;
page height is a final aggregate cross-check, never the sizing objective. Fix the
source layout rule, containing block, font/media metrics, or pin lifecycle instead
of inserting numeric height floors or blank space. Run affected load-bearing motion
early when needed to reproduce a section's layout. Do not wait for every unrelated
hover/click entry to pass before diagnosing that section.

When representative pairs show a semantic mismatch that the numeric/text
diagnostics do not explain, ask the main agent to delegate Phase E with
`reviewMode: "diagnostic"` as defined in `../visual-debug/comparison-fix.md`.
Reuse the existing matched pairs for the affected section or splash. The iterator
stays vision-free; this early diagnosis cannot satisfy final visual review.

## Verification cost discipline (inner iterations)

For any known content, structure, runtime, or geometry failure, first read its failing rows and compare
the reference and implementation under the same viewport, initial state, scroll
target, and settling conditions. Fix the implementation or recover invalid
measurement evidence; never weaken a check to clear it. Rerun the failing check
and its dependency closure using `UI_CLONE_ITERATION_CHECKS=<check-id>` with
`run-required-checks.sh`. Repeat expensive section/motion capture only after these
failures clear, or when a specific diagnostic question requires that measurement.
Route exhaustive transition sweeps through the dispatcher so its prerequisite
barriers apply. Direct motion probes are for a named diagnostic question or the
affected IDs, not a way around failed content/section checks.
Report the remaining failed checks even when other sections pass. Process liveness,
a successful build, and matching total height do not prove visual convergence.

Re-running the full comprehensive sweep after every edit is the dominant
wall-clock sink (~5min+/cycle). Closeout safety is enforced elsewhere
(quick-tier closeout blocker, deferredChecks, canonical verify re-runs the
full suite), so inner iterations are SAFE to scope:

1. **Tier:** `UI_CLONE_VERIFY_TIER=standard` while iterating (one-shot browser
   checks, no 60fps video). Comprehensive ONLY for the closeout verify.
2. **Sections:** `UI_CLONE_VERIFY_SECTIONS=<failing,csv>` re-compares only the
   sections you just fixed (read the failing list from `sections/result.json`).
3. **Transitions:** `UI_CLONE_FIRES_IDS=<id1,id2>` re-probes the affected spec
   entries — writes `transition-fires.scoped.json` so the canonical artifact is
   never clobbered by a partial measurement. Firing alone does not prove the
   correct target, amplitude, or timing; use matched-state trajectory evidence.
4. **Affected set:** after a scoped fix, test the failed entries and any siblings
   driven by the same changed controller. Broaden only when that dependency
   requires it, and state why. Do not insist on the full entry set merely because
   partial evidence cannot certify completion; repair and closeout are different
   stages. Independent failures need not all be fixed before testing one fix.
5. **Closeout:** the final `pipeline ... verify` must run full comprehensive —
   scoped/standard artifacts cannot satisfy the Stop hook by design.
6. **Dynamic-reference pinning:** sites with carousels / auto-rotating banners /
   lazy content change between ref captures, so re-capturing the ref every
   compare diffs a MOVING target (impl can never converge on it). After the
   FIRST full ref capture, iterate with `RECATCH_REF=0` (frozen-ref reuse —
   compares against the same frozen ref crops every cycle); re-capture
   explicitly only when the ref evidence is genuinely stale. Carousel state is
   pinned automatically (Swiper/Splide stop + slide 0, videos at frame 0) on
   BOTH sides, so freeze your impl's initial carousel index at 0 to match.

## Discipline

1. **VISION-FREE — strict.** Do NOT `Read` any `.png` / `.jpg` / `.jpeg` / `.webp` / `.gif` file. The plugin's value prop is "near-zero vision tokens"; reading diff images here defeats the entire purpose AND introduces host vision-model interpretation variance. Use the text-based signals in order:
   - **1st: `auto-diagnose.sh`** — `bash $PLUGIN_ROOT/skills/visual-debug/scripts/auto-diagnose.sh <session> <ref-url> <impl-url> tmp/ref/<component>/sections/diff/<worst-failing-section>.png` — hotspot selectors via elementFromPoint + per-selector computed-style diff, all text. (4 args; the diff crop comes from section-compare's `sections/diff/`.)
   - **2nd: `tree-diff-status.json` + `tree-diff.json`** — DOM/style mismatches in text form (display, flex-direction, position, dimensions, font props). This catches structural fails that pixel diff can't explain.
   - **3rd: `computed-diff.sh`** — per-element computed-style comparison, text only.
   - **4th: `diagnosis.md` catalog (Root Cause A-R)** — classify by symptom, apply by class.
   - **Last resort (only if all above return "nothing actionable"):** request main agent to escalate — do NOT read the PNG yourself.
2. **No raw-source loading.** Do not read raw `bundles/*.js`, large `css/*.css`, captured HTML dumps, or full DOM/style JSON. If compact artifacts and text gates cannot explain the next fix, return `bailout-source-forensics` with the failing section, selectors, and exact source questions. The main agent will dispatch the `source-forensics` worker and then re-enter this loop with `source-forensics.json`.
3. **One fix per iteration.** Pick the highest-severity row (🌑 saturated > critical > major > layout-major > minor). Identify the specific impl file + DOM node from the text signals above. Apply a SCOPED edit (single component or single style rule). Re-run the gate.
4. **Substitution-aware.** STRUCTURAL_ONLY (substituted) rows are not failures — skip them. Focus on PASS-blocking rows only.
5. **Check the changed behavior.** For a visual fix, rerun the affected section comparison; for content, structure, or runtime repairs, rerun the corresponding failed check and affected dependencies. Record its exit code and remaining failures. A scoped result is repair evidence, not closeout.
6. **Max 5 iterations.** If 5 consecutive iterations don't reduce FAIL_COUNT, return with a "blocked" verdict naming the section + suspected root cause. The main agent decides whether to escalate.
7. **Contract preservation.** Edits must not violate `generation-plan.json` — do not swap libraries, restructure components, or change architecture layers. Stay within "scoped style/JSX/data fix." If a fix would require contract change, return with `blocked-contract-conflict`.

## Asset substitution policy (research-mode default)

**Plugin philosophy**: this is a local-use clone tool (research / benchmark / personal study) — NOT a publication pipeline. License flags from `paid-features-detect.sh` are advisory, not blockers. The default behavior is **download everything**; substitute only when an HTTP request genuinely fails.

- **MANDATORY before any substitution:** run `bash $PLUGIN_ROOT/scripts/extract/asset-download.sh "$(pwd)/tmp/ref/<component>" "<impl-public-dir>"` to attempt every URL in `visible-images.json`. The script writes `download-log.json` with HTTP status per attempt. Substitution declaration is rejected (by `asset-transfer-check.sh` + `generation-plan.sh` validator) unless an entry has a corresponding failed download attempt.
- **NEVER substitute images with `emoji-or-gradient` / `emoji` / `gradient` / `placeholder` / `stub`.** These wreck visual fidelity. Banned by `scripts/extract/generation-plan.sh` validation.
- **Concrete substitution targets only.** When download genuinely fails, replacement must be a real alternative path (free font family, CC0 image URL, brand-equivalent stock asset) — never a generic placeholder string.
- **Public-domain TLD short-circuit:** `.gov` / `wikimedia.org` / `wikipedia.org` / `commons.wikimedia.org` images are by-default downloadable. If `asset-download.sh` reports 0 succeeded for these, the network or capture is broken — investigate before declaring substitution. Agent self-assessed "looks USDA-licensed" is NOT evidence; `.gov` IS public domain.
- **Commercial fonts in research mode:** Die Grotesk, PP Neue Montreal, etc. — fetch + use the self-hosted .woff2 directly via `asset-download.sh`. This is permissible for local research / benchmark fidelity (no publication). Substitution to free font is opt-in for users who plan to publish.

## Convergence follows the failure class

Measure the signal the fix is intended to change: missing-text rows for content,
parent/asset/layout differences for structure, affected trigger/trajectory results
for behavior, and per-section AE for pixel mismatches. Do not require an unrelated
full visual sweep to prove a content or runtime repair. A build or total-height
match alone does not establish progress.

- Record the before/after failing row or metric and the affected component.
- During visual iterations, record per-section AE deltas. Two iterations without
  meaningful improvement require a new source-backed hypothesis or the delegated
  source-forensics path; do not repeat the same correction or broaden blindly.
- Never obtain convergence by weakening thresholds, hiding content, inventing
  spacers, or excluding failures without reference-backed applicability evidence.
- Final closeout still requires comprehensive, unscoped checks and canonical stamps.
  Partial improvements must be reported alongside unresolved failures.

## Bailout cases (return immediately)

- **Asset 404**: missing image at expected path → return with `fixType: "asset-transfer"`, the main agent re-runs asset-transfer-check.sh
- **Hydration error**: console reports React hydration mismatch → return with `fixType: "ssr-mismatch"`
- **Library missing**: gate output references "lenis is not defined" or similar → return with `fixType: "missing-install"`, the main agent installs the package
- **Contract conflict**: fix requires a `generation-plan.json` change (library swap, component delete) → return with `fixType: "contract-conflict"`
- **Source forensics required**: compact artifacts cannot explain the next scoped fix or two visual iterations show no AE reduction → return with `fixType: "source-forensics"`, failing section, selectors, and source questions; do not read raw HTML/CSS/JS yourself.
- **Reference-side measurement**: the failing row describes the REFERENCE, not the
  clone, so no implementation edit can clear it → return with
  `fixType: "reference-measurement"` and the row verbatim. The two that occur in
  practice:
  - `splash-lifecycle` failing `ref-overlay-absent`. The check was dispatched by a
    detector that read bundle source or a DOM diff, then measured no overlay on the
    reference itself. `refAbsence.guidance` in `splash-lifecycle.json` names what to
    inspect. Editing the clone cannot make the reference grow a splash.
  - `video-transition-compare` rows tagged `ref-unstable`. The reference does not
    reproduce itself between captures, so the comparison has no stable baseline to
    judge the clone against.

These are out-of-scope for visual iteration; they need pipeline-level intervention.

Recognising this class early is what keeps the loop bounded. Left unrecognised it
reads as an ordinary gate failure, the iterate doctrine above points at component
source, and the run burns its whole Stop retry budget editing a clone that was
never the problem. If a row names the reference rather than a selector in your
implementation, stop and report it — a correct clone cannot make it pass.

## Output

After every iteration write a single-line summary to stdout:

```
iter N: section=<name> sev=<critical|major|layout-major> fix=<file:line> gate_exit=<code> result=<PASS|reduced|same|worse>
```

After max 5 iterations or PASS, write the final verdict to stdout and return:

```
verdict: <PASS | blocked-after-5 | bailout-<fixType>>
remaining_fail: <count>
gate_exit_final: <code>
source_questions: <required when verdict is bailout-source-forensics>
```

## Don'ts

- Don't `Read` PNG / JPG / WebP / GIF — vision-free hard rule.
- Don't edit the gate scripts themselves to "make it pass".
- Don't add `// eslint-disable` or skip-tests to dodge the gate.
- Don't re-architect sections — that's the main agent's job. You're a fix-iterator, not a refactorer.
- Don't read raw bundles, large CSS, captured HTML, or full DOM/style dumps; request `source-forensics` instead.
- Don't violate `generation-plan.json` contract — return with `contract-conflict` instead.
