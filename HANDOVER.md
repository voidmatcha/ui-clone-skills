# Handover — deferred refactors from 2026-05-22 enforcement session

This is a fresh-session handover. Today's session (42 commits on
`origin/tmp`) added 15 new enforcement gates, the gate-dependency DAG,
SKILL.md "Hard Done Criteria" + full gate inventory, OKLab color
grounding, CHANGELOG split, and ~1090 historical-comment cleanup.
**The remaining items below were deferred because each is a 2–4-hour
refactor that needs a focused session with low context pressure.**

You (next-session agent) should read this file, pick **one item at a
time** (preferably in the order below), commit + push each on its own,
then continue down the list. **When every item is done, delete this
file** (`git rm HANDOVER.md && git commit -m "chore: remove HANDOVER
after refactor sweep complete"`).

---

## Working state at handover

- Branch: `tmp` (origin/tmp pushed up to commit "test: 2 functional duration-easing…")
- Tests: 401 passing
- CI: `bash scripts/ci/ci-local.sh --quiet` is green
- All 15 new gates wired into `verification-plan.sh` + dispatcher SIGNATURES
- Reference repo audit lives in commit f3df6ec4 (scratch leak removal)

## Ground rules for every item below

1. Make changes on `tmp` (or a `tmp-refactor-<item>` branch you merge into `tmp` after CI green).
2. Run `uv run python -m pytest -q` after each meaningful step.
3. Final per-item check: `bash scripts/ci/ci-local.sh --quiet` (must exit 0).
4. Commit per item with a clear message; push.
5. **Do not** combine items in one commit — they're independent enough that
   bisecting matters if anything regresses.
6. **Do not** modify gate behavior during a refactor — physical moves only,
   no logic changes. If you spot a behavior bug, write it down here and fix
   in a separate follow-up commit.

---

## Item 1 — `pipeline.py` Pipeline class slimming (start here, lowest risk)

**File**: `ui_clone/pipeline.py` (~730 lines, 14 methods)
**Risk**: 🟡 medium — `execute_phases` and `execute_verify` are each ~200
lines; the rest are small helpers.
**Approach**:

1. Read `Pipeline.execute_phases` and `Pipeline.execute_verify`.
2. Each delegates to phase-specific helper functions. Extract their
   inner loops into module-level functions (or per-phase modules under
   `ui_clone/pipeline_phases/phase_<N>.py`).
3. Keep `Pipeline` as the orchestrator that dispatches to the new
   per-phase functions.
4. Verify `tests/test_pipeline.py` (666 lines) still passes unchanged.

**Done when**: `pipeline.py` ≤ 400 lines and `tests/test_pipeline.py`
passes without modification.

---

## Item 2 — `bundle-extraction.sh` embedded Python extraction

**File**: `scripts/extract/bundle-extraction.sh`
**Risk**: 🟡 medium — extraction pipeline relies on this; breaking it
breaks Phase 2 for every site.
**Approach**:

1. Locate the embedded `python3 - <<'PY' … PY` block.
2. Move the Python body to `scripts/extract/_bundle_extraction.py` as
   a module with a `def main(argv): …` entry point.
3. Have the shell script call `python3 -m scripts.extract._bundle_extraction "$@"`
   (or `python3 "$REPO_ROOT/scripts/extract/_bundle_extraction.py" "$@"`).
4. Write 2–3 unit tests against the new module (parse a fixture
   bundle, assert specific extractions).
5. Run end-to-end: `python -m ui_clone.pipeline run https://example.com test 2-2`
   (or similar) to confirm Phase 2 still emits `bundle-map.json`.

**Done when**: shell script is a thin wrapper, Python is unit-testable,
end-to-end Phase 2 still produces the same artifact shape.

---

## Item 3 — README.md trim & restructure

**File**: `README.md` (523 lines / 40KB)
**Risk**: 🟢 low — markdown only; main risk is that the marketplace
preview might look worse, so review the rendered output locally
(`gh markdown-preview` or paste into a GitHub gist).
**Approach**:

1. Keep these sections short and **above the fold** (≤ 250 lines combined):
   - One-line tagline + decision tree
   - Design principles (≤ 10 bullets)
   - Skills overview (3 skills, 2-3 lines each)
   - Install one-liner + verification check
   - Quickstart (3 commands)
   - Link to deeper docs
2. Move the deep-dive sections (`ui-reverse-engineering` Phase 1-10
   detail, Token management, Security, Responsible use) to dedicated
   files. **NOTE: `docs/` is gitignored** — use `docs_site/` or
   `README_detail/` at repo root.
3. Update internal anchors and any links pointing at those sections.
4. `gh markdown-preview README.md` before pushing.

**Done when**: root README ≤ 250 lines, deep content lives in
discoverable per-topic files, all relative links resolve.

---

## Item 4 — `pre_bash.py` decomposition

**File**: `ui_clone/hooks/pre_bash.py` (1144 lines)
**Risk**: 🟡 medium — independent rules; the existing structure is
already amenable to a registry pattern.
**Approach**:

1. Read the file. The rules tend to be self-contained `_check_*` /
   `_is_*` / `_violates_*` style functions.
