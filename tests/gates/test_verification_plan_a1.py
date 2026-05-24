import json
from pathlib import Path

from ui_clone.gate import Gate

from ._helpers import (
    _post_implement_baseline,
    _run_verification_plan,
)


def _html_paste_check() -> dict:
    return {
        "id": "html-paste",
        "produces": "html-paste.json",
        "reason": "Universal anti-cheat",
        "severity": "block",
    }


def _write_html_paste_pass(ref: Path) -> None:
    (ref / "html-paste.json").write_text(json.dumps({"status": "pass"}))


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
             "reason": "Universal", "severity": "block"},
            _html_paste_check(),
        ],
    }))
    (ref / "hydration-check.json").write_text(json.dumps({"status": "pass", "errorCount": 0}))
    _write_html_paste_pass(ref)
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
             "reason": "signals.hasHover=true", "severity": "block"},
            _html_paste_check(),
        ],
    }))
    _write_html_paste_pass(ref)
    (ref / "transitions").mkdir(exist_ok=True)
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
             "reason": "signals.hasHover=true", "severity": "block"},
            _html_paste_check(),
        ],
    }))
    _write_html_paste_pass(ref)
    (ref / "transitions").mkdir(exist_ok=True)
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
    (ref / "transitions").mkdir(exist_ok=True)
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
    (ref / "transitions").mkdir(exist_ok=True)
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
             "reason": "Optional", "severity": "warn"},
            _html_paste_check(),
        ],
    }))
    (ref / "optional.json").write_text(json.dumps({"status": "fail"}))
    _write_html_paste_pass(ref)
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


def test_verification_plan_missing_html_paste_check_fails(tmp_path: Path) -> None:
    """post-implement must reject plans missing the universal html-paste rail."""
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
    (ref / "hydration-check.json").write_text(json.dumps({"status": "pass"}))

    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]

    assert any(
        r.label == "verification-plan.json"
        and r.message == (
            "verification-plan.json is missing required anti-cheat check: "
            "html-paste. Regenerate the verification plan."
        )
        for r in failures
    ), f"Missing html-paste check must hard-fail, got: {results}"



def test_verification_plan_empty_required_checks_fails_missing_html_paste(
    tmp_path: Path,
) -> None:
    """schemaVersion=1 with `requiredChecks: []` now fails the anti-cheat guard."""
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
    assert any(
        r.label == "verification-plan.json" and "empty" in r.message.lower()
        for r in warns
    ), f"Empty list must produce a visibility warn, got: {results}"
    assert any(
        r.label == "verification-plan.json"
        and "missing required anti-cheat check: html-paste" in r.message
        for r in failures
    ), f"Empty list must fail the html-paste guard, got: {results}"



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
