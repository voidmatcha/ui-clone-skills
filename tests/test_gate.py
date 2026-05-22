import json
import os
from pathlib import Path

import pytest

from ui_clone import state as _state
from ui_clone.gate import VALID_GATES, Gate


def test_dispatch_matches_gate_order(tmp_path: Path) -> None:
    """_make_dispatch() must return exactly the gates declared in state.GATE_ORDER.

    state.GATE_ORDER is the single source of truth. dispatch is auto-derived
    via getattr; this test guards against accidental method-name typos / missing
    methods that the import-time validator already catches but is cheap to
    re-assert at the unit-test layer."""
    gate = Gate(tmp_path)
    assert list(gate._make_dispatch().keys()) == list(_state.GATE_ORDER)


def test_valid_gates_derives_from_gate_order() -> None:
    """VALID_GATES must equal GATE_ORDER + ['all'] — no manual list to drift."""
    assert VALID_GATES == list(_state.GATE_ORDER) + ["all"]


# ── check_file ──


def test_check_file_pass(ref_dir_with_artifacts: Path) -> None:
    gate = Gate(ref_dir_with_artifacts)
    result = gate.check_file(ref_dir_with_artifacts / "structure.json", "structure.json")
    assert result.status == "pass"


def test_check_file_missing(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    gate = Gate(ref)
    result = gate.check_file(ref / "missing.json", "missing.json")
    assert result.status == "fail"
    assert "MISSING" in result.message


def test_check_file_empty(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    empty = ref / "empty.json"
    empty.write_bytes(b"")
    gate = Gate(ref)
    result = gate.check_file(empty, "empty.json")
    assert result.status == "fail"
    assert "empty" in result.message.lower()


def test_check_dir_pass(ref_dir_with_artifacts: Path) -> None:
    gate = Gate(ref_dir_with_artifacts)
    result = gate.check_dir(ref_dir_with_artifacts / "static" / "ref", "screenshots", min_files=5)
    assert result.status == "pass"


def test_check_dir_missing(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    gate = Gate(ref)
    result = gate.check_dir(ref / "nonexistent", "dir", min_files=1)
    assert result.status == "fail"


def test_check_dir_too_few_files(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    d = ref / "screenshots"
    d.mkdir()
    (d / "only_one.png").write_bytes(b"PNG")
    gate = Gate(ref)
    result = gate.check_dir(d, "screenshots", min_files=5)
    assert result.status == "fail"
    assert "1" in result.message


def test_check_json_key_pass(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    f = ref / "extracted.json"
    f.write_text(json.dumps({"sections": [], "url": "https://example.com"}))
    gate = Gate(ref)
    result = gate.check_json_key(f, "sections", "extracted.json has sections")
    assert result.status == "pass"


def test_check_json_key_missing_key(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    f = ref / "extracted.json"
    f.write_text(json.dumps({"url": "https://example.com"}))
    gate = Gate(ref)
    result = gate.check_json_key(f, "sections", "extracted.json has sections")
    assert result.status == "fail"
    assert "sections" in result.message


def test_check_json_key_malformed(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    f = ref / "bad.json"
    f.write_text("{not valid json")
    gate = Gate(ref)
    result = gate.check_json_key(f, "sections", "bad.json")
    assert result.status == "fail"
    assert "malformed" in result.message.lower()


# ── gate_reference ──


def test_gate_reference_pass(ref_dir_with_artifacts: Path) -> None:
    gate = Gate(ref_dir_with_artifacts)
    results = gate.gate_reference()
    failures = [r for r in results if r.status == "fail"]
    assert failures == [], f"Unexpected failures: {failures}"


def test_gate_reference_fail_no_screenshots(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    gate = Gate(ref)
    results = gate.gate_reference()
    failures = [r for r in results if r.status == "fail"]
    assert len(failures) > 0


def test_gate_reference_fail_no_transitions_ref(tmp_path: Path) -> None:
    """gate_reference must fail when transitions/ref/ is missing (SKILL.md Phase 1 gate)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    # Has screenshots but no transitions/ref/
    screenshots = ref / "static" / "ref"
    screenshots.mkdir(parents=True)
    for i in range(5):
        (screenshots / f"scroll_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
    (ref / "regions.json").write_text('{"regions": []}')

    gate = Gate(ref)
    results = gate.gate_reference()
    failures = [r for r in results if r.status == "fail"]
    assert any("transitions" in r.label or "transitions" in r.message for r in failures), (
        "Missing transitions/ref/ must produce a fail result"
    )


def test_gate_reference_pass_with_transitions_ref(tmp_path: Path) -> None:
    """gate_reference must pass when all three Phase 1 artifacts exist."""
    ref = tmp_path / "ref"
    ref.mkdir()
    screenshots = ref / "static" / "ref"
    screenshots.mkdir(parents=True)
    for i in range(5):
        (screenshots / f"scroll_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
    transitions = ref / "transitions" / "ref"
    transitions.mkdir(parents=True)
    (transitions / "scroll.webm").write_bytes(b"\x1aE\xdf\xa3" + b"\x00" * 100)
    (ref / "regions.json").write_text('{"regions": []}')

    gate = Gate(ref)
    results = gate.gate_reference()
    failures = [r for r in results if r.status == "fail"]
    assert failures == [], f"Unexpected failures: {failures}"


# ── run() exit codes ──


def test_run_returns_0_on_pass(ref_dir_with_artifacts: Path) -> None:
    gate = Gate(ref_dir_with_artifacts)
    code = gate.run("reference")
    assert code == 0


def test_run_returns_1_on_fail(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    gate = Gate(ref)
    code = gate.run("reference")
    assert code == 1


def test_run_returns_2_on_unknown_gate(ref_dir_with_artifacts: Path) -> None:
    gate = Gate(ref_dir_with_artifacts)
    code = gate.run("nonexistent-gate")
    assert code == 2


# ── JSON output ──


def test_json_output_structure(ref_dir_with_artifacts: Path, capsys: pytest.CaptureFixture[str]) -> None:
    gate = Gate(ref_dir_with_artifacts)
    gate.run("reference", json_output=True)
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "passed" in data
    assert "fail_count" in data
    assert "failures" in data
    assert isinstance(data["failures"], list)


# ── pipeline-state.json recording ──


def test_run_gate_pass_writes_pipeline_state(ref_dir_with_artifacts: Path) -> None:
    """Gate PASS: pipeline-state.json is created and the gate is recorded."""
    from ui_clone.state import PipelineState

    gate = Gate(ref_dir_with_artifacts)
    exit_code = gate.run("reference", json_output=True)
    assert exit_code == 0
    state = PipelineState.load(ref_dir_with_artifacts)
    assert "reference" in state.completed_steps
    assert state.current_gate == "extraction"


def test_run_gate_fail_bumps_consecutive_fail_count(tmp_path: Path) -> None:
    """Gate FAIL on the active gate: gate_fail_counts[gate] increments and is
    written to pipeline-state.json so the goal card can surface a STUCK banner
    after the threshold. completed_steps stays empty — the gate did not pass.
    """
    from ui_clone.state import PipelineState

    ref = tmp_path / "ref"
    ref.mkdir()
    gate = Gate(ref)
    for expected_count in (1, 2, 3):
        exit_code = gate.run("reference", json_output=True)
        assert exit_code == 1
        state = PipelineState.load(ref)
        assert state.gate_fail_counts.get("reference") == expected_count
        assert "reference" not in state.completed_steps
        assert state.current_gate == "reference"


def test_run_gate_fails_when_pipeline_state_skips_prerequisites(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A later active gate with missing earlier completed_steps must fail closed."""
    from ui_clone.state import PipelineState

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "pipeline-state.json").write_text(
        json.dumps(
            {
                "component": "ref",
                "completed_steps": ["extraction"],
                "current_gate": "post-implement",
            }
        ),
        encoding="utf-8",
    )

    code = Gate(ref).run("post-implement", json_output=True)
    data = json.loads(capsys.readouterr().out)
    failures = data["failures"]

    assert code == 1
    assert any(f["label"] == "pipeline-state prerequisites" for f in failures)
    reason = " ".join(f["reason"] for f in failures)
    assert "reference" in reason
    assert "pre-generate" in reason
    state = PipelineState.load(ref)
    assert state.current_gate == "post-implement"
    assert "post-implement" not in state.completed_steps


def test_gate_post_implement_requires_verification_plan(tmp_path: Path) -> None:
    """post-implement must not silently skip site-specific verification rows."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "extracted.json").write_text(json.dumps({"sections": []}), encoding="utf-8")
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "hero-reveal",
                        "trigger": "intersection",
                        "source_chunk": "bundle.js",
                        "bundle_branch": "IntersectionObserver",
                        "target": ".hero",
                        "animation": {"type": "fade-up"},
                        "reference_frames": ["static/ref/0.png"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    static_ref = ref / "static" / "ref"
    static_ref.mkdir(parents=True)
    for i in range(5):
        (static_ref / f"{i}.png").write_bytes(b"\x89PNG" + b"\0" * 20)

    failures = [r for r in Gate(ref).gate_post_implement() if r.status == "fail"]

    assert any(r.label == "verification-plan.json" for r in failures)


# ── gate_pre_generate — footer check ──


def test_gate_pre_generate_blocks_when_footer_missing_from_component_map(tmp_path: Path) -> None:
    """section-map has hasFooter=True but component-map has no footer entry → fail."""
    ref = tmp_path / "ref"
    ref.mkdir()

    # Minimal artifacts required by gate_pre_generate
    (ref / "extracted.json").write_text(json.dumps({"sections": [], "url": "https://example.com"}))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "fixture-reveal-on-scroll",
            "trigger": "intersection",
            "source_chunk": "fixture.js",
            "bundle_branch": "main",
            "target": ".fixture",
            "animation": "opacity-translateY",
            "reference_frames": ["frame_00.png"],
        }]
    }))
    (ref / "animation-init-styles.json").write_text(json.dumps({}))
    (ref / "svg-text-elements.json").write_text(json.dumps([]))
    responsive = ref / "responsive"
    responsive.mkdir()
    (responsive / "sizing-expressions.json").write_text(json.dumps({}))
    (ref / "interactions-detected.json").write_text(
        json.dumps({"interactions": [], "hasPreloader": False})
    )
    (ref / "hover-css-rules.json").write_text(json.dumps([]))
    (ref / "transition-coverage.json").write_text(
        json.dumps({"animatedElements": [], "staticElements": []})
    )
    (ref / "element-roles.json").write_text(json.dumps({}))
    (ref / "element-groups.json").write_text(json.dumps({}))
    (ref / "layout-decisions.json").write_text(json.dumps({}))

    # section-map has a <footer>
    (ref / "section-map.json").write_text(
        json.dumps(
            {
                "sections": [{"tag": "main"}],
                "totalCount": 1,
                "hasFooter": True,
            }
        )
    )
    # component-map has NO footer entry
    (ref / "component-map.json").write_text(
        json.dumps(
            {
                "sections": [{"componentName": "HeroSection", "sourceTag": "main"}],
                "sectionCount": 1,
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_pre_generate()
    footer_failures = [r for r in results if r.status == "fail" and "footer" in r.message.lower()]
    assert footer_failures, "Missing footer in component-map must produce a fail result"


# ── gate_pre_generate — hover timing unknown ──


def test_gate_pre_generate_fails_when_hover_timing_unknown(tmp_path: Path) -> None:
    """interactions with timingSource='unknown' must cause gate failure."""
    ref = tmp_path / "ref"
    ref.mkdir()

    (ref / "extracted.json").write_text(json.dumps({"sections": [], "url": "https://example.com"}))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "fixture-reveal-on-scroll",
            "trigger": "intersection",
            "source_chunk": "fixture.js",
            "bundle_branch": "main",
            "target": ".fixture",
            "animation": "opacity-translateY",
            "reference_frames": ["frame_00.png"],
        }]
    }))
    (ref / "animation-init-styles.json").write_text(json.dumps({}))
    (ref / "svg-text-elements.json").write_text(json.dumps([]))
    responsive = ref / "responsive"
    responsive.mkdir()
    (responsive / "sizing-expressions.json").write_text(json.dumps({}))
    (ref / "hover-css-rules.json").write_text(json.dumps([]))
    (ref / "transition-coverage.json").write_text(
        json.dumps({"animatedElements": [], "staticElements": []})
    )
    (ref / "element-roles.json").write_text(json.dumps({}))
    (ref / "element-groups.json").write_text(json.dumps({}))
    (ref / "layout-decisions.json").write_text(json.dumps({}))
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [], "totalCount": 0, "hasFooter": False})
    )
    (ref / "component-map.json").write_text(json.dumps({"sections": [], "sectionCount": 0}))
    # interactions with timingSource='unknown'
    (ref / "interactions-detected.json").write_text(
        json.dumps(
            {
                "interactions": [
                    {"trigger": "hover", "timingSource": "unknown", "selector": ".btn"},
                ],
                "hasPreloader": False,
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_pre_generate()
    timing_failures = [r for r in results if r.status == "fail" and "unknown" in r.message.lower()]
    assert timing_failures, "timingSource='unknown' must produce a fail result"


# ── gate_pre_generate — artifact provenance ──


def _write_pre_generate_baseline(ref: Path) -> None:
    """Write enough artifacts for pre-generate so provenance is the only blocker."""
    (ref / "extracted.json").write_text(json.dumps({"sections": [], "url": "https://example.com"}))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "fixture-reveal-on-scroll",
            "trigger": "intersection",
            "source_chunk": "fixture.js",
            "bundle_branch": "main",
            "target": ".fixture",
            "animation": "opacity-translateY",
            "reference_frames": ["frame_00.png"],
        }]
    }))
    (ref / "animation-init-styles.json").write_text(json.dumps({"elements": []}))
    (ref / "section-map.json").write_text(json.dumps({"sections": [], "totalCount": 0, "hasFooter": False}))
    (ref / "svg-text-elements.json").write_text(json.dumps([]))
    # Fix 9 — dom-scaffold.json now a pre-generate prereq.
    (ref / "dom-scaffold.json").write_text(json.dumps({"sections": [], "tree": {"tag": "body"}}))
    responsive = ref / "responsive"
    responsive.mkdir()
    (responsive / "sizing-expressions.json").write_text(json.dumps({"expressions": []}))
    (ref / "interactions-detected.json").write_text(json.dumps({"interactions": [], "hasPreloader": False}))
    (ref / "hover-css-rules.json").write_text(json.dumps([]))
    (ref / "transition-coverage.json").write_text(json.dumps({"animatedElements": [], "staticElements": []}))
    (ref / "element-roles.json").write_text(json.dumps({"roles": []}))
    (ref / "element-groups.json").write_text(json.dumps({"groups": []}))
    (ref / "layout-decisions.json").write_text(json.dumps({"decisions": []}))
    (ref / "component-map.json").write_text(json.dumps({"sections": [], "sectionCount": 0}))


def _write_valid_artifact_provenance(ref: Path) -> None:
    artifacts = [
        "extracted.json",
        "transition-spec.json",
        "animation-init-styles.json",
        "section-map.json",
        "svg-text-elements.json",
        "responsive/sizing-expressions.json",
        "interactions-detected.json",
        "transition-coverage.json",
        "component-map.json",
    ]
    (ref / "artifact-provenance.json").write_text(json.dumps({
        "artifacts": [
            {
                "path": artifact,
                "source": "agent-browser-eval" if artifact != "transition-spec.json" else "bundle-grep",
                "evidence": [artifact],
                "generatedAt": "2026-05-14T00:00:00Z",
            }
            for artifact in artifacts
        ],
    }))


def test_gate_pre_generate_blocks_without_artifact_provenance(tmp_path: Path) -> None:
    """Pre-generation must fail when extraction artifacts have no evidence trail."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)

    gate = Gate(ref)
    results = gate.gate_pre_generate()

    assert any(
        r.status == "fail" and "artifact-provenance.json" in r.message
        for r in results
    ), "Missing artifact provenance must block pre-generation"


def test_gate_pre_generate_blocks_manual_artifact_provenance(tmp_path: Path) -> None:
    """Critical artifacts cannot be declared as hand-written/manual evidence."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)
    provenance = json.loads((ref / "artifact-provenance.json").read_text())
    provenance["artifacts"][0]["source"] = "manual"
    (ref / "artifact-provenance.json").write_text(json.dumps(provenance))

    gate = Gate(ref)
    results = gate.gate_pre_generate()

    assert any(
        r.status == "fail" and "manual" in r.message.lower()
        for r in results
    ), "Manual provenance for critical extraction artifacts must block pre-generation"


def test_gate_pre_generate_accepts_evidence_backed_artifact_provenance(tmp_path: Path) -> None:
    """Valid provenance should not add failures to an otherwise complete extraction."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)

    gate = Gate(ref)
    results = gate.gate_pre_generate()

    provenance_failures = [
        r for r in results
        if r.status == "fail" and "provenance" in r.message.lower()
    ]
    assert not provenance_failures


# ── gate_extraction must NOT require Step 6d artifacts ──


def test_gate_extraction_does_not_require_transition_coverage(tmp_path: Path) -> None:
    """gate_extraction must pass without transition-coverage.json.

    transition-coverage.json is produced at Step 6d, after bundle (5c) and spec (5d).
    Requiring it at the extraction gate (which runs after Step 2-3) would deadlock
    the pipeline — extraction can never advance until 6d, but 6d depends on bundle,
    which depends on extraction having passed. Coverage of transition-coverage.json
    belongs to gate_pre_generate (see test_gate_pre_generate_*).
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    for fname in [
        "structure.json",
        "head.json",
        "styles.json",
        "fonts.json",
        "visible-images.json",
        "inline-svgs.json",
        "body-state.json",
        "design-bundles.json",
    ]:
        (ref / fname).write_text(json.dumps({}))
    css_dir = ref / "css"
    css_dir.mkdir()
    (css_dir / "variables.txt").write_text(":root {}")
    # transition-coverage.json intentionally omitted

    gate = Gate(ref)
    results = gate.gate_extraction()
    failures = [r for r in results if r.status == "fail"]
    labels = [r.label for r in failures]
    assert not any("transition-coverage" in lbl for lbl in labels), (
        "gate_extraction must not require transition-coverage.json (Step 6d artifact)"
    )


# ── gate_bundle ──


def test_gate_bundle_fails_when_no_js_files(tmp_path: Path) -> None:
    """gate_bundle must fail when bundles/ directory has no JS files."""
    ref = tmp_path / "ref"
    ref.mkdir()
    bundles = ref / "bundles"
    bundles.mkdir()
    # No JS files

    gate = Gate(ref)
    results = gate.gate_bundle()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "Empty bundles/ must produce a fail result"


def test_gate_bundle_fails_when_required_json_missing(tmp_path: Path) -> None:
    """gate_bundle must fail when interactions-detected.json is missing."""
    ref = tmp_path / "ref"
    ref.mkdir()
    bundles = ref / "bundles"
    bundles.mkdir()
    (bundles / "chunk-0.js").write_text("// bundle")
    # interactions-detected.json, scroll-engine.json, external-sdks.json intentionally absent

    gate = Gate(ref)
    results = gate.gate_bundle()
    failures = [r for r in results if r.status == "fail"]
    assert any(
        "interactions-detected" in r.label or "interactions-detected" in r.message for r in failures
    ), "Missing interactions-detected.json must produce a fail"


def test_gate_bundle_passes_with_required_files(tmp_path: Path) -> None:
    """gate_bundle must pass when bundles/ has JS files and all required JSON files exist."""
    ref = tmp_path / "ref"
    ref.mkdir()
    bundles = ref / "bundles"
    bundles.mkdir()
    for i in range(3):
        (bundles / f"chunk-{i}.js").write_text("// bundle")
    (ref / "interactions-detected.json").write_text(
        json.dumps({"interactions": [], "hasPreloader": False})
    )
    (ref / "scroll-engine.json").write_text(json.dumps({"engine": "native"}))
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": [], "gsap": False}))

    gate = Gate(ref)
    results = gate.gate_bundle()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"gate_bundle must pass with required files: {failures}"


# ── gate_spec ──


def test_gate_spec_fails_when_transition_spec_missing(tmp_path: Path) -> None:
    """gate_spec must fail when transition-spec.json is absent."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({"chunks": ["a.js"]}))
    # transition-spec.json intentionally absent

    gate = Gate(ref)
    results = gate.gate_spec()
    failures = [r for r in results if r.status == "fail"]
    assert any("transition-spec" in r.label or "transition-spec" in r.message for r in failures), (
        "Missing transition-spec.json must produce a fail"
    )


def test_gate_spec_fails_when_bundle_map_missing(tmp_path: Path) -> None:
    """gate_spec must fail when bundle-map.json is absent."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "fixture-reveal-on-scroll",
            "trigger": "intersection",
            "source_chunk": "fixture.js",
            "bundle_branch": "main",
            "target": ".fixture",
            "animation": "opacity-translateY",
            "reference_frames": ["frame_00.png"],
        }]
    }))
    # bundle-map.json intentionally absent

    gate = Gate(ref)
    results = gate.gate_spec()
    failures = [r for r in results if r.status == "fail"]
    assert any("bundle-map" in r.label or "bundle-map" in r.message for r in failures), (
        "Missing bundle-map.json must produce a fail"
    )


def test_gate_spec_passes_with_required_files(tmp_path: Path) -> None:
    """gate_spec must pass when all required artifacts exist."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({"chunks": ["a.js"]}))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "fixture-reveal-on-scroll",
            "trigger": "intersection",
            "source_chunk": "fixture.js",
            "bundle_branch": "main",
            "target": ".fixture",
            "animation": "opacity-translateY",
            "reference_frames": ["frame_00.png"],
        }]
    }))
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": []}))
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1, "requiredChecks": []
    }))
    verify = ref / "verify"
    verify.mkdir()
    for i in range(5):
        (verify / f"frame_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)

    gate = Gate(ref)
    results = gate.gate_spec()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"gate_spec must pass with required files present: {failures}"


def test_gate_spec_fails_when_verification_plan_missing(tmp_path: Path) -> None:
    """gate_spec must fail when verification-plan.json is absent — without it,
    universal checks like hydration-check are silently skipped downstream."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({"chunks": ["a.js"]}))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "fixture-reveal-on-scroll",
            "trigger": "intersection",
            "source_chunk": "fixture.js",
            "bundle_branch": "main",
            "target": ".fixture",
            "animation": "opacity-translateY",
            "reference_frames": ["frame_00.png"],
        }]
    }))
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": []}))
    verify = ref / "verify"
    verify.mkdir()
    for i in range(5):
        (verify / f"frame_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)

    gate = Gate(ref)
    results = gate.gate_spec()
    failures = [r for r in results if r.status == "fail"]
    assert any("verification-plan.json" in r.label for r in failures), (
        f"Missing verification-plan.json must fail gate_spec: {failures}"
    )


def test_gate_spec_fails_when_any_transition_missing_documented_fields(tmp_path: Path) -> None:
    """Every transition entry must carry the documented bundle-to-code handoff fields."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({"chunks": ["a.js"]}))
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": []}))
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1, "requiredChecks": []
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {
                "id": "hero-reveal",
                "trigger": "page load",
                "source_chunk": "a.js",
                "bundle_branch": "first visit",
                "target": ".hero",
                "animation": {"property": "opacity", "from": 0, "to": 1},
                "reference_frames": "verify/hero/f001.png",
            },
            {
                "id": "cards-scroll",
                "trigger": "scroll",
                "bundle_branch": "desktop",
            },
        ]
    }))
    verify = ref / "verify"
    verify.mkdir()
    for i in range(5):
        (verify / f"frame_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)

    gate = Gate(ref)
    results = gate.gate_spec()
    failures = [r for r in results if r.status == "fail"]

    assert any(
        r.label == "transitions[1] keys"
        and "source_chunk" in r.message
        and "target" in r.message
        and "animation" in r.message
        and "reference_frames" in r.message
        for r in failures
    ), f"Missing documented transition fields must fail gate_spec: {failures}"


# ── gate_post_implement ──


def test_gate_post_implement_fails_when_extracted_missing(tmp_path: Path) -> None:
    """gate_post_implement must fail when extracted.json is absent."""
    ref = tmp_path / "ref"
    ref.mkdir()

    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert any("extracted" in r.label or "extracted" in r.message for r in failures), (
        "Missing extracted.json must produce a fail in gate_post_implement"
    )


def test_gate_post_implement_passes_with_required_files(tmp_path: Path) -> None:
    """gate_post_implement must pass when required closeout artifacts exist."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "extracted.json").write_text(json.dumps({"sections": [], "url": "https://example.com"}))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "fixture-reveal-on-scroll",
            "trigger": "intersection",
            "source_chunk": "fixture.js",
            "bundle_branch": "main",
            "target": ".fixture",
            "animation": "opacity-translateY",
            "reference_frames": ["frame_00.png"],
        }]
    }))
    screenshots = ref / "static" / "ref"
    screenshots.mkdir(parents=True)
    for i in range(5):
        (screenshots / f"scroll_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredChecks": []}),
        encoding="utf-8",
    )

    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"gate_post_implement must pass with required files present: {failures}"


def _post_implement_baseline(ref: Path) -> None:
    """Write minimal artifacts so gate_post_implement passes baseline checks."""
    (ref / "extracted.json").write_text(json.dumps({"sections": [], "url": "https://example.com"}))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "fixture-reveal-on-scroll",
            "trigger": "intersection",
            "source_chunk": "fixture.js",
            "bundle_branch": "main",
            "target": ".fixture",
            "animation": "opacity-translateY",
            "reference_frames": ["frame_00.png"],
        }]
    }))
    screenshots = ref / "static" / "ref"
    screenshots.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        (screenshots / f"scroll_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredChecks": []}),
        encoding="utf-8",
    )


def test_componentization_gate_fails_on_monolithic_page(tmp_path: Path) -> None:
    """Regression — c9b638d benchmark shipped a 214-line page.tsx with 0
    files in impl/src/components/. New post-implement check enforces:
    page.tsx > 200 LOC AND components/ < 3 → FAIL.
    """
    work = tmp_path / "benchmark" / "work" / "deadbee"
    ref = work / "ref"
    impl = work / "impl"
    ref.mkdir(parents=True)
    _post_implement_baseline(ref)
    (impl / "src" / "app").mkdir(parents=True)
    page = impl / "src" / "app" / "page.tsx"
    page.write_text("\n".join(f"// line {i}" for i in range(220)) + "\n", encoding="utf-8")
    gate = Gate(ref)
    failures = [r for r in gate.gate_post_implement() if r.status == "fail"]
    assert any(r.label == "componentization" for r in failures), (
        f"monolithic page.tsx must fail post-implement: {failures}"
    )


def test_componentization_gate_passes_when_split(tmp_path: Path) -> None:
    """page.tsx > 200 LOC but components/ has ≥ 3 .tsx files → PASS.
    Counterpart that confirms the guard only triggers on monolithic shape.
    """
    work = tmp_path / "benchmark" / "work" / "deadbee"
    ref = work / "ref"
    impl = work / "impl"
    ref.mkdir(parents=True)
    _post_implement_baseline(ref)
    (impl / "src" / "app").mkdir(parents=True)
    (impl / "src" / "app" / "page.tsx").write_text(
        "\n".join(f"// line {i}" for i in range(220)) + "\n", encoding="utf-8"
    )
    comps = impl / "src" / "components"
    comps.mkdir()
    for name in ("Hero", "Stats", "Footer"):
        (comps / f"{name}.tsx").write_text(f"export default function {name}() {{ return null; }}\n")
    gate = Gate(ref)
    failures = [r for r in gate.gate_post_implement() if r.status == "fail"]
    assert not any(r.label == "componentization" for r in failures), (
        f"split impl must not trigger componentization fail: {failures}"
    )


def test_componentization_gate_skipped_when_no_impl(tmp_path: Path) -> None:
    """Regular tmp/ref/ flow with no co-located impl → silent skip."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    gate = Gate(ref)
    failures = [r for r in gate.gate_post_implement() if r.status == "fail"]
    assert not any(r.label == "componentization" for r in failures)


def _build_renamed_impl(loop_root: Path, name: str, page_loc: int) -> Path:
    """Helper for rename-resolver tests. Creates
    `loop_root/<name>/{package.json, src/app/page.tsx}` with `page_loc` LOC.
    Returns the impl dir.
    """
    impl = loop_root / name
    (impl / "src" / "app").mkdir(parents=True)
    (impl / "package.json").write_text('{"name":"clone","version":"0.1.0"}\n')
    (impl / "src" / "app" / "page.tsx").write_text(
        "\n".join(f"// line {i}" for i in range(page_loc)) + "\n", encoding="utf-8"
    )
    return impl


def test_find_impl_root_detects_renamed_sibling(tmp_path: Path) -> None:
    """Codex L38 issue 11 — adversarial rename happy path.

    Loop-37 sub-agent renamed `impl/` → `realfood-clone/` to bypass gate
    hooks that hard-coded the `impl/` path. The shared resolver
    (`scripts/extract/find-impl-root.sh`, wired into Gate._find_impl_root)
    must detect any sibling directory that LOOKS like an impl scaffold
    (package.json + src/app + .tsx) regardless of its name.
    """
    loop_root = tmp_path / "scratch" / "loop-X"
    ref = loop_root / "tmp" / "ref" / "realfood-main"
    ref.mkdir(parents=True)
    _post_implement_baseline(ref)
    _build_renamed_impl(loop_root, "realfood-clone", page_loc=220)
    gate = Gate(ref)
    resolved = gate._find_impl_root()
    assert resolved is not None, "resolver must locate renamed impl dir"
    assert resolved.name == "realfood-clone", f"got {resolved}"


def test_find_impl_root_skips_skip_dir_names(tmp_path: Path) -> None:
    """Codex L38 issue 11 — adversarial rename negative path.

    A sub-agent renaming impl/ to a name in the resolver's skip-list
    (`dist`, `node_modules`, `.next`, `.git`, `benchmark`, `tmp`, `scratch`,
    `scripts`) MUST NOT escape detection by sneaking through the heuristic
    — instead, the resolver should return None so downstream gates that
    require impl_root cannot silently no-op. The right UX is "we cannot
    find impl/" → gate fails loudly, not "looks fine, nothing to check".
    """
    loop_root = tmp_path / "scratch" / "loop-Y"
    ref = loop_root / "tmp" / "ref" / "realfood-main"
    ref.mkdir(parents=True)
    _post_implement_baseline(ref)
    _build_renamed_impl(loop_root, "dist", page_loc=220)
    gate = Gate(ref)
    assert gate._find_impl_root() is None, (
        "resolver must not return a skip-dir-named candidate"
    )


def test_find_impl_root_disambiguates_multiple_candidates(tmp_path: Path) -> None:
    """When two impl-shaped directories exist, the resolver should fail
    with AMBIGUOUS rather than picking arbitrarily — this prevents a
    sub-agent from making a second clone to hide the broken first one.
    """
    loop_root = tmp_path / "scratch" / "loop-Z"
    ref = loop_root / "tmp" / "ref" / "realfood-main"
    ref.mkdir(parents=True)
    _post_implement_baseline(ref)
    _build_renamed_impl(loop_root, "realfood-clone-a", page_loc=50)
    _build_renamed_impl(loop_root, "realfood-clone-b", page_loc=50)
    gate = Gate(ref)
    # Resolver script exits 2 with AMBIGUOUS message when neither has a
    # framework config marker (next.config / vite.config) — gate returns
    # None on non-zero exit.
    assert gate._find_impl_root() is None, (
        "resolver must refuse to pick between two impl-shaped siblings"
    )


def test_componentization_gate_skipped_when_page_small(tmp_path: Path) -> None:
    """page.tsx ≤ 200 LOC → silent skip even if components/ is empty.
    A small monolith is still legible and the split forcing is unnecessary.
    """
    work = tmp_path / "benchmark" / "work" / "deadbee"
    ref = work / "ref"
    impl = work / "impl"
    ref.mkdir(parents=True)
    _post_implement_baseline(ref)
    (impl / "src" / "app").mkdir(parents=True)
    (impl / "src" / "app" / "page.tsx").write_text(
        "\n".join(f"// line {i}" for i in range(150)) + "\n", encoding="utf-8"
    )
    gate = Gate(ref)
    failures = [r for r in gate.gate_post_implement() if r.status == "fail"]
    assert not any(r.label == "componentization" for r in failures)


def test_verification_plan_missing_fails_post_implement(tmp_path: Path) -> None:
    """No verification-plan.json → post-implement fails instead of skipping checks."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").unlink()
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert any(r.label == "verification-plan.json" for r in failures)


def test_verification_plan_missing_artifact_fails_block(tmp_path: Path) -> None:
    """A block-severity requiredCheck whose `produces` artifact is missing → FAIL with fix command."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {
                "id": "hydration-check",
                "script": "skills/visual-debug/scripts/hydration-check.sh",
                "produces": "hydration-check.json",
                "reason": "Universal",
                "severity": "block",
            }
        ],
    }))
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert any(r.label == "required: hydration-check" for r in failures)
    assert any("hydration-check.sh" in r.fix for r in failures)


