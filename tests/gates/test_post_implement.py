import json
from pathlib import Path

from ui_clone.gate import Gate

from ._helpers import (
    _post_implement_baseline,
)


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
        json.dumps({
            "schemaVersion": 1,
            "requiredChecks": [{
                "id": "html-paste",
                "produces": "html-paste.json",
                "reason": "Universal anti-cheat",
                "severity": "block",
            }],
        }),
        encoding="utf-8",
    )
    (ref / "html-paste.json").write_text(json.dumps({"status": "pass"}))
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    transitions = ref / "transitions"
    transitions.mkdir()
    (transitions / "result.txt").write_text(
        "Transition compare: 1 PASS, 0 FAIL\n"
        "✅ PASS .fixture\n",
        encoding="utf-8",
    )
    (ref / "visual-debug-stamp.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "passed": True,
            "exitCode": 0,
            "totalChecks": 4,
            "totalFail": 0,
            "phaseE": False,
        }),
        encoding="utf-8",
    )

    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"gate_post_implement must pass with required files present: {failures}"



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


def test_sections_result_zero_pass_fails_post_implement(tmp_path: Path) -> None:
    """Loop-23 paradox: aux gates pass while sections/result.txt is 0 PASS / 12 FAIL.

    gate_post_implement must aggregate the canonical visual-diff result
    instead of silently passing when the auxiliary gates report green.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    sections = ref / "sections"
    sections.mkdir(exist_ok=True)
    (sections / "result.txt").write_text(
        "| Section | AE | Status |\n"
        "|---------|----|--------|\n"
        "| hero    | 1M | ❌      |\n"
        "\n"
        "**Result: 0 PASS, 12 FAIL, 3 SKIP, 0 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert any("sections/result.txt visual health" in r.label for r in failures), (
        f"0 PASS / 12 FAIL must produce a post-implement fail, got: "
        f"{[(r.label, r.status) for r in results]}"
    )


def test_sections_result_zero_pass_zero_fail_also_fails(tmp_path: Path) -> None:
    """Empty pipeline shape: section-compare ran, emitted result.txt, but
    has 0 PASS / 0 FAIL (no rows). Universalised per codex review — any
    `pass_count == 0` blocks, not just `pass_count == 0 AND fail_count >= 1`.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    sections = ref / "sections"
    sections.mkdir(exist_ok=True)
    (sections / "result.txt").write_text(
        "**Result: 0 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert any("sections/result.txt visual health" in r.label for r in failures), (
        f"0 PASS / 0 FAIL (empty pipeline) must also block: "
        f"{[(r.label, r.status) for r in results]}"
    )


def test_sections_result_one_pass_does_not_block(tmp_path: Path) -> None:
    """sections/result.txt with ≥1 PASS keeps the post-implement gate clean
    on the new aggregate check (other checks may still complain)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    sections = ref / "sections"
    sections.mkdir(exist_ok=True)
    (sections / "result.txt").write_text(
        "**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert not any("sections/result.txt visual health" in r.label for r in failures), (
        f"≥1 PASS must not trigger the new aggregate check: "
        f"{[(r.label, r.status) for r in results]}"
    )


def test_sections_result_absent_blocks_post_implement(tmp_path: Path) -> None:
    """No sections/result.txt yet means section-compare was skipped."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "sections" / "result.txt").unlink()
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert any("sections/result.txt visual health" in r.label for r in failures)


def test_sections_result_missing_fails_post_implement(tmp_path: Path) -> None:
    """Regression: post-implement must not pass when section-compare was skipped."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "sections" / "result.txt").unlink()

    failures = [r for r in Gate(ref).gate_post_implement() if r.status == "fail"]

    assert any(r.label == "sections/result.txt visual health" for r in failures)


def test_visual_debug_stamp_missing_when_sections_result_exists(tmp_path: Path) -> None:
    """Bare section-compare.sh produced result.txt without going through the
    canonical auto-verify.sh umbrella. visual-debug-stamp.json absent while
    sections/result.txt exists (ANY result — pass, fail, or all-fail) → block.

    Trigger is "result.txt exists" so codex cannot skip the universal anti-
    cheat baseline by intentionally failing sections."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    # Baseline writes the stamp; remove it to reproduce the cheat shape.
    (ref / "visual-debug-stamp.json").unlink()
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail" and "visual-debug-stamp" in r.label]
    assert failures, (
        f"sections result.txt without stamp must block: "
        f"{[(r.label, r.status) for r in results]}"
    )


def test_visual_debug_stamp_required_even_when_sections_all_fail(tmp_path: Path) -> None:
    """0-PASS shape: section-compare ran but emitted no successful row — must
    still require the canonical auto-verify stamp (the baseline-bypass
    workaround where the agent intentionally accepts visual failure to
    avoid the anti-cheat checks is closed)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "sections" / "result.txt").write_text(
        "**Result: 0 PASS, 11 FAIL, 3 SKIP, 0 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    (ref / "visual-debug-stamp.json").unlink()
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail" and "visual-debug-stamp" in r.label]
    assert failures, (
        f"0-PASS sections without stamp must block: "
        f"{[(r.label, r.status) for r in results]}"
    )


def test_visual_debug_stamp_present_clears_check(tmp_path: Path) -> None:
    """Stamp with passed=true clears the new check."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "visual-debug-stamp.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "passed": True,
            "exitCode": 0,
            "totalChecks": 4,
            "totalFail": 0,
            "phaseE": False,
        }),
        encoding="utf-8",
    )
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail" and "visual-debug-stamp" in r.label]
    assert not failures, (
        f"stamp with passed=true must not trigger fail: "
        f"{[(r.label, r.status) for r in results]}"
    )


def test_visual_debug_stamp_passed_false_blocks(tmp_path: Path) -> None:
    """Stamp with passed=false (auto-verify exited 1) blocks."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "visual-debug-stamp.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "passed": False,
            "exitCode": 1,
            "totalChecks": 4,
            "totalFail": 2,
            "phaseE": False,
        }),
        encoding="utf-8",
    )
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail" and "visual-debug-stamp" in r.label]
    assert failures, "stamp with passed=false must trigger fail"


def test_phase_e_result_passed_false_blocks(tmp_path: Path) -> None:
    """phase-e-result.json with passed=false (LLM rejected) blocks the gate."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "phase-e-result.json").write_text(
        json.dumps({
            "passed": False,
            "reason": "impl appears to be a static HTML paste of ref",
        }),
        encoding="utf-8",
    )
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail" and "phase-e-result" in r.label]
    assert failures, "Phase E rejection must trigger fail"


def test_phase_e_result_absent_silent(tmp_path: Path) -> None:
    """Phase E artifact absent → silent (expensive optional run, not required)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail" and "phase-e-result" in r.label]
    assert not failures, "absent Phase E must not block"


def test_transitions_result_missing_fails_post_implement_when_spec_has_transitions(
    tmp_path: Path,
) -> None:
    """Regression: post-implement must not pass when transition-compare was skipped."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "transitions" / "result.txt").unlink()

    failures = [r for r in Gate(ref).gate_post_implement() if r.status == "fail"]

    assert any(r.label == "transitions/result.txt visual health" for r in failures)
