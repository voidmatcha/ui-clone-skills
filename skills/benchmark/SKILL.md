---
name: benchmark
description: |
  Local-only regression / benchmark skill for ui-clone-skills maintainers.
  Drives the standard ui-reverse-engineering pipeline against the canonical
  reference site (https://realfood.gov) and records AE/SSIM, iteration count,
  gate fail counts, and outcome to benchmark/history.csv so prompt /
  sub-doc / model-version drift surfaces as a trend.

  Trigger phrases: "run benchmark" / "regression benchmark" / "benchmark
  clone". The Makefile no longer has a `benchmark` target — setup is inline
  bash in this skill (Step 1 below).

  Internal: NOT registered in `.claude-plugin/plugin.json` `skills`. Not part
  of the public 3-skill marketplace surface. Maintainer tooling only.
metadata:
  filePattern:
    - "**/benchmark/history.csv"
    - "**/benchmark/history/**"
  bashPattern:
    - "benchmark-harvest\\.sh"
  priority: 70
---

# benchmark — local regression / benchmark skill

## ⚡ Activation sentinel (READ FIRST)

When this skill is loaded into the session and you act on its trigger ("run
benchmark" or equivalent), the FIRST LINE of your response MUST be exactly
the following token, with no decoration:

```
[BENCHMARK-SKILL-ACTIVE v1 / skills/benchmark/SKILL.md]
```

This sentinel exists purely so the maintainer can verify with one round-trip
whether `--plugin-dir` discovery picked up this internal skill from the dev
clone. If you ever respond to a benchmark trigger WITHOUT this token, the
maintainer cannot tell whether the skill is loaded — emit it every time, even
if the rest of your response is a clarifying question.

## Trigger

Maintainer asks the agent to "run benchmark". This is not a public skill — it
is invisible to contributors using the plugin and is not registered in either
host's marketplace manifest.

## Why this exists

`tests/` covers Python regression (gates, hooks, DAG — 360+ tests). It does
not cover: SKILL.md / sub-doc prompt drift, agent-browser version drift, real
reference-site DOM drift, or Anthropic / OpenAI model version drift. This
skill closes that gap by running the actual pipeline end-to-end against a
canonical site and recording metrics across runs.

## Architecture

**You (the LLM) drive the entire loop.** Python provides verification (`goal.py`,
`gate.py`, `measure.py`) and post-run metrics (`benchmark-harvest.sh`) only.
There is no external loop driver, no static prompt re-injection, no harness
choosing your next move. You iterate inside this Claude Code session using
your normal tool-use pattern until every STRICT v2 stop condition is met,
then you stop.

This intentionally mirrors how real users invoke the `ui-reverse-engineering`
skill — single session, agent-driven, verification gates surface failures so
you know what's left.

## Procedure

### Step 1 — Setup (inline bash, run once at start)

```bash
SHA=$(git rev-parse --short HEAD)
WORK_DIR="benchmark/work/${SHA}"
REF_DIR="${WORK_DIR}/ref"
IMPL_DIR="${WORK_DIR}/impl"
# Wipe any prior run at the same SHA for a clean baseline.
rm -rf "$WORK_DIR"
mkdir -p "$REF_DIR" "$IMPL_DIR" tmp/ref
# Symlink so /ui-capture (hard-coded to tmp/ref/<component>) lands in our work dir.
ln -sfn "$(pwd)/$REF_DIR" tmp/ref/realfood
date +%s > "$REF_DIR/.benchmark-start"
echo "Work dir: $WORK_DIR"
```

### Step 2 — Drive the ui-reverse-engineering pipeline

Source URL: `https://realfood.gov`. Component name: `realfood`. STRICT path policy
— use EXACTLY component name `realfood` and ref dir EXACTLY
`benchmark/work/<sha>/ref` (already symlinked from `tmp/ref/realfood`). If you
catch yourself typing `realfood-main` or any variant, STOP — `benchmark-harvest.sh`
reads the canonical ref dir only, any other location is invisible to metrics.

Follow the normal ui-reverse-engineering pipeline:

- **Phase 1 (capture)** — if `<ref>/static/ref/` has fewer than 5 PNGs:
  ```
  /ui-capture https://realfood.gov '' realfood
  ```
  Populates sections + scroll video + regions.json into `<ref>` via the symlink.

- **Phase 2 — extraction** (DOM, CSS, bundles, fonts, paid features).

- **Phase 2.5 — asset transfer (MANDATORY, not just cataloging)**:
  ```bash
  bash scripts/extract/extract-assets.sh realfood-bench "$REF_DIR" "$IMPL_DIR/public"
  ```
  Downloads ref images / fonts / videos to `impl/public/`. Without this step
  the impl renders placeholder boxes for every image and section-compare AE
  explodes to 1M+ on every section. Also parse `<ref>/visible-images.json`
  and reference any non-CDN URLs in your generated code.

- **Phase 3 — spec** (transition-spec, verification-plan).

- **Phase 4 — pre-generate + scaffold**: if `<impl>` is empty, scaffold a
  Next.js project:
  ```
  npx create-next-app@latest "$IMPL_DIR" --typescript --tailwind --app \
      --src-dir --no-eslint --use-npm --no-import-alias --yes
  ```
  Then generate the cloned component there, REFERENCING the downloaded assets
  in `public/` (not placeholder rectangles). Split into per-section components
  under `src/components/` — `componentization` gate fails when `page.tsx` >
  200 LOC AND components/ < 3.

- **Phase 5 — verification**: run `npm run dev` (background, capture port),
  then run section-compare, tree-diff, transition-compare against the local
  impl URL. Use `python -m ui_clone.measure` to invoke the comparison scripts
  with locked default env (`EXCLUDE_DYNAMIC=1`, `SECTION_THRESHOLD=2000`) so
  the classifier can't be tuned to mask gaming.

- **Phase 5b — visual-judge iteration (when section-compare fails)**:
  When `sections/result.txt` has FAIL rows with high AE/Mpx, the AE signal
  itself is a dead gradient (every section ~950k, no direction). DO NOT
  quit. `python -m ui_clone.goal <ref-dir>` will route you through
  `skills/visual-debug/scripts/visual-judge.sh`, which calls a multimodal
  LLM on each ref-clip vs impl-clip pair and emits actionable findings
  (`category`, `severity`, `selector_hint`, tailwind suggestions).
  Apply the `priority_fix` from each `visual-judge-<section>.json` to
  `impl/src/components/<Name>.tsx`, re-run section-compare, re-route via
  `python -m ui_clone.goal`. Repeat until result.txt has 0 FAIL rows or
  the per-section AE/Mpx drops below the section-compare critical threshold.

After every chunk of work, route the next action via:

```bash
python -m ui_clone.goal "$REF_DIR"
```

The goal card emits one bounded "Next action" string based on
`pipeline-state.json.current_gate` and any blocking gate failures. Run that
next action, then re-route.

### Step 3 — STRICT v2 stop conditions

You may emit "DONE" and stop ONLY when EVERY condition below is true:

- **Structure**: `impl/src/app/page.tsx` < 200 LOC AND `impl/src/components/`
  has > 3 .tsx files.
- **Section-compare**: `pipeline-state.json.gate_fail_counts == {}` AND
  `current_gate == "done"`. result.txt has 0 ❌ FAIL rows, 0 MISSING impl
  rows, NO `STRUCTURAL_ONLY` rows whose `structure-diff.json` severity is
  critical or major-with-height-ratio<0.5, and **no more than 50% of rows
  marked STRUCTURAL_ONLY** (gate fires "structural-only excess" when
  substitution covers more than half the page).
- **SECTION_THRESHOLD integrity**: every `minor` / `ok` row in result.txt
  has AE/Mpx ≤ 2000 (the gate detects classifier inflation and fails it).
- **tree-diff convergence**: `tree-diff-status.json.status == "pass"` AND
  `elements_walked >= max(30, section_count * 5)` (the gate enforces the
  floor — a near-empty impl that walks 11 elements does not count).
- **Motion**: `transitions/result.txt` exists AND has 0 ❌ FAIL rows.
  If `transition-spec.json` declares any transitions, `result.txt` must
  contain at least one ✅/❌ measurement row (the gate fails an empty
  artifact as "transition-compare never ran").
- **Composition**: `bundle-impl-coverage.json.status == "pass"` (every lib
  detected in `bundle-map.json` is installed in `impl/package.json`) AND
  `asset-utilization.json.status == "pass"` with `downloaded >= 5`.

The verifier is:

```bash
python -m ui_clone.goal "$REF_DIR" --check-done
```

Exits 0 when all gates pass, 2 if pipeline-state has unclonable_reasons,
1 otherwise. Do not declare DONE before this exits 0.

### Step 4 — Harvest metrics

After you successfully stop (or after you decide the run cannot make further
progress), run:

```bash
bash skills/benchmark/scripts/benchmark-harvest.sh "$REF_DIR"
```

Reads `pipeline-state.json` + `sections/result.txt` + the `.benchmark-start`
marker, writes `benchmark/history/<timestamp>-<sha>.json` plus a one-row
append to `benchmark/history.csv`, prints a delta-vs-previous summary.

### Step 5 — Inspect the delta

A worsening trend on `ae_avg`, `iterations_to_done`, or new
`unclonable_reasons` means a regression in the prompt / sub-doc / external
dependency stack. Investigate before the next release.

## Outcome values

| Outcome | Meaning |
|---|---|
| `DONE` | All STRICT v2 stop conditions satisfied; `--check-done` exit 0. |
| `ABORT` | `pipeline-state.json.unclonable_reasons[]` non-empty (paid font, DRM canvas, auth-gated). Records the reason but no AE/SSIM. |
| `INCOMPLETE` | Catch-all for "not done, not aborted" — agent decided the run cannot make further progress (cost cap, persistent failure, etc.). Inspect `pipeline-state.json.completed_gates` + `gate_fail_counts` for which gate halted. |

## Headless / CI path (optional)

`ui_clone/benchmark_harness.py` invokes `claude --print` per-iter with focused
prompts and Python-side stop checks. Useful for unattended cron / CI where
no interactive Claude Code session exists. **Not the canonical entry point**
— this skill is the canonical entry. The harness is a separate Python module
you invoke directly if you need it:

```bash
python -m ui_clone.benchmark_harness "$REF_DIR" --impl-dir "$IMPL_DIR" \
    --orig-url https://realfood.gov --impl-url http://localhost:3000 \
    --max-iter 100 --token-budget 500000 --wall-budget-s 14400
```

## Metrics in `benchmark/history.csv`

```
timestamp,sha,outcome,iterations,wallclock_s,ae_avg,ssim_avg,gate_fail_total,unclonable_count
2026-05-16T00:00:00Z,abc123,DONE,12,1840,0.023,0.94,4,0
```

## What this skill is NOT

- Not a CI tool. Lives entirely in the maintainer's local session.
- Not a model benchmark. Measures the *pipeline's* behavior against a fixed
  site, mixing prompt + model + ref-site contributions. Cross-version model
  A/B requires separate methodology.
- Not contributor-facing. Contributors can ignore this skill entirely; their
  workflow uses only `ui-reverse-engineering`, `ui-capture`, `visual-debug`.

## When NOT to run

- Mid-clone of a real user component (would interleave with the active
  pipeline state).
- Without an LLM session (the skill needs the agent to actually drive the
  clone — pure shell cannot).

## Storage policy

`benchmark/history.csv` and `benchmark/history/` are **gitignored**. Trend
data lives only on the maintainer's machine. If you want to share trend,
export and attach manually.