def test_verification_plan_artifact_status_pass(tmp_path: Path) -> None:
    """Artifact present with status: pass → PASS."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "hydration-check", "produces": "hydration-check.json",
             "reason": "Universal", "severity": "block"}
        ],
    }))
    (ref / "hydration-check.json").write_text(json.dumps({"status": "pass", "errorCount": 0}))
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert not failures


def test_verification_plan_artifact_status_fail(tmp_path: Path) -> None:
    """Artifact present with status: fail → block-severity FAIL."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "hydration-check", "produces": "hydration-check.json",
             "reason": "Universal", "severity": "block"}
        ],
    }))
    (ref / "hydration-check.json").write_text(json.dumps({"status": "fail", "errorCount": 3}))
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert any("status: fail" in r.message for r in failures)


def test_verification_plan_text_artifact_fails_on_cross_mark(tmp_path: Path) -> None:
    """Non-JSON `produces` artifacts (e.g. transitions/result.txt) must be
    scanned for ❌ FAIL markers — presence-only would let real failures slip
    past gate_post_implement when no dedicated parser exists for that file."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "transition-compare", "produces": "transitions/result.txt",
             "reason": "signals.hasHover=true", "severity": "block"}
        ],
    }))
    (ref / "transitions").mkdir()
    (ref / "transitions" / "result.txt").write_text(
        "card.button hover idle/hover ✅ PASS AE=120\n"
        "card.title  hover idle/hover ❌ FAIL AE=2400 (delta missing)\n"
    )
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert any(r.label == "required: transition-compare" for r in failures)
    assert any("1 FAIL line" in r.message for r in failures)


def test_verification_plan_text_artifact_passes_when_clean(tmp_path: Path) -> None:
    """Same artifact type as above, but with no ❌ markers — passes."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "transition-compare", "produces": "transitions/result.txt",
             "reason": "signals.hasHover=true", "severity": "block"}
        ],
    }))
    (ref / "transitions").mkdir()
    (ref / "transitions" / "result.txt").write_text(
        "card.button hover idle/hover ✅ PASS AE=120\n"
        "card.title  hover idle/hover ✅ PASS AE=80\n"
    )
    gate = Gate(ref)
    results = gate.gate_post_implement()
    assert not [r for r in results if r.status == "fail"]
    assert any(r.label == "required: transition-compare" and r.status == "pass"
               for r in results)


