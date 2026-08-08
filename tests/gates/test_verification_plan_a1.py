import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest

from ui_clone.check_inputs import compute_check_input_hash, sidecar_path
from ui_clone.gate import Gate
from ui_clone.gates.verification_plan import _runtime_text_semantic_error

from ._helpers import (
    _post_implement_baseline,
    _run_verification_plan,
    _write_impl_fixture,
)


def _html_paste_check() -> dict:
    return {
        "id": "html-paste",
        "produces": "html-paste.json",
        "reason": "Universal anti-cheat",
        "severity": "block",
    }


def _write_html_paste_pass(ref: Path) -> None:
    impl_root = (ref / ".impl-root").read_text(encoding="utf-8").strip()
    (ref / "html-paste.json").write_text(
        json.dumps({"status": "pass", "implRoot": impl_root}),
        encoding="utf-8",
    )


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


def test_verification_plan_live_parity_findings_fail_block(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {
                "id": "live-parity-sweep",
                "script": "skills/visual-debug/scripts/live-parity-sweep.sh",
                "produces": "live-parity.json",
                "reason": "Live DOM census must be clean",
                "severity": "block",
            }
        ],
    }))
    (ref / "live-parity.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "findings",
        "findingCount": 1,
        "findings": [{"kind": "missing-images"}],
    }))

    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]

    assert any(r.label == "required: live-parity-sweep" for r in failures)



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


def test_strict_warnings_promote_selected_advisory_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Release/strict closeout can promote known fidelity warnings to failures."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "strictWarnings": True,
        "requiredChecks": [
            {"id": "scroll-coverage", "produces": "scroll-coverage.json",
             "reason": "release fidelity", "severity": "warn"},
            {"id": "visual-fidelity-judge", "produces": "visual-fidelity-judge.json",
             "reason": "release fidelity", "severity": "warn"},
            {"id": "runtime-text-sequence", "produces": "runtime-text-sequence.json",
             "reason": "release fidelity", "severity": "warn"},
            {"id": "optional", "produces": "optional.json",
             "reason": "still advisory", "severity": "warn"},
            _html_paste_check(),
        ],
    }))
    (ref / "scroll-coverage.json").write_text(json.dumps({"status": "fail", "failureCount": 1}))
    (ref / "visual-fidelity-judge.json").write_text(
        json.dumps({"status": "fail", "failureCount": 1})
    )
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps({"status": "fail", "failureCount": 1})
    )
    (ref / "optional.json").write_text(json.dumps({"status": "fail", "failureCount": 1}))
    _write_html_paste_pass(ref)
    monkeypatch.delenv("UI_CLONE_STRICT_WARNINGS", raising=False)

    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    warns = [r for r in results if r.status == "warn"]

    assert any(r.label == "required: scroll-coverage" for r in failures)
    assert any(r.label == "required: visual-fidelity-judge" for r in failures)
    assert any(r.label == "required: runtime-text-sequence" for r in failures)
    assert any(r.label == "required: optional" for r in warns)


