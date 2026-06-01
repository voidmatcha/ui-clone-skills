import json
from pathlib import Path

import pytest

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
    # Seed fixture.js into bundles/ so _check_spec_bundle_grounding passes.
    (ref / "bundles").mkdir()
    (ref / "bundles" / "fixture.js").write_text("// fixture bundle", encoding="utf-8")
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


def test_post_implement_enforces_forensic_preservation_when_required(tmp_path: Path) -> None:
    """CSS-module-heavy refs must not pass with a freehand class system."""
    loop = tmp_path / "loop"
    ref = loop / "tmp" / "ref" / "realfood"
    ref.mkdir(parents=True)
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "html-paste",
                        "produces": "html-paste.json",
                        "reason": "Universal anti-cheat",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "html-paste.json").write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "forensicPreservation": {
                    "required": True,
                    "strategy": "ref-derived-jsx-with-local-css",
                    "classSignatureCount": 120,
                    "copyCssTo": "src/ref-css",
                    "rules": [
                        "copy ref css/*.css into impl/src/ref-css and import them before overrides",
                        "preserve original CSS-module className tokens",
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    impl = loop / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text('{"name":"clone"}', encoding="utf-8")
    (ref / "html-paste.json").write_text(
        json.dumps({"status": "pass", "implRoot": str(impl)}), encoding="utf-8"
    )
    (impl / "src" / "main.tsx").write_text(
        "export function App(){return <main className='hero stats pyramid'>Hi</main>}\n",
        encoding="utf-8",
    )

    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]

    assert any(r.label == "forensic-preservation-compliance" for r in failures)
    assert any("CSS-module" in r.message and "src/ref-css" in r.message for r in failures)


def test_post_implement_accepts_ref_css_and_preserved_class_tokens(tmp_path: Path) -> None:
    """A ref-derived JSX path satisfies the forensic preservation gate."""
    loop = tmp_path / "loop"
    ref = loop / "tmp" / "ref" / "realfood"
    ref.mkdir(parents=True)
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "html-paste",
                        "produces": "html-paste.json",
                        "reason": "Universal anti-cheat",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "html-paste.json").write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "forensicPreservation": {
                    "required": True,
                    "strategy": "ref-derived-jsx-with-local-css",
                    "classSignatureCount": 40,
                    "copyCssTo": "src/ref-css",
                }
            }
        ),
        encoding="utf-8",
    )
    impl = loop / "impl"
    (impl / "src" / "ref-css").mkdir(parents=True)
    (impl / "package.json").write_text('{"name":"clone"}', encoding="utf-8")
    (ref / "html-paste.json").write_text(
        json.dumps({"status": "pass", "implRoot": str(impl)}), encoding="utf-8"
    )
    (impl / "src" / "ref-css" / "app.css").write_text(
        ".dga_section__aaaa{} .dga_hero__bbbb{}\n", encoding="utf-8"
    )
    tokens = " ".join(f"dga_part{i}__abcd{i:02d}" for i in range(12))
    (impl / "src" / "main.tsx").write_text(
        "import './ref-css/app.css';\n"
        f"export function App(){{return <main className=\"{tokens}\">Hi</main>}}\n",
        encoding="utf-8",
    )

    results = Gate(ref).gate_post_implement()

    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"expected forensic preservation path to pass: {failures}"


def test_post_implement_blocks_forensic_mode_when_ref_css_artifacts_missing(
    tmp_path: Path,
) -> None:
    """Missing ref CSS artifacts must not be bypassed with handwritten local CSS."""
    loop = tmp_path / "loop"
    ref = loop / "tmp" / "ref" / "realfood"
    ref.mkdir(parents=True)
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "html-paste",
                        "produces": "html-paste.json",
                        "reason": "Universal anti-cheat",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    impl = loop / "impl"
    (impl / "src" / "ref-css").mkdir(parents=True)
    (impl / "package.json").write_text('{"name":"clone"}', encoding="utf-8")
    (ref / "html-paste.json").write_text(
        json.dumps({"status": "pass", "implRoot": str(impl)}), encoding="utf-8"
    )
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "forensicPreservation": {
                    "required": True,
                    "strategy": "ref-derived-jsx-with-local-css",
                    "classSignatureCount": 80,
                    "cssBytes": 0,
                    "cssFiles": [],
                    "cssArtifactStatus": "missing",
                    "missingCssArtifacts": True,
                    "blockedUntilCssArtifacts": True,
                    "copyCssTo": "src/ref-css",
                }
            }
        ),
        encoding="utf-8",
    )
    (impl / "src" / "ref-css" / "app.css").write_text(
        ".dga_section__aaaa{} .dga_hero__bbbb{}\n", encoding="utf-8"
    )
    tokens = " ".join(f"dga_part{i}__abcd{i:02d}" for i in range(25))
    (impl / "src" / "main.tsx").write_text(
        "import './ref-css/app.css';\n"
        f"export function App(){{return <main className=\"{tokens}\">Hi</main>}}\n",
        encoding="utf-8",
    )

    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]

    assert any(r.label == "forensic-preservation-compliance" for r in failures)
    assert any("ref CSS artifacts" in r.message and "missing" in r.message for r in failures)


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