def test_verification_plan_tree_diff_floor_fails_on_empty_walk(tmp_path: Path) -> None:
    """Regression — 5199dd9 benchmark shipped tree-diff status=pass with
    walked=11 (vs the ~200 a real impl would walk). With section-map declaring
    9 sections, the floor is max(30, 9*5)=45. 11 < 45 → fail.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "tree-diff", "produces": "tree-diff-status.json",
             "reason": "primary convergence gate", "severity": "block"}
        ],
    }))
    (ref / "section-map.json").write_text(json.dumps({
        "totalCount": 9,
        "sections": [{"tag": "section", "id": f"s{i}", "y": i*1000, "height": 1000, "width": 1440} for i in range(9)],
    }))
    (ref / "tree-diff-status.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass",
        "elements_walked": 11,
        "counts": {"critical": 0, "major": 0, "layout-major": 0, "minor": 0, "layout-minor": 0, "ok": 11, "unpaired": 0},
        "errorCount": 0, "reason": "all paired elements within tolerance",
    }))
    gate = Gate(ref)
    failures = [r for r in gate.gate_post_implement() if r.status == "fail"]
    assert any("tree-diff" in r.label and "elements walked" in r.message for r in failures), (
        f"tree-diff with 11 walked < 45 floor must fail; got: {[(r.label, r.message[:80]) for r in failures]}"
    )


def test_verification_plan_tree_diff_floor_passes_on_real_walk(tmp_path: Path) -> None:
    """Counterpart: tree-diff walked=200 with the same 9-section ref passes."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "tree-diff", "produces": "tree-diff-status.json",
             "reason": "primary convergence gate", "severity": "block"}
        ],
    }))
    (ref / "section-map.json").write_text(json.dumps({
        "totalCount": 9,
        "sections": [{"tag": "section", "id": f"s{i}", "y": i*1000, "height": 1000, "width": 1440} for i in range(9)],
    }))
    (ref / "tree-diff-status.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass",
        "elements_walked": 200,
        "counts": {"critical": 0, "major": 0, "layout-major": 0, "minor": 0, "layout-minor": 0, "ok": 200, "unpaired": 0},
        "errorCount": 0, "reason": "all paired elements within tolerance",
    }))
    gate = Gate(ref)
    failures = [r for r in gate.gate_post_implement() if r.status == "fail"]
    assert not any("tree-diff" in r.label for r in failures), (
        f"tree-diff with walked=200 should pass; got: {failures}"
    )


def test_verification_plan_tree_diff_unpaired_majority_fails(tmp_path: Path) -> None:
    """Loop-58 regression: tree-diff status=pass is not meaningful when most
    walked elements are unpaired. That means elementFromPoint pairing failed,
    not that styles converged.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "tree-diff", "produces": "tree-diff-status.json",
             "reason": "primary convergence gate", "severity": "block"}
        ],
    }))
    (ref / "section-map.json").write_text(json.dumps({
        "totalCount": 9,
        "sections": [{"tag": "section", "id": f"s{i}", "y": i*1000, "height": 1000, "width": 1440} for i in range(9)],
    }))
    (ref / "tree-diff-status.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass",
        "elements_walked": 90,
        "counts": {
            "critical": 0, "major": 0, "layout-major": 0,
            "minor": 0, "layout-minor": 0, "ok": 10, "unpaired": 80,
        },
        "errorCount": 0, "reason": "all paired elements within tolerance",
    }))
    gate = Gate(ref)
    failures = [r for r in gate.gate_post_implement() if r.status == "fail"]
    assert any("tree-diff" in r.label and "unpaired" in r.message for r in failures), (
        f"tree-diff with unpaired majority must fail; got: {[(r.label, r.message[:100]) for r in failures]}"
    )


def test_verification_plan_transition_compare_empty_artifact_fails(tmp_path: Path) -> None:
    """Regression — empty transitions/result.txt while transition-spec declares
    transitions is the "transition-compare never ran" gaming pattern.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "transition-compare", "produces": "transitions/result.txt",
             "reason": "spec has hover transitions", "severity": "block"}
        ],
    }))
    # transition-spec declares 5 transitions
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": f"t{i}", "trigger": "hover"} for i in range(5)]
    }))
    # Empty result.txt — no measurement rows
    (ref / "transitions").mkdir()
    (ref / "transitions" / "result.txt").write_text(
        "# transition-compare\n# generated: 2026-05-16\n\n"
    )
    gate = Gate(ref)
    failures = [r for r in gate.gate_post_implement() if r.status == "fail"]
    assert any("transition-compare" in r.label and "0 measurement rows" in r.message for r in failures), (
        f"empty transitions/result.txt with spec.transitions[] must fail; got: {[(r.label, r.message[:80]) for r in failures]}"
    )


def test_verification_plan_transition_compare_empty_artifact_passes_when_no_spec(tmp_path: Path) -> None:
    """Counterpart: when transition-spec has no transitions, empty
    result.txt is legitimate — there's nothing to measure.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    # This test specifically exercises the "spec declares no transitions"
    # path, so override the baseline's populated spec with an empty one.
    # gate_spec rejects empty transitions at the spec gate, but the
    # post-implement counterpart accepts an empty result.txt when the spec
    # is also empty — different gate, different responsibility.
    (ref / "transition-spec.json").write_text(json.dumps({"transitions": []}))
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "transition-compare", "produces": "transitions/result.txt",
             "reason": "static page, no transitions expected", "severity": "block"}
        ],
    }))
    (ref / "transitions").mkdir()
    (ref / "transitions" / "result.txt").write_text("# no transitions detected\n")
    gate = Gate(ref)
    failures = [r for r in gate.gate_post_implement() if r.status == "fail"]
    assert not any("transition-compare" in r.label for r in failures)


def test_verification_plan_warn_severity_does_not_fail(tmp_path: Path) -> None:
    """A warn-severity requiredCheck whose artifact reports fail must NOT fail the gate."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "optional", "produces": "optional.json",
             "reason": "Optional", "severity": "warn"}
        ],
    }))
    (ref / "optional.json").write_text(json.dumps({"status": "fail"}))
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    warns = [r for r in results if r.status == "warn"]
    assert not failures
    assert any(r.label == "required: optional" for r in warns)


def test_verification_plan_unsupported_schema_warns(tmp_path: Path) -> None:
    """Future schemaVersion → warn, plan ignored (forward compat)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 99,
        "requiredChecks": [
            {"id": "x", "produces": "x.json", "reason": "y", "severity": "block"}
        ],
    }))
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    warns = [r for r in results if r.status == "warn"]
    assert not failures, "Unknown schema must not fail the gate"
    assert any("schemaVersion" in r.message for r in warns)


def test_verification_plan_missing_schema_version_fails(tmp_path: Path) -> None:
    """Regression — agent hallucinated `{component, checks}` for verification-plan.json
    (no `schemaVersion` key). Previously silently ignored as if forward-compat; now
    hard-fails so the agent must actually run verification-plan.sh.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "component": "realfood",
        "checks": [
            {"name": "hydration-check", "required": True, "tier": "quick"}
        ],
    }))
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert any(
        r.label == "verification-plan.json" and "schemaVersion" in r.message
        for r in failures
    ), f"Missing schemaVersion must hard-fail post-implement, got: {results}"
    assert any("verification-plan.sh" in (r.fix or "") for r in failures), (
        "Fix hint must point at verification-plan.sh"
    )


def test_verification_plan_missing_required_checks_key_fails(tmp_path: Path) -> None:
    """schemaVersion=1 with `requiredChecks` key entirely absent → hard-fail."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        # no requiredChecks key
    }))
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert any(
        r.label == "verification-plan.json" and "requiredChecks" in r.message
        for r in failures
    ), f"Missing requiredChecks must hard-fail, got: {results}"


def test_verification_plan_empty_required_checks_warns(tmp_path: Path) -> None:
    """schemaVersion=1 with `requiredChecks: []` → warn (rare but legitimate)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [],
    }))
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    warns = [r for r in results if r.status == "warn"]
    assert not failures, "Empty requiredChecks should warn, not fail"
    assert any(
        r.label == "verification-plan.json" and "empty" in r.message.lower()
        for r in warns
    ), f"Empty list must produce a visibility warn, got: {results}"


# ── verification-plan.sh dispatch (subprocess) ──
#
# These two tests cover the wiring between the bash dispatcher and the
# gate.py consumer. Pure unit tests for gate.py above can't catch a
# regression where the dispatcher stops emitting a row that the gate
# expects to consume.


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _run_verification_plan(ref_dir: Path, tier: str | None = None) -> dict:
    import subprocess
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    cmd = ["bash", str(script), str(ref_dir)]
    if tier is not None:
        cmd.append(f"--tier={tier}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"verification-plan.sh failed: {proc.stderr}"
    return json.loads((ref_dir / "verification-plan.json").read_text())  # type: ignore[no-any-return]


def test_verification_plan_emits_video_motion_check_when_scroll_scrub_detected(tmp_path: Path) -> None:
    """hasScrollScrub=true via external-sdks → video-motion-compare row required.

    Replaces the prior 5-point transition-trajectory probe with a 60fps
    frame-by-frame compare. Catches "same end-state, wrong velocity curve"
    that 5-point sampling could not see (easeOutCubic vs easeOutQuint read
    identical at 0/25/50/75/100). transition-trajectory-compare.sh remains
    available for ad-hoc debug but is no longer in dispatch.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "external-sdks.json").write_text(json.dumps({
        "detected": ["useScroll", "scrollYProgress"]
    }))
    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasScrollScrub"] is True
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "video-motion-compare" in ids, (
        f"video-motion row missing when hasScrollScrub=true: {ids}"
    )
    assert "transition-trajectory" not in ids, (
        f"trajectory row should be retired from dispatch: {ids}"
    )


def test_verification_plan_omits_video_motion_check_when_no_motion_signal(tmp_path: Path) -> None:
    """No motion signals → no video-motion-compare row (cheap-check discipline).

    The video-motion check loads ref + impl in two agent-browser sessions,
    records ~5-10s of video, extracts 60fps frames, and SSIMs every pair.
    Skip it on static pages.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasScrollScrub"] is False
    assert plan["signals"]["hasSplash"] is False
    assert plan["signals"]["hasIOReveal"] is False
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "video-motion-compare" not in ids, (
        f"video-motion row present without motion signal: {ids}"
    )


def test_verification_plan_emits_click_state_check_when_click_trigger_detected(tmp_path: Path) -> None:
    """regions.json with triggerType: click-* → click-state-compare row required.

    Click-state transitions (tabs/accordions/modals/menu toggles) have their
    own motion arc that hover-compare + section-compare never exercise.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "click": [
            {"name": "tabs", "triggerType": "click-cycle", "selector": ".tab"}
        ]
    }))
    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasClickStateTransition"] is True
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "click-state-compare" in ids, (
        f"click-state row missing when click trigger present: {ids}"
    )


def test_verification_plan_omits_click_state_check_when_no_click_trigger(tmp_path: Path) -> None:
    """No click triggers anywhere → no click-state-compare row."""
    ref = tmp_path / "ref"
    ref.mkdir()
    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasClickStateTransition"] is False
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "click-state-compare" not in ids


def test_verification_plan_emits_hover_state_check_when_hover_signal_detected(tmp_path: Path) -> None:
    """hasHover=true → hover-state-compare row required.

    Static transition-compare verifies idle/hover end-states only. hover-state-compare
    runs 60fps video over the entry arc to catch easing/duration divergence on
    hover transitions — same bug class as video-motion-compare for scroll motion.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "interactions-detected.json").write_text(json.dumps({
        "interactions": [{"trigger": "hover", "target": ".btn"}]
    }))
    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasHover"] is True
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "hover-state-compare" in ids, (
        f"hover-state row missing when hasHover=true: {ids}"
    )
    # transition-compare should remain — they cover different bug classes.
    assert "transition-compare" in ids, (
        f"transition-compare should also be present alongside hover-state: {ids}"
    )


