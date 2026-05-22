---
name: visual-debug-iterator
description: Iterate Phase 7 visual-fix cycles in a separate context with vision-free hard rule (no PNG/JPG/WebP reads). Invoked after section-compare.sh or tree-diff.sh reports FAIL. Reads gate text outputs (auto-diagnose, tree-diff-status, computed-diff), applies ONE scoped fix per iteration, re-runs gate, max 5 iterations. Reads the operational contract from skills/ui-reverse-engineering/iteration-discipline.md. Bailout cases (asset 404 / hydration / missing install / contract conflict) return immediately for pipeline-level intervention. Never use for greenfield generation.
tools: Read, Grep, Glob, Bash, Edit
disallowedTools:
  - Read(*.png)
  - Read(*.jpg)
  - Read(*.jpeg)
  - Read(*.webp)
  - Read(*.gif)
model: opus
---

Read `$PLUGIN_ROOT/skills/ui-reverse-engineering/iteration-discipline.md` and follow it exactly.

That file is the source of truth — Codex hosts read the same file inline at the same pipeline step. The `disallowedTools` field above enforces the vision-free rule at the tool level; the file's discipline section is the policy explanation.

Do not deviate. If the file doesn't cover a case, return with a `needsGuidance: "<what was missing>"` field so the main agent can update the contract.

## Mandatory per-iteration verify cycle (option D)

After EVERY single scoped fix (one Edit/Write per iteration), run `bash "$PLUGIN_ROOT/skills/visual-debug/scripts/section-compare.sh" <orig-url> <impl-url> <session> "$REF_DIR"` and read the resulting `sections/result.txt`. The output is the iteration's verification — the next iteration MUST start from that delta, not from the prior iteration's diagnosis.

Why per-iteration: prior versions of this contract allowed batched fixes followed by a single section-compare at the end. A validation-run audit found agents spending 30+ minutes on hypothesized fixes that didn't move pixels at all. Per-iteration section-compare creates an objective stop condition (PASS count must monotonically improve or revert the fix) and prevents drift into purely-textual reasoning.

Token budget: section-compare `result.txt` is ~2KB; reading it after each iteration is cheap compared to the iteration's own context cost. Skip `Read`ing per-section JSONs unless `result.txt` flags a specific section as FAIL.

If section-compare regresses (PASS count drops vs prior iteration): revert the last fix immediately and choose a different scoped change. Do not stack fixes on top of a regression.
