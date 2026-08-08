"""
Stop hook — blocks Claude response based on current pipeline gate.

Reads pipeline-state.json to determine which gate to enforce.
If pipeline-state.json is absent, defaults to reference gate (fresh start).

Activation: only fires when a .ui-re-active marker exists in tmp/ref/*/.

Usage: python -m ui_clone.hooks.section_gate
Outputs {"decision": "block", "reason": "..."} to stdout to block, or exits 0 to allow.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import cast

from ui_clone.goal import build_goal_card
from ui_clone.hooks._common import deferred_checks_blocker as _deferred_checks_blocker
from ui_clone.hooks._common import find_project_root as _find_project_root
from ui_clone.hooks._common import gate_skip_blocker as _gate_skip_blocker
from ui_clone.hooks._common import has_clone_writes, has_external_browse
from ui_clone.hooks._common import load_json_safe as _load_json_safe
from ui_clone.hooks._common import quick_tier_blocker as _quick_tier_blocker
from ui_clone.hooks._common import run_gate as _run_gate
from ui_clone.hooks._common import session_id_from_payload as _session_id_from_payload
from ui_clone.hooks._common import should_enforce_ref_for_session as _should_enforce_ref_for_session
from ui_clone.state import (
    GATE_ORDER,
    POST_IMPL_VERIFY_GATES,
    TERMINAL_STATUSES,
    PipelineState,
)

_DEFAULT_STALE_DAYS = 3
_DEFAULT_ACTIVE_MAX = 2


def _is_driver_session(project_root: Path, session_id_from_payload: str = "") -> bool:
    """Driver-session bypass — release Stop unconditionally when the current
    Claude Code session is registered as a loop driver for this repo.

    Why this exists:
      A maintainer driver session in this repo monitors parallel
      benchmark / sub-workspace sessions (each in their own tab with
      their own session id). When a sub-workspace creates impl/ at
      `<repo>/scratch/<dir>/impl/`, the Stop hook's search_root walk
      finds that ref dir as "active" and fires on the driver's response.
      The driver itself never claims to clone anything, so verify-stamp
      enforcement is a category mismatch.

    Activation:
      1. Marker file <project_root>/.driver-session.id exists, AND
      2. Its content matches the active session id. We check, in order:
           (a) `session_id_from_payload` — the `session_id` field on the
               Claude Code hook JSON stdin payload (canonical),
           (b) `os.environ.get("CLAUDE_CODE_SESSION_ID")` — fallback for
               hosts that propagate session-id via env instead of stdin
               (e.g. direct CLI invocation in tests).

    Production users never create the marker, so the gate works as before.
    Child sessions spawned by the driver have their own session_id; even
    if they could read the marker, no match → gate fires for them as
    expected.

    The marker is local-only state (gitignored). Register via
    `bash scripts/register-driver-session.sh` or
    `python -m ui_clone.driver_session register <id>` — both use the
    append-if-missing writer in `ui_clone.driver_session` which holds
    `fcntl.flock` across the read-modify-write so concurrent driver
    sessions don't stomp each other. Manual `echo > .driver-session.id`
    is single-writer and was the source of the 2026-05-24 multi-driver
    stomping incident.
    """
    marker = project_root / ".driver-session.id"
    if not marker.is_file():
        return False
    try:
        recorded_ids = {
            line.strip()
            for line in marker.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    except OSError:
        return False
    if not recorded_ids:
        return False
    candidates = [
        session_id_from_payload.strip(),
        os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip(),
    ]
    return any(c in recorded_ids for c in candidates if c)


def _get_stale_seconds() -> float:
    """Return stale threshold in seconds. Overridable via UI_RE_STALE_DAYS env var."""
    try:
        days = float(os.environ.get("UI_RE_STALE_DAYS", _DEFAULT_STALE_DAYS))
    except (ValueError, TypeError):
        days = _DEFAULT_STALE_DAYS
    return days * 24 * 3600


def _get_active_max() -> int:
    """Return max active refs kept by the Stop hook. 0 disables LRU pruning."""
    try:
        value = int(os.environ.get("UI_RE_ACTIVE_MAX", _DEFAULT_ACTIVE_MAX))
    except (ValueError, TypeError):
        return _DEFAULT_ACTIVE_MAX
    return value if value >= 0 else _DEFAULT_ACTIVE_MAX


def _is_cross_scratch_impl(ref_dir: Path, impl_dir: Path) -> bool:
    """Return True when `impl_dir` points at another scratch run.

    `implRoot` / `.impl-root` are explicit wires, but they are local state and
    can survive from interrupted sessions. A stale wire like
    `tmp/ref/project-a-main -> scratch/project-a-sustainability-04` makes the
    Stop hook demand verify evidence for the wrong clone. Keep arbitrary
    non-scratch impl locations valid, and keep env overrides above this guard;
    only reject repo-local `scratch/<slot>` paths whose slot is not correlated
    with this ref name.
    """
    if not (ref_dir.parent.name == "ref" and ref_dir.parent.parent.name == "tmp"):
        return False
    project_root = ref_dir.parent.parent.parent
    scratch_root = project_root / "scratch"
    try:
        rel = impl_dir.resolve().relative_to(scratch_root.resolve())
    except (OSError, ValueError):
        return False
    if not rel.parts:
        return False
    slot = rel.parts[0]
    ref_name = ref_dir.name
    return not (
        slot == ref_name
        or slot.startswith(f"{ref_name}-")
        or slot.startswith(f"{ref_name}_")
        or slot.startswith(f"{ref_name}.")
    )


def _resolve_impl_dir(
    ref_dir: Path,
    fallback_root: Path | None = None,
    *,
    allow_legacy_project_fallback: bool = True,
) -> Path | None:
    """Resolve the impl/ directory for `ref_dir`, preferring the per-ref-dir
    `impl_root` recorded in pipeline-state.json.

    False-positive cascade closure:
    `ref_dir.parent.parent.parent / "impl"` and `project_root / "impl"` both
    assume one canonical impl/ per project. When a stray `<repo>/impl`
    symlink lives at the repo root (rogue subagent state, a hand-symlink, a
    leftover convention), every prior `tmp/ref/<c>/` false-positives as
    "active" and the verify-stamp gate fires against unrelated months-old
    clones. Now that `PipelineState.impl_root` and the `.impl-root` marker
    are written at Phase 1 start, the resolver can be precise per ref dir.

    Priority order:
    1. UI_CLONE_IMPL_ROOT env (operator override / sub-workspace testing)
    2. `pipeline-state.json` → `impl_root` (set by Phase 1 of the pipeline)
    3. `<ref_dir>/.impl-root` marker file (set alongside the state field)
    4. When `allow_legacy_project_fallback` is true, fall back to the convention
       path (`<fallback_root or repo>/impl`) for refs that predate impl_root.

    Markerless implicit scans disable step 4 unless pipeline-state.json or a
    captured structure.json proves the directory is a real clone run. This
    keeps ad-hoc section-map probes from borrowing an unrelated root impl/.

    Returns None when no resolution exists at all, so callers can branch
    on "no impl is wired up to this ref" instead of grabbing a rogue symlink.
    """
    env_root = os.environ.get("UI_CLONE_IMPL_ROOT", "").strip()
    if env_root:
        p = Path(env_root)
        if p.is_dir():
            return p

    try:
        state = PipelineState.load(ref_dir)
        if state.impl_root:
            p = Path(state.impl_root)
            if p.is_dir() and not _is_cross_scratch_impl(ref_dir, p):
                return p
    except OSError:
        pass

    marker = ref_dir / ".impl-root"
    if marker.is_file():
        try:
            marker_value = marker.read_text(encoding="utf-8").strip()
            if marker_value:
                p = Path(marker_value)
                if p.is_dir() and not _is_cross_scratch_impl(ref_dir, p):
                    return p
        except OSError:
            pass

    candidates: list[Path] = []
    # Planned agent-first layout: .ui-clone/runs/<id>/impl.
    candidates.append(ref_dir / "impl")
    if allow_legacy_project_fallback:
        if fallback_root is not None:
            candidates.append(fallback_root / "impl")
        # ref_dir.parent.parent.parent matches the legacy derivation
        # (tmp/ref/<c>/.. = tmp/ref/.. = tmp/.. = <project-root>).
        try:
            candidates.append(ref_dir.parent.parent.parent / "impl")
        except (IndexError, OSError):
            pass
    # Sub-workspace fallback: an agent may write `<repo>/scratch/<name>/impl/`
    # and `<repo>/tmp/ref/<name>/` as siblings but never set `.impl-root`
    # or `pipeline-state.impl_root`. The legacy `<repo>/impl` fallback
    # doesn't exist in that layout, so the resolver returns None and the
    # Stop hook's verify-stamp gate silently skips this ref dir. The
    # agent then declares success on build/render alone.
    # Heuristic: when ref dir lives at `<repo>/tmp/ref/<name>/`, also
    # try `<repo>/scratch/<name>/impl/`. This recovers the linkage
    # without trusting the agent to write the marker.
    try:
        repo = ref_dir.parent.parent.parent  # tmp/ref/<c>/ → <repo>
        candidates.append(repo / "scratch" / ref_dir.name / "impl")
    except (IndexError, OSError):
        pass
    for cand in candidates:
        if cand.is_dir() and not _is_cross_scratch_impl(ref_dir, cand):
            return cand
    return None


_RESULT_SUMMARY_RE = re.compile(
    r"\*\*Result:\s*(\d+)\s+PASS,\s*(\d+)\s+FAIL,\s*(\d+)\s+SKIP,\s*(\d+)\s+STRUCTURAL_ONLY"
    r"(?:,\s*(\d+)\s+UNMEASURED)?",
    re.IGNORECASE,
)
# A ❌ status in a TABLE ROW (line starting with `|`) — the unambiguous per-row
# fail glyph section-compare emits. Anchored to a row so a free-floating "FAIL" /
# "MISSING" token planted in prose cannot fake a non-success (the decoy-token
# evasion), and using ❌ only avoids a false non-success on a section NAMED "fail".
_HONEST_FAILURE_RE = re.compile(r"^\s*\|.*❌", re.MULTILINE)
# Per-row unmeasured admission, for artifacts written before UNMEASURED became a
# field on the canonical Result line. Anchored on the SEVERITY cell rather than
# anywhere-in-the-row: a loose match would let a section merely *named*
# "unmeasured-hero" flip any result.txt into releasable.
_UNMEASURED_ROW_RE = re.compile(
    r"^\s*\|[^|]*\|[^|]*\|[^|]*\|\s*unmeasured\s*\|", re.MULTILINE | re.IGNORECASE
)


def _result_txt_claims_success(ref_dir: Path) -> bool:
    """True when sections/result.txt does NOT provably show failure — i.e. a
    reader (human, benchmark harness) would not see honest FAILs.

    Security boundary: this gates whether a SELF-ATTESTED terminal end may release
    the Stop. terminalState is the canonical NON-success end, so a release is only
    legitimate when result.txt honestly shows the run did not converge. We
    therefore FAIL TOWARD BLOCKING — anything that is not provably a non-success is
    treated as a success claim. A self-attested terminal closes out with ZERO gates
    run, so it must not be usable to certify a success (forged or real); that path
    is the canonical verify, which runs the gates and writes a real verify-stamp.

    Provably NON-success (returns False, the pin-bound release is allowed):
      - an UNMEASURED admission (canonical `**Unmeasured: N ...**` line or a
        `⚠️ UNMEASURED` table row): the run is stating it has no pixel evidence
        for a section, which is an honest non-convergence, OR
      - a canonical `**Result: N PASS, M FAIL, ...**` line with M>0, OR with 0 PASS
        (case-insensitive — a lowercase clone of the line is not a loophole), OR
      - an honest per-row failure marker (❌ / a `| FAIL` row / MISSING).
    Everything else with a result.txt present (an all-✅ table, a prose/emoji
    "success"/"converged" claim, a simplified `**Result: 1 PASS**`, an empty or
    garbled body) is a success claim → BLOCK. An ABSENT result.txt is not a claim
    (nothing on disk to read) → False, preserving the back-compat early-run release.

    Admitting UNMEASURED as non-success is not a success loophole: this predicate
    only unlocks a terminalState release, and terminalState is by definition a
    non-success end pinned to result.txt's sha256. It cannot certify a pass.
    """
    result_txt = ref_dir / "sections" / "result.txt"
    if not result_txt.is_file():
        return False
    try:
        text = result_txt.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    # An unmeasured section is absence of evidence, which the producer itself
    # labels "capture failure, not impl evidence". A table carrying one is not
    # claiming success no matter what the PASS/FAIL arithmetic says — that
    # arithmetic was the blind spot the producer laundered UNMEASURED through.
    if _UNMEASURED_ROW_RE.search(text):
        return False
    matches = list(_RESULT_SUMMARY_RE.finditer(text))
    if matches:
        # The LAST `**Result:` line — matching parse_section_result (loop-overwrite)
        # and the convergence definition. Reading the FIRST would let a decoy
        # `**Result: 0 PASS, 3 FAIL**` line above a real all-PASS line release while
        # the harvester/human reads the success — a detector/parser disagreement.
        m = matches[-1]
        passes, fails = int(m.group(1)), int(m.group(2))
        unmeasured = int(m.group(5) or 0)
        # Provable non-success iff it asserts real failures, nothing passed, or a
        # section was never measured (absence of evidence is not a success claim).
        return not (fails > 0 or passes == 0 or unmeasured > 0)
    # No canonical summary line: trust only an honest table-row ❌ as non-success;
    # otherwise treat as a success claim (fail toward enforcement).
    return not _HONEST_FAILURE_RE.search(text)


def _live_gate_has_failure(ref_dir: Path, gate_name: str) -> bool:
    """Re-run one declared canonical gate and fail closed on evaluation errors."""
    try:
        from ui_clone.gate import Gate

        results = Gate(ref_dir)._dispatch(gate_name)
    except Exception:
        return False
    return any(result.status == "fail" for result in results)


def _canonical_nonsection_failure_is_live(
    ref_dir: Path, terminal: dict[str, object]
) -> bool:
    """Confirm that canonical verify genuinely failed outside section-compare.

    `sections/result.txt` reports only section-compare. It may therefore be all
    PASS while canonical verify correctly fails at post-implement, boundary,
    font-parity, or spec. The Stop hook may accept that honest non-success only
    when terminal state and verify-report.json agree and the declared root gate
    still fails when evaluated now. Reports alone are writable evidence and do
    not release the hook.
    """
    if (
        str(terminal.get("status") or "").lower() != "failed"
        or terminal.get("category") != "canonical-verify-failed"
        or terminal.get("writtenBy") != "pipeline"
    ):
        return False

    gate_name = terminal.get("gate")
    if (
        not isinstance(gate_name, str)
        or gate_name not in POST_IMPL_VERIFY_GATES
        or gate_name == "section-compare"
    ):
        return False

    detail = terminal.get("detail")
    if not isinstance(detail, dict):
        return False
    failed_gates = detail.get("failed_gates")
    root_gates = detail.get("root_cause_gates")
    exit_codes = detail.get("gate_exit_codes")
    if (
        not isinstance(failed_gates, list)
        or not all(isinstance(item, str) for item in failed_gates)
        or not isinstance(root_gates, list)
        or not all(isinstance(item, str) for item in root_gates)
        or not isinstance(exit_codes, dict)
        or gate_name not in failed_gates
        or gate_name not in root_gates
        or "section-compare" in failed_gates
    ):
        return False
    gate_exit_code = exit_codes.get(gate_name)
    if not isinstance(gate_exit_code, int) or gate_exit_code == 0:
        return False

    report_path = ref_dir / "verify-report.json"
    declared_report = detail.get("verify_report")
    if not isinstance(declared_report, str) or not declared_report:
        return False
    declared_path = Path(declared_report)
    if not declared_path.is_absolute():
        declared_path = ref_dir / declared_path
    try:
        if declared_path.resolve() != report_path.resolve():
            return False
    except OSError:
        return False

    report = _load_json_safe(report_path)
    if report is None or report.get("schemaVersion") != 1 or report.get("verdict") != "fail":
        return False
    report_failures = report.get("failures")
    if (
        not isinstance(report_failures, list)
        or not all(isinstance(item, str) for item in report_failures)
        or report_failures != failed_gates
        or "section-compare" in report_failures
    ):
        return False

    raw_gates = report.get("gates")
    if not isinstance(raw_gates, list):
        return False
    gate_report = next(
        (
            row
            for row in raw_gates
            if isinstance(row, dict) and row.get("gate") == gate_name
        ),
        None,
    )
    if not isinstance(gate_report, dict):
        return False
    checks = gate_report.get("checks")
    if (
        gate_report.get("passed") is not False
        or not isinstance(gate_report.get("fail_count"), int)
        or gate_report["fail_count"] < 1
        or gate_report.get("exit_code") != gate_exit_code
        or not isinstance(checks, list)
        or not any(
            isinstance(check, dict) and check.get("status") == "fail"
            for check in checks
        )
    ):
        return False

    return _live_gate_has_failure(ref_dir, gate_name)


def _terminal_state_block_reason(ref_dir: Path, state: PipelineState) -> str | None:
    """Return a Stop block if explicit terminal state is stale.

    Terminal failed/incomplete/unclonable state is the canonical way to end an
    evidence run without forging verify-stamp.json. It should release Stop only
    for that exact impl snapshot. If the agent edits impl after the terminal
    state was recorded, the run is active again and must verify or terminalize
    anew.
    """
    terminal = state.terminal_state
    if not terminal:
        return None
    status = str(terminal.get("status") or "").lower()
    if status not in TERMINAL_STATUSES:
        return (
            f"⛔ UI-RE terminal-state gate: malformed terminalState for {ref_dir}\n\n"
            f"terminalState.status={terminal.get('status')!r} is not one of "
            f"{'/'.join(TERMINAL_STATUSES)}. Fix pipeline-state.json "
            f"or rerun canonical verify.\n"
        )

    # Provenance bind (item 5): a SELF-ATTESTED terminal write — writtenBy != "pipeline"
    # (the `state terminal` CLI / agent escape, or a legacy untagged record) — must pin
    # the exact evidence snapshot. Gate-bound internal writes (verify.execute_verify
    # failed, record_unclonable content-blocker) set writtenBy="pipeline" and are exempt.
    # When a sections/result.txt exists, require a matching sectionsResultSha256 so the
    # non-success Stop release cannot be self-declared against a stale/forged result.txt.
    written_by = str(terminal.get("writtenBy") or "cli")
    result_txt = ref_dir / "sections" / "result.txt"

    # N1: a terminal state normally cannot release Stop while a SUCCESS-shaped
    # result.txt is on disk. terminalState is a NON-success end, so an all-PASS
    # result paired with a self-attested terminal is the forge. The one narrow
    # exception is a pipeline-written canonical verify failure outside
    # section-compare whose report is coherent and whose root gate still fails
    # when this hook re-runs it. That path ran gates and is honestly incomplete;
    # it does not certify success or create a verify-stamp.
    if _result_txt_claims_success(ref_dir) and not _canonical_nonsection_failure_is_live(
        ref_dir, cast(dict[str, object], terminal)
    ):
        return (
            f"⛔ UI-RE terminal-state gate: success-claiming result.txt cannot "
            f"self-attest a terminal release for {ref_dir}\n\n"
            f"terminalState is {status!r} (a non-success end) but sections/result.txt "
            f"does not honestly show failure. A self-attested terminal closes out with "
            f"ZERO gates run, so it cannot certify a SUCCESS. Close out canonically so "
            f"the gates actually run and a verify-stamp is written:\n"
            f"  python -m ui_clone.pipeline <url> <component> <session> verify --json\n\n"
            f"Do not invent section FAIL rows to make an all-PASS section result look "
            f"failed. If canonical verify failed at a different gate, rerun verify so "
            f"its report and terminal state identify that gate; the Stop hook releases "
            f"only while that non-section failure still reproduces.\n"
        )

    if written_by != "pipeline" and result_txt.is_file():
        import hashlib

        try:
            actual = hashlib.sha256(result_txt.read_bytes()).hexdigest()
        except OSError:
            actual = None
        pinned = terminal.get("sectionsResultSha256")
        if not pinned or actual is None or pinned != actual:
            return (
                f"⛔ UI-RE terminal-state gate: unverified self-attested terminal "
                f"state for {ref_dir}\n\n"
                f"terminalState is {status!r} ({terminal.get('category', 'unknown')}) "
                f"written by {written_by!r} but it does not pin the current "
                f"sections/result.txt (sectionsResultSha256 "
                f"{'absent' if not pinned else 'mismatched'}). A self-attested end "
                f"cannot be trusted against an unbound or edited result.txt.\n\n"
                f"Resolve either way:\n"
                f"  - if the run actually converged, close out canonically:\n"
                f"      python -m ui_clone.pipeline <url> <component> <session> verify --json\n"
                f"  - if it is genuinely incomplete/abandoned, re-record AFTER your "
                f"final edit so the evidence pin recomputes:\n"
                f"      python -m ui_clone.state terminal {ref_dir} --status {status} "
                f"--category <c> --reason <r>\n"
            )

    impl_dir = _resolve_impl_dir(ref_dir)
    state_path = ref_dir / "pipeline-state.json"
    if impl_dir is not None and impl_dir.is_dir() and state_path.is_file():
        changed = _newer_impl_files(impl_dir, state_path)
        if changed:
            sample = "\n".join(f"  - {p}" for p in changed)
            return (
                f"⛔ UI-RE terminal-state gate: impl changed after terminal state for {ref_dir}\n\n"
                f"terminalState is {status!r} ({terminal.get('category', 'unknown')}) "
                f"but these implementation files are newer than pipeline-state.json:\n"
                f"{sample}\n\n"
                f"Rerun canonical verify or record a fresh explicit terminal state:\n"
                f"  python -m ui_clone.pipeline <url> <component> <session> verify --json\n"
            )
    return None


def _find_active_markers(search_root: Path) -> list[Path]:
    """Return ref dirs that should engage the Stop hook.

    Two activation paths:
    1. Explicit `.ui-re-active` marker — written by pre_generate.py on the
       first passing pre-generate gate. This is the canonical "I am in a
       ui-re flow" signal.
    2. A markerless ref with explicit/correlated impl linkage, or a legacy
       repository-root impl plus pipeline-state.json/structure.json. This keeps
       skipped Phase 5/6 clone runs fail-closed without treating capture-only
       section-map probes as completed implementations.
    """
    if not search_root.is_dir():
        return []
    dirs: list[Path] = []
    # project_root is the parent of tmp/, which is the parent of search_root.
    project_root = search_root.parent.parent
    for d in sorted(search_root.iterdir()):
        if not d.is_dir():
            continue
        if (d / ".ui-re-active").is_file():
            dirs.append(d)
            continue
        # Implicit activation: impl/ exists but the canonical marker is missing.
        # Resolve impl per ref dir (impl_root / .impl-root marker / env),
        # not from a single repo-root convention.
        # A rogue <project_root>/impl symlink must not activate capture/probe
        # dirs. Explicit/correlated impl roots remain eligible, while the root
        # convention fallback is limited to pipeline state or captured DOM
        # structure, either of which distinguishes a clone from a map probe.
        impl_dir = _resolve_impl_dir(
            d,
            fallback_root=project_root,
            allow_legacy_project_fallback=(d / "pipeline-state.json").is_file()
            or (d / "structure.json").is_file(),
        )
        if impl_dir is not None and any(d.iterdir()):
            # Skip empty ref dirs to avoid false positives on totally cold
            # fresh starts.
            dirs.append(d)
    return dirs


# Hook-managed bookkeeping under a ref dir. These are written/touched by the
# Stop hook and pre_generate on every scan, so they must NOT count as "activity"
# when aging out a markerless orphan — otherwise the hook re-freshens the very
# ref it is deciding the staleness of.
_HOOK_BOOKKEEPING_NAMES = frozenset({".ui-re-sessions", ".ui-re-active"})


def _pipeline_state_epoch(ref_dir: Path) -> float | None:
    """Authoritative activity timestamp: pipeline-state.json's last_updated,
    parsed to epoch seconds. Returns None when absent/unparseable.

    Filesystem mtime alone is unreliable for staleness: any read, scan, or the
    Stop hook itself touching a ref dir bumps its mtime, so an abandoned orphan
    (last advanced days ago) can masquerade as fresh and out-rank the genuinely
    active ref. pipeline-state.last_updated only moves when the pipeline does
    real work, so it is the correct activity signal.
    """
    try:
        state = PipelineState.load(ref_dir)
    except (OSError, ValueError):
        return None
    raw = (state.last_updated or "").strip()
    if not raw:
        return None
    import datetime

    iso = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt.timestamp()


def _active_ref_mtime(ref_dir: Path) -> float | None:
    """Best-effort activity timestamp for implicit active refs without markers.

    Prefers pipeline-state.last_updated (authoritative) and only falls back to
    filesystem mtime when no pipeline-state timestamp exists — so a freshly
    touched but abandoned orphan is still correctly classified stale.
    """
    state_epoch = _pipeline_state_epoch(ref_dir)
    if state_epoch is not None:
        return state_epoch
    # Fallback (no pipeline-state): use the newest REAL artifact mtime, and
    # EXCLUDE the Stop hook's own bookkeeping — the `.ui-re-sessions/` session
    # crumbs and the `.ui-re-active` marker, which the hook writes on every scan
    # and which would otherwise perpetually re-freshen an abandoned orphan so it
    # never ages out (the "fires in unrelated work" recurrence). The ref-dir's
    # own mtime is also excluded: it bumps whenever a crumb is added under it.
    mtimes: list[float] = []
    try:
        children = list(ref_dir.iterdir())
    except OSError:
        return None
    for child in children:
        if child.name in _HOOK_BOOKKEEPING_NAMES:
            continue
        try:
            mtimes.append(child.stat().st_mtime)
        except OSError:
            continue
    return max(mtimes) if mtimes else None


def _fresh_active_dirs(active_dirs: list[Path]) -> list[Path]:
    now = time.time()
    fresh_dirs: list[tuple[Path, float, bool]] = []
    for ref_dir in active_dirs:
        marker = ref_dir / ".ui-re-active"
        if not marker.is_file():
            activity_mtime = _active_ref_mtime(ref_dir)
            if activity_mtime is None:
                continue
            age = now - activity_mtime
            if age >= _get_stale_seconds():
                age_days = int(age // 86400)
                print(
                    f"ui-clone-skills: Stale implicit WIP ref ({age_days}d) at {ref_dir} — skipping.",
                    file=sys.stderr,
                )
                continue
            fresh_dirs.append((ref_dir, activity_mtime, False))
            continue
        try:
            marker_mtime = marker.stat().st_mtime
        except OSError:
            continue
        age = now - marker_mtime
        if age >= _get_stale_seconds():
            age_days = int(age // 86400)
            print(
                f"ui-clone-skills: Stale WIP marker ({age_days}d) at {marker} — removing.",
                file=sys.stderr,
            )
            try:
                marker.unlink()
            except OSError:
                pass
            continue
        fresh_dirs.append((ref_dir, marker_mtime, True))

    active_max = _get_active_max()
    if active_max > 0 and len(fresh_dirs) > active_max:
        newest = sorted(fresh_dirs, key=lambda item: (item[1], str(item[0])), reverse=True)[
            :active_max
        ]
        keep = {ref_dir for ref_dir, _, _ in newest}
        for ref_dir, _, has_marker in fresh_dirs:
            if ref_dir in keep:
                continue
            marker = ref_dir / ".ui-re-active"
            if has_marker:
                print(
                    f"ui-clone-skills: LRU-pruned WIP marker at {marker} — removing.",
                    file=sys.stderr,
                )
                try:
                    marker.unlink()
                except OSError:
                    pass
            else:
                print(
                    f"ui-clone-skills: LRU-pruned implicit WIP ref at {ref_dir} — skipping.",
                    file=sys.stderr,
                )
    else:
        keep = {ref_dir for ref_dir, _, _ in fresh_dirs}

    return [ref_dir for ref_dir, _, _ in fresh_dirs if ref_dir in keep]


def _emit_block(reason: str) -> None:
    # Headless driver (benchmark_harness): a Stop block ends the turn with no
    # printed answer, so the iteration is spent and the reason only lands on
    # the next one. The driver re-runs the same Python gates between
    # iterations, so the block adds no enforcement it does not already have —
    # demote to an advisory that still reaches the driver log via stderr.
    if os.environ.get("UI_RE_HEADLESS_DRIVER") == "1":
        print(reason, file=sys.stderr)
        return
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


def _block_reason_for_gate(gate_name: str, ref_dir: Path, gate_result: dict[str, object]) -> str:
    failures: list[dict[str, str]] = cast(list[dict[str, str]], gate_result.get("failures", []))
    fail_count = cast(int, gate_result.get("fail_count", len(failures)))
    missing_list = "\n  - ".join(f["label"] for f in failures[:10])
    return (
        f"⛔ UI-RE Gate: {gate_name} BLOCKED\n\n"
        f"Incomplete items ({fail_count}):\n  - {missing_list}\n\n"
        f"Run:\n"
        f"  python -m ui_clone.gate {ref_dir} {gate_name}\n"
        f"  → After passing, run python -m ui_clone.goal {ref_dir} for the next bounded goal\n\n"
        f"{build_goal_card(ref_dir)}"
    )


def _section_compare_block_reason(ref_dir: Path, gate_result: dict[str, object]) -> str:
    failures: list[dict[str, str]] = cast(list[dict[str, str]], gate_result.get("failures", []))
    fail_count = cast(int, gate_result.get("fail_count", len(failures)))
    parts = [f"⛔ UI-RE Gate: section-compare FAILED for {ref_dir} ({fail_count} issue(s))."]
    for f in failures[:5]:
        parts.append(f"  • {f['label']}: {f['reason']}")
        if f.get("fix"):
            parts.append(f"    → {f['fix']}")
    parts.append("\nAll sections must PASS before finishing.")
    parts.append(f"\nRun: python -m ui_clone.goal {ref_dir}")
    parts.append(build_goal_card(ref_dir))
    return "\n".join(parts)


def _unknown_gate_block_reason(current_gate: str, ref_dir: Path) -> str:
    valid_gates = ", ".join([*GATE_ORDER, "done"])
    return (
        f"⛔ UI-RE Gate: unknown current_gate BLOCKED for {ref_dir}\n\n"
        f"pipeline-state.json has unknown current_gate {current_gate!r}.\n"
        f"Valid current_gate values: {valid_gates}.\n\n"
        f"Run:\n"
        f"  python -m ui_clone.goal {ref_dir}\n\n"
        f"{build_goal_card(ref_dir)}"
    )


_VERIFY_STAMP_MAX_AGE_S = 1800  # 30 min — generous so the agent has time to
# finish the response after running verify, but short enough that stale
# stamps from a previous run don't satisfy the gate.
_VERIFY_STAMP_WATCH_EXTS = {
    ".css",
    ".gif",
    ".html",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".jsx",
    ".mp4",
    ".png",
    ".scss",
    ".svg",
    ".ts",
    ".tsx",
    ".webm",
    ".webp",
}
_VERIFY_STAMP_SKIP_DIRS = {
    ".git",
    ".next",
    "build",
    "dist",
    "node_modules",
}


def _newer_impl_files(impl_dir: Path, stamp_path: Path, limit: int = 5) -> list[Path]:
    """Return impl files modified after verify-stamp.json.

    A fresh timestamp alone is not enough: agents can run `pipeline ... verify`,
    then patch JSX/CSS/assets and stop within the 30-minute stamp window. Scan
    the implementation surface and force a new verify when source changed.
    """
    try:
        stamp_mtime = stamp_path.stat().st_mtime
    except OSError:
        return []

    changed: list[Path] = []
    for path in impl_dir.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _VERIFY_STAMP_SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in _VERIFY_STAMP_WATCH_EXTS:
            continue
        try:
            if path.stat().st_mtime > stamp_mtime + 0.001:
                changed.append(path)
        except OSError:
            continue
        if len(changed) >= limit:
            break
    return changed


def _enforce_verify_stamp(ref_dir: Path) -> str | None:
    """Block Stop unless pipeline.execute_verify wrote a fresh stamp.

    Prior audit finding: the SKILL.md mandate to run
    `pipeline ... verify` was bypassed because the agent invoked
    individual verification scripts directly. This check closes the
    bypass — Stop blocks unless `verify-stamp.json` exists AND is
    newer than _VERIFY_STAMP_MAX_AGE_S.

    Only fires when impl/ exists (post-generation). Pre-generation
    loops are governed by the regular current_gate enforcement.
    """
    # impl/ is resolved via the per-ref-dir impl_root field. The legacy
    # ref_dir.parent.parent.parent / "impl" walk is kept as a fallback
    # inside _resolve_impl_dir for ref dirs that predate the field, but
    # the prior single-impl-per-project assumption no longer false-
    # positives every ref dir when a rogue <repo>/impl symlink exists.
    impl_dir = _resolve_impl_dir(ref_dir)
    if impl_dir is None or not impl_dir.is_dir():
        return None  # pre-generation — no stamp required yet

    # H1: the canonical verify closeout must consult the skip-ledger too, not only
    # the structural path. A fresh, valid verify-stamp.json after an un-recovered
    # fail-open gate skip would otherwise release green here (this is the DEFAULT
    # closeout in _enforce_ref_dir's else branch).
    gate_skip_block = _gate_skip_blocker(ref_dir)
    if gate_skip_block:
        return (
            f"⛔ UI-RE Verify-stamp gate: un-enforced gates for {ref_dir}\n\n"
            f"{gate_skip_block}\n"
        )

    stamp_path = ref_dir / "verify-stamp.json"
    if not stamp_path.is_file():
        return (
            f"⛔ UI-RE Verify-stamp gate: BLOCKED for {ref_dir}\n\n"
            f"impl/ exists at {impl_dir} but no verify-stamp.json. The Stop hook\n"
            f"requires `python -m ui_clone.pipeline ... verify` to have run and\n"
            f"passed before the response can end.\n\n"
            f"Build success, HTTP 200/title checks, local render, and visual spot checks\n"
            f"are not completion evidence. Missing artifacts are hard failures, not\n"
            f"substitutes for canonical verification.\n\n"
            f"Fix:\n"
            f"  python -m ui_clone.pipeline <url> <component> <session> verify\n\n"
            f"Verify drives the closeout gate suite\n"
            f" ({', '.join(POST_IMPL_VERIFY_GATES)}) and stamps {stamp_path.name}\n"
            f"on success.\n"
        )

    try:
        stamp = json.loads(stamp_path.read_text())
        import datetime
        stamped_at = datetime.datetime.strptime(
            stamp["verifiedAt"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=datetime.UTC)
        age_s = (datetime.datetime.now(datetime.UTC) - stamped_at).total_seconds()
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        return (
            f"⛔ UI-RE Verify-stamp gate: malformed stamp {stamp_path}\n\n"
            f"{exc}\n\n"
            f"Re-run `python -m ui_clone.pipeline ... verify` to regenerate.\n"
        )
    required_gates = set(POST_IMPL_VERIFY_GATES)
    stamped_by = stamp.get("stampedBy")
    gates_passed = stamp.get("gatesPassed")
    missing_gates: list[str] = []
    if isinstance(gates_passed, list):
        passed_set = {str(g) for g in gates_passed}
        missing_gates = sorted(required_gates - passed_set)
    else:
        missing_gates = sorted(required_gates)
    if stamped_by != "pipeline.execute_verify" or missing_gates:
        missing = ", ".join(missing_gates) if missing_gates else "none"
        return (
            f"⛔ UI-RE Verify-stamp gate: non-canonical stamp for {ref_dir}\n\n"
            f"verify-stamp.json must be written by pipeline.execute_verify after the\n"
            f"canonical post-implementation gate suite passes. Current stampedBy={stamped_by!r};\n"
            f"missing required gate evidence: {missing}.\n\n"
            f"Build success, HTTP 200/title checks, local render, and visual spot checks\n"
            f"are not completion evidence.\n\n"
            f"Re-run:\n"
            f"  python -m ui_clone.pipeline <url> <component> <session> verify\n"
        )
    if age_s > _VERIFY_STAMP_MAX_AGE_S:
        return (
            f"⛔ UI-RE Verify-stamp gate: STALE stamp for {ref_dir}\n\n"
            f"verify-stamp.json is {int(age_s)}s old (max {_VERIFY_STAMP_MAX_AGE_S}s).\n"
            f"impl/ was likely modified after the last verify. Re-run:\n\n"
            f"  python -m ui_clone.pipeline <url> <component> <session> verify\n"
        )

    changed = _newer_impl_files(impl_dir, stamp_path)
    if changed:
        sample = "\n".join(f"  - {p}" for p in changed)
        return (
            f"⛔ UI-RE Verify-stamp gate: impl changed after verify for {ref_dir}\n\n"
            f"These implementation files are newer than verify-stamp.json:\n"
            f"{sample}\n\n"
            f"Re-run the canonical closeout after the last code/asset edit:\n\n"
            f"  python -m ui_clone.pipeline <url> <component> <session> verify\n"
        )

    # Hash pin (parity with the structural-convergence stamp): the canonical
    # stamp records sections/result.txt's sha256 at verify time — stamping
    # while green then editing result.txt to claim more PASS rows must block.
    result_file = ref_dir / "sections" / "result.txt"
    stamped_sha = stamp.get("sectionsResultSha256")
    if stamped_sha and result_file.is_file():
        import hashlib

        current_sha = hashlib.sha256(result_file.read_bytes()).hexdigest()
        if current_sha != stamped_sha:
            return (
                f"⛔ UI-RE Verify-stamp gate: result.txt tampered after verify for {ref_dir}\n\n"
                f"sections/result.txt sha256 mismatch: stamp recorded {stamped_sha[:12]}…\n"
                f"but the file now hashes to {current_sha[:12]}…\n\n"
                f"Re-run the canonical closeout:\n\n"
                f"  python -m ui_clone.pipeline <url> <component> <session> verify\n"
            )
    from ui_clone.pipeline_phases.verify import verify_stamp_evidence_problem

    evidence_problem = verify_stamp_evidence_problem(ref_dir, stamp)
    if evidence_problem is not None:
        return (
            f"⛔ UI-RE Verify-stamp gate: motion evidence changed for {ref_dir}\n\n"
            f"{evidence_problem}.\n\n"
            f"Re-run the canonical closeout:\n\n"
            f"  python -m ui_clone.pipeline <url> <component> <session> verify\n"
        )
    return None


def _enforce_structural_convergence_stamp(ref_dir: Path) -> str | None:
    """Block Stop unless both structural and canonical evidence are fresh.

    Plans that opted into the structural closeout policy must prove staged
    convergence with structural-convergence-stamp.json from scripts/verify/
    check-converged.sh. That evidence supplements rather than replaces the
    canonical verify-stamp.json written by pipeline.execute_verify.

    Structural errors take precedence: validate its canonical writer, age,
    result hash, and impl freshness before enforcing canonical verification.
    """
    impl_dir = _resolve_impl_dir(ref_dir)
    if impl_dir is None or not impl_dir.is_dir():
        return None  # pre-generation — no stamp required yet

    # Mirror of the canonical path's quick-tier blocker: a quick-tier plan
    # must not satisfy Stop via the structural stamp either.
    tier_block = _quick_tier_blocker(ref_dir)
    if tier_block:
        return (
            f"⛔ UI-RE Structural-stamp gate: quick-tier plan for {ref_dir}\n\n"
            f"{tier_block}\n"
        )
    deferred_block = _deferred_checks_blocker(ref_dir)
    if deferred_block:
        return (
            f"⛔ UI-RE Structural-stamp gate: deferred checks for {ref_dir}\n\n"
            f"{deferred_block}\n"
        )
    gate_skip_block = _gate_skip_blocker(ref_dir)
    if gate_skip_block:
        return (
            f"⛔ UI-RE Structural-stamp gate: un-enforced gates for {ref_dir}\n\n"
            f"{gate_skip_block}\n"
        )

    stamp_path = ref_dir / "structural-convergence-stamp.json"
    if not stamp_path.is_file():
        return (
            f"⛔ UI-RE Structural-stamp gate: BLOCKED for {ref_dir}\n\n"
            f"closeoutPolicy=structural but no structural-convergence-stamp.json.\n"
            f"This run satisfies Stop via the convergence detector's stamp; it\n"
            f"is written on 0-FAIL by:\n\n"
            f"  bash scripts/verify/check-converged.sh {ref_dir} --write-stamp [--stage <A|B|C|D>]\n\n"
            f"Convergence definition: sections/result.txt's last `**Result: ...**` line\n"
            f"shows 0 FAIL (STRUCTURAL_ONLY counted as PASS, SKIP doesn't gate).\n"
        )

    try:
        stamp = json.loads(stamp_path.read_text())
        import datetime
        stamped_at = datetime.datetime.strptime(
            stamp["verifiedAt"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=datetime.UTC)
        age_s = (datetime.datetime.now(datetime.UTC) - stamped_at).total_seconds()
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        return (
            f"⛔ UI-RE Structural-stamp gate: malformed stamp {stamp_path}\n\n"
            f"{exc}\n\n"
            f"Re-run `bash scripts/verify/check-converged.sh {ref_dir} --write-stamp`.\n"
        )

    stamped_by = stamp.get("stampedBy")
    closeout_kind = stamp.get("closeoutKind")
    if stamped_by != "scripts/verify/check-converged.sh" or closeout_kind != "structural":
        return (
            f"⛔ UI-RE Structural-stamp gate: non-canonical stamp for {ref_dir}\n\n"
            f"structural-convergence-stamp.json must be written by\n"
            f"scripts/verify/check-converged.sh with closeoutKind=structural.\n"
            f"Current stampedBy={stamped_by!r}, closeoutKind={closeout_kind!r}.\n\n"
            f"Build success, HTTP 200/title checks, and hand-written stamps are\n"
            f"not completion evidence — only the convergence detector's stamp\n"
            f"counts.\n\n"
            f"Re-run:\n"
            f"  bash scripts/verify/check-converged.sh {ref_dir} --write-stamp\n"
        )

    if age_s > _VERIFY_STAMP_MAX_AGE_S:
        return (
            f"⛔ UI-RE Structural-stamp gate: STALE stamp for {ref_dir}\n\n"
            f"structural-convergence-stamp.json is {int(age_s)}s old "
            f"(max {_VERIFY_STAMP_MAX_AGE_S}s).\n"
            f"Re-run:\n\n"
            f"  bash scripts/verify/check-converged.sh {ref_dir} --write-stamp\n"
        )

    # Re-validate the sections/result.txt hash — detects the cheat of stamping
    # while converged then editing result.txt to claim more PASS rows.
    result_file = ref_dir / "sections" / "result.txt"
    stamped_sha = stamp.get("sectionsResultSha256")
    if stamped_sha and result_file.is_file():
        import hashlib
        current_sha = hashlib.sha256(result_file.read_bytes()).hexdigest()
        if current_sha != stamped_sha:
            return (
                f"⛔ UI-RE Structural-stamp gate: result.txt tampered after stamp for {ref_dir}\n\n"
                f"sections/result.txt sha256 mismatch: stamp recorded {stamped_sha[:12]}…\n"
                f"but the file now hashes to {current_sha[:12]}…\n\n"
                f"The convergence evidence the stamp attests to has changed.\n"
                f"Re-run the convergence detector:\n\n"
                f"  bash scripts/verify/check-converged.sh {ref_dir} --write-stamp\n"
            )

    changed = _newer_impl_files(impl_dir, stamp_path)
    if changed:
        sample = "\n".join(f"  - {p}" for p in changed)
        return (
            f"⛔ UI-RE Structural-stamp gate: impl changed after stamp for {ref_dir}\n\n"
            f"These implementation files are newer than structural-convergence-stamp.json:\n"
            f"{sample}\n\n"
            f"Re-converge after the last code/asset edit:\n\n"
            f"  bash scripts/verify/check-converged.sh {ref_dir} --write-stamp\n"
        )
    return _enforce_verify_stamp(ref_dir)


def _enforce_canvas_replay_stamp(ref_dir: Path) -> str | None:
    """Block Stop unless scripts/verify/check-canvas-replay.sh wrote a fresh
    canvas-replay stamp AND the attestation it attests to still hashes
    identically.

    Parallel to _enforce_structural_convergence_stamp but for plans that
    opted into the canvas-replay closeout policy (v0.7.0). Canvas-replay
    is the opt-in escape from the 30-min canvas CSS replication cap for
    refs whose visual identity is canvas-driven (WebGL UnicornStudio
    scenes, generative scroll-driven plates). Review finding applied
    (2026-05-25):

      [1] No new GATE_ORDER entry — modifier inside post-implement.
      [2] Attestation file is operator's explicit license confirmation.
      [5] Stamp records sha256(attestation) so tampering is detected.
      [7] Schema: design doc says `kind: "canvas"` for section-compare relief.

    Invariants enforced here:
      - canvas-replay-stamp.json exists, is fresh (< _VERIFY_STAMP_MAX_AGE_S),
        was written by scripts/verify/check-canvas-replay.sh with
        closeoutKind=canvas-replay.
      - canvas-replay-attestation.json exists (the operator's license proof)
        and its sha256 matches what the stamp recorded.
      - impl files are not newer than the stamp (anti-tamper, mirrors
        canonical + structural).
    """
    impl_dir = _resolve_impl_dir(ref_dir)
    if impl_dir is None or not impl_dir.is_dir():
        return None  # pre-generation — no stamp required yet

    # H1: the canvas-replay closeout must also consult the skip-ledger — an
    # un-recovered fail-open gate skip cannot be masked by a fresh canvas-replay
    # stamp (parallel to the structural + canonical paths).
    gate_skip_block = _gate_skip_blocker(ref_dir)
    if gate_skip_block:
        return (
            f"⛔ UI-RE Canvas-replay gate: un-enforced gates for {ref_dir}\n\n"
            f"{gate_skip_block}\n"
        )

    stamp_path = ref_dir / "canvas-replay-stamp.json"
    attestation_path = ref_dir / "canvas-replay-attestation.json"

    if not stamp_path.is_file():
        return (
            f"⛔ UI-RE Canvas-replay gate: BLOCKED for {ref_dir}\n\n"
            f"closeoutPolicy=canvas-replay but no canvas-replay-stamp.json.\n"
            f"This run satisfies Stop via the canvas-replay attestation stamp; it\n"
            f"is written by:\n\n"
            f"  bash scripts/verify/check-canvas-replay.sh {ref_dir} --write-stamp\n\n"
            f"Prerequisites:\n"
            f"  - {ref_dir}/canvas-replay-attestation.json must exist with\n"
            f"    license, disclaimer, attestedBy, attestedAt, ref_canvas_sources.\n"
            f"  - See skills/ui-reverse-engineering/canvas-replay-mode.md for\n"
            f"    the operator-facing opt-in workflow + scope boundary.\n"
        )

    try:
        stamp = json.loads(stamp_path.read_text())
        import datetime
        stamped_at = datetime.datetime.strptime(
            stamp["verifiedAt"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=datetime.UTC)
        age_s = (datetime.datetime.now(datetime.UTC) - stamped_at).total_seconds()
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        return (
            f"⛔ UI-RE Canvas-replay gate: malformed stamp {stamp_path}\n\n"
            f"{exc}\n\n"
            f"Re-run `bash scripts/verify/check-canvas-replay.sh {ref_dir} --write-stamp`.\n"
        )

    stamped_by = stamp.get("stampedBy")
    closeout_kind = stamp.get("closeoutKind")
    if (
        stamped_by != "scripts/verify/check-canvas-replay.sh"
        or closeout_kind != "canvas-replay"
    ):
        return (
            f"⛔ UI-RE Canvas-replay gate: non-canonical stamp for {ref_dir}\n\n"
            f"canvas-replay-stamp.json must be written by\n"
            f"scripts/verify/check-canvas-replay.sh with closeoutKind=canvas-replay.\n"
            f"Current stampedBy={stamped_by!r}, closeoutKind={closeout_kind!r}.\n\n"
            f"Hand-written stamps and stamps from other writers are not accepted.\n"
            f"Re-run:\n"
            f"  bash scripts/verify/check-canvas-replay.sh {ref_dir} --write-stamp\n"
        )

    if age_s > _VERIFY_STAMP_MAX_AGE_S:
        return (
            f"⛔ UI-RE Canvas-replay gate: STALE stamp for {ref_dir}\n\n"
            f"canvas-replay-stamp.json is {int(age_s)}s old "
            f"(max {_VERIFY_STAMP_MAX_AGE_S}s).\n"
            f"Re-run:\n\n"
            f"  bash scripts/verify/check-canvas-replay.sh {ref_dir} --write-stamp\n"
        )

    # Attestation file must exist + still hash to the same value the stamp
    # recorded. Tamper detection protects the operator's license confirmation
    # (adding ref_canvas_sources URLs after stamping would
    # silently expand which JS bundles the gate allows).
    if not attestation_path.is_file():
        return (
            f"⛔ UI-RE Canvas-replay gate: attestation missing for {ref_dir}\n\n"
            f"canvas-replay-stamp.json present but canvas-replay-attestation.json\n"
            f"is gone. The attestation is the operator's license confirmation;\n"
            f"the stamp attests to its content and cannot stand alone.\n\n"
            f"Restore the attestation file or re-run:\n"
            f"  bash scripts/verify/check-canvas-replay.sh {ref_dir} --write-stamp\n"
        )

    import hashlib
    stamped_sha = stamp.get("attestationSha256")
    current_sha = hashlib.sha256(attestation_path.read_bytes()).hexdigest()
    if stamped_sha and current_sha != stamped_sha:
        return (
            f"⛔ UI-RE Canvas-replay gate: attestation tampered after stamp for {ref_dir}\n\n"
            f"canvas-replay-attestation.json sha256 mismatch: stamp recorded "
            f"{stamped_sha[:12]}…\nbut the file now hashes to {current_sha[:12]}….\n\n"
            f"The license/disclaimer/ref_canvas_sources the stamp attests to\n"
            f"has changed. Re-attest:\n\n"
            f"  bash scripts/verify/check-canvas-replay.sh {ref_dir} --write-stamp\n"
        )

    changed = _newer_impl_files(impl_dir, stamp_path)
    if changed:
        sample = "\n".join(f"  - {p}" for p in changed)
        return (
            f"⛔ UI-RE Canvas-replay gate: impl changed after stamp for {ref_dir}\n\n"
            f"These implementation files are newer than canvas-replay-stamp.json:\n"
            f"{sample}\n\n"
            f"Re-stamp after the last code/asset edit:\n\n"
            f"  bash scripts/verify/check-canvas-replay.sh {ref_dir} --write-stamp\n"
        )
    return None


def _enforce_ref_dir(ref_dir: Path) -> str | None:
    # Load current gate from pipeline-state.json.
    # If absent, treat as fresh start at "reference" gate (not legacy section-compare fallback).
    state = PipelineState.load(ref_dir)
    current_gate = state.current_gate

    # Explicit terminal state is the only non-success release path. Do not
    # infer terminal lifecycle from `unclonable_reasons`; those are evidence
    # details, not a completion/staleness contract.
    if state.terminal_state:
        terminal_block = _terminal_state_block_reason(ref_dir, state)
        if terminal_block:
            return terminal_block
        return None

    if state.unclonable_reasons:
        # Legacy compatibility: old state files may contain blockers but no
        # terminalState. Keep them fail-closed so agents repair/migrate the
        # lifecycle state instead of silently relying on overloaded reasons.
        canonical_reasons = [
            r for r in state.unclonable_reasons
            if r.get("gate") in GATE_ORDER
        ]
        if canonical_reasons:
            return (
                f"⛔ UI-RE terminal-state gate: legacy blocker lacks terminalState for {ref_dir}\n\n"
                f"pipeline-state.json has unclonable_reasons but no terminalState. "
                f"Do not forge verify-stamp.json. Record the explicit lifecycle "
                f"state instead, for example:\n\n"
                f"  python -m ui_clone.state terminal {ref_dir} "
                f"--status incomplete --category harvested-failure "
                f"--gate {canonical_reasons[0].get('gate')} "
                f"--reason '<why this run is terminal>'\n"
            )
        if state.completed_steps:
            return None

    # Closeout policy routing (Task #11): structural plans (section-staged
    # convergence loops) require their structural-convergence-stamp.json in
    # addition to canonical verify-stamp.json. Canonical plans (default)
    # require only verify-stamp.json from pipeline.execute_verify.
    if state.closeout_policy == "structural":
        stamp_enforcer = _enforce_structural_convergence_stamp
    elif state.closeout_policy == "canvas-replay":
        # v0.7.0: canvas-replay opt-in escape from the 30-min canvas CSS
        # replication cap for refs whose visual identity is canvas-driven.
        # The stamp from check-canvas-replay.sh attests to an operator-
        # written canvas-replay-attestation.json (license + disclaimer +
        # ref_canvas_sources). See skills/ui-reverse-engineering/canvas-
        # replay-mode.md for the operator workflow.
        stamp_enforcer = _enforce_canvas_replay_stamp
    else:
        stamp_enforcer = _enforce_verify_stamp

    impl_dir = _resolve_impl_dir(ref_dir)
    if impl_dir is not None and impl_dir.is_dir():
        return stamp_enforcer(ref_dir)

    if current_gate in {"section-compare", "done"}:
        gate_result = _run_gate(ref_dir, "section-compare")
        if not gate_result.get("passed", True):
            return _section_compare_block_reason(ref_dir, gate_result)
        # Section-compare PASS but no stamp yet — point the agent at the
        # canonical entry instead of releasing silently.
        return stamp_enforcer(ref_dir)

    if current_gate not in GATE_ORDER:
        return _unknown_gate_block_reason(current_gate, ref_dir)

    gate_result = _run_gate(ref_dir, current_gate)
    if not gate_result.get("passed", True):
        return _block_reason_for_gate(current_gate, ref_dir, gate_result)
    # Per-gate PASS does NOT release the Stop hook on its own — verify
    # stamp is the canonical "agent finished cleanly" signal.
    return stamp_enforcer(ref_dir)


def _coerce_stop_hook_active(value: object) -> bool:
    """Codex LOW: the Stop payload may carry stop_hook_active as a real bool OR
    a JSON string. `bool("false")` is truthy, which would wrongly release the
    gate on the FIRST stop. Coerce string forms explicitly."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "off", "none")
    return bool(value)


def main() -> None:
    project_root = _find_project_root()
    search_root = project_root / "tmp" / "ref"

    # Claude Code Stop hooks receive a JSON payload on stdin with
    # `session_id`, `hook_event_name`, etc. We read it (best-effort, never
    # block on parse error) to extract the active session id for the
    # driver-session bypass check.
    session_id_from_payload = ""
    stop_scope_session_id = ""
    stop_hook_active = False
    payload: dict[str, object] | None = None
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw.strip():
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    payload = parsed
                    sid = payload.get("session_id") or payload.get("sessionId")
                    if isinstance(sid, str):
                        stop_scope_session_id = sid.strip()
                    session_id_from_payload = _session_id_from_payload(payload)
                    stop_hook_active = _coerce_stop_hook_active(payload.get("stop_hook_active"))
        except (json.JSONDecodeError, OSError):
            pass
    if not session_id_from_payload:
        session_id_from_payload = _session_id_from_payload(payload)

    # Stop-hook re-entrancy guard. Claude Code sets stop_hook_active=true when
    # the current stop is itself the result of a previous Stop-hook block. If we
    # block again the turn never ends — it loops until the consecutive-block cap
    # (observed on loop-112: 9 blocks -> ~2h18m wasted churn, then a forced
    # turn-end with the agent left idle and no STATUS marker, read as a stall).
    # Allow the stop: the agent was already nudged once, and the driver's STATUS
    # marker + stall watchdog own round closeout. Matches Claude Code guidance.
    if stop_hook_active:
        sys.exit(0)

    # Driver-session bypass — maintainer running parallel loops as an
    # observer/orchestrator, not a clone agent. Production users never
    # write the marker, so this is a no-op for them.
    if _is_driver_session(project_root, session_id_from_payload):
        sys.exit(0)

    active_dirs = _fresh_active_dirs(
        _find_active_markers(search_root)
        + _find_active_markers(project_root / ".ui-clone" / "runs")
    )
    # Ownership scoping runs UNCONDITIONALLY, including when the Stop payload
    # carries no session_id. should_enforce_ref_for_session handles both cases:
    # with a sid it enforces only refs this session touched; with NO sid it
    # SKIPS a ref whose crumbs all belong to OTHER identifiable sessions but
    # still enforces a crumb-LESS active ref (the omx ship-short fail-closed
    # case — an un-instrumented own-clone leaves no crumb). Previously this block
    # was gated behind `if stop_scope_session_id:`, so on a no-sid Stop the
    # no-sid branch of should_enforce_ref_for_session never ran and an unrelated
    # tab was blocked by another session's live clone.
    scoped_dirs = [
        ref_dir
        for ref_dir in active_dirs
        if _should_enforce_ref_for_session(ref_dir, stop_scope_session_id)
    ]
    skipped = [ref_dir for ref_dir in active_dirs if ref_dir not in set(scoped_dirs)]
    if skipped:
        names = ", ".join(str(p) for p in skipped[:3])
        more = f" (+{len(skipped) - 3} more)" if len(skipped) > 3 else ""
        _whose = (
            "not touched by this session"
            if stop_scope_session_id
            else "owned by other identifiable session(s); this Stop carried no session id"
        )
        print(
            "ui-clone-skills: Session-scoped Stop gate skipped "
            f"{len(skipped)} WIP ref(s) {_whose}: {names}{more}.",
            file=sys.stderr,
        )
    active_dirs = scoped_dirs
    if not active_dirs:
        # Off-pipeline scratch-clone closure (omx postmortem): block only on
        # the CORRELATED PAIR — (A) this session browsed an EXTERNAL site
        # (browse crumb from pre_bash) AND (B) this session wrote
        # clone-shaped files (write crumb from pre_generate). One-signal
        # activation is context-blind: the browse crumb alone fired on an
        # orchestrator session in live use (2026-06-12) — browse-only
        # sessions (orchestration, monitoring, research) and write-only
        # sessions (ordinary web dev) must stop freely. Block once
        # (stop_hook_active re-entrancy already returned above) demanding
        # pipeline bootstrap, with an explicit user-level escape hatch.
        if (
            os.environ.get("UI_RE_ALLOW_OFFPIPELINE") != "1"
            and session_id_from_payload
            and has_external_browse(project_root, session_id_from_payload)
            and has_clone_writes(project_root, session_id_from_payload)
        ):
            reason = (
                "⛔ UI-RE off-pipeline Stop gate: this session browsed an external "
                "site via agent-browser but owns no pipeline ref dir — clone-shaped "
                "work without a single verification gate is the documented "
                "ship-short failure mode (omx postmortem: 1593px missing, "
                "completion declared on build/smoke checks).\n\n"
                "Either bootstrap the pipeline so gates can run:\n"
                "  python -m ui_clone.pipeline <url> <component> <session> run --phases 0A,1,2\n"
                "or, if this genuinely is not clone work, the off-pipeline "
                "escape hatch is documented for HUMANS in docs/agent-cli.md "
                "— ask the user."
            )
            _emit_block(reason)
            sys.exit(0)
        sys.exit(0)

    if len(active_dirs) > 1:
        print(
            f"ui-clone-skills: WARNING: {len(active_dirs)} concurrent WIP markers. Enforcing all.",
            file=sys.stderr,
        )

    for ref_dir in active_dirs:
        block_reason = _enforce_ref_dir(ref_dir)
        if block_reason:
            _emit_block(block_reason)
            sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