def test_verification_plan_omits_hover_state_check_when_no_hover_signal(tmp_path: Path) -> None:
    """No hover signal → no hover-state-compare row.

    Static pages without hover interactions should skip the 60fps hover sweep
    (it loads ref + impl and records a video per target — expensive on no-op).
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasHover"] is False
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "hover-state-compare" not in ids


def test_verification_plan_emits_runtime_spec_coverage_when_dump_and_spec_present(tmp_path: Path) -> None:
    """animation-runtime-dump.json + transition-spec.json present → runtime-spec-coverage row required.

    Turns transition-spec-rules.md Rule 7 ("consult animation-runtime-dump.json
    when authoring transition-spec.json") from an advisory into an enforced
    gate. If the dump shows ScrollTrigger entries but the spec has zero scroll
    entries, this check fails post-implement.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "animation-runtime-dump.json").write_text(json.dumps({
        "scrollTrigger": [{"start": 100, "end": 500}],
        "webAnimations": None, "lenis": None, "ix2": None, "gsap": None
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero", "trigger": "scroll", "type": "scroll-driven"}]
    }))
    plan = _run_verification_plan(ref)
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "runtime-spec-coverage" in ids, (
        f"runtime-spec-coverage row missing when dump + spec both present: {ids}"
    )


def test_verification_plan_omits_runtime_spec_coverage_when_dump_absent(tmp_path: Path) -> None:
    """No animation-runtime-dump.json → no runtime-spec-coverage row.

    Pre-Phase-0 captures (older runs or pages without runtime animations) won't
    have the dump. The coverage check should silently skip rather than fail.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "x", "trigger": "hover"}]
    }))
    plan = _run_verification_plan(ref)
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "runtime-spec-coverage" not in ids


def test_verification_plan_emits_spec_implementation_coverage_when_spec_present(tmp_path: Path) -> None:
    """transition-spec.json present → spec-implementation-coverage row required.

    Catches the silent-killer "selector matched but no motion declared" gap:
    transition-spec-coverage answers "does the impl mention this entry?", but
    spec-implementation-coverage answers "and does the impl actually animate
    it?". Both rows must dispatch when transition-spec.json exists so the
    presence check (pre-generate sanity) and the declaration check
    (post-generate enforcement) cover the spec→generation seam end-to-end.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "x", "trigger": "hover"}]
    }))
    plan = _run_verification_plan(ref)
    ids = {c["id"] for c in plan["requiredChecks"]}
    assert "spec-implementation-coverage" in ids, (
        f"spec-implementation-coverage row missing when transition-spec.json present: {ids}"
    )
    # The presence row must remain — they cover different bug classes
    # (presence vs declaration) and the cheaper presence check stays at quick.
    assert "transition-spec-coverage" in ids


def test_verification_plan_emits_transition_compare_when_spec_present_without_hover(
    tmp_path: Path,
) -> None:
    """A transition spec requires runtime comparison even when hover is absent."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-scroll", "trigger": "scroll", "type": "scroll-driven"}]
    }))

    plan = _run_verification_plan(ref)
    ids = [c["id"] for c in plan["requiredChecks"]]

    assert "transition-compare" in ids, (
        "transition-compare must be required for transition-spec.json, not only "
        f"hover signals: {ids}"
    )


def test_verification_plan_spec_implementation_coverage_tier_is_standard(tmp_path: Path) -> None:
    """spec-implementation-coverage must be tagged tier=standard.

    The row is meaningful only after the agent has generated impl source — at
    quick tier (inner iteration loop, often before generation), it would
    silently warn on every entry. Standard tier is the first level where the
    declaration check pays off. Locking the tier here prevents a future
    refactor from accidentally promoting it back to quick.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "x", "trigger": "hover"}]
    }))
    plan = _run_verification_plan(ref)
    entry = next(c for c in plan["requiredChecks"] if c["id"] == "spec-implementation-coverage")
    assert entry["tier"] == "standard", f"expected standard, got {entry['tier']!r}"


def test_spec_implementation_coverage_fails_when_motion_missing(tmp_path: Path) -> None:
    """The script must exit non-zero when an entry's selector is matched in
    impl source but the matched file contains no motion declaration.

    Reproduces the silent-killer: spec author wrote a scroll-driven entry,
    transition-spec-coverage passes (selector hits the impl), but the impl
    component returns a static element with no transition / animation /
    framer-motion / useScroll / IntersectionObserver wiring.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-fade", "trigger": "scroll", "type": "scroll-driven", "selector": ".hero"}]
    }))
    (impl / "src" / "Hero.tsx").write_text(
        "export function Hero() { return <section className=\"hero\">static</section>; }\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["presenceOnly"] == 1


def test_spec_implementation_coverage_passes_when_motion_declared(tmp_path: Path) -> None:
    """The script must exit 0 when every covered entry's matched file has at
    least one motion-declaration keyword (transition / framer-motion / useScroll
    / IntersectionObserver / animate-* / etc.).

    Catches the inverse failure mode: a too-strict matcher would false-fail
    valid impls and force callers to disable the gate. The needle list in
    spec-implementation-coverage.sh is intentionally permissive so common
    framer-motion + Tailwind impls register without configuration.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-fade", "trigger": "scroll", "type": "scroll-driven", "selector": ".hero"}]
    }))
    (impl / "src" / "Hero.tsx").write_text(
        "import { useScroll, useTransform } from \"framer-motion\";\n"
        "export function Hero() {\n"
        "  const { scrollYProgress } = useScroll();\n"
        "  const opacity = useTransform(scrollYProgress, [0, 1], [0, 1]);\n"
        "  return <section className=\"hero\">animated</section>;\n"
        "}\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["withMotion"] == 1


def test_spec_implementation_coverage_fails_marker_only_trigger_hooks(tmp_path: Path) -> None:
    """Loop-56 regression: hidden marker strings and generic useScroll text
    must not count as real trigger-specific transition implementations.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src" / "app").mkdir(parents=True)
    (impl / "src" / "components").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "page-load-reveal", "trigger": "load", "type": "reveal", "selector": "main section"},
            {"id": "smooth-scroll-lenis", "trigger": "scroll", "type": "scroll", "selector": "html"},
            {"id": "nav-dot-hover", "trigger": "hover", "type": "hover", "selector": ".nav_dot_button__kZB4V"},
            {"id": "faq-click-state", "trigger": "click", "type": "accordion", "selector": "section"},
        ]
    }))
    (impl / "src" / "app" / "page.tsx").write_text(
        "export default function Page() {\n"
        "  return <main data-transition=\"page-load-reveal smooth-scroll-lenis nav-dot-hover faq-click-state\">\n"
        "    <section data-scroll-hook=\"Lenis useScroll scroll(\" data-hover-hook=\":hover onPointerEnter\">static</section>\n"
        "  </main>;\n"
        "}\n"
    )
    (impl / "src" / "components" / "TransitionHooks.tsx").write_text(
        "export function TransitionHooks() {\n"
        "  const hooks = [\n"
        "    'page-load-reveal',\n"
        "    'smooth-scroll-lenis',\n"
        "    'nav-dot-hover',\n"
        "    'faq-click-state',\n"
        "    'main section',\n"
        "    '.nav_dot_button__kZB4V',\n"
        "    'Lenis',\n"
        "    'useScroll',\n"
        "    ':hover',\n"
        "    'onPointerEnter',\n"
        "  ];\n"
        "  return <span hidden data-transition-hooks={hooks.join(' ')} />;\n"
        "}\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["presenceOnly"] == 4
    assert artifact["markerOnly"] == 4


def test_spec_implementation_coverage_fails_unrelated_generic_motion(tmp_path: Path) -> None:
    """A generic motion hook in a matched file must not satisfy a different
    trigger family such as click/accordion.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "faq-click-state",
            "trigger": "click",
            "type": "accordion",
            "selector": ".faq",
        }]
    }))
    (impl / "src" / "Faq.tsx").write_text(
        "import { useScroll } from 'framer-motion';\n"
        "export function Faq() {\n"
        "  const scroll = useScroll();\n"
        "  return <section className=\"faq\" data-scroll={String(scroll)}>static</section>;\n"
        "}\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["triggerStatic"] == 1


def test_spec_implementation_coverage_passes_trigger_specific_impls(tmp_path: Path) -> None:
    """Trigger-specific implementations should pass without relying on
    unrelated generic motion keywords.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "nav-dot-hover", "trigger": "hover", "type": "hover", "selector": ".nav-dot"},
            {"id": "faq-click-state", "trigger": "click", "type": "accordion", "selector": ".faq"},
            {"id": "smooth-scroll-lenis", "trigger": "scroll", "type": "smooth-scroll", "selector": "html"},
        ]
    }))
    (impl / "src" / "Interactions.tsx").write_text(
        "import Lenis from 'lenis';\n"
        "import { useState } from 'react';\n"
        "export function Interactions() {\n"
        "  const [open, setOpen] = useState(false);\n"
        "  const lenis = new Lenis({ smoothWheel: true });\n"
        "  return <main>\n"
        "    <button className=\"nav-dot transition-transform hover:scale-105\" onPointerEnter={() => lenis.raf(performance.now())}>dot</button>\n"
        "    <section className=\"faq\" aria-expanded={open} onClick={() => setOpen(!open)} style={{ transition: 'height .3s' }}>faq</section>\n"
        "  </main>;\n"
        "}\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["triggerStatic"] == 0


def test_spec_implementation_coverage_fails_scroll_scrub_css_only(tmp_path: Path) -> None:
    """Loop-55 regression: a scroll-scrub entry must not pass just because
    the selector appears next to a CSS transition. Pinned scrollytelling needs
    a scroll progress source and a sticky/pin structure.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "line-pin",
            "trigger": "scroll",
            "type": "scroll-scrub",
            "selector": ".line",
        }]
    }))
    (impl / "src" / "Line.tsx").write_text(
        "export function Line() {\n"
        "  return <section className=\"line\" style={{ transition: 'opacity .45s, transform .45s' }}>static</section>;\n"
        "}\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["scrollScrubStatic"] == 1


def test_spec_implementation_coverage_passes_scroll_scrub_with_progress_and_pin(tmp_path: Path) -> None:
    """scroll-scrub passes when matched source has both scroll progress wiring
    and sticky/pin structure.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "line-pin",
            "trigger": "scroll",
            "type": "scroll-scrub",
            "selector": ".line",
        }]
    }))
    (impl / "src" / "Line.tsx").write_text(
        "import { useScroll, useTransform } from \"framer-motion\";\n"
        "export function Line() {\n"
        "  const { scrollYProgress } = useScroll();\n"
        "  const opacity = useTransform(scrollYProgress, [0, 1], [0, 1]);\n"
        "  return <section className=\"line\" style={{ position: 'sticky', top: 0, opacity }}>animated</section>;\n"
        "}\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["scrollScrubStatic"] == 0


def test_spec_implementation_coverage_fails_intersection_reveal_css_only(tmp_path: Path) -> None:
    """Intersection reveal needs viewport/observer wiring. A CSS transition on
    the selector is only a style declaration, not an in-view implementation.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "pyramid-reveal",
            "trigger": "intersection",
            "type": "intersection-reveal",
            "selector": ".pyramid",
        }]
    }))
    (impl / "src" / "Pyramid.tsx").write_text(
        "export function Pyramid() {\n"
        "  return <section className=\"pyramid\" style={{ transition: 'opacity .45s, transform .45s' }}>static</section>;\n"
        "}\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["intersectionStatic"] == 1


def test_spec_implementation_coverage_fails_intersection_reveal_data_attr_css_only(tmp_path: Path) -> None:
    """A data-in-view CSS state is not observer wiring by itself."""
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "pyramid-reveal",
            "trigger": "intersection",
            "type": "intersection-reveal",
            "selector": ".pyramid",
        }]
    }))
    (impl / "src" / "styles.css").write_text(
        ".pyramid { transition: opacity .45s, transform .45s; }\n"
        ".pyramid[data-in-view=\"true\"] { opacity: 1; transform: none; }\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["intersectionStatic"] == 1


def test_spec_implementation_coverage_passes_intersection_reveal_with_observer(tmp_path: Path) -> None:
    """Intersection reveal passes when matched source has viewport observer
    wiring.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "pyramid-reveal",
            "trigger": "intersection",
            "type": "intersection-reveal",
            "selector": ".pyramid",
        }]
    }))
    (impl / "src" / "Pyramid.tsx").write_text(
        "export function Pyramid() {\n"
        "  const observer = new IntersectionObserver(() => {});\n"
        "  return <section className=\"pyramid\" style={{ transition: 'opacity .45s, transform .45s' }}>animated</section>;\n"
        "}\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["intersectionStatic"] == 0


# ── verification-plan tier filtering ──
#
# Tier system: quick < standard < comprehensive. Each add_check is tagged with
# a min_tier; only checks at or below the active tier are emitted. Default tier
# is comprehensive to preserve prior unconditional behavior — quick/standard
# are opt-in cost reductions for iteration loops.


def _fixture_all_signals(ref: Path) -> None:
    """Write extraction artifacts that fire every conditional signal so the
    dispatch produces one of every check type."""
    (ref / "external-sdks.json").write_text(json.dumps({
        "detected": ["useScroll", "scrollYProgress"]
    }))
    (ref / "interactions-detected.json").write_text(json.dumps({
        "interactions": [{"trigger": "hover", "target": ".btn"}]
    }))
    (ref / "regions.json").write_text(json.dumps({
        "click": [{"name": "tabs", "triggerType": "click-cycle", "selector": ".tab"}]
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "x", "trigger": "hover"}]
    }))
    (ref / "animation-runtime-dump.json").write_text(json.dumps({
        "scrollTrigger": [{"start": 0}]
    }))
    (ref / "paid-features.json").write_text(json.dumps({
        "paidFonts": [{"family": "Foo", "cdn": "use.typekit.net", "decision": None}]
    }))


def test_verification_plan_default_tier_is_comprehensive(tmp_path: Path) -> None:
    """Default (no --tier flag, no env) must produce a comprehensive plan to
    preserve unconditional-dispatch behavior from before the tier system.

    Backward-compat lock: any change that flips this default needs a
    documented reason and a migration note for downstream consumers.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _fixture_all_signals(ref)
    plan = _run_verification_plan(ref)
    assert plan["tier"] == "comprehensive"


def test_verification_plan_quick_tier_filters_to_static_checks(tmp_path: Path) -> None:
    """tier=quick must emit only the static / JSON-comparison checks.

    Static-only set (with all signals firing): hydration-check,
    tailwind-transform-conflict, transition-spec-coverage, runtime-spec-coverage,
    plus the Fix 8 anti-fabrication gates (text-fidelity-check, dom-mirror-check)
    and proxy-mirror-check, which blocks original-runtime proxy/cache mirrors;
    plus the loop-9 ref-screenshot-asset anti-cheat (static filesystem scan +
    sha256 fingerprint of impl tree vs ref's captured screenshot dirs);
    which are pure static AST/tree comparison — no browser, no LLM, no IO.
    Everything else (one-shot browser + 60fps video) must be filtered out.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _fixture_all_signals(ref)
    plan = _run_verification_plan(ref, tier="quick")
    assert plan["tier"] == "quick"
    ids = {c["id"] for c in plan["requiredChecks"]}
    assert ids == {
        "hydration-check",
        "tailwind-transform-conflict",
        "transition-spec-coverage",
        "runtime-spec-coverage",
        "text-fidelity-check",
        "dom-mirror-check",
        "proxy-mirror-check",
        "ref-screenshot-asset",
        # Loop-9 family A1/A2/A3 anti-cheat (static):
        "entry-coherence",
        "scaffold-residue",
        "html-paste",
        # Loop-9 family A5 (static CSS mirror):
        "css-mirror",
        # Loop-9 fix #4 — explicit invalidation stamp:
        "invalidation",
        # Signal 1 — scaffold-warn placeholders:
        "scaffold-warn",
        # Diagnosis B — required-media coverage (dispatched
        # unconditionally; script self-skips when ref has no required
        # video/Lottie/SVG):
        "required-media-coverage",
        # Codex-2 findings — monolithic-impl + motion-coverage:
        "monolithic-impl",
        "motion-coverage",
        # scroll-engine-parity — engine class match (Lenis / GSAP
        # ScrollTrigger / scroll-scrub / scroll-pin):
        "scroll-engine-parity",
        # 2026-05-22 retune (user direction A) — hero-composite spot-check
        # replaces dom-mirror's structural-enforcement role. Verifies the
        # 4-element hero pattern (video + button + h1/h2 + label) which
        # LLMs consistently flatten away. Pure static (regex over impl
        # source + structure.json walk), so tier=quick.
        "hero-composite-check",
        # 2026-05-22 codex-rescue (a125b997) — composite roll-ups +
        # ref-js-loader anti-cheat. All three are pure file IO (rollups
        # read existing artifacts; loader does static grep on impl
        # source) so they belong in tier=quick.
        "runtime-proof",
        "transition-proof",
        "ref-js-loader",
        # 2026-05-22 user observation (gate-cheat block) — impl-scope
        # guard runs `git diff` only, no browser. tier=quick.
        "impl-scope",
        # 2026-05-22 codex-rescue grounding audit — color-token diff
        # against ref palette. Pure regex + math, no browser → quick.
        "color-token-grounding",
        # 2026-05-22 user request — duration/easing grounding. Pure
        # source scan, no browser → quick.
        "duration-easing-grounding",
    }, f"quick tier emitted unexpected ids: {ids}"
    # Every emitted check must be tagged tier=quick.
    tiers = {c["tier"] for c in plan["requiredChecks"]}
    assert tiers == {"quick"}, f"quick plan contains non-quick tiers: {tiers}"


def test_verification_plan_standard_tier_drops_video_checks(tmp_path: Path) -> None:
    """tier=standard must include quick + standard but drop comprehensive-tier
    checks (60fps video recording rows).

    Comprehensive-only ids that MUST be absent: video-motion-compare,
    hover-state-compare, click-state-compare. These each spin up two browser
    sessions and record ~5-10s of video per signal — the cost class the tier
    system exists to gate.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _fixture_all_signals(ref)
    plan = _run_verification_plan(ref, tier="standard")
    assert plan["tier"] == "standard"
    ids = {c["id"] for c in plan["requiredChecks"]}
    for forbidden in ("video-motion-compare", "hover-state-compare", "click-state-compare"):
        assert forbidden not in ids, f"{forbidden} should be filtered at standard: {ids}"
    # Standard tier should still keep the cheap quick checks.
    assert {"hydration-check", "tailwind-transform-conflict"}.issubset(ids)
    # No emitted check is tagged comprehensive.
    tiers = {c["tier"] for c in plan["requiredChecks"]}
    assert "comprehensive" not in tiers, f"standard plan leaked comprehensive: {tiers}"


def test_verification_plan_comprehensive_tier_emits_all_checks(tmp_path: Path) -> None:
    """tier=comprehensive must reproduce the prior unconditional dispatch.

    With every signal firing, comprehensive should include every check id the
    dispatcher can emit. This guards against accidentally adding a new
    add_check row that gets tagged at a higher (nonexistent) tier and silently
    falls out of the comprehensive plan.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _fixture_all_signals(ref)
    plan = _run_verification_plan(ref, tier="comprehensive")
    assert plan["tier"] == "comprehensive"
    ids = {c["id"] for c in plan["requiredChecks"]}
    expected = {
        "hydration-check",
        "tailwind-transform-conflict",
        "scroll-end-completion",
        "video-motion-compare",
        "transition-compare",
        "hover-state-compare",
        "click-state-compare",
        "transition-spec-coverage",
        "runtime-spec-coverage",
    }
    missing = expected - ids
    assert not missing, f"comprehensive plan missing expected checks: {missing}"


def test_verification_plan_rejects_invalid_tier(tmp_path: Path) -> None:
    """An unknown --tier value must exit non-zero with a clear error.

    Surfacing a typo (e.g. --tier=quik) at script time prevents silent
    degradation where every check falls below the bogus level and the plan
    ships with `requiredChecks: []`.
    """
    import subprocess
    ref = tmp_path / "ref"
    ref.mkdir()
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), "--tier=quik"],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode != 0
    assert "invalid --tier" in proc.stderr.lower() or "invalid --tier" in proc.stdout.lower()


# ── gate_boundary ──


def test_gate_boundary_fails_when_artifact_missing(tmp_path: Path) -> None:
    """gate_boundary must fail when responsive/boundary-collisions.json is absent."""
    ref = tmp_path / "ref"
    ref.mkdir()

    gate = Gate(ref)
    results = gate.gate_boundary()
    failures = [r for r in results if r.status == "fail"]
    assert any("boundary-collisions.json" in r.message for r in failures), (
        "Missing boundary-collisions.json must produce a fail in gate_boundary"
    )


def test_gate_boundary_passes_when_array_empty(tmp_path: Path) -> None:
    """gate_boundary must pass when the artifact exists and is `[]` (no collisions)."""
    ref = tmp_path / "ref"
    (ref / "responsive").mkdir(parents=True)
    (ref / "responsive" / "boundary-collisions.json").write_text("[]")

    gate = Gate(ref)
    results = gate.gate_boundary()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"empty array must pass gate_boundary: {failures}"
    assert any("No breakpoint collisions" in r.message for r in results)


def test_gate_boundary_fails_when_collisions_present(tmp_path: Path) -> None:
    """gate_boundary must fail when the array has at least one finding."""
    ref = tmp_path / "ref"
    (ref / "responsive").mkdir(parents=True)
    (ref / "responsive" / "boundary-collisions.json").write_text(
        json.dumps([{"bp": 768, "reasons": ["isolated overflow spike"]}])
    )

    gate = Gate(ref)
    results = gate.gate_boundary()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "non-empty boundary-collisions.json must fail gate_boundary"
    assert any("768" in r.message for r in failures)


def test_gate_boundary_fails_when_artifact_invalid_json(tmp_path: Path) -> None:
    """gate_boundary must fail when the artifact is not valid JSON."""
    ref = tmp_path / "ref"
    (ref / "responsive").mkdir(parents=True)
    (ref / "responsive" / "boundary-collisions.json").write_text("{not json")

    gate = Gate(ref)
    results = gate.gate_boundary()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "invalid JSON must fail gate_boundary"


def test_gate_boundary_fails_when_artifact_not_array(tmp_path: Path) -> None:
    """gate_boundary must fail when the artifact is JSON but not an array."""
    ref = tmp_path / "ref"
    (ref / "responsive").mkdir(parents=True)
    (ref / "responsive" / "boundary-collisions.json").write_text('{"bp": 768}')

    gate = Gate(ref)
    results = gate.gate_boundary()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "non-array JSON must fail gate_boundary"


# ── gate_font_parity ──


def test_gate_font_parity_fails_when_artifact_missing(tmp_path: Path) -> None:
    """gate_font_parity must fail when font-parity.json is absent."""
    ref = tmp_path / "ref"
    ref.mkdir()

    gate = Gate(ref)
    results = gate.gate_font_parity()
    failures = [r for r in results if r.status == "fail"]
    assert any("font-parity.json" in r.message for r in failures), (
        "Missing font-parity.json must fail gate_font_parity"
    )


def test_gate_font_parity_passes_when_match(tmp_path: Path) -> None:
    """gate_font_parity must pass when parity is 'match'."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "font-parity.json").write_text(
        json.dumps(
            {"ref": {"family": "Inter"}, "impl": {"family": "Inter"}, "parity": "match"}
        )
    )

    gate = Gate(ref)
    results = gate.gate_font_parity()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"match must pass: {failures}"


