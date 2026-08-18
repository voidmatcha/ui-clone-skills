# Scroll Motion Evidence Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent failed scroll-runtime capture from masquerading as a static page, require captured motion sites to be accounted for, and ground generated motion instructions in canonical evidence.

**Architecture:** The runtime extractor produces an explicit success/error contract plus a bounded scroll audit. Specification and pre-generation gates consume that contract fail-closed. Generation-plan provenance and structured motion wires connect captured evidence to implementation, while the existing scroll-linked driver gains numeric blur replay.

**Tech Stack:** Bash, browser-side JavaScript, Python 3.11, pytest, generated React/TypeScript helpers, Markdown skill documentation.

---

## Task 1: Make runtime capture bounded and explicit

**Files:**

- Modify: `scripts/extract/extract-animation-runtime.js`
- Modify: `scripts/extract/extract-animation-runtime.sh`
- Modify: `tests/test_extraction_js_assets.py`

- [x] **Step 1: Add failing asset-contract tests**

Add tests that require the JavaScript asset to expose `captureStatus`, `captureError`, and `scrollAudit`; use a bounded initial-position list and adaptive cap; inspect `[style]` candidates rather than `querySelectorAll("*")`; record requested versus observed positions; and sample `filter`. Add wrapper assertions that command failure, empty stdout, and invalid JSON create `captureStatus: "error"` instead of a successful null dump.

- [x] **Step 2: Run the tests to prove the regression is red**

Run:

```bash
uv run python -m pytest tests/test_extraction_js_assets.py -q
```

Expected: FAIL because the extractor has no explicit capture contract and the wrapper collapses failures.

- [x] **Step 3: Implement the bounded sampler**

In `extract-animation-runtime.js`:

- detect Lenis, ScrollSmoother, or native scrolling;
- drive and read each engine through small helper functions;
- use initial positions `[0, .05, .1, .2, .35, .55, .75, 1]`;
- poll at most three times per position and add at most eight adaptive midpoints;
- scan `[style]` candidates plus nodes already observed to change;
- include `filter` in style samples;
- emit stable `sourceId` values for `scrollLinkedStyles` rows;
- emit `captureStatus: "ok"`, `captureError: null`, and a `scrollAudit` containing engine, max scroll, sample requests, observed positions, and drive method;
- emit `captureStatus: "error"` when a scrollable page never exhibits meaningful observed movement.

In `extract-animation-runtime.sh`, preserve the literal `eval --stdin < "$EVAL_JS"` invocation contract while capturing stdout, stderr, and the exit code. Atomically write a structured error artifact and exit non-zero for command failure, empty output, invalid JSON, or a payload whose status is not `ok`.

- [x] **Step 4: Run the focused tests green**

Run:

```bash
uv run python -m pytest tests/test_extraction_js_assets.py -q
bash -n scripts/extract/extract-animation-runtime.sh
```

Expected: PASS.

- [x] **Step 5: Commit the capture contract**

Stage only the three Task 1 files and use a Lore-format commit describing the fail-closed runtime-capture decision.

## Task 2: Block failed or unaccounted runtime evidence at the spec gate

**Files:**

- Modify: `ui_clone/gates/spec.py`
- Modify: `tests/gates/test_spec.py`

- [x] **Step 1: Add failing gate tests**

Add tests proving:

- a motion-rich reference fails when `captureStatus` is `error`;
- legacy `note: "eval returned empty"` fails for a motion-rich reference;
- a measured static page with `maxScroll: 0` is allowed;
- each successful runtime `sourceId` must appear in a transition-spec evidence field or a structured skipped entry;
- an uncovered runtime site reports its identifier in `spec-runtime-site-coverage`.

- [x] **Step 2: Run the focused tests red**

Run:

```bash
uv run python -m pytest tests/gates/test_spec.py -q
```

Expected: new tests FAIL.

- [x] **Step 3: Implement integrity and site-coverage checks**

