"""check_phase_* function bodies, moved out of `Pipeline` methods.

Each function takes a `Pipeline` instance and uses its mutable attributes
(`next_phase`, `next_step`) plus its `_set_next` / `_check` helpers.
This is a physical move from `ui_clone.pipeline.Pipeline` — no logic
changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ui_clone import dag as _dag
from ui_clone.gate import Gate
from ui_clone.hooks._common import BOLD as _BOLD
from ui_clone.hooks._common import GREEN as _GREEN
from ui_clone.hooks._common import NC as _NC
from ui_clone.hooks._common import YELLOW as _YELLOW
from ui_clone.hooks._common import load_json_safe

if TYPE_CHECKING:
    from ui_clone.pipeline import Pipeline
    from ui_clone.pipeline_phases.types import PhaseResult


def check_phase_0a(pipeline: Pipeline) -> PhaseResult:
    """Phase 0A: Canvas/WebGL render type detection."""
    from ui_clone.pipeline_phases.types import PhaseCheck, PhaseResult

    result = PhaseResult(name="0A", title="Render Type Detection")
    print(f"{_BOLD}Phase 0A — Render Type Detection{_NC}")

    detect_path = pipeline.ref_dir / "canvas-webgl-detection.json"
    data = load_json_safe(detect_path)

    if data is not None:
        render_type = data.get("primaryRenderType", "unknown")
        has_canvas = data.get("hasCanvas", False)
        has_webgl = data.get("hasWebGL", False)
        print(
            f"  {_GREEN}✓{_NC} Render type: {render_type} (canvas={has_canvas}, webgl={has_webgl})"
        )
        result.checks.append(PhaseCheck("canvas-webgl-detection.json", True))

        if has_canvas or has_webgl:
            print(
                f"  {_YELLOW}⚠{_NC}  Canvas/WebGL detected — CSS replication will be APPROXIMATE."
            )
            print("       Read canvas-webgl-extraction.md before Phase 2 extraction.")
    else:
        print(
            f"  {_YELLOW}○{_NC} canvas-webgl-detection.json missing — run detection FIRST"
        )
        print(f"     agent-browser --session {pipeline.session} open {pipeline.url}")
        result.checks.append(PhaseCheck("canvas-webgl-detection.json", False))
        if not pipeline.ref_dir.is_dir():
            pipeline._set_next("0A", "Run canvas/WebGL detection, then re-run status.")
            result.next_step = "Run canvas/WebGL detection, then re-run status."

    print()
    return result


def check_phase_0(pipeline: Pipeline) -> PhaseResult:
    """Phase 0: Check for prior data."""
    from ui_clone.pipeline_phases.types import PhaseCheck, PhaseResult

    result = PhaseResult(name="0", title="Prior Data")
    print(f"{_BOLD}Phase 0 — Prior Data{_NC}")

    has_spec = (pipeline.ref_dir / "transition-spec.json").is_file()
    if has_spec:
        print(f"  {_GREEN}✓{_NC} transition-spec.json exists — READ THIS FIRST")
    else:
        print(f"  {_YELLOW}○{_NC} No prior transition-spec.json")

    has_extracted = (pipeline.ref_dir / "extracted.json").is_file()
    if has_extracted:
        print(f"  {_GREEN}✓{_NC} extracted.json exists")

    result.checks.append(PhaseCheck("transition-spec.json", has_spec))
    result.checks.append(PhaseCheck("extracted.json", has_extracted))
    print()
    return result


def check_phase_1(pipeline: Pipeline) -> PhaseResult:
    """Phase 1: Reference capture."""
    from ui_clone.pipeline import _has_files
    from ui_clone.pipeline_phases.types import PhaseResult

    result = PhaseResult(name="1", title="Reference Capture")
    print(f"{_BOLD}Phase 1 — Reference Capture{_NC}")

    has_ref = _has_files(pipeline.ref_dir / "static" / "ref", "*.png", 5)
    result.checks.append(pipeline._check("static/ref/ screenshots (≥5 files)", has_ref))
    result.checks.append(
        pipeline._check(
            "scroll-video/ref/ video",
            _has_files(pipeline.ref_dir / "scroll-video" / "ref", "*.webm", 1),
        )
    )
    result.checks.append(
        pipeline._check(
            "transitions/ref/ videos",
            _has_files(pipeline.ref_dir / "transitions" / "ref", "*.webm", 1),
        )
    )
    result.checks.append(
        pipeline._check("regions.json", (pipeline.ref_dir / "regions.json").is_file())
    )

    if not has_ref:
        pipeline._set_next("1", f"Invoke /ui-capture {pipeline.url}. See SKILL.md Phase 1.")
        result.next_step = f"Invoke /ui-capture {pipeline.url}. See SKILL.md Phase 1."
    print()
    return result


def check_phase_2(pipeline: Pipeline, has_ref: bool) -> PhaseResult:
    """Phase 2: Extraction checks."""
    from ui_clone.pipeline import _has_files
    from ui_clone.pipeline_phases.types import PhaseResult

    result = PhaseResult(name="2", title="Extraction")
    print(f"{_BOLD}Phase 2 — Extraction{_NC}")

    if not has_ref:
        print(f"  {_YELLOW}○{_NC} (skipped — complete Phase 1 first)")
        result.skipped = True
        result.skip_reason = "Complete Phase 1 first"
        print()
        return result

    extraction_steps: list[tuple[str, str, str]] = [
        (
            "structure.json",
            "section-map.json",
            "Read dom-extraction.md → run Step 2 (structure) + semantic section enumeration.",
        ),
        (
            "head.json",
            "fonts.json",
            "Read asset-extraction.md → extract head, assets, fonts.",
        ),
    ]
    for file_a, file_b, step_msg in extraction_steps:
        passed = (pipeline.ref_dir / file_a).is_file() and (pipeline.ref_dir / file_b).is_file()
        pipeline._check(f"{file_a} + {file_b}", passed)
        if not passed:
            pipeline._set_next("2", step_msg)

    single_file_steps: list[tuple[str, str, str]] = [
        (
            "svg-text-elements.json",
            "Step 2.5b",
            "Read dom-extraction.md Step 2.5b → SVG-as-text detection.",
        ),
        (
            "animation-init-styles.json",
            "Step 2.6",
            "Read dom-extraction.md Steps 2.6a-b → extract animation init styles.",
        ),
    ]
    for filename, step_label, step_msg in single_file_steps:
        passed = (pipeline.ref_dir / filename).is_file()
        pipeline._check(f"{step_label}: {filename}", passed)
        if not passed:
            pipeline._set_next("2", step_msg)

    # Step 3: Styles
    styles_ok = (pipeline.ref_dir / "styles.json").is_file() and (
        pipeline.ref_dir / "design-bundles.json"
    ).is_file()
    pipeline._check("Step 3: styles.json + design-bundles.json", styles_ok)
    if not styles_ok:
        pipeline._set_next("2", "Read style-extraction.md → extract computed styles.")

    # Step 4: Responsive
    bp_ok = (pipeline.ref_dir / "detected-breakpoints.json").is_file()
    pipeline._check("Step 4: detected-breakpoints.json", bp_ok)
    if not bp_ok:
        pipeline._set_next("2", "Read responsive-detection.md → sweep viewports.")

    sizing_ok = (pipeline.ref_dir / "responsive" / "sizing-expressions.json").is_file()
    pipeline._check("Step 4-C2: sizing-expressions.json", sizing_ok)
    if not sizing_ok:
        pipeline._set_next(
            "2", "Read responsive-detection.md Step 4-C2 → multi-viewport element sizing."
        )

    # Step 5: Interactions
    inter_ok = (pipeline.ref_dir / "interactions-detected.json").is_file()
    pipeline._check("Step 5: interactions-detected.json", inter_ok)
    if not inter_ok:
        pipeline._set_next("2", "Read interaction-detection.md → detect interactions.")

    # Step 5c: Bundles
    bundles_ok = _has_files(pipeline.ref_dir / "bundles", "*.js", 1)
    pipeline._check("Step 5c: bundles/ (≥1 JS file)", bundles_ok)
    if not bundles_ok:
        pipeline._set_next(
            "2", "Read bundle-analysis.md → download ALL JS chunks. Gate: bundle"
        )

    # Advisory: warn when <3 chunks
    if bundles_ok and not _has_files(pipeline.ref_dir / "bundles", "*.js", 3):
        js_count = sum(1 for _ in (pipeline.ref_dir / "bundles").rglob("*.js"))
        print(
            f"  {_YELLOW}⚠{_NC}  Only {js_count} JS chunk(s) — typical SPAs have ≥3."
        )

    sdks_ok = (pipeline.ref_dir / "external-sdks.json").is_file()
    pipeline._check("Step 5c: external-sdks.json", sdks_ok)
    if not sdks_ok:
        pipeline._set_next(
            "2",
            "Read bundle-analysis.md → detect external SDKs. Write external-sdks.json.",
        )

    # Step 5d: Spec + hover artifacts
    spec_ok = (pipeline.ref_dir / "transition-spec.json").is_file()
    pipeline._check("Step 5d: transition-spec.json", spec_ok)
    if not spec_ok:
        pipeline._set_next(
            "2",
            "Read bundle-analysis.md + transition-spec-rules.md → write transition-spec.json. Gate: spec",
        )

    hover_ok = (pipeline.ref_dir / "hover-css-rules.json").is_file()
    pipeline._check("Step 5d-2b: hover-css-rules.json", hover_ok)
    if not hover_ok:
        pipeline._set_next(
            "2", "Read interaction-detection.md Step 5d-2b → extract ALL :hover CSS rules."
        )

    # Step 6b: Assembled extraction
    extracted_ok = (pipeline.ref_dir / "extracted.json").is_file()
    pipeline._check("Step 6b: extracted.json (assembled)", extracted_ok)
    if not extracted_ok:
        pipeline._set_next("2", "Assemble extracted.json from all artifacts.")

    # Staleness check
    if extracted_ok:
        stale_issues = _dag.check_staleness(pipeline.ref_dir)
        stale_parents = [i.because_of for i in stale_issues if i.stale == "extracted.json"]
        if stale_parents:
            print(
                f"  {_YELLOW}⚠{_NC}  extracted.json is STALE — changed after assembly: {' '.join(stale_parents)}"
            )
            print("     Re-run Step 6b (assemble) before generating code.")

    # Step 6c: Section audit
    cmap_ok = (pipeline.ref_dir / "component-map.json").is_file()
    pipeline._check("Step 6c: component-map.json (section audit)", cmap_ok)
    if not cmap_ok:
        pipeline._set_next(
            "2",
            "Read section-audit.md → six-stage audit → component-map.json. Gate: pre-generate",
        )

    print()
    return result


def check_pre_generate_gate(pipeline: Pipeline) -> bool:
    """Run pre-generate gate. Returns True if passed."""
    if pipeline.next_phase or not pipeline.ref_dir.is_dir():
        return False

    print(f"{_BOLD}Pre-generate gate (auto){_NC}")
    gate = Gate(pipeline.ref_dir)
    exit_code = gate.run("pre-generate")
    if exit_code != 0:
        pipeline._set_next(
            "2", "Pre-generate gate FAILED. Fix missing artifacts before code generation."
        )
        print()
        return False
    print()
    return True


def check_phase_3(pipeline: Pipeline) -> PhaseResult:
    """Phase 3: Generation check."""
    import os

    from ui_clone.pipeline import _count_tsx_files, _find_app_dir
    from ui_clone.pipeline_phases.types import PhaseResult

    result = PhaseResult(name="3", title="Generation")
    print(f"{_BOLD}Phase 3 — Generation{_NC}")

    app_dir = _find_app_dir(pipeline.project_root, pipeline.component)
    if app_dir is not None:
        comp_count = _count_tsx_files(app_dir)
        min_comp = int(os.environ.get("MIN_COMPONENT_COUNT", "1"))
        passed = comp_count >= min_comp
        pipeline._check(f"Components generated ({comp_count} .tsx files)", passed)
        if not passed:
            pipeline._set_next(
                "3",
                "Read component-generation.md → generate from extracted.json. Gate: pre-generate",
            )
            result.next_step = "Generate components from extracted.json"
        # Monorepo fallback warning
        if app_dir != pipeline.project_root and (pipeline.project_root / "apps").is_dir():
            # Check if it's the first-match fallback
            comp_dir = pipeline.project_root / "apps" / pipeline.component
            if not comp_dir.is_dir():
                print(
                    f"  {_YELLOW}⚠{_NC}  Monorepo: using first app dir found. Set CLAUDE_PROJECT_DIR to target workspace."
                )
    else:
        print(f"  {_YELLOW}○{_NC} No app directory found")
        pipeline._set_next("3", "Scaffold app, then read component-generation.md.")

    print()
    return result


def check_phase_4(pipeline: Pipeline) -> PhaseResult:
    """Phase 4: Verification check."""
    from ui_clone.pipeline import _has_files
    from ui_clone.pipeline_phases.types import PhaseResult

    result = PhaseResult(name="4", title="Verification")
    print(f"{_BOLD}Phase 4 — Verification{_NC}")

    impl_ok = _has_files(pipeline.ref_dir / "static" / "impl", "*.png", 5)
    pipeline._check("impl screenshots captured", impl_ok)

    diff_ok = _has_files(pipeline.ref_dir / "static" / "diff", "*.png", 1)
    pipeline._check("diff images generated", diff_ok)

    if not pipeline.next_phase:
        pipeline._set_next("4", "Run scripts/verify/auto-verify.sh. Gate: post-implement")

    print()
    return result