@pytest.mark.parametrize(
    ("field", "bad_score"),
    [
        ("axis", True),
        ("axis", float("nan")),
        ("axis", float("inf")),
        ("axis", -1),
        ("axis", 11),
        ("static", 100),
        ("overall", 100),
    ],
    ids=[
        "bool-axis",
        "nan-axis",
        "infinity-axis",
        "negative-axis",
        "eleven-axis",
        "hundred-static",
        "hundred-overall",
    ],
)
def test_visual_fidelity_gate_revalidates_strict_numeric_semantics(
    tmp_path: Path,
    field: str,
    bad_score: object,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "visual-fidelity-judge",
                        "produces": "visual-fidelity-judge.json",
                        "reason": "numeric fidelity",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "status": "pass",
        "staticSections": [{"label": "hero", "score": 10}],
        "motion": {
            "axes": {
                "layout": 10,
                "text": 10,
                "color": 10,
                "animation": 10,
            }
        },
        "overall": {"score": 10, "min": 10},
    }
    if field == "axis":
        payload["motion"]["axes"]["layout"] = bad_score
    elif field == "static":
        payload["staticSections"][0]["score"] = bad_score
    else:
        payload["overall"]["score"] = bad_score
    (ref / "visual-fidelity-judge.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    results = Gate(ref).gate_post_implement()

    failure = next(
        result
        for result in results
        if result.label == "required: visual-fidelity-judge"
    )
    assert failure.status == "fail"
    assert (
        "strict JSON" in failure.message
        or "semantically invalid artifact" in failure.message
    )


def test_forged_transition_pass_fails_even_with_current_input_fingerprint(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {"id": "hero-load", "trigger": "page-load"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "transition-proof",
                        "produces": "transition-proof.json",
                        "reason": "composite motion proof",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "transition-proof.json").write_text(
        json.dumps({"schemaVersion": 1, "status": "pass"}),
        encoding="utf-8",
    )
    impl_root = Path(
        (ref / ".impl-root").read_text(encoding="utf-8").strip()
    )
    fingerprint = compute_check_input_hash(impl_root, ref, "transition-proof")
    assert fingerprint
    sidecar_path(ref, "transition-proof").write_text(
        fingerprint, encoding="utf-8"
    )

    results = Gate(ref).gate_post_implement()

    failure = next(
        result
        for result in results
        if result.label == "required: transition-proof"
    )
    assert failure.status == "fail"
    assert "semantically invalid composite proof" in failure.message



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
    """hasScrollScrub=true via external-sdks → video-motion-compare row required,
    with transition-trajectory as its cheap standard-tier pre-filter row.

    video-motion-compare (comprehensive) stays the easing authority: it catches
    "same end-state, wrong velocity curve" that 5-point sampling cannot see
    (easeOutCubic vs easeOutQuint read identical at 0/25/50/75/100). The
    trajectory row returned to dispatch at STANDARD tier so inner iteration
    loops get a seconds-cheap gross-scroll-motion FAIL signal instead of flying
    blind until the 5min+ 60fps sweep; its argsRecipe makes it self-describing
    (no SIGNATURES entry).
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
    trajectory = [c for c in plan["requiredChecks"] if c["id"] == "transition-trajectory"]
    assert trajectory, f"trajectory pre-filter row missing when hasScrollScrub=true: {ids}"
    assert trajectory[0]["tier"] == "standard"
    assert trajectory[0]["severity"] == "block"
    assert "{ref_url}" in trajectory[0].get("argsRecipe", ""), (
        "trajectory row must self-describe dispatch args via argsRecipe"
    )


def test_verification_plan_registers_runtime_text_sequence_as_blocking(
    tmp_path: Path,
) -> None:
    """Runtime-rendered text order blocks by default at standard tier."""
    ref = tmp_path / "ref"
    ref.mkdir()

    plan = _run_verification_plan(ref)
    rows = {
        row["id"]: row
        for row in plan["requiredChecks"]
        if row["id"] == "runtime-text-sequence"
    }

    assert rows == {
        "runtime-text-sequence": {
            "id": "runtime-text-sequence",
            "script": "skills/visual-debug/scripts/runtime-text-sequence-check.sh",
            "produces": "runtime-text-sequence.json",
            "reason": (
                "Rendered reference and implementation text must match in "
                "document order after runtime and scroll effects"
            ),
            "severity": "block",
            "tier": "standard",
            "argsRecipe": "{session}-rts {ref_url} {impl_url} {ref_dir}",
            "dependsOn": ["runtime-env"],
        }
    }


def test_runtime_text_sequence_legacy_warn_plan_still_blocks_in_rapid_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale warn row cannot make missing rendered-copy evidence green."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "requiredChecks": [{
                "id": "runtime-text-sequence",
                "produces": "runtime-text-sequence.json",
                "severity": "warn",
            }],
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("UI_CLONE_PHASE", "rapid")
    monkeypatch.delenv("UI_CLONE_STRICT_WARNINGS", raising=False)

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].label == "required: runtime-text-sequence"
    assert results[0].status == "fail"
    assert "MISSING_ARTIFACT" in results[0].message


def _runtime_text_artifact(
    status: str,
    ref_blocks: list[str],
    impl_blocks: list[str],
) -> dict:
    lcs_length = len(ref_blocks) if ref_blocks == impl_blocks else 0
    combined = len(ref_blocks) + len(impl_blocks)
    missing_count = len(ref_blocks) - lcs_length
    extra_count = len(impl_blocks) - lcs_length

    def capture(blocks: list[str], side: str) -> dict:
        records = [
            {
                "slot": f"main>{side}-p:nth-of-type({index + 1})::run(1)",
                "text": text,
                "tag": "P",
                "initialViewport": False,
            }
            for index, text in enumerate(blocks)
        ]
        return {
            "blockCount": len(blocks),
            "blocks": blocks,
            "records": records,
            "samples": [records, records],
            "phaseSampleStartIndex": 0,
        }

    return {
        "schemaVersion": 1,
        "status": status,
        "refUrl": "https://ref.example.test/",
        "implUrl": "https://impl.example.test/",
        "actualRefUrl": "https://ref.example.test/",
        "actualImplUrl": "https://impl.example.test/",
        "captureReceipt": {
            "ref": _runtime_capture_receipt("https://ref.example.test/"),
            "impl": _runtime_capture_receipt("https://impl.example.test/"),
        },
        "thresholds": {
            "minOrderedSimilarity": 0.85,
            "maxMissingRatio": 0.15,
            "maxMissingBlocks": max(1, int(len(ref_blocks) * 0.15)),
        },
        "ref": capture(ref_blocks, "ref"),
        "impl": capture(impl_blocks, "impl"),
        "phaseVariance": {
            "accepted": False,
            "reason": "exact-match" if ref_blocks == impl_blocks else "not-confirmed",
        },
        "comparison": {
            "lcsLength": lcs_length,
            "orderedSimilarity": (
                round(2 * lcs_length / combined, 4) if combined else 1.0
            ),
            "missingCount": missing_count,
            "missingRatio": (
                round(missing_count / len(ref_blocks), 4) if ref_blocks else 0.0
            ),
            "extraCount": extra_count,
        },
        "violations": [] if status == "pass" else [{"kind": "sequence-mismatch"}],
    }


def _runtime_capture_receipt(url: str) -> dict:
    origin = url.rstrip("/")
    return {
        "requestedUrl": url,
        "openUrl": url,
        "actualUrl": url,
        "analysisUrl": url,
        "analysisOrigin": origin,
        "responseStatus": 200,
        "readyState": "complete",
        "navigationType": "navigate",
        "errorDocument": False,
        "batchCommandCount": 6,
        "attempt": 1,
        "closeAttempts": 1,
        "closed": True,
    }


def _write_runtime_text_provenance(
    ref: Path,
    *,
    digest: str | None = None,
) -> None:
    artifact_path = ref / "runtime-text-sequence.json"
    raw = artifact_path.read_bytes()
    artifact = json.loads(raw)
    (ref / "runtime-text-sequence.provenance.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "owner": "run-required-checks",
            "artifact": artifact_path.name,
            "refUrl": artifact["refUrl"],
            "implUrl": artifact["implUrl"],
            "artifactSha256": digest or hashlib.sha256(raw).hexdigest(),
            "artifactMtimeNs": artifact_path.stat().st_mtime_ns,
        }),
        encoding="utf-8",
    )


def _add_runtime_contract(artifact: dict) -> dict:
    ref_blocks = artifact["ref"]["blocks"]
    artifact.update({
        "refUrl": "https://ref.example.test/",
        "implUrl": "https://impl.example.test/",
        "actualRefUrl": "https://ref.example.test/",
        "actualImplUrl": "https://impl.example.test/",
        "captureReceipt": {
            "ref": _runtime_capture_receipt("https://ref.example.test/"),
            "impl": _runtime_capture_receipt("https://impl.example.test/"),
        },
        "thresholds": {
            "minOrderedSimilarity": 0.85,
            "maxMissingRatio": 0.15,
            "maxMissingBlocks": max(1, int(len(ref_blocks) * 0.15)),
        },
    })
    return artifact


def _runtime_phase_artifact() -> dict:
    stable = [f"Stable copy {index}" for index in range(20)]

    def record(text: str, slot: str, tag: str = "P") -> dict:
        return {
            "slot": slot,
            "text": text,
            "tag": tag,
            "initialViewport": False,
        }

    ref_records = [
        record(text, f"main>p:nth-of-type({index + 1})::run(1)")
        for index, text in enumerate(stable)
    ]
    variant = record("Carousel variant", "main>h4::run(1)", "H4")
    impl_records = [*ref_records[:10], dict(variant), *ref_records[10:]]
    observed = [*ref_records[:10], dict(variant), *ref_records[10:]]
    artifact = {
        "schemaVersion": 1,
        "status": "pass",
        "ref": {
            "blockCount": len(ref_records),
            "blocks": [item["text"] for item in ref_records],
            "records": ref_records,
            "samples": [observed, ref_records, ref_records, observed, ref_records],
            "phaseSampleStartIndex": 0,
        },
        "impl": {
            "blockCount": len(impl_records),
            "blocks": [item["text"] for item in impl_records],
            "records": impl_records,
            "samples": [impl_records, ref_records, ref_records, impl_records],
            "phaseSampleStartIndex": 0,
        },
        "comparison": {
            "lcsLength": 20,
            "orderedSimilarity": round(40 / 41, 4),
            "missingCount": 0,
            "missingRatio": 0.0,
            "extraCount": 1,
        },
        "phaseVariance": {
            "accepted": True,
            "advisory": "bounded rendered phase variance confirmed",
            "gapCount": 1,
            "proof": [{
                "gapIndex": 0,
                "beforeSlot": "main>p:nth-of-type(10)::run(1)",
                "afterSlot": "main>p:nth-of-type(11)::run(1)",
                "beforeAnchor": {
                    "slot": "main>p:nth-of-type(10)::run(1)",
                    "text": "Stable copy 9",
                },
                "afterAnchor": {
                    "slot": "main>p:nth-of-type(11)::run(1)",
                    "text": "Stable copy 10",
                },
                "candidateSide": "impl",
                "candidate": {
                    "slot": "main>h4::run(1)",
                    "text": "Carousel variant",
                    "tag": "H4",
                    "initialViewport": False,
                },
                "matchedReferenceCandidatePresentSample": 0,
                "referenceCyclePolarity": "present-absent-present",
                "matchedReferenceCandidateAbsentStartSample": 1,
                "matchedReferenceCandidateRecurredSample": 3,
                "referenceAbsenceRunLength": 2,
                "referencePhaseSampleStartIndex": 0,
                "referenceCandidate": {
                    "slot": "main>h4::run(1)",
                    "text": "Carousel variant",
                    "tag": "H4",
                    "initialViewport": False,
                },
                "matchedImplementationCandidateSample": 0,
                "implementationCyclePolarity": "present-absent-present",
                "matchedImplementationCandidateAbsentStartSample": 1,
                "matchedImplementationCandidateRecurredSample": 3,
                "implementationAbsenceRunLength": 2,
                "implementationPhaseSampleStartIndex": 0,
                "implementationCandidate": {
                    "slot": "main>h4::run(1)",
                    "text": "Carousel variant",
                    "tag": "H4",
                    "initialViewport": False,
                },
                "refBeforeAnchor": {
                    "slot": "main>p:nth-of-type(10)::run(1)",
                    "text": "Stable copy 9",
                },
                "refAfterAnchor": {
                    "slot": "main>p:nth-of-type(11)::run(1)",
                    "text": "Stable copy 10",
                },
                "implBeforeAnchor": {
                    "slot": "main>p:nth-of-type(10)::run(1)",
                    "text": "Stable copy 9",
                },
                "implAfterAnchor": {
                    "slot": "main>p:nth-of-type(11)::run(1)",
                    "text": "Stable copy 10",
                },
            }],
            "referenceSampleCount": 5,
            "implementationSampleCount": 4,
        },
        "violations": [],
    }
    return _add_runtime_contract(artifact)


def _runtime_substitution_phase_artifact() -> dict:
    def record(
        text: str,
        slot: str,
        tag: str = "P",
    ) -> dict:
        return {
            "slot": slot,
            "text": text,
            "tag": tag,
            "initialViewport": False,
        }

    def stable_records(side: str) -> list[dict]:
        return [
            record(
                f"Stable copy {index}",
                f"{side}>stable>p:nth-of-type({index + 1})",
                "A" if index == 15 else ("BUTTON" if index == 16 else "P"),
            )
            for index in range(30)
        ]

    ref_stable = stable_records("ref")
    impl_stable = stable_records("impl")
    ref_dynamic = [
        record(
            "Reference dynamic alpha",
            "ref>dynamic>span:nth-of-type(1)",
            "SPAN",
        ),
        record(
            "Reference dynamic beta",
            "ref>dynamic>span:nth-of-type(2)",
            "SPAN",
        ),
    ]
    impl_dynamic = [
        record(
            "Implementation dynamic alpha",
            "impl>dynamic>span:nth-of-type(1)",
            "SPAN",
        ),
        record(
            "Implementation dynamic beta",
            "impl>dynamic>span:nth-of-type(2)",
            "SPAN",
        ),
    ]
    ref_live = [
        record(
            "Reference live title",
            "ref>cards>article:nth-of-type(1)>h4:nth-of-type(1)",
            "H4",
        ),
        record(
            "Reference live summary",
            "ref>cards>article:nth-of-type(1)>p:nth-of-type(1)",
        ),
    ]
    impl_live = [
        record(
            "Implementation live title",
            "impl>cards>article:nth-of-type(1)>h4:nth-of-type(1)",
            "H4",
        ),
        record(
            "Implementation live summary",
            "impl>cards>article:nth-of-type(1)>p:nth-of-type(1)",
        ),
    ]
    ref_progressive = record(
        "Everyday",
        "ref>hero>message>h5:nth-of-type(1)",
        "H5",
    )
    impl_progressive = record(
        "Everyday Tech",
        "impl>hero>message>h5:nth-of-type(1)",
        "H5",
    )
    ref_records = [
        *ref_stable[:6],
        *ref_dynamic,
        *ref_stable[6:16],
        *ref_live,
        *ref_stable[16:26],
        ref_progressive,
        *ref_stable[26:],
    ]
    impl_records = [
        *impl_stable[:6],
        *impl_dynamic,
        *impl_stable[6:16],
        *impl_live,
        *impl_stable[16:26],
        impl_progressive,
        *impl_stable[26:],
    ]

    def sample_with(
        records: list[dict],
        replacements: dict[str, str],
    ) -> list[dict]:
        return [
            {
                **item,
                "text": replacements.get(item["slot"], item["text"]),
            }
            for item in records
        ]

    ref_earlier = sample_with(
        ref_records,
        {
            ref_dynamic[0]["slot"]: "Earlier reference dynamic alpha",
            ref_dynamic[1]["slot"]: "Earlier reference dynamic beta",
            ref_progressive["slot"]: "Every",
        },
    )
    impl_earlier = sample_with(
        impl_records,
        {
            impl_dynamic[0]["slot"]: "Earlier implementation dynamic alpha",
            impl_dynamic[1]["slot"]: "Earlier implementation dynamic beta",
        },
    )

    def capture(records: list[dict], earlier: list[dict]) -> dict:
        return {
            "blockCount": len(records),
            "blocks": [item["text"] for item in records],
            "records": records,
            "samples": [earlier, records],
            "phaseSampleStartIndex": 0,
        }

    lcs_length = 30
    missing_count = len(ref_records) - lcs_length
    combined = len(ref_records) + len(impl_records)
    artifact = {
        "schemaVersion": 1,
        "status": "pass",
        "ref": capture(ref_records, ref_earlier),
        "impl": capture(impl_records, impl_earlier),
        "comparison": {
            "lcsLength": lcs_length,
            "orderedSimilarity": round(2 * lcs_length / combined, 4),
            "missingCount": missing_count,
            "missingRatio": round(missing_count / len(ref_records), 4),
            "extraCount": len(impl_records) - lcs_length,
        },
        "phaseVariance": {
            "accepted": True,
            "advisory": "bounded rendered phase variance confirmed",
            "gapCount": 3,
            "proof": [
                {
                    "gapIndex": 0,
                    "kind": "dynamic-region",
                    "recordCount": 2,
                    "referenceStateCount": 2,
                    "implementationStateCount": 2,
                },
                {
                    "gapIndex": 1,
                    "kind": "live-card-region",
                    "recordCount": 2,
                    "slotTailDepth": 3,
                },
                {
                    "gapIndex": 2,
                    "kind": "progressive-reveal",
                    "observedVariantCount": 2,
                    "reference": ref_progressive,
                    "implementation": impl_progressive,
                },
            ],
            "referenceSampleCount": 2,
            "implementationSampleCount": 2,
        },
        "violations": [],
    }
    return _add_runtime_contract(artifact)


def _runtime_volatile_counter_artifact() -> dict:
    ref_blocks = [
        *(f"Stable prefix {index}" for index in range(8)),
        "←",
        "04",
        "→",
        *(f"Stable suffix {index}" for index in range(8)),
    ]
    impl_blocks = list(ref_blocks)
    impl_blocks[9] = "26"
    artifact = _runtime_text_artifact("pass", ref_blocks, impl_blocks)
    for side in ("ref", "impl"):
        counter = artifact[side]["records"][9]
        counter["slot"] = f"{side}>counter>value>span:nth-of-type(1)"
        counter["tag"] = "SPAN"
    lcs_length = len(ref_blocks) - 1
    artifact["comparison"] = {
        "lcsLength": lcs_length,
        "orderedSimilarity": round(
            2 * lcs_length / (len(ref_blocks) + len(impl_blocks)),
            4,
        ),
        "missingCount": 1,
        "missingRatio": round(1 / len(ref_blocks), 4),
        "extraCount": 1,
    }
    artifact["phaseVariance"] = {
        "accepted": True,
        "advisory": "bounded rendered phase variance confirmed",
        "gapCount": 1,
        "proof": [{
            "gapIndex": 0,
            "kind": "volatile-counter",
            "reference": dict(artifact["ref"]["records"][9]),
            "implementation": dict(artifact["impl"]["records"][9]),
        }],
        "referenceSampleCount": 2,
        "implementationSampleCount": 2,
    }
    artifact["violations"] = []
    return artifact


def _runtime_reverse_phase_artifact() -> dict:
    forward = _runtime_phase_artifact()
    reverse = cast(dict, json.loads(json.dumps(forward)))
    reverse["ref"], reverse["impl"] = reverse["impl"], reverse["ref"]
    reverse["comparison"] = {
        "lcsLength": 20,
        "orderedSimilarity": round(40 / 41, 4),
        "missingCount": 1,
        "missingRatio": round(1 / 21, 4),
        "extraCount": 0,
    }
    reverse["phaseVariance"]["proof"][0]["candidateSide"] = "ref"
    reverse["phaseVariance"]["referenceSampleCount"] = 4
    reverse["phaseVariance"]["implementationSampleCount"] = 5
    return reverse


def _runtime_shifted_anchor_phase_artifact() -> dict:
    artifact = cast(dict, json.loads(json.dumps(_runtime_phase_artifact())))
    stable = [f"Stable copy {index}" for index in range(20)]

    def records(side: str, *, present: bool) -> list[dict]:
        values = [
            {
                "slot": f"{side}-phase:slot:{index}",
                "text": text,
                "tag": "P",
                "initialViewport": False,
            }
            for index, text in enumerate(stable)
        ]
        if present:
            values.insert(10, {
                "slot": f"{side}-phase:variant",
                "text": "Carousel variant",
                "tag": "H4",
                "initialViewport": False,
            })
        return values

    ref_absent = records("ref", present=False)
    ref_present = records("ref", present=True)
    impl_absent = records("impl", present=False)
    impl_present = records("impl", present=True)
    artifact["ref"]["records"] = ref_absent
    artifact["ref"]["samples"] = [
        ref_present,
        ref_absent,
        ref_absent,
        ref_present,
        ref_absent,
    ]
    artifact["impl"]["records"] = impl_present
    artifact["impl"]["samples"] = [
        impl_present,
        impl_absent,
        impl_absent,
        impl_present,
    ]
    artifact["phaseVariance"]["proof"][0].update({
        "beforeSlot": "impl-phase:slot:9",
        "afterSlot": "impl-phase:slot:10",
        "beforeAnchor": {
            "slot": "impl-phase:slot:9",
            "text": "Stable copy 9",
        },
        "afterAnchor": {
            "slot": "impl-phase:slot:10",
            "text": "Stable copy 10",
        },
        "refBeforeAnchor": {
            "slot": "ref-phase:slot:9",
            "text": "Stable copy 9",
        },
        "refAfterAnchor": {
            "slot": "ref-phase:slot:10",
            "text": "Stable copy 10",
        },
        "implBeforeAnchor": {
            "slot": "impl-phase:slot:9",
            "text": "Stable copy 9",
        },
        "implAfterAnchor": {
            "slot": "impl-phase:slot:10",
            "text": "Stable copy 10",
        },
        "candidateSide": "impl",
        "candidate": {
            "slot": "impl-phase:variant",
            "text": "Carousel variant",
            "tag": "H4",
            "initialViewport": False,
        },
        "matchedReferenceCandidatePresentSample": 0,
        "referenceCyclePolarity": "present-absent-present",
        "matchedReferenceCandidateAbsentStartSample": 1,
        "matchedReferenceCandidateRecurredSample": 3,
        "referenceAbsenceRunLength": 2,
        "referenceCandidate": {
            "slot": "ref-phase:variant",
            "text": "Carousel variant",
            "tag": "H4",
            "initialViewport": False,
        },
        "matchedImplementationCandidateSample": 0,
        "implementationCyclePolarity": "present-absent-present",
        "matchedImplementationCandidateAbsentStartSample": 1,
        "matchedImplementationCandidateRecurredSample": 3,
        "implementationAbsenceRunLength": 2,
        "implementationCandidate": {
            "slot": "impl-phase:variant",
            "text": "Carousel variant",
            "tag": "H4",
            "initialViewport": False,
        },
    })
    return artifact


def test_runtime_text_sequence_gate_uses_producer_cjk_whitespace_policy() -> None:
    artifact = _runtime_text_artifact(
        "pass",
        ["Before", "AI 원천기술을 도입하여 사용자 맞춤형 서비스", "After"],
        ["Before", "AI 원천기술을 도입하여사용자 맞춤형 서비스", "After"],
    )
    artifact["comparison"] = {
        "lcsLength": 3,
        "orderedSimilarity": 1.0,
        "missingCount": 0,
        "missingRatio": 0.0,
        "extraCount": 0,
    }
    artifact["phaseVariance"] = {
        "accepted": False,
        "reason": "exact-match",
    }
    artifact["violations"] = []

    assert _runtime_text_semantic_error(artifact) is None


def test_runtime_text_sequence_gate_rejects_canonical_empty_text() -> None:
    artifact = _runtime_text_artifact("pass", ["\u200b"], ["\u200b"])

    assert _runtime_text_semantic_error(artifact) is not None


def test_runtime_text_sequence_gate_accepts_producer_substitution_proofs() -> None:
    artifact = _runtime_substitution_phase_artifact()

    assert _runtime_text_semantic_error(artifact) is None


def test_runtime_text_sequence_gate_accepts_volatile_counter_proof() -> None:
    artifact = _runtime_volatile_counter_artifact()

    assert _runtime_text_semantic_error(artifact) is None


@pytest.mark.parametrize(
    "tamper",
    [
        "comparison-metric",
        "claimed-proof",
        "proof-type",
        "captured-states",
        "live-card-anchor",
        "progressive-variant",
        "volatile-counter",
    ],
)
def test_runtime_text_sequence_gate_rederives_substitution_proofs(
    tamper: str,
) -> None:
    artifact = (
        _runtime_volatile_counter_artifact()
        if tamper == "volatile-counter"
        else _runtime_substitution_phase_artifact()
    )
    if tamper == "comparison-metric":
        artifact["comparison"]["lcsLength"] -= 1
    elif tamper == "claimed-proof":
        artifact["phaseVariance"]["proof"][0]["referenceStateCount"] += 1
    elif tamper == "proof-type":
        artifact["phaseVariance"]["proof"][0]["recordCount"] = 2.0
    elif tamper == "captured-states":
        final_text_by_slot = {
            item["slot"]: item["text"]
            for item in artifact["ref"]["records"]
        }
        for item in artifact["ref"]["samples"][0]:
            if ">dynamic>" in item["slot"]:
                item["text"] = final_text_by_slot[item["slot"]]
    elif tamper == "live-card-anchor":
        for side in ("ref", "impl"):
            for sample in artifact[side]["samples"]:
                for item in sample:
                    if item["text"] == "Stable copy 15":
                        item["tag"] = "P"
    elif tamper == "progressive-variant":
        for item in artifact["ref"]["samples"][0]:
            if ">hero>message>" in item["slot"]:
                item["text"] = "Unrelated phase copy"
    else:
        artifact["ref"]["blocks"][9] = "not a counter"
        for sample in artifact["ref"]["samples"]:
            sample[9]["text"] = "not a counter"

    assert _runtime_text_semantic_error(artifact) is not None


def test_runtime_text_sequence_accepts_independently_verified_phase_proof(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "strictWarnings": True,
            "requiredChecks": [{
                "id": "runtime-text-sequence",
                "produces": "runtime-text-sequence.json",
                "severity": "warn",
            }],
        }),
        encoding="utf-8",
    )
    artifact = _runtime_phase_artifact()
    proof = artifact["phaseVariance"]["proof"][0]
    assert (
        proof["matchedReferenceCandidatePresentSample"],
        proof["matchedReferenceCandidateAbsentStartSample"],
        proof["matchedReferenceCandidateRecurredSample"],
    ) == (0, 1, 3)
    assert (
        proof["matchedImplementationCandidateSample"],
        proof["matchedImplementationCandidateAbsentStartSample"],
        proof["matchedImplementationCandidateRecurredSample"],
    ) == (0, 1, 3)
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(artifact),
        encoding="utf-8",
    )
    _write_runtime_text_provenance(ref)

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "pass"


def test_runtime_text_sequence_accepts_independently_verified_reverse_polarity(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "strictWarnings": True,
            "requiredChecks": [{
                "id": "runtime-text-sequence",
                "produces": "runtime-text-sequence.json",
                "severity": "warn",
            }],
        }),
        encoding="utf-8",
    )
    artifact = _runtime_phase_artifact()
    ref_absent = artifact["ref"]["records"]
    ref_present = artifact["ref"]["samples"][0]
    impl_present = artifact["impl"]["records"]
    impl_absent = artifact["impl"]["samples"][1]
    artifact["ref"]["samples"] = [
        ref_absent,
        ref_present,
        ref_absent,
    ]
    artifact["impl"]["samples"] = [
        impl_absent,
        impl_present,
        impl_absent,
        impl_present,
    ]
    phase_variance = artifact["phaseVariance"]
    phase_variance["referenceSampleCount"] = 3
    phase_variance["implementationSampleCount"] = 4
    proof = phase_variance["proof"][0]
    proof.update({
        "matchedReferenceCandidatePresentSample": 1,
        "referenceCyclePolarity": "absent-present-absent",
        "matchedReferenceCandidateAbsentStartSample": 0,
        "matchedReferenceCandidateRecurredSample": 2,
        "referenceAbsenceRunLength": 1,
        "matchedImplementationCandidateSample": 1,
        "implementationCyclePolarity": "absent-present-absent",
        "matchedImplementationCandidateAbsentStartSample": 0,
        "matchedImplementationCandidateRecurredSample": 2,
        "implementationAbsenceRunLength": 1,
    })
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(artifact),
        encoding="utf-8",
    )
    _write_runtime_text_provenance(ref)

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "pass"


@pytest.mark.parametrize("provenance_state", ["missing", "stale"])
def test_runtime_text_sequence_rejects_unprovenanced_forged_pass(
    tmp_path: Path,
    provenance_state: str,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "strictWarnings": True,
            "requiredChecks": [{
                "id": "runtime-text-sequence",
                "produces": "runtime-text-sequence.json",
                "severity": "warn",
            }],
        }),
        encoding="utf-8",
    )
    artifact = _runtime_text_artifact(
        "pass",
        ["Fully self-consistent forged copy"],
        ["Fully self-consistent forged copy"],
    )
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(artifact),
        encoding="utf-8",
    )
    if provenance_state == "stale":
        _write_runtime_text_provenance(ref, digest="0" * 64)

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "fail"
    assert "dispatcher provenance is missing or stale" in (
        results[0].message or ""
    )


@pytest.mark.parametrize(
    "missing_field",
    [
        "records",
        "samples",
        "phaseSampleStartIndex",
        "actualImplUrl",
        "captureReceipt",
    ],
)
def test_runtime_text_sequence_rejects_minimal_exact_match_artifact(
    tmp_path: Path,
    missing_field: str,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "strictWarnings": True,
            "requiredChecks": [{
                "id": "runtime-text-sequence",
                "produces": "runtime-text-sequence.json",
                "severity": "warn",
            }],
        }),
        encoding="utf-8",
    )
    artifact = _runtime_text_artifact(
        "pass",
        ["Canonical exact copy"],
        ["Canonical exact copy"],
    )
    if missing_field in {"records", "samples", "phaseSampleStartIndex"}:
        artifact["impl"].pop(missing_field)
    else:
        artifact.pop(missing_field)
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(artifact),
        encoding="utf-8",
    )

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "fail"
    assert "malformed artifact" in (results[0].message or "") or (
        "semantically inconsistent artifact" in (results[0].message or "")
    )


@pytest.mark.parametrize(
    "tamper",
    ["redirect", "ref-as-impl", "http-error", "receipt-route"],
)
def test_runtime_text_sequence_rejects_forged_url_capture_receipt(
    tmp_path: Path,
    tamper: str,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "strictWarnings": True,
            "requiredChecks": [{
                "id": "runtime-text-sequence",
                "produces": "runtime-text-sequence.json",
                "severity": "warn",
            }],
        }),
        encoding="utf-8",
    )
    artifact = _runtime_text_artifact(
        "pass",
        ["Canonical exact copy"],
        ["Canonical exact copy"],
    )
    if tamper == "redirect":
        artifact["actualImplUrl"] = "https://impl.example.test/redirected"
    elif tamper == "ref-as-impl":
        artifact["implUrl"] = artifact["refUrl"]
        artifact["actualImplUrl"] = artifact["actualRefUrl"]
        artifact["captureReceipt"]["impl"] = json.loads(
            json.dumps(artifact["captureReceipt"]["ref"])
        )
    elif tamper == "http-error":
        artifact["captureReceipt"]["impl"]["responseStatus"] = 500
    else:
        artifact["captureReceipt"]["impl"]["actualUrl"] = (
            "https://impl.example.test/wrong-route"
        )
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(artifact),
        encoding="utf-8",
    )

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "fail"
    assert "semantically inconsistent artifact" in (results[0].message or "")


def test_runtime_text_sequence_rejects_exact_final_with_phase_catalog_gap(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "strictWarnings": True,
            "requiredChecks": [{
                "id": "runtime-text-sequence",
                "produces": "runtime-text-sequence.json",
                "severity": "warn",
            }],
        }),
        encoding="utf-8",
    )
    artifact = _runtime_text_artifact(
        "pass",
        ["Canonical exact copy"],
        ["Canonical exact copy"],
    )
    transient = {
        "slot": "main>h4:nth-of-type(1)::run(1)",
        "text": "Transient canonical copy",
        "tag": "H4",
        "initialViewport": False,
    }
    artifact["ref"]["samples"][0] = [
        transient,
        *artifact["ref"]["records"],
    ]
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(artifact),
        encoding="utf-8",
    )

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "fail"
    assert "phase-window rendered text catalogs" in (
        results[0].message or ""
    )


@pytest.mark.parametrize(
    "tamper",
    ["pre-window-only", "persistent-impl", "absence-before-first-present"],
)
def test_runtime_text_sequence_rejects_out_of_window_or_persistent_phase_copy(
    tmp_path: Path,
    tamper: str,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "strictWarnings": True,
            "requiredChecks": [{
                "id": "runtime-text-sequence",
                "produces": "runtime-text-sequence.json",
                "severity": "warn",
            }],
        }),
        encoding="utf-8",
    )
    artifact = _runtime_phase_artifact()
    if tamper == "pre-window-only":
        present = artifact["ref"]["samples"][0]
        absent = artifact["ref"]["records"]
        artifact["ref"]["samples"] = [present, absent, absent, absent]
        artifact["ref"]["phaseSampleStartIndex"] = 1
    elif tamper == "persistent-impl":
        present = artifact["impl"]["records"]
        artifact["impl"]["samples"] = [present, present]
        artifact["impl"]["phaseSampleStartIndex"] = 0
    else:
        present = artifact["impl"]["records"]
        absent = artifact["ref"]["records"]
        artifact["impl"]["samples"] = [absent, absent, present, present]
        artifact["impl"]["phaseSampleStartIndex"] = 0
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(artifact),
        encoding="utf-8",
    )

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "fail"
    assert "semantically inconsistent artifact" in (results[0].message or "")


def test_runtime_text_sequence_rejects_protected_reference_phase_occurrence(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "strictWarnings": True,
            "requiredChecks": [{
                "id": "runtime-text-sequence",
                "produces": "runtime-text-sequence.json",
                "severity": "warn",
            }],
        }),
        encoding="utf-8",
    )
    artifact = _runtime_phase_artifact()
    artifact["ref"]["samples"][0][10]["initialViewport"] = True
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(artifact),
        encoding="utf-8",
    )

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "fail"
    assert "semantically inconsistent artifact" in (results[0].message or "")


@pytest.mark.parametrize(
    "tamper",
    ["protected-implementation", "final-duplicate", "sample-duplicate", "coherence"],
)
def test_runtime_text_sequence_rejects_phase_evidence_integrity_errors(
    tmp_path: Path,
    tamper: str,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "strictWarnings": True,
            "requiredChecks": [{
                "id": "runtime-text-sequence",
                "produces": "runtime-text-sequence.json",
                "severity": "warn",
            }],
        }),
        encoding="utf-8",
    )
    artifact = (
        _runtime_reverse_phase_artifact()
        if tamper == "protected-implementation"
        else _runtime_phase_artifact()
    )
    if tamper == "protected-implementation":
        artifact["impl"]["samples"][0][10]["initialViewport"] = True
    elif tamper == "final-duplicate":
        artifact["impl"]["records"][10]["slot"] = (
            artifact["impl"]["records"][9]["slot"]
        )
    elif tamper == "sample-duplicate":
        artifact["ref"]["samples"][0][10]["slot"] = (
            artifact["ref"]["samples"][0][9]["slot"]
        )
    else:
        artifact["ref"]["samples"][-1] = artifact["ref"]["samples"][0]
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(artifact),
        encoding="utf-8",
    )

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "fail"
    assert "semantically inconsistent artifact" in (results[0].message or "")


@pytest.mark.parametrize("invalid", [True, 0.0])
def test_runtime_text_sequence_rejects_non_integer_phase_evidence(
    tmp_path: Path,
    invalid: object,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "strictWarnings": True,
            "requiredChecks": [{
                "id": "runtime-text-sequence",
                "produces": "runtime-text-sequence.json",
                "severity": "warn",
            }],
        }),
        encoding="utf-8",
    )
    artifact = _runtime_phase_artifact()
    artifact["phaseVariance"]["proof"][0]["gapIndex"] = invalid
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(artifact),
        encoding="utf-8",
    )

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "fail"
    assert "semantically inconsistent artifact" in (results[0].message or "")


def test_runtime_text_sequence_verifies_variant_side_occurrence_anchors(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "strictWarnings": True,
            "requiredChecks": [{
                "id": "runtime-text-sequence",
                "produces": "runtime-text-sequence.json",
                "severity": "warn",
            }],
        }),
        encoding="utf-8",
    )
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(_runtime_shifted_anchor_phase_artifact()),
        encoding="utf-8",
    )
    _write_runtime_text_provenance(ref)

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "pass"


def test_runtime_text_sequence_accepts_independently_verified_reverse_phase(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "strictWarnings": True,
            "requiredChecks": [{
                "id": "runtime-text-sequence",
                "produces": "runtime-text-sequence.json",
                "severity": "warn",
            }],
        }),
        encoding="utf-8",
    )
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(_runtime_reverse_phase_artifact()),
        encoding="utf-8",
    )
    _write_runtime_text_provenance(ref)

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "pass"


def test_runtime_text_sequence_rejects_unproven_reverse_phase(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "strictWarnings": True,
            "requiredChecks": [{
                "id": "runtime-text-sequence",
                "produces": "runtime-text-sequence.json",
                "severity": "warn",
            }],
        }),
        encoding="utf-8",
    )
    artifact = _runtime_reverse_phase_artifact()
    artifact["impl"]["samples"] = [artifact["impl"]["records"]]
    artifact["phaseVariance"]["implementationSampleCount"] = 1
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(artifact),
        encoding="utf-8",
    )

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "fail"
    assert "semantically inconsistent artifact" in (results[0].message or "")


def test_runtime_text_sequence_rejects_static_reverse_phase_waiver(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "strictWarnings": True,
            "requiredChecks": [{
                "id": "runtime-text-sequence",
                "produces": "runtime-text-sequence.json",
                "severity": "warn",
            }],
        }),
        encoding="utf-8",
    )
    artifact = _runtime_reverse_phase_artifact()
    artifact["ref"]["samples"] = [artifact["ref"]["records"]]
    artifact["phaseVariance"]["referenceSampleCount"] = 1
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(artifact),
        encoding="utf-8",
    )

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "fail"
    assert "semantically inconsistent artifact" in (results[0].message or "")


@pytest.mark.parametrize("sequence", ["a-b", "empty-a"])
def test_runtime_text_sequence_rejects_nonrecurrent_reverse_proof(
    tmp_path: Path,
    sequence: str,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "strictWarnings": True,
            "requiredChecks": [{
                "id": "runtime-text-sequence",
                "produces": "runtime-text-sequence.json",
                "severity": "warn",
            }],
        }),
        encoding="utf-8",
    )
    artifact = _runtime_reverse_phase_artifact()
    if sequence == "a-b":
        variant_b = json.loads(json.dumps(artifact["ref"]["records"]))
        variant_b[10]["text"] = "Different reference variant"
        artifact["ref"]["samples"] = [
            artifact["ref"]["records"],
            variant_b,
            artifact["ref"]["records"],
        ]
    else:
        artifact["ref"]["samples"] = [
            artifact["impl"]["records"],
            artifact["ref"]["records"],
        ]
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(artifact),
        encoding="utf-8",
    )

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "fail"
    assert "semantically inconsistent artifact" in (results[0].message or "")


@pytest.mark.parametrize(
    "tamper",
    [
        "unknown",
        "cross-gap",
        "cross-occurrence",
        "same-slot-wrong-gap",
        "initial",
        "heading",
        "proof",
        "polarity",
        "exhausted",
    ],
)
def test_runtime_text_sequence_rejects_forged_phase_proof(
    tmp_path: Path,
    tamper: str,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "strictWarnings": True,
            "requiredChecks": [{
                "id": "runtime-text-sequence",
                "produces": "runtime-text-sequence.json",
                "severity": "warn",
            }],
        }),
        encoding="utf-8",
    )
    artifact = _runtime_phase_artifact()
    impl_variant = artifact["impl"]["records"][10]
    if tamper == "unknown":
        impl_variant["text"] = "Unknown variant"
        artifact["impl"]["blocks"][10] = "Unknown variant"
        artifact["phaseVariance"]["proof"][0]["candidate"]["text"] = (
            "Unknown variant"
        )
    elif tamper == "cross-gap":
        impl_variant["slot"] = "footer>h4::run(1)"
        artifact["phaseVariance"]["proof"][0]["candidate"]["slot"] = (
            "footer>h4::run(1)"
        )
    elif tamper == "cross-occurrence":
        impl_variant["slot"] = "main>h4::run(2)"
        artifact["phaseVariance"]["proof"][0]["candidate"]["slot"] = (
            "main>h4::run(2)"
        )
    elif tamper == "same-slot-wrong-gap":
        observed = artifact["ref"]["samples"][0]
        moved = observed.pop(10)
        observed.insert(15, moved)
    elif tamper == "initial":
        impl_variant["initialViewport"] = True
    elif tamper == "heading":
        impl_variant["tag"] = "H2"
    elif tamper == "proof":
        artifact["phaseVariance"]["proof"][0][
            "matchedReferenceCandidatePresentSample"
        ] = 1
    elif tamper == "polarity":
        artifact["phaseVariance"]["proof"][0]["referenceCyclePolarity"] = (
            "absent-present-absent"
        )
    elif tamper == "exhausted":
        artifact["ref"]["samples"] = [artifact["ref"]["records"]]
        artifact["phaseVariance"]["referenceSampleCount"] = 1
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(artifact),
        encoding="utf-8",
    )

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "fail"
    assert "semantically inconsistent artifact" in (results[0].message or "")


@pytest.mark.parametrize("strict_warnings", [False, True])
def test_runtime_text_sequence_exact_mismatch_always_blocks(
    tmp_path: Path,
    strict_warnings: bool,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "strictWarnings": strict_warnings,
                "requiredChecks": [
                    {
                        "id": "runtime-text-sequence",
                        "produces": "runtime-text-sequence.json",
                        "severity": "warn",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(
            _runtime_text_artifact(
                "fail",
                ["Canonical exact copy"],
                ["Canonical exact copy changed"],
            )
        ),
        encoding="utf-8",
    )

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "fail"


def test_runtime_text_sequence_rejects_forged_pass_with_wrong_copy(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "strictWarnings": True,
                "requiredChecks": [
                    {
                        "id": "runtime-text-sequence",
                        "produces": "runtime-text-sequence.json",
                        "severity": "warn",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    artifact = _runtime_text_artifact(
        "pass",
        ["Canonical copy"],
        ["Forged different copy"],
    )
    artifact["comparison"] = {
        "lcsLength": 1,
        "orderedSimilarity": 1.0,
        "missingCount": 0,
        "missingRatio": 0.0,
        "extraCount": 0,
    }
    artifact["violations"] = []
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(artifact),
        encoding="utf-8",
    )

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "fail"
    assert "semantically inconsistent artifact" in (results[0].message or "")


@pytest.mark.parametrize("strict_warnings", [False, True])
def test_runtime_text_sequence_statusless_artifact_never_passes(
    tmp_path: Path,
    strict_warnings: bool,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "strictWarnings": strict_warnings,
                "requiredChecks": [
                    {
                        "id": "runtime-text-sequence",
                        "produces": "runtime-text-sequence.json",
                        "severity": "warn",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "runtime-text-sequence.json").write_text("{}", encoding="utf-8")

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "fail"
    assert "malformed artifact" in (results[0].message or "")


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        '{"status":"pass","orderedSimilarity":NaN}',
        '{"status":"pass","orderedSimilarity":Infinity}',
        '{"status":"pass","orderedSimilarity":-Infinity}',
    ],
    ids=["malformed", "nan", "positive-infinity", "negative-infinity"],
)
def test_runtime_text_sequence_requires_strict_json(
    tmp_path: Path,
    raw: str,
) -> None:
    """Malformed or non-finite runtime-text evidence must fail closed."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_impl_fixture(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "runtime-text-sequence",
                        "produces": "runtime-text-sequence.json",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "runtime-text-sequence.json").write_text(raw, encoding="utf-8")

    results = Gate(ref)._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "fail"
    assert "not strict JSON" in (results[0].message or "")


# ── Wave 5 seam C — scroll-coverage infra-skip fails closed ──────────────


def _scroll_coverage_plan(severity: str = "block") -> dict:
    return {
        "schemaVersion": 1,
        "signals": {"hasScrollScrub": True},
        "requiredChecks": [
            {"id": "scroll-coverage", "produces": "scroll-coverage.json",
             "reason": "scroll-scrub declared", "severity": severity}
        ],
    }


def _write_scroll_linked_dump(ref: Path) -> None:
    """animation-runtime-dump.json proving OBSERVED scroll-linked motion (styles
    that vary across scroll) — the signal the escalation now gates on."""
    (ref / "animation-runtime-dump.json").write_text(json.dumps({
        "scrollLinkedStyles": [
            {"domIndex": 4, "props": {"opacity": ["0", "0.5", "1"]}}
        ],
    }))


def test_scroll_coverage_infra_skip_fails_closed_when_motion_observed(tmp_path: Path) -> None:
    """An infra skip (impl unreachable) on a page with OBSERVED scroll-linked
    motion, with the impl confirmed up (runtime-env passed), leaves that motion
    UNVERIFIED — fail closed at block severity."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps(_scroll_coverage_plan("block")))
    (ref / "runtime-env.json").write_text(json.dumps({"status": "pass"}))
    _write_scroll_linked_dump(ref)
    (ref / "scroll-coverage.json").write_text(json.dumps({
        "status": "skip", "skipClass": "infra",
        "reason": "impl URL not reachable: http://localhost:3000",
    }))
    failures = [r for r in Gate(ref).gate_post_implement() if r.status == "fail"]
    assert any(
        r.label == "required: scroll-coverage" and "could not be measured" in r.message
        for r in failures
    ), [(r.label, r.message[:80]) for r in failures]


def test_scroll_coverage_infra_skip_stays_pass_without_observed_motion(tmp_path: Path) -> None:
    """The over-broad plan signal hasScrollScrub flips true on a mere smooth-
    scroll library (Lenis) with NO scroll-linked transforms. When the runtime
    dump proves no scroll-linked motion, an infra skip must NOT fail closed —
    otherwise every Lenis-only page bricks at strict closeout."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps(_scroll_coverage_plan("block")))
    (ref / "runtime-env.json").write_text(json.dumps({"status": "pass"}))
    # Dump present but scrollLinkedStyles is null (Lenis smooth-scroll only).
    (ref / "animation-runtime-dump.json").write_text(json.dumps({"scrollLinkedStyles": None}))
    (ref / "scroll-coverage.json").write_text(json.dumps({
        "status": "skip", "skipClass": "infra",
        "reason": "impl URL not reachable: http://localhost:3000",
    }))
    failures = [r for r in Gate(ref).gate_post_implement() if r.status == "fail"]
    assert not any(r.label == "required: scroll-coverage" for r in failures), failures


def test_scroll_coverage_page_shape_skip_stays_pass(tmp_path: Path) -> None:
    """A page-shape skip (short/static page) is a legitimate no-op — it must NOT
    fail even at block severity with scroll-scrub declared."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps(_scroll_coverage_plan("block")))
    (ref / "runtime-env.json").write_text(json.dumps({"status": "pass"}))
    (ref / "scroll-coverage.json").write_text(json.dumps({
        "status": "skip", "skipClass": "page-shape",
        "reason": "only 3 regions/sections — coverage redundant for short pages",
    }))
    failures = [r for r in Gate(ref).gate_post_implement() if r.status == "fail"]
    assert not any(r.label == "required: scroll-coverage" for r in failures), failures


def test_scroll_coverage_infra_skip_lenient_when_runtime_env_unconfirmed(tmp_path: Path) -> None:
    """Without a positively-passing runtime-env.json the impl was never confirmed
    up, so an infra skip is treated as a mid-build blip, not motion debt — stays
    a pass to avoid bricking iteration."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps(_scroll_coverage_plan("block")))
    # runtime-env.json intentionally ABSENT.
    (ref / "scroll-coverage.json").write_text(json.dumps({
        "status": "skip", "skipClass": "infra",
        "reason": "impl URL not reachable: http://localhost:3000",
    }))
    failures = [r for r in Gate(ref).gate_post_implement() if r.status == "fail"]
    assert not any(r.label == "required: scroll-coverage" for r in failures), failures


def test_scroll_coverage_infra_skip_fails_closed_on_scrolltrigger_only(tmp_path: Path) -> None:
    """GSAP ScrollTrigger scrubs a property (clip-path/filter/pin) that leaves no
    varying inline style, so scrollLinkedStyles is null — but a non-null
    scrollTrigger IS observed scroll-linked motion (and, unlike hasScrollScrub,
    a Lenis-only site has scrollTrigger==null). An infra skip must fail closed."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps(_scroll_coverage_plan("block")))
    (ref / "runtime-env.json").write_text(json.dumps({"status": "pass"}))
    (ref / "animation-runtime-dump.json").write_text(json.dumps({
        "scrollLinkedStyles": None,
        "scrollTrigger": [{"trigger": ".hero", "start": 0, "end": 600}],
    }))
    (ref / "scroll-coverage.json").write_text(json.dumps({
        "status": "skip", "skipClass": "infra",
        "reason": "batch-compare produced no rows — capture failed?",
    }))
    failures = [r for r in Gate(ref).gate_post_implement() if r.status == "fail"]
    assert any(
        r.label == "required: scroll-coverage" and "could not be measured" in r.message
        for r in failures
    ), [(r.label, r.message[:80]) for r in failures]


def test_scroll_coverage_infra_skip_is_advisory_at_warn_severity(tmp_path: Path) -> None:
    """In fast iteration (default warn severity) the infra skip surfaces as a
    WARN — loud but non-blocking — not a silent pass and not a hard fail."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps(_scroll_coverage_plan("warn")))
    (ref / "runtime-env.json").write_text(json.dumps({"status": "pass"}))
    _write_scroll_linked_dump(ref)
    (ref / "scroll-coverage.json").write_text(json.dumps({
        "status": "skip", "skipClass": "infra",
        "reason": "batch-compare produced no rows — capture failed?",
    }))
    results = Gate(ref).gate_post_implement()
    assert not any(
        r.label == "required: scroll-coverage" and r.status == "fail" for r in results
    ), [r for r in results if r.status == "fail"]
    assert any(
        r.label == "required: scroll-coverage" and r.status == "warn" for r in results
    ), [(r.label, r.status) for r in results if r.label == "required: scroll-coverage"]


# ── Wave 5 seam D — transition-trajectory empty measurement fails closed ──


def _trajectory_plan() -> dict:
    return {
        "schemaVersion": 1,
        "signals": {"hasScrollScrub": True},
        "requiredChecks": [
            {"id": "transition-trajectory",
             "produces": "transitions/trajectory-result.txt",
             "reason": "scroll-scrub declared", "severity": "block"}
        ],
    }


def test_transition_trajectory_empty_measurement_fails_closed(tmp_path: Path) -> None:
    """A trajectory artifact with a vacuous '✅ all 0 sample points' summary and
    zero table rows, on a scroll-declared page, is the 'never measured' pass —
    fail closed (the summary line's ✅ must not be counted as a measurement)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps(_trajectory_plan()))
    (ref / "transitions").mkdir(exist_ok=True)
    (ref / "transitions" / "trajectory-result.txt").write_text(
        "# trajectory-compare (AE mode)\n# generated: 2026-07-03\n\n"
        "✅ all 0 sample points within ceiling\n"
    )
    failures = [r for r in Gate(ref).gate_post_implement() if r.status == "fail"]
    assert any(
        r.label == "required: transition-trajectory" and "0 sample-point rows" in r.message
        for r in failures
    ), [(r.label, r.message[:80]) for r in failures]


def test_transition_trajectory_no_scroll_skip_passes(tmp_path: Path) -> None:
    """The legitimate no-scroll skip sentinel (neither page scrolls) has zero
    table rows but must stay a pass."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps(_trajectory_plan()))
    (ref / "transitions").mkdir(exist_ok=True)
    (ref / "transitions" / "trajectory-result.txt").write_text(
        "# trajectory-compare: no-scroll page\n"
        "✅ skipped: neither page scrolls; trajectory check N/A\n"
    )
    failures = [r for r in Gate(ref).gate_post_implement() if r.status == "fail"]
    assert not any(r.label == "required: transition-trajectory" for r in failures), failures


def test_transition_trajectory_empty_json_is_forge_fails_closed(tmp_path: Path) -> None:
    """The genuine trajectory report is always #-markdown; a JSON `{}` in its
    place parses cleanly and dodges the text-branch sample-row scan. Any
    JSON-parsing trajectory artifact is a forge → fail closed."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps(_trajectory_plan()))
    (ref / "transitions").mkdir(exist_ok=True)
    (ref / "transitions" / "trajectory-result.txt").write_text("{}")
    failures = [r for r in Gate(ref).gate_post_implement() if r.status == "fail"]
    assert any(
        r.label == "required: transition-trajectory" and "json" in r.message.lower()
        for r in failures
    ), [(r.label, r.message[:80]) for r in failures]


def test_transition_trajectory_forged_status_pass_json_fails_closed(tmp_path: Path) -> None:
    """A forged `{"status":"pass"}` JSON would otherwise reach the status
    dispatch and vacuously pass, bypassing the empty-measurement guard (which
    only runs in the JSONDecodeError text branch). The JSON-is-forge guard
    catches it first."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps(_trajectory_plan()))
    (ref / "transitions").mkdir(exist_ok=True)
    (ref / "transitions" / "trajectory-result.txt").write_text('{"status": "pass"}')
    failures = [r for r in Gate(ref).gate_post_implement() if r.status == "fail"]
    assert any(
        r.label == "required: transition-trajectory" and "json" in r.message.lower()
        for r in failures
    ), [(r.label, r.message[:80]) for r in failures]


def test_transition_trajectory_with_measured_rows_passes(tmp_path: Path) -> None:
    """A real trajectory with sample-point table rows (and no ❌) passes."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps(_trajectory_plan()))
    (ref / "transitions").mkdir(exist_ok=True)
    (ref / "transitions" / "trajectory-result.txt").write_text(
        "# trajectory-compare (AE mode)\n"
        "| pos | AE | AE/Mpx | status |\n"
        "| 0% | 120 | 60 | ✅ |\n"
        "| 50% | 300 | 150 | ✅ |\n"
        "✅ all 2 sample points within ceiling\n"
    )
    failures = [r for r in Gate(ref).gate_post_implement() if r.status == "fail"]
    assert not any(r.label == "required: transition-trajectory" for r in failures), failures