def test_gate_font_parity_fails_when_mismatch_undeclared(tmp_path: Path) -> None:
    """gate_font_parity must fail when parity is 'mismatch' and asset-substitution.json is absent."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "font-parity.json").write_text(
        json.dumps(
            {"ref": {"family": "Exat"}, "impl": {"family": "Roboto Flex"}, "parity": "mismatch"}
        )
    )

    gate = Gate(ref)
    results = gate.gate_font_parity()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "undeclared mismatch must fail"
    assert any("Exat" in r.message and "Roboto Flex" in r.message for r in failures)


def test_gate_font_parity_passes_when_mismatch_declared(tmp_path: Path) -> None:
    """gate_font_parity must pass when mismatch is declared in asset-substitution.json."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "font-parity.json").write_text(
        json.dumps(
            {"ref": {"family": "Exat"}, "impl": {"family": "Roboto Flex"}, "parity": "mismatch"}
        )
    )
    (ref / "asset-substitution.json").write_text(
        json.dumps(
            {
                "fonts": [
                    {"original": "Exat", "replacement": "Roboto Flex", "reason": "license"}
                ],
                "structuralOnlySections": ["*"],
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_font_parity()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"declared mismatch must pass: {failures}"


def test_gate_font_parity_fails_when_substitution_has_empty_fonts(tmp_path: Path) -> None:
    """gate_font_parity must fail when asset-substitution.json exists but fonts[] is empty."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "font-parity.json").write_text(
        json.dumps(
            {"ref": {"family": "Exat"}, "impl": {"family": "Roboto Flex"}, "parity": "mismatch"}
        )
    )
    (ref / "asset-substitution.json").write_text(json.dumps({"fonts": [], "images": []}))

    gate = Gate(ref)
    results = gate.gate_font_parity()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "empty fonts[] must still fail"


def test_gate_font_parity_fails_when_impl_declared_but_not_loaded(tmp_path: Path) -> None:
    """gate_font_parity must catch the silent-fallback case: same family declared but impl FontFace failed to load."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "font-parity.json").write_text(
        json.dumps(
            {
                "ref": {"family": "Exat", "loaded": True},
                "impl": {"family": "Exat", "loaded": False},
                "parity": "match",
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_font_parity()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "match parity but impl unloaded must fail"
    assert any("NOT actually loaded" in r.message or "not actually loaded" in r.message.lower() for r in failures)


def test_gate_font_parity_passes_when_both_loaded(tmp_path: Path) -> None:
    """gate_font_parity must pass when both ref and impl have loaded:true."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "font-parity.json").write_text(
        json.dumps(
            {
                "ref": {"family": "Inter", "loaded": True},
                "impl": {"family": "Inter", "loaded": True},
                "parity": "match",
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_font_parity()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"both loaded must pass: {failures}"


def test_gate_font_parity_passes_when_loaded_field_missing(tmp_path: Path) -> None:
    """Backward compat: older font-parity.json without `loaded` field still passes on match."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "font-parity.json").write_text(
        json.dumps(
            {"ref": {"family": "Inter"}, "impl": {"family": "Inter"}, "parity": "match"}
        )
    )

    gate = Gate(ref)
    results = gate.gate_font_parity()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, "missing loaded field defaults to True (backward compat)"


def test_gate_font_parity_fails_when_invalid_parity_value(tmp_path: Path) -> None:
    """gate_font_parity must fail when `parity` is not 'match' or 'mismatch'."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "font-parity.json").write_text(json.dumps({"parity": "unknown"}))

    gate = Gate(ref)
    results = gate.gate_font_parity()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "unknown parity value must fail"


# ── gate_paid_features ──


def test_gate_paid_features_fails_when_artifact_missing(tmp_path: Path) -> None:
    """gate_paid_features must fail when paid-features.json is absent."""
    ref = tmp_path / "ref"
    ref.mkdir()

    gate = Gate(ref)
    results = gate.gate_paid_features()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "Missing paid-features.json must fail gate_paid_features"
    assert any("paid-features.json" in r.message for r in failures)


def test_gate_paid_features_passes_when_no_findings(tmp_path: Path) -> None:
    """gate_paid_features must pass when paidFonts is empty."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "paid-features.json").write_text(json.dumps({"paidFonts": []}))

    gate = Gate(ref)
    results = gate.gate_paid_features()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"empty findings must pass: {failures}"


def test_gate_paid_features_fails_when_decision_is_null(tmp_path: Path) -> None:
    """gate_paid_features must fail when any paid font has decision=null."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "paid-features.json").write_text(
        json.dumps(
            {
                "paidFonts": [
                    {
                        "family": None,
                        "cdn": "use.typekit.net",
                        "evidence": "css/main.css:1",
                        "decision": None,
                    }
                ],
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_paid_features()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "decision=null must fail"
    assert any("use.typekit.net" in r.message for r in failures)


def test_gate_paid_features_passes_when_decisions_set(tmp_path: Path) -> None:
    """gate_paid_features must pass once every paid font has a valid decision."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "paid-features.json").write_text(
        json.dumps(
            {
                "paidFonts": [
                    {
                        "family": None,
                        "cdn": "use.typekit.net",
                        "evidence": "css/main.css:1",
                        "decision": "substitute",
                    },
                    {
                        "family": None,
                        "cdn": "fast.fonts.net",
                        "evidence": "head.json:1",
                        "decision": "use",
                    },
                ],
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_paid_features()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"valid decisions must pass: {failures}"


def test_gate_paid_features_fails_when_decision_invalid(tmp_path: Path) -> None:
    """gate_paid_features must fail when decision is not in {use, substitute, skip}."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "paid-features.json").write_text(
        json.dumps(
            {
                "paidFonts": [
                    {
                        "family": None,
                        "cdn": "p.typekit.net",
                        "evidence": "css/main.css:7",
                        "decision": "yes",
                    }
                ],
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_paid_features()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "invalid decision value must fail"
    assert any("p.typekit.net" in r.message for r in failures)


def test_gate_paid_features_fails_when_partial_decisions(tmp_path: Path) -> None:
    """gate_paid_features must fail if even one paid font has decision=null among many."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "paid-features.json").write_text(
        json.dumps(
            {
                "paidFonts": [
                    {
                        "family": None,
                        "cdn": "use.typekit.net",
                        "evidence": "css/a.css:1",
                        "decision": "use",
                    },
                    {
                        "family": None,
                        "cdn": "fast.fonts.net",
                        "evidence": "css/b.css:2",
                        "decision": None,
                    },
                ],
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_paid_features()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "any null decision must fail the gate"


# ── gate_spec ↔ paid-features cross-validation (font substitution) ──


def _write_min_spec_artifacts(ref: Path, transitions: list[dict] | None = None) -> None:
    """Write the minimum artifacts gate_spec needs so we can exercise the
    cross-validation branch without satisfying every other check."""
    (ref / "bundle-map.json").write_text(json.dumps({}))
    (ref / "external-sdks.json").write_text(json.dumps({}))
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": transitions or []})
    )


def test_gate_spec_passes_when_no_substitute_decisions(tmp_path: Path) -> None:
    """No paid-features.json (or no substitute decisions) → cross-check is silent."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_min_spec_artifacts(ref)
    (ref / "paid-features.json").write_text(
        json.dumps(
            {
                "paidFonts": [
                    {
                        "family": None,
                        "cdn": "use.typekit.net",
                        "evidence": "css/main.css:1",
                        "decision": "use",
                    }
                ]
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_spec()
    sub_failures = [
        r
        for r in results
        if r.status == "fail" and "paid-font substitution" in r.label
    ]
    assert not sub_failures, (
        f"decision=use must not trigger substitution failure: {sub_failures}"
    )


def test_gate_spec_fails_when_substitute_but_no_asset_substitution_json(tmp_path: Path) -> None:
    """decision='substitute' without asset-substitution.json must fail at spec time
    (otherwise font-parity discovers it much later, after generation)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_min_spec_artifacts(ref)
    (ref / "paid-features.json").write_text(
        json.dumps(
            {
                "paidFonts": [
                    {
                        "family": None,
                        "cdn": "use.typekit.net",
                        "evidence": "css/main.css:1",
                        "decision": "substitute",
                    }
                ]
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_spec()
    failures = [
        r
        for r in results
        if r.status == "fail" and "paid-font substitution" in r.label
    ]
    assert failures, "substitute without asset-substitution.json must fail"
    assert any("use.typekit.net" in r.message for r in failures)


def test_gate_spec_fails_when_asset_substitution_has_no_fonts(tmp_path: Path) -> None:
    """asset-substitution.json present but missing fonts[] → still fail."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_min_spec_artifacts(ref)
    (ref / "paid-features.json").write_text(
        json.dumps(
            {
                "paidFonts": [
                    {
                        "family": None,
                        "cdn": "fast.fonts.net",
                        "evidence": "css/main.css:7",
                        "decision": "substitute",
                    }
                ]
            }
        )
    )
    # Has images but no fonts — schema allows other categories
    (ref / "asset-substitution.json").write_text(
        json.dumps({"images": [{"from": "a", "to": "b"}]})
    )

    gate = Gate(ref)
    results = gate.gate_spec()
    failures = [
        r
        for r in results
        if r.status == "fail" and "paid-font substitution" in r.label
    ]
    assert failures, "asset-substitution.json without fonts[] must fail"


def test_gate_spec_passes_when_substitute_and_fonts_declared(tmp_path: Path) -> None:
    """substitute + asset-substitution.json with fonts[] → pass."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_min_spec_artifacts(ref)
    (ref / "paid-features.json").write_text(
        json.dumps(
            {
                "paidFonts": [
                    {
                        "family": None,
                        "cdn": "use.typekit.net",
                        "evidence": "css/main.css:1",
                        "decision": "substitute",
                    }
                ]
            }
        )
    )
    (ref / "asset-substitution.json").write_text(
        json.dumps(
            {
                "fonts": [
                    {"from": "Adobe Garamond Pro", "to": "EB Garamond", "reason": "paid"}
                ]
            }
        )
    )
    # Loop-38 fix: substitute decision is only valid AFTER a download attempt.
    # download-log.json must record at least one URL matching the family/CDN.
    (ref / "download-log.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "attempts": [
                    {
                        "url": "https://use.typekit.net/k/whatever-garamond.woff2",
                        "status": 403,
                        "error": "license-blocked",
                    }
                ],
                "succeeded": 0,
                "failed": 1,
                "totalAttempted": 1,
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_spec()
    failures = [
        r
        for r in results
        if r.status == "fail" and "paid-font substitution" in r.label
    ]
    assert not failures, f"declared substitute must pass: {failures}"
    sub_pass = [r for r in results if r.label == "paid-font substitution"]
    assert sub_pass and sub_pass[0].status == "pass"


def test_gate_spec_fails_when_substitute_without_download_attempt(tmp_path: Path) -> None:
    """Loop-38 regression: paid font marked decision='substitute' with
    asset-substitution.json declaration but ZERO download attempt in
    download-log.json must FAIL. Research-mode policy enforced.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_min_spec_artifacts(ref)
    (ref / "paid-features.json").write_text(
        json.dumps(
            {
                "paidFonts": [
                    {
                        "family": None,
                        "cdn": "Die Grotesk",
                        "evidence": "css/variables.txt:38",
                        "decision": "substitute",
                        "substituteFamily": "Inter Variable",
                    }
                ]
            }
        )
    )
    (ref / "asset-substitution.json").write_text(
        json.dumps(
            {"fonts": [{"from": "Die Grotesk", "to": "Inter Variable"}]}
        )
    )
    (ref / "download-log.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "attempts": [
                    # 46 image attempts — no font URL anywhere
                    {"url": "https://cdn.example/hero.png", "status": 200},
                    {"url": "https://cdn.example/logo.svg", "status": 200},
                ],
                "succeeded": 2,
                "failed": 0,
                "totalAttempted": 2,
            }
        )
    )
    gate = Gate(ref)
    results = gate.gate_spec()
    fail_labels = [r.label for r in results if r.status == "fail"]
    assert any(
        "download attempt missing" in label for label in fail_labels
    ), f"loop-38 regression — must fail without download attempt: {fail_labels}"


def test_gate_spec_skips_substitution_check_when_no_paid_features_json(tmp_path: Path) -> None:
    """No paid-features.json → no substitution check runs (paid-features gate
    would block first; here we just verify spec stays silent)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_min_spec_artifacts(ref)
    # No paid-features.json written

    gate = Gate(ref)
    results = gate.gate_spec()
    sub_results = [r for r in results if "paid-font substitution" in r.label]
    assert sub_results == [], "no paid-features.json → no substitution check"


# ── gate_section_compare ──


def test_gate_section_compare_fails_when_result_txt_missing(tmp_path: Path) -> None:
    """gate_section_compare must fail when sections/result.txt does not exist."""
    ref = tmp_path / "ref"
    ref.mkdir()
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "Missing result.txt must produce a fail result"
    assert any("result.txt" in r.message or "result.txt" in r.fix for r in failures)
    combined_output = " ".join(f"{r.message} {r.fix}" for r in failures)
    assert "skills/visual-debug/scripts/section-compare.sh" in combined_output
    assert "MISSING (visual-debug/scripts/section-compare.sh" not in combined_output


def test_gate_section_compare_passes_when_all_sections_pass(tmp_path: Path) -> None:
    """gate_section_compare must pass when result.txt has only ✅ lines."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text("| Hero | ✅ PASS | 97% |\n| Footer | ✅ PASS | 99% |\n")
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"All-pass result.txt must not produce failures: {failures}"


def test_gate_section_compare_fails_when_section_failed(tmp_path: Path) -> None:
    """gate_section_compare must fail when result.txt contains ❌."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text("| Hero | ❌ FAIL | 55% |\n| Footer | ✅ PASS | 99% |\n")
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "❌ in result.txt must produce a fail result"
    assert any("FAILED" in r.message or "section" in r.message.lower() for r in failures)


def test_gate_section_compare_fails_when_section_missing(tmp_path: Path) -> None:
    """gate_section_compare must fail when result.txt contains ⚠️ MISSING impl."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text("| Hero | ✅ PASS | 97% |\n| Nav | ⚠️ MISSING impl |\n")
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "MISSING impl in result.txt must produce a fail result"


def test_gate_section_compare_caps_structural_only_ratio(tmp_path: Path) -> None:
    """Regression — 5199dd9 benchmark shipped a 9-section page with ALL 9
    marked STRUCTURAL_ONLY via asset-substitution.json. Cap is 50%.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    rows = "\n".join(
        f"| sec-{i} | — | — | substituted | 🔁 STRUCTURAL_ONLY |"
        for i in range(9)
    )
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---|---|---|---|---|\n"
        + rows + "\n"
        "\n**Result: 9 PASS, 0 FAIL, 0 SKIP, 9 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    gate = Gate(ref)
    failures = [r for r in gate.gate_section_compare() if r.status == "fail"]
    assert any(r.label == "structural-only excess" for r in failures), (
        f"100% STRUCTURAL_ONLY must fail; got: {[(r.label, r.status) for r in failures]}"
    )
    assert any("9/9" in r.message and "100%" in r.message for r in failures)


def test_gate_section_compare_allows_minority_structural_only(tmp_path: Path) -> None:
    """Counterpart: a handful of substituted sections (commercial fonts etc)
    is legitimate. 2/9 (22%) should pass — below the 50% cap.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    pass_rows = "\n".join(
        f"| sec-{i} | 100 | 50 | ok | ✅ |"
        for i in range(7)
    )
    subst_rows = "\n".join(
        f"| sec-sub-{i} | — | — | substituted | 🔁 STRUCTURAL_ONLY |"
        for i in range(2)
    )
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---|---|---|---|---|\n"
        + pass_rows + "\n" + subst_rows + "\n"
        "\n**Result: 9 PASS, 0 FAIL, 0 SKIP, 2 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    gate = Gate(ref)
    failures = [r for r in gate.gate_section_compare() if r.status == "fail"]
    assert not any(r.label == "structural-only excess" for r in failures)


def test_gate_section_compare_detects_threshold_gaming(tmp_path: Path) -> None:
    """Regression — d19e28d benchmark agent set SECTION_THRESHOLD=250000
    so AE/Mpx 88823 + 228325 (both nominally `critical` >20000) got
    re-classified as `minor` with ✅ PASS. New check: any row labeled
    ok/minor with AE/Mpx > 2000 (canonical bound) is flagged as gaming.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---|---|---|---|---|\n"
        "| footer | 279470 | 228325 | minor | ✅ |\n"
        "| section-0 | 109742 | 88823 | minor | ✅ |\n"
        "\n"
        "**Result: 2 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    gate = Gate(ref)
    failures = [r for r in gate.gate_section_compare() if r.status == "fail"]
    assert any(r.label == "section-threshold gaming" for r in failures), (
        f"inflated SECTION_THRESHOLD must surface as gaming fail: {failures}"
    )
    assert any("228325" in r.message for r in failures)
    assert any("ui_clone.measure" in (r.fix or "") for r in failures)


def test_gate_section_compare_accepts_legitimate_minor_under_threshold(tmp_path: Path) -> None:
    """Counterpart: `minor` rows with AE/Mpx ≤ 2000 (canonical default) are
    legit. Don't false-positive trip the gaming detector.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---|---|---|---|---|\n"
        "| hero | 5000 | 800 | minor | ✅ |\n"
        "| footer | 3000 | 1500 | minor | ✅ |\n",
        encoding="utf-8",
    )
    gate = Gate(ref)
    failures = [r for r in gate.gate_section_compare() if r.status == "fail"]
    assert not any(r.label == "section-threshold gaming" for r in failures)


def test_gate_section_compare_overrides_structural_only_on_critical_diff(tmp_path: Path) -> None:
    """Regression — STRUCTURAL_ONLY rows must NOT silent-pass when the same
    section has severity=critical in structure-diff.json. The realfood.gov
    benchmark shipped a 638px-tall impl against a 19954px ref and the gate
    still reported "All sections PASS" because asset-substitution flipped
    every section to STRUCTURAL_ONLY. This test locks the override in.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| section-0 | — | — | substituted | 🔁 STRUCTURAL_ONLY |\n"
        "| footer    | — | — | substituted | 🔁 STRUCTURAL_ONLY |\n"
        "\n"
        "**Result: 2 PASS, 0 FAIL, 0 SKIP, 2 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    (sections / "structure-diff.json").write_text(json.dumps([
        {
            "section": "section-0",
            "issues": [
                "DISPLAY_MISMATCH: ref=block, impl=flex",
                "HEIGHT_MISMATCH: ref=19954px, impl=638px (ratio=0.03)",
            ],
            "severity": "critical",
            "score": 0.867,
        }
    ]))
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert any(
        r.label == "structural-only critical override" for r in failures
    ), (
        "STRUCTURAL_ONLY with critical structure-diff must fail the gate; got: "
        f"{[(r.label, r.status, r.message) for r in results]}"
    )
    assert any("section-0" in r.message for r in failures), (
        "Failing section name must surface in the message"
    )


def test_gate_section_compare_overrides_structural_only_on_major_with_low_ratio(tmp_path: Path) -> None:
    """Regression — the 077d8c3 benchmark exposed `major` severity with
    HEIGHT_MISMATCH ratio=0.35 (impl is 35% of ref height) slipping past
    the `critical`-only guard. ratio<0.5 with severity=major means content
    is missing, not substituted — guard must catch it.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| section-0 | — | — | substituted | 🔁 STRUCTURAL_ONLY |\n"
        "**Result: 1 PASS, 0 FAIL, 0 SKIP, 1 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    (sections / "structure-diff.json").write_text(json.dumps([
        {
            "section": "section-0",
            "issues": [
                "HEIGHT_MISMATCH: ref=19954px, impl=6955px (ratio=0.35)",
            ],
            "severity": "major",
            "score": 0.363,
        }
    ]))
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert any(r.label == "structural-only critical override" for r in failures), (
        f"major + ratio=0.35 must fail STRUCTURAL_ONLY: {results}"
    )


def test_gate_section_compare_allows_major_with_acceptable_ratio(tmp_path: Path) -> None:
    """`major` severity with HEIGHT_MISMATCH ratio≥0.5 (impl reasonably close
    to ref height) — keep STRUCTURAL_ONLY PASS. The guard is targeted at
    "content disappeared," not "minor height delta."
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| section-0 | — | — | substituted | 🔁 STRUCTURAL_ONLY |\n",
        encoding="utf-8",
    )
    (sections / "structure-diff.json").write_text(json.dumps([
        {
            "section": "section-0",
            "issues": ["HEIGHT_MISMATCH: ref=1000px, impl=750px (ratio=0.75)"],
            "severity": "major",
            "score": 0.2,
        }
    ]))
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, (
        f"major with ratio≥0.5 must not fail STRUCTURAL_ONLY: {results}"
    )


def test_gate_section_compare_allows_structural_only_when_diff_not_critical(tmp_path: Path) -> None:
    """STRUCTURAL_ONLY rows still PASS when structure-diff.json carries
    only non-critical severities (warn / info) — the override is targeted
    at the layout-regression class, not at every minor structural delta.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| hero | — | — | substituted | 🔁 STRUCTURAL_ONLY |\n",
        encoding="utf-8",
    )
    (sections / "structure-diff.json").write_text(json.dumps([
        {"section": "hero", "issues": ["minor"], "severity": "warn", "score": 0.1}
    ]))
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, (
        f"Non-critical structure-diff must not fail STRUCTURAL_ONLY: {results}"
    )


def test_gate_section_compare_accessible_via_run(tmp_path: Path) -> None:
    """section-compare gate must be callable through Gate.run()."""
    ref = tmp_path / "ref"
    ref.mkdir()
    gate = Gate(ref)
    # No result.txt → BLOCKED (exit code 1)
    exit_code = gate.run("section-compare", json_output=True)
    assert exit_code == 1


_RESULT_TABLE_TEMPLATE = (
    "| Section | AE | AE/Mpx | Severity | Status |\n"
    "|---------|-----|--------|----------|--------|\n"
    "| hero    | 1500 | 1200 | critical | ❌ |\n"
    "| footer  | 30000 | 25000 | critical | ❌ |\n"
    "| nav     | 0 | 0 | ok | ✅ |\n"
)


def test_known_artifacts_downgrades_valid_entry(tmp_path: Path) -> None:
    """Valid known-artifacts entry downgrades matching ❌ to PASS in gate output."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(_RESULT_TABLE_TEMPLATE)
    (ref / "known-artifacts.json").write_text(json.dumps({
        "schemaVersion": 1,
        "sections": [
            {
                "name": "hero",
                "verifiedBy": "readPixels",
                "evidence": "frame match",
                "aeThresholdCeiling": 1800,
                "verifiedAt": "2026-05-11T00:00:00Z",
            }
        ],
    }))
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    # 'hero' downgraded → 1 effective fail (footer), not 2
    fail_msgs = " ".join(r.message for r in failures)
    assert "1 section(s) FAILED" in fail_msgs
    passes = [r for r in results if r.status == "pass"]
    assert any("downgraded" in r.message for r in passes)


def test_known_artifacts_rejects_entry_when_ae_grew(tmp_path: Path) -> None:
    """AE exceeds ceiling × 1.5 → entry rejected, FAIL stays."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(_RESULT_TABLE_TEMPLATE)
    (ref / "known-artifacts.json").write_text(json.dumps({
        "schemaVersion": 1,
        "sections": [
            {
                "name": "footer",
                "verifiedBy": "readPixels",
                "evidence": "frame match",
                "aeThresholdCeiling": 500,  # current 30000 ≫ 500 * 1.5
                "verifiedAt": "2026-05-11T00:00:00Z",
            }
        ],
    }))
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    warns = [r for r in results if r.status == "warn"]
    fail_msgs = " ".join(r.message for r in failures)
    assert "2 section(s) FAILED" in fail_msgs
    assert any("bug got worse" in r.message for r in warns)


def test_known_artifacts_rejects_missing_required_fields(tmp_path: Path) -> None:
    """Entry without `evidence`/`aeThresholdCeiling`/etc. → ignored, FAIL stays."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(_RESULT_TABLE_TEMPLATE)
    (ref / "known-artifacts.json").write_text(json.dumps({
        "schemaVersion": 1,
        "sections": [
            {"name": "hero", "verifiedBy": "readPixels"}  # missing fields
        ],
    }))
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    fail_msgs = " ".join(r.message for r in failures)
    assert "2 section(s) FAILED" in fail_msgs


def test_known_artifacts_rejects_unknown_verified_by(tmp_path: Path) -> None:
    """`verifiedBy` not in the allowed enum → ignored."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(_RESULT_TABLE_TEMPLATE)
    (ref / "known-artifacts.json").write_text(json.dumps({
        "schemaVersion": 1,
        "sections": [
            {
                "name": "hero",
                "verifiedBy": "vibes",
                "evidence": "looks fine",
                "aeThresholdCeiling": 9999,
                "verifiedAt": "2026-05-11T00:00:00Z",
            }
        ],
    }))
    gate = Gate(ref)
    results = gate.gate_section_compare()
    warns = [r for r in results if r.status == "warn"]
    assert any("unknown verifiedBy" in r.message for r in warns)


def test_known_artifacts_missing_keeps_legacy_behavior(tmp_path: Path) -> None:
    """No known-artifacts.json → existing FAIL counts unchanged."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(_RESULT_TABLE_TEMPLATE)
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    fail_msgs = " ".join(r.message for r in failures)
    assert "2 section(s) FAILED" in fail_msgs


def test_section_count_mismatch_warns(tmp_path: Path) -> None:
    """section-map totalCount=3 vs component-map sectionCount=0 must produce a warn."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"tag": "s1"}, {"tag": "s2"}, {"tag": "s3"}], "totalCount": 3})
    )
    (ref / "component-map.json").write_text(json.dumps({"sections": [], "sectionCount": 0}))
    gate = Gate(ref)
    results = gate._check_section_counts(
        json.loads((ref / "section-map.json").read_text()),
        json.loads((ref / "component-map.json").read_text()),
    )
    warns = [r for r in results if r.status == "warn" and "section count" in r.label.lower()]
    assert warns, "section-map=3 vs component-map=0 must produce a warn"


def test_section_count_both_zero_passes(tmp_path: Path) -> None:
    """section-map totalCount=0 vs component-map sectionCount=0 must pass (not silently skip)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    gate = Gate(ref)
    results = gate._check_section_counts(
        {"sections": [], "totalCount": 0},
        {"sections": [], "sectionCount": 0},
    )
    passes = [r for r in results if r.status == "pass" and "section count" in r.label.lower()]
    assert passes, "Both counts=0 must produce a pass result"


def test_valid_gates_matches_dispatch() -> None:
    """VALID_GATES must exactly match the gates handled by _dispatch."""
    from pathlib import Path

    gate = Gate(Path("/tmp"))
    for gate_name in VALID_GATES:
        if gate_name == "all":
            continue
        results = gate._dispatch(gate_name)
        assert isinstance(results, list), f"_dispatch('{gate_name}') must return a list"


# ── multi-viewport fan-out (hover-state / click-state) ──
#
# The fan-out is gated by the VIEWPORTS env var (comma-separated WxH list).
# Empty = single-viewport (back-compat, preserves cost for non-comprehensive
# callers). Non-empty = outer loop runs once per viewport, results land in
# per-WxH subdirs. We stub the inner video-transition-compare.sh because the
# real one needs agent-browser; the fan-out logic itself is what we're locking
# in. PLUGIN_ROOT is the well-known knob the script uses to resolve the inner
# compare path, so we redirect it at a tmp dir containing a no-op stub.


def _make_stub_compare(plugin_root: Path) -> None:
    """Write a video-transition-compare.sh stub that exits 0 immediately.

    The real script (scripts/verify/video-transition-compare.sh) launches two
    agent-browser sessions and records video — neither tractable in a unit
    test. The fan-out logic in hover/click-state-compare lives in the OUTER
    loop, so a no-op inner is enough to verify per-viewport dirs + result.txt
    sections are emitted correctly.
    """
    target = plugin_root / "scripts" / "verify" / "video-transition-compare.sh"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "#!/usr/bin/env bash\n"
        "# stub: no-op inner compare for unit tests\n"
        "echo \"[stub] called with: $*\"\n"
        "exit 0\n"
    )
    target.chmod(0o755)


def test_hover_state_compare_fans_out_per_viewport(tmp_path: Path) -> None:
    """VIEWPORTS=\"375x812,1920x1080\" → result.txt names both viewports and the
    per-viewport subdirs exist under transitions/hover-state/.

    Locks in the fan-out output layout: <ref-dir>/transitions/hover-state/
    <WxH>/<safe-name>/ — the per-viewport subdir is what lets diff inspection
    distinguish a desktop pass from a mobile fail (otherwise both write to
    the same target dir and the second clobbers the first).
    """
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{"name": "btn", "triggerType": "hover", "selector": ".btn"}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    _make_stub_compare(plugin_root)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {**os.environ, "PLUGIN_ROOT": str(plugin_root), "VIEWPORTS": "375x812,1920x1080"}
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert proc.returncode == 0, f"hover fan-out failed: {proc.stdout}\n{proc.stderr}"
    result = (ref / "transitions" / "hover-state-result.txt").read_text()
    assert "viewports: 375x812,1920x1080" in result
    assert "viewport: 375x812" in result
    assert "viewport: 1920x1080" in result
    assert "[375x812]" in result and "[1920x1080]" in result
    # Per-viewport subdirs must exist (target name is "btn" → safe name "btn")
    assert (ref / "transitions" / "hover-state" / "375x812" / "btn").is_dir()
    assert (ref / "transitions" / "hover-state" / "1920x1080" / "btn").is_dir()


def test_hover_state_compare_single_viewport_back_compat(tmp_path: Path) -> None:
    """VIEWPORTS unset → no per-viewport subdir, no per-viewport line — current
    behavior preserved bit-for-bit so single-tier callers see no cost increase.

    Critical regression guard: the fan-out was an additive capability, NOT a
    coverage upgrade for existing callers. If unset-VIEWPORTS suddenly started
    fanning out to the four verification-plan default viewports, every
    standard-tier caller would 4× their browser cost overnight.
    """
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{"name": "btn", "triggerType": "hover", "selector": ".btn"}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    _make_stub_compare(plugin_root)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {k: v for k, v in os.environ.items() if k != "VIEWPORTS"}
    env["PLUGIN_ROOT"] = str(plugin_root)
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert proc.returncode == 0
    result = (ref / "transitions" / "hover-state-result.txt").read_text()
    assert "viewports: <single" in result
    # No per-viewport WxH subdir under hover-state/ — the target dir sits
    # directly under hover-state/<safe-name>/.
    assert (ref / "transitions" / "hover-state" / "btn").is_dir()
    assert not (ref / "transitions" / "hover-state" / "375x812").exists()


def test_click_state_compare_fans_out_per_viewport(tmp_path: Path) -> None:
    """VIEWPORTS=\"375x812,1280x800\" → per-viewport subdirs + result.txt sections.

    Click-state's responsive divergence is the killer case: modals render as
    full-screen sheets on mobile and floating panels on desktop; menu toggles
    swap between hamburger and inline nav. A single-viewport sweep can pass
    the desktop arc cleanly while mobile drops the entire panel.
    """
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "click": [{"name": "tabs", "triggerType": "click-cycle", "selector": ".tab"}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    _make_stub_compare(plugin_root)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "click-state-compare.sh"
    env = {**os.environ, "PLUGIN_ROOT": str(plugin_root), "VIEWPORTS": "375x812,1280x800"}
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert proc.returncode == 0, f"click fan-out failed: {proc.stdout}\n{proc.stderr}"
    result = (ref / "transitions" / "click-state-result.txt").read_text()
    assert "viewport: 375x812" in result
    assert "viewport: 1280x800" in result
    assert (ref / "transitions" / "click-state" / "375x812" / "tabs").is_dir()
    assert (ref / "transitions" / "click-state" / "1280x800" / "tabs").is_dir()


def test_hover_state_compare_rejects_malformed_viewport(tmp_path: Path) -> None:
    """Malformed VIEWPORTS entry → exit 2 with clear error.

    A silent coerce would write garbage to VIEW_W/VIEW_H and ship a broken
    capture; exit 2 is the explicit signal that the env var is wrong.
    """
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{"name": "btn", "triggerType": "hover", "selector": ".btn"}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    _make_stub_compare(plugin_root)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {**os.environ, "PLUGIN_ROOT": str(plugin_root), "VIEWPORTS": "375x812,bogus"}
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert proc.returncode == 2
    assert "malformed" in proc.stderr.lower() or "bogus" in proc.stderr


# ── image-fidelity-check ──


def test_image_fidelity_passes_when_impl_references_all_urls(tmp_path: Path) -> None:
    """impl source mentions every visible-images.json URL → exit 0, status=pass.

    Closes the inverse failure mode: a too-strict matcher (requiring exact-URL
    match only) false-fails impls that import the same asset via a basename
    proxy or via a CDN-rewritten path. The matcher falls back: full URL →
    basename → basename-without-query → stem.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": "https://cdn.example.com/hero.jpg", "element": "img.hero"},
        {"type": "bg-image", "src": "https://cdn.example.com/banner.png", "element": "div", "width": 800, "height": 600},
    ]))
    (impl / "src" / "Hero.tsx").write_text(
        'export const Hero = () => <img src="https://cdn.example.com/hero.jpg" />;\n'
    )
    (impl / "src" / "Banner.tsx").write_text(
        'export const Banner = () => <div style={{ backgroundImage: "url(https://cdn.example.com/banner.png)", width: 800, height: 600 }} />;\n'
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "image-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "image-fidelity.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["matched"] == 2
    assert artifact["implRoot"] == str(impl)
    assert artifact["implDir"] == str(impl)
    assert artifact["implSrcDir"] == str(impl / "src")
    assert artifact["implPkgJson"] == str(impl / "package.json")


def test_image_fidelity_fails_when_url_dropped(tmp_path: Path) -> None:
    """impl source missing a ref URL → exit 1, status=fail, unmatched lists it.

    This is the failure class the gate exists for: agent generated a component
    that silently dropped a hero/logo/banner image. AE/SSIM catches the pixel
    diff but the URL-level signal here points the agent at the specific asset
    to fix, not at a region of pixel-diff to investigate.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": "https://cdn.example.com/dropped.jpg", "element": "img.dropped"},
    ]))
    (impl / "src" / "Empty.tsx").write_text('export const Empty = () => null;\n')
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "image-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "image-fidelity.json").read_text())
    assert artifact["status"] == "fail"
    assert len(artifact["unmatched"]) == 1
    assert artifact["unmatched"][0]["src"] == "https://cdn.example.com/dropped.jpg"


def test_image_fidelity_warns_on_dimension_mismatch(tmp_path: Path) -> None:
    """impl references URL but declares a width outside DIM_TOLERANCE → status=warn.

    Warn (not fail) because CSS-driven sizing is the common case and the
    declared prop may be a min-width / hint rather than ground truth. Exit 0
    so the gate doesn't block on a soft signal — the artifact still surfaces
    the mismatch for the agent to read.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "bg-image", "src": "https://cdn.example.com/big.png", "element": "div", "width": 1000, "height": 500},
    ]))
    (impl / "src" / "Big.tsx").write_text(
        'export const Big = () => <div style={{ backgroundImage: "url(https://cdn.example.com/big.png)", width: 200, height: 500 }} />;\n'
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "image-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    # Exit 0 because warn is a soft signal — the failure class for blocking
    # is "impl dropped the URL entirely", not "impl used a different width".
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "image-fidelity.json").read_text())
    assert artifact["status"] == "warn"
    assert len(artifact["dimensionMismatches"]) == 1
    assert "width: ref=1000 impl=200" in artifact["dimensionMismatches"][0]["issues"]


def test_image_fidelity_fails_on_local_cdn_optimizer_runtime_path(tmp_path: Path) -> None:
    """Loop-55 regression: static basename matching passed even though the
    browser loaded `/cdn-cgi/image/widtth=.../foo.webp` from the local Next app.

    The asset existed in public/ and the source mentioned `foo.webp`, so
    image-fidelity + asset-transfer both passed. At runtime, the local app
    does not serve Cloudflare image optimizer URLs, and a JS string typo
    (`widt\\u0074h`) made the path even worse. This must be a blocking
    image-fidelity failure, not a pixel-diff-only discovery.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": "https://cdn.example.com/images/foo.webp", "element": "img.foo"},
    ]))
    (impl / "src" / "Foo.tsx").write_text(
        'export const Foo = () => <img src="/cdn-cgi/image/widt\\u0074h=640,quality=90/images/foo.webp" />;\n',
        encoding="utf-8",
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "image-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "image-fidelity.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["matched"] == 1
    assert artifact["runtimeImageIssues"]
    assert artifact["runtimeImageIssues"][0]["kind"] == "local-cdn-optimizer-path"
    assert "widt\\u0074h" in artifact["runtimeImageIssues"][0]["snippet"]


def test_image_fidelity_skips_when_no_visible_images_json(tmp_path: Path) -> None:
    """Missing visible-images.json → status=pass, exit 0 (no-op, not an error).

    Mirrors runtime-spec-coverage.sh skip behavior: the verification-plan
    only wires this row when visible-images.json exists, but the script must
    still tolerate a missing input gracefully — defensive parity in case the
    script is invoked outside the dispatcher.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    impl.mkdir()
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "image-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0
    artifact = json.loads((ref / "image-fidelity.json").read_text())
    assert artifact["status"] == "pass"


def test_image_fidelity_rejects_hidden_reference_manifest_only_usage(tmp_path: Path) -> None:
    """Hidden reference manifests are not rendered asset usage.

    Loop validation found impls that stuffed every ref URL into a hidden
    `reference-manifest` node so static string matching passed while the
    visible page still used placeholders. image-fidelity must ignore that
    manifest surface and fail the actually unmatched images.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    urls = [f"https://cdn.example.com/food-{i}.webp" for i in range(5)]
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": url, "element": f"img.food-{i}"}
        for i, url in enumerate(urls)
    ]))
    (impl / "src" / "reference-manifest.tsx").write_text(
        "export function ReferenceManifest() {\n"
        "  return <div className=\"reference-manifest\" hidden>\n"
        + "\n".join(f"    <span>{url}</span>" for url in urls)
        + "\n  </div>;\n}\n",
        encoding="utf-8",
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "image-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=30,
    )

    assert proc.returncode == 1, f"hidden manifest must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "image-fidelity.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["matched"] == 0
    assert len(artifact["unmatched"]) == 5


