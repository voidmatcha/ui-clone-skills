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
    "_check_dependencies",
    "_count_tsx_files",
    "_find_app_dir",
    "_has_files",
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


class Pipeline:
    """Pipeline status checker — determines current phase and next action.

    Per-phase logic lives in `ui_clone.pipeline_phases.checks` (check_phase_*),
    `ui_clone.pipeline_phases.execute` (execute_phases), and
    `ui_clone.pipeline_phases.verify` (execute_verify). The methods below
    are thin shims so existing call-sites (`p.check_phase_2(...)`,
    `p.execute_phases(...)`, etc.) keep working.
    """

    def __init__(self, url: str, component: str, session: str) -> None:
        self.url = url
        self.component = component
        self.session = session
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
        self.ref_dir = self.project_root / "tmp" / "ref" / component
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

    def execute_verify(self) -> int:
        from ui_clone.pipeline_phases.verify import execute_verify as _impl
        return _impl(self)

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
        usage="python -m ui_clone.pipeline <url> <component> <session> {status|run} [--json] [--phases LIST]",
    )
    parser.add_argument("url", help="Target URL")
    parser.add_argument("component", help="Component name")
    parser.add_argument("session", help="Browser session name")
    parser.add_argument(
        "action",
        choices=["status", "run", "verify"],
        help=(
            "status = inspect + next-step report; "
            "run = deterministic execution of pre-generation phases; "
            "verify = run the post-impl gates after impl/ has been generated"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Also output structured JSON summary (status only)",
    )
    parser.add_argument(
        "--phases",
        default="0A,1,2",
        help="Comma-separated phases to execute when action=run (default 0A,1,2)",
    )
    args = parser.parse_args()

    pipeline = Pipeline(args.url, args.component, args.session)
    if args.action == "status":
        sys.exit(pipeline.run(json_output=args.json_output))
    if args.action == "verify":
        sys.exit(pipeline.execute_verify())
    # action == run
    requested = tuple(p.strip() for p in args.phases.split(",") if p.strip())
    sys.exit(pipeline.execute_phases(requested))


if __name__ == "__main__":
    main()
