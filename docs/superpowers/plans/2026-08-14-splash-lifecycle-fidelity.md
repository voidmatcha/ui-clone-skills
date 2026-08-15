# Splash Lifecycle Fidelity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make first-load splash overlays an extracted, generated, and fail-closed verified runtime contract, then restore the missing Realfood splash from measured reference evidence.

**Architecture:** `capture-states.sh` records overlay identity, coverage, animation activity, and exit timing in `states/splash/contract.json`. `generation_plan.py` treats that contract as a mandatory `IntroAnimation` input, while a runtime lifecycle check proves that the implementation mounts, animates, and removes a comparable overlay before SSIM calibration is allowed to corroborate timing. Realfood uses the existing Framer Motion dependency and reference CSS/assets; no site bundle is loaded.

**Tech Stack:** Bash, Python 3.9, pytest, agent-browser, React 19, TypeScript, Framer Motion, Vite.

---

### Task 1: Extract a structured splash lifecycle contract

**Files:**
- Modify: `scripts/extract/capture-states.sh`
- Modify: `scripts/extract/state-structure-spec.py`
- Test: `tests/test_capture_states.py`
- Test: `tests/test_state_structure_spec.py`

- [x] Add a failing capture-state test whose initial and settled states keep identical html/body classes but change a full-screen overlay from present to absent. Assert `states/splash/contract.json` contains `detected: true`, the overlay selector, maximum coverage, animation activity, media fingerprint, and exit time.
- [x] Run `uv run python -m pytest tests/test_capture_states.py -q` and confirm the new assertion fails because `contract.json` is absent.
- [x] Change the in-page probe so `computeState()` carries an explicit overlay observation and active animation count instead of burying the overlay count only inside `compositeDigest`; write schema-versioned `contract.json` from the trajectory bookends.
- [x] Add a failing state-structure-spec test asserting the splash event links `states/splash/contract.json` and preserves its overlay selector/lifecycle fields, then update the generator minimally.
- [x] Run both test modules and confirm they pass.

### Task 2: Make generation require a real intro coordinator

**Files:**
- Modify: `scripts/extract/generation_plan.py`
- Test: `tests/test_generation_plan.py`
- Modify: `skills/ui-reverse-engineering/SKILL.md`

- [x] Add a failing generation-plan test with a classless splash contract and empty `animation-init-styles.json`. Assert `introAnimation.required` is true and carries `sourceArtifact`, `overlaySelector`, `visibleDurationMs`, and `requiresOverlay`.
- [x] Run the single pytest node and confirm current output incorrectly reports `required: false`.
- [x] Derive `introAnimation` from either non-trivial animation init state, a detected splash contract, or an evidence-backed page-load transition; include `states/splash/contract.json` in provenance hashes.
- [x] Update the skill generation rule so page-load overlays require both the coordinator and overlay DOM/media, not only final-state hero reveals.
- [x] Run `tests/test_generation_plan.py` and the skill contract tests.

### Task 3: Add fail-closed splash runtime verification

**Files:**
- Create: `skills/visual-debug/scripts/lib/splash-lifecycle-probe.js`
- Create: `skills/visual-debug/scripts/splash-lifecycle-check.sh`
- Modify: `skills/visual-debug/scripts/verification-plan.sh`
- Modify: `ui_clone/check_inputs.py`
- Modify: `ui_clone/dag.py`
- Test: `tests/gates/test_verification_plan_a2.py`
- Test: `tests/test_run_required_checks.py`
- Create: `tests/test_splash_lifecycle_check.py`

- [x] Add failing plan/dispatcher tests asserting `hasSplash=true` registers and dispatches a block-severity `splash-lifecycle-check` that produces `splash-lifecycle.json`.
- [x] Add a two-sided script test: reference trace mounts/moves/unmounts an overlay while implementation trace has none, which must fail; equivalent lifecycle traces must pass.
- [x] Implement the init-script probe so it starts before navigation, records overlay coverage/selector/media/active-animation deltas, and never infers success from HTTP status or settled screenshots.
- [x] Implement the check to require reference-observed overlay presence, implementation presence, at least one implementation lifecycle/motion delta, and an exit state; retain live ref/impl measurements in the JSON artifact.
- [x] Wire the check into plan generation and required-check dispatch, then run the focused tests.

### Task 4: Prevent unbounded splash arc calibration

**Files:**
- Modify: `scripts/verify/lib/frame-align.sh`
- Test: `tests/test_splash_arc_and_series.py`

- [x] Add a failing regression for the observed Realfood arcs (`ref=156`, `impl=48`, `refcal=383`) and assert calibration cannot rescue the implementation.
- [x] Keep the measured legitimate cold/warm 41-frame jitter regression passing.
- [x] Bound the usable calibration noise while still allowing an implementation that is within the ordinary tolerance of either reference recording.
- [x] Run the splash arc/distribution suite and confirm both positive and negative cases pass.

### Task 5: Restore the Realfood splash

**Files:**
- Create: `scratch/realfood-gov-clean-20260812/workspace/impl/src/components/SplashOverlay.tsx`
- Modify: `scratch/realfood-gov-clean-20260812/workspace/impl/src/App.tsx`
- Copy: exact reference broccoli/steak/milk assets into `scratch/realfood-gov-clean-20260812/workspace/impl/public/images/intro/`
- Test: live browser lifecycle receipt against `<reference-url>` and `<implementation-url>`

- [x] Record the current failing live receipt: reference overlay present/moving/removed and implementation overlay absent with zero active animations.
- [x] Build the overlay with the existing reference CSS-module class tokens and exact local assets. Use Framer Motion for entry, overlay exit, page offset release, hero-content reveal, and unmount timing measured from the reference.
- [x] Expose deterministic lifecycle state markers without changing the public timeline; reduced-motion hides the overlay as the reference CSS does.
- [x] Build `dist`, confirm the existing port 4174 server serves the new asset hashes, and run a fresh-session lifecycle comparison.

### Task 6: Adversarial review and canonical closeout

**Files:**
- Review all files changed by Tasks 1-5; do not touch the unrelated dirty `emit_scroll_helpers` pair.

- [x] Ask Claude for an independent Fable-style review focused on false-pass and false-fail counterexamples; reproduce any actionable finding before editing.
- [x] Reproduce the remaining fully masked hero-video blocker and prove that the frozen crop contains only a native video surface while separate block-severity media and structural checks remain declared.
- [x] Reuse the existing `STRUCTURAL_ONLY` status only after strict AE passes, recognized media covers at least 99.5%, ref/impl media structure and per-kind multiplicity match tightly, and kind-specific live proof, `video-motion-compare`, plus a structural anchor are all required; keep every weaker case fail-closed.
- [x] Re-run the focused Fable review and receive `APPROVE` with no findings; run the focused pytest modules, Ruff, mypy, Bash syntax, ShellCheck, and `git diff --check` against the reviewed diff.
- [x] Re-run `bash scripts/ci/ci-local.sh` and `bash scripts/ci/pre-push-security.sh` with durable logs.
- [x] Regenerate Realfood required checks, run `pipeline verify --json`, confirm `verify-stamp.json`, and run `goal --check-done` to exit 0.
- [x] Commit only intended repo files with a Lore-protocol message; leave the preview and Tailscale link running.
