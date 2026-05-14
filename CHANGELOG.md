# Changelog

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
- **Multi-viewport fan-out for `hover-state-compare.sh` + `click-state-compare.sh`.** Both scripts now accept a `VIEWPORTS=<WxH>,<WxH>,…` env var (matching the convention `scroll-end-completion-check.sh` already uses). When set, the per-target loop runs once per viewport with `VIEW_W`/`VIEW_H` exported to the inner `video-transition-compare.sh`; results land under `<ref-dir>/transitions/{hover,click}-state/<WxH>/<target>/` instead of being clobbered into a single shared dir. The `result.txt` aggregator gets per-viewport sections (`viewport: 375x812` + `[375x812]` tags on each target row) so an agent inspecting a fail knows which viewport diverged. Default empty = single-viewport (back-compat — single-tier callers see no cost increase; the fan-out is an additive comprehensive-tier opt-in, not a coverage upgrade for existing callers). Closes the responsive-regression failure class where mobile-only behaviors (no `:hover`, full-screen modal sheets vs floating panels, hamburger nav swap) pass a single-desktop sweep cleanly. **Out of scope for this release: `section-compare.sh`** — the existing single-viewport implementation is 1343 lines tangled with `ONLY_IF_CHANGED` hashing, Stop-gate result-file integration, and output-dir defaults; adding a viewport outer-loop without breaking those couplings is its own follow-up. Hover + click cover the interactive-UI failure surface where responsive divergence most often hides; static section coverage stays single-viewport for now. Locked in by `tests/test_gate.py::test_hover_state_compare_fans_out_per_viewport` / `::test_hover_state_compare_single_viewport_back_compat` / `::test_click_state_compare_fans_out_per_viewport` / `::test_hover_state_compare_rejects_malformed_viewport`. The tests stub the inner `video-transition-compare.sh` via `PLUGIN_ROOT` redirection — the outer fan-out logic is what's locked in, not the inner sweep (which already has its own browser-required test coverage).
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

- **Identity-leakage scrub across the repo.** Real company / employer / partner-site / third-party-CDN names that had crept into code comments, doc bodies, fixture queries, and CHANGELOG historical entries were replaced with generic placeholders (`project-a` / `project-b` / `example.com` / `<streaming-cdn-host>` / `<image-cdn-host>` / "a partner site"). Locked in by a new `scripts/ci/pre-push-security.sh` **Identity leakage** section that greps the repo against a denylist of real-name patterns on every push and blocks on any match. Add new patterns to the script's `leak_patterns=()` array as the project grows.
- **Non-English text scrub.** Hangul codepoints in `skills/*/evals/trigger-eval.json` fixtures and a CJK-glyph example in `skills/ui-reverse-engineering/asset-extraction.md` were replaced with English-only equivalents (the asset-extraction example now uses `<sample-glyphs>` plus a "for CJK fonts, pass characters from the target subset" note). `scripts/ci/review.sh` language check no longer excludes `evals/` or `asset-extraction.md` and now also scans CHANGELOG / README / AGENTS / CLAUDE files for Hangul, so future non-English text is blocked before push.
- **`AGENTS.md` "Identity / example placeholders" rule.** Added under Rules section so future agents/humans default to generic placeholders by writing-time, not just by gate-time enforcement.
- **`scripts/ci/test-parity.sh` drift smoke test.** Mutates tracked files to known-bad states (denylisted name, AKIA-shaped secret, version drift, broken JSON, Hangul), runs the relevant guard (`pre-push-security.sh` or `review.sh`), asserts the expected error substring appears, restores from backup. Prevents the new identity-leak denylist + language scanner + version-sync check rotting silently if a regex breaks or a denylist entry is dropped. Wired into `ci-local.sh` step 6. `pre-push-security.sh` gains a `$DRIFT_TEST` exclude (sibling to `$SELF`) so the test file's inline trip patterns don't self-trigger the scanner.
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

## [0.3.1] - 2026-04-30

Full-audit hardening — browser session correctness, validation gate reliability, lazy-content fingerprinting, skill invocation UX, hook system fixes, Webflow IX2 extraction, transition coverage audit, incremental pipeline improvements from active clone sessions (real-world clone sessions), and Python migration of the gate/hook system.

### Python Migration (`ui_skills/` package)

Replaced ~2,400 lines of bash (`validate-gate.sh`, three pipeline hooks) with a Python 3.11+ package managed by `uv`. All external interfaces (CLI args, exit codes, `hooks.json`) are unchanged — existing workflows require no updates.

**Root causes fixed:**

- **Silent staleness failure** — `stat -f %m` (macOS) and `stat -c %Y` (Linux) both fell back to `echo 0` on failure, completely disabling staleness detection. Replaced with `Path.stat().st_mtime` (Python stdlib, cross-platform, raises on error).
- **Flat staleness detection** — `validate-gate.sh` only compared direct parent → child pairs. A change to `structure.json` did not invalidate `component-map.json` (two hops away). Now uses a dependency DAG with BFS + Kahn's topological sort: changing `structure.json` correctly marks `section-map.json` → `component-map.json` → `extracted.json` all stale.
- **Hook bypass via Bash** — `PreToolUse` hook only blocked `Write`/`Edit` tool calls. Direct `Bash` writes bypassed the gate entirely. The new hook fires on `Write|Edit` and validates against the Python gate result.
- **Single-scale SSIM false positives** — a 1px layout shift produced SSIM scores below threshold, triggering spurious failures. Replaced with 3-scale multiscale SSIM (1/4 → 1/2 → full resolution, weighted [0.5, 0.3, 0.2]).
- **Hardcoded 50px threshold** — `diff > 50px → CRITICAL` regardless of viewport. Replaced with viewport-relative thresholds: `fontSize` 2% of 100px, `width` 5% of viewport, `margin`/`padding` 8% of 100px.
- **Emoji-based hook parsing** — hooks parsed `✗`/`✅` from text output, breaking in non-UTF-8 environments. Replaced with `--json` flag outputting `{"passed": bool, "fail_count": int, "failures": [...]}`.

**New files:**

| File | Replaces |
|------|----------|
| `ui_skills/gate.py` | `scripts/validate-gate.sh` (594 lines) |
| `ui_skills/dag.py` | inline mtime comparisons in `validate-gate.sh` |
| `ui_skills/metrics.py` | single-SSIM calls in `compare-sections.sh` |
| `ui_skills/hooks/pre_generate.py` | `hooks/ui-re-pre-generate-check.sh` |
| `ui_skills/hooks/post_verify.py` | `hooks/ui-re-post-verify-check.sh` |
| `ui_skills/hooks/section_gate.py` | `hooks/ui-re-section-compare-gate.sh` |

Each replaced bash file is now a 2-line `exec uv run` shim. `uv` auto-creates a virtualenv and installs dependencies on first invocation — no manual setup required.

**Test coverage:** 56 tests across `test_dag.py`, `test_gate.py`, `test_hooks.py`, `test_metrics.py`, `test_integration.py`.

### Fixed

#### `transition-compare.sh` — bash syntax error (script completely broken)

`HOVER_CAPTURE_SCRIPT` was a single-quoted shell variable containing Python code. Expanding it with `python3 -c "$HOVER_CAPTURE_SCRIPT ..."` inside a double-quoted argument caused bash to fail at parse time when the Python string literals (e.g. `'ref'`, `'ref-elements.json'`) were encountered — bash interprets the single-quotes as ending the double-quoted string context. Result: `syntax error near unexpected token '('` on every invocation; the script was completely non-functional.

Fix: write the Python function to a `mktemp` tmpfile via `cat > "$_TC_PY" << 'PYEOF'` heredoc, pass session names + output dir as env vars (`_TC_SESSION_REF`, `_TC_SESSION_IMPL`, `_TC_DIR`), run `python3 "$_TC_PY"`. Secondary fix: merged the second `trap ... EXIT` (tmpfile cleanup) into the existing `cleanup_all` function so browser close is not overwritten.

#### `agent-browser close` arg order — systemic bug (9 scripts)

All affected scripts used `agent-browser close --session NAME` or `agent-browser close "$SESSION"`. Wrong order silently closed the default session instead of the named one, leaking Chrome Helper GPU + Renderer processes indefinitely. Correct form: `agent-browser --session NAME close`.

- `scripts/auto-verify.sh` — 2 occurrences
- `scripts/transition-compare.sh` — 4 occurrences (trap + inline after record stop)
- `scripts/freeze-animations.sh`, `extract-assets.sh`, `extract-section-html.sh`, `section-clips.sh`, `extract-dynamic-styles.sh` — trap EXIT calls
- `skills/visual-debug/scripts/layout-diff.sh` — 4 occurrences (trap + inline)
- `skills/visual-debug/scripts/layout-health-check.sh` — 4 occurrences (trap + inline)

#### `validate-gate.sh` — project root detection + bundle gate

`project_root` fallback used `$(dirname "$REF_DIR")/../..`, which resolved incorrectly for non-git nested repos. Replaced with: (1) `git rev-parse --show-toplevel` when available, (2) walk-up loop that ascends from `$REF_DIR` until a directory containing `tmp/ref/` is found.

Bundle gate previously required ≥3 JS files, blocking valid 2-file micro-sites. Changed to require ≥1 file (hard block) with advisory warning for <3.

#### `run-pipeline.sh` — app directory detection + COMP_COUNT

Phase 3 app-dir detection picked the first alphabetical monorepo match. Now prioritizes `apps/$COMPONENT/src/components`, `apps/$COMPONENT/src`, `apps/$COMPONENT/app` before generic search.

`COMP_COUNT` only searched `src/components`, missing Next.js App Router pages. Now searches `src/components + src/app + app` in parallel.

#### `skills/visual-debug/scripts/section-compare.sh` — lazy content + mid-animation screenshots

Two independent bugs fixed:

1. **Lazy-loaded sections had empty `innerText`** — fingerprint = `""` → `MATCH_COUNT=0` → every section fell back to positional matching. Fix: pre-scroll both sessions through the full page before fingerprint extraction to trigger IntersectionObserver lazy loading.

2. **Scroll-triggered CSS transitions mid-animation at screenshot time** — `window.scrollTo()` triggers GSAP ScrollTrigger / enter-reveal animations; fixed 0.3s wait wasn't sufficient. Fix: re-inject `PAUSE_ANIMATIONS` CSS after each section scroll inside the Python capture loop.

#### `hooks/ui-re-section-compare-gate.sh` — section name regex

`grep -oE '^\| [a-zA-Z0-9_-]+ \|'` missed section names with dots (`section.active`), slashes, or spaces. Replaced with `sed -n 's/^| \([^|]*\) |.*/\1/p'`.

#### Hook system reliability

- **Stop hook fired on unrelated responses**: Now guarded by `.ui-re-active` WIP marker in `tmp/ref/<session>/`.
- **PostToolUse hook non-functional**: Read from `TOOL_INPUT` env var (never set by Claude Code). Fixed to read JSON from stdin + `tool_input.command` via Python.
- **Path resolution broken in all 3 gate scripts**: `find ~/.claude/skills` returned empty when skills installed elsewhere. Fixed to use `$(dirname "$0")`-relative paths.
- **Content injection: paths printed instead of content**: `validate-gate.sh`, `section-compare.sh`, `computed-diff.sh` now print actual `diagnosis.md` Root Cause sections inline on failure.

#### `computed-diff.sh` — reliability

- `jq` pipe on bash arrays failed with `Parse error: Extra data` for selectors with special characters. Replaced with `python3 -c "import json,sys; print(json.dumps(sys.argv[1:]))"`.
- Missing `wait` between open and eval caused stale style extraction. Added parallel open + `wait $WAIT_MS` (default 4000ms).
- `2>&1 > /dev/null` redirect order wrong. Fixed to `>/dev/null 2>&1`.
- Single session reuse caused browser history interference. Now uses `<session>-orig` + `<session>-impl` parallel sessions.

### Added

- **`skills/ui-reverse-engineering/webflow-ix2.md`** (new) — Step W: mandatory Webflow IX2 extraction. Detects IX2 via `<meta name=generator>`, extracts hide-rule selector list, downloads IX2 timeline JSON, maps `actionTypeId` values to React hooks. Gate enforces 3 JSON artifacts before any other extraction on Webflow sites.

- **`skills/ui-reverse-engineering/transition-coverage.md`** (new) — Step 6d: multi-position scroll measurement → `transition-coverage.json`. Samples 10 scroll positions, decodes every CSS transform matrix, classifies scroll-driven / enter-reveal / static, builds per-element curve table. Pre-generate gate now requires `animatedElements.length > 0`.

- **`skills/ui-reverse-engineering/diagnosis.md`** (new) — Root Cause A–E with diagnosis commands + fix patterns. Printed inline by `section-compare.sh` and `computed-diff.sh` on failure.

- **`skills/ui-reverse-engineering/skip-zones.md`** (new) — 5 responsibility zones replacing the flat 50-row "steps most likely skipped" table. Each zone has a gate check command; printed inline by `validate-gate.sh` on failure.

- **`skills/ui-reverse-engineering/no-judgment.md`** (new) — Group A–E decision tables: Measurement vs Assumption, Library Choice, Visual Semantics, CSS Cascade, Verification & Data Trust.

- **`skills/visual-debug/common-selectors.md`** (new) — Ready-to-use selector sets: CSS reset canaries, Tailwind preflight known resets, example-site specific, general e-commerce. `IGNORE_FONT_SIZE=1` guide for macOS 105% text-scaling false positives.

- **`computed-diff.sh` — `IGNORE_FONT_SIZE=1` env var** — skips fontSize/lineHeight/width/height diffs from OS-level text scaling. Fix hints printed on mismatch (Tailwind preflight fontWeight reset, display:block on img, height:auto on img).

- **`component-generation.md` — Screenshot-first rule** — before writing code for any section, reference screenshot MUST be Read. Mandatory guessed-implementation verification block added (screenshot ref + impl at same trigger point).

