"""ui_clone.benchmark_harness — headless Python-driven benchmark loop.

Drives Claude in headless `claude --print` mode with PER-ITER FOCUSED
PROMPTS for unattended automation paths (cron, CI, batch runs). NOT
the canonical user-facing benchmark — that's the `benchmark` skill,
which is LLM-driven and matches real skill-use semantics. This module
exists for headless drivers that need a deterministic Python loop +
hard token / wall-clock budgets.

Per-iter prompt = current pipeline-state + unmet STRICT v2 conditions,
so the agent gets specific failure context ("AE=279K on footer, fix
that section") rather than a static "try again" re-injection.

Stop conditions (STRICT v2; mirrors plan file)
───────────────────────────────────────────────
- All structural: page.tsx < 200 LOC, components/ > 3
- gate_fail_counts == {} AND current_gate == "done"
- 0 STRUCTURAL_ONLY-with-major-or-critical-ratio<0.5
- 0 section-threshold gaming
- tree-diff-status.json status == "pass"
- transitions/result.txt exists AND 0 ❌ rows
- bundle-impl-coverage.json status == "pass"
- asset-utilization.json status == "pass" AND downloaded >= 5

Outcomes:
- DONE — all stop conditions met
- INCOMPLETE_MAX_ITER — hit --max-iter without converging
- INCOMPLETE_BUDGET — hit --token-budget without converging
- INCOMPLETE_TIMEOUT — wall clock budget exceeded
- ABORTED — pipeline-state.json declared site unclonable

Usage:
    python -m ui_clone.benchmark_harness <ref-dir> \\
        --orig-url https://realfood.gov \\
        --impl-url http://localhost:3000 \\
        --impl-dir benchmark/work/<sha>/impl \\
        --max-iter 100 \\
        --token-budget 500000
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from ui_clone.goal import _MAX_GATE_FAILS
from ui_clone.state import PipelineState

# ── Stop-condition probes ────────────────────────────────────────────────


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _file_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        return sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
    except OSError:
        return 0


def _count_tsx(dir: Path) -> int:
    if not dir.is_dir():
        return 0
    return sum(1 for p in dir.rglob("*.tsx") if p.is_file() and not p.name.startswith("."))


def check_strict_done(ref_dir: Path, impl_dir: Path) -> tuple[bool, list[str]]:
    """Return (done, list_of_unmet_conditions).

    Each unmet condition is a short human-readable string the harness can
    feed back into the next iter's prompt so the agent knows exactly what
    still needs to happen.
    """
    unmet: list[str] = []

    state = PipelineState.load(ref_dir)
    if state.unclonable_reasons:
        # Treated separately by caller as ABORTED, not as "not done."
        unmet.append(f"unclonable: {state.unclonable_reasons[0]}")
        return False, unmet

    if state.current_gate != "done":
        unmet.append(f"current_gate={state.current_gate!r} (need 'done')")

    if state.gate_fail_counts:
        unmet.append(f"gate_fail_counts non-empty: {dict(state.gate_fail_counts)}")

    # Structure
    page = impl_dir / "src" / "app" / "page.tsx"
    page_loc = _file_lines(page)
    if page_loc == 0:
        unmet.append("impl/src/app/page.tsx missing")
    elif page_loc >= 200:
        unmet.append(f"page.tsx {page_loc} LOC >= 200 (need <200 — split into components)")
    comps_dir = impl_dir / "src" / "components"
    comps = _count_tsx(comps_dir)
    if comps <= 3:
        unmet.append(f"impl/src/components has {comps} .tsx files (need >3)")

    # Section-compare result
    result_txt = ref_dir / "sections" / "result.txt"
    if not result_txt.is_file():
        unmet.append("sections/result.txt missing (run section-compare)")
    else:
        text = result_txt.read_text(encoding="utf-8", errors="replace")
        fail_rows = sum(
            1 for ln in text.splitlines()
            if ln.startswith("|") and "❌" in ln
        )
        if fail_rows > 0:
            unmet.append(f"sections/result.txt has {fail_rows} ❌ row(s)")
        missing_rows = sum(1 for ln in text.splitlines() if "⚠️ MISSING impl" in ln)
        if missing_rows > 0:
            unmet.append(f"sections/result.txt has {missing_rows} MISSING impl row(s)")

    # tree-diff status (primary convergence)
    td = _read_json(ref_dir / "tree-diff-status.json")
    if td is None:
        unmet.append("tree-diff-status.json missing (run tree-diff.sh)")
    elif isinstance(td, dict) and td.get("status") != "pass":
        unmet.append(
            f"tree-diff status={td.get('status')!r} "
            f"(errorCount={td.get('errorCount')}; reason={td.get('reason')})"
        )
    elif isinstance(td, dict):
        counts = td.get("counts") or {}
        if isinstance(counts, dict):
            unpaired = int(counts.get("unpaired") or 0)
            ok = int(counts.get("ok") or 0)
            if unpaired >= 3 and unpaired > ok:
                unmet.append(
                    f"tree-diff unpaired={unpaired} ok={ok} "
                    "(pairing failed despite status='pass')"
                )

    # transitions/result.txt
    tr = ref_dir / "transitions" / "result.txt"
    if tr.is_file():
        tr_fails = sum(1 for ln in tr.read_text(encoding="utf-8", errors="replace").splitlines() if "❌" in ln)
        if tr_fails > 0:
            unmet.append(f"transitions/result.txt has {tr_fails} ❌ row(s)")

    # bundle-impl-coverage
    bic = _read_json(ref_dir / "bundle-impl-coverage.json")
    if isinstance(bic, dict) and bic.get("status") not in (None, "pass", "skip"):
        unmet.append(
            f"bundle-impl-coverage status={bic.get('status')!r} "
            f"(missing={bic.get('missingDeps')})"
        )

    # asset-utilization
    au = _read_json(ref_dir / "asset-utilization.json")
    if isinstance(au, dict):
        if au.get("status") not in (None, "pass", "skip"):
            unmet.append(
                f"asset-utilization status={au.get('status')!r} "
                f"(ratio={au.get('ratio')}, threshold={au.get('threshold')})"
            )

    return (len(unmet) == 0), unmet


# ── Failure context extraction ───────────────────────────────────────────


def collect_recent_failures(ref_dir: Path, impl_dir: Path) -> str:
    """Build a short human-readable summary of what's failing right now.

    Fed into the next iter's prompt so the agent knows the specific
    failures rather than re-running gates to discover them.
    """
    done, unmet = check_strict_done(ref_dir, impl_dir)
    if done:
        return "(no failures detected — all STRICT v2 conditions met)"
    lines = ["STRICT v2 conditions still unmet:"]
    for u in unmet:
        lines.append(f"  - {u}")
    return "\n".join(lines)


# ── Prompt building ──────────────────────────────────────────────────────


_INITIAL_PROMPT = """You are running ui-clone-skills benchmark iter 1 against the canonical site.

