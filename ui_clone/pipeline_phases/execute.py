"""execute_phases — deterministic Phase 0A-2 executor.

Hook-policy hardening: hook policy closed the negative space
(ad-hoc artifact writes denied), but the natural-prompt nested agent
got stuck in Phase 1 because hooks can't force forward progress.
This driver does the forward push: each phase invokes the canonical
script, then runs the existing check_phase_* validators to confirm
the artifacts appeared. On any failure, abort with a clear message
— no silent fallback to ad-hoc dumping.

Initial coverage is Phase 0A → 1 → 2 (the initial deterministic scope).
Phase 3+ stays under check-only / SKILL.md guidance for now;
extending coverage is `--phases 3-5` follow-up work.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from ui_clone.hooks._common import BOLD as _BOLD
from ui_clone.hooks._common import GREEN as _GREEN
from ui_clone.hooks._common import NC as _NC
from ui_clone.hooks._common import RED as _RED
from ui_clone.hooks._common import YELLOW as _YELLOW


def _resolve_impl_root(
    plugin_root: str,
    cwd: Path,
    env: Mapping[str, str],
    existing_state_root: str = "",
) -> str:
    """Resolve where the implementation lives for THIS run. Always returns a
    path (never empty) so a marker can be written and the agent told where to
    scaffold.

    Priority: UI_CLONE_IMPL_ROOT env > existing pipeline-state.impl_root >
    an already-scaffolded impl/ > default <cwd>/impl.

    Crucially, a loop working dir (cwd != plugin_root) is anchored at
    <cwd>/impl and never adopts the plugin/repo-root impl/. That prevents the
    failure where a clone loop scaffolds into the repo root's impl/ and every
    round clobbers the same shared directory.
    """
    env_root = (env.get("UI_CLONE_IMPL_ROOT") or "").strip()
    if env_root:
        return str(Path(env_root).resolve())
    if existing_state_root:
        return existing_state_root
    cwd = Path(cwd)
    candidates = [cwd / "impl"]
    # Only consider the plugin/repo-root impl/ when cwd IS that root
    # (normal single-shot use); from a loop dir it must be off-limits.
    if Path(plugin_root).resolve() == cwd.resolve():
        candidates.append(Path(plugin_root) / "impl")
    for cand in candidates:
        if cand.is_dir() and (cand / "package.json").is_file():
            return str(cand.resolve())
    return str((cwd / "impl").resolve())

if TYPE_CHECKING:
    from ui_clone.pipeline import Pipeline


def execute_phases(pipeline: Pipeline, phases: tuple[str, ...] = ("0A", "1", "2")) -> int:
    """Run the canonical scripts for each named phase, validate, repeat.

    Returns 0 on success (all requested phases pass their check_phase_*
    validator), non-zero on the first phase that fails. Each phase
    invocation prints a header so the operator (or the wrapping nested
    agent) can see exactly which step blocked.
    """
    # Resolve $PLUGIN_ROOT for the wrapper scripts. Mirrors the cascade
    # used by hooks/shim.sh so the run command works both inside the
    # plugin checkout and from a fresh top-level folder where the
    # plugin is installed elsewhere.
    plugin_root = os.environ.get("PLUGIN_ROOT") or os.environ.get(
        "CLAUDE_PLUGIN_ROOT"
    ) or os.environ.get("CODEX_PLUGIN_ROOT")
    if not plugin_root or not (Path(plugin_root) / "scripts" / "extract").is_dir():
        # Fallback: pipeline.py lives in ui_clone/, plugin root is two up.
        plugin_root = str(Path(__file__).resolve().parent.parent.parent)
    scripts = Path(plugin_root) / "scripts" / "extract"
    visual_scripts = Path(plugin_root) / "skills" / "visual-debug" / "scripts"

    # Ensure ref dir exists for downstream artifact targets.
    pipeline.ref_dir.mkdir(parents=True, exist_ok=True)

    # Universal impl-root resolution (writes implRoot field to
    # pipeline-state.json so find-impl-root.sh can read it back
    # regardless of directory naming convention).
    # Priority: UI_CLONE_IMPL_ROOT env > existing state.impl_root >
    # canonical guess (<plugin_root>/impl OR <cwd>/impl).
    from ui_clone.state import PipelineState as _PS
    _state = _PS.load(pipeline.ref_dir)
    impl_root_resolved = _resolve_impl_root(
        plugin_root, Path.cwd(), os.environ, _state.impl_root or "",
    )
    if _state.impl_root != impl_root_resolved:
        _state.impl_root = impl_root_resolved
        try:
            _state.save(pipeline.ref_dir)
            # Also write the bare marker file the resolver checks.
            (pipeline.ref_dir / ".impl-root").write_text(
                impl_root_resolved + "\n", encoding="utf-8",
            )
        except OSError:
            pass
    # Tell the agent — in-band — exactly where to scaffold so it does not
    # default to the repo/plugin root it can see via --add-dir.
    print(
        f"{_BOLD}== impl root: {impl_root_resolved}{_NC}\n"
        "  Create and edit the implementation ONLY under this path "
        "(it is this loop's impl/, not the repository root)."
    )

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
            detect = visual_scripts / "canvas-webgl-detect.sh"
            if not detect.is_file():
                print(
                    f"\n{_RED}Phase 0A failed: canvas-webgl-detect.sh not found at {detect}{_NC}"
                )
                return 1
            if not _run(
                ["bash", str(detect), pipeline.url, pipeline.session, str(pipeline.ref_dir)],
                "Phase 0A — canvas/WebGL detection",
            ):
                return 1
            pipeline.check_phase_0a()
            if not (pipeline.ref_dir / "canvas-webgl-detection.json").is_file():
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
                ["bash", str(capture), pipeline.url, pipeline.session, str(pipeline.ref_dir)],
                "Phase 1 — reference capture",
            ):
                return 1
            has_ref = (pipeline.ref_dir / "regions.json").is_file()
            pipeline.check_phase_1()
            if not has_ref:
                print(
                    f"\n{_RED}Phase 1 failed: regions.json missing after capture.{_NC}"
                )
                return 1
            continue

        if phase == "2":
            # Phase 2 covers DOM extraction (extract-dom.sh + scaffold)
            # and asset/style extraction. dom-scaffold.sh consumes three
            # artifacts (structure.json + styles.json + section-map.json),
            # so Phase 2 produces all three before scaffolding.
            #
            # Fresh-capture diagnosis: extract-dom.sh wrote only
            # structure.json; the other two were documented in
            # skills/ui-reverse-engineering/dom-extraction.md as manual
            # agent-browser evals, which made the pipeline depend on
            # stale scratch dir artifacts for the section-map and styles.
            # Adding extract-section-map.sh + extract-styles.sh closes
            # that contract gap so fresh-only runs reach the scaffold.
            extract_dom = visual_scripts / "extract-dom.sh"
            extract_section_map = visual_scripts / "extract-section-map.sh"
            extract_styles = visual_scripts / "extract-styles.sh"
            scaffold = visual_scripts / "dom-scaffold.sh"
            if extract_dom.is_file() and not _run(
                ["bash", str(extract_dom), str(pipeline.ref_dir), pipeline.session, "body"],
                "Phase 2 — DOM extraction",
            ):
                return 1
            # section-map.json: agent-browser eval, runs against the
            # same session extract-dom.sh just used.
            if extract_section_map.is_file() and not _run(
                [
                    "bash",
                    str(extract_section_map),
                    str(pipeline.ref_dir),
                    pipeline.session,
                ],
                "Phase 2 — section-map enumeration",
            ):
                return 1
            # styles.json: aggregates from the structure.json we just
            # wrote, no browser round-trip.
            if extract_styles.is_file() and not _run(
                ["bash", str(extract_styles), str(pipeline.ref_dir)],
                "Phase 2 — styles aggregation",
            ):
                return 1
            required_for_scaffold = ["structure.json", "styles.json", "section-map.json"]
            missing_for_scaffold = [
                name for name in required_for_scaffold if not (pipeline.ref_dir / name).is_file()
            ]
            if missing_for_scaffold:
                has_ref = (pipeline.ref_dir / "regions.json").is_file()
                pipeline.next_phase = ""
                pipeline.next_step = ""
                pipeline.check_phase_2(has_ref)
                missing = ", ".join(missing_for_scaffold)
                print(
                    f"\n{_RED}Phase 2 failed: dom-scaffold inputs missing: {missing}.{_NC}"
                )
                return 1
            if scaffold.is_file() and not _run(
                ["bash", str(scaffold), str(pipeline.ref_dir)],
                "Phase 2 — DOM scaffold",
            ):
                return 1
            # Validate the artifact gate.
            has_ref = (pipeline.ref_dir / "regions.json").is_file()
            pipeline.next_phase = ""
            pipeline.next_step = ""
            pipeline.check_phase_2(has_ref)
            if pipeline.next_phase == "2":
                print(
                    f"\n{_RED}Phase 2 failed: extraction validator still reports missing artifacts.{_NC}"
                )
                if pipeline.next_step:
                    print(f"  Next: {pipeline.next_step}")
                return 1
            continue

        print(
            f"\n{_YELLOW}Phase {phase} not yet supported by the run driver. "
            f"Use status to see the next step.{_NC}"
        )
        return 1

    print(f"\n{_GREEN}{_BOLD}run: requested phases complete: {','.join(phases)}{_NC}")
    return 0