def test_asset_utilization_rejects_hidden_reference_manifest_only_usage(tmp_path: Path) -> None:
    """asset-utilization must not count hidden reference-manifest strings as usage."""
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    urls = [f"https://cdn.example.com/asset-{i}.png" for i in range(5)]
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": url, "element": f"img.asset-{i}"}
        for i, url in enumerate(urls)
    ]))
    (impl / "src" / "App.tsx").write_text(
        "export default function App() {\n"
        "  return <div className=\"reference-manifest\" style={{ display: 'none' }}>\n"
        + "\n".join(f"    <span>{url}</span>" for url in urls)
        + "\n  </div>;\n}\n",
        encoding="utf-8",
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "asset-utilization-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl / "src")],
        capture_output=True, text=True, timeout=30,
    )

    assert proc.returncode == 1, f"hidden manifest must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "asset-utilization.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["referenced"] == 0
    assert "reference-manifest" in artifact["reason"]


def test_asset_utilization_rejects_low_opacity_asset_rail_usage(tmp_path: Path) -> None:
    """Bulk low-opacity/offscreen asset rails are not original-position usage."""
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    urls = [f"https://cdn.example.com/photo-{i}.webp" for i in range(6)]
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": url, "element": f"section:nth-child({i + 1}) img"}
        for i, url in enumerate(urls)
    ]))
    (impl / "src" / "App.tsx").write_text(
        "export default function App() {\n"
        "  return <div className=\"asset-rail fixed bottom-0 opacity-10 pointer-events-none blur-sm\" aria-hidden>\n"
        + "\n".join(f"    <img src=\"/images/{Path(url).name}\" />" for url in urls)
        + "\n  </div>;\n}\n",
        encoding="utf-8",
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "asset-utilization-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl / "src")],
        capture_output=True, text=True, timeout=30,
    )

    assert proc.returncode == 1, f"asset rail must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "asset-utilization.json").read_text())
    assert artifact["status"] == "fail"
    assert "asset rail" in artifact["reason"]


