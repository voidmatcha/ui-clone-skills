"""Orchestrator for the PreToolUse Bash hook.

Reads stdin, decodes the PreToolUse payload, and walks the rule families
in deny-precedence order. Each guard returns a (reason, exit) tuple via
the `_emit_block` shortcut — first match wins.

Kept as a separate module so `pre_bash.py` is a thin importable entry
point and the rule families stay independently testable / readable.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, cast

from ui_clone.hooks._common import (
    extract_tool_command,
    find_project_root,
    find_ref_dir,
    has_clone_writes,
    has_external_browse,
    mark_external_browse,
    mark_ref_session,
    run_gate,
    session_id_from_payload,
    should_enforce_ref_for_session,
    target_ref_dir_for_ui_re_command,
)
from ui_clone.state import PipelineState

from .bash_write import (
    _bash_adhoc_ref_target,
    _bash_enforcement_state_target,
    _bash_scratch_nested_ref_target,
    _bash_verification_plan_ack_target,
    _bash_write_target,
)
from .declaration import _is_declaration_command
from .impl_scaffold import _impl_scaffold_violation
from .ref_state import (
    _find_active_ref,
    _ref_dir_for_static_guard,
    _state_before_gate,
)
from .section_compare import (
    _SECTION_COMPARE_COMMAND_PATTERNS,
    _section_compare_precondition_reason,
)
from .static_mirror import (
    _static_html_mirror_write_target,
    _static_mirror_download_violation,
    _static_server_violation,
    _whole_document_html_snapshot_violation,
)

_CONTINUATION_RECEIPT_DIR = ".ui-re-continuation"
_CONTINUATION_FINAL_STATES = {"complete", "terminal", "unsupported"}
_CONTINUATION_BLOCKED_STATES = {"arming", "armed", "canceling", "paused"}
_CONTINUATION_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_CONTINUATION_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_CONTINUATION_CONTROL_SUBCOMMANDS = {
    "activate",
    "bind-ref",
    "arm",
    "mark-unsupported",
    "pause",
    "status",
}


def _emit_block(reason: str) -> None:
    # Dual-emit so the deny ENFORCES on both hosts: codex-cli 0.137 honors the
    # top-level decision/reason (exit 0), Claude Code honors the nested
    # hookSpecificOutput.permissionDecision. Each host ignores the other's
    # sibling fields. (Emitting only the Claude shape let codex run the command.)
    payload = {
        "decision": "block",
        "reason": reason,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }
    print(json.dumps(payload, ensure_ascii=False))


def _emit_warn(reason: str) -> None:
    """Non-blocking advisory to stderr — surfaces to the agent transcript
    without denying the command. Used for demoted guards (hook slimming B)
    where the concern is real but the hard block was disproportionate and has a
    downstream backstop (the Stop verify-stamp gate)."""
    print(reason, file=sys.stderr)


def _resolve_payload_cwd(data: dict) -> Path | None:
    payload_cwd_raw = data.get("cwd", "")
    if isinstance(payload_cwd_raw, str) and payload_cwd_raw:
        candidate = Path(payload_cwd_raw)
        if candidate.is_dir():
            return candidate.resolve()
    return None


def _mark_ui_re_session(
    cmd: str, project_root: Path, session_id: str, payload_cwd: Path | None
) -> None:
    ref_dir = target_ref_dir_for_ui_re_command(cmd, project_root, cwd=payload_cwd)
    if ref_dir is not None:
        mark_ref_session(ref_dir, session_id, source="pre_bash")


def _continuation_token_ok(token: str) -> bool:
    return (
        isinstance(token, str)
        and bool(token)
        and token not in {".", ".."}
        and (
            _CONTINUATION_UUID_RE.fullmatch(token) is not None
            or _CONTINUATION_TOKEN_RE.fullmatch(token) is not None
        )
    )


def _continuation_receipt_path(project_root: Path, session_id: str) -> Path | None:
    if not _continuation_token_ok(session_id):
        return None
    return project_root / _CONTINUATION_RECEIPT_DIR / f"{session_id}.json"


def _continuation_ref_safe(ref_dir: object) -> str | None:
    if ref_dir is None:
        return None
    if not isinstance(ref_dir, str) or not ref_dir:
        return None
    path = Path(ref_dir)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path.as_posix()


def _load_continuation_receipt(
    project_root: Path, session_id: str
) -> tuple[dict[str, Any] | None, bool]:
    path = _continuation_receipt_path(project_root, session_id)
    if path is None:
        return None, False
    try:
        continuation = importlib.import_module("ui_clone.claude_continuation")
    except ImportError:
        return None, path.exists()
    try:
        receipt = continuation.load_receipt(project_root, session_id)
    except Exception:
        return None, path.exists()
    return receipt, receipt is None and path.exists()


def _relative_continuation_ref(project_root: Path, ref_dir: Path) -> str | None:
    try:
        rel = ref_dir.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return None
    return _continuation_ref_safe(rel)


def _bind_continuation_ref(
    project_root: Path, session_id: str, receipt: dict[str, Any], ref_dir: Path
) -> str | None:
    if receipt.get("state") in _CONTINUATION_FINAL_STATES:
        return None
    rel = _relative_continuation_ref(project_root, ref_dir)
    if rel is None:
        return (
            "⛔ UI-RE continuation bind failed: the targeted ref is outside "
            "the project, so this session cannot safely bind continuation "
            "state. Run the continuation status control command and restart "
            "the pipeline from a project-local tmp/ref/<component> path."
        )
    existing = receipt.get("refDir")
    if existing is not None and existing != rel:
        return (
            "⛔ UI-RE continuation ref mismatch: this session is already "
            f"bound to `{existing}`, but the command targets `{rel}`. Continue "
            "the bound ref, pause this continuation, or create a separate "
            "continuation receipt for the other ref before running UI-RE work."
        )
    if existing == rel:
        return None
    try:
        continuation = importlib.import_module("ui_clone.claude_continuation")
    except ImportError:
        return (
            "⛔ UI-RE continuation bind unavailable: the continuation core "
            "could not be imported, so the Pre-Bash hook cannot safely bind "
            f"this session to `{rel}`. Repair the continuation CLI import path "
            "or pause this continuation before UI-RE work; the existing "
            "receipt was preserved."
        )
    try:
        continuation.bind_ref(project_root, session_id, ref_dir)
    except Exception as exc:
        return (
            "⛔ UI-RE continuation bind failed: the receipt could not be "
            f"bound to `{rel}` ({exc}). Run "
            "`python -m ui_clone.claude_continuation status ...` to inspect "
            "the receipt, then repair or pause it before UI-RE work."
        )
    return None


def _is_continuation_control_command(cmd: str) -> bool:
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()
    if len(tokens) < 4:
        return False
    executable = tokens[0].rsplit("/", 1)[-1]
    return (
        executable in {"python", "python3"}
        and tokens[1] == "-m"
        and tokens[2] == "ui_clone.claude_continuation"
        and tokens[3] in _CONTINUATION_CONTROL_SUBCOMMANDS
    )


def _guard_claude_continuation(
    cmd: str, project_root: Path, session_id: str, payload_cwd: Path | None
) -> str | None:
    if not session_id or _is_continuation_control_command(cmd):
        return None
    target_ref = target_ref_dir_for_ui_re_command(cmd, project_root, cwd=payload_cwd)
    if target_ref is None:
        return None
    receipt, invalid = _load_continuation_receipt(project_root, session_id)
    if invalid:
        return (
            "⛔ UI-RE continuation receipt invalid/corrupt or core unavailable "
            "for this session. "
            f"The receipt at `{_continuation_receipt_path(project_root, session_id)}` "
            "could not be validated, so this UI-RE command is fail-closed and "
            "the existing receipt is preserved. Fix by running the continuation "
            "control CLI (`python -m ui_clone.claude_continuation status ...`) "
            "to repair or pause it, or by creating a fresh valid receipt before "
            "pipeline work."
        )
    if receipt is None:
        return None
    state = receipt.get("state")
    if state in _CONTINUATION_BLOCKED_STATES:
        return (
            f"⛔ UI-RE continuation state `{state}` owns this Claude session. "
            "Do not start or resume UI-RE pipeline work until the one-shot create/delete "
            "transition finishes or an explicit reactivation returns the receipt to "
            "`running`. Allowed control commands are `activate`, `bind-ref`, `arm`, "
            "`mark-unsupported`, `pause`, and `status`."
        )
    if state == "running":
        return _bind_continuation_ref(project_root, session_id, receipt, target_ref)
    return None


def _guard_whole_document_mirror(cmd: str) -> str | None:
    if not _whole_document_html_snapshot_violation(cmd):
        return None
    return (
        "⛔ UI-RE whole-document static mirror blocked: do not dump "
        "`document.documentElement.outerHTML` / `document.body.innerHTML` "
        "into tmp/ref or impl files. Section-level outerHTML probes are "
        "allowed for extraction, but the implementation must be generated "
        "from canonical artifacts and verified with motion/runtime gates."
    )


def _guard_static_html_mirror(cmd: str) -> str | None:
    html_target = _static_html_mirror_write_target(cmd)
    if html_target is None:
        return None
    return (
        f"⛔ UI-RE static mirror blocked: writing copied live HTML into "
        f"impl/index.html ({html_target}) is not a React/Tailwind clone "
        "and strips the original transition runtime. Use canonical "
        "extraction artifacts to generate source components, then run "
        "`python -m ui_clone.pipeline <url> <component> <session> verify`."
    )


def _guard_scratch_nested_ref(cmd: str) -> str | None:
    """Block writes to `scratch/<dir>/tmp/ref/...` (scratch-nested ref bypass)."""
    target = _bash_scratch_nested_ref_target(cmd)
    if target is None:
        return None
    return (
        f"⛔ UI-RE scratch-nested ref blocked: write to '{target}' uses a "
        f"non-canonical tmp/ref/ tree under scratch/. The canonical "
        f"location is `<repo>/tmp/ref/<component>/` directly at the repo "
        f"root, not `<repo>/scratch/<loop>/tmp/ref/<component>/`. The "
        f"Stop hook's verify-stamp gate only scans the canonical location, "
        f"so the scratch-nested layout silently escapes verification.\n\n"
        f"Fix: write artifacts to <repo>/tmp/ref/<component>/, not under "
        f"scratch/. Run the pipeline driver with cwd = <repo> (not a "
        f"scratch subdir):\n"
        f"  cd <repo>\n"
        f"  python -m ui_clone.pipeline <url> <component> <session> run\n"
    )


def _guard_enforcement_state_rm(cmd: str) -> str | None:
    """Block targeted deletion/truncation/overwrite/move of a guard's own state file.

    The fail-LOUD ledger (.gate-skip-log) and the off-pipeline activation crumbs
    (.ui-re-external-browse/, .ui-re-active) are checked by EXISTENCE/CONTENT:
    destroying or clobbering them silently releases an un-enforced or off-pipeline
    run. A whole-dir reset (`rm -rf tmp/ref/<c>`) does not name these files and is
    unaffected.
    """
    target = _bash_enforcement_state_target(cmd)
    if target is None:
        return None
    return (
        f"⛔ UI-RE enforcement-state write blocked: '{target}' is gate state "
        "(the fail-LOUD .gate-skip-log ledger or an off-pipeline activation "
        "crumb). Destroying, truncating, overwriting, or editing it in place by "
        "any means (rm/mv/cp/tee/ln/dd/truncate/install/rsync/sed/perl/ex, python "
        "file APIs, or shell redirect — among others) silently disables a gate "
        "that would otherwise block closeout. Do not destroy it.\n\n"
        "If you are genuinely resetting a run, remove the whole ref dir "
        "(`rm -rf tmp/ref/<component>`), which re-runs every gate from clean — "
        "do not single out the enforcement file. To re-enforce a skipped gate, "
        "re-run it on a host with the ui-clone-skills env (which clears the "
        "ledger entry), or record `gateSkipAck` in verification-plan.json.\n\n"
        "Only READING it? This guard also over-blocks a read (a python "
        "`open(...)` is blocked in any mode because distinguishing read from "
        "write reopens truncate-bypasses). Use a sanctioned read instead — it "
        "is not blocked: `cat <file>`, `jq . <file>`, `grep <pat> <file>`, or "
        "`python -m ui_clone.pipeline <ref> status --json` for pipeline state."
    )


def _guard_verification_plan_ack(cmd: str) -> str | None:
    """Block a Bash write that sets gateSkipAck/deferredAck in
    verification-plan.json — the ack keys release closeout blockers, and a
    self-granted ack closes out an un-enforced/deferred run."""
    target = _bash_verification_plan_ack_target(cmd)
    if target is None:
        return None
    return (
        f"⛔ UI-RE closeout-ack write blocked: '{target}' write sets an ack key "
        "(gateSkipAck/deferredAck) that dissolves a closeout blocker "
        "(gate_skip_blocker / deferred_checks_blocker). That ack is an explicit "
        "USER decision accepting an un-enforced or deferred run — not an agent "
        "edit. Re-run the gate on a host with the ui-clone-skills env (which "
        "clears .gate-skip-log) or regenerate the plan at the required tier "
        "instead of self-granting the ack."
    )


def _guard_adhoc_redirect(cmd: str) -> str | None:
    adhoc = _bash_adhoc_ref_target(cmd)
    if adhoc is None:
        return None
    target, suggested = adhoc
    basename = Path(target).name
    if suggested:
        return (
            f"⛔ UI-RE: Bash redirect to ad-hoc ref artifact "
            f"'{basename}' blocked. Use canonical '{suggested}' "
            f"produced by the matching pipeline script "
            f"(e.g. `bash $PLUGIN_ROOT/skills/visual-debug/scripts/"
            f"dom-scaffold.sh <ref-dir>` for section-map.json). "
            f"Do NOT dump JSON into tmp/ref/<c>/ via cat/echo/tee/"
            f"agent-browser eval redirects. See SKILL.md Pipeline section."
        )
    return (
        f"⛔ UI-RE: Bash redirect to ad-hoc ref artifact "
        f"'{basename}' blocked. Run a canonical extraction "
        f"script (skills/visual-debug/scripts/*.sh) instead of "
        f"hand-dumping JSON into tmp/ref/<c>/. See SKILL.md "
        f"Pipeline section for the step → artifact mapping."
    )


def _guard_static_mirror_download(cmd: str) -> str | None:
    if not _static_mirror_download_violation(cmd):
        return None
    return (
        "⛔ UI-RE static mirror blocked: copying live HTML/CSS/JS "
        "into impl/public is not a React/Tailwind clone and does not "
        "produce gateable implementation evidence. Continue the "
        "ui_clone pipeline, finish extraction/spec/pre-generate, then "
        "implement source code instead of mirroring the live site."
    )


def _guard_static_server(
    cmd: str, project_root: Path, payload_cwd: Path | None
) -> str | None:
    if not _static_server_violation(cmd):
        return None
    ref_dir = _ref_dir_for_static_guard(project_root, payload_cwd, cmd)
    if ref_dir is not None and not _state_before_gate(ref_dir, "post-implement"):
        return None
    gate = "missing" if ref_dir is None else PipelineState.load(ref_dir).current_gate
    if ref_dir is None:
        next_action = (
            "Locate the active tmp/ref/<component> first, then advance the "
            "pipeline to post-implement before starting a server."
        )
    elif gate == "state-coverage":
        next_action = (
            f"Run `python -m ui_clone.gate {ref_dir} state-coverage` and "
            "follow that gate's next-action (usually state/scroll/hover "
            "capture or source wiring). Do not inspect hook or gate source "
            "to bypass this ordering."
        )
    else:
        next_action = (
            f"Advance the pipeline for {ref_dir} until current_gate is "
            "`post-implement`; use `python -m ui_clone.goal "
            f"{ref_dir}` for the next required step. Do not inspect hook or "
            "gate source to bypass this ordering."
        )
    return (
        "⛔ UI-RE static server blocked before post-implement: "
        f"current gate is {gate}. A local server is verification "
        "surface, not an implementation shortcut. "
        f"{next_action}"
    )


def _guard_section_compare(cmd: str, project_root: Path) -> str | None:
    if not _SECTION_COMPARE_COMMAND_PATTERNS.search(cmd):
        return None
    ref_dir = _find_active_ref(project_root / "tmp" / "ref")
    if ref_dir is None:
        return None
    return _section_compare_precondition_reason(ref_dir, cmd)


def _resolve_ref_dir_for_write(bash_write: str, project_root: Path) -> Path | None:
    """Walk up from the write target looking for the nearest tmp/ref/."""
    ref_dir: Path | None = None
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
    return ref_dir


def _guard_bash_write_component(
    bash_write: str, project_root: Path, session_id: str
) -> tuple[str | None, bool]:
    """Run the pre-generate gate when a Bash redirect targets a component file.

    Returns `(reason, halt)`:
      - reason: block payload to emit, or None.
      - halt: True when main() should `sys.exit(0)` immediately after, False
        when the caller should fall through to declaration checks.
    """
    ref_dir = _resolve_ref_dir_for_write(bash_write, project_root)
    if ref_dir is None:
        return None, True
    gate_result = run_gate(ref_dir, "pre-generate")
    if gate_result.get("passed", True):
        mark_ref_session(ref_dir, session_id, source="pre_bash_component_write")
        return None, False
    failures: list[dict[str, str]] = cast(
        list[dict[str, str]], gate_result.get("failures", [])
    )
    fail_count = cast(int, gate_result.get("fail_count", len(failures)))
    missing = ", ".join(f.get("label", "?") for f in failures[:6])
    return (
        (
            f"⛔ UI-RE: Bash write to component file '{bash_write}' blocked — "
            f"extraction incomplete ({fail_count} artifacts missing: {missing}).\n"
            f"This bypass route (cat>/tee/sed -i) goes through the same gate as Edit/Write.\n"
            f"Complete Phase 2 extraction before writing components.\n"
        ),
        True,
    )


def _build_section_compare_block(
    cmd: str, ref_dir: Path, fail_count: int, missing_count: int
) -> str:
    return (
        f"⛔ UI-RE: cannot run '{cmd.split(chr(10))[0][:60]}' — "
        f"section-compare shows {fail_count} FAIL, {missing_count} MISSING.\n"
        f"Fix diffs in {ref_dir}/sections/diff/ and re-run:\n"
        f"  bash $SCRIPTS_DIR/section-compare.sh <orig> <impl> <session> {ref_dir}\n"
        f"Then: python -m ui_clone.gate {ref_dir} section-compare"
    )


def _build_pipeline_incomplete_block(cmd: str, ref_dir: Path, remaining: str) -> str:
    return (
        f"⛔ UI-RE: cannot run '{cmd.split(chr(10))[0][:60]}' — "
        f"pipeline incomplete. Current gate: {remaining}.\n"
        f"Run: python -m ui_clone.gate {ref_dir} {remaining}"
    )


def _build_gate_failure_block(
    cmd: str, ref_dir: Path, gate_name: str, gate_result: dict
) -> str:
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
    )
    return "\n".join(parts)


def _run_declaration_cascade(cmd: str, project_root: Path, session_id: str) -> None:
    """Final flow: declaration + section-compare result.txt + gate fallback.

    Exits the process (`sys.exit(0)`) when it emits a block or decides the
    command may proceed. Caller passes through only when `is_decl` was True
    or a Bash write to a component file already passed the pre-generate
    gate (the "fall through" case from `_guard_bash_write_component`).
    """
    ref_dir = _find_active_ref(project_root / "tmp" / "ref")
    if ref_dir is None:
        # Off-pipeline completion closure (omx postmortem): the session
        # browsed an external site AND wrote clone-shaped files, but owns no
        # ref dir — a declaration command (commit/push/PR) here ships a
        # scratch clone no gate ever measured. Same bootstrap guidance and
        # escape hatch as the pre_generate guard.
        if (
            os.environ.get("UI_RE_SKIP_BASH_GATE") != "1"
            and os.environ.get("UI_RE_ALLOW_OFFPIPELINE") != "1"
            and session_id
            and _is_declaration_command(cmd)
            and has_external_browse(project_root, session_id)
            and has_clone_writes(project_root, session_id)
        ):
            _emit_block(
                "UI Reverse Engineering: completion command in a session that "
                "browsed an external site and wrote clone-shaped files with NO "
                "tmp/ref/<component> evidence directory — committing an "
                "unverified scratch clone is the documented ship-short failure "
                "mode (omx postmortem). Enter the pipeline first: "
                "`python -m ui_clone.pipeline <url> <component> <session> run "
                "--phases 0A,1,2`. (Non-clone work: the off-pipeline escape "
                "hatch is documented for HUMANS in docs/agent-cli.md — ask the "
                "user.)"
            )
            sys.exit(0)
        sys.exit(0)
    if not should_enforce_ref_for_session(ref_dir, session_id):
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
        if fail_count > 0 or missing_count > 0:
            _emit_block(_build_section_compare_block(cmd, ref_dir, fail_count, missing_count))
            sys.exit(0)

    # No result.txt at all OR result.txt clean but state isn't done — run the gate
    # and report what's actually missing. This avoids hardcoded message drift.
    gate_name = (
        "section-compare"
        if state.current_gate in ("section-compare", "done")
        else state.current_gate
    )
    gate_result = run_gate(ref_dir, gate_name)

    if gate_result.get("passed", True):
        # Gate passes (rare with no result.txt — could be 'reference' fail-open)
        # but state didn't say done. Re-load — Gate.run() may have advanced it.
        state = PipelineState.load(ref_dir)
        if state.current_gate == "done":
            sys.exit(0)
        _emit_block(_build_pipeline_incomplete_block(cmd, ref_dir, state.current_gate))
        sys.exit(0)

    _emit_block(_build_gate_failure_block(cmd, ref_dir, gate_name, gate_result))
    sys.exit(0)


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

    cmd = extract_tool_command(data)
    if not isinstance(cmd, str):
        sys.exit(0)

    payload_cwd = _resolve_payload_cwd(data)
    project_root = find_project_root()
    session_id = session_id_from_payload(data)

    reason = _guard_claude_continuation(cmd, project_root, session_id, payload_cwd)
    if reason is not None:
        _emit_block(reason)
        sys.exit(0)

    _mark_ui_re_session(cmd, project_root, session_id, payload_cwd)
    # Off-pipeline clone detection: remember external agent-browser browsing
    # so pre_generate can recognize clone-shaped work without a ref dir.
    # Always anchored at project_root — pre_generate reads from project_root,
    # and a cwd-anchored breadcrumb written from a subdirectory would be
    # invisible to enforcement (Codex review).
    mark_external_browse(cmd, project_root, session_id)

    # Each guard returns a block reason string or None. First match wins.
    skip = os.environ.get("UI_RE_SKIP_BASH_GATE") == "1"

    if not skip:
        for guard_fn in (
            _guard_whole_document_mirror,
            _guard_static_html_mirror,
            _guard_scratch_nested_ref,
            _guard_enforcement_state_rm,
            _guard_verification_plan_ack,
            _guard_adhoc_redirect,
        ):
            reason = guard_fn(cmd)
            if reason is not None:
                _emit_block(reason)
                sys.exit(0)

        # Hook slimming B (Fable+Codex review): the fresh-folder guard is
        # retired. Out-of-order extraction is self-correcting — pre-generate
        # blocks component writes without artifacts and every gate fails on
        # missing artifacts — and the degenerate "mirror the live site into
        # impl/public" case it partly covered is fully caught by the retained
        # static-mirror family (verified: wget/curl mirrors still deny). The
        # guard's cost was a broad ordering-nanny that false-positived on
        # inspection commands (visual-judge blocked via a persisted cwd). The
        # onboarding nudge ("run the pipeline driver first") lives in the
        # SessionStart session_resume path. The _is_fresh_state /
        # _fresh_state_violation predicates stay (public API, unit-tested).

        reason = _impl_scaffold_violation(cmd, project_root, cwd=payload_cwd)
        if reason is not None:
            _emit_block(reason)
            sys.exit(0)

        reason = _guard_static_mirror_download(cmd)
        if reason is not None:
            _emit_block(reason)
            sys.exit(0)

        # Hook slimming B: demoted from a hard block to an advisory warning. A
        # local server started before post-implement is a verification surface,
        # not itself a shipped clone — and the real ship-short risk (declaring
        # done on an HTTP-200 / mirror) is backstopped by the Stop verify-stamp
        # gate, which still requires a passing verify before closeout. Kept as a
        # warning (not deleted) because the static-mirror family does NOT cover
        # "server started too early", so the nudge still has unique value.
        reason = _guard_static_server(cmd, project_root, payload_cwd)
        if reason is not None:
            _emit_warn(reason)

    reason = _guard_section_compare(cmd, project_root)
    if reason is not None:
        _emit_block(reason)
        sys.exit(0)

    is_decl = _is_declaration_command(cmd)
    bash_write = _bash_write_target(cmd)
    if not is_decl and bash_write is None:
        sys.exit(0)

    if bash_write is not None:
        reason, halt = _guard_bash_write_component(bash_write, project_root, session_id)
        if reason is not None:
            _emit_block(reason)
            sys.exit(0)
        if halt and not is_decl:
            sys.exit(0)

    _run_declaration_cascade(cmd, project_root, session_id)