def test_sections_result_structural_only_blocks(tmp_path: Path) -> None:
    """Phase 2 (genuine-fidelity convergence): a pure-substitution result with
    0 genuine pixel PASS — even at 0 FAIL — is the STRUCTURAL_ONLY gaming vector
    (footer 'N PASS' built entirely of substituted rows). Convergence requires
    >=1 genuine pixel ✅, so post-implement MUST block 0-genuine-PASS results."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    sections = ref / "sections"
    sections.mkdir(exist_ok=True)
    (sections / "result.txt").write_text(
        "**Result: 0 PASS, 0 FAIL, 0 SKIP, 12 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert any("sections/result.txt visual health" in r.label for r in failures), (
        f"0 genuine PASS (pure STRUCTURAL_ONLY) must block post-implement: "
        f"{[(r.label, r.status) for r in results]}"
    )


def test_sections_result_genuine_pass_with_substitution_does_not_block(tmp_path: Path) -> None:
    """Counterpart to the Phase-2 block: >=1 genuine pixel PASS alongside
    legitimate substitution (commercial fonts / CDN media) is fine. Phase 2
    blocks only 0-genuine results, not all substitution."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    sections = ref / "sections"
    sections.mkdir(exist_ok=True)
    (sections / "result.txt").write_text(
        "**Result: 3 PASS, 0 FAIL, 0 SKIP, 5 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert not any("sections/result.txt visual health" in r.label for r in failures), (
        f">=1 genuine PASS must not block even with substitution present: "
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


def test_visual_debug_stamp_orphaned_provisional_blocks(
    tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
) -> None:
    """Provisional stamp WITHOUT matching env var = crashed auto-verify; reject.

    Stamp-race fix (codex review 2026-05-27): a previous auto-verify.sh run
    crashed mid-execution after writing the provisional stamp (`passed=true,
    provisional=true`) but before writing the final verdict. Without this
    guard, the next post-implement check would falsely trust the stale
    provisional and grant pass. The env var `UI_RE_AUTOVERIFY_INFLIGHT` is
    NOT set (no auto-verify in flight), so the gate must reject.
    """
    monkeypatch.delenv("UI_RE_AUTOVERIFY_INFLIGHT", raising=False)
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "visual-debug-stamp.json").write_text(
        json.dumps({
            "schemaVersion": 2,
            "passed": True,
            "exitCode": 0,
            "totalChecks": 0,
            "totalFail": 0,
            "phaseE": False,
            "provisional": True,
            "invocationId": "stale-from-crashed-run",
        }),
        encoding="utf-8",
    )
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail" and "visual-debug-stamp" in r.label]
    assert failures, "orphaned provisional stamp must be rejected"
    assert any("provisional" in (r.message or "").lower() for r in failures), (
        "failure message should explain provisional cause"
    )


def test_visual_debug_stamp_inflight_provisional_accepted(
    tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
) -> None:
    """Provisional stamp WITH matching env var = current auto-verify run; trust.

    During auto-verify's own execution, it writes a provisional stamp and
    then calls the post-implement gate. Both the stamp's invocationId and
    the env var are set to the same uuid for this run, so the gate accepts
    the provisional verdict — otherwise auto-verify could never clear its
    own gate (chicken-and-egg).
    """
    invocation_id = "00000000-1111-2222-3333-444444444444"
    monkeypatch.setenv("UI_RE_AUTOVERIFY_INFLIGHT", invocation_id)
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "visual-debug-stamp.json").write_text(
        json.dumps({
            "schemaVersion": 2,
            "passed": True,
            "exitCode": 0,
            "totalChecks": 0,
            "totalFail": 0,
            "phaseE": False,
            "provisional": True,
            "invocationId": invocation_id,
        }),
        encoding="utf-8",
    )
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail" and "visual-debug-stamp" in r.label]
    assert not failures, (
        f"in-flight provisional stamp (matching env var) must clear: "
        f"{[(r.label, r.status, r.message) for r in results]}"
    )


