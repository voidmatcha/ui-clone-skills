"""execute_verify — drive post-generation gates.

The gates themselves live in ui_clone.gate; we shell out so the gate
module's argparse + exit codes stay the source of truth.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ui_clone.hooks._common import BOLD as _BOLD
from ui_clone.hooks._common import GREEN as _GREEN
from ui_clone.hooks._common import NC as _NC
from ui_clone.hooks._common import RED as _RED
from ui_clone.hooks._common import _clear_gate_skip
from ui_clone.hooks._common import deferred_checks_blocker as _deferred_checks_blocker
from ui_clone.hooks._common import gate_skip_blocker as _gate_skip_blocker
from ui_clone.hooks._common import quick_tier_blocker as _quick_tier_blocker
from ui_clone.pipeline_logs import (
    completed_process_output,
    log_tail_lines,
    tail_text,
    write_process_log,
)
from ui_clone.state import POST_IMPL_VERIFY_GATES, PipelineState
from ui_clone.verify_report import build_verify_report, write_verify_report

if TYPE_CHECKING:
    from ui_clone.pipeline import Pipeline


_MOTION_EVIDENCE_PINS = (
    ("transition-spec.json", "transitionSpecSha256"),
    ("transition-fires.json", "transitionFiresSha256"),
)

UTC = datetime.timezone.utc  # noqa: UP017 - macOS /usr/bin/python3 is still 3.9.


def _resolve_verify_impl_dir(ref_dir: Path, cwd: Path) -> Path:
    """Resolve the implementation tree that verify should stamp.

    Phase execution persists the canonical per-run impl root in
    pipeline-state.json and mirrors it to `.impl-root`. Verify must prefer that
    run-owned path over the repository-level `impl/` symlink; otherwise a
    stale symlink can make a stamp/report describe one clone while browser
    checks or operators inspect another.
    """
    env_root = (os.environ.get("UI_CLONE_IMPL_ROOT") or "").strip()
    candidates: list[str] = []
    if env_root:
        candidates.append(env_root)
    state_root = PipelineState.load(ref_dir).impl_root.strip()
    if state_root:
        candidates.append(state_root)
    marker = ref_dir / ".impl-root"
    try:
        marker_root = marker.read_text(encoding="utf-8").strip()
    except OSError:
        marker_root = ""
    if marker_root:
        candidates.append(marker_root)
    candidates.append(str(cwd / "impl"))

    for candidate in candidates:
        path = Path(candidate).expanduser()
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved.is_dir():
            return resolved
    return (cwd / "impl").resolve()


def _unmeasurable_motion_blocker(ref_dir: Path) -> str | None:
    """Block closeout when a declared transition could not be measured."""
    try:
        spec = json.loads(
            (ref_dir / "transition-spec.json").read_text(encoding="utf-8")
        )
        fires = json.loads(
            (ref_dir / "transition-fires.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None

    transitions = spec.get("transitions") if isinstance(spec, dict) else None
    if not isinstance(transitions, list) or not transitions:
        return None
    if not isinstance(fires, dict):
        return None

    ids = fires.get("unmeasurableIds")
    unmeasurable_ids = {
        str(transition_id)
        for transition_id in ids
        if transition_id is not None
    } if isinstance(ids, list) else set()
    entries = fires.get("entries")
    if isinstance(entries, list):
        unmeasurable_ids.update(
            str(entry.get("id") or "<unknown>")
            for entry in entries
            if isinstance(entry, dict) and entry.get("status") == "unmeasurable"
        )
    if not unmeasurable_ids:
        return None

    return (
        "transition-fires.json contains unmeasurable transitions for a non-empty "
        f"transition spec: {', '.join(sorted(unmeasurable_ids))}"
    )


def build_verify_stamp(ref_dir: Path, impl_dir: Path, gates: list[str]) -> dict:
    """Canonical verify-stamp payload, hash-pinned to closeout evidence.

    The structural-convergence stamp already pins result.txt by sha256
    (tamper-after-stamp detection); the canonical stamp gets the same pin so
    `goal --check-done` and the Stop hook can both verify the section
    evidence the stamp attests to is the evidence still on disk. Motion
    evidence is pinned for the same reason: a previously green stamp cannot
    attest a later transition spec or runtime measurement.
    """
    import hashlib

    stamp: dict = {
        "verifiedAt": datetime.datetime.now(UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "gatesPassed": list(gates),
        "stampedBy": "pipeline.execute_verify",
        "implDir": str(impl_dir),
        "refDir": str(ref_dir),
    }
    result_file = ref_dir / "sections" / "result.txt"
    if result_file.is_file():
        stamp["sectionsResultSha256"] = hashlib.sha256(
            result_file.read_bytes()
        ).hexdigest()
    for artifact_name, stamp_key in _MOTION_EVIDENCE_PINS:
        artifact = ref_dir / artifact_name
        if artifact.is_file():
            stamp[stamp_key] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    return stamp


def verify_stamp_evidence_problem(ref_dir: Path, stamp: dict) -> str | None:
    """Return a problem when motion evidence no longer matches its stamp pins."""
    import hashlib

    for artifact_name, stamp_key in _MOTION_EVIDENCE_PINS:
        artifact = Path(ref_dir) / artifact_name
        stamped_sha = stamp.get(stamp_key)
        if artifact.is_file():
            if not isinstance(stamped_sha, str) or not stamped_sha:
                return (
                    f"{artifact_name} exists but verify-stamp.json has no "
                    f"{stamp_key} evidence pin"
                )
            try:
                current_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
            except OSError as exc:
                return f"{artifact_name} could not be read ({exc})"
            if current_sha != stamped_sha:
                return (
                    f"{artifact_name} changed after the stamp "
                    f"(sha {current_sha[:12]}… != stamped {stamped_sha[:12]}…)"
                )
        elif stamped_sha is not None:
            return f"{artifact_name} was removed after the stamp"
    return None


def canonical_stamp_problem(ref_dir: Path, max_age_s: int = 1800) -> str | None:
    """Why the canonical verify-stamp does NOT currently attest completion.

    Shared by `goal --check-done` (external loop drivers) so it enforces the
    same stamp contract the Stop hook does: present, written by
    pipeline.execute_verify, covering every post-impl gate, fresh, and with
    a sections/result.txt hash pin that still matches the file on disk.
    Returns None when the stamp is valid.
    """
    import hashlib

    from ui_clone.state import POST_IMPL_VERIFY_GATES as _GATES

    stamp_path = Path(ref_dir) / "verify-stamp.json"
    if not stamp_path.is_file():
        return (
            "not satisfied: verify-stamp.json missing — run "
            "`python -m ui_clone.pipeline <url> <component> <session> verify`"
        )
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
        stamped_at = datetime.datetime.strptime(
            stamp["verifiedAt"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=UTC)
    except (KeyError, ValueError, json.JSONDecodeError, OSError) as exc:
        return f"not satisfied: malformed verify-stamp.json ({exc})"
    if stamp.get("stampedBy") != "pipeline.execute_verify":
        return (
            "not satisfied: verify-stamp.json not written by the canonical "
            f"verify (stampedBy={stamp.get('stampedBy')!r})"
        )
    gates_passed = {str(g) for g in stamp.get("gatesPassed") or []}
    missing = sorted(set(_GATES) - gates_passed)
    if missing:
        return f"not satisfied: stamp missing gate evidence: {', '.join(missing)}"
    age_s = (datetime.datetime.now(UTC) - stamped_at).total_seconds()
    if age_s > max_age_s:
        return (
            f"not satisfied: verify-stamp.json is {int(age_s)}s old "
            f"(max {max_age_s}s) — re-run the canonical verify"
        )
    result_file = Path(ref_dir) / "sections" / "result.txt"
    stamped_sha = stamp.get("sectionsResultSha256")
    if stamped_sha and result_file.is_file():
        current_sha = hashlib.sha256(result_file.read_bytes()).hexdigest()
        if current_sha != stamped_sha:
            return (
                "not satisfied: sections/result.txt changed after the stamp "
                f"(sha {current_sha[:12]}… != stamped {stamped_sha[:12]}…)"
            )
    evidence_problem = verify_stamp_evidence_problem(Path(ref_dir), stamp)
    if evidence_problem is not None:
        return f"not satisfied: {evidence_problem}"
    from ui_clone.hooks.section_gate import _newer_impl_files, _resolve_impl_dir

    impl_dir = _resolve_impl_dir(Path(ref_dir))
    if impl_dir is not None and impl_dir.is_dir():
        changed = _newer_impl_files(impl_dir, stamp_path)
        if changed:
            sample = ", ".join(str(path) for path in changed)
            return (
                "not satisfied: impl changed after verify "
                f"(newer than verify-stamp.json): {sample}"
            )
    # H1: a valid stamp is not enough if a fail-open gate skip was never recovered.
    # `goal --check-done` (this function) is a canonical driver-exit path and must
    # honor the same skip-ledger block the Stop hook's closeout paths do. Local
    # import to avoid a hooks<->pipeline_phases import cycle.
    from ui_clone.hooks._common import gate_skip_blocker

    skip_problem = gate_skip_blocker(Path(ref_dir))
    if skip_problem:
        return f"not satisfied: {skip_problem}"
    return None


def _write_gate_log(ref_dir: Path, gate_name: str, result: subprocess.CompletedProcess[str]) -> Path:
    return write_process_log(
        ref_dir,
        "verify",
        gate_name,
        completed_process_output(result),
        command=[sys.executable, "-m", "ui_clone.gate", str(ref_dir), gate_name],
        exit_code=result.returncode,
    )


def _split_root_cause_and_cascade(
    failures: list[str], state: PipelineState
) -> tuple[list[str], list[str]]:
    """Split failing gates into independent failures and ordering cascades.

    A gate whose prerequisites are unmet could not have passed no matter what the
    implementation looks like — `_check_pipeline_state_prerequisites` fails it
    before any of its own checks run, so it carries no independent evidence.
    Reporting those alongside real failures inflates the apparent work and buries
    the one gate an agent should actually act on.

    Uses the structural signal rather than matching gate stdout, so it cannot
    drift away from the guard that produces the cascade.
    """
    cascade = [g for g in failures if state.missing_prerequisites(g)]
    root = [g for g in failures if g not in cascade]
    return root, cascade


def execute_verify(pipeline: Pipeline, json_output: bool = False) -> int:
    gates_post_impl = POST_IMPL_VERIFY_GATES
    stamp_path = pipeline.ref_dir / "verify-stamp.json"
    try:
        stamp_path.unlink(missing_ok=True)
    except OSError as exc:
        message = f"verify: could not invalidate prior verify-stamp.json ({exc})"
        if json_output:
            print(json.dumps({
                "schemaVersion": 1,
                "status": "failed",
                "reason": message,
                "next_action": "remove_stale_verify_stamp",
                "verify_stamp": {
                    "path": str(stamp_path),
                    "created": False,
                    "success_only": True,
                },
            }, ensure_ascii=False, indent=2))
        else:
            print(f"{_RED}{message}{_NC}")
        return 1
    impl_dir = _resolve_verify_impl_dir(pipeline.ref_dir, Path.cwd())
    if not impl_dir.is_dir():
        message = (
            f"verify: impl/ not found at {impl_dir}. "
            "Generate components first, then re-run verify."
        )
        if json_output:
            print(json.dumps({
                "schemaVersion": 1,
                "status": "failed",
                "reason": message,
                "next_action": "generate_impl",
                "verify_stamp": {
                    "path": str(pipeline.ref_dir / "verify-stamp.json"),
                    "created": False,
                    "success_only": True,
                },
            }, ensure_ascii=False, indent=2))
        else:
            print(f"{_RED}{message}{_NC}")
        return 1

    tier_blocker = _quick_tier_blocker(pipeline.ref_dir)
    if tier_blocker is not None:
        if json_output:
            print(json.dumps({
                "schemaVersion": 1,
                "status": "failed",
                "reason": tier_blocker,
                "next_action": "regenerate_verification_plan",
                "verify_stamp": {
                    "path": str(pipeline.ref_dir / "verify-stamp.json"),
                    "created": False,
                    "success_only": True,
                },
            }, ensure_ascii=False, indent=2))
        else:
            print(f"{_RED}verify: {tier_blocker}{_NC}")
        return 1

    deferred_blocker = _deferred_checks_blocker(pipeline.ref_dir)
    if deferred_blocker is not None:
        if json_output:
            print(json.dumps({
                "schemaVersion": 1,
                "status": "failed",
                "reason": deferred_blocker,
                "next_action": "regenerate_verification_plan",
                "verify_stamp": {
                    "path": str(pipeline.ref_dir / "verify-stamp.json"),
                    "created": False,
                    "success_only": True,
                },
            }, ensure_ascii=False, indent=2))
        else:
            print(f"{_RED}verify: {deferred_blocker}{_NC}")
        return 1

    failures: list[str] = []
    gate_exit_codes: dict[str, int] = {}
    gate_logs: dict[str, str] = {}
    tail_limit = log_tail_lines()
    gate_env = os.environ.copy()
    gate_env["UI_CLONE_PHASE"] = "strict"
    for gate_name in gates_post_impl:
        if not json_output:
            print(f"\n{_BOLD}== verify: gate {gate_name}{_NC}")
        result = subprocess.run(
            [sys.executable, "-m", "ui_clone.gate", str(pipeline.ref_dir), gate_name],
            capture_output=True,
            text=True,
            env=gate_env,
        )
        log_path = _write_gate_log(pipeline.ref_dir, gate_name, result)
        gate_logs[gate_name] = str(log_path)
        gate_exit_codes[gate_name] = result.returncode
        # The gate actually ran here (capable host), pass OR fail — clear any
        # earlier fail-open skip so it stops blocking closeout (self-heal). The
        # gate_skip_blocker is re-checked AFTER this loop, so only gates that
        # were skipped and never re-enforced remain to block.
        _clear_gate_skip(pipeline.ref_dir, gate_name)
        if result.returncode != 0:
            failures.append(gate_name)
            if not json_output:
                print(
                    f"  {_RED}✗{_NC} {gate_name} exit {result.returncode} "
                    f"— continuing to surface every failure rather than short-circuit"
                )
                print(f"  log → {log_path}")
                tail = tail_text(completed_process_output(result), tail_limit)
                if tail:
                    print(f"  last {min(tail_limit, len(tail.splitlines()))} log line(s):\n{tail}")
        elif not json_output:
            print(f"  {_GREEN}✓{_NC} {gate_name} passed — log → {log_path}")
    if failures:
        report = build_verify_report(
            pipeline.ref_dir,
            gates=gates_post_impl,
            impl_dir=impl_dir,
            gate_exit_codes=gate_exit_codes,
        )
        json_path, html_path = write_verify_report(pipeline.ref_dir, report)
        next_action = (
            f"Read {json_path} and patch only the listed failures, then rerun "
            f"`python -m ui_clone.pipeline {pipeline.url} {pipeline.component} "
            f"{pipeline.session} verify`."
        )
        state = PipelineState.load(pipeline.ref_dir)
        root_gates, cascade_gates = _split_root_cause_and_cascade(failures, state)
        if root_gates:
            reason = (
                f"canonical verify failed {len(root_gates)} gate(s) on their own "
                f"evidence: {', '.join(root_gates)}"
            )
        else:
            reason = (
                f"canonical verify failed {len(failures)} gate(s): "
                f"{', '.join(failures)}"
            )
        if cascade_gates:
            reason += (
                f" ({len(cascade_gates)} further gate(s) blocked only by gate order, "
                f"carrying no independent evidence: {', '.join(cascade_gates)})"
            )
        state.mark_terminal(
            pipeline.ref_dir,
            status="failed",
            category="canonical-verify-failed",
            gate=(root_gates or failures)[0],
            reason=reason,
            detail={
                "failed_gates": failures,
                "root_cause_gates": root_gates,
                "cascade_gates": cascade_gates,
                "gate_exit_codes": gate_exit_codes,
                "verify_report": str(json_path),
                "verify_report_html": str(html_path),
                "gate_logs": gate_logs,
                "implDir": str(impl_dir),
            },
            next_action=next_action,
            written_by="pipeline",
        )
        payload = {
            "schemaVersion": 1,
            "status": "failed",
            "failed_gates": failures,
            "gate_exit_codes": gate_exit_codes,
            "verify_stamp": {
                "path": str(pipeline.ref_dir / "verify-stamp.json"),
                "created": False,
                "success_only": True,
            },
            "terminalState": state.terminal_state,
            "reports": {
                "json": str(json_path),
                "html": str(html_path),
            },
            "logs": gate_logs,
            "next_action": next_action,
            "read_for_llm": [str(json_path), str(pipeline.ref_dir / "pipeline-state.json")],
            "do_not_read": [
                str(pipeline.ref_dir / "raw"),
                str(pipeline.ref_dir / "raw" / "dom.json"),
                str(pipeline.ref_dir / "raw" / "computed.json"),
                str(pipeline.ref_dir / "raw" / "styles.json"),
            ],
        }
        if json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(
                f"\n{_RED}{_BOLD}verify: {len(failures)} gate(s) failed: "
                f"{', '.join(failures)}{_NC}"
            )
            print(f"  report: {json_path}")
            print(f"  html  : {html_path}")
            print(f"  terminalState: {pipeline.ref_dir / 'pipeline-state.json'}")
        return 1
    # All post-impl gates ran and passed (each cleared its own skip above). Any
    # gate STILL in .gate-skip-log was skipped fail-open earlier and never
    # re-enforced — refuse the success stamp until a capable host runs it (which
    # clears it) or the user records gateSkipAck. Checked AFTER the loop so the
    # capable host's own verify run is never false-blocked by its prior skip.
    gate_skip = _gate_skip_blocker(pipeline.ref_dir)
    if gate_skip is not None:
        if json_output:
            print(json.dumps({
                "schemaVersion": 1,
                "status": "failed",
                "reason": gate_skip,
                "next_action": "rerun_gates_on_capable_host",
                "verify_stamp": {
                    "path": str(pipeline.ref_dir / "verify-stamp.json"),
                    "created": False,
                    "success_only": True,
                },
            }, ensure_ascii=False, indent=2))
        else:
            print(f"{_RED}verify: {gate_skip}{_NC}")
        return 1
    motion_blocker = _unmeasurable_motion_blocker(pipeline.ref_dir)
    if motion_blocker is not None:
        if json_output:
            print(json.dumps({
                "schemaVersion": 1,
                "status": "failed",
                "reason": motion_blocker,
                "next_action": "resolve_unmeasurable_transitions",
                "verify_stamp": {
                    "path": str(pipeline.ref_dir / "verify-stamp.json"),
                    "created": False,
                    "success_only": True,
                },
            }, ensure_ascii=False, indent=2))
        else:
            print(f"{_RED}verify: {motion_blocker}{_NC}")
        return 1
    # Stop-hook stamp. On success, write a current-run-fresh marker that
    # the Stop hook checks before allowing the agent to claim completion.
    # Without this stamp, the Stop hook will block — closing the bypass
    # where an agent runs individual verification scripts directly and
    # never reaches the canonical entry.
    report = build_verify_report(
        pipeline.ref_dir,
        gates=gates_post_impl,
        impl_dir=impl_dir,
        gate_exit_codes=gate_exit_codes,
    )
    json_path, html_path = write_verify_report(pipeline.ref_dir, report)
    stamp = build_verify_stamp(pipeline.ref_dir, impl_dir, list(gates_post_impl))
    tmp_stamp_path = stamp_path.with_name(f".{stamp_path.name}.tmp")
    try:
        tmp_stamp_path.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
        tmp_stamp_path.replace(stamp_path)
    finally:
        try:
            tmp_stamp_path.unlink(missing_ok=True)
        except OSError:
            pass
    PipelineState.load(pipeline.ref_dir).clear_terminal(pipeline.ref_dir)
    if json_output:
        print(json.dumps({
            "schemaVersion": 1,
            "status": "passed",
            "gates_passed": list(gates_post_impl),
            "verify_stamp": {
                "path": str(stamp_path),
                "created": True,
                "success_only": True,
            },
            "reports": {
                "json": str(json_path),
                "html": str(html_path),
            },
            "logs": gate_logs,
            "next_action": None,
        }, ensure_ascii=False, indent=2))
    else:
        print(f"\n{_GREEN}{_BOLD}verify: all post-impl gates passed{_NC}")
        print(f"  stamp: {stamp_path}")
        print(f"  report: {json_path}")
        print(f"  html  : {html_path}")
    return 0
