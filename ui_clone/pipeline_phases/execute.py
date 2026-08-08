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

import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from ui_clone.capture_readiness import score_capture
from ui_clone.hooks._common import BOLD as _BOLD
from ui_clone.hooks._common import GREEN as _GREEN
from ui_clone.hooks._common import NC as _NC
from ui_clone.hooks._common import RED as _RED
from ui_clone.hooks._common import YELLOW as _YELLOW
from ui_clone.pipeline_logs import (
    completed_process_output,
    echo_success_output,
    log_tail_lines,
    tail_text,
    timeout_output,
    write_process_log,
)
from ui_clone.shell import bash_bin as _bash_bin
from ui_clone.shell import bash_env as _bash_env


def _resolve_impl_root(
    plugin_root: str,
    cwd: Path,
    env: Mapping[str, str],
    existing_state_root: str = "",
    ref_dir: Path | None = None,
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

    def _is_cross_scratch_impl(path: Path) -> bool:
        if ref_dir is None:
            return False
        if not (ref_dir.parent.name == "ref" and ref_dir.parent.parent.name == "tmp"):
            return False
        ref_name = ref_dir.name
        scratch_root = ref_dir.parent.parent.parent / "scratch"
        try:
            rel = path.resolve().relative_to(scratch_root.resolve())
        except (OSError, ValueError):
            return False
        slot = rel.parts[0] if rel.parts else ""
        same_run = (
            slot == ref_name
            or slot.startswith(f"{ref_name}-")
            or slot.startswith(f"{ref_name}_")
            or slot.startswith(f"{ref_name}.")
        )
        return bool(slot and not same_run)

    def _backlink_mismatch(path: Path) -> bool:
        # D4 (loop-nvti-0): an impl tree whose `.ref-dir` backlink resolves to
        # a DIFFERENT ref dir is another site's run — adopting it made
        # state-coverage PASS against stale leftovers (false positive) and let
        # the new run clobber that tree. No backlink = legacy tree, keep the
        # old adoption (single-shot back-compat).
        if ref_dir is None:
            return False
        backlink = path / ".ref-dir"
        if not backlink.is_file():
            return False
        try:
            lines = backlink.read_text(encoding="utf-8").strip().splitlines()
            recorded = lines[0].strip() if lines else ""
        except OSError:
            return False
        if not recorded:
            return False
        try:
            return Path(recorded).resolve() != ref_dir.resolve()
        except OSError:
            return False

    if existing_state_root and _is_cross_scratch_impl(Path(existing_state_root)):
        existing_state_root = ""
    if existing_state_root:
        return existing_state_root
    cwd = Path(cwd)
    candidates = [cwd / "impl"]
    # Only consider the plugin/repo-root impl/ when cwd IS that root
    # (normal single-shot use); from a loop dir it must be off-limits.
    if Path(plugin_root).resolve() == cwd.resolve():
        candidates.append(Path(plugin_root) / "impl")
    for cand in candidates:
        if (
            cand.is_dir()
            and (cand / "package.json").is_file()
            and not _is_cross_scratch_impl(cand)
            and not _backlink_mismatch(cand)
        ):
            return str(cand.resolve())
    default_impl = cwd / "impl"
    if ref_dir is not None and (
        _is_cross_scratch_impl(default_impl) or _backlink_mismatch(default_impl)
    ):
        return str((cwd / "scratch" / ref_dir.name).resolve())
    return str(default_impl.resolve())

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
        plugin_root,
        Path.cwd(),
        os.environ,
        _state.impl_root or "",
        pipeline.ref_dir,
    )
    if _state.impl_root != impl_root_resolved:
        _state.impl_root = impl_root_resolved
        try:
            _state.save(pipeline.ref_dir)
            # Also write the bare marker file the resolver checks.
            (pipeline.ref_dir / ".impl-root").write_text(
                impl_root_resolved + "\n", encoding="utf-8",
            )
            # Mutual handshake: the impl dir vouches for this ref dir so
            # find-impl-root.sh trusts the marker even when the impl lives
            # under scratch/ with an unrelated slot name. Stale markers
            # (copied ref dirs) fail the handshake because the old impl's
            # backlink points at its own ref dir.
            _impl_p = Path(impl_root_resolved)
            if _impl_p.is_dir():
                (_impl_p / ".ref-dir").write_text(
                    str(pipeline.ref_dir.resolve()) + "\n", encoding="utf-8",
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
        if cmd and cmd[0] == "bash":
            cmd = [_bash_bin(), *cmd[1:]]
        print(f"\n{_BOLD}== execute: {label}{_NC}")
        print(f"  $ {' '.join(cmd)}")
        tail_limit = log_tail_lines()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                env=_bash_env(),
            )
        except FileNotFoundError as exc:
            print(f"  {_RED}✗{_NC} {label} failed: {exc}")
            return False
        except subprocess.TimeoutExpired as exc:
            output = timeout_output(exc)
            log_path = write_process_log(
                pipeline.ref_dir,
                "run",
                label,
                output,
                command=cmd,
                exit_code="timeout",
            )
            print(f"  {_RED}✗{_NC} {label} timed out — log → {log_path}")
            tail = tail_text(output, tail_limit)
            if tail:
                print(f"  last {min(tail_limit, len(tail.splitlines()))} log line(s):\n{tail}")
            return False

        output = completed_process_output(result)
        log_path = write_process_log(
            pipeline.ref_dir,
            "run",
            label,
            output,
            command=cmd,
            exit_code=result.returncode,
        )
        if result.returncode != 0:
            print(f"  {_RED}✗{_NC} {label} exit {result.returncode} — log → {log_path}")
            tail = tail_text(output, tail_limit)
            if tail:
                print(f"  last {min(tail_limit, len(tail.splitlines()))} log line(s):\n{tail}")
            return False
        if output.strip():
            print(f"  {_GREEN}✓{_NC} output saved → {log_path}")
            if echo_success_output():
                tail = tail_text(output, tail_limit)
                if tail:
                    print(f"  success output tail:\n{tail}")
        return True

    def _run_gate(gate_name: str) -> bool:
        """Run the canonical gate immediately after its producer phase.

        `pipeline ... run --phases 0A,1,2` is the deterministic producer for
        early reference/extraction artifacts. The pipeline-state cursor must
        advance at the same time; otherwise outer loop drivers can produce a
        full artifact tree while `pipeline-state.json` remains at `reference`,
        making later `verify` runs look out-of-order. Keep this in-process so
        the same Gate implementation remains the source of truth.
        """
        from ui_clone.gate import Gate

        print(f"\n{_BOLD}== execute: gate {gate_name}{_NC}")
        return Gate(pipeline.ref_dir).run(gate_name) == 0

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
            if not _run_gate("reference"):
                print(
                    f"\n{_RED}Phase 1 failed: reference gate did not pass after capture.{_NC}"
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
            extract_asset_metadata = scripts / "extract-asset-metadata.sh"
            resource_mirror = scripts / "resource-mirror.sh"
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
            # animation-runtime-dump.json: framer-motion / rAF scroll-scrub
            # timing (scrollLinkedStyles) + other runtime animation params.
            # NON-FATAL by design: the dump distinguishes "looked, found none"
            # (null fields) from "never looked" (no file), so extraction must
            # ALWAYS look — but a site with no JS motion, or a transient agent-
            # browser hiccup, must not sink the whole run. Enforcement that the
            # captured curve is actually bound lives in the gate layer, not
            # here. Runs against the same loaded session; the script restores
            # scroll after its sweep, so it cannot corrupt later captures.
            extract_anim_runtime = scripts / "extract-animation-runtime.sh"
            if extract_anim_runtime.is_file():
                _run(
                    [
                        "bash",
                        str(extract_anim_runtime),
                        pipeline.session,
                        str(pipeline.ref_dir),
                    ],
                    "Phase 2 — animation runtime capture (scroll-scrub / framer)",
                )
            # container-context.json: CSS container-query context inventory
            # (container-type ancestors + their resolved widths). NON-FATAL,
            # same rationale as animation-runtime above — a container the
            # transpiler drops or reproduces at the wrong width silently breaks
            # every @container utility beneath it (the "recognizable but ~15%
            # off" failure on Tailwind SPAs). container-context-check.sh diffs
            # the impl against this inventory at verify time.
            extract_container_ctx = scripts / "extract-container-context.sh"
            if extract_container_ctx.is_file():
                _run(
                    [
                        "bash",
                        str(extract_container_ctx),
                        str(pipeline.ref_dir),
                        pipeline.session,
                    ],
                    "Phase 2 — container-query context capture",
                )
            # responsive/sizing-expressions.json: multi-viewport sizing sweep.
            # NON-FATAL, and placed LAST among the browser-reading steps because
            # it resizes the session (768/1280/1440) to recover the CSS
            # expressions (calc/vw/linear/breakpoint) that a single-viewport
            # capture flattens to desktop-frozen px. responsive-sweep.sh is
            # deterministic; without it responsive/sizing-expressions.json stays
            # the single-viewport sentinel the pre-generate gate rejects and the
            # clone renders desktop-frozen. Everything after this (styles,
            # scaffold) reads artifacts off disk, so the resize cannot corrupt
            # an earlier capture.
            responsive_sweep = scripts / "responsive-sweep.sh"
            if responsive_sweep.is_file():
                _run(
                    [
                        "bash",
                        str(responsive_sweep),
                        pipeline.url,
                        str(pipeline.ref_dir),
                        "--session",
                        pipeline.session,
                    ],
                    "Phase 2 — responsive multi-viewport sizing sweep",
                )
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
            if not extract_asset_metadata.is_file():
                resumable = (
                    f"bash {extract_asset_metadata} {pipeline.session} "
                    f"{pipeline.ref_dir} {pipeline.url}"
                )
                print(
                    f"\n{_RED}Phase 2 failed: extract-asset-metadata.sh not found at "
                    f"{extract_asset_metadata}.{_NC}"
                )
                print(f"  Resume Step 2.5 with: {resumable}")
                return 1
            if not _run(
                [
                    "bash",
                    str(extract_asset_metadata),
                    pipeline.session,
                    str(pipeline.ref_dir),
                    pipeline.url,
                ],
                "Phase 2.5 — asset metadata extraction",
            ):
                has_ref = (pipeline.ref_dir / "regions.json").is_file()
                pipeline.next_phase = ""
                pipeline.next_step = ""
                pipeline.check_phase_2(has_ref)
                print(
                    f"\n{_RED}Phase 2 failed: Step 2.5 asset metadata extraction did not complete.{_NC}"
                )
                print(
                    "  Resume with: "
                    f"bash {extract_asset_metadata} {pipeline.session} "
                    f"{pipeline.ref_dir} {pipeline.url}"
                )
                return 1
            # Capture-readiness recovery (loop-claude-ebay root cause).
            # extract-dom.sh snapshotted the reused session with no readiness gate,
            # so a transient pre-settle / error frame can yield a structure.json
            # missing the main content region — while extract-asset-metadata (same
            # session, just now) recorded the images the page actually rendered.
            # When a majority of those rendered images are absent from the DOM
            # snapshot, re-run extract-dom + section-map + styles against the
            # now-settled session (NO reload: the content is already in the session
            # DOM, proven by the images we just recorded, and a reload would discard
            # splash/cookie/scroll state established by earlier steps). Bounded
            # retries; the verdict is written to capture-readiness.json either way,
            # so an unrecoverable capture is honestly marked degraded rather than
            # silently shipping the impoverished DOM. Non-fatal by design.
            _MAX_RESNAPSHOT = 2
            readiness = score_capture(pipeline.ref_dir)
            _resnapshots = 0
            while readiness.get("needsResnapshot") and _resnapshots < _MAX_RESNAPSHOT:
                _resnapshots += 1
                print(
                    f"\n{_YELLOW}Capture-readiness: {readiness['orphanImages']}/"
                    f"{readiness['checkableImages']} rendered images missing from the "
                    f"DOM snapshot (a pre-settle/error frame). Re-snapshotting the "
                    f"settled session (attempt {_resnapshots}/{_MAX_RESNAPSHOT}).{_NC}"
                )
                _run(
                    ["bash", str(extract_dom), str(pipeline.ref_dir), pipeline.session, "body"],
                    "Phase 2 — DOM re-snapshot (capture-readiness recovery)",
                )
                if extract_section_map.is_file():
                    _run(
                        ["bash", str(extract_section_map), str(pipeline.ref_dir), pipeline.session],
                        "Phase 2 — section-map re-enumeration (capture-readiness recovery)",
                    )
                if extract_styles.is_file():
                    _run(
                        ["bash", str(extract_styles), str(pipeline.ref_dir)],
                        "Phase 2 — styles re-aggregation (capture-readiness recovery)",
                    )
                readiness = score_capture(pipeline.ref_dir)
            readiness["resnapshotAttempts"] = _resnapshots
            try:
                (pipeline.ref_dir / "capture-readiness.json").write_text(
                    json.dumps(readiness, indent=2) + "\n", encoding="utf-8"
                )
            except OSError as exc:
                print(f"{_YELLOW}Capture-readiness: could not write marker: {exc}{_NC}")
            if readiness.get("status") == "degraded":
                print(
                    f"{_YELLOW}Capture-readiness: still degraded after {_resnapshots} "
                    f"re-snapshot(s) ({readiness['orphanImages']}/"
                    f"{readiness['checkableImages']} images missing); structure.json is a "
                    f"fallback frame. See capture-readiness.json.{_NC}"
                )
            elif _resnapshots:
                print(
                    f"{_GREEN}Capture-readiness: recovered after {_resnapshots} "
                    f"re-snapshot(s) ({readiness['orphanImages']}/"
                    f"{readiness['checkableImages']} images now missing).{_NC}"
                )
            if resource_mirror.is_file() and not _run(
                [
                    "bash",
                    str(resource_mirror),
                    pipeline.session,
                    str(pipeline.ref_dir),
                    pipeline.url,
                ],
                "Phase 2.5b — browser resource mirror",
            ):
                mirror_required = (
                    os.environ.get("UI_CLONE_RESOURCE_MIRROR_REQUIRED", "")
                    .strip()
                    .lower()
                    not in {"", "0", "false", "no", "off"}
                )
                has_ref = (pipeline.ref_dir / "regions.json").is_file()
                pipeline.next_phase = ""
                pipeline.next_step = ""
                pipeline.check_phase_2(has_ref)
                if mirror_required:
                    print(
                        f"\n{_RED}Phase 2 failed: Step 2.5b resource mirror is required "
                        f"and did not complete.{_NC}"
                    )
                    print(
                        "  Resume with: "
                        f"UI_CLONE_RESOURCE_MIRROR_REQUIRED=1 bash {resource_mirror} "
                        f"{pipeline.session} {pipeline.ref_dir} {pipeline.url}"
                    )
                    return 1
                print(
                    f"\n{_YELLOW}Phase 2 advisory: Step 2.5b resource mirror did not "
                    f"complete, continuing because UI_CLONE_RESOURCE_MIRROR_REQUIRED "
                    f"is not set.{_NC}"
                )
                print(
                    "  Optional recovery evidence: "
                    f"bash {resource_mirror} {pipeline.session} "
                    f"{pipeline.ref_dir} {pipeline.url}"
                )
            if scaffold.is_file() and not _run(
                ["bash", str(scaffold), str(pipeline.ref_dir)],
                "Phase 2 — DOM scaffold",
            ):
                return 1
            try:
                from ui_clone.extraction_artifacts import finalize_full_extraction_artifacts

                actions = finalize_full_extraction_artifacts(pipeline.ref_dir)
                if actions:
                    print(f"  extraction finalizer: {len(actions)} artifact(s) updated")
            except Exception as exc:
                print(f"  {_YELLOW}⚠{_NC} extraction finalizer skipped: {exc}")
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
            # Deterministic bundle-parameter extraction (Lenis/GSAP/Framer
            # scroll-scrub tables, library presence). Producer-only and
            # best-effort: the wrapper SKIPs (exit 0) when no bundles/ exists,
            # and a non-zero parse is non-fatal because the Phase-5d
            # bundle-analyzer LLM fallback still covers the gaps the regex parser
            # flags as unresolved. Running it here means bundle-extraction.json
            # is produced WITHOUT dispatching a subagent whenever bundles are
            # present. Optional-on-file so fixtures lacking the wrapper are
            # unaffected.
            bundle_extraction = scripts / "bundle-extraction.sh"
            if bundle_extraction.is_file():
                bundle_ok = _run(
                    ["bash", str(bundle_extraction), str(pipeline.ref_dir)],
                    "Phase 2 — bundle parameter extraction (deterministic)",
                )
                # N3: the parser is best-effort (its gaps fall through to the
                # Phase-5d bundle-analyzer LLM), so a failure stays non-fatal — but
                # it must not be SILENT. The wrapper exits 0 on a no-bundles SKIP, so
                # a False return WITH bundles/ present means it actually errored on a
                # site that has bundles to parse. Emit a loud advisory and persist a
                # durable status artifact so Phase 5d has an explicit obligation to
                # cover the params the deterministic pass could not. NOT gate-required
                # (would regress minified-bundle sites onto the documented LLM path).
                if not bundle_ok and (pipeline.ref_dir / "bundles").is_dir():
                    advisory = (
                        "bundle parameter extraction did not complete — the Phase-5d "
                        "bundle-analyzer LLM must cover the scroll-scrub / Lenis / GSAP "
                        "/ Framer params the deterministic parser could not resolve."
                    )
                    print(f"\n{_YELLOW}⚠ {advisory}{_NC}")
                    try:
                        (pipeline.ref_dir / "bundle-extraction-status.json").write_text(
                            json.dumps(
                                {
                                    "completed": False,
                                    "writtenBy": "pipeline.execute",
                                    "advisory": advisory,
                                },
                                indent=2,
                            ),
                            encoding="utf-8",
                        )
                    except OSError:
                        pass
            # paid-features.json: static scan of the already-downloaded
            # bundles/ + css/ + fonts.json + head.json + external-sdks.json for
            # known paid web-font / SDK CDN hosts. Producer-only, no browser.
            # Its own header: it "runs BEFORE generation (Step 5c-c) … the
            # `paid-features` gate then fails until every entry has a decision"
            # — but it was never wired, so the gate hard-stopped every scripted
            # run with paid-features.json MISSING even on sites (like ebay) that
            # have zero paid fonts. Running it here writes the artifact: a
            # no-paid-font site auto-passes the gate; a site WITH paid fonts
            # still (correctly) blocks for a license decision — that block is
            # intended human-in-the-loop, not a driver stop. Exit 0 always;
            # optional-on-file so fixtures without the wrapper are unaffected.
            paid_features = visual_scripts / "paid-features-detect.sh"
            if paid_features.is_file():
                _run(
                    ["bash", str(paid_features), str(pipeline.ref_dir)],
                    "Phase 2 — paid-feature static scan",
                )
            # verification-plan.json: pure JSON synth (no browser) declaring
            # which classes of bug to verify on THIS site, derived from the
            # bundle/extraction artifacts just produced. The `spec` gate
            # requires it; its own header says the plan is "minted at Step 5d",
            # yet it was never wired, so the spec gate hard-stopped every
            # scripted run with verification-plan.json MISSING. Honors
            # UI_CLONE_VERIFY_TIER (default comprehensive) — no --tier passed so
            # existing tier behavior is preserved. Exit 0 always; non-fatal.
            verification_plan = visual_scripts / "verification-plan.sh"
            if verification_plan.is_file():
                _run(
                    ["bash", str(verification_plan), str(pipeline.ref_dir)],
                    "Phase 2 — verification-plan synthesis",
                )
            for gate_name in ("extraction", "bundle"):
                if not _run_gate(gate_name):
                    print(
                        f"\n{_RED}Phase 2 failed: {gate_name} gate did not pass after extraction.{_NC}"
                    )
                    return 1
            continue

        # Phase 3 — deterministic generation (OPT-IN). By default the run driver
        # still hands Phase 3+ to the SKILL.md/LLM generation path (unchanged
        # fall-through below). When UI_CLONE_DETERMINISTIC_GENERATE is set, run
        # the deterministic motion-aware codegen chain in canonical order:
        #   generation-plan.sh  -> tmp/ref/<c>/generation-plan.json
        #   scaffold-to-jsx.sh  -> impl/src/components/*.tsx (verbatim layout/text)
        #   emit-scroll-helpers.sh -> impl/src/lib/*.tsx (Lenis/scroll helpers from
        #                              the plan's REAL params)
        # These emitters already exist and read the extracted transition-spec /
        # generation-plan; they were only reachable from the LLM path before, so
        # the auto-research loop's clones were motion-dead at baseline. The flag
        # keeps every existing caller's behavior identical (default = unset).
        if phase == "3" and os.environ.get("UI_CLONE_DETERMINISTIC_GENERATE"):
            gen_plan = scripts / "generation-plan.sh"
            scaffold = visual_scripts / "scaffold-to-jsx.sh"
            emit_scroll = visual_scripts / "emit-scroll-helpers.sh"
            # The operator explicitly opted into deterministic generation, so a
            # missing emitter is a broken install — fail loudly rather than
            # silently skipping it and reporting success (mirrors phases 0A/1,
            # which abort when their script is absent).
            for label, script in (
                ("generation-plan.sh", gen_plan),
                ("scaffold-to-jsx.sh", scaffold),
                ("emit-scroll-helpers.sh", emit_scroll),
            ):
                if not script.is_file():
                    print(
                        f"\n{_RED}Phase 3: required emitter missing: {script}{_NC}\n"
                        f"  Deterministic generation cannot proceed without {label}."
                    )
                    return 1
            if not _run(
                ["bash", str(gen_plan), str(pipeline.ref_dir)],
                "phase 3: generation-plan",
            ):
                return 1
            if not _run(
                ["bash", str(scaffold), str(pipeline.ref_dir), impl_root_resolved],
                "phase 3: scaffold-to-jsx",
            ):
                return 1
            if not _run(
                ["bash", str(emit_scroll), str(pipeline.ref_dir), impl_root_resolved],
                "phase 3: emit-scroll-helpers",
            ):
                return 1
            # Deterministic Lottie slot mounts. Auxiliary like the font steps
            # below: a missing script or a non-zero exit is a warning, never a
            # blocker — a site with no Lottie entries is a valid no-op, and the
            # script writes its own report (lottie-mounts-emitted.json) plus
            # impl/src/generated/lottie-mounts.ts, which lottie-slot-identity-
            # check.sh verifies against the spec's slot->asset map.
            emit_lottie = scripts / "emit-lottie-mounts.sh"
            if not emit_lottie.is_file():
                print(
                    f"  {_YELLOW}⚠{_NC} phase 3: emit-lottie-mounts.sh not found — "
                    f"skipping (Lottie slots may be mis-mounted)"
                )
            elif not _run(
                ["bash", str(emit_lottie), str(pipeline.ref_dir), impl_root_resolved],
                "phase 3: emit-lottie-mounts",
            ):
                print(
                    f"  {_YELLOW}⚠{_NC} phase 3: emit-lottie-mounts reported an error "
                    f"(non-fatal — see lottie-mounts-emitted.json)"
                )
            # Deterministic motion-hook skeletons (scroll-scrub / state-machine /
            # swiper) from the spec's real params. Auxiliary/warning-only, same as
            # the Lottie step: a site with no such entries is a valid no-op, and
            # the script writes its own report (motion-skeletons-emitted.json) +
            # impl/src/generated/motion-skeletons.ts.
            emit_motion = scripts / "emit-motion-skeletons.sh"
            if not emit_motion.is_file():
                print(
                    f"  {_YELLOW}⚠{_NC} phase 3: emit-motion-skeletons.sh not found — "
                    f"skipping (scroll/swiper motion may be re-approximated)"
                )
            elif not _run(
                ["bash", str(emit_motion), str(pipeline.ref_dir), impl_root_resolved],
                "phase 3: emit-motion-skeletons",
            ):
                print(
                    f"  {_YELLOW}⚠{_NC} phase 3: emit-motion-skeletons reported an error "
                    f"(non-fatal — see motion-skeletons-emitted.json)"
                )
            # Deterministic font transfer + Preflight neutralization. Runs after
            # scaffold-to-jsx so impl/src exists for the @layer base injection.
            # Auxiliary asset steps: a missing script or a non-zero exit is a
            # warning, never a generation blocker — each writes its own report
            # artifact (font-transfer.json / preflight-neutralize.json) that the
            # font-binaries presence check consumes.
            for _label, _script in (
                ("transfer-fonts", scripts / "transfer-fonts.sh"),
                ("emit-preflight-neutralize", scripts / "emit-preflight-neutralize.sh"),
            ):
                if not _script.is_file():
                    print(
                        f"  {_YELLOW}⚠{_NC} phase 3: {_label}.sh not found — "
                        f"skipping (fonts may 404 / headings may lose weight)"
                    )
                    continue
                if not _run(
                    ["bash", str(_script), str(pipeline.ref_dir), impl_root_resolved],
                    f"phase 3: {_label}",
                ):
                    print(
                        f"  {_YELLOW}⚠{_NC} phase 3: {_label} reported an error "
                        f"(non-fatal — see its report artifact)"
                    )
            # Success gate: the chain must have actually emitted components, so a
            # phase-3 "success" can never mean "ran the scripts but produced
            # nothing" (e.g. an empty/garbage structure.json).
            tsx = [
                p for p in Path(impl_root_resolved).rglob("*.tsx")
                if "node_modules" not in p.parts
            ]
            if not tsx:
                print(
                    f"\n{_RED}Phase 3: no .tsx components were generated under "
                    f"{impl_root_resolved} — generation produced nothing.{_NC}"
                )
                return 1
            print(f"  {_GREEN}✓{_NC} phase 3: {len(tsx)} .tsx component file(s) generated")
            continue

        print(
            f"\n{_YELLOW}Phase {phase} not yet supported by the run driver. "
            f"Use status to see the next step.{_NC}"
        )
        return 1

    print(f"\n{_GREEN}{_BOLD}run: requested phases complete: {','.join(phases)}{_NC}")
    return 0