2. Create `ui_clone/hooks/pre_bash_rules/` with one file per rule
   family (e.g., `impl_scaffold.py`, `repo_root.py`, `scratch_nested.py`).
3. Each module exports a callable returning either `None` (no
   violation) or a `(severity, message)` tuple.
4. The top-level `pre_bash.py` becomes a thin dispatcher that imports
   each rule and runs them in order, short-circuiting on the first
   block-severity violation.
5. `tests/test_hooks.py` (3054 lines) covers most paths — keep tests
   unchanged; the public hook interface must not change.

**Done when**: `pre_bash.py` ≤ 300 lines, individual rules live in
their own files, `tests/test_hooks.py` passes without modification.

---

## Item 5 — `gate.py` god-class decomposition (largest item, highest leverage)

**File**: `ui_clone/gate.py` (3018 lines, 36 methods, 133KB)
**Risk**: 🔴 **HIGH** — cross-method `self._check_*` dependencies; if
you move a method without identifying its private-helper deps, the
gate breaks at runtime, not at test time.

**Reference**: `tests/test_gate.py` is 3954 lines / 158KB and exercises
most gate methods. Run frequently during the refactor.

**Approach (suggested by codex audit on 2026-05-22)**:

1. **Map cross-method calls first** (do not skip this step):
   ```
   uv run python -c "
   import ast
   src = open('ui_clone/gate.py').read()
   tree = ast.parse(src)
   for node in ast.walk(tree):
       if isinstance(node, ast.ClassDef) and node.name == 'Gate':
           for fn in node.body:
               if isinstance(fn, ast.FunctionDef):
                   # Find self.<method> calls inside fn
                   calls = []
                   for n in ast.walk(fn):
                       if (isinstance(n, ast.Attribute) and
                           isinstance(n.value, ast.Name) and
                           n.value.id == 'self'):
                           calls.append(n.attr)
                   print(f'{fn.name}: {sorted(set(calls))}')
   "
   ```
   Save the output. This is the **dependency map**.

2. **Plan the split** based on the map. The 10 `gate_*` methods
   correspond 1:1 to the dispatch keys in `docs/gates.md`:
   `reference`, `extraction`, `bundle`, `paid-features`, `spec`,
   `pre-generate`, `post-implement`, `boundary`, `font-parity`,
   `section-compare`. Each gets its own module.

3. **Suggested target structure**:
   ```
   ui_clone/gates/
     __init__.py            # re-export Gate + dispatch
     base.py                # CheckResult, _load_json, check_file/dir,
                            # _check_artifact_provenance — anything used
                            # by ≥ 2 gates
     dispatch.py            # _make_dispatch, _dispatch, _check_pipeline_
                            # state_prerequisites
     reference.py           # gate_reference + only its private helpers
     extraction.py
     bundle.py
     paid_features.py
     spec.py
     pre_generate.py
     post_implement.py
     boundary.py
     font_parity.py
     section_compare.py
     verification_plan.py   # _check_verification_plan (lines 1704-2222
                            # of current file — its own 518-line monster)
   ```

4. **Move strategy**: do it gate by gate, one commit per gate:
   - Copy the method body and its exclusive helpers to the new module.
   - In the old file, replace the method body with `from .gates.<name>
     import gate_<name> as _gate_<name>` and `gate_<name> = _gate_<name>`
     (class-level rebinding).
   - Run `uv run python -m pytest tests/test_gate.py -q` after each move.
   - Commit. Move on to the next gate.

5. **After all 10 gates are moved**, decide whether to keep the
   `Gate` class as a thin shim or replace it with a module-level
   `dispatch()` function. The shim is lower-risk — keep it.

6. **`tests/test_gate.py`** will likely split naturally to
   `tests/gates/test_<gate>.py` after the source is split. Do that
   in a separate commit after Item 5 is otherwise complete.

**Done when**: `ui_clone/gate.py` ≤ 300 lines (just imports + thin
shim class), each gate has its own module ≤ 500 lines, all
`tests/test_gate.py` tests pass without modification.

---

## Item 6 — Test file splits (do last, purely cosmetic)

**Files**:
- `tests/test_gate.py` (3954 lines, ~158KB)
- `tests/test_hooks.py` (3054 lines, ~127KB)
- `tests/test_measure.py` (3471 lines, ~123KB)

**Risk**: 🟢 low (cosmetic / token-cost optimization).

**Approach**: After Item 5 (gate.py split), the natural test split
follows. For each: identify test clusters by their `test_<prefix>` and
move into `tests/<area>/test_<cluster>.py`. Move shared fixtures
(`_project_root`, `_run_script`, etc.) into `tests/conftest.py`.

**Done when**: each test file ≤ 1000 lines, `pytest` discovers + runs
the same number of tests as before.

---

## When all 6 items are done

1. Run the full local CI one last time: `bash scripts/ci/ci-local.sh`.
2. Verify the test count is unchanged or grew (it should never
   shrink — that means tests were lost).
3. Delete this handover:
   ```
   git rm HANDOVER.md
   git commit -m "chore: remove HANDOVER after refactor sweep complete"
   git push origin tmp
   ```
4. Optionally PR the `tmp` branch to `main`.

---

_Generated 2026-05-22 by the enforcement-session handover._