def test_visual_debug_stamp_mismatched_invocation_id_blocks(
    tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
) -> None:
    """Provisional stamp with WRONG invocationId vs env var = stale; reject.

    Failure mode: a prior auto-verify run wrote its provisional stamp,
    crashed, and left it on disk. A new auto-verify run starts (exporting a
    NEW invocationId env var) but somehow fails to overwrite the stale
    stamp before the gate check fires. The mismatch must be detected and
    treated as if no env var was set.
    """
    monkeypatch.setenv("UI_RE_AUTOVERIFY_INFLIGHT", "new-run-id-aaa")
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "visual-debug-stamp.json").write_text(
        json.dumps({
            "schemaVersion": 2,
            "passed": True,
            "exitCode": 0,
            "totalChecks": 0,
            "totalFail": 0,
            "phaseE": False,
            "provisional": True,
            "invocationId": "old-run-id-bbb",
        }),
        encoding="utf-8",
    )
    gate = Gate(ref)
    results = gate.gate_post_implement()
    failures = [r for r in results if r.status == "fail" and "visual-debug-stamp" in r.label]
    assert failures, "mismatched invocationId must be rejected even with env var set"


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
    """Regression: post-implement must not pass when required transition-compare was skipped."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "transition-compare",
                        "produces": "transitions/result.txt",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "transitions" / "result.txt").unlink()

    failures = [r for r in Gate(ref).gate_post_implement() if r.status == "fail"]

    assert any(r.label == "transitions/result.txt visual health" for r in failures)


def test_transitions_result_missing_is_not_required_for_scroll_only_motion(
    tmp_path: Path,
) -> None:
    """Scroll/splash motion is proven by video/transition-proof, not hover compare."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "transition-proof",
                        "produces": "transition-proof.json",
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
    (ref / "transitions" / "result.txt").unlink()

    failures = [r for r in Gate(ref).gate_post_implement() if r.status == "fail"]

    assert not any(r.label == "transitions/result.txt visual health" for r in failures)


# ── anti-cheat pattern detection (F1 — claude fidelity analysis 2026-05-25) ──


def _setup_impl_root_for_anti_cheat(tmp_path: Path, src_files: dict[str, str]) -> Path:
    """Build a repo layout so find-impl-root.sh resolves impl_root from ref_dir.

    Layout (matches the fallback used by _find_impl_root):
        tmp_path/repo/tmp/ref/ref/   ← ref_dir
        tmp_path/repo/impl/          ← impl_root (via parent-walk fallback)
        tmp_path/repo/impl/src/...   ← src files
        tmp_path/repo/impl/package.json
    """
    repo = tmp_path / "repo"
    ref = repo / "tmp" / "ref" / "ref"
    ref.mkdir(parents=True)
    impl = repo / "impl"
    src = impl / "src"
    src.mkdir(parents=True)
    (impl / "package.json").write_text('{"name":"x"}', encoding="utf-8")
    for relpath, contents in src_files.items():
        f = src / relpath
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(contents, encoding="utf-8")
    return ref


def test_anti_cheat_stub_classname_fails(tmp_path: Path) -> None:
    """Pattern observed in juanmora claude (26-site loop 2026-05-24/25):
    className with -stub suffix on a check-required selector. Must fail."""
    ref = _setup_impl_root_for_anti_cheat(tmp_path, {
        "Hero.tsx": (
            '<button type="button" aria-hidden="true" '
            'className="hero-composite-button-stub" '
            'style={{ width: "1px", height: "1px" }}>menu</button>'
        ),
    })
    _post_implement_baseline(ref)
    failures = [
        r for r in Gate(ref).gate_post_implement()
        if r.status == "fail" and r.label == "anti-cheat-pattern-detection"
    ]
    assert failures, "stub className must trigger anti-cheat detector"