Add small helpers in `ui_clone/gates/spec.py` to:

- identify explicit capture failure without rejecting compatible legacy success fixtures;
- use the existing motion-rich predicate for failure severity;
- verify meaningful movement only when a new `captureStatus: "ok"` audit is present and `maxScroll > 0`;
- collect stable runtime sites;
- match each site by exact `sourceArtifact` plus `sourceId`, with existing selector/target matching retained only as a compatibility fallback;
- accept a structured skipped entry containing `sourceArtifact`, `sourceId`, and nonempty `reason`.

Register the checks before the existing runtime motion and inventory coverage checks so diagnostics report the primary capture problem first.

- [x] **Step 4: Run the spec tests green**

Run:

```bash
uv run python -m pytest tests/gates/test_spec.py tests/test_runtime_spec_coverage_groups.py -q
```

Expected: PASS.

- [x] **Step 5: Commit the gate contract**

Stage only Task 2 files and commit with Lore trailers identifying compatibility constraints and test evidence.

## Task 3: Ground motion wires and stale plans when runtime evidence changes

**Files:**

- Modify: `ui_clone/gates/pre_generate.py`
- Modify: `ui_clone/dag.py`
- Modify: `scripts/extract/generation_plan.py`
- Modify: `tests/gates/test_pre_generate.py`
- Modify: `tests/test_generation_plan.py`
- Modify: relevant DAG/provenance test module discovered by `rg "generation_plan_source" tests`

- [x] **Step 1: Add failing grounded-wire and provenance tests**

Cover these cases:

- non-motion string wires remain valid;
- motion-like prose wires fail as ungrounded;
- structured motion wires require `kind`, `trigger`, `selector`, `sourceArtifact`, `sourceId`, and a list-valued `hooks` field;
- the referenced artifact must exist inside the reference directory and contain the cited `sourceId`;
- `animation-runtime-dump.json` appears in canonical generation-plan source hashes;
- modifying the runtime dump invalidates a previously current generation plan;
- runtime-derived component motion wires emitted by the deterministic planner use the structured schema.

- [x] **Step 2: Run the tests red**

Run:

```bash
uv run python -m pytest tests/gates/test_pre_generate.py tests/test_generation_plan.py -q
```

Expected: new tests FAIL.

- [x] **Step 3: Implement validation and structured emission**

In `pre_generate.py`, identify motion-like string wires conservatively and validate structured motion wires against their source artifact. Resolve artifact paths under the reference directory and search parsed JSON recursively for the exact source identifier.

In `dag.py`, add `animation-runtime-dump.json` to the canonical source list used by `generation_plan_source_hashes`.

In `generation_plan.py`, carry runtime `sourceId`, selector, trigger, library, and hook evidence into structured component `libraryWires`; do not synthesize a motion wire when there is no matching evidence row.

- [x] **Step 4: Run focused tests green**

Run:

```bash
uv run python -m pytest tests/gates/test_pre_generate.py tests/test_generation_plan.py -q
```

Then run the exact provenance test module identified in Step 1.

Expected: PASS.

- [x] **Step 5: Commit grounded planning**

Stage only Task 3 files and commit with Lore trailers.

## Task 4: Preserve and replay supported scroll filter curves

**Files:**

- Modify: `scripts/extract/generation_plan.py`
- Modify: `skills/visual-debug/scripts/lib/emit_scroll_helpers.py`
- Modify: `tests/test_generation_plan.py`
- Modify: `tests/test_emit_scroll_helpers.py`
- Modify: `tests/test_scaffold_stack_motion_drivers.py` if the generated driver integration needs coverage

- [x] **Step 1: Add failing filter replay tests**

Add runtime-series fixtures containing `filter: "blur(20px)"`, `filter: "blur(0px)"`, exact `blur(px) brightness(number)` sequences, and unsupported compound filters. Require the planner to emit numeric scrub bands only for supported stable sequences, require repeated identical non-latched rows to become `replay: "all-matches"` generation-plan sites while preserving `sourceIds[]`, and require the generated driver to apply the composed filter.