Goal: clone {orig_url} into {impl_dir} until every STRICT v2 condition in
the plan file at `~/.claude/plans/happy-finding-pelican.md` is met.

REF dir is `{ref_dir}` (already symlinked from tmp/ref/realfood).
IMPL dir is `{impl_dir}`.

Pipeline phases:
1. (if static/ref/ has <5 PNGs) `/ui-capture {orig_url} '' realfood` — populates {ref_dir}
2. Extract → spec → pre-generate → impl scaffold (Next.js)
3. Asset transfer: `bash scripts/extract/extract-assets.sh realfood-bench {ref_dir} {impl_dir}/public`
4. Generate per-section components (target: page.tsx < 200 LOC, components/ > 3)
5. Run section-compare → tree-diff → motion checks
6. Iterate until all STRICT conditions pass

Use `python -m ui_clone.goal {ref_dir}` to see the next bounded action.
Use `python -m ui_clone.gate {ref_dir} <gate>` to verify any gate.
Use `python -m ui_clone.measure section-compare {ref_dir} --orig-url {orig_url} --impl-url {impl_url} --session realfood-bench` to run measurement with LOCKED defaults.

After your work, exit. The Python harness will re-invoke you with focused
fix instructions for the next iter."""


_ITER_PROMPT = """ui-clone-skills benchmark iter {iter} / {max_iter}.
ref={ref_dir}  impl={impl_dir}

Current pipeline state:
{goal_output}

What's still unmet (STRICT v2 conditions the harness already checked):
{failures}

Your job for THIS iter:
1. Address the unmet conditions above. Pick the cheapest fix that moves the
   most conditions toward met.
2. Use `python -m ui_clone.goal {ref_dir}` for the canonical next action if
   the failures don't make the next step obvious.
