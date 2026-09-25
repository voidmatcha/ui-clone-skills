# Context recovery — Step 0

Read after compaction or when existing evidence and live state conflict.

## Context management

Long sessions cause context decay — initial rules get diluted as the conversation grows.

**When context is running low** (warning appears or response quality drops):
1. Run `uv run --project "$PLUGIN_ROOT" python -m ui_clone.pipeline <url> <component> <session> status` (with the same `UV_PROJECT_ENVIRONMENT` and `PYTHONPATH` exports as [Validation gates](pipeline-execution.md#validation-gates)) — output shows current gate and next action
2. `pipeline-state.json` in `tmp/ref/<component>/` persists gate progress automatically — no manual save needed
3. Start a new session — Claude re-reads SKILL.md fresh, then runs `python -m ui_clone.pipeline ... status` to resume

**Never skip to a later phase under context pressure.** Fewer sections done correctly > more sections done wrongly.

**Compaction-survival rule — re-verify any "X is broken" claim before acting on it.**
Compaction summaries flatten observation, hypothesis, and disproven-theory into one paragraph. A summary that asserts "REF shows A while IMPL shows B at scroll position N" is *a claim*, not *a fact* — earlier-in-session evidence has been compressed out. Before starting any non-trivial implementation in response to such a claim:
1. Re-capture both ref and impl at the *exact* scroll position the summary names (`agent-browser --session <s> eval "(() => { window.scrollTo(0, <sy>); return 'ok'; })()"` then screenshot, both sides).
2. Compare the two fresh captures — confirm the asserted difference is real, not residue from an earlier wrong screenshot the prior session never re-took.
3. Only then implement. The cost of a 30-second re-capture is far less than porting a complex animation that turns out to have already been correct.

This bites hardest right after `<system-reminder>` summaries reactivate a long-running task — exactly when the urge to "just continue" is strongest.
