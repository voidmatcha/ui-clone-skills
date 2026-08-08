"""
Pipeline status — determines current phase and reports next action.

Usage:
    python -m ui_clone.pipeline <url> <component> <session> status [--json]
Exit: 0 on success, 1 on missing dependencies, 2 on usage error.

The per-phase check/execute/verify bodies live in
`ui_clone.pipeline_phases.*` to keep this module focused on the
`Pipeline` class shim and CLI. Public surface (Pipeline methods,
module-level helpers, dataclasses) is unchanged.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from itertools import islice
from pathlib import Path

from ui_clone.gate import Gate
from ui_clone.hooks._common import BOLD as _BOLD
from ui_clone.hooks._common import GREEN as _GREEN
from ui_clone.hooks._common import NC as _NC
from ui_clone.hooks._common import RED as _RED
from ui_clone.hooks._common import YELLOW as _YELLOW
from ui_clone.hooks._common import find_project_root
from ui_clone.pipeline_phases.types import PhaseCheck, PhaseResult
from ui_clone.state import GATE_ORDER, PipelineState

__all__ = [
    "Pipeline",
    "PhaseCheck",
    "PhaseResult",
    "REFERENCE_EVIDENCE_ARTIFACTS",
    "_check_dependencies",
    "_count_tsx_files",
    "_find_app_dir",
    "_has_files",
    "reference_evidence_satisfied",
]

# Required CLI tools and install hints
_REQUIRED_TOOLS: list[tuple[str, str]] = [
    ("agent-browser", "npm i -g agent-browser"),
    ("ffmpeg", "brew install ffmpeg"),
    ("jq", "brew install jq"),
    ("compare", "brew install imagemagick"),
    ("identify", "brew install imagemagick"),
    ("python3", "brew install python3"),
    ("curl", "brew install curl"),
]

_OPTIONAL_TOOLS: list[tuple[str, str]] = [
    ("dssim", "brew install dssim"),
]


def _check_dependencies() -> list[str]:
    """Check for required CLI tools. Returns list of missing tool names."""
    missing: list[str] = []
    for tool, hint in _REQUIRED_TOOLS:
        if shutil.which(tool) is None:
            missing.append(f"{tool} ({hint})")
    for tool, hint in _OPTIONAL_TOOLS:
        if shutil.which(tool) is None:
            print(f"  {_YELLOW}⚠{_NC} Optional: {tool} ({hint})")
    return missing


def _has_files(directory: Path, pattern: str, min_count: int) -> bool:
    """Check if directory has at least min_count files matching glob pattern."""
    if not directory.is_dir():
        return False
    return len(list(islice(directory.rglob(pattern), min_count))) >= min_count


def _find_app_dir(project_root: Path, component: str) -> Path | None:
    """Find the application directory for a component.

    Priority:
    1. Component-specific monorepo dir (apps/<component>/src/...)
    2. Flat project layout (src/components, app/, src/)
    3. First monorepo match (fallback)
    """
    # Priority 1: component-specific app dir (monorepo)
    app_base = project_root / "apps" / component
    for subdir in ["src/components", "src", "app"]:
        candidate = app_base / subdir
        if candidate.is_dir():
            return app_base

    # Priority 2: flat project layout
    for subdir in ["src/components", "app", "src"]:
        candidate = project_root / subdir
        if candidate.is_dir():
            return project_root

    # Priority 3: first match in monorepo (fallback)
    apps_dir = project_root / "apps"
    if apps_dir.is_dir():
        for app_dir in sorted(apps_dir.iterdir()):
            if (app_dir / "src" / "components").is_dir():
                return app_dir

    return None


def _count_tsx_files(app_dir: Path) -> int:
    """Count .tsx files in common component locations."""
    count = 0
    for subdir in ["src/components", "src/app", "app"]:
        d = app_dir / subdir
        if d.is_dir():
            count += sum(1 for f in d.rglob("*.tsx") if f.is_file())
    return count


def _resolve_ref_dir(
    project_root: Path,
    component: str,
    *,
    run_dir: str | None = None,
    reuse_frozen_ref: str | None = None,
) -> Path:
    """Resolve the run/reference directory for legacy and future layouts.

    Legacy callers pass a component name and use `<root>/tmp/ref/<component>`.
    Agent-first CLI callers may pass an existing run directory directly, or
    use the planned `<root>/.ui-clone/runs/<id>` layout. This resolver adds
    that compatibility without migrating existing artifacts.

    Explicit binding (LAND item A): ``reuse_frozen_ref`` takes precedence
    (bind an externally-supplied prebuilt reference), then ``run_dir`` (bind
    the run/reference dir explicitly). When neither is given the resolution
    below is byte-identical to the legacy resolver.
    """
    if reuse_frozen_ref:
        return Path(reuse_frozen_ref).expanduser().resolve()
    if run_dir:
        return Path(run_dir).expanduser().resolve()

    raw = Path(component).expanduser()
    if raw.is_absolute() or len(raw.parts) > 1:
        candidate = raw if raw.is_absolute() else project_root / raw
        if candidate.exists():
            return candidate.resolve()

    future_run = project_root / ".ui-clone" / "runs" / component
    if future_run.exists():
        return future_run.resolve()

    return project_root / "tmp" / "ref" / component


# LAND item A (frozen-ref flag): reference-ACQUISITION evidence artifacts a
# non-frozen run produces before downstream gates trust the reference. An
# externally-supplied prebuilt reference is evidence-satisfied-by-supply, so the
# absence of these acquisition artifacts does not deadlock as "unclonable".
# This marks ACQUISITION only — it NEVER bypasses the self-pass meta-gate
# (impl==ref => AE~0), the localized-defect guard, or the ref-variance guard,
# all of which are enforced downstream at section-compare time.
REFERENCE_EVIDENCE_ARTIFACTS = (
    "extracted.json",
    "section-map.json",
    "animations-detected.json",
)


def reference_evidence_satisfied(
    ref_dir: Path, *, external_prebuilt: bool,
) -> tuple[bool, list[str]]:
    """Is the reference's ACQUISITION evidence satisfied?

    For an externally-supplied prebuilt reference the flag marks it satisfied
    (the reference exists and was supplied as evidence), so missing acquisition
    artifacts do not deadlock. For a normal run the real artifact check applies.
    """
    missing = [a for a in REFERENCE_EVIDENCE_ARTIFACTS if not (ref_dir / a).is_file()]
    if external_prebuilt:
        # evidence-satisfied-by-supply: acquisition is not re-demanded.
        return True, missing  # report what's missing (telemetry) but do not block
    return (len(missing) == 0), missing


class Pipeline:
    """Pipeline status checker — determines current phase and next action.

    Per-phase logic lives in `ui_clone.pipeline_phases.checks` (check_phase_*),
    `ui_clone.pipeline_phases.execute` (execute_phases), and
    `ui_clone.pipeline_phases.verify` (execute_verify). The methods below
    are thin shims so existing call-sites (`p.check_phase_2(...)`,
    `p.execute_phases(...)`, etc.) keep working.
    """

    def __init__(
        self,
        url: str,
        component: str,
        session: str,
        *,
        run_dir: str | None = None,
        reuse_frozen_ref: str | None = None,
    ) -> None:
        self.url = url
        self.component = component
        self.session = session
        # LAND item A: explicit run/reference binding. external_prebuilt is True
        # only when --reuse-frozen-ref is supplied; it gates the acquisition
        # shortcut so a normal run is unchanged.
        self.reuse_frozen_ref = reuse_frozen_ref
        self.external_prebuilt = bool(reuse_frozen_ref)
        # v1.3: prefer cwd as the project root scope so iterations launched
        # from a nested sub-workspace land their artifacts inside that
        # sub-workspace, not in the plugin repo's top-level tmp/ref.
        # find_project_root() walks up to the git root, which is right for
        # cross-process hook resolution but wrong for per-workspace output
        # isolation. Fall back to find_project_root() only when the cwd is
        # clearly not a working dir (system root, /tmp, /var/tmp).
        cwd = Path.cwd()
        if str(cwd) in ("/", "/tmp", "/var/tmp", "/usr", "/etc"):
            self.project_root = find_project_root()
        else:
            self.project_root = cwd
        self.ref_dir = _resolve_ref_dir(
            self.project_root,
            component,
            run_dir=run_dir,
            reuse_frozen_ref=reuse_frozen_ref,
        )
        self.next_phase: str = ""
        self.next_step: str = ""

    def _set_next(self, phase: str, step: str) -> None:
        """Set next phase/step only if not already set (first incomplete wins)."""
        if not self.next_phase:
            self.next_phase = phase
            self.next_step = step

    def _check(self, label: str, condition: bool) -> PhaseCheck:
        """Create a phase check and print its status."""
        if condition:
            print(f"  {_GREEN}✓{_NC} {label}")
        else:
            print(f"  {_YELLOW}○{_NC} {label}")
        return PhaseCheck(label=label, passed=condition)

    # ── phase check shims ──
    def check_phase_0a(self) -> PhaseResult:
        from ui_clone.pipeline_phases.checks import check_phase_0a as _impl
        return _impl(self)

    def check_phase_0(self) -> PhaseResult:
        from ui_clone.pipeline_phases.checks import check_phase_0 as _impl
        return _impl(self)

    def check_phase_1(self) -> PhaseResult:
        from ui_clone.pipeline_phases.checks import check_phase_1 as _impl
        return _impl(self)

    def check_phase_2(self, has_ref: bool) -> PhaseResult:
        from ui_clone.pipeline_phases.checks import check_phase_2 as _impl
        return _impl(self, has_ref)

    def check_pre_generate_gate(self) -> bool:
        from ui_clone.pipeline_phases.checks import check_pre_generate_gate as _impl
        return _impl(self)

    def check_phase_3(self) -> PhaseResult:
        from ui_clone.pipeline_phases.checks import check_phase_3 as _impl
        return _impl(self)

    def check_phase_4(self) -> PhaseResult:
        from ui_clone.pipeline_phases.checks import check_phase_4 as _impl
        return _impl(self)

    # ── execute/verify shims ──
    def execute_phases(self, phases: tuple[str, ...] = ("0A", "1", "2")) -> int:
        from ui_clone.pipeline_phases.execute import execute_phases as _impl
        return _impl(self, phases)

    def execute_verify(self, json_output: bool = False) -> int:
        from ui_clone.pipeline_phases.verify import execute_verify as _impl
        return _impl(self, json_output=json_output)

    def _agent_paths(self) -> dict[str, object]:
        """Return compact, stable path guidance for LLM agents."""
        read_for_llm = [str(self.ref_dir / "pipeline-state.json")]
        for candidate in (
            self.ref_dir / "reports" / "llm-context.json",
            self.ref_dir / "reports" / "next-action.json",
            self.ref_dir / "verify-report.json",
            self.ref_dir / "verify-report.html",
        ):
            if candidate.exists():
                read_for_llm.append(str(candidate))

        do_not_read = [
            str(self.ref_dir / "raw"),
            str(self.ref_dir / "raw" / "dom.json"),
            str(self.ref_dir / "raw" / "computed.json"),
            str(self.ref_dir / "raw" / "styles.json"),
            str(self.ref_dir / "screenshots"),
            str(self.ref_dir / "videos"),
        ]
        return {
            "read_for_llm": read_for_llm,
            "do_not_read": do_not_read,
        }

    def status_payload(self) -> dict[str, object]:
        """Agent-readable run status contract.

        This is the small JSON truth surface hooks and LLM agents should read
        instead of scraping raw artifacts or human logs.
        """
        from ui_clone.pipeline_phases.verify import (
            _resolve_verify_impl_dir,
            canonical_stamp_problem,
        )

        state = PipelineState.load(self.ref_dir)
        remaining = [gate for gate in GATE_ORDER if gate not in state.completed_steps]
        if state.current_gate == "done":
            remaining = []

        verify_stamp = self.ref_dir / "verify-stamp.json"
        verify_report = self.ref_dir / "verify-report.json"
        impl_dir = _resolve_verify_impl_dir(self.ref_dir, self.project_root)
        terminal = dict(state.terminal_state)
        stamp_problem = (
            canonical_stamp_problem(self.ref_dir) if verify_stamp.is_file() else None
        )

        if terminal:
            status = str(terminal.get("status") or "terminal")
            next_action = terminal.get("next_action") or self._resume_command(state.current_gate)
        elif state.current_gate == "done" and verify_stamp.is_file() and stamp_problem is None:
            status = "verified"
            next_action = None
        elif state.current_gate == "done":
            status = "needs_verify_stamp"
            next_action = self._resume_command("section-compare")
        else:
            status = "active"
            next_action = self._resume_command(state.current_gate)

        path_guidance = self._agent_paths()
        return {
            "schemaVersion": 1,
            "status": status,
            "component": self.component,
            "url": self.url,
            "session": self.session,
            "layout": (
                "agent-run"
                if ".ui-clone" in self.ref_dir.parts and "runs" in self.ref_dir.parts
                else "legacy-tmp-ref"
            ),
            "run_dir": str(self.ref_dir),
            "ref_dir": str(self.ref_dir),
            "impl_dir": str(impl_dir) if impl_dir.is_dir() else "",
            "current_gate": state.current_gate,
            "completed_steps": state.completed_steps,
            "remaining": remaining,
            "closeoutPolicy": state.closeout_policy,
            "terminalState": terminal,
            "verify_stamp": {
                "path": str(verify_stamp),
                "exists": verify_stamp.is_file(),
                "success_only": True,
                "problem": stamp_problem,
            },
            "verify_report": {
                "path": str(verify_report),
                "exists": verify_report.is_file(),
            },
            "next_action": next_action,
            **path_guidance,
        }

    def print_status_json(self) -> int:
        print(json.dumps(self.status_payload(), ensure_ascii=False, indent=2))
        return 0

    def next(self, json_output: bool = False) -> int:
        payload = self.status_payload()
        next_payload = {
            "schemaVersion": 1,
            "status": payload["status"],
            "component": self.component,
            "run_dir": payload["run_dir"],
            "current_gate": payload["current_gate"],
            "terminalState": payload["terminalState"],
            "next_action": payload["next_action"],
            "read_for_llm": payload["read_for_llm"],
            "do_not_read": payload["do_not_read"],
        }
        if json_output:
            print(json.dumps(next_payload, ensure_ascii=False, indent=2))
            return 0
        print(f"{_BOLD}Next action: {self.component}{_NC}")
        print(f"  status : {next_payload['status']}")
        print(f"  current: {next_payload['current_gate']}")
        print(f"  next   : {next_payload['next_action']}")
        return 0

    def report(self, json_output: bool = False, for_llm: bool = False) -> int:
        payload = self.status_payload()
        report = {
            "schemaVersion": 1,
            "kind": "llm-context" if for_llm else "status-report",
            **payload,
        }
        if for_llm or json_output:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        print(f"{_BOLD}Report: {self.component}{_NC}")
        print(f"  status : {report['status']}")
        print(f"  run    : {report['run_dir']}")
        print(f"  gate   : {report['current_gate']}")
        print(f"  next   : {report['next_action']}")
        return 0

    def remaining(self, json_output: bool = False) -> int:
        """Print remaining gates from pipeline-state.json."""
        state = PipelineState.load(self.ref_dir)
        remaining = [gate for gate in GATE_ORDER if gate not in state.completed_steps]
        if state.current_gate == "done":
            remaining = []
        payload = {
            "component": self.component,
            "ref_dir": str(self.ref_dir),
            "current_gate": state.current_gate,
            "remaining": remaining,
            "next_action": self._resume_command(state.current_gate) if remaining else None,
        }
        if json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        print(f"{_BOLD}Remaining gates: {self.component}{_NC}")
        print(f"  current: {state.current_gate}")
        if remaining:
            for gate in remaining:
                print(f"  - {gate}")
            print(f"  next: {payload['next_action']}")
        else:
            print(f"  {_GREEN}none — pipeline state is done{_NC}")
        return 0

    def reconcile(self, json_output: bool = False) -> int:
        """Reconcile pipeline-state.json from current gate artifacts.

        Re-runs gate checks in-process without calling Gate.run(), so it does
        not increment failure counters. The first failing gate becomes
        current_gate; all earlier passing gates become completed_steps.
        """
        from ui_clone.gate import Gate
        from ui_clone.gates.dispatch import _gate_method_name

        gate = Gate(self.ref_dir)
        completed: list[str] = []
        first_failed: str | None = None
        gate_summary: list[dict[str, object]] = []
        for gate_name in GATE_ORDER:
            method = getattr(gate, _gate_method_name(gate_name))
            results = list(method())
            fail_count = sum(1 for row in results if row.status == "fail")
            pass_count = sum(1 for row in results if row.status == "pass")
            warn_count = sum(1 for row in results if row.status == "warn")
            passed = fail_count == 0
            gate_summary.append(
                {
                    "gate": gate_name,
                    "passed": passed,
                    "pass_count": pass_count,
                    "warn_count": warn_count,
                    "fail_count": fail_count,
                }
            )
            if passed and first_failed is None:
                completed.append(gate_name)
                continue
            if first_failed is None:
                first_failed = gate_name
            break

        current_gate = first_failed or "done"
        state = PipelineState.load(self.ref_dir)
        state.completed_steps = completed
        state.current_gate = current_gate
        for gate_name in completed:
            state.gate_fail_counts.pop(gate_name, None)
        state.save(self.ref_dir)

        payload = {
            "component": self.component,
            "ref_dir": str(self.ref_dir),
            "current_gate": current_gate,
            "completed_steps": completed,
            "remaining": [g for g in GATE_ORDER if g not in completed],
            "gates": gate_summary,
            "next_action": self._resume_command(current_gate),
        }
        exit_code = 0 if current_gate == "done" else 1
        if json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return exit_code
        print(f"{_BOLD}Reconciled pipeline state: {self.component}{_NC}")
        print(f"  current: {current_gate}")
        print(f"  completed: {len(completed)}/{len(GATE_ORDER)}")
        if current_gate != "done":
            print(f"  next: {payload['next_action']}")
        return exit_code

    def resume(self, json_output: bool = False) -> int:
        """Print the next command for the current pipeline gate."""
        state = PipelineState.load(self.ref_dir)
        command = self._resume_command(state.current_gate)
        payload = {
            "component": self.component,
            "ref_dir": str(self.ref_dir),
            "current_gate": state.current_gate,
            "next_action": command,
        }
        if json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        print(f"{_BOLD}Resume: {self.component}{_NC}")
        print(f"  current: {state.current_gate}")
        print(f"  next: {command}")
        return 0

    def _resume_command(self, current_gate: str) -> str:
        if current_gate == "done":
            return "No remaining gate."
        if current_gate in {"reference", "extraction"}:
            return (
                f"python -m ui_clone.pipeline {self.url} {self.component} "
                f"{self.session} run --phases 0A,1,2"
            )
        if current_gate in GATE_ORDER[GATE_ORDER.index("post-implement"):]:
            return (
                f"python -m ui_clone.pipeline {self.url} {self.component} "
                f"{self.session} verify"
            )
        return f"python -m ui_clone.gate {self.ref_dir} {current_gate}"

    def run(self, json_output: bool = False) -> int:
        """Run full pipeline status check.

        Returns 0 on success, 1 on missing dependencies.
        """
        # Dependency check
        missing = _check_dependencies()
        if missing:
            print(f"{_RED}Missing required tools:{_NC}")
            for m in missing:
                print(f"  {_RED}✗{_NC} {m}")
            print("  brew install imagemagick ffmpeg dssim && npm i -g agent-browser")
            return 1

        # Pipeline state header
        state = PipelineState.load(self.ref_dir)
        total_gates = len(GATE_ORDER)
        completed = len(state.completed_steps)
        print(f"{_BOLD}━━━ Pipeline State ━━━{_NC}")
        print(f"  Component  : {state.component or self.component}")
        print(f"  Progress   : {completed}/{total_gates} gates completed")
        if state.current_gate == "done":
            print("  Current    : ✅ ALL GATES COMPLETE")
        else:
            print(f"  Current    : {state.current_gate}")
        if state.last_updated:
            print(f"  Updated    : {state.last_updated}")
        print(
            f"{_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━{_NC}"
        )
        print()

        # Short-circuit if all gates done
        if state.current_gate == "done":
            print(
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            print(f"{_GREEN}All phases complete!{_NC}")
            if json_output:
                self._print_json_result()
            return 0

        print(f"{_BOLD}Pipeline Status: {self.component}{_NC}")
        print(f"URL: {self.url}")
        print(f"Session: {self.session}")
        print(f"Ref dir: {self.ref_dir}")
        print()

        # Phase checks
        self.check_phase_0a()
        self.check_phase_0()

        phase_1 = self.check_phase_1()
        # static/ref/ screenshots is the canonical "reference exists" signal — it is the
        # first check appended in check_phase_1() and the local `has_ref` used there to
        # decide next_step. Other phase 1 checks (scroll-video, transitions, regions.json)
        # are supplementary; if regions.json exists alone, Phase 2 must still be skipped.
        has_ref = bool(phase_1.checks) and phase_1.checks[0].passed
        # LAND item A: an externally-supplied prebuilt reference is
        # evidence-satisfied-by-supply (reference ACQUISITION only). Gated behind
        # --reuse-frozen-ref so a normal run is byte-identical; the downstream
        # self-pass / localized-defect / ref-variance guards are untouched.
        if not has_ref and self.external_prebuilt:
            satisfied, _missing = reference_evidence_satisfied(
                self.ref_dir, external_prebuilt=True
            )
            has_ref = satisfied

        self.check_phase_2(has_ref)

        # Auto pre-generate gate
        self.check_pre_generate_gate()

        self.check_phase_3()
        self.check_phase_4()

        # Next action
        print(
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        if self.next_phase:
            print(f"{_BOLD}NEXT: Phase {self.next_phase}{_NC}")
            print(f"{_YELLOW}→ {self.next_step}{_NC}")

            # Run extraction gate for additional context
            if self.next_phase == "2" and self.ref_dir.is_dir():
                print()
                print("Running extraction gate check:")
                gate = Gate(self.ref_dir)
                gate.run("extraction")
        else:
            print(f"{_GREEN}All phases complete!{_NC}")

        if json_output:
            self._print_json_result()

        return 0

    def _print_json_result(self) -> None:
        """Print JSON summary of pipeline status.

        Re-loads PipelineState because run() may have advanced the gate
        via check_pre_generate_gate() → Gate.run() → mark_passed().
        """
        state = PipelineState.load(self.ref_dir)
        output = {
            "component": self.component,
            "url": self.url,
            "session": self.session,
            "ref_dir": str(self.ref_dir),
            "current_gate": state.current_gate,
            "completed_steps": len(state.completed_steps),
            "total_steps": len(GATE_ORDER),
            "next_phase": self.next_phase or None,
            "next_step": self.next_step or None,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pipeline driver for ui-clone-skills",
        usage=(
            "python -m ui_clone.pipeline <url> <component> <session> "
            "{status|run|verify|remaining|reconcile|resume|next|report} "
            "[--json] [--for-llm] [--phases LIST]"
        ),
    )
    parser.add_argument("url", help="Target URL")
    parser.add_argument("component", help="Component name")
    parser.add_argument("session", help="Browser session name")
    parser.add_argument(
        "action",
        choices=[
            "status",
            "run",
            "verify",
            "remaining",
            "reconcile",
            "resume",
            "next",
            "report",
        ],
        help=(
            "status = inspect + next-step report; "
            "run = deterministic execution of pre-generation phases; "
            "verify = run the post-impl gates after impl/ has been generated; "
            "remaining/reconcile/resume/next/report = recover or inspect interrupted runs"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output machine-readable JSON only where supported",
    )
    parser.add_argument(
        "--for-llm",
        action="store_true",
        dest="for_llm",
        help="Emit compact agent/LLM context for action=report",
    )
    parser.add_argument(
        "--phases",
        default="0A,1,2",
        help="Comma-separated phases to execute when action=run (default 0A,1,2)",
    )
    parser.add_argument(
        "--run-dir",
        default=None,
        help="Bind the run/reference dir explicitly (overrides component inference)",
    )
    parser.add_argument(
        "--reuse-frozen-ref",
        default=None,
        dest="reuse_frozen_ref",
        help=(
            "Bind an externally-supplied prebuilt reference; marks its "
            "acquisition evidence satisfied-by-supply (acquisition only)"
        ),
    )
    args = parser.parse_args()

    pipeline = Pipeline(
        args.url,
        args.component,
        args.session,
        run_dir=args.run_dir,
        reuse_frozen_ref=args.reuse_frozen_ref,
    )
    if args.action == "status":
        if args.json_output:
            sys.exit(pipeline.print_status_json())
        sys.exit(pipeline.run(json_output=args.json_output))
    if args.action == "verify":
        sys.exit(pipeline.execute_verify(json_output=args.json_output))
    if args.action == "remaining":
        sys.exit(pipeline.remaining(json_output=args.json_output))
    if args.action == "reconcile":
        sys.exit(pipeline.reconcile(json_output=args.json_output))
    if args.action == "resume":
        sys.exit(pipeline.resume(json_output=args.json_output))
    if args.action == "next":
        sys.exit(pipeline.next(json_output=args.json_output))
    if args.action == "report":
        sys.exit(pipeline.report(json_output=args.json_output, for_llm=args.for_llm))
    # action == run
    requested = tuple(p.strip() for p in args.phases.split(",") if p.strip())
    sys.exit(pipeline.execute_phases(requested))


if __name__ == "__main__":
    main()