def test_verification_plan_emits_image_fidelity_when_visible_images_present(tmp_path: Path) -> None:
    """verification-plan.sh must add the image-fidelity + asset-transfer rows when
    visible-images.json exists. Both block-severity (severity upgraded from
    warn after the realfood.gov benchmark showed the agent reliably skips
    actual download — see CHANGELOG entry on `asset-transfer-check.sh`).
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": "https://cdn.example.com/x.jpg", "element": "img"},
    ]))
    plan = _run_verification_plan(ref)
    rows = {c["id"]: c for c in plan["requiredChecks"]}
    assert "image-fidelity" in rows
    assert rows["image-fidelity"]["severity"] == "block"
    # min_tier dropped standard → quick in HEAD after 077d8c3 — the agent set
    # tier=quick to silently drop these rows; making them quick-tier ensures
    # they fire at every cost-tier (cheap file-existence + grep checks).
    assert rows["image-fidelity"]["tier"] == "quick"
    assert rows["image-fidelity"]["produces"] == "image-fidelity.json"
    # Asset-transfer is the companion check — code refs vs actual files in impl/public/.
    assert "asset-transfer" in rows
    assert rows["asset-transfer"]["severity"] == "block"
    assert rows["asset-transfer"]["tier"] == "quick"
    assert rows["asset-transfer"]["produces"] == "asset-transfer.json"


def test_verification_plan_emits_asset_utilization_when_visible_images_present(tmp_path: Path) -> None:
    """Regression — c9b638d shipped 45 downloaded images with only 2 referenced
    in src (95% orphan). New `asset-utilization` row requires ≥60% referenced.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": "https://cdn.example.com/x.jpg", "element": "img"},
    ]))
    plan = _run_verification_plan(ref)
    rows = {c["id"]: c for c in plan["requiredChecks"]}
    assert "asset-utilization" in rows
    assert rows["asset-utilization"]["severity"] == "block"
    assert rows["asset-utilization"]["tier"] == "quick"
    assert rows["asset-utilization"]["produces"] == "asset-utilization.json"