def test_anti_cheat_zero_area_with_data_lottie_fails(tmp_path: Path) -> None:
    """Pattern observed in ordrhealth claude: data-lottie attribute on an
    element with display:none + zero dimensions. Must fail."""
    ref = _setup_impl_root_for_anti_cheat(tmp_path, {
        "Lottie.tsx": (
            '<div data-lottie aria-hidden="true" '
            "style={{ display: 'none', width: 0, height: 0 }}>"
            '<svg width="0" height="0" /></div>'
        ),
    })
    _post_implement_baseline(ref)
    failures = [
        r for r in Gate(ref).gate_post_implement()
        if r.status == "fail" and r.label == "anti-cheat-pattern-detection"
    ]
    assert failures, "data-lottie + zero-area must trigger anti-cheat detector"


def test_anti_cheat_legitimate_sr_only_passes(tmp_path: Path) -> None:
    """sr-only is a legitimate a11y pattern, NOT an anti-cheat shim. Must
    not trigger the detector (false-positive guard)."""
    ref = _setup_impl_root_for_anti_cheat(tmp_path, {
        "Nav.tsx": (
            '<span className="sr-only">Toggle menu</span>'
            '<button className="menu-toggle">Menu</button>'
        ),
    })
    _post_implement_baseline(ref)
    failures = [
        r for r in Gate(ref).gate_post_implement()
        if r.status == "fail" and r.label == "anti-cheat-pattern-detection"
    ]
    assert not failures, f"sr-only must not trigger detector, got: {failures}"


def test_anti_cheat_clean_component_passes(tmp_path: Path) -> None:
    """A normal component with no stub patterns and no zero-area shims must
    not trigger the detector."""
    ref = _setup_impl_root_for_anti_cheat(tmp_path, {
        "Hero.tsx": (
            '<section className="hero"><h1>Welcome</h1>'
            '<p>Real content here.</p></section>'
        ),
    })
    _post_implement_baseline(ref)
    failures = [
        r for r in Gate(ref).gate_post_implement()
        if r.status == "fail" and r.label == "anti-cheat-pattern-detection"
    ]
    assert not failures


def test_anti_cheat_no_impl_root_returns_none(tmp_path: Path) -> None:
    """Capture-phase runs (ref_dir without impl yet) — detector must skip
    silently rather than fail."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    failures = [
        r for r in Gate(ref).gate_post_implement()
        if r.status == "fail" and r.label == "anti-cheat-pattern-detection"
    ]
    assert not failures, "missing impl_root must not produce anti-cheat fail"


# ── spec-bundle grounding (F — claude fidelity analysis 2026-05-25) ──


def _seed_spec_with_chunks(ref: Path, chunks: list[str]) -> None:
    """Replace transition-spec.json with entries citing given source_chunk
    values. Each entry uses a distinct id/target so we can inspect failures
    per entry."""
    transitions = [
        {
            "id": f"t{i}",
            "description": "test entry",
            "trigger": "load",
            "source_chunk": ch,
            "target": f".t{i}",
            "animation": {"property": "opacity", "from": 0, "to": 1, "duration": 0.5},
        }
        for i, ch in enumerate(chunks)
    ]
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": transitions}), encoding="utf-8"
    )


def _seed_bundles(ref: Path, files: dict[str, str]) -> None:
    """Drop given files into ref/bundles/ (or other subdir if relpath has a /)."""
    for relpath, contents in files.items():
        f = ref / relpath if "/" in relpath else ref / "bundles" / relpath
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(contents, encoding="utf-8")


def test_spec_bundle_grounding_passes_when_chunks_exist(tmp_path: Path) -> None:
    """Each source_chunk file present in ref/bundles → grounding passes."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _seed_bundles(ref, {"gsap.min.js": "// gsap stub", "webflow.js": "// wf"})
    _seed_spec_with_chunks(ref, ["gsap.min.js", "webflow.js"])
    failures = [
        r for r in Gate(ref).gate_post_implement()
        if r.status == "fail" and r.label == "spec-bundle-grounding"
    ]
    assert not failures, f"grounded chunks must pass, got: {failures}"