- **`generation-pitfalls.md` — new failure patterns** (real-world clone sessions):
  - 3rd-party library replaced with custom impl (Swiper/Splide → `useState` slider)
  - JS scroll threshold guessed (`scrollY > 10` hardcoded without bundle grep)
  - `!important` as first resort (valid only for CSS you can't modify)
  - CSS top/left hardcoded in JS parallax (breaks responsive)
  - Off-screen DOM elements not implemented (`mo-nav`)
  - CSS animation classes never triggered (`effect-flip`)
  - `dangerouslySetInnerHTML` for nav messages
  - `<br>` in JSX fragments

- **SKILL.md — URL prompt instruction** (`ui-reverse-engineering`, `ui-capture`): When invoked without `<url>`, Claude stops and asks for the URL instead of proceeding.

### Changed

- **`ui-reverse-engineering/SKILL.md`** slimmed from 688 lines → ~210 lines. "Steps most likely skipped" moved to `skip-zones.md`. "No Judgment" moved to `no-judgment.md`. WIP marker instruction added. Webflow IX2 (Step W) and transition coverage (Step 6d) added to pipeline table.

- **`visual-debug/SKILL.md`** — `computed-diff` promoted to Step 0 (before AE/SSIM capture).

- **`hooks/hooks.json`** — `ui-re-section-compare-gate.sh` Stop hook entry added (was created but never registered).

---

### Fixed

#### `computed-diff.sh` — critical bug fixes
- **`jq` pipe broke on bash arrays** — `printf '%s\n' "${SELECTORS[@]}" | jq -R . | jq -s .` failed with `Parse error: Extra data` when selectors contained special characters or attribute selectors (`[class*=foo]`). Root cause: bash array expansion inside command substitution is unreliable across shells. **Fix:** replaced with `python3 -c "import json,sys; print(json.dumps(sys.argv[1:]))" "${SELECTORS[@]}"` — deterministic, no shell expansion issues.
- **Missing `wait` between open and eval** — `agent-browser eval` ran before page JS settled, returning stale/empty styles. **Fix:** added parallel open + `wait $WAIT_MS` (default 4000ms) for both sessions before extracting.
- **`2>&1 > /dev/null` stderr redirect order wrong** — stderr mixed into stdout before redirect, polluting captured output. **Fix:** corrected to `>/dev/null 2>&1` throughout.
- **Single session reuse** — opening orig then impl in the same session caused browser history interference. **Fix:** uses `<session>-orig` and `<session>-impl` parallel sessions with `trap EXIT` cleanup.
- **No error output on empty response** — silent failure when agent-browser returned empty string. **Fix:** explicit check + `exit 2` with actionable message.

#### `computed-diff.sh` — new features
- **`IGNORE_FONT_SIZE=1` env var** — skips fontSize/lineHeight/width/height diffs caused by macOS OS-level text scaling (105% system setting produces 14.7px vs 14px, which is not a real bug). Documented in output and `common-selectors.md`.
- **Fix hints in output** — when mismatches found, prints prioritized fix list (Tailwind preflight fontWeight reset, display:block on img, height:auto on img).
- **Better parse error messages** — shows raw output snippet to aid debugging.

### Added

- **`skills/ui-reverse-engineering/webflow-ix2.md`** (new) — Step W: mandatory Webflow IX2 extraction procedure. Detects IX2 via `<meta name=generator>`, extracts hide-rule selector list from inline `<style>`, downloads IX2 timeline JSON, runs multi-position scroll measurement (wheel-event aware for Lenis), and maps IX2 `actionTypeId` values to React hooks (`useScroll+useTransform`, `useScrollTrigger`, state toggles). Gate enforces `webflow-detection.json`, `webflow-hide-rule.json`, `webflow-ix2.json` before any other extraction on Webflow sites. Prevents silent loss of scroll-driven rotations, parallax, 3D folds, opacity fades, width-draw animations, and section enter-reveals.

- **`skills/ui-reverse-engineering/transition-coverage.md`** (new) — Step 6d: multi-position scroll measurement audit → `transition-coverage.json`. Builds candidate element list, samples at 10 scroll positions, decodes every CSS transform matrix, classifies each element as scroll-driven / enter-reveal / static, and produces a per-element curve table. Pre-generate gate now requires `transition-coverage.json` with `animatedElements.length > 0`. Prevents decorative shapes shipping as static, folder icons staying 2D, headlines never revealing, line-draws staying at width:0.

- **`scripts/validate-gate.sh` Webflow + transition-coverage checks** — `pre-generate` gate now validates: Webflow IX2 artifacts (if `webflow-detection.json` indicates Webflow), `transition-coverage.json` existence, `animatedElements.length > 0`, and animated count plausibility vs `data-w-id` count.

- **`hooks/ui-re-section-compare-gate.sh`** (new) — Stop hook that blocks Claude from finishing if `section-compare.sh` hasn't been run or has failures. Reads `$REF_DIR/sections/result.txt` — blocks on missing file, `❌` FAIL lines, or `MISSING impl:` lines. Prevents self-reporting "done" without running visual verification.

- **`skills/visual-debug/common-selectors.md`** — reference document with ready-to-use selector sets:
  - CSS framework reset canaries (h1–h6, img, button — the elements most commonly broken by Tailwind/CSS-reset)
  - Tailwind preflight known resets table (fontWeight, display, height, color, text-decoration) with symptoms and fixes
  - example-site specific selectors (from real clone work)
  - General e-commerce/portal selectors
  - OS font-scaling artifact detection guide

### Changed

- **`visual-debug/SKILL.md` workflow** — `computed-diff` promoted to **Step 0** (before AE/SSIM capture). Rationale: computed-diff finds root causes directly; AE only shows that something is wrong. Running computed-diff first eliminates the hunt-through-diff-images cycle for CSS property mismatches.
- **Scripts table** — `computed-diff.sh` moved to first row with "Run first" label. `common-selectors.md` reference added below table.
- **`skills/visual-debug/scripts/section-compare.sh`** — auto-saves results to `$DIR/sections/result.txt` after AE comparison. Previously required manual `tee`; now always written so the Stop gate works reliably.
- **`skills/ui-reverse-engineering/generation-pitfalls.md`** — 5 new failure patterns from a real-world clone session: CSS top/left hardcoded in JS (parallax responsive), off-screen DOM elements not implemented (mo-nav), CSS animation classes never triggered (effect-flip), `dangerouslySetInnerHTML` for nav messages, `<br>` in JSX fragments.

### Fixed (docs/config)

- **`hooks/hooks.json`** — `ui-re-section-compare-gate.sh` was added in this release but never registered. Added `Stop` hook entry so the gate actually fires.
- **`README.md` hooks table** — `ui-re-section-compare-gate.sh` was missing from the hooks table. Added.

## [0.3.0] - 2026-04-28

Quality and reliability hardening. No new skills or features — all changes are backwards-compatible bug fixes, documentation improvements, and operational hardening.

### Fixed

#### High-severity bugs
- **6 broken cross-references** — `ui-capture/SKILL.md` and `ui-reverse-engineering/SKILL.md` linked to `visual-debug/...` instead of `../visual-debug/...`. Audit found 8 occurrences total. All now resolve correctly.
- **Install-command drift** — `ui-reverse-engineering/SKILL.md` had `brew install agent-browser` but README uses `npm i -g agent-browser`. Aligned to README's npm + brew split form.
- **Unsafe `/tmp` race in pre-generate hook** — `hooks/ui-re-pre-generate-check.sh` wrote to fixed path `/tmp/ui-re-gate-result.txt` (race + symlink risk). Now uses `mktemp -t ui-re-gate-result.XXXXXX` + `trap 'rm -f' EXIT`.
- **`claude-pre-push.sh` silent bypass on `main`-branch repos** — hardcoded `origin/master` silently skipped on repos using other default branches. Now uses three-level upstream detection chain (`@{upstream}` → `origin/<HEAD>` → `origin/master`) with explicit warning instead of silent bypass.
- **Missing strict mode in hooks** — both hook scripts now run with `set -euo pipefail` plus `|| echo "0"` fallbacks for find pipelines on missing dirs.

#### Script hardening
- **`set -e` enabled** on `validate-gate.sh`, `run-pipeline.sh`, `auto-verify.sh`. Required `true` sentinel appended to 13 `|| { ... }` blocks in `run-pipeline.sh` to prevent premature exit when phase already determined.
- **`trap EXIT` cleanup added** to 6 browser scripts: `extract-assets.sh`, `extract-section-html.sh`, `section-clips.sh`, `freeze-animations.sh`, `extract-dynamic-styles.sh` close their `agent-browser` session on exit. `compare-sections.sh` gets `trap '' EXIT` for consistency.
- **Dependency doctor extended** in `run-pipeline.sh` — now also checks `python3`, `curl`, `bc` (used by downstream scripts but previously unchecked).
- **Dead code removed** in `extract-dynamic-styles.sh` — unreachable `else if /^(translate|rotate|scale)$/` branch (already caught by preceding `transform|opacity|visibility|translate|rotate|scale` matcher) deleted.
- **Pipeline safety** — `|| echo "0"` added to `verify_frames`, `px_leaks`, `scroll_listeners` pipelines in `validate-gate.sh`. `|| true` added to all `agent-browser` calls in `auto-verify.sh` screenshot loop.

#### Documentation
- **Terminology clarification** — "Zero vision tokens" now scoped to the comparison phase only (AE/SSIM diff). Phase E (LLM review) is mandatory for full verification and does use vision tokens. Updated in both `visual-debug/SKILL.md` and `README.md`.
- **README quickstart added** — concrete worked example using real script paths and artifact locations. Bridges install → first invocation.
- **Stray non-Latin character fixed** in `CHANGELOG.md:767` (a stray Hangul codepoint was embedded inside the word "not"; restored to `not`).

### Operational
- **Plugin/marketplace version sync** — `plugin.json` (0.2.10) and `marketplace.json` (0.2.9) were out of sync. Both now at `0.3.0`.
- **Version-sync enforcement** in `claude-pre-push.sh` — push is blocked if `plugin.json` and `marketplace.json` versions don't match, preventing future drift.
- **Pre-push security gate** — new `scripts/pre-push-security.sh` runs Snyk/Socket-class checks (secret scan, bash eval, insecure /tmp, reverse-shell patterns, JSON validity, bash syntax, shellcheck errors, cross-ref resolution, version sync) on every push. Blocks push on any blocker. Run manually anytime with `bash scripts/pre-push-security.sh`.

### Notes
Fully backwards-compatible with v0.2.10. No skill triggers, file paths, or invocation patterns changed. Deferred to v0.4.0: token compression of large skill docs, phase naming normalization across skills, CHANGELOG archival.

## [0.2.10] - 2026-04-25

Expanded failure-mode coverage, multi-viewport verification, and new section/transition comparison scripts.

### New features
- **`section-compare.sh`** — new visual-debug script that compares original vs implementation by semantic section. Matches sections by text fingerprint, crops element-level screenshots, runs AE diff per section, and diffs computed styles + DOM structure. Eliminates scroll-alignment noise from full-page comparisons.
- **`transition-compare.sh`** — new visual-debug script that compares hover/transition behavior between original and implementation. Detects elements with CSS transitions, captures idle/hover states, diffs computedStyle changes, and validates transition timing (duration, easing, delay).
- **Multi-viewport audit (Step A3.6)** in `style-audit.md` — runs style audit at 1280px, 1440px, and 1920px minimum. Catches font-size, padding, and grid height differences invisible at single viewport width.
- **Post-implementation height verification (Stage 7)** in `section-audit.md` — re-measures implementation section heights against original after component generation. Catches hardcoded height drift, grid ratio mismatches, and accumulated font metric differences before visual diff. Thresholds: >15% or >80px = warning, >30% = fail.

### Fixed
- **`@theme` Tailwind v4 scoping** — documented silent failure in monorepo/embedded projects where `@theme` in separate CSS files is ignored. Fix: use plain CSS custom properties on `[data-project]` selector instead. Added to `SKILL.md` anti-skip table, `css-first-generation.md`, and `style-audit.md`.
- **Line-height extraction** — Tailwind default `leading-normal` (1.5) applied when explicit `lineHeight` not extracted. Added to `generation-pitfalls.md` failure table and `SKILL.md` anti-skip rules.
- **Color opacity loss** — text colors extracted without alpha channel (`rgb` instead of `rgba`). Subtitles/labels often use 20–40% opacity. Added to `generation-pitfalls.md`.
- **Grid height responsiveness** — fixed `height: Npx` on grid cells breaks at wider viewports. Fix: use `aspectRatio` instead. Added to `generation-pitfalls.md` and `style-audit.md`.
- **Hover-OUT snap** — hover transition works but reverse (mouse-leave) snaps. Cause: missing initial values in idle state CSS. Fix: declare initial values for all animated properties. Added to `generation-pitfalls.md` and `post-gen-verification.md`.
- **Wrapper div recursion** — `section-audit.md` and `layout-health-check.sh` now recurse through single-child wrapper divs (e.g., `main > div.home > sections`) instead of stopping at the first `<main>`. Fixes false section counts on sites like eBay.
- **`layout-health-check.sh` thresholds tightened** — added intermediate `⛔ FAIL` band (1.3x–2x, was only warning). Added absolute pixel diff display. Ratio precision increased to 2 decimal places.

## [0.2.9] - 2026-04-23

Browser session cleanup, token efficiency, shell script hardening, and pipeline status accuracy.

### New features
- **`hooks/hooks.json`** — hooks now auto-register on plugin install. No manual `.claude/settings.json` editing needed. PreToolUse blocks component writes before extraction completes; PostToolUse warns on premature completion signals.
- **Dependency doctor in `run-pipeline.sh`** — blocks pipeline start if required tools are missing. Checks agent-browser, ffmpeg, jq, compare, identify.
- **Auto pre-generate gate** — `run-pipeline.sh` now automatically runs `validate-gate.sh pre-generate` when all Phase 2 artifacts exist, before entering Phase 3. LLM can no longer skip the gate by not calling it.
- **Browser cleanup rule** — all 3 SKILL.md files now require closing own `--session <name>` on exit. `close --all` prohibited (kills other agent-browser sessions' browsers).
- **Token rule** — all 3 SKILL.md files now require piping large `eval` output to files (`> tmp/ref/<name>.json`) instead of stdout. Prevents multi-MB JSON from consuming tokens.
- **`run-pipeline.sh`** — added status checks for Steps 2.5b (`svg-text-elements.json`), 2.6 (`animation-init-styles.json`), 5d-2b (`hover-css-rules.json`), 6c (`component-map.json`). Added Phase 1 checks for `regions.json` and `transitions/ref/`.

### Fixed
- **`run-pipeline.sh`** — removed phantom `animation-spec.json` check (artifact never produced by any step). Fixed Step 5c message pointing to wrong doc (`interaction-detection.md` → `bundle-analysis.md`). Fixed Step 6b/6c ordering to match SKILL.md pipeline.
- **`validate-gate.sh`** — `gate_reference` now checks `regions.json` (was only checking `static/ref/`).
- **`layout-diff.sh`** — race condition: pipe subshell wrote FAIL count to shared `/tmp/layout-diff-fail-count`. Replaced with heredoc (`<<< "$TSV_DATA"`) so FAIL increments in parent process. Added `trap cleanup_browsers EXIT`.
- **`batch-scroll.sh`** — no browser cleanup at all (open without close). Added `trap cleanup_browsers EXIT`. Added numeric validation for page heights (prevents `bc` errors on non-numeric eval output).
- **`auto-verify.sh`** — added `trap cleanup_browsers EXIT` for error/signal cleanup.
- **`transition-compare.sh`** (initial version) — added `trap cleanup_browsers EXIT` for error/signal cleanup. Rewritten in 0.2.10 with idle/hover state capture and computedStyle diffing.
- **`layout-health-check.sh`** — added `trap cleanup_browsers EXIT`. Added `node` dependency check.
- **`computed-diff.sh`** — added `python3` dependency check.
- **`ae-compare.sh`, `batch-compare.sh`, `dssim-compare.sh`** — replaced PID/filename-based temp paths with `mktemp` (prevents collision on parallel runs).

### Removed
- **`hooks/ui-re-start-session.sh`** — marker file (`tmp/.ui-re-active`) no longer needed. Pre-generate hook auto-discovers `tmp/ref/*/` directories. Simplified `ui-re-pre-generate-check.sh` to remove Mode 1 (marker) code.

### Changed
- **Viewport hardcoding removed** — all visual-debug scripts, `auto-verify.sh`, `transition-compare.sh`, and `section-clips.sh` now use `VIEW_W`/`VIEW_H` env vars (default 1440×900). Enables `VIEW_W=375 VIEW_H=667 bash batch-scroll.sh ...` for mobile comparison.

## [0.2.8] - 2026-04-23

Splash-aware extraction, hover completeness enforcement, and anti-skip gates — addresses systemic issues where extraction steps were silently skipped.

### New features
- **`dom-extraction.md` Step 2.5b** — SVG-as-text detection. Finds headings/brand text rendered as SVG `<path>` (not fonts). Saves `svg-text-elements.json`. Gate enforced.
- **`dom-extraction.md` Step 2.6-pre** — Dual-snapshot extraction. Captures DOM state pre-splash AND post-splash, diffs runtime-injected transitions. Splash completion auto-detected via 4 universal signals (full-screen overlay, interactive reachability, scrollability, DOM stability) — no hardcoded wait or class name patterns.
- **`dom-extraction.md` Session management** — Single session reuse rule for splash sites. One `agent-browser` session opened once, reused for all Steps 1–6.
- **`interaction-detection.md` Step 5d-2b** — Extracts ALL `:hover` CSS rules from live page stylesheets (including inline `<style>` tags invisible to CSS file download). Saves `hover-css-rules.json`. Gate enforced.
- **`interaction-detection.md` Step 5d-2c** — Scans for `data-text`/`data-label` attributes that drive text-swap hover effects via `::after { content: attr(data-text) }`.
- **`interaction-detection.md` Step 5d-2d** — Hover video recording for every hoverable element. Captures exact visual effect regardless of DOM inspection limitations.
- **`style-extraction.md` Pre-step** — Merges runtime-injected transitions from `dom-state-diff.json` into `globals.css`. Warns that these transitions are NOT in downloaded CSS files.
- **`animation-detection.md` Phase B** — Scroll method selection table. Defaults to `agent-browser scroll down` (wheel events) for smooth-scroll sites instead of `window.scrollTo`.

### Changed
- **`component-generation.md`** — Rule 13: SVG-as-text must not be recreated with fonts. Rule 14: smooth scroll requires RAF + `getBoundingClientRect()`, not `addEventListener('scroll')`.
- **`css-first-generation.md`** — Step 5: CSS value accuracy verification (diff original vs globals.css). Step 6: Body style scoping for embedded/monorepo projects.
- **`validate-gate.sh` `pre-generate`** — Now checks: `svg-text-elements.json`, `hover-css-rules.json`, hover video existence, `dom-state-diff.json` (if preloader). Blocks on missing artifacts.
- **`validate-gate.sh` `post-implement`** — Now checks: hover rule count (impl >= original), px fontSize leaks (viewport-scaled sites), `addEventListener('scroll')` usage (smooth-scroll sites). Framework-agnostic file detection (tsx/jsx/vue/svelte).
- **`SKILL.md`** — Pipeline table: added Steps 2.5b, 2.6-pre splash auto-detect, 5d-2b/2c/2d. "Steps most likely to be skipped" table (12 items). "No Judgment" table extended (6 new anti-rationalization patterns). Step 9 updated for hover verification.

### Fixed
- Gate scripts use `git rev-parse --show-toplevel` for project root instead of hardcoded relative paths.

## [0.2.7] - 2026-04-23

Viewport-scaled font handling, multi-viewport sizing recovery, and JS hover timing extraction — closes the three biggest sources of "looks different" bugs.

### New features
- **`style-extraction.md`** — ⛔ Viewport-scaled font em-conversion gate. When `scalingSystem !== 'px-fixed'`, extracts full em conversion table (`em-conversion.json`) with every unique font size mapped to `emValue`/`remValue`. Blocks progression without it.
- **`responsive-detection.md`** — Step 4-C2: multi-viewport element sizing comparison at 768/1280/1440. Recovers original CSS expressions (`calc()`, `vw`, `%`, breakpoint-jump) from computed px values → `sizing-expressions.json`. ⛔ Gate: `pre-generate` fails without this file.
- **`interaction-detection.md`** — Step 5d-3: JS-driven hover timing extraction. Detects elements with visual deltas but `transitionDuration: 0s` (GSAP/Framer/vanilla JS). Measures via `getAnimations()` WAAPI; falls back to bundle grep → `hover-timing.json`. ⛔ Gate: `timingSource: unknown` blocks generation.
- **`interaction-detection.md`** — Step 5d-4: hover child cascade detection. Measures all children before/after hover to catch cascading effects (card hover → image scale + overlay fade + title shift). Extends `hover-deltas.json`.
- **`bundle-analysis.md`** — Hover event listener extraction (MANDATORY section). Greps downloaded bundles for `mouseenter`/`pointerenter`/`whileHover` patterns, maps to DOM selectors, extracts animation parameters → `hover-bundle-map.json`. Cross-references with `hover-deltas.json` to flag timing gaps.

### Changed
- **`component-generation.md`** — Rule 3 strengthened: `em-conversion.json` is now a HARD BLOCK; every text element must use `emValue` from the conversion table. Rule 5 rewritten: must use `sizing-expressions.json` instead of manual per-breakpoint comparison. Post-generation px font audit added (grep scan for leaked px font sizes).
- **`validate-gate.sh`** — `gate_extraction`: checks `em-conversion.json` when `typography.json` has viewport-scaled/em-based scaling. `gate_pre_generate`: checks `sizing-expressions.json`, re-checks em-conversion, validates hover timing (flags `timingSource: unknown` count).
- **`SKILL.md`** — Pipeline table Steps 3/4/5 updated with new mandatory outputs and gates. Reference files table updated with new gate descriptions.
- **`.claude-plugin/plugin.json`** — version 0.2.7, description updated
- **`.claude-plugin/marketplace.json`** — version 0.2.7 (was 0.2.5), description updated, 5 keywords added

### New extraction artifacts
| File | Produced by | Used by |
|---|---|---|
| `em-conversion.json` | `style-extraction.md` Step 3 | `component-generation.md` Rule 3 |
| `sizing-expressions.json` | `responsive-detection.md` Step 4-C2 | `component-generation.md` Rule 5 |
| `hover-timing.json` | `interaction-detection.md` Step 5d-3 | `component-generation.md` transitions |
| `hover-bundle-map.json` | `bundle-analysis.md` | `interaction-detection.md` cross-ref |

## [0.2.6] - 2026-04-22

Split oversized files, added hidden element extraction and external SDK reuse pipeline, then absorbed transition-reverse-engineering into ui-reverse-engineering — 4 skills → 3 skills.

### Split: `interaction-detection.md` (1767 → 279 lines)
- **`interaction-detection.md`** — Step 5 only (hover, scroll, click, drag detection)
- **`bundle-analysis.md`** (NEW, 142L) — Step 6: JS bundle download, scroll engine, animation library, preloader detection, external SDK detection
- **`transition-spec-rules.md`** (NEW, 170L) — Spec format, rules, capture verification (Step 5e), external SDK reuse procedure

### Split: `dom-extraction.md` (670 → 341 lines)
- **`dom-extraction.md`** — Steps 1–2 + 2.6 (DOM structure, hidden elements, portals, sticky, section HTML)
- **`asset-extraction.md`** (NEW, 339L) — Step 2.5: CSS files, fonts, images, SVGs, videos, head metadata, CSS variables

### Split: `visual-debug/verification.md` (764 → 465 lines)
- **`verification.md`** — Phase A/B (capture) + Phase D (pixel-perfect gate) + auxiliary checks
- **`comparison-fix.md`** (NEW, 309L) — Phase C: AE/SSIM comparison, computed-style diagnosis, Phase E LLM review, Phase H self-healing

### Skill boundary restructure: detect(ui-reverse-engineering) → extract(transition-reverse-engineering) → absorb

**Merged into transition-reverse-engineering (intermediate step, later absorbed):**
- **`patterns.md`** — Canvas Renderer, Disc/Carousel, Lottie Asset Mapping, State Machine, Auto-Timer detection patterns added as "Detection & Classification Patterns" section (from ui-reverse-engineering/bundle-patterns.md)
- **`css-extraction.md`** — Hover state delta capture added (from ui-reverse-engineering/interaction-detection.md)

**Replaced with forwards in ui-reverse-engineering:**
- **`bundle-analysis.md`** — Framer/GSAP/scroll lib detailed greps replaced with quick-detect + forward to transition-reverse-engineering
- **`interaction-detection.md`** — CSS keyframe eval + hover delta eval replaced with forwards to transition-reverse-engineering/css-extraction.md

**Moved across skills:**
- **`capture-reference.md`** → **`ui-capture/element-capture.md`** — Element-scope capture (hover/scroll/page-load) now lives in ui-capture
- **`verification.md`** (transition-reverse-engineering) → **`visual-debug/comparison-fix.md`** — Element-Scope Verification section added (frame comparison, bug diagnosis protocol, completion checklist)

**Deleted (content merged elsewhere):**
- ui-reverse-engineering/bundle-patterns.md -- merged into ui-reverse-engineering/patterns.md
- transition-reverse-engineering/capture-reference.md -- moved to ui-capture/element-capture.md
- transition-reverse-engineering/verification.md -- merged into visual-debug/comparison-fix.md

**Absorbed transition-reverse-engineering into ui-reverse-engineering (4 to 3 skills):**
- 7 sub-docs (measurement, css-extraction, js-animation-extraction, canvas-webgl-extraction, patterns, waapi-scrubbing, bundle-verification) moved into ui-reverse-engineering
- Transition extraction pipeline added as Step T in ui-reverse-engineering SKILL.md (classification eval, scope, sub-pipeline)
- All invoke transition-reverse-engineering replaced with direct sub-doc references
- transition-reverse-engineering directory deleted
- element-capture.md moved from ui-capture to ui-reverse-engineering (only used by Step T0)
- interaction-detection.md idle+active capture code removed (duplicated ui-capture Phase 2C), replaced with delegation

### New features
- **Hidden element extraction** (`dom-extraction.md`) — Elements with `height:0`, `display:none`, `opacity:0` are force-shown and extracted → `hidden-elements.json`
- **External SDK detection** (`transition-spec-rules.md`) — Auto-detect UnicornStudio, Spline, Rive, Lottie, Three.js → reuse SDK directly instead of CSS replication
- **Splash detection flow** (`bundle-analysis.md`) — Bundle grep + DOM class check at Step 5c, before capture verification
- **Orphan fix** — `dynamic-content-protocol.md` routed from `animation-detection.md`

### Accuracy improvements
- **Responsive value recovery** (`component-generation.md` Rule 5) — Compare per-breakpoint computed styles to recover original CSS expressions (calc, viewport units, responsive prefixes) instead of hardcoding pixel values from a single viewport
- **Project-specific references removed** — All `@beyond/core`, `@beyond/react`, `onpixel` hardcoded references replaced with generic "project animation library or OSS alternative" across 4 sub-docs (transition-implementation, generation-pitfalls, site-detection, component-generation)
- **Evals merged** — 22 transition-reverse-engineering evals + 25 trigger-evals merged into ui-reverse-engineering (total: 57 evals, 58 trigger-evals)

### Fixes
- **`validate-gate.sh`** — `gate_spec`: fixed `jq has()` multi-line output bug. Added `verify/` frame count check
- **Back-references clarified** — Sub-docs no longer reference calling docs ambiguously (prevents circular confusion)
- **R&R dedup** — interaction-detection.md idle+active capture code removed (duplicated ui-capture Phase 2C)

### Updated
- **All 3 SKILL.md files** -- Pipeline tables + reference files tables updated for all changes
- **animation-detection.md** -- Added routing to dynamic-content-protocol.md
- All cross-references updated (ui-capture, visual-debug, evals.json)

### Audit results
- 3 skills, 35 files total (27 + 5 + 3), 0 broken references, 0 orphans
- ui-reverse-engineering: 26 sub-docs (57 evals, 58 trigger-evals), ui-capture: 4 sub-docs, visual-debug: 2 sub-docs
- No project-specific hardcoded references remain

## [0.2.5] - 2026-04-21

SKILL.md token optimization — 43% reduction (11,836 → 6,780 tokens) across all 4 skills with zero functional regression.

### Changed
- **`ui-reverse-engineering/SKILL.md`** — 259 → 187 lines (-28%). "No Judgment" table: 13 → 8 rows (kept highest-impact anti-patterns, dropped rows covered by execution rules or sub-docs). Execution rules restructured from numbered list to 3 categories (extraction/implementation/verification). Security section inlined to 1 sentence. `agent-browser` cheatsheet removed (available in sub-docs). Output schema shortened. Input modes table consolidated.
- **`transition-reverse-engineering/SKILL.md`** — 174 → 132 lines (-24%). Security section inlined. `agent-browser` cheatsheet removed. Step 0 detail section removed (covered by pipeline table + sub-docs). Troubleshooting rows for `onfinish` callbacks and CSS class rules removed (handled by sub-docs). Ralph worker rules consolidated.
- **`ui-capture/SKILL.md`** — 200 → 137 lines (-32%). Phase R inline description removed (sub-doc `report-page.md` is authoritative). Phase 1 setup/video instructions compressed. Troubleshooting table reduced to top issues. Phases R/3/4/5 consolidated.
- **`visual-debug/SKILL.md`** — 166 → 104 lines (-37%). Anti-patterns table removed (replaced by hard rule). Script path resolution shortened. Example section trimmed. Phase E description compressed.

### Preserved (verified by audit)
- All sub-doc file references and step numbers unchanged
- All gate names (`bundle`, `spec`, `pre-generate`, `post-implement`) unchanged
- All artifact file names and directory paths unchanged
- All script names unchanged
- `waapi-scrubbing.md` reference restored after initial removal flagged by audit
- Phase 2C "No video" constraint restored
- Phase 3 identity constraint (same speeds/wait times/hover durations) restored
- Phase 5 autonomous retry/escalation protocol (≤3 retries → escalate) restored
- Rule 12d drag handler constraint restored
- GSAP Premium alternative mappings (SplitText→splitting, MorphSVG→flubber, etc.) restored inline
- `splash-extraction.md` trigger condition ("Tier 1 AE shows changes in first 1–3s") restored

## [0.2.4] - 2026-04-20

Hook hardening: result-aware verification, multi-state extraction checks, and session marker for early pipeline enforcement.

### Added
- **`hooks/ui-re-start-session.sh`** — Creates `tmp/.ui-re-active` marker file at pipeline start. Pre-generate hook reads this marker to block component writes before extraction completes, even when no `tmp/ref/` directory exists yet.
- **`hooks/ui-re-post-verify-check.sh`** — **Check 2: PASS/FAIL result inspection.** Reads `batch-compare-result.txt` and counts `❌`/`✅` markers. Previously only checked whether verification had been *run*; now also checks whether it *passed*. Warns with fail/pass counts and points to diff images.
- **`hooks/ui-re-post-verify-check.sh`** — **Check 3: Multi-state verification.** When `interactions-detected.json` contains click interactions, checks for alternate-state captures (search, active, result, click). Warns if state-changing interactions exist but no alternate view was verified.
- **`hooks/ui-re-pre-generate-check.sh`** — **Multi-state extraction check.** When click interactions exist, checks for per-state extraction files (`styles-*.json`, `structure-*.json`) or `transition-spec.json` state documentation. Advisory warning (non-blocking) to avoid breaking existing workflows.

### Changed
- **README.md** — Installation: `npx skills install` → `npx skills add` (correct CLI command). Removed non-existent `/plugin marketplace add` and `/plugin install` methods.
- **README.md** — Requirements: `agent-browser` changed from `brew install` to `npm i -g` for cross-platform support. Added `magick --version` and `ffmpeg -version` to verification commands.
- **README.md** — Hooks section rewritten: added `settings.json` config example with `<PLUGIN_PATH>` placeholder, `start-session.sh` manual invocation, and skip-condition explanation. Replaced single-hook paragraph with 3-hook table.
- **README.md** — Automation scripts table split into `scripts/` and `skills/visual-debug/scripts/` to reflect actual file locations.
- **README.md** — Removed duplicate "progressive-disclosure sub-docs" paragraph (already in Design principles).
- **`hooks/ui-re-pre-generate-check.sh`** — **Component-only enforcement.** Now checks `file_path` from tool input and only enforces pipeline on `*/src/components/*`, `*/src/app/*/page.*`, and `*/src/projects/*/components/*`. Non-component files pass freely.
- **`hooks/ui-re-pre-generate-check.sh`** — **Active session marker (Mode 1).** Reads `tmp/.ui-re-active` to detect pipeline-in-progress state before any `tmp/ref/` directory exists. Denies component writes with actionable error message.
- **`hooks/ui-re-pre-generate-check.sh`** — Tool input now read from `$1` or stdin fallback (`${1:-$(cat 2>/dev/null)}`), fixing cases where the argument wasn't passed.
- **`hooks/ui-re-pre-generate-check.sh`** — Deny message now includes missing artifact count for quicker triage.
- **`hooks/ui-re-post-verify-check.sh`** — Completion-signal pattern expanded: now also matches `"looks good"` and `"all pass"`.
- **`hooks/ui-re-post-verify-check.sh`** — Early exit when command is not a completion signal, avoiding unnecessary file checks on every Bash invocation.

## [0.2.3] - 2026-04-19

GSAP-baked style handling, state coupling verification, bundle analysis patterns, and pipeline/script hardening.

### Added
- **`dom-extraction.md`** — **Step 2.6a: GSAP-Baked Inline Style Catalog.** Scraped HTML contains `visibility:hidden`, `opacity:0`, `transform:translate(-500px)` baked by GSAP at scrape time. These are animation init states, not desired defaults — they make elements invisible. New eval script scans all elements and saves to `animation-init-styles.json`. Each must be explicitly reset during implementation.
- **`dom-extraction.md`** — **Step 2.6b: State-Coupled Element Mapping.** For carousels/tabs/accordions: identifies ALL elements that change when shared state changes (bg color, card text, illustration, section bg). Saves coupling table to `state-coupling.json`. Missing couplings = elements that stay stale when they should update.
- **`dom-extraction.md`** — **CSS `background-image` collection** in visible-images step. Previous version only captured `<img>` tags; sites using CSS `background-image` for hero/section backgrounds were missed entirely. New pass checks `getComputedStyle(el).backgroundImage` on all elements with visible dimensions.
- **`interaction-detection.md`** — **Step 5e: Drag/Swipe Effect Classification.** Three effect types (state-flip, transform-tracking, parallax-tracking) with detection methods and implementation rules. Critical rule: if drag triggers state change (carousel rotation), handler must ONLY detect direction and trigger `goTo()` — never apply `translateX` to illustration.
- **`interaction-detection.md`** — **Bundle Analysis Patterns** reference section (488 lines). Five pattern guides with DOM inspection commands, bundle grep strategies, verification steps, and common traps:
  1. Canvas Renderer Detection — size comparison, renderer type check, paint-over verification
  2. Disc/Carousel Structure Detection — angle delta calculation, transform-origin confirmation, translate trap warning
  3. Lottie Asset Mapping — fetch intercept, JSON layer name extraction, multi-file composition (pants/nopants pattern)
  4. State Machine Extraction — MutationObserver, switch/case grep, boolean-collapse trap
  5. Auto-Timer Extraction — setInterval intercept, GSAP repeat grep, splash-gate/scroll-gate/page-visibility detection
- **`post-gen-verification.md`** — **Loop 0.5: State Coupling Verification.** For carousels/tabs: verify ALL coupled elements update when shared state changes. Includes splash/auto-timer conflict detection (recording first 8s to check if carousel rotates during splash).
- **`component-generation.md`** — Rules 9–11 added: auto-timers must respect splash phase (delay start by splash duration + 1s), GSAP-baked inline styles must be explicitly reset, DOM structure must be verified via `agent-browser eval` before implementing interactions.
- **`SKILL.md`** — 7 new entries in "No Judgment" table: CSS color swap assumption, Canvas size trap, GSAP-baked style recognition, Lottie asset replacement, auto-rotate timing, visual verification requirement, DOM structure assumption.
- **`SKILL.md`** — Execution rules 10b (GSAP-baked style catalog), 10c (auto-play timer classification), 12b (DOM structure verification), 12c (SVG replacement verification), 12d (drag handler = swipe only), 13b (splash timing), 16b (state coupling verification), 16c (browser-first verification).
- **`SKILL.md`** — Pipeline table: Step 2.6 added (animation-init-styles.json, state-coupling.json).

### Changed
- **`interaction-detection.md`** — `scroll-engine.json` now ALWAYS created, even for native scroll sites (`{"type":"native"}`). Previously only created when custom scroll was detected, causing pipeline gate failure on native-scroll sites.
- **`interaction-detection.md`** — `element-animation-map.json` relationship to `transition-spec.json` clarified: supplement (selector mapping), not replacement. `transition-spec.json` remains single source of truth; conflicts resolved in its favor.
- **`interaction-detection.md`** — Phase A cross-reference in Step 6 bundle analysis now has ordering note: defer to after animation-detection.md if Phase A hasn't run yet.
- **`component-generation.md`** — Input checklist: `interaction-states.json` removed (never produced by any step), `fonts.json` added, `animation-init-styles.json` and `state-coupling.json` added from Step 2.6.
- **`SKILL.md`** — Audit stage (e): `interaction-states.json` removed from required artifacts (dead reference).
- **`SKILL.md`** — Execution rules: GSAP-baked style warning deduplicated (single reference to dom-extraction.md Step 2.6a instead of inline repetition).

### Fixed
- **`auto-verify.sh` — wrong `VISUAL_DEBUG_SCRIPTS` path.** `$(dirname "$SCRIPT_DIR")/../visual-debug/scripts` resolved 2 levels above the project root. Now searches sibling skill dir, installed skills, then fallback `find`. Exits with error message if not found.
- **`auto-verify.sh` / `run-pipeline.sh` — `eval` removed.** Shell-string `eval "$cmd"` replaced with direct `"$@"` execution and helper functions (`has_file`, `has_files`). Paths with spaces or special characters no longer break.
- **`validate-gate.sh` — `transition-spec.json` structure validation restored.** `gate_spec()` only checked file existence; empty `{}` would pass. Now validates `.transitions` array length and required keys (`id`, `trigger`, `bundle_branch`) when jq is available.
- **`batch-compare.sh` / `dssim-compare.sh` / `ae-compare.sh` — temp file cleanup.** Resized images in `/tmp/` were never deleted. Added `trap cleanup EXIT`.
- **`batch-compare.sh` / `ae-compare.sh` — ImageMagick dependency check.** `dssim-compare.sh` checked for `dssim` but the other scripts silently failed without `compare`/`identify`. Now exit with install instructions.
- **`layout-diff.sh` — N+1 jq invocations.** Called jq 6 times per loop iteration. Replaced with single `jq` call producing TSV, parsed via `while read`. Added jq dependency check.
- **Duplicate scripts in `skills/ui-reverse-engineering/scripts/`.** Identical copies of `auto-verify.sh`, `run-pipeline.sh`, `validate-gate.sh` diverged from root `scripts/`. Replaced with symlinks.
- **Hooks hardcoded `~/Documents/ui-skills` path.** `ui-re-pre-generate-check.sh` and `ui-re-post-verify-check.sh` now use `CLAUDE_PLUGIN_ROOT` when available, with `-maxdepth` on `find` fallback.
- **`interaction-states.json` dead reference** — referenced as BLOCKING input in component-generation.md and SKILL.md audit stage, but never produced by any extraction step. Removed from all references.
- **`scroll-engine.json` native scroll gap** — not created for native-scroll sites, causing pipeline gate failure. Now always created.
- **Step 2.5a/2.5b ordering** — Steps labeled as sub-steps of 2.5 but appeared before 2.5 in the file. Renumbered to 2.6a/2.6b and moved after Step 2.5.
- **`style-audit.md`** — `interaction-states.json` reference updated to `interactions-detected.json` (the file that actually exists).
- **`css-variables.json` vs `variables.txt` name mismatch** — validate-gate.sh checked `css-variables.json` but dom-extraction.md produces `css/variables.txt`. Gate updated. SKILL.md pipeline table updated.
- **`run-pipeline.sh` hardcoded `apps/maximatherapy`** — replaced with generic `apps/*/src/components` glob.
### Removed
- **`batch-compare.sh.bak`** — stale backup file removed from untracked files.
- **Near-duplicate rule explanations in `component-generation.md`** — "Never guess UI layout" (33 words → 14-word cross-ref to SKILL.md rule 12), GSAP-baked style explanation (45 words → 11-word cross-ref to dom-extraction.md Step 2.6a), splash timing explanation (46 words → 11-word cross-ref to SKILL.md rule 13b), dropdown/overlay warning (38 words → 14-word cross-ref). ~160 words / ~700 tokens saved per invocation.
- **Rationalization list in `interaction-detection.md`** — 10-item bullet list duplicating SKILL.md "No Judgment" table. Replaced with one-line cross-reference.
- **"visual-debug verification Phase D" verbose naming in `style-audit.md`** — 5 occurrences standardized to "Phase D".

## [0.2.2] - 2026-04-17

Automated verification pipeline, bundle-based verification for untriggerable animations, and anti-rationalization enforcement across all skills.

### Added
- **`scripts/auto-verify.sh`** — Single-command verification pipeline. Runs D0 (layout-health-check), Phase C (batch-scroll + AE comparison), and post-implement gate sequentially. Replaces manual multi-step verification. `exit 0` = done, `exit 1` = not done.
- **`skills/visual-debug/scripts/layout-health-check.sh`** — Phase D0: layout structure comparison (section heights, total height ratio) before pixel-level diff. Catches structural mismatches (collapsed sections, missing padding) in 2 seconds that would otherwise produce noise in every Phase D position.
- **`skills/transition-reverse-engineering/bundle-verification.md`** — Numerical verification for untriggerable animations (carousel, auto-rotate, page-load). Extracts parameters from JS bundles, diffs against implementation code, produces `bundle-verification.json`. Replaces frame comparison for animations where T=0 synchronization is impossible.
- **`hooks/ui-re-post-verify-check.sh`** — Post-verify hook for enforcement after verification step.
- **`skills/ui-reverse-engineering/SKILL.md`** — "No Judgment — Data Only" section. Table of 9 judgment traps with required actions (e.g., "This looks close enough" → run `auto-verify.sh`). Exists because the LLM consistently guesses instead of measuring.
- **`skills/ui-reverse-engineering/transition-implementation.md`** — "GSAP Premium Plugin Alternatives" section. SplitText → `@beyond/core splitText` or `splitting` npm package. MorphSVG → `flubber` or SVG `rx`/`ry` animation. ScrollSmoother → project library or `lenis`. DrawSVG → CSS `stroke-dashoffset`. Prevents skipping features because a library is paid.
- **`skills/transition-reverse-engineering/js-animation-extraction.md`** — Auto-rotation/carousel detection section. Detection patterns (setInterval, GSAP repeat:-1, classList carousel), parameter extraction table, freezing script for resting-state screenshot.
- **`skills/visual-debug/scripts/batch-compare.sh`** — Anti-rationalization enforcement block on FAIL. Prints mandatory diagnosis steps and forbids proceeding without documented root causes.
- **`skills/visual-debug/scripts/batch-scroll.sh`** — Height ratio check with warning when impl is >1.3x or <0.7x of ref height.

### Changed
- **`skills/visual-debug/scripts/batch-scroll.sh`** — Rewritten to interleaved capture (ref 0% → impl 0% → ref 10% → impl 10% → ...). Eliminates carousel/animation drift between the two sides. Opens both sites in parallel sessions. Adds smart carousel freeze (monkey-patches setInterval ≥2s, pauses GSAP repeat:-1 timelines, freezes classList mutations and inline styles on carousel elements).
- **`skills/visual-debug/verification.md`** — Phase D0 (Layout Health Check) added as mandatory step before Phase D. Anti-pattern phrases list expanded ("close enough", "just a content difference", "remaining differences are minor" all forbidden). Verification now requires `auto-verify.sh exit 0` instead of manual steps.
- **`skills/ui-reverse-engineering/SKILL.md`** — Step 8 (Verify) rewritten: single `auto-verify.sh` command replaces selective individual checks. Phase D still runs separately after auto-verify passes. Execution rules renumbered with anti-rationalization rules added.
- **`skills/ui-reverse-engineering/component-generation.md`** — HARD BLOCK on interaction captures added. Rule 7 (never guess UI layout) and Rule 8 (never skip paid library features) added. Input checklist includes idle+active screenshots from Step 5b/A-C3.
- **`skills/ui-reverse-engineering/interaction-detection.md`** — Mandatory idle+active state capture section added for every hover/click interaction. Gate: `validate-gate.sh pre-generate` checks captures exist. Easing conversion table replaced with pointer to `transition-implementation.md`.
- **`skills/transition-reverse-engineering/SKILL.md`** — Step 4 verification split by animation type: triggerable (frame comparison + Phase D) vs untriggerable (bundle-verification.md). Gate conditions updated for both paths.
- **`skills/transition-reverse-engineering/verification.md`** — Bundle-Based Verification section added for untriggerable animations. "Is This Done?" checklist split into triggerable and untriggerable paths.
- **`hooks/ui-re-pre-generate-check.sh`** — Picks most recently modified ref dir (not first found). Searches multiple marker files (regions.json, structure.json, etc.). Checks for actual missing artifacts (❌ lines) instead of relying on exit code. Fallback to source repo for `validate-gate.sh`.
- **`scripts/run-pipeline.sh`** — Step 5-verify now prints `auto-verify.sh` command instead of manual steps.
- **`scripts/validate-gate.sh`** — `pre-generate` gate: interaction state capture check added (idle+active screenshots for each hover/click interaction). `post-implement` gate: mandatory artifact checks (layout-health.json, style-audit-diff.json, pixel-perfect-diff.json) added before clip comparison.
- **`plugin.json`, `marketplace.json`** — version bumped to 0.2.2; keywords updated with `auto-verify`, `anti-rationalization`, `bundle-verification`, `layout-health-check`.

## [0.2.1] - 2026-04-16

Docs restructuring and metadata cleanup. No runtime behavior changes — all pipelines, gates, and generation paths work identically.

### Changed

- **SKILL.md slimming (all 4 skills).** Converted verbose ASCII pipeline diagrams to compact tables. Consolidated duplicated Reference Files / Pipeline descriptions into a single location. Moved repeated `$PLUGIN_ROOT` setup blocks to a single "First action" section. Merged redundant `agent-browser` examples into a cheatsheet.
- **Progressive-disclosure sub-docs.** Split large sub-docs so SKILL.md + the common path stay lean and specialized procedures load on demand:
  - `component-generation.md` split into `css-first-generation.md` (Steps 1–4 + fallback prompt), `generation-pitfalls.md` (CSS-to-React translation errors + 20-row failure-diagnosis table), `post-gen-verification.md` (Loop 0/1/2/3 + library wiring patterns)
  - `animation-detection.md` split — splash-specific logic (throttled capture, video↔bundle cross-reference, GSAP timeline parsing, conditional branches, overlay cleanup, end-state verification) moved to `splash-extraction.md`, read only when Tier 1 AE diff detects early motion
  - `transition-reverse-engineering/SKILL.md` Step 0 single-element capture procedure (100+ lines of eval patterns) moved to `capture-reference.md`
  - `ui-capture/SKILL.md` scroll-type/section detection eval moved to `detection.md` Step 2.0
- **Phase naming unified to `D1`/`D2`.** All references to "Phase 1 Visual Gate" / "Phase 2 Numerical Diagnosis" now use the Phase D1/D2 naming consistent with `visual-debug/verification.md`. References to the removed `pixel-perfect-diff.md P1–P6` procedure updated to `visual-debug/verification.md Phase D`. Applied across SKILL.md files and eval JSON.
- **`transition-reverse-engineering/verification.md` Pixel-Perfect section deduplicated.** Previously duplicated Phase D procedure (40 lines of eval + compare commands). Now reduced to a pointer: read `visual-debug/verification.md` Phase D, with only the triggerType → states mapping and gate condition retained locally. Phase D is now documented in exactly one place.
- **`visual-debug` script path variable renamed `PLUGIN_ROOT` → `SCRIPTS_DIR`.** Prevents collision with `ui-reverse-engineering`'s `PLUGIN_ROOT` (which means the repo root). Fallback logic also handles the case where `CLAUDE_PLUGIN_ROOT` is set by the plugin host.
- **`plugin.json` / `marketplace.json` description rewritten.** Version-specific feature dumps replaced with three core benefits (CSS-First, zero-vision-token verification, real JS bundle extraction) and a mention of the progressive-disclosure sub-doc structure.
- **Marketplace keywords trimmed from 106 to 18.** Removed highly-specific terms that duplicated others. Added `zero-vision-tokens`, `progressive-disclosure`, `css-first`.
- **README.md.** Added "Design principles" section (real values / zero vision tokens / progressive disclosure / single source of truth / automation over introspection). Removed version-history prose (was duplicating CHANGELOG). Fixed the "same license as anthropics/skills" claim — that repo has no license; this project's Apache-2.0 stands alone. Added an Optional pre-generate hook installation note.

### Fixed

- **Broken references to removed stub files.** `skills/pixel-perfect-diff.md` and `skills/ui-reverse-engineering/visual-verification.md` were redirect stubs pointing at `visual-debug/verification.md`; removed both and updated every reference (SKILL.md files, eval JSON) to point directly at the target.
- **`scripts/` directory duplicated.** Eleven scripts existed byte-identically in both `/scripts/` and `/skills/ui-reverse-engineering/scripts/`. Kept only `/scripts/` (matches every SKILL.md's `$PLUGIN_ROOT/scripts/...` reference) and removed the skill-local copy. Eliminates non-deterministic `PLUGIN_ROOT` resolution.
- **`skills/visual-debug/scripts/batch-scroll.sh` hint message.** Final line printed `bash scripts/batch-compare.sh $DIR` (relative path that breaks depending on where the user runs from). Now prints `bash "$(dirname "$0")/batch-compare.sh" $DIR`.
- **CHANGELOG language consistency.** The [0.1.1] section was written in Korean while the rest of the file was English; translated for consistency.

### Removed

- `skills/pixel-perfect-diff.md` (redirect stub, content already absorbed into `visual-debug/verification.md`)
- `skills/ui-reverse-engineering/visual-verification.md` (redirect stub, same)
- `skills/ui-reverse-engineering/scripts/` (duplicate of root `/scripts/`)
- `ui-skills-workspace/` local workspace artifacts (listed in `.gitignore`; were never intended to be in the repo)

## [0.2.0] - 2026-04-14

### Added
- **`visual-debug` skill** — Automated visual comparison between original site and implementation using AE/SSIM diff. **Zero vision tokens** — never reads images with LLM for comparison. Only reads diff images when AE reports a FAIL. Includes 4 scripts:
  - `batch-scroll.sh` — captures both original and implementation at identical scroll positions (0%-100%)
  - `ae-compare.sh` — compares two images, outputs AE score + identifies worst region (top/middle/bottom)
  - `batch-compare.sh` — compares all captured pairs, outputs markdown table of scores
  - `computed-diff.sh` — compares `getComputedStyle` values between original and implementation for specified selectors
- **Raw HTML Injection approach** (`site-detection.md`) — New implementation strategy for complex sites with CSS Modules, GSAP, Lottie, Canvas, or 200KB+ HTML. Extracts raw `outerHTML` per section, serves original CSS files from `/public/css/`, renders via `dangerouslySetInnerHTML`. Documents the critical "wrapper div problem" (extra `<div>` between parent and child breaks CSS Module selectors) and the "GSAP inline style cleanup" problem (layout values like `height: 500svh` must be preserved while animation values like `transform: rotateY(-180deg)` must be removed).
- **`extract-dynamic-styles.sh`** — Classifies inline styles as layout (height/width in svh/vh — KEEP) vs animation (transform/opacity/visibility — REMOVE). Prevents the #1 debugging issue: cleaning all GSAP inline styles removes layout heights, causing sections to collapse to 0px.
- **`validate-gate.sh` `dynamic-heights` gate** — Detects when scroll sentinels or sections have lost their `svh`/`vh` height values after GSAP cleanup. Warns about layout values that must be re-set by ClientShell JS.
- **`visual-debug` trigger evals** — 15 test cases (10 positive, 5 negative) for skill activation.
- **Parallel worktree builders** — Phase 3 generation splits into 3A (foundation, sequential) → 3B (section builders, parallel via Agent tool with worktree isolation) → 3C (assembly, sequential). 2-3x faster on pages with 4+ sections. Falls back to sequential if Agent tool unavailable.
- **Self-healing error loop (Phase H)** — When Phase D verification fails, defects are automatically classified by category (LAYOUT/COLOR/TYPOGRAPHY/ANIMATION/CONTENT) and severity (CRITICAL/MAJOR/MINOR), then fixed in priority order with minimal targeted edits. Re-verifies after each iteration. Max 3 cycles before escalating with structured defect report.
- **Click sweep** — `ui-capture` Phase 2 now detects and captures click-toggle (accordions, dropdowns, toggles) and click-cycle (tabs, pills) interactions. Detects via `aria-expanded`, `role="tab"`, `data-state`, `<details>`. Captures idle/active per toggle, per-state screenshots for tab cycles. Deduplicates against existing hover candidates.
- **`interaction-detection.md`** — `click-toggle` and `click-cycle` trigger types added to signal table.
- **`transition-implementation.md`** — React implementation patterns for click-toggle (useState + CSS transition) and click-cycle (activeIndex + tabpanel) with exact extracted values.
- **`visual-verification.md`** — Phase D comparison now includes click-toggle (idle/active) and click-cycle (state-0..N) states.

### Changed
- **`visual-debug` absorbs `visual-verification.md` + `pixel-perfect-diff.md`** — All visual verification is now in one place. `visual-debug/verification.md` contains the full Phase A/B/C/D/H/E procedure (formerly `ui-reverse-engineering/visual-verification.md`). `pixel-perfect-diff.md` and `visual-verification.md` are now redirect stubs. All cross-skill references updated.
- **`site-detection.md`** — Implementation Approach Gate added (MANDATORY before writing code). Detection script checks CSS Module ratio, JS animation library count, inline style count, and total HTML size. Decision matrix routes to Raw HTML Injection or React Component approach.
- **`plugin.json`** — `visual-debug` added to skills list.
- **Scripts JSON output** — `compare-sections.sh`, `validate-gate.sh`, `download-chunks.sh`, `section-clips.sh`, `extract-section-html.sh`, `extract-assets.sh` now output structured JSON: `{status, phase, data, defects, errors, duration_ms}`. Human-readable output moved to stderr. Exit codes unchanged — fully backward-compatible with existing SKILL.md flows.
- **`compare-sections.sh`** — Layer 3 style mismatches now include `category` (LAYOUT/COLOR/TYPOGRAPHY/ANIMATION/CONTENT) and `severity` (CRITICAL/MAJOR/MINOR) classification. Defect list written to `comparison-output.json` for self-healing loop consumption.
- **`validate-gate.sh`** — JSON output includes gate name, failed check count, and missing file list. New `dynamic-heights` gate added.

### Fixed
- `extract-dynamic-styles.sh` missing from `skills/ui-reverse-engineering/scripts/` (only existed in root `scripts/`)
- `validate-gate.sh` out of sync between root `scripts/` and `skills/ui-reverse-engineering/scripts/`
- `visual-debug` SKILL.md script paths used bare `scripts/` without `PLUGIN_ROOT` resolution
- `site-detection.md` referenced `extract-dynamic-styles.sh` without `PLUGIN_ROOT` path
- Visual verification scattered across 3 locations (visual-verification.md, pixel-perfect-diff.md, visual-debug) — now consolidated into `visual-debug`

## [0.1.1] - 2026-04-13

### Added
- **`style-extraction.md`** — **Mandatory section height/gap extraction rule.** Page-level layout (per-section heights, flex/grid gap, padding) must be extracted. Added after a real session where a flex container's `gap: 234px` was missed, making the implementation 957px shorter overall.
- **`visual-verification.md`** — **Mandatory section alignment comparison rule.** Compare per-section top offsets between original and implementation; flag as a spacing bug when the difference exceeds 50px. Prevents the case where different content is visible at the same scroll position.
- **`visual-verification.md`** — **Mandatory original SVG/asset extraction rule.** Forbid generating placeholder SVGs; extract the original SVG `outerHTML` directly from the DOM. Based on a case where the footer logo (460×171 viewBox) was replaced with a placeholder ellipse and required 3 rounds of corrections.
- **`visual-verification.md`** — **Tailwind arbitrary value compatibility check.** If arbitrary values like `px-[19px]` render as `0px`, fall back to inline styles. Based on a case where Tailwind v4 ignored them and the entire padding collapsed to 0.
- **`interaction-detection.md`** — **Mandatory preloader/splash JS bundle analysis rule.** Do not implement preloaders from DOM structure alone; download the custom JS file and extract the GSAP timeline, CustomEase, dedicated images, and sessionStorage gating. Based on a case where the DOM's `display:none` state led to a full-screen image blur implementation when the original was actually a 209×261px centered box + blue (#050fff) clip-path + 8 dedicated images.
- **`component-generation.md`** — **Font size accuracy rule (#1 user feedback).** Use extracted computed values as-is; rounding/approximation forbidden. Based on a case where 40px was implemented as 18px and required repeated fixes.

### Fixed
- Scroll positions not aligning because inter-section gaps were not extracted
- Assets replaced with placeholder SVGs, diverging from the original
- Missed diagnosis of arbitrary px values being ignored in Tailwind v4
- Preloader animations guessed from DOM alone, diverging from the original
- Font sizes set to approximate values, requiring repeated fixes

## [0.1.0] - 2026-04-12

### Breaking Changes
- **CSS-First generation is now the default strategy.** Instead of extracting computed values and re-implementing with Tailwind/inline styles, the skill now downloads original CSS files and uses original class names in JSX. This produces pixel-perfect results but requires readable CSS class names (Shopify, WordPress, static sites). For obfuscated CSS (Tailwind, CSS-in-JS), falls back to the extract-values strategy.

### Added
- **`site-detection.md`** — Auto-detects site tech stack (Shopify/WordPress/Next.js/Tailwind/CSS-in-JS) at Step 1 and selects CSS-First or Extract-Values strategy. Prevents applying the wrong extraction approach.
- **`transition-implementation.md`** — Complete bundle → code translation guide. ScrollTrigger progress formulas, easing conversion table (power1-5 → cubic-bezier), splash/intro animation timing pattern (handles cached vs uncached video), sticky + overflow conflict pre-check, performance patterns (refs vs useState, will-change, passive listeners).
- **`run-pipeline.sh`** — State machine orchestrator. Detects current phase by checking which artifacts exist in `tmp/ref/`, prints exactly what to do next. Prevents skipping steps or guessing which phase you're in. Phases: 0-init → 1-capture → 2-extract → 2.5-css → 2.6-vars → 3-pregen → 4-generate → 5-verify.
- **`extract-assets.sh`** — Downloads video backgrounds, Typekit/Adobe Fonts, and CDN font files. Extracts static video frame as poster fallback. Solves "implementation uses static image but original has video background" mismatch.
- **`extract-section-html.sh`** — Per-section HTML structure + computed CSS + media element extraction. Produces the ground truth for code generation: element hierarchy, computed styles, video/img attributes.
- **`dom-extraction.md`** — Step 2.5: Download original CSS files (MANDATORY). Step 2.6: Extract and preserve CSS variables to `variables.txt` before `:root` cleanup. Download video backgrounds with `<video>` attribute detection. Download Typekit/Adobe Fonts via CSS URL extraction.
- **`component-generation.md`** — CSS-First Generation section: download CSS → import in project → use original class names in JSX. Original CSS + React structure conflict resolution (height override, transform conflicts, z-index stacking). Auto-detect missing assets (grep `url()` in CSS, verify local existence). CSS variable consistency rule (match computed values, not just defined values).
- **`visual-verification.md`** — Content-anchored screenshot alignment (use text anchors, not y-coordinates). ScrollTrigger progress-based comparison for pinned sections. Anti-pattern rule: "looks close enough" phrases banned, `getComputedStyle` numerical comparison required.
- **`validate-gate.sh`** — `pre-generate` gate: verifies original CSS files exist, CSS variables extracted to `variables.txt`, background image assets downloaded. `post-implement` gate: transition coverage checklist from `transition-spec.json`. Section-clip SSIM comparison with Layer 3 `getComputedStyle` diff.
- **`compare-sections.sh`** — Layer 3: `getComputedStyle` numerical comparison. Reads `clips/ref/styles.json` and `clips/impl/styles.json`, outputs per-property mismatches with exact selector + property + ref/impl values. Tells you exactly what CSS property to fix instead of showing a vague diff image.
- **`section-clips.sh`** — Per-section + per-element screenshot capture for targeted comparison.

### Changed
- **`SKILL.md`** — Process flow: `run-pipeline.sh status` is now the FIRST action before any work. Step 7 reads `site-detection.md` first, then `component-generation.md` + `transition-implementation.md`. Reference Files section updated with new documents.
- **`component-generation.md`** — "Transitions are NOT separate from generation" (HARD RULE). Transition coverage gate moved here from post-verification. Section HTML + ref screenshot must be Read before writing each component.
- **`dom-extraction.md`** — Step 2.6 added (per-section HTML structure extraction via `extract-section-html.sh`).

### Fixed
- CSS `:root` variables lost when cleaning downloaded CSS — now extracted to `variables.txt` before cleanup
- `overflow: hidden` silently breaking `position: sticky` — now detected as pre-implementation check
- Agent declaring "almost matches" without numerical verification — banned phrases + mandatory `getComputedStyle` comparison
- Background images not downloaded for showcase/product sections — auto-detected via `url()` grep in original CSS
- Splash animation expanding too fast when video is cached — reliable timing pattern with minimum 1s visibility

## [0.0.18] - 2026-04-11

### Added
- **`ui-reverse-engineering`**: `style-extraction.md` — **Global overlay scan** section. Detects full-page texture overlays (`position: fixed; pointer-events: none; z-index > 100`) such as film grain, noise patterns, and paper textures. Extracts `background-image`, `background-size`, `mix-blend-mode`, and `opacity`. These overlays are easy to miss during extraction but produce a noticeably "too clean" implementation when omitted.
- **`ui-reverse-engineering`**: `component-generation.md` — **"Do not invent interactions"** reminder. Explicitly prohibits adding hover transforms, opacity transitions, or other effects that were not observed in the reference extraction. Extends the existing "no guessing values" rule to cover interaction behavior.
- **`ui-reverse-engineering`**: `dom-extraction.md` — **Font download** section in Step 2.5. Extracts all `@font-face` rules from stylesheets, downloads woff2 files, and saves `fonts.json`. Missing fonts cause fallback to system fonts with different glyph metrics, producing cascading layout differences (wrong text width → wrong wrapping → wrong element positions) that are impossible to fix with CSS alone.
- **`ui-reverse-engineering`**: `SKILL.md` — Step 2.5 checkpoint updated to require `fonts.json` and downloaded font files.
- **`ui-reverse-engineering`**: `dom-extraction.md` — **Generation rules for downloaded assets.** Explicit instructions for applying favicon (add `<link rel="icon">` in HTML head) and images (copy to public directory). Previously, assets were downloaded but no rule specified how to wire them into the implementation.

### Changed
- **`pixel-perfect-diff.md`** merged into `ui-reverse-engineering/visual-verification.md` Phase D. The standalone file now redirects to the merged location. Other skills (`transition-reverse-engineering`, `ui-capture`) that reference `../pixel-perfect-diff.md` will see the redirect notice.

## [0.0.17] - 2026-04-11

### Added
- **`ui-reverse-engineering`**: `interaction-detection.md` — **Step 6b: Transition Spec Document** (new section). After bundle analysis, produce `bundle-map.json` (chunk → feature mapping) and `transition-spec.json` (per-transition spec with trigger, target, easing, duration, bundle branch, reference frames). Single source of truth for implementation — eliminates re-grepping bundles during fixes.
- **`ui-reverse-engineering`**: `interaction-detection.md` — **ALL loaded chunks download** via `performance.getEntriesByType('resource')`. Replaces single main.js download. Lazy chunks contain page-specific transition logic (bookmark animations, scroll triggers, component transitions).
- **`ui-reverse-engineering`**: `component-generation.md` — **HARD BLOCK on transition-spec.json**: generation step refuses to proceed without it. Includes "Using transition-spec.json during implementation" protocol (5 steps).
- **`ui-reverse-engineering`**: `component-generation.md` — **Mandatory comparison after each transition implementation**: screenshot original + impl at same state, compare before moving on. Max 3 cycles per transition.
- **`ui-reverse-engineering`**: `evals.json` — 5 new evals (31–35) covering: conditional branch verification, lazy chunk discovery, spec re-loading on re-invocation, frames-before-implementation, per-transition comparison loop.
- `scripts/validate-gate.sh` — **Bash gate enforcement script**. 4 gates: `bundle` (ALL chunks + element-animation-map), `spec` (transition-spec.json + bundle-map.json structure validation), `pre-generate` (all extraction artifacts + reference frames), `post-implement` (comparison screenshots exist). Exits with code 1 on failure — hard blocks proceeding.
- `scripts/download-chunks.sh` — **Automated chunk download + analysis**. Takes URL list (JSON array or newline), downloads all chunks, detects animation libraries (GSAP, Lenis, ScrollTrigger, SplitText, Framer Motion), extracts transition-related selectors, produces `bundle-analysis.json` + skeleton `bundle-map.json`.
- `scripts/gsap-to-css.sh` — **GSAP easing → CSS cubic-bezier converter**. Single lookup (`power5` → `cubic-bezier(0.05, 0.86, 0.09, 1)`), full table (`all`), or bundle scan (`scan file.js` finds all ease values and converts).
- **`ui-reverse-engineering`**: `SKILL.md` — **Phase 0: Load Existing Analysis**. On re-invocation, check for `transition-spec.json` / `bundle-map.json` and load immediately. Prevents redundant re-extraction.
- **`ui-reverse-engineering`**: `SKILL.md` — **Step 5d: Transition Spec** gate in process flow. `bundle-map.json` + `transition-spec.json` must exist before proceeding to animation detection.
- **`ui-reverse-engineering`**: `animation-detection.md` — AE diff curve analysis, GSAP timeline position parser, conditional branch detection, fixed overlay cleanup protocol.
- **`ui-reverse-engineering`**: `component-generation.md` — failure-based diagnosis entries, CSS multi-step limitation, package rebuild requirement.
- **`ui-reverse-engineering`**: `animation-detection.md` — **3-tier idle frame analysis**: Tier 1 AE diff (zero tokens, finds WHEN changes happen), Tier 2 DOM polling (zero tokens, finds WHAT elements change with exact timestamps), Tier 3 LLM Read (minimal tokens, only for transition boundaries that automation can't classify). Replaces "Read consecutive frame pairs" which consumed ~260K tokens for 104 frames. Expected usage: ~10K tokens (2-4 frame reads).
- **`ui-reverse-engineering`**: `component-generation.md` — **Loop 0: Original A/B comparison at 60fps** (MANDATORY for animated components). Captures both original and implementation at 60fps via agent-browser rAF polling, then diffs 5 properties: DIRECTION (which axis animates), RANGE (start/end values), TIMING (when transitions start/end), EASING (curve shape), COUPLING (which properties are synchronized). Prevents shipping wrong clipPath axis, inventing nonexistent animations, and desynchronized phases.
- **`ui-reverse-engineering`**: `component-generation.md` — **CSS-to-React translation pitfalls** section: 3 categories of errors when converting extracted CSS/GSAP animations to React components — (1) exit animations impossible with conditional rendering, (2) callback chains breaking on React lifecycle, (3) text line splitting must match CSS not character counts. Each with wrong/right patterns.
- **`ui-reverse-engineering`**: `component-generation.md` — 3 new entries in failure-based diagnosis table: splash transition not playing (conditional rendering), text line breaks differ (hardcoded split), scroll overlay not disappearing (callback chain failure).

### Changed
- **`ui-reverse-engineering`**: `animation-detection.md` — Tier 2 DOM polling upgraded from `setInterval(200ms)` (5fps) to `requestAnimationFrame` (60fps). 200ms polling loses easing curve shape, can't distinguish which clipPath axis animates, and merges simultaneous property changes. 60fps gives per-frame values that reveal direction, easing, and coupling. Includes comparison table showing what 5fps misses vs 60fps catches.
- **`ui-reverse-engineering`**: `animation-detection.md` — **Splash throttle protocol** added to Tier 2. `agent-browser eval` runs AFTER page load, so splash animations that fire on DOMContentLoaded are already finished by the time the capture script injects. Fix: apply network throttle (`agent-browser throttle 3g`) before eval injection, then remove throttle — this delays JS execution so the rAF capture starts before splash fires. Includes detection heuristic (Tier 1 AE spikes in first 3s = splash exists), fallback for when throttle isn't available (60fps video frame extraction + pixel measurement), and when-to-use decision matrix.
- **`ui-reverse-engineering`**: `animation-detection.md` — **MANDATORY video→bundle cross-reference** for splash animations. Both `eval` and `addInitScript` can miss splash because GSAP sets `from` values before capture starts — the captured "initial state" is already mid-animation. Video frames are the only reliable source. Protocol: (1) extract 60fps from video, (2) read 3-5 transition frames visually, (3) immediately grep bundle for the animation selector, (4) look for `> *` child-selector patterns that indicate per-child staggered animation.
- **`ui-reverse-engineering`**: `component-generation.md` — **SVG/DOM child staggered animation** pattern added to animation library wiring section. When bundle shows `.fromTo(".selector > *", ...)` with `stagger`, animate each child individually via loop, not the parent. Includes code template and common use cases (logo assembly, icon reveals, grid cards).
- **`ui-reverse-engineering`**: `component-generation.md` — 2 new entries in failure-based diagnosis table: (1) logo "assembles" in original but slides as unit in implementation → per-child animation, (2) splash data shows "element was always static" → eval/addInitScript missed it, use video frames.
- **`ui-reverse-engineering`**: `interaction-detection.md` — **Scroll method verification** (Step 5): when custom scroll detected, 3-screenshot comparison (before / scrollTo / wheel) to verify which scroll method works. Prevents `window.scrollTo()` false observations on Lenis/GSAP sites. Saves `scrollToWorks` and `verifiedMethod` to `scroll-engine.json`.
- **`ui-reverse-engineering`**: `interaction-detection.md` — **Bundle values → DOM element mapping** (mandatory after grep): cross-references bundle animation parameters with DOM selectors and idle capture frames to produce `element-animation-map.json`. Prevents applying wrong animation values to wrong elements.
- **`ui-reverse-engineering`**: `animation-detection.md` — **Idle capture execution protocol**: explicit command block with rationale for recording DURING page load (not after). Explains why "wait first, then record" misses splash animations entirely.
- **`ui-reverse-engineering`**: `SKILL.md` — JS bundle analysis promoted from conditional ("if needed") to **mandatory for ALL sites**. New Step 5c inserted into process flow with dedicated blocking gate.
- **`ui-reverse-engineering`**: `SKILL.md` — **Step Execution Rules reorganized**: 17 flat rules → **16 rules in 4 execution phases** (A: Before any work, B: During extraction, C: During implementation, D: During verification). Removed project-specific rules (shared package rebuild, URL TLD guessing). Consolidated bundle rules (download + analysis + branch verification).
- **`ui-reverse-engineering`**: `SKILL.md` — Bundle gate updated: "≥1 bundle" → "ALL loaded chunks downloaded via performance API".
- **`ui-reverse-engineering`**: `SKILL.md` — Step checkpoint table updated: Step 5→6 now requires ALL lazy chunks, not just main.js.
- **`ui-reverse-engineering`**: `interaction-detection.md` — Step 6 header changed from "JS Bundle Analysis (if needed)" to "JS Bundle Analysis (MANDATORY)". Added skip-rationalizations with rebuttals.
- **`ui-reverse-engineering`**: `animation-detection.md` — Phase A header changed to "MANDATORY".
- **`ui-reverse-engineering`**: `visual-verification.md` — Frame extraction standardized to 60fps.
- `plugin.json`, `marketplace.json` — version 0.0.17; description updated with transition-spec.json, bundle-map.json, phase-organized rules.

## [0.0.16] - 2026-04-08

### Added
- **`ui-capture`**: `report-page.md` — new overlay-based report page: fullpage screenshot as base layer with interactive transition overlays pinned at exact page coordinates. Sidebar region index with trigger badges, click-to-scroll navigation. Video overlays (scroll/mousemove/timer) auto-play via IntersectionObserver. Image toggle overlays (hover/intersection) show active state on mouse hover.
- **`ui-capture`**: `detection.md` — `bounds.x` coordinate collection: all 4 region types (hover, scroll, mousemove, timer) now capture `rect.left + window.scrollX` for precise horizontal overlay positioning.
- **`ui-reverse-engineering`**: `visual-verification.md` — Phase E: VLM sanity check. After all automated gates pass, read exactly 1 ref+impl screenshot pair (~4000 tokens) to catch issues outside measured selectors (missing elements, z-index stacking, overflow clipping, visual weight).

### Changed
- **`ui-reverse-engineering`**: `visual-verification.md` — all image comparisons switched from LLM Vision reading to AE/SSIM (zero tokens). C1: 5 static screenshots now compared via `compare -metric AE`. C2: 60fps scroll frames now compared via `ffmpeg SSIM` batch. C3 video frames: same SSIM batch. LLM only reads images for: fail diagnosis (diff images), one-time spot-checks (Phase A gate), and final VLM sanity check (Phase E, 1 pair).
- **`transition-reverse-engineering`**: `verification.md` — element-scope frame comparison switched from LLM table to AE batch. Fullpage-scope frame comparison switched to SSIM batch. Post-implementation capture comparison also SSIM-based.
- **`pixel-perfect-diff.md`** — diff image reading restricted to AE > 0 failures only. No image reading for passing elements.
- **`ui-capture`**: `comparison-page.md` — Report Mode section extracted to standalone `report-page.md`. Section now contains a short reference pointer instead of the full HTML template.
- **`ui-capture`**: `detection.md` — all region types now wrap coordinates in `bounds: { x, width, height }` object, matching `regions.json` schema. Previously output raw `x`, `y`, `width`, `height` at root level.
- **`ui-capture`**: `detection.md` — `regions.json` schema examples updated with `bounds.x` field for all region types.
- **`ui-capture`**: `SKILL.md` — reference files list includes `report-page.md`. Phase R references updated from `comparison-page.md` to `report-page.md`.
- **`ui-reverse-engineering`**: `SKILL.md` — reference files description updated with AE/SSIM comparison and Phase E VLM sanity check.
- `plugin.json`, `marketplace.json` — version bumped to 0.0.16; description updated with overlay report page.
- `README.md` — ui-capture description updated with overlay-based report page and `bounds.x` coordinate collection.

### Fixed
- **`ui-capture`**: `capture-transitions.md` — removed `hover-` prefix from css-hover capture filenames (`hover-<name>-idle.png` → `<name>-idle.png`). Now consistent with comparison-page.md and report-page.md templates.
- **`ui-capture`**: `report-page.md` — template placeholders renamed from `<xPct>/<wPct>/<hPct>` to `<topPct>/<leftPct>/<widthPct>/<heightPct>`, matching the overlay positioning rules section.

## [0.0.15] - 2026-04-06

### Added
- **`ui-reverse-engineering`**: `style-extraction.md` — design bundle grouping: post-processing step groups CSS properties into 5 co-varying bundles (surface, shape, type, tone, motion). Deduplicates identical bundles and assigns IDs. Results saved to `design-bundles.json`.
- **`ui-reverse-engineering`**: `component-generation.md` — bundle covariance rules: when fixing a property during iterations, all sibling properties in the same bundle must be verified. Prevents isolated fixes that break visual coherence (e.g., changing fontSize without lineHeight).
- **`ui-reverse-engineering`**: `style-audit.md` — 10-point design fidelity scoring: diagnostic checklist (typography, colors, spacing, surface, layout, responsive, interactions, motion, assets, completeness). Runs at each fix iteration to guide priority. Score regression triggers rollback. 3 iterations without 9+ triggers user escalation.
- **`ui-reverse-engineering`**: `SKILL.md` — Step 6c expanded from 3-check audit to 6-stage pre-generation design audit: data inventory, role identification, grouping + hierarchy, layout direction, design bundle verification, component boundaries. Each stage produces a JSON artifact.

### Changed
- **`ui-reverse-engineering`**: `visual-verification.md` — fix protocol updated: 10-point scoring runs first to guide fix direction, covariance rules checked before committing changes, score regression triggers rollback.
- **`ui-reverse-engineering`**: `visual-verification.md` — completion gate updated: score ≥ 9 required before running pixel-perfect-diff.
- **`ui-reverse-engineering`**: `SKILL.md` — Step 6b assembly list includes `design-bundles.json`. Extraction gate includes bundle validation.
- **`ui-reverse-engineering`**: `component-generation.md` — input checklist includes `design-bundles.json` and `component-map.json`.
- `plugin.json`, `marketplace.json` — version bumped to 0.0.15; description and keywords updated.
- `README.md` — pipeline diagram updated with 6-stage audit and scoring loop; 6b assembly list includes `design-bundles`.

### Fixed
- **`ui-reverse-engineering`**: `component-generation.md` — removed `typography-scale.json` from input checklist (absorbed by `design-bundles.json` type bundle). "Typographic scale consistency check" replaced with "Design bundle consistency check". Fixed Step 6c references in checklist (`interaction-states.json` from Step 5, `decorative-svgs.json` from Step 3).
- **`ui-reverse-engineering`**: `style-audit.md` — removed A3.6 "Cross-section typography consistency" (duplicated by 10-point scoring #1). Clarified A1-A4 → 10-point scoring relationship (detail → summary). Removed duplicate `---` separator. Scoring eval note: 10-point score derives from A1-A4 `style-audit-diff.json`, not a separate getComputedStyle run.
- **`ui-reverse-engineering`**: `style-extraction.md` — fixed bundle grouping eval: removed unused `bundles` initialization, declared at conversion point.
- **`ui-reverse-engineering`**: `SKILL.md` — differentiated 6b gate (existence) from 6c gate (consistency). Phase 4 completion gate now requires `10-point score ≥ 9`.
- All skill documents — translated remaining Korean text to English (`pixel-perfect-diff.md`, `capture-transitions.md`, `comparison-page.md`, `SKILL.md`, `visual-verification.md`, `README.md`).

## [0.0.14] - 2026-04-05

### Added
- **`ui-reverse-engineering`**: `dom-extraction.md` — portal-escaped element detection: finds `position: fixed` elements inside `transform`-ed scroll wrappers (broken by CSS spec). Detects elements rendered outside the wrapper (already portal-escaped) and elements inside (need portal in implementation). Results saved to `portal-candidates.json`.
- **`ui-reverse-engineering`**: `dom-extraction.md` — inline SVG collection: extracts `outerHTML` verbatim for all `<svg>` elements (logos, icons, brandmarks). Never recreates SVGs from visual appearance. Results saved to `inline-svgs.json`.
- **`ui-reverse-engineering`**: `style-extraction.md` — decorative SVG extraction: captures `position: absolute` / `aria-hidden` SVGs with full path data (`d`, `stroke-width`, `fill`, `strokeDasharray`).
- **`ui-reverse-engineering`**: `style-extraction.md` — stroke-based hover animation detection: captures idle + active `stroke-dasharray`/`stroke-dashoffset` values on SVG children during hover state delta.
- **`ui-reverse-engineering`**: `interaction-detection.md` — mouse-tracking interaction detection: finds elements that follow cursor position (image tooltips, custom cursors, parallax tilt, spotlight effects) by detecting absolutely-positioned `pointer-events: none` children.
- **`ui-reverse-engineering`**: `interaction-detection.md` — hover state delta now captures `stroke-dasharray`/`stroke-dashoffset` on ALL SVG children (`path`, `rect`, `circle`, `line`), not just the parent element.
- **`ui-reverse-engineering`**: `interaction-detection.md` — custom scroll engine detection: detects `overflow: hidden` + `transform`-based scroll (rAF lerp), extracts wrapper selector and lerp behavior via wheel event dispatch. Known library detection (Lenis, GSAP ScrollSmoother, Locomotive). Impact rules for downstream extraction steps (IntersectionObserver, window.scrollTo, portal escapes). Results saved to `scroll-engine.json`.
- **`ui-reverse-engineering`**: `interaction-detection.md` — cross-component DOM manipulation detection: finds `querySelector + style` patterns and scroll-position-based state changes in bundles. Records as `type: "cross-component"` in `interactions-detected.json`.
- **`ui-reverse-engineering`**: `SKILL.md` — Step 6c pre-generation audit: typography scale table (consistent values per role), multi-state interaction table (idle + active values), decorative SVG inventory (verbatim paths). Gate requires all three artifacts before code generation.
- **`ui-reverse-engineering`**: `component-generation.md` — Tailwind v4 custom font registration rule (`@theme` block, not `:root` CSS variables). `font-[var(--my-font)]` with comma-separated values does not work in Tailwind v4.
- **`ui-reverse-engineering`**: `component-generation.md` — font size vw conversion formula (`vw = extractedPx / viewportWidth * 100`) with `clamp()` pattern.
- **`ui-reverse-engineering`**: `component-generation.md` — custom scroll engine generation rules: rAF lerp loop, portal escape for fixed elements, scroll context for dependent components.
- **`ui-reverse-engineering`**: `component-generation.md` — mouse-follow interaction generation pattern (`onMouseMove` + absolute child positioning).
- **`ui-reverse-engineering`**: `component-generation.md` — SVG verbatim rule: never recreate from visual appearance, use `outerHTML` from `inline-svgs.json` with HTML→JSX attribute conversion.
- **`ui-reverse-engineering`**: `animation-detection.md` — NEW: 3-phase motion detection document (idle capture → scroll capture → per-element tracking). Detects splash, auto-timers, parallax, scroll-zoom, clip-reveal, sticky, word-stagger.
- **`ui-reverse-engineering`**: `style-audit.md` — NEW: post-generation class-level computed style comparison (ref vs impl). Catches wrong font-size, font-weight, missing SVGs, wrong images, spacing mismatches. Runs in parallel with Step 8.

### Changed
- **`ui-reverse-engineering`**: `SKILL.md` — Step 6b assembly list expanded with `portal-candidates.json`, `inline-svgs.json`, `scroll-engine.json`. Extraction gate checklist updated with new artifacts.
- **`ui-reverse-engineering`**: `SKILL.md` — Reference Files section updated: `interaction-detection.md` scoped to Step 5; new `animation-detection.md` listed for Step 6; `style-audit.md` listed as parallel post-generation check.
- **`ui-reverse-engineering`**: `component-generation.md` — input checklist expanded with `portal-candidates.json`, `inline-svgs.json`, `scroll-engine.json`, `typography-scale.json`, `interaction-states.json`, `decorative-svgs.json`.
- **`ui-reverse-engineering`**: `component-generation.md` — mandatory typography scale consistency check before generation.
- `plugin.json`, `marketplace.json` — version bumped to 0.0.14; description and keywords updated.
- `README.md` — pipeline diagram updated; new sub-documents listed.

## [0.0.13] - 2026-04-03

### Added
- **`ui-capture`**: Phase 1 — custom scroll container auto-detection (`data-lenis`, `.locomotive-scroll`, `overflow: hidden` fallback). Returns `scrollType` (`native`|`custom`) and `scrollSelector` for all subsequent scroll operations.
- **`ui-capture`**: Phase 1 — section-by-section screenshot capture: resize viewport to each section's actual height, scroll into view, capture. Replaces single fullpage screenshot.
- **`ui-capture`**: Phase 1 — `mouse wheel`-based scroll recording for custom scroll sites (Lenis, Locomotive, etc.) — only real wheel events trigger these libraries.
- **`ui-capture`**: Phase 1 — mandatory ffmpeg trim for scroll videos (`-ss 0.3 -t <activeDuration>`) to remove dead frames from `record start`/`stop`.
- **`ui-reverse-engineering`**: Step 6 — animation detection pipeline: frame extraction (`ffmpeg fps=2`) → consecutive frame comparison → DOM element mapping → classification (scroll-reveal, parallax, sticky, scale, clip-path, auto-timer) → per-animation capture. Results saved to `animations-detected.json`.
- **`ui-reverse-engineering`**: Step 6 — automatic `/transition-reverse-engineering` invocation when scroll-driven, canvas, or WebGL animations detected.

### Changed
- **`ui-capture`**: Phase 1 troubleshooting table expanded with 6 new entries: custom scroll container detection, `scrollTo` no-op on custom sites, blank selector screenshots, identical section heights, scroll video dead time, and scroll video instant-jump.
- **`ui-reverse-engineering`**: Step 6b assembly list now includes `animations-detected.json`.
- **`ui-reverse-engineering`**: Extraction gate checklist now requires `animations-detected.json` with selector/type/captures per entry.
- `plugin.json`, `marketplace.json` — version bumped to 0.0.13; description and keywords updated.
- `README.md` — pipeline diagram updated with animation detection step; ui-capture description updated with custom scroll and section screenshots.

## [0.0.12] - 2026-04-02

### Added
- **`ui-reverse-engineering`**: `interaction-detection.md` — auto-timer detection section: `setInterval`/`setTimeout` carousel/slideshow/rotating-text detection via timed screenshot comparison and bundle grep. Results saved to `interactions-detected.json` under `autoTimer` key.
- **`ui-reverse-engineering`**: `interaction-detection.md` — JS animation library detection section: bundle grep patterns for Framer Motion, GSAP, and pure CSS transitions. Extracts spring params, ease presets, duration/stagger from minified code.
- **`ui-reverse-engineering`**: `interaction-detection.md` — spring-to-cubic-bezier mapping table: common spring/ease configs → CSS `cubic-bezier` equivalents.
- **`ui-reverse-engineering`**: `interaction-detection.md` — known issues: `agent-browser record start` page reload workaround (rapid sequential screenshots), intro animation scroll blocking (5–8s wait).

### Changed
- `plugin.json`, `marketplace.json` — version bumped to 0.0.12; description and keywords updated.
- `README.md` — animation types table updated with auto-timer and animation library extraction rows.

## [0.0.11] - 2026-03-31

### Added
- **`ui-reverse-engineering`**: `dom-extraction.md` — Step 2.5: head metadata extraction (`<title>`, favicon, viewport) + visible image collection and download. Images filtered by `getBoundingClientRect().height > 0`. Assets saved to `tmp/ref/<component>/assets/`. HTTPS only, 10MB limit.
- **`ui-reverse-engineering`**: `interaction-detection.md` — scroll behavior detection step: scans all elements for `scroll-snap-type/align/stop`, `scroll-behavior: smooth`, `overscroll-behavior`. Results saved to `interactions-detected.json` under `scrollBehavior` field. JS scroll library detection (Lenis, GSAP ScrollSmoother, Locomotive) via bundle grep.
- **`transition-reverse-engineering`**: `js-animation-extraction.md` — scroll library parameter extraction section: detection signatures, config extraction (lerp, duration, wheelMultiplier, smooth, wrapper/content), `scroll-library.json` schema, and Lenis component generation example.

### Evals
- **`ui-reverse-engineering`**: `evals/evals.json` — evals 28–30 added: asset download (favicon + visible images), scroll behavior detection (snap/smooth/overscroll), and Lenis JS scroll library extraction.
- **`transition-reverse-engineering`**: `evals/evals.json` — eval 22 added: GSAP ScrollSmoother config extraction from bundle.

### Changed
- **`ui-reverse-engineering`**: `component-generation.md` — input checklist updated with `head.json` + `assets.json`; image rule updated to prefer downloaded assets over placeholders; scroll behavior added to generation prompt template with Tailwind utility mapping.
- **`ui-reverse-engineering`**: `interaction-detection.md` — JS scroll library detection moved from Step 5 to Step 6 (after bundle download, where bundles actually exist).
- **`ui-reverse-engineering`**: `SKILL.md` — pipeline diagram updated with Step 2.5 (head + assets extraction); Step 6b assembly list includes head.json + assets.json; Output schema includes head/assets/scrollBehavior fields; Reference Files updated.
- **`ui-capture`**: `SKILL.md` — Phase R added: standalone report mode (`report.html`) when no local-url provided. Shows fullpage screenshot, detected regions table, per-region captures, and CTA. Process flow diagram updated with branching (local-url → compare mode, no local-url → report mode).
- **`ui-capture`**: `SKILL.md` — Phase 5 rewritten from "User Review" to "Completion Gate" with two paths: interactive mode (wait for user feedback) and autonomous mode (external loop driver, e.g. `ui_clone.benchmark_harness`). Autonomous mode uses `pixel-perfect-diff.json` as binary pass/fail gate with 3 auto-fix retries before escalation.
- **`ui-capture`**: `comparison-page.md` — Report Mode section added with full `report.html` template: regions table with trigger-type badges, per-region capture previews (clip screenshots + videos), and usage conditions (standalone vs comparison).
- `README.md` — pipeline diagram updated with Step 2.5; scroll behavior row added to animation types table; description updated to mention asset extraction.
- `plugin.json`, `marketplace.json` — version bumped to 0.0.11; keywords updated.

## [0.0.10] - 2026-03-30

### Security
- **`ui-reverse-engineering`**: `component-generation.md` — "Use the EXACT text" rule replaced with untrusted data handling: all extracted text treated as data, not instructions. Prompt-like language rendered as literal display text only. New "Security: Extracted Content Handling" section added with explicit rules for DOM text, HTML comments, CSS content properties, `data-*` attributes, and prompt boundary markers.
- **`ui-reverse-engineering`**: `interaction-detection.md` — post-detection sanitization check added after `interactions-detected.json` save. Grep scan for prompt injection patterns (`ignore previous`, `system prompt`, `<script>`, `javascript:`, `data:text`); suspicious content logged and redacted.
- **`ui-capture`**: `SKILL.md` — Security section expanded from 3-line summary to full "Content Sanitization" section with 5 rules (untrusted data, directive rejection, eval output sanitization, no credential forwarding, cleanup) and explicit "What to ignore" checklist for captured content.
- **`ui-capture`**: `detection.md` — security note added: detection eval results (selectors, class names, attribute values) are classification data only, never instructions. Suspicious directive-like text in attributes redacted before saving to `regions.json`.
- **`transition-reverse-engineering`**: `css-extraction.md` — security note added: extracted CSS values treated as display values only. `javascript:` URIs and encoded payloads in custom property values logged and skipped.
- **`transition-reverse-engineering`**: `js-animation-extraction.md` — security note added: bundle analysis is read-only, never execute downloaded code. Directive-like text and suspicious encoded strings skipped.

### Changed
- `plugin.json`, `marketplace.json` — version bumped to 0.0.10.

## [0.0.9] - 2026-03-29

### Changed
- **`pixel-perfect-diff`** — restructured from "getComputedStyle-first" to "Visual Gate first, always-run-both" approach. Phase 1 (Visual Gate) is the primary pass/fail criterion using DOM clip screenshots + pixel diff (AE/SSIM). Phase 2 (Numerical Diagnosis via getComputedStyle) now always runs regardless of Phase 1 result — catches sub-pixel mismatches like `font-size: 15px vs 16px` and `letter-spacing` micro-differences that AE/SSIM passes. Gate: Phase 1 all pass AND Phase 2 mismatches = 0 (both required).
- **`pixel-perfect-diff`** — Visual Gate (Phase 1) captures per-element state: idle (all elements) + active (css-hover / js-class / intersection) + before/mid/after (scroll-driven). Active rect re-measured after state activation to handle `transform: scale()` and geometry-changing transitions. `mid` state catches easing curve mismatches (linear vs ease-in-out) that before/after alone would miss.
- **`pixel-perfect-diff`** — `scroll-driven` transitions now follow a two-phase approach: (1) exploration video to identify trigger_y / mid_y / settled_y, then (2) clip screenshot verification at those exact y positions. V3 clip commands, V4 diff loops, V6 JSON schema, and P2/P3 (Numerical Diagnosis) all updated for scroll-driven 3-state capture.
- **`pixel-perfect-diff`** — Phase 2 Numerical Diagnosis measures both idle and active states separately (`ref-styles-idle.json`, `ref-styles-active.json`). P3 Diff Table includes State column. Active measurement targets visual-change props (`color`, `backgroundColor`, `boxShadow`, `transform`, `opacity`, `filter`).
- **`pixel-perfect-diff`** — Visual Gate JSON schema: each element entry now includes `"state"` field (`"idle"`, `"active"`, `"before"`, `"mid"`, `"after"`).
- **`ui-capture`**: `capture-transitions.md` Step 2C — hover/js-class/intersection capture changed from video recording to eval + clip screenshot (idle + active states as static PNGs). CDP hover documented for CSS `:hover` cases.
- **`ui-capture`**: `capture-transitions.md` Step 2B — split into 2B-1 (exploration video, identifies trigger_y/mid_y/settled_y) and 2B-2 (clip screenshot verification at before/mid/after). Clip paths: `tmp/ref/capture/clip/{ref,impl}/`. Mid rect re-measured at each scroll position (scroll transforms change element bounds).
- **`ui-capture`**: `comparison-page.md` — Phase 4A renamed to "Pixel-Perfect Visual Gate". Gate requires both Visual Gate pass and mismatches = 0. Hover section: videos → paired idle/active clip screenshots. Scroll-driven section: paired before/mid/after clip screenshots added. Image paths updated to `clip/{ref,impl}/`. diff table columns: element, state, ae, ssim, status.
- **`ui-capture`**: `SKILL.md` — Phase 2C updated to eval + clip screenshot (no video). Phase 2B updated to 2-phase (exploration + clip). `--session` flag added to all Phase 1 commands. `clip/{ref,impl,diff}` directory added to setup block. Phase 4A gate updated.
- **`ui-reverse-engineering`**: `visual-verification.md` — A-C3 and B-C3 rewritten with triggerType dispatch: css-hover/js-class/intersection use eval + clip screenshot (idle + active), scroll-driven/mousemove/auto-timer retain video. C3 comparison table split into clip-diff and frame-comparison tracks. Phase D gate updated: Visual Gate all pass AND mismatches = 0.
- **`ui-reverse-engineering`**: `SKILL.md` — Phase D gate box and Principle 6 updated to Visual Gate framing. Step R GATE: "transition videos" → "transition captures (png or webm)". Reference Files updated.
- **`transition-reverse-engineering`**: `verification.md` — Pixel-Perfect Static State Diff updated to Visual Gate (clip screenshot + AE diff) with before/after capture commands. Both phases always run.
- **`transition-reverse-engineering`**: `SKILL.md` — Step 0 Option B scroll-driven updated to 2-phase (exploration video → clip at before/mid/after). Hover capture: full screenshot → clip screenshot (idle + active, CDP hover, rect re-measure). Step 4 GATE: Visual Gate all pass AND mismatches = 0.
- **`README.md`** — Shared Document section, trigger type table, and skill flow diagrams updated to reflect clip-screenshot approach and always-run-both behavior.
- **`mousemove` and `auto-timer` remain video-only** — no capture method change; only css-hover/js-class/intersection (eval + clip) and scroll-driven (2-phase) changed.
- **`plugin.json`**, **`marketplace.json`** — version 0.0.9; description updated to reflect always-run-both and scroll-driven 2-phase; keywords updated.
- **Consistency fixes** — `capture-transitions.md` Step 2B-2 "4 states" corrected to "3 states" (before/mid/after); all clip screenshot paths in Step 2C unified to `clip/{ref,impl}/` (was incorrectly `transitions/{ref,impl}/`); `compare` command paths in `visual-verification.md` and `verification.md` prefixed with correct `tmp/ref/<component>/`; Phase D and Step 4 gate wording updated to cover all state variants (idle / active / before / mid / after).

## [0.0.8] - 2026-03-28

### Added
- **`ui-capture`**: `SKILL.md` — `agent-browser` session rule added. Named `--session <project-name>` is now required on every `agent-browser` command. The default session is global and shared; without a name, commands from other projects overwrite browser state mid-capture.
- **`transition-reverse-engineering`**: `SKILL.md` — same session rule added as a top-of-file callout.
- **`ui-reverse-engineering`**: `SKILL.md` — same session rule added as a top-of-file callout.

### Changed
- **`ui-capture`**: `evals/evals.json` — evals 17–18 added: timestamp-based crop for deep sections (hero footage bleed case), and named session requirement across all agent-browser commands. eval 11 expectation updated to reference timestamp crop method (stdev-only reference removed).

### Fixed
- **`ui-capture`**: `capture-transitions.md` — scroll crop logic rewritten. The previous stdev > 8 method only stripped blank frames but left hero footage at the start of deep-section clips. Correct approach is timestamp-based: record start at t=0, wait for page load (~3 s), note wall-clock offset before scroll command, then use that timestamp as the ffmpeg crop point. Old stdev Python snippet removed and replaced with explicit SCROLL_T variable pattern.

## [0.0.7] - 2026-03-27

### Added
- **`pixel-perfect-diff`** — new shared verification document (not a registered skill). Mandatory numerical gate invoked by all three skills. Measures every key element with `getComputedStyle` on both reference and implementation across typography, spacing, sizing, layout, visual, and position properties. Produces `pixel-perfect-diff.json` with `"result": "pass"` and `"mismatches": 0` as the only valid PASS state. "Looks the same" is not a valid completion criterion.
- **`ui-reverse-engineering`**: `evals/evals.json` — evals 26–27 added: pixel-perfect pass scenario (P1–P6 artifact chain), and mismatch found and fixed (targeted re-measurement, no full rewrite).
- **`transition-reverse-engineering`**: `evals/evals.json` — evals 20–21 added: pixel-perfect diff for before/after resting states, and "close enough" rejection with exact pixel fix.
- **`ui-capture`**: `evals/evals.json` — evals 15–16 added: Phase 4A standalone pass scenario, and mismatches fixed before compare.html generated.

### Changed
- **`ui-reverse-engineering`**: `SKILL.md` — Step 8 Visual Verification restructured into Phase A (reference capture), Phase B (impl capture), Phase C (frame comparison tables C1/C2/C3), and Phase D (pixel-perfect numerical diff via `pixel-perfect-diff.md`). Gate now requires ALL of C1/C2/C3 ✅ AND `pixel-perfect-diff.json` `"result": "pass"`, `"mismatches": 0`. Principle 6 added: "Numerical match, not visual match."
- **`ui-reverse-engineering`**: `visual-verification.md` — Phase D section added: explains what screenshot comparison cannot catch (font-size, font-weight, padding, height within ~10%), requires `pixel-perfect-diff.md` P1–P6 for each major section. Completion gate updated to `C1 ✅ AND C2 ✅ AND C3 ✅ AND Phase D "mismatches": 0`.
- **`ui-capture`**: `SKILL.md` — Phase 4 renamed to "Phase 4: Pixel-Perfect Diff + Comparison Page". Phase 4A (pixel-perfect-diff.md P1–P6 for every major section, gate before compare.html) added before Phase 4B (compare.html generation). Reference Files ordering corrected (pixel-perfect-diff.md listed as Phase 4A, comparison-page.md as Phase 4A gate + Phase 4B).
- **`ui-capture`**: `comparison-page.md` — Phase 4A section added with gate checklist; diff table CSS (`.diff-table`, `.diff-pass`, `.diff-fail`, `.diff-summary`) added to compare.html structure; pixel-perfect diff table embedded at top of compare.html before video sections.
- **`ui-capture`**: `evals/evals.json` — eval 6 `expected_output` updated to reflect Phase 4A requirement; duplicate key removed.
- **`transition-reverse-engineering`**: `SKILL.md` — Step 4 Verify now requires `pixel-perfect-diff.md` P1–P6 for resting states (before + after animation). Gate adds `pixel-perfect-diff.json` `"result": "pass"`, `"mismatches": 0`.
- **`transition-reverse-engineering`**: `verification.md` — "Pixel-Perfect Static State Diff (MANDATORY)" section added. Gate updated to require both `"result": "pass"` and `"mismatches": 0` (before + after states). Checklist item updated to match full two-condition form.
- `README.md` — intro framing corrected from "four skills" to "three skills + one shared verification document"; `pixel-perfect-diff` section header renamed from "Skill 4" to "Shared Document"; Security and Evals sections updated accordingly; flow diagrams updated for all three skills.
- `plugin.json` and `marketplace.json` — version bumped to 0.0.7; description updated to mention pixel-perfect numerical verification; keywords added (`pixel-perfect`, `getComputedStyle`, `numerical-diff`, `css-verification`).

## [0.0.6] - 2026-03-25

### Fixed
- **`ui-capture`**: `detection.md` — trigger-type classification table added before detection script. Each region now tagged with `triggerType` (`css-hover`, `js-class`, `intersection`, `scroll-driven`, `mousemove`, `auto-timer`). Wrong trigger type previously caused blank recordings.
- **`ui-capture`**: `detection.md` — `:hover` stylesheet scan integrated into detection script; `regions.json` schema updated with `triggerType` and `triggerClass` fields; example schema updated to show all three trigger types
- **`ui-capture`**: `capture-transitions.md` — documented `record start` fresh-context behavior (resets scroll to y=0 regardless of pre-scroll); correct pattern requires scroll AFTER `record start` + viewport re-set + verify screenshot
- **`ui-capture`**: `capture-transitions.md` — blank start crop script added (python3 stdev threshold to find first content frame); all capture sequences now use trigger-type-specific activation instead of generic hover
- **`ui-capture`**: `comparison-page.md` — video sync rewritten: `busy` flag prevents recursive play loops; `!a.ended` guard on pause listener prevents buffering events from halting playback; `seeked` events added for scrub sync; `ended` event no longer pauses the paired video (each plays to its own end)
- **`ui-capture`**: `SKILL.md` — 2D description updated to "mousemove raster-path video (10×10 grid sweep, single video per element)"; added trigger-type classification note before 2B–2E; 4 common failure rows added (wrong scroll position in recording, blank start, pause/play loop, shorter video stopping longer)

### Changed
- **`ui-capture`**: `SKILL.md` description updated — removed "cursor-position matrices" phrasing, simplified to "interactive animations"
- **`ui-capture`**: `SKILL.md` Phase 1 full scroll video now issues `record start` first, then scrolls (consistent with `record start` fresh-context rule)
- **`ui-capture`**: `SKILL.md` Phase 2C description expanded — "hover in/hold/out" → lists all 3 trigger types covered (css-hover, js-class, intersection)
- **`ui-capture`**: `SKILL.md` Phase 3 — removed stale "10×10 matrix → matrix/impl/" line; removed `matrix/` from Phase 1 directory setup (mousemove output is a video in `transitions/`, not a separate matrix directory)
- **`ui-capture`**: `SKILL.md` Phase 4 — removed "10×10 matrix grids" from comparison page description
- **`ui-capture`**: `comparison-page.md` — Matrix comparison section replaced with Cursor-reactive section using paired raster-path videos; removed `.matrix-grid` CSS; updated HTML comment
- **`ui-capture`**: `capture-transitions.md` Step 2E — fixed scroll-before-record bug: scroll now happens AFTER `record start` + viewport set + page load wait
- **`ui-capture`**: `detection.md` — detection script now tags ALL result types with `triggerType`: scroll (`scroll-driven`), mousemove (`mousemove`), timer (`auto-timer`); timer entries now include `interval_ms` estimated from `data-autoplay-speed`/`data-interval`/`data-delay` attributes (fallback 3000ms); regions.json schema example updated with triggerType in all arrays and a populated timer example
- **`ui-capture`**: `evals/evals.json` — eval 9 scroll/mousemove/timer array expectations updated with correct field names and triggerType values; eval 4 expected_output updated to `transitions/ref/` path (no matrix directory); eval 13 added for intersection trigger capture; eval 14 (was 13) renumbered
- plugin.json and marketplace.json updated, version bumped to 0.0.6; added keywords `trigger-type-detection`, `video-sync`

## [0.0.5] - 2026-03-24

### Added
- **`ui-capture`** — new skill for capturing baseline screenshots and transition videos from reference URLs. Detects scroll, hover, mousemove, and auto-timer transitions. Generates web-based comparison page (original vs clone) with synchronized video playback and 10×10 cursor-reactive matrix grids *(mousemove capture replaced with single raster-path video in 0.0.6)*. Includes error handling for bot detection, hydration delays, and lazy-loaded content.
- **`ui-capture`**: `evals/` directory with trigger-eval.json and evals.json
- **`ui-capture`**: `detection.md`, `capture-transitions.md`, `comparison-page.md` — phase implementation split out from SKILL.md following other skills' convention

### Changed
- **`ui-reverse-engineering`**: Phase A (reference capture) and Phase 4 (verification) now delegate to `/ui-capture` instead of executing visual-verification.md directly. visual-verification.md marked as deprecated.
- **`ui-reverse-engineering`**: Added `ui-capture` as a sub-skill in Reference Files section
- **`transition-reverse-engineering`**: Step 0 (capture reference frames) now offers `/ui-capture` as Option A for fullpage scope. Step 4 (verify) can delegate to `/ui-capture` for comparison.
- **`ralph-kage-bunshin-start`**: UI Clone Detection now invokes `/ui-capture` for baseline capture + web-based user confirmation before task generation
- **`ralph-kage-bunshin-loop`**: DoD visual regression check now invokes `/ui-capture` for impl capture and comparison
- plugin.json and marketplace.json updated to include ui-capture, version bumped to 0.0.5

## [0.0.4] - 2026-03-22

### Added
- **`transition-reverse-engineering`**: `js-animation-extraction.md` — new extraction path for JS-driven animations (scroll-driven, Motion, GSAP, rAF). Covers JS chunk identification, minified pattern decoding (useTransform/useScroll keyframes, interpolation ranges, scroll offsets), raw CSS stylesheet extraction for responsive units (`calc()`, `cqw`, `%`, custom properties), and 4 documented pitfalls (computed-only extraction, transform:none false negative, wrapper-vs-children scale, once-vs-toggle)
- **`transition-reverse-engineering`**: `canvas-webgl-extraction.md` — Rive/Spline/Lottie interactive extraction: scene URL extraction, state machine input detection via bundle grep (SMIBool/SMITrigger/SplineEventName/playSegments), interactive state capture (hover/click reference frames), and extracted.json schema for engine/interactions/playback

### Changed
- **`transition-reverse-engineering`**: SKILL.md — added core principles 8 (getComputedStyle limitation) and 9 (raw CSS over computed values); process flow now has 3 extraction paths (CSS / JS Animation / Canvas) instead of 2 (CSS / Canvas); Effect Classification adds JS Animation Path with CRITICAL warning that scroll-driven effects must use JS bundle analysis; Reference Files updated with js-animation-extraction.md
- README.md — transition-RE description includes scroll-driven JS animations; "When to Use" adds Motion/GSAP/rAF; process diagram shows Step 2a/2b/2c; Supported Animation Types adds scroll-driven and CSS-in-JS responsive layout rows

### Security evals
- **`ui-reverse-engineering`**: `evals/evals.json` — 3 security evals added (id 23–25): prompt injection in extracted DOM, suspicious bundle patterns, post-completion cleanup
- **`transition-reverse-engineering`**: `evals/evals.json` — 2 security evals added (id 18–19): suspicious bundle patterns, prompt injection in measurement data

### Security
- **`ui-reverse-engineering`**: Added Security section to SKILL.md — content boundary rules, prompt injection defense, bundle execution prohibition, credential forwarding prohibition, cleanup policy, and suspicious content handling
- **`ui-reverse-engineering`**: `dom-extraction.md` — post-extraction sanitization check scans `structure.json` for prompt injection patterns
- **`ui-reverse-engineering`**: `style-extraction.md` — post-extraction sanitization check scans `styles.json` for suspicious CSS values (`javascript:`, `expression()`, `data:text`)
- **`ui-reverse-engineering`**: `interaction-detection.md` — bundle sanitization check before analysis, security reminder that bundle analysis is read-only
- **`ui-reverse-engineering`**: `component-generation.md` — prompt boundary markers (`═══ BEGIN/END EXTRACTED DATA ═══`) wrap all untrusted content passed to generation, with explicit instruction to never interpret extracted text as directives
- **`transition-reverse-engineering`**: Added Security section to SKILL.md — untrusted data handling, bundle execution prohibition, credential forwarding prohibition, cleanup policy
- **`transition-reverse-engineering`**: `canvas-webgl-extraction.md` — bundle sanitization check after download, security reminder for read-only analysis
- **`transition-reverse-engineering`**: `css-extraction.md` — security comment on stylesheet curl analysis
- **`transition-reverse-engineering`**: `waapi-scrubbing.md` — security note clarifying scrubber injection context (trusted local script into remote page)
- **`transition-reverse-engineering`**: `measurement.md` — security note on treating `getComputedStyle` measurement data as untrusted
- **`ui-reverse-engineering`**: `responsive-detection.md` — security note on `node -e` JSON parsing from untrusted extraction data
- **`ui-reverse-engineering`**: `visual-verification.md` — post-completion cleanup step (`rm -rf tmp/ref/`) to remove sensitive data
- **`ui-reverse-engineering`**: `component-generation.md` — "EXACT text" rule clarified: directive-like text is rendered literally, never followed
- **`ui-reverse-engineering`**: `interaction-detection.md` — fixed grep regex syntax (`\|` → `|` for ERE mode with `-iE`)
- `README.md` — added Security section summarizing built-in mitigations
- `.claude-plugin/plugin.json` and `marketplace.json` — version bumped to 0.0.4

## [0.0.3] - 2026-03-21

### Added
- **`ui-reverse-engineering`**: `evals/evals.json` — 22 functional evals (137 expectations) covering all documented features: static clone, interactions (hover/click/scroll/auto-timer), responsive sweep, screenshot/video input, multi-section pages, overlay dismissal, SPA loading, Canvas/WebGL branching, JS bundle analysis, CORS fallback, fix protocol, CSS custom properties, @keyframes extraction, resize video, component self-containment, and partial extraction (single-section, hidden-element, multi-section)
- **`ui-reverse-engineering`**: `evals/trigger-eval.json` — 30 trigger evals (16 true / 14 false)
- **`transition-reverse-engineering`**: `evals/evals.json` — 17 functional evals (105 expectations) covering all documented features: hover CSS, WAAPI stagger, scroll parallax, Three.js particles, CSS @keyframes, modal spring overshoot, hybrid CSS+Canvas, Rive, Lottie, Spline, multi-line globalCharIndex stagger, scroll reverse direction, fix protocol, capture-frames.sh validation, children cascade, WAAPI recovery, and post-implementation capture
- **`transition-reverse-engineering`**: `evals/trigger-eval.json` — 25 trigger evals (13 true / 12 false)
- **`transition-reverse-engineering`**: `measurement.md` — mandatory Step -1: 11-point multi-property measurement pass (hover, page-load, scroll-driven). Reveals multi-phase timing and non-linear curves before implementation
- **`transition-reverse-engineering`**: `verification.md` — visual verification & bug diagnosis protocol extracted from SKILL.md. Includes scope-specific comparison tables (element vs fullpage), root-cause-first diagnosis protocol, and "Is This Done?" checklist
- **`transition-reverse-engineering`**: `waapi-scrubbing.md` — WAAPI scrubber injection procedure extracted from SKILL.md. Includes 3-level path fallback (CLAUDE_SKILLS_DIR → git root → ~/.claude/skills)
- **`ui-reverse-engineering`**: `responsive-detection.md` — Step 4: auto-detect real breakpoints via 2-pass viewport sweep (coarse 40px → fine 5px) instead of hardcoded 375/768/1440. Includes per-breakpoint style extraction, responsive verification (A-R/B-R/C-R), and resize video capture
- **`ui-reverse-engineering`**: Step 5b (deferred C3 capture) and Step 6b (assemble extracted.json) in SKILL.md pipeline

### Changed
- **`transition-reverse-engineering`**: SKILL.md restructured — gated step flow (Step -1 → 0 → 1 → 2 → 3 → 4) with explicit gates at each step; principles 6–7 added (measure all properties at multiple points, never assume linearity)
- **`transition-reverse-engineering`**: css-extraction.md — critical warning now references measurement.md instead of duplicating the rationale
- **`ui-reverse-engineering`**: SKILL.md — C1+C2 mandatory in Phase 1, C3 deferred to Step 5b (needs interaction data); breakpoints output changed from fixed `375/768/1440` to `{ "detected": [...], "tailwind": {...} }`
- **`ui-reverse-engineering`**: style-extraction.md — removed orphaned Step 4 section (now a one-line pointer to responsive-detection.md)
- **`ui-reverse-engineering`**: visual-verification.md — A-C3 explicitly marked as deferred to Step 5b; A-R deferred to Step 4
- **`ui-reverse-engineering`**: SKILL.md — added "Partial extraction" section with 4 scope types (single-section, multi-section, single-element, hidden-element), per-scope pipeline adjustments, and artifact naming conventions
- **`ui-reverse-engineering`**: SKILL.md description updated — trigger-oriented phrasing with typical request examples and explicit NOT-trigger conditions
- Eval files placed in per-skill `skills/*/evals/` directories (skill-creator convention)
- `.gitignore` — added `ui-skills-workspace/` for eval run artifacts
- README.md — pipeline diagram updated with Steps 5b/6b, viewport sweep description, transition-RE process overview, and eval coverage section
- `.claude-plugin/plugin.json` and `marketplace.json` — version bumped to 0.0.3; description updated with viewport sweep and 11-point measurement; added keywords (`breakpoint-detection`, `viewport-sweep`, `visual-verification`, `waapi`)

### Fixed
- **`transition-reverse-engineering`**: verification.md — removed Phase B/C terminology (belongs to ui-RE, not transition-RE)
- **`transition-reverse-engineering`**: measurement.md — scroll-driven example replaced overly specific placeholders (`<ring-group-selector>`) with generic pattern + explicit "adapt selectors" guidance
- **`transition-reverse-engineering`**: waapi-scrubbing.md — SKILL_DIR fallback now searches git root and env var, not just `~/.claude/skills`
- **`ui-reverse-engineering`**: SKILL.md Reference Files — `visual-verification.md` now listed as "Steps 8–9" (was "Step 8" only)
- **`transition-reverse-engineering`**: verification.md — SVG `className` now uses `.baseVal` fallback (consistent with dom-extraction.md, interaction-detection.md, css-extraction.md)
- **`ui-reverse-engineering`**: responsive-detection.md — Pass 1 coarse sweep now checks and re-registers `__responsiveMeasure` if page reloads mid-sweep (previously only Pass 2 had this guard)

## [0.0.2] - 2026-03-20

### Added
- **`transition-reverse-engineering`**: Step 0 — mandatory reference frame capture before classification (SKILL.md)
- **`transition-reverse-engineering`**: stagger with hidden parent — correct reveal order recipe; DOM restore, parent opacity, React effect cleanup troubleshooting entries (patterns.md)
- **`ui-reverse-engineering`**: three mandatory capture types (C1 static screenshots, C2 scroll video, C3 transition/interaction video) at 60 fps (visual-verification.md)
- **`ui-reverse-engineering`**: interaction detection results must be saved to `interactions-detected.json` (interaction-detection.md)
- **`ui-reverse-engineering`**: Phase A / Phase B gates — validation checks before proceeding to extraction or comparison (visual-verification.md, SKILL.md)

### Changed
- **`ui-reverse-engineering`**: SKILL.md pipeline diagram now includes Phase 1 (reference capture) with gate checks; sub-document references changed from "see X" to "Read X, execute"
- **`ui-reverse-engineering`**: visual-verification.md restructured — separate C1/C2/C3 comparison tables replace single frame table; 60 fps replaces 2 fps
- **`ui-reverse-engineering`**: component-generation.md prerequisites now block generation if artifacts are missing

### Fixed
- README.md: typo fix (`not` → `not`)
- **`transition-reverse-engineering`**: css-extraction.md — added missing HTTPS validation for stylesheet download (consistent with other download commands)
- **`transition-reverse-engineering`**: SKILL.md — added selector validation guidance for `window.__scrub.setup()`

## [0.0.1] - 2026-03-19

### Added
- **`ui-reverse-engineering`** — full pipeline skill: URL → DOM/CSS/JS extraction → responsive breakpoints → React + Tailwind component → visual verification
  - URL input: exact values via `getComputedStyle`, DOM inspection, JS bundle analysis
  - Screenshot/video input: accepted as fallback, analyzed via Claude Vision (approximation)
- **`transition-reverse-engineering`** — precise animation extraction sub-skill
  - CSS path (transitions, keyframes) and Canvas/WebGL path (engine detection, bundle grep)
  - WAAPI scrubbing (`waapi-scrub-inject.js` + `capture-frames.sh`) for page-load animations that complete before capture
  - Frame-by-frame visual comparison workflow with named scopes: `element` (cropped to target) and `fullpage` (entire transition window)

### Changed
- `ui-reverse-engineering`: split into focused reference files — `dom-extraction.md`, `style-extraction.md`, `interaction-detection.md`, `component-generation.md`, `visual-verification.md`; `SKILL.md` is now a slim index
- Responsive breakpoints now extracted from actual CSS `@media` rules; fixed values (375/768/1440) are fallback only

### Fixed
- `waapi-scrub-inject.js`: `cancelAll()` now uses `document.getAnimations()` (Chrome 84+/FF 75+/Safari 14+) instead of full DOM walk; `seek()` calls `pause()` before `currentTime` assignment to avoid `InvalidStateError` on finished animations; selector warns when 0 elements matched; default easing changed `ease` → `linear` with extraction note; `onComplete` → `onfinish` in comments
- `capture-frames.sh`: last frame clamped to `TOTAL_MS` to avoid integer-division drift; `seek` eval now an IIFE that checks `__scrub` presence and surfaces JS errors (exit code alone is unreliable); warning added when `frames=1`
- `css-extraction.md`: division-by-zero fixed in easing curve extraction when sampled array has 1 element
- `canvas-webgl-extraction.md`: Lottie detection narrowed from all `.json` to `lottie`/`bodymovin` only; `md5sum || md5` fallback pipes through `awk '{print $1}'` to strip filename suffix on Linux; framework-agnostic chunk patterns added (Nuxt/Vite/Remix)
- `interaction-detection.md`: HTTPS validation added before bundle download; `className` sanitized before storing in `__scrollTransitions`; retrieve eval falls back to `|| []`
- `visual-verification.md`: bare `agent-browser eval "window.scrollTo(0,0)"` → IIFE form; Phase A/B responsive blocks now re-open in a fresh session before viewport change
- `component-generation.md`: `@keyframes` placement covers Next.js App Router, Vite/CRA, Tailwind v3, and Tailwind v4
- `style-extraction.md`: removed over-filtering of `normal`/`auto` values that silently dropped `fontWeight: normal`, `margin: auto`, etc.
- `dom-extraction.md`: depth limit comment added (increase to 6–8 for deep component trees like shadcn/MUI)
- `patterns.md`: `to()` library example replaced with plain WAAPI; troubleshooting updated to `onfinish` + `anim.cancel()`
- `SKILL.md` (transition): install path uses `CLAUDE_SKILLS_DIR` env var; Bug Diagnosis snippet uses proper IIFE with semicolons
