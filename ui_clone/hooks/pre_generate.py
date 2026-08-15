"""
Pre-generation check hook for ui-reverse-engineering.
Blocks Write/Edit on component files when extraction pipeline is incomplete.

Usage: python -m ui_clone.hooks.pre_generate
Reads PreToolUse JSON from stdin.
Exit 0 = allow; exit 0 with JSON on stdout = block.

Environment variables:
    UI_RE_COMPONENT_PATHS  — colon-separated list of path substrings to enforce
                             (overrides the built-in defaults)
    Example: UI_RE_COMPONENT_PATHS=/src/components/:/app/components/:/src/pages/
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import cast

from ui_clone.hooks._common import extract_tool_file_paths as _extract_tool_file_paths
from ui_clone.hooks._common import find_project_root as _find_project_root
from ui_clone.hooks._common import find_ref_dir as _find_ref_dir
from ui_clone.hooks._common import has_external_browse as _has_external_browse
from ui_clone.hooks._common import is_ad_hoc_ref_artifact as _is_ad_hoc_ref_artifact
from ui_clone.hooks._common import is_component_file as _is_component_file
from ui_clone.hooks._common import mark_clone_write
from ui_clone.hooks._common import mark_ref_session as _mark_ref_session
from ui_clone.hooks._common import run_gate as _run_gate_common
from ui_clone.hooks._common import session_id_from_payload as _session_id_from_payload
from ui_clone.state import PipelineState, is_authoritative_terminal_state

# Clone-shaped write detection for the widened off-pipeline guard: the file
# classes a scratch clone produces. Repo-infrastructure segments are exempt
# so ordinary plugin development never false-positives.
_CLONE_WRITE_SUFFIXES = (".html", ".jsx", ".tsx", ".vue", ".svelte", ".css")
_INFRA_SEGMENTS = (
    "/skills/",
    "/ui_clone/",
    "/scripts/",
    "/tests/",
    "/hooks/",
    "/docs/",
    "/.claude",
    "/node_modules/",
)


def _is_clone_shaped_write(path: str) -> bool:
    p = str(path)
    if not p.endswith(_CLONE_WRITE_SUFFIXES):
        return False
    normalized = "/" + p.replace("\\", "/").lstrip("/")
    return not any(seg in normalized for seg in _INFRA_SEGMENTS)


# ── N1 residual: closeout-provenance write-guard (Write/Edit route) ──
#
# bash_write.py's _ENFORCEMENT_STATE_RE blocks SHELL writes to pipeline-state.json
# / verify-stamp.json, but the Write/Edit/MultiEdit tools route through THIS hook,
# which did not guard them. So an agent could Edit pipeline-state.json to set
# terminalState.writtenBy="pipeline" — a self-attested closeout-provenance forge on
# a security boundary (N1 Part 1 makes that useless for a SUCCESS release, but it
# still lets a non-success terminal skip the result.txt pin and forges provenance).
#
# verify-stamp.json has no legitimate hand-edit path → deny any tool write.
# pipeline-state.json has ONE blessed hand-edit: setting closeoutPolicy
# (canvas-replay-mode.md Step 3). Deny only writes whose payload text touches the
# closeout-provenance keys (terminalState / writtenBy); allow the rest so the
# documented closeoutPolicy edit keeps working. The match runs over BOTH the
# added AND removed text so it catches both forging a terminal (new content) and
# hand-clearing a hard-cap-fail terminal (removed content) — neither is a
# documented Edit-tool flow (recovery is via `python -m ui_clone.state ... clear`).
#
# Canonical writers are the `python -m ui_clone.state|pipeline` CLIs, which run as
# Bash (governed by pre_bash, not this hook), so they are unaffected.
_VERIFY_STAMP_NAME = "verify-stamp.json"
_PIPELINE_STATE_NAME = "pipeline-state.json"
_CLOSEOUT_PROVENANCE_RE = re.compile(
    r"terminalState|terminal_state|writtenBy|written_by"
)
# sections/result.txt(+.json) is the sha256-stamped section verdict the
# post-implement gate trusts. It is written by section-compare (Bash, path built
# from the ref-dir arg) and never hand-edited — a tool write is an attempt to
# forge an all-PASS before verify stamps it. Path-qualified so a build/result.txt
# (or transitions/result.txt, a different evidence file) is not over-matched.
_SECTION_RESULT_RE = re.compile(r"(?:^|/)sections/result\.(?:txt|json)$", re.IGNORECASE)
# gateSkipAck/deferredAck in verification-plan.json dissolve closeout blockers
# (gate_skip_blocker / deferred_checks_blocker). An agent-set ack self-releases an
# un-enforced/deferred run, so a tool write touching either key is denied; an
# ack-free plan write (e.g. listing deferredChecks debt) stays allowed.
_VERIFICATION_PLAN_NAME = "verification-plan.json"
_VERIFICATION_PLAN_ACK_RE = re.compile(r"gateSkipAck|deferredAck")
# Closeout/identity stamps with NO legitimate hand-edit path — produced by
# check-converged.sh / check-canvas-replay.sh / register-driver-session.sh. An
# agent hand-writing one forces convergence/closeout or forges a driver identity,
# so any tool write is denied (mirrors verify-stamp.json). Case-folded basenames.
_NO_HANDEDIT_CLOSEOUT_NAMES = frozenset({
    "structural-convergence-stamp.json",
    "canvas-replay-stamp.json",
    ".driver-session.id",
})


def _tool_write_text(payload: dict[str, object] | None) -> str:
    """Concatenate every write-content string a Write/Edit/MultiEdit/apply_patch
    payload carries (content, new/old strings, per-edit strings, patch/diff body)
    so the provenance scan sees both added and removed text. Reads Claude-shaped
    (`tool_input`) and Codex-shaped (top-level) payloads."""
    if not isinstance(payload, dict):
        return ""
    chunks: list[str] = []
    containers: list[dict[str, object]] = [payload]
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        containers.insert(0, tool_input)
    text_keys = (
        "content",
        "new_string",
        "old_string",
        "new_str",
        "old_str",
        "patch",
        "input",
        "diff",
    )
    for container in containers:
        for key in text_keys:
            value = container.get(key)
            if isinstance(value, str):
                chunks.append(value)
        edits = container.get("edits")
        if isinstance(edits, list):
            for edit in edits:
                if isinstance(edit, dict):
                    for key in text_keys:
                        value = edit.get(key)
                        if isinstance(value, str):
                            chunks.append(value)
    return "\n".join(chunks)


def _closeout_provenance_block_reason(
    file_paths: list[str], payload: dict[str, object] | None
) -> str | None:
    """Return a deny reason if a tool write targets verify-stamp.json, or targets
    pipeline-state.json while touching closeout-provenance keys; else None.

    Matched by case-folded basename (the enforcement files live at
    `tmp/ref/<component>/`, but we match anywhere to mirror the bash guard and
    resist relative-path tricks). Case-folding is required: on case-insensitive
    filesystems (macOS APFS, Windows NTFS) a write to `Pipeline-State.JSON`
    lands on the real `pipeline-state.json`, so a case-sensitive compare would
    let a one-character rename bypass the guard. It never over-blocks a real
    flow — no legitimate flow writes any case-variant of these names.
    pipeline-state.json fails TOWARD blocking: when the write content cannot be
    read (empty/unparseable payload) it is denied, since the only blessed edit
    (closeoutPolicy) always carries readable content."""
    cli_hint = (
        "Use the canonical CLI (`python -m ui_clone.state ...` / "
        "`python -m ui_clone.pipeline ...`), which records provenance the Stop "
        "gate trusts. Do NOT hand-write closeout state."
    )
    for path in file_paths:
        name = Path(path).name.lower()
        norm = str(path).replace("\\", "/")
        if _SECTION_RESULT_RE.search(norm):
            return (
                "UI Reverse Engineering: sections/result.txt (and result.json) is "
                "the sha256-stamped section verdict the post-implement gate trusts. "
                "It is produced by section-compare, not hand-written — a tool write "
                "here forges an all-PASS that the verify stamp would then bless. "
                "Re-run section-compare (skills/visual-debug/scripts/section-compare.sh) "
                "to regenerate it honestly."
            )
        if name == _VERIFY_STAMP_NAME:
            return (
                "UI Reverse Engineering: verify-stamp.json is closeout provenance "
                "(the Stop gate trusts it as proof the gates ran) and has no "
                f"hand-edit path. {cli_hint}"
            )
        if name in _NO_HANDEDIT_CLOSEOUT_NAMES:
            return (
                f"UI Reverse Engineering: {Path(path).name} is a closeout/identity "
                "stamp produced by its verify/register script (check-converged.sh / "
                "check-canvas-replay.sh / register-driver-session.sh) and has no "
                "hand-edit path — a tool write forges convergence/closeout or a "
                f"driver identity. {cli_hint}"
            )
        if name == _VERIFICATION_PLAN_NAME:
            text = _tool_write_text(payload)
            if _VERIFICATION_PLAN_ACK_RE.search(text):
                return (
                    "UI Reverse Engineering: this write to verification-plan.json "
                    "sets an ack key (gateSkipAck/deferredAck) that dissolves a "
                    "closeout blocker — a self-granted release of an un-enforced or "
                    "deferred run. The ack is an explicit USER decision, not an "
                    "agent edit. Re-run the gate / regenerate the plan instead; if "
                    "the user genuinely accepts the un-enforced run they record the "
                    "ack outside the agent."
                )
        if name == _PIPELINE_STATE_NAME:
            text = _tool_write_text(payload)
            if not text.strip() or _CLOSEOUT_PROVENANCE_RE.search(text):
                return (
                    "UI Reverse Engineering: this write to pipeline-state.json "
                    "touches closeout-provenance (terminalState/writtenBy) — a "
                    "self-attested terminal/provenance forge the Stop gate would "
                    f"trust. {cli_hint} (Setting only `closeoutPolicy` per "
                    "canvas-replay-mode.md is still allowed.)"
                )
    return None


def _run_gate(ref_dir: Path) -> dict[str, object]:
    return _run_gate_common(ref_dir, "pre-generate")


# ── Emit block JSON ──


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


# ── Main ──


def main() -> None:
    # Read tool input from stdin
    raw_input = sys.stdin.read() if not sys.stdin.isatty() else ""
    file_paths: list[str] = []
    payload: dict[str, object] | None = None
    if raw_input.strip():
        try:
            data = json.loads(raw_input)
            if isinstance(data, dict):
                payload = data
                file_paths = _extract_tool_file_paths(data)
        except json.JSONDecodeError:
            pass
    session_id = _session_id_from_payload(payload)

    # ── Ad-hoc ref-artifact denial ──
    # Block Write/Edit to non-canonical top-level *.json names under
    # `<any>/tmp/ref/<component>/`. This is the v0.5.0+1 fix: nested agents
    # given a natural-language prompt tend to invent artifact names like
    # `sections.json`, `content-detail.json`, `key-sections.json`,
    # `styles-core.json` instead of running the canonical scripts that
    # produce `section-map.json`, `structure.json`, `styles.json`. Deny the
    # Write and point at the right script so the canonical pipeline runs.
    for candidate_path in file_paths:
        is_adhoc, suggested = _is_ad_hoc_ref_artifact(candidate_path)
        if is_adhoc:
            basename = Path(candidate_path).name
            if suggested:
                reason = (
                    f"UI Reverse Engineering: '{basename}' is not a canonical "
                    f"ref-dir artifact name. Use '{suggested}' produced by the "
                    f"matching pipeline script (e.g. `bash $PLUGIN_ROOT/skills/"
                    f"visual-debug/scripts/dom-scaffold.sh <ref-dir>` for "
                    f"section-map.json, or `python -m ui_clone.pipeline` for "
                    f"the full sequence). Do NOT hand-write artifacts."
                )
            else:
                reason = (
                    f"UI Reverse Engineering: '{basename}' is not a canonical "
                    f"ref-dir artifact. Run the canonical extraction scripts "
                    f"under skills/visual-debug/scripts/ instead of hand-writing "
                    f"ref artifacts. See SKILL.md Pipeline section for the "
                    f"step → artifact mapping."
                )
            _emit_block(reason)
            sys.exit(0)

    # ── Closeout-provenance write-guard (N1 residual) ──
    # Deny Write/Edit/MultiEdit forging closeout provenance in pipeline-state.json
    # / verify-stamp.json. These are canonical artifact names (so the ad-hoc check
    # above intentionally exempts them), and they are not component files (so the
    # component-only filter below would exit before any gate) — meaning without
    # this guard a tool write to them sails through. Mirrors the bash write-guard.
    provenance_reason = _closeout_provenance_block_reason(file_paths, payload)
    if provenance_reason is not None:
        _emit_block(provenance_reason)
        sys.exit(0)

    # Off-pipeline widened guard (omx postmortem follow-up): a scratch clone
    # writes plain index.html + styles.css — NOT component paths — so the
    # component-only filter below exited before the off-pipeline guard ever
    # ran. When this session's external-browse breadcrumb exists, ANY
    # markup/style write outside repo-infrastructure paths is clone-shaped.
    if session_id and file_paths:
        import os as _os

        clone_shaped = [p for p in file_paths if _is_clone_shaped_write(p)]
        if clone_shaped:
            wide_root = _find_project_root()
            ref_root = wide_root / "tmp" / "ref"
            has_pipeline_ref = ref_root.is_dir() and any(
                d.is_dir() for d in ref_root.iterdir()
            )
            if not has_pipeline_ref:
                # Record the write evidence UNCONDITIONALLY (cheap, per
                # session): the Stop gate and the declaration cascade key on
                # the CORRELATED PAIR (browse crumb + clone-write crumb), and
                # writes may precede the browse — gating the marker on the
                # browse crumb would miss the write-first ordering.
                mark_clone_write(wide_root, session_id, clone_shaped)
            if (
                not has_pipeline_ref
                and _has_external_browse(wide_root, session_id)
            ):
                if _os.environ.get("UI_RE_ALLOW_OFFPIPELINE") != "1":
                    _emit_block(
                        "UI Reverse Engineering: this session opened an external "
                        "site via agent-browser and is now writing markup/style "
                        f"files ({Path(clone_shaped[0]).name}) with NO "
                        "tmp/ref/<component> evidence directory — clone-shaped "
                        "work outside the pipeline ships unverified (omx "
                        "postmortem: 1593px missing, completion declared on "
                        "build/smoke checks). Enter the pipeline first: "
                        "`python -m ui_clone.pipeline <url> <component> "
                        "<session> run --phases 0A,1,2`. (Non-clone work: the "
                        "off-pipeline escape hatch is documented for HUMANS in "
                        "docs/agent-cli.md — ask the user.)"
                    )
                    sys.exit(0)

    # Only enforce on component/page files
    component_paths = [path for path in file_paths if _is_component_file(path)]
    file_path = component_paths[0] if component_paths else ""
    if not _is_component_file(file_path):
        sys.exit(0)

    # Derive project root from the file being edited when possible.
    # This prevents cross-project ref dir pollution (e.g., editing
    # project-a/src/... but hook finds project-b/tmp/ref/).
    project_root = None
    if file_path:
        fp = Path(file_path).resolve()
        # Walk up from file path looking for tmp/ref/
        cur = fp.parent
        while cur != cur.parent:
            if (cur / "tmp" / "ref").is_dir():
                project_root = cur
                break
            cur = cur.parent
    if project_root is None:
        project_root = _find_project_root()

    search_root = project_root / "tmp" / "ref"
    ref_dir = _find_ref_dir(search_root)

    # No ref dir → either (a) not a ui-re project (legitimate exit
    # silently) or (b) the entry-bypass: agent writes directly to
    # impl/src/ without ever running extraction, so no tmp/ref/<c>
    # exists to gate against. Detect (b) by checking that the project
    # carries the ui-reverse-engineering SKILL.md AND the file is in an
    # impl/src or impl/app dir.
    if ref_dir is None:
        # Off-pipeline clone guard (omx postmortem): this session browsed an
        # EXTERNAL site via agent-browser and is now writing component files
        # with no ref dir anywhere — clone-shaped work outside the pipeline.
        # Unlike the impl-path check below, this fires in ANY project (the
        # omx run was on a machine where the SKILL.md co-location check never
        # matched), and offers an explicit escape hatch for false positives.
        import os as _os
        if (
            session_id
            and _os.environ.get("UI_RE_ALLOW_OFFPIPELINE") != "1"
            and _has_external_browse(project_root, session_id)
        ):
            _emit_block(
                "UI Reverse Engineering: this session opened an external site "
                "via agent-browser and is now writing component files with NO "
                "tmp/ref/<component> evidence directory — clone-shaped work "
                "outside the pipeline ships unverified (no font-parity, no "
                "section-compare, no motion checks). Enter the pipeline first: "
                "`python -m ui_clone.pipeline <url> <new-component> <session> "
                "run --phases 0A,1,2`. (Non-clone work: the escape hatch is "
                "documented for HUMANS in the repo docs — ask the user.)"
            )
            sys.exit(0)
        fp_str = str(Path(file_path).resolve()) if file_path else ""
        ui_re_skill = (
            project_root
            / "skills"
            / "ui-reverse-engineering"
            / "SKILL.md"
        )
        is_ui_re_impl = (
            ui_re_skill.is_file()
            and ("/impl/src/" in fp_str or "/impl/app/" in fp_str)
        )
        if is_ui_re_impl:
            _emit_block(
                "UI Reverse Engineering: impl-side write detected but "
                "NO tmp/ref/<component> directory exists. The pipeline "
                "requires extraction (Phase 2) BEFORE generation. Run "
                "`python -m ui_clone.pipeline <url> <component> "
                "<session> extract` first, then re-run this edit. "
                "(This block closes the validation run entry bypass where "
                "an agent skipped extraction and wrote impl directly.)"
            )
            sys.exit(0)
        # Otherwise treat as a non-ui-re project and exit silently.
        sys.exit(0)

    # The marker is the activation signal for downstream hooks (Stop / Bash /
    # SessionStart / PostCompact). It is *created* on the first passing
    # pre-generate gate run (further down). Until then it doesn't exist — but
    # we still want to run the gate on a component-file edit so the agent
    # gets blocked on missing extraction artifacts. Marker presence is *not*
    # a precondition for blocking; it's a side-effect that activates the
    # rest of the enforcement chain.
    marker = ref_dir / ".ui-re-active"
    state = PipelineState.load(ref_dir)

    # Terminal-ref variant of the off-pipeline guard: the found ref is
    # intentionally over (terminal), this session browsed an external site,
    # and is writing component files — a NEW clone needs a NEW ref dir, not
    # unverified edits riding under a closed run (the omx run did exactly
    # this: old refs terminal, new clone hand-built, zero gates).
    import os as _os
    if (
        is_authoritative_terminal_state(state.terminal_state)
        and session_id
        and _os.environ.get("UI_RE_ALLOW_OFFPIPELINE") != "1"
        and _has_external_browse(project_root, session_id)
    ):
        _emit_block(
            f"UI Reverse Engineering: {ref_dir.name} is TERMINAL "
            f"({state.terminal_state.get('status')}) but this session opened "
            "an external site and is writing component files. A new clone "
            "requires a NEW evidence directory: `python -m ui_clone.pipeline "
            "<url> <new-component> <session> run --phases 0A,1,2`. Do not "
            "build unverified work under a closed run. (Non-clone work: the "
            "escape hatch is documented for HUMANS in the repo docs — ask "
            "the user.)"
        )
        sys.exit(0)

    # Post-done invalidation only fires when there's an active session
    # (marker exists). Without the marker, no other hook is enforcing, and
    # there's no stale gate state to retract.
    if marker.is_file() and state.current_gate == "done":
        try:
            state.demote_to("section-compare", ref_dir)
            # Move (not delete) result.txt → audit trail of prior PASS state.
            result_file = ref_dir / "sections" / "result.txt"
            if result_file.is_file():
                stale_path = result_file.with_suffix(".txt.stale")
                # If a previous .stale file exists, overwrite — only the most
                # recent stale state is interesting.
                try:
                    if stale_path.exists():
                        stale_path.unlink()
                    result_file.rename(stale_path)
                except OSError:
                    pass
            print(
                "⚑  UI-RE: post-done edit detected — pipeline state demoted to 'section-compare'. "
                "sections/result.txt invalidated. Re-run section-compare before declaring done.",
                file=sys.stderr,
            )
        except OSError:
            pass

    # Run pre-generate gate
    gate_result = _run_gate(ref_dir)

    if not gate_result.get("passed", True):
        failures: list[dict[str, str]] = cast(list[dict[str, str]], gate_result.get("failures", []))
        fail_count = cast(int, gate_result.get("fail_count", len(failures)))
        if failures:
            missing_list = ", ".join(f["label"] for f in failures[:8])
            reason = (
                f"UI Reverse Engineering: extraction incomplete "
                f"({fail_count} artifacts missing). Missing: {missing_list}. "
                f"Complete Phase 2 before writing components."
            )
        else:
            reason = (
                "UI Reverse Engineering: pre-generate gate FAILED. "
                "Run: python -m ui_clone.gate tmp/ref/<component> pre-generate"
            )
        _emit_block(reason)
        sys.exit(0)

    # Gate passed — ensure marker exists. Path.touch() creates the file if
    # absent, refreshes mtime if present. First-time creation here is the
    # documented activation site for the Stop / Bash / SessionStart /
    # PostCompact hooks. Print the activation message only on first creation
    # so subsequent edits don't spam the agent.
    was_new = not marker.is_file()
    try:
        marker.touch()
        _mark_ref_session(ref_dir, session_id, source="pre_generate")
        if was_new:
            print(
                "⚑  UI-RE Stop gate ACTIVATED: section-compare must pass before finishing.",
                file=sys.stderr,
            )
    except OSError:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