def test_spec_bundle_grounding_fails_when_chunk_missing(tmp_path: Path) -> None:
    """source_chunk references a file not in ref artifacts → fail."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _seed_bundles(ref, {"gsap.min.js": "// gsap"})
    _seed_spec_with_chunks(ref, ["gsap.min.js", "nonexistent.js"])
    failures = [
        r for r in Gate(ref).gate_post_implement()
        if r.status == "fail" and r.label == "spec-bundle-grounding"
    ]
    assert failures, "missing chunk file must trigger spec-bundle fail"
    assert "nonexistent" in failures[0].message


def test_spec_bundle_grounding_handles_concatenated_chunks(tmp_path: Path) -> None:
    """juanmora schema has `"ScrollTrigger.min.js + gsap.min.js"`. Parser
    must split on `+` and check each file individually."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _seed_bundles(ref, {"ScrollTrigger.min.js": "// st", "gsap.min.js": "// g"})
    _seed_spec_with_chunks(ref, ["ScrollTrigger.min.js + gsap.min.js"])
    failures = [
        r for r in Gate(ref).gate_post_implement()
        if r.status == "fail" and r.label == "spec-bundle-grounding"
    ]
    assert not failures


def test_spec_bundle_grounding_strips_parenthetical(tmp_path: Path) -> None:
    """juanmora has `"webflow.js (IX2 actions) + gsap.min.js + ScrollTrigger.min.js"`.
    Parser must strip `(...)` annotation before file-existence check."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _seed_bundles(ref, {
        "webflow.js": "// wf",
        "gsap.min.js": "// g",
        "ScrollTrigger.min.js": "// st",
    })
    _seed_spec_with_chunks(ref, [
        "webflow.js (IX2 actions) + gsap.min.js + ScrollTrigger.min.js",
    ])
    failures = [
        r for r in Gate(ref).gate_post_implement()
        if r.status == "fail" and r.label == "spec-bundle-grounding"
    ]
    assert not failures


def test_spec_bundle_grounding_accepts_path_prefix(tmp_path: Path) -> None:
    """mersi schema has `"bundles/main.js"`. Parser must accept path-prefixed
    chunk names by matching basename."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _seed_bundles(ref, {"main.js": "// m"})
    _seed_spec_with_chunks(ref, ["bundles/main.js"])
    failures = [
        r for r in Gate(ref).gate_post_implement()
        if r.status == "fail" and r.label == "spec-bundle-grounding"
    ]
    assert not failures


def test_spec_bundle_grounding_skips_inline_init(tmp_path: Path) -> None:
    """`"inline init"` or `"or inline init"` is a sentinel for "no bundle
    file expected" — not a grounding failure."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _seed_bundles(ref, {"webflow.js": "// wf"})
    _seed_spec_with_chunks(ref, ["webflow.js (custom code) or inline init"])
    failures = [
        r for r in Gate(ref).gate_post_implement()
        if r.status == "fail" and r.label == "spec-bundle-grounding"
    ]
    assert not failures


def test_spec_bundle_grounding_skips_when_spec_missing(tmp_path: Path) -> None:
    """transition-spec.json absent → grounding check returns None silently."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "transition-spec.json").unlink()
    results = Gate(ref).gate_post_implement()
    grounding = [r for r in results if r.label == "spec-bundle-grounding"]
    assert not grounding, "no spec → no grounding check"


# ── E1: bundle-grep context inject (claude fidelity 2026-05-25, codex review) ──


def _seed_pipeline_state_for_e1(ref: Path, counts: dict[str, int]) -> None:
    """Write pipeline-state.json with the given gate_fail_counts so E1's
    threshold check sees a fail-loop scenario."""
    state = {
        "component": "ref",
        "current_gate": "post-implement",
        "completed_steps": ["reference", "extraction", "bundle", "paid-features",
                            "spec", "pre-generate"],
        "gate_fail_counts": counts,
        "unclonable_reasons": [],
    }
    (ref / "pipeline-state.json").write_text(json.dumps(state), encoding="utf-8")


def _seed_failing_sections(ref: Path, rows: list[tuple[str, int, str]]) -> None:
    """Write sections/result.txt with given (label, ae_per_mpx, status_icon) rows.

    status_icon: '❌' or '🌑' (saturated)."""
    sections = ref / "sections"
    sections.mkdir(parents=True, exist_ok=True)
    lines = ["| Section | AE | AE/Mpx | Severity | Status |"]
    for label, ae_per_mpx, icon in rows:
        lines.append(f"| {label} | 0 | {ae_per_mpx} | critical | {icon} |")
    lines.append(f"**Result: 0 PASS, {len(rows)} FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**")
    (sections / "result.txt").write_text("\n".join(lines), encoding="utf-8")


