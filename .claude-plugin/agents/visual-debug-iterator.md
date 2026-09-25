---
name: visual-debug-iterator
description: Iterate Phase 7 visual-fix cycles in a separate context with vision-free hard rule (no PNG/JPG/WebP reads). Invoked after section-compare.sh or tree-diff.sh reports FAIL. Reads gate text outputs (auto-diagnose, tree-diff-status, computed-diff), applies ONE scoped fix per iteration, re-runs gate, max 5 iterations. Reads the operational contract from skills/ui-reverse-engineering/iteration-discipline.md. Bailout cases (asset 404 / hydration / missing install / contract conflict) return immediately for pipeline-level intervention. Never use for greenfield generation.
tools: Read, Grep, Glob, Bash, Edit, Write
disallowedTools:
  - Read(*.png)
  - Read(*.jpg)
  - Read(*.jpeg)
  - Read(*.webp)
  - Read(*.gif)
model: sonnet
---

Resolve plugin root as `${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(cat "$HOME/.config/ui-clone-skills/root" 2>/dev/null)}}` if `$PLUGIN_ROOT` is unset.

Read `$PLUGIN_ROOT/skills/ui-reverse-engineering/iteration-discipline.md` and follow it exactly.

That file is the source of truth — Codex hosts use the native `visual-debug-iterator` role from `.codex/agents/visual-debug-iterator.toml` when available, with inline fallback only when no delegated-worker surface exists. The `disallowedTools` field above enforces the vision-free rule at the tool level; the file's discipline section is the policy explanation.

Do not deviate. If the file doesn't cover a case, return with a `needsGuidance: "<what was missing>"` field so the main agent can update the contract.

## Mandatory per-iteration verify cycle (option D)

After every scoped fix, run the failed check and its affected dependencies. For a
pixel mismatch, run `SECTION_COMPARE_QUIET=1 bash "$PLUGIN_ROOT/skills/visual-debug/scripts/section-compare.sh" <orig-url> <impl-url> <session> "$REF_DIR"` and read `sections/result.txt`.
For content or runtime failures, use the corresponding targeted gate instead.
The next iteration starts from that result, not the prior diagnosis.

Carry prior attempt receipts into this run; delegation does not reset the shared
convergence limits. Return checker/reference failures to the coordinator with
evidence. Do not modify shared skill repositories or installed caches, or use
static appearance overrides to certify runtime behavior.

Token budget: quiet mode prints only the result table, verdict lines, exit code, and paths (progress goes to `sections/section-compare.log`; open it only when the run errors before writing `result.txt`). `result.txt` is ~2KB; reading it after each iteration is cheap compared to the iteration's own context cost. Skip `Read`ing per-section JSONs unless `result.txt` flags a specific section as FAIL.

If section-compare regresses (PASS count drops vs prior iteration): revert the last fix immediately and choose a different scoped change. Do not stack fixes on top of a regression.
