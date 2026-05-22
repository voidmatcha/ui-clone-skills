# Visual-debug iteration discipline

**Audience**: anyone (host-agnostic) iterating Phase 7 fix cycles on a failed section-compare or tree-diff.

- **Claude Code path**: invoked via `visual-debug-iterator` sub-agent (`.claude-plugin/agents/visual-debug-iterator.md`). The sub-agent reads this file as its operational contract. The sub-agent's `disallowedTools` field enforces the vision-free rule by blocking `Read(*.png)` etc.
- **Codex path**: read this file inline at Step 8b on FAIL per `.codex-plugin/plugin.json defaultPrompt`. The vision-free rule is policy, not tool-level — DO NOT `Read` PNG/JPG/WebP files in the main context either.

## Pre-condition

`section-compare.sh` or `tree-diff.sh` reported FAIL (`FAIL_COUNT > 0`, `INCOMPLETE`, or `🌑 saturated`). An impl is running and the gate output is in `tmp/ref/<component>/sections/result.txt` or `tree-diff-status.json`.

## Inputs

- `tmp/ref/<component>/sections/result.txt` — section-compare table (PASS / FAIL / STRUCTURAL_ONLY / SKIP per section)
- `tmp/ref/<component>/sections/matches.json` — pair-by-position matches with score
- `tmp/ref/<component>/tree-diff.json` + `tree-diff-status.json` — DOM walk diffs (counts, top critical/major/layout-major rows)
- `tmp/ref/<component>/transitions/` — hover/scroll/timer transition compare outputs
- `tmp/ref/<component>/generation-plan.json` — current contract (consult for sticky strategy, library set, signature effects)
- `impl/src/` — the implementation source (read freely; edit only what the failing row points at)

## Discipline

1. **VISION-FREE — strict.** Do NOT `Read` any `.png` / `.jpg` / `.jpeg` / `.webp` / `.gif` file. The plugin's value prop is "near-zero vision tokens"; reading diff images here defeats the entire purpose AND introduces Claude Vision interpretation variance. Use the text-based signals in order:
   - **1st: `auto-diagnose.sh`** — `bash $PLUGIN_ROOT/skills/visual-debug/scripts/auto-diagnose.sh "$(pwd)/tmp/ref/<component>" <impl>` returns root-cause class + impl file:line, all text.
   - **2nd: `tree-diff-status.json` + `tree-diff.json`** — DOM/style mismatches in text form (display, flex-direction, position, dimensions, font props). This catches structural fails that pixel diff can't explain.
   - **3rd: `computed-diff.sh`** — per-element computed-style comparison, text only.
   - **4th: `diagnosis.md` catalog (Root Cause A-R)** — classify by symptom, apply by class.
   - **Last resort (only if all above return "nothing actionable"):** request main agent to escalate — do NOT read the PNG yourself.
2. **One fix per iteration.** Pick the highest-severity row (🌑 saturated > critical > major > layout-major > minor). Identify the specific impl file + DOM node from the text signals above. Apply a SCOPED edit (single component or single style rule). Re-run the gate.
3. **Substitution-aware.** STRUCTURAL_ONLY (substituted) rows are not failures — skip them. Focus on PASS-blocking rows only.
4. **Gate re-run.** After every edit, re-run `section-compare.sh` (or `tree-diff.sh` for non-pixel fails) — never assume the fix works without verification. The summary line MUST include the gate's exit code, not just `PASS`.
5. **Max 5 iterations.** If 5 consecutive iterations don't reduce FAIL_COUNT, return with a "blocked" verdict naming the section + suspected root cause. The main agent decides whether to escalate.
6. **Contract preservation.** Edits must not violate `generation-plan.json` — do not swap libraries, restructure components, or change architecture layers. Stay within "scoped style/JSX/data fix." If a fix would require contract change, return with `blocked-contract-conflict`.

## Asset substitution policy (research-mode default)

**Plugin philosophy**: this is a local-use clone tool (research / benchmark / personal study) — NOT a publication pipeline. License flags from `paid-features-detect.sh` are advisory, not blockers. The default behavior is **download everything**; substitute only when an HTTP request genuinely fails.