def _seed_grep_target_bundle(ref: Path, selector_to_snippet: dict[str, str]) -> None:
    """Drop a synthetic bundle containing the requested selector → snippet lines
    so bundle-grep returns hits when called by E1."""
    bundles = ref / "bundles"
    bundles.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        f"// ref bundle hit\ngsap.to('{sel}', {{ {snippet} }});"
        for sel, snippet in selector_to_snippet.items()
    )
    (bundles / "ref-bundle.js").write_text(content, encoding="utf-8")


def test_bundle_grep_inject_fires_at_fail_count_2(tmp_path: Path) -> None:
    """E1 fires when active-gate fail_count >= 2 (codex review item (a):
    effective threshold accounting for mark_failed post-check increment)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _seed_pipeline_state_for_e1(ref, {"post-implement": 2})
    _seed_failing_sections(ref, [(".hero", 950000, "❌"), (".nav", 120000, "❌")])
    _seed_grep_target_bundle(ref, {".hero": "opacity: 0, duration: 1.0"})

    results = Gate(ref).gate_post_implement()
    injects = [
        r for r in results
        if r.label == "bundle-grep-context-inject"
    ]
    assert injects, f"E1 must fire at fail_count=2, got: {[r.label for r in results]}"
    # Message should reference the failing selector AND include a ref-source line.
    msg = injects[0].message
    assert ".hero" in msg, f"message should cite failing selector: {msg}"
    assert "ref-bundle.js" in msg or "opacity" in msg, (
        f"message should include a bundle-grep hit: {msg}"
    )


def test_bundle_grep_inject_skips_below_threshold(tmp_path: Path) -> None:
    """E1 must not fire when fail_count == 1 (still in 'first attempt' phase)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _seed_pipeline_state_for_e1(ref, {"post-implement": 1})
    _seed_failing_sections(ref, [(".hero", 950000, "❌")])
    _seed_grep_target_bundle(ref, {".hero": "opacity: 0"})

    results = Gate(ref).gate_post_implement()
    injects = [r for r in results if r.label == "bundle-grep-context-inject"]
    assert not injects, "E1 must not fire at fail_count=1"


def test_bundle_grep_inject_includes_saturated_rows(tmp_path: Path) -> None:
    """Codex review item (c): saturated rows use 🌑 not ❌ but still count
    as fail. E1 must include both icon types when selecting worst sections."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _seed_pipeline_state_for_e1(ref, {"post-implement": 3})
    _seed_failing_sections(ref, [
        (".sat-section", 9999999, "🌑"),  # saturated
        (".reg-section", 500000, "❌"),    # regular fail
    ])
    _seed_grep_target_bundle(ref, {
        ".sat-section": "scale: 0",
        ".reg-section": "y: 100",
    })

    results = Gate(ref).gate_post_implement()
    injects = [r for r in results if r.label == "bundle-grep-context-inject"]
    assert injects, "E1 must fire"
    msg = injects[0].message
    assert ".sat-section" in msg, f"saturated (🌑) row must be considered: {msg}"


def test_bundle_grep_inject_skips_when_sections_result_absent(tmp_path: Path) -> None:
    """No sections/result.txt → nothing to extract failing selectors from →
    E1 returns None silently."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _seed_pipeline_state_for_e1(ref, {"post-implement": 3})
    # baseline created sections/result.txt with PASS — overwrite to remove it
    (ref / "sections" / "result.txt").unlink()

    results = Gate(ref).gate_post_implement()
    injects = [r for r in results if r.label == "bundle-grep-context-inject"]
    assert not injects, "no result.txt → no E1 fire"


def test_bundle_grep_inject_uses_active_gate_max(tmp_path: Path) -> None:
    """Codex review item (d): use max(post-implement, section-compare) for
    counter so visual fails accruing under post-implement still trigger E1."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    # section-compare 0, post-implement 2 → max=2 → should fire
    _seed_pipeline_state_for_e1(ref, {"post-implement": 2, "section-compare": 0})
    _seed_failing_sections(ref, [(".hero", 950000, "❌")])
    _seed_grep_target_bundle(ref, {".hero": "opacity: 0"})

    results = Gate(ref).gate_post_implement()
    injects = [r for r in results if r.label == "bundle-grep-context-inject"]
    assert injects, "E1 must use max() of counters across gates"
