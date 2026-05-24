"""Orchestrator for the PreToolUse Bash hook.

Reads stdin, decodes the PreToolUse payload, and walks the rule families
in deny-precedence order. Each guard returns a (reason, exit) tuple via
the `_emit_block` shortcut — first match wins.

Kept as a separate module so `pre_bash.py` is a thin importable entry
point and the rule families stay independently testable / readable.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import cast

from ui_clone.hooks._common import find_project_root, find_ref_dir, run_gate
from ui_clone.state import PipelineState

from .bash_write import (
    _bash_adhoc_ref_target,
    _bash_scratch_nested_ref_target,
    _bash_write_target,
)
from .declaration import _is_declaration_command
from .impl_scaffold import _impl_scaffold_violation
from .ref_state import (
    _find_active_ref,
    _fresh_state_violation,
    _is_fresh_state,
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


def _emit_block(reason: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(payload, ensure_ascii=False))


def _resolve_payload_cwd(data: dict) -> Path | None:
    payload_cwd_raw = data.get("cwd", "")
    if isinstance(payload_cwd_raw, str) and payload_cwd_raw:
        candidate = Path(payload_cwd_raw)
        if candidate.is_dir():
            return candidate.resolve()
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
        f"  python -m ui_clone.pipeline <url> <component> <session> run\n\n"
        f"Bypass (emergency only): UI_RE_SKIP_BASH_GATE=1 <command>"
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


def _guard_fresh_state(
    cmd: str, project_root: Path, payload_cwd: Path | None
) -> str | None:
    if not _is_fresh_state(project_root, cwd=payload_cwd):
        return None
    if not _fresh_state_violation(cmd):
        return None
    example_component = "site"
    example_session = "ref-capture"
    return (
        f"⛔ UI-RE fresh-folder enforcement: tmp/ref/ has no Phase 1 "
        f"evidence yet, so direct extraction commands are blocked.\n"
        f"Run the pipeline driver FIRST:\n"
        f"  python -m ui_clone.pipeline <URL> {example_component} "
        f"{example_session} run --phases 0A,1,2\n"
        f"It invokes capture.sh + extract-dom.sh + dom-scaffold.sh "
        f"in the right order and produces canonical artifacts.\n"
        f"Inspection commands (which / command -v / ls / cat / "
        f"`python -m ui_clone.pipeline ... status`) still pass.\n"
        f"Bypass (emergency only, voids measurement signal): "
        f"UI_RE_SKIP_BASH_GATE=1 <command>"
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
    return (
        "⛔ UI-RE static server blocked before post-implement: "
        f"current gate is {gate}. A local server is verification "
        "surface, not an implementation shortcut. Run the pipeline "
        "through pre-generate and write the React/Tailwind source "
        "before starting a dev/static server."
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
    bash_write: str, project_root: Path
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
            f"Bypass (emergency only): UI_RE_SKIP_BASH_GATE=1 <command>"
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
        f"Bypass (emergency only): UI_RE_SKIP_BASH_GATE=1 <command>"
    )
    return "\n".join(parts)


def _run_declaration_cascade(cmd: str, project_root: Path) -> None:
    """Final flow: declaration + section-compare result.txt + gate fallback.

    Exits the process (`sys.exit(0)`) when it emits a block or decides the
    command may proceed. Caller passes through only when `is_decl` was True
    or a Bash write to a component file already passed the pre-generate
    gate (the "fall through" case from `_guard_bash_write_component`).
    """
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

    cmd = data.get("tool_input", {}).get("command", "") or data.get("command", "")
    if not isinstance(cmd, str):
        sys.exit(0)

    payload_cwd = _resolve_payload_cwd(data)
    project_root = find_project_root()

    # Each guard returns a block reason string or None. First match wins.
    skip = os.environ.get("UI_RE_SKIP_BASH_GATE") == "1"

    if not skip:
        for guard_fn in (
            _guard_whole_document_mirror,
            _guard_static_html_mirror,
            _guard_scratch_nested_ref,
            _guard_adhoc_redirect,
        ):
            reason = guard_fn(cmd)
            if reason is not None:
                _emit_block(reason)
                sys.exit(0)

        reason = _guard_fresh_state(cmd, project_root, payload_cwd)
        if reason is not None:
            _emit_block(reason)
            sys.exit(0)

        reason = _impl_scaffold_violation(cmd, project_root, cwd=payload_cwd)
        if reason is not None:
            _emit_block(reason)
            sys.exit(0)

        reason = _guard_static_mirror_download(cmd)
        if reason is not None:
            _emit_block(reason)
            sys.exit(0)

        reason = _guard_static_server(cmd, project_root, payload_cwd)
        if reason is not None:
            _emit_block(reason)
            sys.exit(0)

    reason = _guard_section_compare(cmd, project_root)
    if reason is not None:
        _emit_block(reason)
        sys.exit(0)

    is_decl = _is_declaration_command(cmd)
    bash_write = _bash_write_target(cmd)
    if not is_decl and bash_write is None:
        sys.exit(0)

    if bash_write is not None:
        reason, halt = _guard_bash_write_component(bash_write, project_root)
        if reason is not None:
            _emit_block(reason)
            sys.exit(0)
        if halt and not is_decl:
            sys.exit(0)

    _run_declaration_cascade(cmd, project_root)