- **MANDATORY before any substitution:** run `bash $PLUGIN_ROOT/scripts/extract/asset-download.sh "$(pwd)/tmp/ref/<component>" "<impl-public-dir>"` to attempt every URL in `visible-images.json`. The script writes `download-log.json` with HTTP status per attempt. Substitution declaration is rejected (by `asset-transfer-check.sh` + `generation-plan.sh` validator) unless an entry has a corresponding failed download attempt.
- **NEVER substitute images with `emoji-or-gradient` / `emoji` / `gradient` / `placeholder` / `stub`.** These wreck visual fidelity. Banned by `scripts/extract/generation-plan.sh` validation.
- **Concrete substitution targets only.** When download genuinely fails, replacement must be a real alternative path (free font family, CC0 image URL, brand-equivalent stock asset) — never a generic placeholder string.
- **Public-domain TLD short-circuit:** `.gov` / `wikimedia.org` / `wikipedia.org` / `commons.wikimedia.org` images are by-default downloadable. If `asset-download.sh` reports 0 succeeded for these, the network or capture is broken — investigate before declaring substitution. Agent self-assessed "looks USDA-licensed" is NOT evidence; `.gov` IS public domain.
- **Commercial fonts in research mode:** Die Grotesk, PP Neue Montreal, etc. — fetch + use the self-hosted .woff2 directly via `asset-download.sh`. This is permissible for local research / benchmark fidelity (no publication). Substitution to free font is opt-in for users who plan to publish.

## Convergence requirement (AE-first principle)

The terminal goal is NOT gate-pass — it is **AE convergence to ref**. Every fix iteration must demonstrate measurable AE reduction on at least one previously-failing section. Agent reasoning, substitution declarations, library installs, and component rewrites are all means to this end. If they don't lower AE, they don't count.

- **Record per-iteration AE delta**: after each fix, compare current `sections/result.txt` AE/Mpx values against the previous iteration's. Write `iter N: section=<name> ae_prev=<X> ae_now=<Y> delta=<-Z%>` to the verdict summary.
- **No-progress detection**: if 2 consecutive iterations show ≤5% AE reduction on the target section (or worse — AE increased), classify as `blocked-no-convergence` and return. Don't keep editing — the fix strategy is wrong.
- **Gate-pass without AE reduction is INVALID**: agents that game STRUCTURAL_ONLY / wildcard substitution / impl-rename to bypass measurement violate this principle. Return with `blocked-gate-game` if the gate passes but section-compare AE delta is 0 across all sections.
- **Final verdict requires AE evidence**: `verdict: PASS` must include `total_ae_reduction: <%>` and `sections_with_ae_drop: <N>/<total>`. Without these numbers, the verdict is not honored by the main agent.

This applies recursively to any sub-agent reasoning chain. Substitution declarations, library swaps, refactor decisions — each must trace back to a measurable AE outcome. Reasoning that "looks right" but doesn't move AE is wasted iteration.

## Bailout cases (return immediately)

- **Asset 404**: missing image at expected path → return with `fixType: "asset-transfer"`, the main agent re-runs asset-transfer-check.sh
- **Hydration error**: console reports React hydration mismatch → return with `fixType: "ssr-mismatch"`
- **Library missing**: gate output references "lenis is not defined" or similar → return with `fixType: "missing-install"`, the main agent installs the package
- **Contract conflict**: fix requires a `generation-plan.json` change (library swap, component delete) → return with `fixType: "contract-conflict"`

These are out-of-scope for visual iteration; they need pipeline-level intervention.

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
```

## Don'ts

- Don't `Read` PNG / JPG / WebP / GIF — vision-free hard rule.
- Don't edit the gate scripts themselves to "make it pass".
- Don't add `// eslint-disable` or skip-tests to dodge the gate.
- Don't re-architect sections — that's the main agent's job. You're a fix-iterator, not a refactorer.
- Don't violate `generation-plan.json` contract — return with `contract-conflict` instead.