def test_verification_plan_emits_lottie_runtime_when_lottie_detected(tmp_path: Path) -> None:
    """Lottie/bodymovin evidence must dispatch a hard runtime/json gate.

    Without this row, an impl can replace the original animation with generic
    GSAP/CSS motion and still satisfy unrelated transition marker checks.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({
        "resources": ["https://cdn.example.com/bodymovin.min.js"],
        "notes": "lottie-web registered animations",
    }))
    plan = _run_verification_plan(ref, tier="quick")
    rows = {c["id"]: c for c in plan["requiredChecks"]}
    assert "lottie-runtime" in rows
    assert rows["lottie-runtime"]["severity"] == "block"
    assert rows["lottie-runtime"]["tier"] == "quick"
    assert rows["lottie-runtime"]["produces"] == "lottie-runtime.json"


def test_verification_plan_emits_bundle_impl_coverage_when_bundle_map_present(tmp_path: Path) -> None:
    """Regression — c9b638d's bundle-map.json detected gsap-like + motion-like
    + Lenis, but impl/package.json shipped with only next/react/react-dom →
    dead-wire pattern. New `bundle-impl-coverage` row enforces install parity.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({
        "chunks": {"a.js": {"role": "vendor", "libs": ["gsap-like-strings"]}},
        "notes": "lenis class on <html> is conclusive runtime evidence.",
    }))
    plan = _run_verification_plan(ref)
    rows = {c["id"]: c for c in plan["requiredChecks"]}
    assert "bundle-impl-coverage" in rows
    assert rows["bundle-impl-coverage"]["severity"] == "block"
    assert rows["bundle-impl-coverage"]["tier"] == "quick"


def test_bundle_impl_coverage_script_fails_when_libs_missing(tmp_path: Path) -> None:
    """End-to-end: bundle-map detects gsap+lenis, impl/package.json lacks both → exit 1.
    """
    import subprocess
    work = tmp_path / "benchmark" / "work" / "deadbee"
    ref = work / "ref"
    impl = work / "impl"
    ref.mkdir(parents=True)
    impl.mkdir(parents=True)
    (ref / "bundle-map.json").write_text(json.dumps({
        "chunks": {"v.js": {"role": "vendor", "libs": ["gsap-like-strings", "motion-like"]}},
        "notes": "lenis on <html>",
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl", "dependencies": {"next": "16", "react": "19", "react-dom": "19"},
    }))
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "bundle-impl-coverage-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl / "package.json")],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 1, f"missing libs must fail: {proc.stderr}"
    out = json.loads((ref / "bundle-impl-coverage.json").read_text())
    assert out["status"] == "fail"
    sigs = {m["signature"] for m in out["missingDeps"]}
    assert "gsap-like-strings" in sigs
    assert "motion-like" in sigs
    assert "lenis" in sigs


def test_bundle_impl_coverage_script_passes_when_all_installed(tmp_path: Path) -> None:
    import subprocess
    work = tmp_path / "benchmark" / "work" / "deadbee"
    ref = work / "ref"
    impl = work / "impl"
    ref.mkdir(parents=True)
    impl.mkdir(parents=True)
    (ref / "bundle-map.json").write_text(json.dumps({
        "chunks": {"v.js": {"role": "vendor", "libs": ["gsap-like-strings"]}},
        "notes": "lenis on <html>",
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl",
        "dependencies": {"next": "16", "gsap": "3.12", "lenis": "1.0"},
    }))
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "bundle-impl-coverage-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl / "package.json")],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, f"all installed must pass: {proc.stderr}"


def test_verification_plan_forces_comprehensive_tier_under_benchmark_work(tmp_path: Path) -> None:
    """Regression — the 077d8c3 benchmark exposed a gaming pattern where the
    agent set `UI_CLONE_VERIFY_TIER=quick` and the verification surface
    shrank to 3 checks. Benchmark refs (path contains `benchmark/work/`)
    must force tier=comprehensive regardless of caller-supplied tier, so the
    agent does not get to pick which checks fire.
    """
    bench_root = tmp_path / "benchmark" / "work" / "deadbee"
    ref = bench_root / "ref"
    ref.mkdir(parents=True)
    # Wire enough signals to confirm tier-conditional rows light up.
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": "https://cdn.example.com/x.jpg", "element": "img"},
    ]))
    (ref / "external-sdks.json").write_text(json.dumps({"detected": ["useScroll"]}))
    # Caller asks for quick — script must override to comprehensive.
    plan = _run_verification_plan(ref, tier="quick")
    assert plan["tier"] == "comprehensive", (
        f"benchmark/work/ ref must force tier=comprehensive, got: {plan['tier']}"
    )
    # And the comprehensive-tier rows (e.g. video-motion-compare) must appear.
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "video-motion-compare" in ids, (
        f"comprehensive-tier row missing after benchmark force: {ids}"
    )


def test_verification_plan_omits_image_fidelity_when_visible_images_absent(tmp_path: Path) -> None:
    """No visible-images.json → no image-fidelity row.

    Locks in the conditional — this row should NOT fire unconditionally,
    otherwise SVG-only / canvas-only sites would always see a pass-noise row.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    plan = _run_verification_plan(ref)
    ids = {c["id"] for c in plan["requiredChecks"]}
    assert "image-fidelity" not in ids


# ── bench-verification smoke ──
#
# scripts/ci/bench-verification.sh is a developer utility — it builds tiny
# fixtures and times the dispatch path so cost regressions in the verification
# surface are visible before they hit a real clone run. The script is not on
# the ci-local critical path; this smoke test only locks in that the bench
# itself doesn't bitrot (fixture writes, JSON output shape, exit code 0 when
# accuracy holds). We use --repeat=1 to keep wall time under ~2s on CI.


def test_bench_verification_smoke_markdown() -> None:
    """bench-verification.sh --repeat=1 must exit 0 and emit the expected
    markdown header + the three named fixtures.

    Locks in that fixture writes + run_*_bench callers + median calc stay in
    sync. A bash-3.2-incompatible construct (e.g. associative arrays) regresses
    this test on macOS default bash.
    """
    import subprocess

    script = _project_root() / "scripts" / "ci" / "bench-verification.sh"
    proc = subprocess.run(
        ["bash", str(script), "--repeat=1"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"bench failed: {proc.stdout}\n{proc.stderr}"
    out = proc.stdout
    assert "# verification dispatch bench" in out
    assert "verification-plan.sh" in out
    for fixture in ("empty", "hover-only", "all-signals"):
        assert fixture in out, f"missing fixture '{fixture}' in output:\n{out}"
    for gate in ("spec-implementation-coverage", "runtime-spec-coverage"):
        assert gate in out, f"missing gate '{gate}' in output:\n{out}"


def test_bench_verification_json_mode_is_valid_json() -> None:
    """--json mode must produce a JSON object with the documented top-level
    keys (verificationPlan, specImplementationCoverage, runtimeSpecCoverage).

    Locks in the JSON contract for any CI consumer that wants to assert on
    a regression threshold against a stored baseline.
    """
    import subprocess

    script = _project_root() / "scripts" / "ci" / "bench-verification.sh"
    proc = subprocess.run(
        ["bash", str(script), "--repeat=1", "--json"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"bench --json failed: {proc.stdout}\n{proc.stderr}"
    data = json.loads(proc.stdout)
    assert set(data.keys()) >= {"repeat", "verificationPlan", "specImplementationCoverage", "runtimeSpecCoverage"}
    assert set(data["verificationPlan"].keys()) == {"empty", "hoverOnly", "allSignals"}
    for fixture_block in data["verificationPlan"].values():
        assert set(fixture_block.keys()) == {"quick", "standard", "comprehensive"}
        for tier_block in fixture_block.values():
            assert "medianMs" in tier_block and "checkCount" in tier_block
    # accuracy column should report "ok" on a clean tree — the unit tests
    # already lock in the pass/fail exit codes the bench fixtures depend on.
    assert data["specImplementationCoverage"]["accuracy"] == "ok"
    assert data["runtimeSpecCoverage"]["accuracy"] == "ok"


def test_bench_verification_rejects_bad_repeat() -> None:
    """--repeat must be a positive odd integer (median picks middle element).
    Even or non-numeric values must exit 2 with a clear message — otherwise
    a typo silently coerces to whatever bash's arithmetic returns and the
    median calc breaks in non-obvious ways.
    """
    import subprocess

    script = _project_root() / "scripts" / "ci" / "bench-verification.sh"
    for bad in ("2", "0", "abc"):
        proc = subprocess.run(
            ["bash", str(script), f"--repeat={bad}"],
            capture_output=True, text=True, timeout=15,
        )
        assert proc.returncode == 2, f"--repeat={bad} should exit 2, got {proc.returncode}: {proc.stderr}"
        assert "repeat" in proc.stderr.lower()
