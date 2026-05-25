# Visual-judge dispatcher — design (D from claude-fidelity-analysis)

> Status: **DESIGN**. Implementation pending. Codex review (2026-05-25) of 6 design decisions applied below.

## Why

`docs/claude-fidelity-analysis.md` identified that claude's fix iterations stall on visual-fidelity sections because the existing `_visual_judge_next_action` in `goal.py` only emits text command suggestions — agents skip running `visual-judge.sh`. The previous codex review locked text-only emission as INTENTIONAL for the default path; an **explicit escape-hatch** dispatcher is the agreed compromise.

`docs/claude-fidelity-analysis.md`의 E1 (commit `9eb7c3e`) was the cheap text-grep half. D is the multimodal-LLM-with-cache half — invoked only when the cheap fixes don't unstick the loop.

## Non-goals

- Replace `_visual_judge_next_action` text emission as the default. Text suggestion stays.
- Auto-dispatch from `post_implement.py`. Codex review item (f): post-implement validates artifacts, doesn't dispatch. D lives in driver/goal-card territory.
- Replace operator-triggered `visual-judge.sh` invocations. The script stays callable directly; the dispatcher is a caching wrapper.

## Module placement (codex item a: OVER-ENG)

Single file at `ui_clone/visual_judge_dispatcher.py`. Do **not** create a new `ui_clone/dispatchers/` directory — premature for one module. Promote later if more dispatchers materialize.

## Cache schema (codex item b: RISKY → applied)

Cache files at `tmp/ref/<c>/sections/visual-judge-cache/<keyhash>.json`. Key = sha256 of:
- ref PNG bytes
- impl PNG bytes
- `LABEL` (section identifier — the prompt embeds this)
- prompt template content (`skills/visual-debug/prompts/visual-judge.md` sha256, first 12 chars)
- script content (`skills/visual-debug/scripts/visual-judge.sh` sha256, first 12 chars)

All five are required. Missing any one → stale cache survives prompt/script revisions.

## Locking (codex item d: SAFE, applied per-key)

`tmp/ref/<c>/sections/visual-judge-cache/<keyhash>.lock`. Pattern lifted from `ui_clone/driver_session.py:register` and `ui_clone/state.py:_pipeline_state_lock`:

```
with _per_key_lock(keyhash):
    if cache_path.is_file():
        return _load_cached(cache_path)  # double-check after acquire
    tmp = cache_path.with_suffix(f".tmp.{os.getpid()}")
    proc = subprocess.run(
        [bash, visual_judge_sh, ref_png, impl_png, "--out", str(tmp), "--label", label],
        timeout=visual_judge_timeout_seconds(),
    )
    if proc.returncode != 0:
        raise VisualJudgeError(proc.returncode, proc.stderr)
    _validate_json(tmp)  # exit 1 from script means invalid JSON; double-check here
    os.replace(tmp, cache_path)
    return _load_cached(cache_path)
```

Per-key lock (not global) so two different sections can dispatch in parallel.

## Error contract (codex item e: RISKY → applied)

`VisualJudgeError(returncode, stderr, cause)` exception, **not** silent `None`. The current `visual-judge.sh` exit codes carry information that callers should be able to act on:
- `exit 2`: bad args → developer error, raise
- `exit 1`: invalid JSON response → judge failed, raise
- `exit 3`: `claude` CLI missing on PATH → environment issue, raise
- `exit 124` (from our `run_with_timeout.py` wrapper): timeout → raise
- `returncode 0`: success → return parsed dict

Goal-card rendering catches `VisualJudgeError` and surfaces a specific diagnostic instead of crashing.

## Invocation pattern (codex item c: RISKY → driver-only)

Two callers, both driver-territory:

1. **`scripts/loop/visual-judge-escape.sh`** — operator-triggered: `bash scripts/loop/visual-judge-escape.sh <ref-dir>`. Reads `sections/result.txt`, picks worst-3 uncached failing rows, dispatches each, summarizes findings to stdout.
2. **`ui_clone/goal.py:_visual_judge_next_action`** — *cache-only read*. If cache hit exists for the worst-AE section, inline the LLM findings into `next_action` instead of just the command string. **No dispatch from goal.py.** Cache misses fall back to existing text-command emission.

This split keeps post_implement.py (gate enforcement) and goal.py (read-only display) decoupled from the LLM-call side effect. Only the explicit escape-hatch script triggers paid vision calls.

## Test strategy (codex item f: SAFE)

`tests/test_visual_judge_dispatcher.py`, all using `subprocess.run` monkeypatch (no live `claude --print`):

1. **cache-hit** — pre-existing valid JSON returned, `subprocess.run` not called
2. **cache-miss success** — fake `subprocess.run` writes tmp JSON → dispatcher validates → `os.replace` publishes → returns parsed dict
3. **non-zero returncode** — fake exit 3 with stderr "claude not found" → `VisualJudgeError(3, "claude not found")` raised
4. **invalid JSON** — fake writes non-JSON to tmp → dispatcher raises, no cache published
5. **per-key lock** — two threads same key + blocking fake subprocess → only one subprocess invocation, second gets cache after lock release
6. **key sensitivity** — same ref PNG, different impl PNG → different keyhash, different cache path. Same inputs, different prompt content → different key.

## Cost ceiling

- Per dispatch: ≤ `VISUAL_JUDGE_TIMEOUT_SEC` (300s) × `claude --print` (≈ $0.05–0.20 per multimodal call observed)
- Per-site escape-hatch invocation: worst-3 sections → max $0.60
- 26-site benchmark: max $15.60 (if all 26 trigger escape, which they shouldn't — E1's cheap grep should resolve most before escape)
- Cache hit: $0 — every subsequent invocation on the same `(ref-png, impl-png, prompt, script, label)` returns cached findings

## Implementation surface

| File | Add/Edit | LOC |
|---|---|---|
| `ui_clone/visual_judge_dispatcher.py` | new | ~180 |
| `ui_clone/goal.py` | edit `_visual_judge_next_action` (cache-only read) | ~25 |
| `scripts/loop/visual-judge-escape.sh` | new | ~50 |
| `tests/test_visual_judge_dispatcher.py` | new (6 tests per codex item f) | ~200 |
| CHANGELOG / docs | edit | ~20 |

Total ≈ **475 lines**. One commit feasible; ~2h focused TDD work.

## Risks the design does NOT mitigate

- `claude --print` itself produces unstable JSON sometimes. Dispatcher validates schema-shape, not findings quality. Stuck-loops on legitimately ambiguous sections can still loop until hard-cap.
- `--label` is part of the cache key, but two sections with the same `id` (e.g. operator rename) would collide. Mitigation: include `ref_dir` basename or full path in the key, not just label.
- Operator manually deleting the cache directory mid-run is fine (next call re-dispatches) but the cache directory may grow indefinitely. Add a `purge older than N days` script in a follow-up if needed.