- [x] **Step 2: Run the tests red**

Run:

```bash
uv run python -m pytest tests/test_generation_plan.py tests/test_emit_scroll_helpers.py -q
```

Expected: new tests FAIL.

- [x] **Step 3: Implement conservative filter parsing and replay**

Parse only `none`, a single numeric `blur(<number>px)` function, or an exact numeric `blur(<number>px) brightness(<number>)` sequence. Add supported filter bands to the scrub property keys and emit a composed filter style in the scroll-linked driver. Preserve unsupported filter strings in runtime evidence without inventing interpolation.

- [x] **Step 4: Run the focused tests green**

Run:

```bash
uv run python -m pytest tests/test_generation_plan.py tests/test_emit_scroll_helpers.py tests/test_scaffold_stack_motion_drivers.py -q
```

Expected: PASS.

- [x] **Step 5: Commit filter replay**

Stage only Task 4 files and commit with Lore trailers.

## Task 5: Update the skill contract and verify on the benchmark site

**Files:**

- Modify: `skills/ui-reverse-engineering/SKILL.md`
- Modify: `skills/ui-reverse-engineering/animation-detection.md`
- Modify: `skills/ui-reverse-engineering/transition-spec-rules.md`
- Modify: `skills/ui-reverse-engineering/enrichment.md`
- Modify: documentation-contract tests discovered by `rg "enrichment.md|transition-spec-rules" tests`
- Refresh only generated benchmark artifacts under the active `scratch/<component>/workspace/tmp/ref/<component>/` directory that the canonical scripts own.

- [ ] **Step 1: Add or update skill-contract tests**

Require the docs to state that failed runtime capture must be rerun, runtime sites must map to transition entries or structured skipped reasons, and motion wires must cite `sourceArtifact` plus `sourceId`.

- [ ] **Step 2: Update the English-only skill documentation**

Document the new artifact fields, fail-closed gate, site-accounting rule, structured wire schema, and runtime dump provenance. Keep examples traceable and avoid duplicating canonical gate lists from `docs/gates.md`.

- [ ] **Step 3: Run targeted repository tests**

Run:

```bash
uv run python -m pytest tests/test_extraction_js_assets.py tests/gates/test_spec.py tests/gates/test_pre_generate.py tests/test_generation_plan.py tests/test_emit_scroll_helpers.py tests/test_runtime_spec_coverage_groups.py -q
```

Expected: PASS.

- [ ] **Step 4: Recapture the benchmark runtime with an owned browser session**

Open the target benchmark URL in a named, owned `agent-browser` session at `1280x800`, run the canonical wrapper, and close only that session. Require:

- wrapper completion within the browser command timeout;
- `captureStatus: "ok"`;
- at least three distinct observed positions when `maxScroll > 0`;
- nonempty runtime scroll-linked evidence or an explicit supported no-site result;
- no `eval returned empty` marker.

If the live site or browser daemon prevents capture, retain the structured error artifact and report the task as explicitly blocked rather than static or complete.

- [ ] **Step 5: Run canonical gates and project verification**

Run the smallest applicable reference gate first, then:

```bash
bash scripts/ci/ci-local.sh > /tmp/ui-clone-ci-local.log 2>&1
tail -40 /tmp/ui-clone-ci-local.log
bash scripts/ci/pre-push-security.sh > /tmp/ui-clone-security.log 2>&1
tail -40 /tmp/ui-clone-security.log
```

Expected: literal `ci-local: all checks passed` and zero security blockers. Report clone completion only if `pipeline verify`, `verify-stamp.json`, and `python -m ui_clone.goal --check-done` also prove it; otherwise report the exact remaining gate.

- [ ] **Step 6: Commit skill documentation and generated evidence separately**

Keep source/skill changes separate from scratch evidence. Do not commit unrelated user-owned changes.
