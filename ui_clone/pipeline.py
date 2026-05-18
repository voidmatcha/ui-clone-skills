"""
Pipeline status — determines current phase and reports next action.

Usage:
    python -m ui_clone.pipeline <url> <component> <session> status [--json]
Exit: 0 on success, 1 on missing dependencies, 2 on usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path

from ui_clone import dag as _dag
from ui_clone.gate import Gate
from ui_clone.hooks._common import BOLD as _BOLD
from ui_clone.hooks._common import GREEN as _GREEN
from ui_clone.hooks._common import NC as _NC
from ui_clone.hooks._common import RED as _RED
from ui_clone.hooks._common import YELLOW as _YELLOW
from ui_clone.hooks._common import find_project_root, load_json_safe
from ui_clone.state import GATE_ORDER, PipelineState

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


@dataclass
class PhaseCheck:
    """Single artifact check within a phase."""

    label: str
    passed: bool
    message: str = ""


@dataclass
class PhaseResult:
    """Result of checking one pipeline phase."""

    name: str
    title: str
    checks: list[PhaseCheck] = field(default_factory=list)
    next_step: str = ""
    skipped: bool = False
    skip_reason: str = ""


def _check_dependencies() -> list[str]:
    """Check for required CLI tools. Returns list of missing tool names."""
    missing: list[str] = []
    for tool, hint in _REQUIRED_TOOLS:
        if shutil.which(tool) is None:
            missing.append(f"{tool} ({hint})")
    for tool, hint in _OPTIONAL_TOOLS:
        if shutil.which(tool) is None:
            print(f"  {_YELLOW}\u26a0{_NC} Optional: {tool} ({hint})")
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
    """Pipeline status checker — determines current phase and next action."""

    def __init__(self, url: str, component: str, session: str) -> None:
        self.url = url
        self.component = component
        self.session = session
        # v1.3: prefer cwd as the project root scope so iterations launched
        # from `scratch/loop-N/` or similar isolated subdirs land their
        # artifacts inside that subdir, not in the plugin repo's top-level
        # tmp/ref. find_project_root() walks up to the git root, which is
        # right for cross-process hook resolution but wrong for per-loop
        # output isolation. Fall back to find_project_root() only when the
        # cwd is clearly not a working dir (system root, /tmp, /var/tmp).
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
            print(f"  {_GREEN}\u2713{_NC} {label}")
        else:
            print(f"  {_YELLOW}\u25cb{_NC} {label}")
        return PhaseCheck(label=label, passed=condition)

    def check_phase_0a(self) -> PhaseResult:
        """Phase 0A: Canvas/WebGL render type detection."""
        result = PhaseResult(name="0A", title="Render Type Detection")
        print(f"{_BOLD}Phase 0A \u2014 Render Type Detection{_NC}")

        detect_path = self.ref_dir / "canvas-webgl-detection.json"
        data = load_json_safe(detect_path)

        if data is not None:
            render_type = data.get("primaryRenderType", "unknown")
            has_canvas = data.get("hasCanvas", False)
            has_webgl = data.get("hasWebGL", False)
            print(
                f"  {_GREEN}\u2713{_NC} Render type: {render_type} (canvas={has_canvas}, webgl={has_webgl})"
            )
            result.checks.append(PhaseCheck("canvas-webgl-detection.json", True))

            if has_canvas or has_webgl:
                print(
                    f"  {_YELLOW}\u26a0{_NC}  Canvas/WebGL detected \u2014 CSS replication will be APPROXIMATE."
                )
                print("       Read canvas-webgl-extraction.md before Phase 2 extraction.")
        else:
            print(
                f"  {_YELLOW}\u25cb{_NC} canvas-webgl-detection.json missing \u2014 run detection FIRST"
            )
            print(f"     agent-browser --session {self.session} open {self.url}")
            result.checks.append(PhaseCheck("canvas-webgl-detection.json", False))
            if not self.ref_dir.is_dir():
                self._set_next("0A", "Run canvas/WebGL detection, then re-run status.")
                result.next_step = "Run canvas/WebGL detection, then re-run status."

        print()
        return result

    def check_phase_0(self) -> PhaseResult:
        """Phase 0: Check for prior data."""
        result = PhaseResult(name="0", title="Prior Data")
        print(f"{_BOLD}Phase 0 \u2014 Prior Data{_NC}")

        has_spec = (self.ref_dir / "transition-spec.json").is_file()
        if has_spec:
            print(f"  {_GREEN}\u2713{_NC} transition-spec.json exists \u2014 READ THIS FIRST")
        else:
            print(f"  {_YELLOW}\u25cb{_NC} No prior transition-spec.json")

        has_extracted = (self.ref_dir / "extracted.json").is_file()
        if has_extracted:
            print(f"  {_GREEN}\u2713{_NC} extracted.json exists")

        result.checks.append(PhaseCheck("transition-spec.json", has_spec))
        result.checks.append(PhaseCheck("extracted.json", has_extracted))
        print()
        return result

    def check_phase_1(self) -> PhaseResult:
        """Phase 1: Reference capture."""
        result = PhaseResult(name="1", title="Reference Capture")
        print(f"{_BOLD}Phase 1 \u2014 Reference Capture{_NC}")

        has_ref = _has_files(self.ref_dir / "static" / "ref", "*.png", 5)
        result.checks.append(self._check("static/ref/ screenshots (\u22655 files)", has_ref))
        result.checks.append(
            self._check(
                "scroll-video/ref/ video",
                _has_files(self.ref_dir / "scroll-video" / "ref", "*.webm", 1),
            )
        )
        result.checks.append(
            self._check(
                "transitions/ref/ videos",
                _has_files(self.ref_dir / "transitions" / "ref", "*.webm", 1),
            )
        )
        result.checks.append(self._check("regions.json", (self.ref_dir / "regions.json").is_file()))

        if not has_ref:
            self._set_next("1", f"Invoke /ui-capture {self.url}. See SKILL.md Phase 1.")
            result.next_step = f"Invoke /ui-capture {self.url}. See SKILL.md Phase 1."
        print()
        return result

    def check_phase_2(self, has_ref: bool) -> PhaseResult:
        """Phase 2: Extraction checks."""
        result = PhaseResult(name="2", title="Extraction")
        print(f"{_BOLD}Phase 2 \u2014 Extraction{_NC}")

        if not has_ref:
            print(f"  {_YELLOW}\u25cb{_NC} (skipped \u2014 complete Phase 1 first)")
            result.skipped = True
            result.skip_reason = "Complete Phase 1 first"
            print()
            return result

        extraction_steps: list[tuple[str, str, str]] = [
            (
                "structure.json",
                "section-map.json",
                "Read dom-extraction.md \u2192 run Step 2 (structure) + semantic section enumeration.",
            ),
            (
                "head.json",
                "fonts.json",
                "Read asset-extraction.md \u2192 extract head, assets, fonts.",
            ),
        ]
        for file_a, file_b, step_msg in extraction_steps:
            passed = (self.ref_dir / file_a).is_file() and (self.ref_dir / file_b).is_file()
            self._check(f"{file_a} + {file_b}", passed)
            if not passed:
                self._set_next("2", step_msg)

        single_file_steps: list[tuple[str, str, str]] = [
            (
                "svg-text-elements.json",
                "Step 2.5b",
                "Read dom-extraction.md Step 2.5b \u2192 SVG-as-text detection.",
            ),
            (
                "animation-init-styles.json",
                "Step 2.6",
                "Read dom-extraction.md Steps 2.6a-b \u2192 extract animation init styles.",
            ),
        ]
        for filename, step_label, step_msg in single_file_steps:
            passed = (self.ref_dir / filename).is_file()
            self._check(f"{step_label}: {filename}", passed)
            if not passed:
                self._set_next("2", step_msg)

        # Step 3: Styles
        styles_ok = (self.ref_dir / "styles.json").is_file() and (
            self.ref_dir / "design-bundles.json"
        ).is_file()
        self._check("Step 3: styles.json + design-bundles.json", styles_ok)
        if not styles_ok:
            self._set_next("2", "Read style-extraction.md \u2192 extract computed styles.")

        # Step 4: Responsive
        bp_ok = (self.ref_dir / "detected-breakpoints.json").is_file()
        self._check("Step 4: detected-breakpoints.json", bp_ok)
        if not bp_ok:
            self._set_next("2", "Read responsive-detection.md \u2192 sweep viewports.")

        sizing_ok = (self.ref_dir / "responsive" / "sizing-expressions.json").is_file()
        self._check("Step 4-C2: sizing-expressions.json", sizing_ok)
        if not sizing_ok:
            self._set_next(
                "2", "Read responsive-detection.md Step 4-C2 \u2192 multi-viewport element sizing."
            )

        # Step 5: Interactions
        inter_ok = (self.ref_dir / "interactions-detected.json").is_file()
        self._check("Step 5: interactions-detected.json", inter_ok)
        if not inter_ok:
            self._set_next("2", "Read interaction-detection.md \u2192 detect interactions.")

        # Step 5c: Bundles
        bundles_ok = _has_files(self.ref_dir / "bundles", "*.js", 1)
        self._check("Step 5c: bundles/ (\u22651 JS file)", bundles_ok)
        if not bundles_ok:
            self._set_next(
                "2", "Read bundle-analysis.md \u2192 download ALL JS chunks. Gate: bundle"
            )

        # Advisory: warn when <3 chunks
        if bundles_ok and not _has_files(self.ref_dir / "bundles", "*.js", 3):
            js_count = sum(1 for _ in (self.ref_dir / "bundles").rglob("*.js"))
            print(
                f"  {_YELLOW}\u26a0{_NC}  Only {js_count} JS chunk(s) \u2014 typical SPAs have \u22653."
            )

        sdks_ok = (self.ref_dir / "external-sdks.json").is_file()
        self._check("Step 5c: external-sdks.json", sdks_ok)
        if not sdks_ok:
            self._set_next(
                "2",
                "Read bundle-analysis.md \u2192 detect external SDKs. Write external-sdks.json.",
            )

        # Step 5d: Spec + hover artifacts
        spec_ok = (self.ref_dir / "transition-spec.json").is_file()
        self._check("Step 5d: transition-spec.json", spec_ok)
        if not spec_ok:
            self._set_next(
                "2",
                "Read bundle-analysis.md + transition-spec-rules.md \u2192 write transition-spec.json. Gate: spec",
            )

        hover_ok = (self.ref_dir / "hover-css-rules.json").is_file()
        self._check("Step 5d-2b: hover-css-rules.json", hover_ok)
        if not hover_ok:
            self._set_next(
                "2", "Read interaction-detection.md Step 5d-2b \u2192 extract ALL :hover CSS rules."
            )

        # Step 6b: Assembled extraction
        extracted_ok = (self.ref_dir / "extracted.json").is_file()
        self._check("Step 6b: extracted.json (assembled)", extracted_ok)
        if not extracted_ok:
            self._set_next("2", "Assemble extracted.json from all artifacts.")

        # Staleness check
        if extracted_ok:
            stale_issues = _dag.check_staleness(self.ref_dir)
            stale_parents = [i.because_of for i in stale_issues if i.stale == "extracted.json"]
            if stale_parents:
                print(
                    f"  {_YELLOW}\u26a0{_NC}  extracted.json is STALE \u2014 changed after assembly: {' '.join(stale_parents)}"
                )
                print("     Re-run Step 6b (assemble) before generating code.")

        # Step 6c: Section audit
        cmap_ok = (self.ref_dir / "component-map.json").is_file()
        self._check("Step 6c: component-map.json (section audit)", cmap_ok)
        if not cmap_ok:
            self._set_next(
                "2",
                "Read section-audit.md \u2192 six-stage audit \u2192 component-map.json. Gate: pre-generate",
            )

        print()
        return result

    def check_pre_generate_gate(self) -> bool:
        """Run pre-generate gate. Returns True if passed."""
        if self.next_phase or not self.ref_dir.is_dir():
            return False

        print(f"{_BOLD}Pre-generate gate (auto){_NC}")
        gate = Gate(self.ref_dir)
        exit_code = gate.run("pre-generate")
        if exit_code != 0:
            self._set_next(
                "2", "Pre-generate gate FAILED. Fix missing artifacts before code generation."
            )
            print()
            return False
        print()
        return True

    def check_phase_3(self) -> PhaseResult:
        """Phase 3: Generation check."""
        result = PhaseResult(name="3", title="Generation")
        print(f"{_BOLD}Phase 3 \u2014 Generation{_NC}")

        app_dir = _find_app_dir(self.project_root, self.component)
        if app_dir is not None:
            comp_count = _count_tsx_files(app_dir)
            min_comp = int(os.environ.get("MIN_COMPONENT_COUNT", "1"))
            passed = comp_count >= min_comp
            self._check(f"Components generated ({comp_count} .tsx files)", passed)
            if not passed:
                self._set_next(
                    "3",
                    "Read component-generation.md \u2192 generate from extracted.json. Gate: pre-generate",
                )
                result.next_step = "Generate components from extracted.json"
            # Monorepo fallback warning
            if app_dir != self.project_root and (self.project_root / "apps").is_dir():
                # Check if it's the first-match fallback
                comp_dir = self.project_root / "apps" / self.component
                if not comp_dir.is_dir():
                    print(
                        f"  {_YELLOW}\u26a0{_NC}  Monorepo: using first app dir found. Set CLAUDE_PROJECT_DIR to target workspace."
                    )
        else:
            print(f"  {_YELLOW}\u25cb{_NC} No app directory found")
            self._set_next("3", "Scaffold app, then read component-generation.md.")

        print()
        return result

    def check_phase_4(self) -> PhaseResult:
        """Phase 4: Verification check."""
        result = PhaseResult(name="4", title="Verification")
        print(f"{_BOLD}Phase 4 \u2014 Verification{_NC}")

        impl_ok = _has_files(self.ref_dir / "static" / "impl", "*.png", 5)
        self._check("impl screenshots captured", impl_ok)

        diff_ok = _has_files(self.ref_dir / "static" / "diff", "*.png", 1)
        self._check("diff images generated", diff_ok)

        if not self.next_phase:
            self._set_next("4", "Run scripts/verify/auto-verify.sh. Gate: post-implement")

        print()
        return result

    def run(self, json_output: bool = False) -> int:
        """Run full pipeline status check.

        Returns 0 on success, 1 on missing dependencies.
        """
        # Dependency check
        missing = _check_dependencies()
        if missing:
            print(f"{_RED}Missing required tools:{_NC}")
            for m in missing:
                print(f"  {_RED}\u2717{_NC} {m}")
            print("  brew install imagemagick ffmpeg dssim && npm i -g agent-browser")
            return 1

        # Pipeline state header
        state = PipelineState.load(self.ref_dir)
        total_gates = len(GATE_ORDER)
        completed = len(state.completed_steps)
        print(f"{_BOLD}\u2501\u2501\u2501 Pipeline State \u2501\u2501\u2501{_NC}")
        print(f"  Component  : {state.component or self.component}")
        print(f"  Progress   : {completed}/{total_gates} gates completed")
        if state.current_gate == "done":
            print("  Current    : \u2705 ALL GATES COMPLETE")
        else:
            print(f"  Current    : {state.current_gate}")
        if state.last_updated:
            print(f"  Updated    : {state.last_updated}")
        print(
            f"{_BOLD}\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501{_NC}"
        )
        print()

        # Short-circuit if all gates done
        if state.current_gate == "done":
            print(
                "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
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
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
        )
        if self.next_phase:
            print(f"{_BOLD}NEXT: Phase {self.next_phase}{_NC}")
            print(f"{_YELLOW}\u2192 {self.next_step}{_NC}")

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


    # ── run action: deterministic Phase 0A-2 executor ──
    #
    # Codex review v0.8 → v1.0: hook policy closed the negative space
    # (ad-hoc artifact writes denied), but the natural-prompt nested agent
    # got stuck in Phase 1 because hooks can't force forward progress.
    # This driver does the forward push: each phase invokes the canonical
    # script, then runs the existing check_phase_* validators to confirm
    # the artifacts appeared. On any failure, abort with a clear message
    # — no silent fallback to ad-hoc dumping.
    #
    # Initial coverage is Phase 0A → 1 → 2 (Codex's 4-hour scope).
    # Phase 3+ stays under check-only / SKILL.md guidance for now;
    # extending coverage is `--phases 3-5` follow-up work.

    def execute_phases(self, phases: tuple[str, ...] = ("0A", "1", "2")) -> int:
        """Run the canonical scripts for each named phase, validate, repeat.

        Returns 0 on success (all requested phases pass their check_phase_*
        validator), non-zero on the first phase that fails. Each phase
        invocation prints a header so the operator (or the wrapping nested
        agent) can see exactly which step blocked.
        """
        import subprocess

        # Resolve $PLUGIN_ROOT for the wrapper scripts. Mirrors the cascade
        # used by hooks/shim.sh so the run command works both inside the
        # plugin checkout and from a fresh top-level folder where the
        # plugin is installed elsewhere.
        plugin_root = os.environ.get("PLUGIN_ROOT") or os.environ.get(
            "CLAUDE_PLUGIN_ROOT"
        ) or os.environ.get("CODEX_PLUGIN_ROOT")
        if not plugin_root or not (Path(plugin_root) / "scripts" / "extract").is_dir():
            # Fallback: pipeline.py lives in ui_clone/, plugin root is two up.
            plugin_root = str(Path(__file__).resolve().parent.parent)
        scripts = Path(plugin_root) / "scripts" / "extract"
        visual_scripts = Path(plugin_root) / "skills" / "visual-debug" / "scripts"

        # Ensure ref dir exists for downstream artifact targets.
        self.ref_dir.mkdir(parents=True, exist_ok=True)

        def _run(cmd: list[str], label: str) -> bool:
            print(f"\n{_BOLD}== execute: {label}{_NC}")
            print(f"  $ {' '.join(cmd)}")
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                print(f"  {_RED}✗{_NC} {label} failed: {exc}")
                return False
            if result.returncode != 0:
                print(f"  {_RED}✗{_NC} {label} exit {result.returncode}")
                if result.stderr.strip():
                    print(f"  stderr:\n{result.stderr.rstrip()}")
                return False
            if result.stdout.strip():
                print(result.stdout.rstrip())
            return True

        for phase in phases:
            if phase == "0A":
                # Phase 0A is a pure detection that the status checker
                # already runs lazily; no driver work required here. Just
                # validate.
                # check_phase_0a is invoked for its side effect — it
                # writes canvas-webgl-detection.json on first call. The
                # return value is unused here; we validate by checking
                # the artefact below.
                self.check_phase_0a()
                if not (self.ref_dir / "canvas-webgl-detection.json").is_file():
                    print(
                        f"\n{_RED}Phase 0A failed: canvas-webgl-detection.json absent.{_NC}"
                    )
                    return 1
                continue

            if phase == "1":
                capture = scripts / "capture.sh"
                if not capture.is_file():
                    print(f"\n{_RED}Phase 1 failed: capture.sh not found at {capture}{_NC}")
                    return 1
                if not _run(
                    ["bash", str(capture), self.url, self.session, str(self.ref_dir)],
                    "Phase 1 — reference capture",
                ):
                    return 1
                has_ref = (self.ref_dir / "regions.json").is_file()
                self.check_phase_1()
                if not has_ref:
                    print(
                        f"\n{_RED}Phase 1 failed: regions.json missing after capture.{_NC}"
                    )
                    return 1
                continue

            if phase == "2":
                # Phase 2 covers DOM extraction (extract-dom.sh + scaffold)
                # and asset/style extraction. The two known invocations
                # come from skills/visual-debug/scripts/.
                extract_dom = visual_scripts / "extract-dom.sh"
                scaffold = visual_scripts / "dom-scaffold.sh"
                # extract-dom.sh and dom-scaffold.sh are agent-browser
                # invocations; both target the ref dir. They are run
                # sequentially; the second consumes the first's outputs.
                if extract_dom.is_file() and not _run(
                    ["bash", str(extract_dom), self.session, str(self.ref_dir)],
                    "Phase 2 — DOM extraction",
                ):
                    return 1
                if scaffold.is_file() and not _run(
                    ["bash", str(scaffold), str(self.ref_dir)],
                    "Phase 2 — DOM scaffold",
                ):
                    return 1
                # Validate the artifact gate.
                has_ref = (self.ref_dir / "regions.json").is_file()
                self.check_phase_2(has_ref)
                if not (self.ref_dir / "section-map.json").is_file():
                    print(
                        f"\n{_RED}Phase 2 failed: section-map.json missing.{_NC}"
                    )
                    return 1
                continue

            print(
                f"\n{_YELLOW}Phase {phase} not yet supported by the run driver. "
                f"Use status to see the next step.{_NC}"
            )
            return 1

        print(f"\n{_GREEN}{_BOLD}run: requested phases complete: {','.join(phases)}{_NC}")
        return 0


    # ── verify action: drive post-generation gates ─────────────────────────
    #
    # Codex structural review (loop-3/4 convergence trigger): the run driver
    # stops after Phase 2 and Phase 3+ verification gates are advisory only,
    # which lets nested agents claim "완료" while the impl/ silently fails
    # boundary / font-parity / section-compare. `verify` closes the loop:
    # one canonical entry that drives every post-impl gate in GATE_ORDER and
    # returns non-zero on the first failure so the nested agent (and the
    # autonomous outer loop) can react.
    #
    # The gates themselves live in ui_clone.gate; we shell out so the gate
    # module's argparse + exit codes stay the source of truth.
    def execute_verify(self) -> int:
        import subprocess
        # GATES_POST_IMPL is the suffix of state.GATE_ORDER that requires
        # the impl/ directory to exist. Kept inline (not lifted into
        # state.py) until a second caller appears.
        gates_post_impl = (
            "post-implement",
            "boundary",
            "font-parity",
            "section-compare",
        )
        impl_dir = Path.cwd() / "impl"
        if not impl_dir.is_dir():
            print(
                f"{_RED}verify: impl/ not found at {impl_dir}. "
                f"Generate components first, then re-run verify.{_NC}"
            )
            return 1

        failures: list[str] = []
        for gate_name in gates_post_impl:
            print(f"\n{_BOLD}== verify: gate {gate_name}{_NC}")
            result = subprocess.run(
                [sys.executable, "-m", "ui_clone.gate", str(self.ref_dir), gate_name],
                capture_output=False,  # stream gate output to operator
            )
            if result.returncode != 0:
                failures.append(gate_name)
                print(
                    f"  {_RED}✗{_NC} {gate_name} exit {result.returncode} "
                    f"— continuing to surface every failure rather than short-circuit"
                )
        if failures:
            print(
                f"\n{_RED}{_BOLD}verify: {len(failures)} gate(s) failed: "
                f"{', '.join(failures)}{_NC}"
            )
            return 1
        print(f"\n{_GREEN}{_BOLD}verify: all post-impl gates passed{_NC}")
        return 0


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