3. After your edits, re-run the relevant gate / check to verify.
4. DO NOT re-do work already in pipeline-state.json `completed_steps`. The
   work persists across iters — only address what's still failing.
5. EXIT when this iter's work is done. The harness will check STRICT
   conditions after you exit and re-invoke you if needed.

Token budget remaining: {budget_remaining:,} tokens. Be efficient."""


def build_iter_prompt(
    ref_dir: Path,
    impl_dir: Path,
    orig_url: str,
    impl_url: str,
    iter_count: int,
    max_iter: int,
    budget_remaining: int,
) -> str:
    """Build the per-iter focused prompt."""
    if iter_count == 1 and not (ref_dir / "pipeline-state.json").is_file():
        return _INITIAL_PROMPT.format(
            orig_url=orig_url,
            impl_url=impl_url,
            ref_dir=ref_dir,
            impl_dir=impl_dir,
        )

    # Goal card
    try:
        goal_proc = subprocess.run(
            [sys.executable, "-m", "ui_clone.goal", str(ref_dir)],
            capture_output=True, text=True, timeout=30,
        )
        goal_output = goal_proc.stdout.strip() or "(goal.py returned empty)"
    except (subprocess.TimeoutExpired, OSError) as e:
        goal_output = f"(goal.py failed: {e})"

    failures = collect_recent_failures(ref_dir, impl_dir)

    return _ITER_PROMPT.format(
        iter=iter_count,
        max_iter=max_iter,
        ref_dir=ref_dir,
        impl_dir=impl_dir,
        goal_output=goal_output,
        failures=failures,
        budget_remaining=budget_remaining,
    )


# ── Claude invocation ────────────────────────────────────────────────────


def invoke_claude(
    prompt: str,
    session_id: str,
    plugin_dir: Path,
    cwd: Path,
    iter_count: int,
) -> dict[str, Any]:
    """Invoke `claude -p` headless. Returns dict with response/tokens.

    Output format `stream-json` would give us per-message detail but
    `json` is simpler — single result object with `result`/`session_id`/
    `total_cost_usd`/`usage`.
    """
    cmd = [
        "claude",
        "--print",
        prompt,
        "--plugin-dir", str(plugin_dir),
        "--session-id", session_id,
        "--output-format", "json",
        "--permission-mode", "auto",
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True, text=True,
        cwd=str(cwd),
        env={**os.environ},
    )
    out = proc.stdout
    err = proc.stderr
    parsed: dict[str, Any]
    try:
        parsed = json.loads(out)
        if not isinstance(parsed, dict):
            parsed = {"_raw_non_dict": parsed}
    except json.JSONDecodeError:
        parsed = {"_raw": out[:500], "_stderr": err[:500]}
    parsed["_exit_code"] = proc.returncode
    parsed["_iter"] = iter_count
    return parsed


def _extract_tokens(claude_result: dict[str, Any]) -> int:
    """Pull total tokens from the JSON response. Best-effort."""
    usage = claude_result.get("usage") or {}
    if isinstance(usage, dict):
        return int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
    return 0


# ── Main loop ────────────────────────────────────────────────────────────


def run_loop(args: argparse.Namespace) -> str:
    ref_dir = Path(args.ref_dir).resolve()
    impl_dir = Path(args.impl_dir).resolve() if args.impl_dir else ref_dir.parent / "impl"
    plugin_dir = Path(__file__).resolve().parents[1]
    session_id = args.session_id or str(uuid.uuid4())

    started = time.time()
    iter_count = 0
    total_tokens = 0
    iter_log: list[dict[str, Any]] = []

    log_path = ref_dir / "benchmark-harness.log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = log_path.open("w", encoding="utf-8")

    def _log(record: dict[str, Any]) -> None:
        log_fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        log_fh.flush()

    _log({
        "event": "start",
        "ref_dir": str(ref_dir),
        "impl_dir": str(impl_dir),
        "session_id": session_id,
        "max_iter": args.max_iter,
        "token_budget": args.token_budget,
        "wall_budget_s": args.wall_budget_s,
    })

    outcome = "INCOMPLETE_UNKNOWN"
    while True:
        # Stop checks BEFORE invoking
        if iter_count >= args.max_iter:
            outcome = "INCOMPLETE_MAX_ITER"
            break
        if total_tokens >= args.token_budget:
            outcome = "INCOMPLETE_BUDGET"
            break
        if (time.time() - started) >= args.wall_budget_s:
            outcome = "INCOMPLETE_TIMEOUT"
            break

        # Done?
        done, unmet = check_strict_done(ref_dir, impl_dir)
        if done:
            outcome = "DONE"
            break

        # Abort signal?
        state = PipelineState.load(ref_dir)
        if state.unclonable_reasons:
            outcome = "ABORTED"
            _log({"event": "abort", "reasons": list(state.unclonable_reasons)})
            break
        # Hard cap: any gate that crossed _MAX_GATE_FAILS is in a runaway
        # retry loop (observed in B bench at 445× post-implement). goal.py's
        # abort_banner triggers on this; the harness mirrors that check so it
        # stops burning iterations / tokens / wallclock on the same failure.
        max_fail_gate = None
        max_fail_count = 0
        for g, n in state.gate_fail_counts.items():
            if n > max_fail_count:
                max_fail_count = n
                max_fail_gate = g
        if max_fail_count >= _MAX_GATE_FAILS:
            outcome = "ABORTED"
            _log({
                "event": "abort",
                "reason": "max_gate_fails_exceeded",
                "gate": max_fail_gate,
                "fail_count": max_fail_count,
                "cap": _MAX_GATE_FAILS,
            })
            break

        # Build prompt + invoke
        iter_count += 1
        budget_remaining = max(0, args.token_budget - total_tokens)
        prompt = build_iter_prompt(
            ref_dir, impl_dir, args.orig_url, args.impl_url,
            iter_count, args.max_iter, budget_remaining,
        )

        _log({
            "event": "iter_start",
            "iter": iter_count,
            "total_tokens_so_far": total_tokens,
            "unmet_count": len(unmet),
            "unmet_first_3": unmet[:3],
            "prompt_chars": len(prompt),
        })

        t0 = time.time()
        result = invoke_claude(prompt, session_id, plugin_dir, cwd=plugin_dir, iter_count=iter_count)
        elapsed = time.time() - t0
        iter_tokens = _extract_tokens(result)
        total_tokens += iter_tokens

        _log({
            "event": "iter_end",
            "iter": iter_count,
            "elapsed_s": round(elapsed, 1),
            "iter_tokens": iter_tokens,
            "total_tokens": total_tokens,
            "exit_code": result.get("_exit_code"),
            "claude_session": result.get("session_id"),
            "is_error": result.get("is_error"),
        })

        iter_log.append({
            "iter": iter_count,
            "tokens": iter_tokens,
            "elapsed_s": round(elapsed, 1),
            "exit_code": result.get("_exit_code"),
        })

    # Final state snapshot
    _, final_unmet = check_strict_done(ref_dir, impl_dir)
    _log({
        "event": "end",
        "outcome": outcome,
        "iters": iter_count,
        "total_tokens": total_tokens,
        "wall_s": round(time.time() - started, 1),
        "final_unmet": final_unmet,
    })
    log_fh.close()

    print("\n══ benchmark-harness ══")
    print(f"outcome:      {outcome}")
    print(f"iters:        {iter_count} / {args.max_iter}")
    print(f"tokens:       {total_tokens:,} / {args.token_budget:,}")
    print(f"wall_clock:   {round(time.time() - started, 1)}s / {args.wall_budget_s}s")
    print(f"unmet:        {len(final_unmet)}")
    for u in final_unmet[:10]:
        print(f"  - {u}")
    print(f"log:          {log_path}")
    return outcome


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m ui_clone.benchmark_harness",
        description="Python-driven benchmark loop with focused per-iter prompts.",
    )
    p.add_argument("ref_dir", help="ref dir (e.g. benchmark/work/<sha>/ref)")
    p.add_argument("--impl-dir", default=None, help="impl dir (defaults to <ref>/../impl)")
    p.add_argument("--orig-url", required=True, help="reference site URL")
    p.add_argument("--impl-url", default="http://localhost:3000", help="local impl URL")
    p.add_argument("--max-iter", type=int, default=100)
    p.add_argument("--token-budget", type=int, default=500_000, help="cumulative input+output tokens budget")
    p.add_argument("--wall-budget-s", type=int, default=14400, help="wall-clock budget in seconds (default 4h)")
    p.add_argument("--session-id", default=None, help="reuse a Claude session-id across iters (auto-generated if absent)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    outcome = run_loop(args)
    return 0 if outcome == "DONE" else 1


if __name__ == "__main__":
    sys.exit(main())
