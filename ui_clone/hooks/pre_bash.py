"""
PreToolUse Bash hook — blocks declaration-of-done commands when verification incomplete.

Why this hook exists
────────────────────
The Stop hook (section_gate) catches the case where Claude finishes a turn while
`current_gate != "done"`. But agents frequently declare done by *running a bash
command* — `git commit`, `git push`, `gh pr create`, `gh pr merge`. Those commands
fire *before* the next Stop event. The PostToolUse advisory (post_verify) prints
warnings but doesn't block — and per the v0.4.5 JSONL analysis, advisories alone
don't change behavior.

This hook fires on PreToolUse Bash. When:
  - a WIP marker `tmp/ref/<c>/.ui-re-active` exists, AND
  - the bash command matches a declaration-of-done pattern, AND
  - section-compare hasn't passed (or pipeline-state isn't "done")

…it denies the tool with a permission decision pointing the agent at the gate.

Bypass:
  - UI_RE_SKIP_BASH_GATE=1 in env disables the hook (escape hatch for emergencies)

Patterns blocked (anchored at start-of-command, after optional whitespace):
  - git commit ...
  - git push ...
  - gh pr create ...
  - gh pr merge ...
  - gh pr close ... (declaring abandonment is also a 'done' state we want to verify)

Not blocked: `git status`, `git diff`, `git log`, `gh pr view`, etc. — those are
read-only inspection.

Usage:
    python -m ui_clone.hooks.pre_bash

Input:  PreToolUse JSON on stdin with tool_input.command
Output: deny payload to stdout when blocking, exit 0 (silent) otherwise
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import cast

from ui_clone.hooks._common import find_project_root, find_ref_dir, is_component_file, run_gate
from ui_clone.state import PipelineState

_BLOCK_PATTERNS = re.compile(
    r"^\s*(?:"
    r"git\s+commit\b"
    r"|git\s+push\b"
    r"|gh\s+pr\s+(?:create|merge|close)\b"
    r")"
)

# Benchmark-related commands that depend on tmp/ref/realfood pointing at the
# current HEAD's work dir. When the symlink is stale (points at a prior SHA's
# dir), these commands silently iterate on the wrong artifacts and produce
# misleading metrics. Block them until `bash skills/benchmark/scripts/setup.sh`
# has re-linked. Pattern matches anywhere in the command — agents commonly
# chain a `cd` or env-setup prefix before the actual call.
_BENCHMARK_COMMAND_PATTERNS = re.compile(
    r"benchmark/work/"
    r"|skills/visual-debug/scripts/(?:section-compare|asset-transfer-check|"
    r"asset-utilization-check|visual-judge|section-spec|image-fidelity-check|"
    r"font-parity-check)"
    r"|scripts/extract/extract-assets"
    r"|skills/benchmark/scripts/benchmark-harvest"
    r"|tmp/ref/realfood",
    re.IGNORECASE,
)
# Within benchmark commands, setup.sh itself MUST be allowed even when the
# symlink is stale — running it is the recovery action. Same for `rm`/`ln`
# style cleanup the agent might issue when shown the block reason.
_BENCHMARK_SETUP_ESCAPES = re.compile(
    r"skills/benchmark/scripts/setup\.sh"
    r"|ln\s+-s",
    re.IGNORECASE,
)

# Bash redirects/streams that write to a file. Each pattern captures the target
# path. Designed to catch the common ways an agent could bypass the PreToolUse
# Edit/Write hook (pre_generate.py): `cat > file`, `tee file`, `sed -i ... file`.
_BASH_WRITE_PATTERNS = [
    # `cmd > file` or `cmd >> file` — any redirect to a path. Excludes process
    # substitutions (>(...)), fd duplications (>&N), and /dev/* sinks.
    re.compile(r">>?\s*(?![&(])\s*([^\s|;&<>()]+)"),
    # `tee file` / `tee -a file` — also blocks `tee --append`.
    re.compile(r"\btee\b\s+(?:-a\s+|--append\s+)?([^\s|;&<>()]+)"),
    # `sed -i ... file` — in-place edit. Match the file argument that follows
    # the sed expression. Conservative: requires the target to literally end
    # in a recognised source extension to avoid false positives on inline scripts.
    re.compile(
        r"\bsed\b[^|;&]*?\s-i(?:\.\S+)?\s[^|;&]*?\s([^\s|;&<>()]+\.(?:tsx|jsx|ts|js|css|scss|svelte|vue))\b"
    ),
]


def _is_declaration_command(cmd: str) -> bool:
    if not cmd:
        return False
    return bool(_BLOCK_PATTERNS.search(cmd))


def _bash_write_target(cmd: str) -> str | None:
    """Return the first component-file target this Bash command writes to, else None.

    Skips writes to /dev/null, /tmp, /var/tmp, .stale paths and the like —
    they're never component files anyway, but the early-out reduces regex work.
    """
    if not cmd:
        return None
    if ">/dev/null" in cmd or ">/tmp/" in cmd:
        # Common no-op redirects; quick reject before regex sweep.
        pass  # don't return — there may still be a real component-file write later in the cmd
    for pat in _BASH_WRITE_PATTERNS:
        for m in pat.finditer(cmd):
            target = m.group(1).strip("\"'")
            if not target or target.startswith("&") or target == "/dev/null":
                continue
            if is_component_file(target):
                return target
    return None


def _emit_block(reason: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(payload, ensure_ascii=False))


def _find_active_ref(search_root: Path) -> Path | None:
    if not search_root.is_dir():
        return None
    for d in sorted(search_root.iterdir()):
        if d.is_dir() and (d / ".ui-re-active").is_file():
            return d
    return None


def _check_benchmark_setup_alignment(project_root: Path) -> str | None:
    """Return a block reason if `tmp/ref/realfood` points at a benchmark/work/
    dir whose SHA doesn't match `git rev-parse --short HEAD`; else None.

    The check applies only when the symlink target matches the canonical
    `benchmark/work/<sha>/ref` layout — a different layout (e.g. user
    pointing at `tmp/ref/realfood -> tmp/ref/someothercomponent` for an
    unrelated clone) is left alone.

    Rationale: rounds A / B / V3 each launched a new SHA but agents skipped
    Step 1 setup, so the symlink stayed pinned to the previous round's work
    dir. Section-compare ran on stale capture data, AE metrics were
    artifacts, and the benchmark history was misleading. This check makes
    that failure mode loud — the agent is blocked at the first benchmark
    command and pointed at the recovery script.
    """
    sym = project_root / "tmp" / "ref" / "realfood"
    if not sym.is_symlink():
        return None
    try:
        target = os.readlink(sym)
    except OSError:
        return None

    m = re.search(r"benchmark/work/([0-9a-f]{7,40})/ref/?$", target)
    if not m:
        # Symlink doesn't point at a benchmark work dir — out of scope.
        return None
    link_sha = m.group(1)

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    head = (result.stdout or "").strip()
    if not head:
        return None

    if link_sha.startswith(head) or head.startswith(link_sha):
        return None  # match (one is shorter prefix of the other)

    return (
        f"⛔ UI-RE benchmark setup mismatch: tmp/ref/realfood points at "
        f"benchmark/work/{link_sha}/ref but HEAD is {head}. The agent is "
        f"about to iterate on a stale work dir (the inheritance bug observed "
        f"in rounds A / B / V3).\n"
        f"Run setup before any other benchmark command:\n"
        f"  bash skills/benchmark/scripts/setup.sh\n"
        f"That script wipes the current-SHA work dir, re-links "
        f"tmp/ref/realfood, and exits 2 on any further mismatch.\n"
        f"Bypass (emergency only): UI_RE_SKIP_BASH_GATE=1 <command>"
    )


def main() -> None:
    if os.environ.get("UI_RE_SKIP_BASH_GATE") == "1":
        sys.exit(0)

    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    if not raw.strip():
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)

    cmd = data.get("tool_input", {}).get("command", "") or data.get("command", "")
    if not isinstance(cmd, str):
        sys.exit(0)

    project_root = find_project_root()

    # Benchmark setup-alignment check. Fires BEFORE the declaration-of-done
    # logic so the agent is blocked on the first stale-symlink command, not
    # only when it tries to commit/push. The escape lets setup.sh / `ln -s`
    # through so the recovery action isn't itself blocked.
    if _BENCHMARK_COMMAND_PATTERNS.search(cmd) and not _BENCHMARK_SETUP_ESCAPES.search(cmd):
        bench_reason = _check_benchmark_setup_alignment(project_root)
        if bench_reason is not None:
            _emit_block(bench_reason)
            sys.exit(0)

    is_decl = _is_declaration_command(cmd)
    bash_write = _bash_write_target(cmd)
    if not is_decl and bash_write is None:
        sys.exit(0)

    # Bash-write to a component file: same gate as pre_generate (extraction must
    # be complete before component code is written). This closes the bypass where
    # an agent could use `cat > Foo.tsx`, `tee Foo.tsx`, or `sed -i ... Foo.tsx`
    # to skip the PreToolUse Edit/Write hook.
    if bash_write is not None:
        # Resolve ref_dir from the file's containing project, mirroring pre_generate.
        ref_dir = None
        try:
            fp = Path(bash_write).resolve()
            cur = fp.parent
            while cur != cur.parent:
                if (cur / "tmp" / "ref").is_dir():
                    ref_dir = find_ref_dir(cur / "tmp" / "ref")
                    break
                cur = cur.parent
        except OSError:
            pass
        if ref_dir is None:
            ref_dir = find_ref_dir(project_root / "tmp" / "ref")
        if ref_dir is None:
            sys.exit(0)
        gate_result = run_gate(ref_dir, "pre-generate")
        if not gate_result.get("passed", True):
            failures: list[dict[str, str]] = cast(list[dict[str, str]], gate_result.get("failures", []))
            fail_count = cast(int, gate_result.get("fail_count", len(failures)))
            missing = ", ".join(f.get("label", "?") for f in failures[:6])
            reason = (
                f"⛔ UI-RE: Bash write to component file '{bash_write}' blocked — "
                f"extraction incomplete ({fail_count} artifacts missing: {missing}).\n"
                f"This bypass route (cat>/tee/sed -i) goes through the same gate as Edit/Write.\n"
                f"Complete Phase 2 extraction before writing components.\n"
                f"Bypass (emergency only): UI_RE_SKIP_BASH_GATE=1 <command>"
            )
            _emit_block(reason)
            sys.exit(0)
        # Gate passed for write — fall through. If the cmd is ALSO a declaration,
        # the section-compare check below still runs. Otherwise we're done.
        if not is_decl:
            sys.exit(0)

    ref_dir = _find_active_ref(project_root / "tmp" / "ref")
    if ref_dir is None:
        sys.exit(0)

    state = PipelineState.load(ref_dir)

    # Always require section-compare result.txt to exist with 0 FAIL / 0 MISSING.
    # State alone isn't enough — we want freshness against actual artifacts.
    result_file = ref_dir / "sections" / "result.txt"
    if result_file.is_file():
        try:
            text = result_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        fail_count = text.count("❌")
        # Match section_gate.py / gate.py: explicit "⚠️ MISSING impl" marker
        missing_count = text.count("⚠️ MISSING impl")
        if fail_count == 0 and missing_count == 0 and state.current_gate == "done":
            sys.exit(0)

        # Have a result.txt but with failures
        if fail_count > 0 or missing_count > 0:
            reason = (
                f"⛔ UI-RE: cannot run '{cmd.split(chr(10))[0][:60]}' — "
                f"section-compare shows {fail_count} FAIL, {missing_count} MISSING.\n"
                f"Fix diffs in {ref_dir}/sections/diff/ and re-run:\n"
                f"  bash $SCRIPTS_DIR/section-compare.sh <orig> <impl> <session> {ref_dir}\n"
                f"Then: python -m ui_clone.gate {ref_dir} section-compare"
            )
            _emit_block(reason)
            sys.exit(0)

    # No result.txt at all OR result.txt clean but state isn't done — run the gate
    # and report what's actually missing. This avoids hardcoded message drift.
    gate_name = "section-compare" if state.current_gate in ("section-compare", "done") else state.current_gate
    gate_result = run_gate(ref_dir, gate_name)

    if gate_result.get("passed", True):
        # Gate passes (rare with no result.txt — could be 'reference' fail-open)
        # but state didn't say done. Re-load — Gate.run() may have advanced it.
        state = PipelineState.load(ref_dir)
        if state.current_gate == "done":
            sys.exit(0)
        # Gate passed but pipeline not at done — list what's left.
        # state.current_gate != "done" here (the == "done" branch returned above).
        remaining = state.current_gate
        reason = (
            f"⛔ UI-RE: cannot run '{cmd.split(chr(10))[0][:60]}' — "
            f"pipeline incomplete. Current gate: {remaining}.\n"
            f"Run: python -m ui_clone.gate {ref_dir} {remaining}"
        )
        _emit_block(reason)
        sys.exit(0)

    # Gate failed — list failures
    failures = cast(list[dict[str, str]], gate_result.get("failures", []))
    fail_count = cast(int, gate_result.get("fail_count", len(failures)))
    parts = [
        f"⛔ UI-RE: cannot run '{cmd.split(chr(10))[0][:60]}' — "
        f"{gate_name} gate FAILED ({fail_count} issue(s))."
    ]
    for f in failures[:5]:
        parts.append(f"  • {f.get('label', '?')}: {f.get('reason', '')}")
        if f.get("fix"):
            parts.append(f"    → {f['fix']}")
    parts.append(
        f"\nFix and re-run: python -m ui_clone.gate {ref_dir} {gate_name}\n"
        f"Bypass (emergency only): UI_RE_SKIP_BASH_GATE=1 <command>"
    )
    _emit_block("\n".join(parts))
    sys.exit(0)


if __name__ == "__main__":
    main()
