from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ._helpers import (
    _baseline_post_implement_inputs,
    _make_verification_plan,
    _project_root,
)


def test_gate_dep_dag_skips_downstream_when_upstream_fails(tmp_path: Path) -> None:
    """The dispatcher's gate-dependency DAG: when an upstream gate (e.g.
    runtime-env) fails, downstream gates that declared `dependsOn` it
    must be marked SKIPPED_DEP rather than run.
    """
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({"name": "impl"}))
    ref.mkdir()
    (ref / ".impl-root").write_text(str(impl) + "\n")
    # Plan: one always-failing upstream and one downstream that depends
    # on it. Choose runtime-env as upstream (real script). For the
    # downstream we use video-play-proof which already declares
    # runtime-env in its dependsOn.
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            # Upstream that will fail (impl-url 127.0.0.1:1 connection refused).
            {
                "id": "runtime-env",
                "script": "skills/visual-debug/scripts/runtime-env-check.sh",
                "produces": "runtime-env.json",
                "reason": "test upstream",
                "severity": "block",
                "tier": "standard",
            },
            # Downstream that depends on runtime-env.
            {
                "id": "video-play-proof",
                "script": "skills/visual-debug/scripts/video-play-proof-check.sh",
                "produces": "video-play-proof.json",
                "reason": "test downstream",
                "severity": "block",
                "tier": "standard",
                "dependsOn": ["runtime-env"],
            },
        ],
    }))

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "run-required-checks.sh"),
            "dag-test-session",
            "https://example.test",
            "http://127.0.0.1:1",  # nothing listening here → runtime-env fails
            str(ref),
        ],
        cwd=root, env=env, capture_output=True, text=True, timeout=30,
    )
    # The downstream must be SKIPPED_DEP — the SKIPPED_DEP marker appears
    # in the dispatcher's per-row output line.
    assert "SKIPPED_DEP" in proc.stdout or "depends on failed" in proc.stdout, (
        f"downstream gate must skip when upstream fails:\n{proc.stdout}\n{proc.stderr}"
    )



def test_gate_hidden_children_status_pass_passes(tmp_path: Path) -> None:
    """status:pass on hidden-children artifact → gate passes."""
    from ui_clone.gate import Gate
    ref = tmp_path / "ref"
    ref.mkdir()
    _baseline_post_implement_inputs(ref)
    _make_verification_plan(ref, "hidden-children", "hidden-children.json")
    (ref / "hidden-children.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "pass",
        "implUrl": "http://localhost:5173",
        "sectionsChecked": 4,
        "violationCount": 0,
        "violations": [],
    }))
    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert not any("hidden-children" in r.label for r in failures), failures



def test_gate_hidden_children_status_fail_fails(tmp_path: Path) -> None:
    """status:fail on hidden-children artifact → gate fails with the issue count."""
    from ui_clone.gate import Gate
    ref = tmp_path / "ref"
    ref.mkdir()
    _baseline_post_implement_inputs(ref)
    _make_verification_plan(ref, "hidden-children", "hidden-children.json")
    (ref / "hidden-children.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "fail",
        "implUrl": "http://localhost:5173",
        "sectionsChecked": 4,
        "violationCount": 2,
        "violations": [
            {"tag": "section", "id": "hero", "className": "",
             "childrenChecked": 5, "area": 1080000},
            {"tag": "section", "id": "again", "className": "",
             "childrenChecked": 3, "area": 800000},
        ],
    }))
    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert any("hidden-children" in r.label for r in failures), results



def test_gate_runtime_dom_parity_status_fail_fails(tmp_path: Path) -> None:
    """status:fail on runtime-dom-parity artifact → gate fails."""
    from ui_clone.gate import Gate
    ref = tmp_path / "ref"
    ref.mkdir()
    _baseline_post_implement_inputs(ref)
    _make_verification_plan(
        ref, "runtime-dom-parity", "runtime-dom-parity.json",
    )
    (ref / "runtime-dom-parity.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "fail",
        "refUrl": "https://realfood.gov",
        "implUrl": "http://localhost:5173",
        "hasLottieEvidence": True,
        "violations": [
            {"kind": "ref-has-lottie-impl-has-no-lottie-container", "impl": 0},
        ],
    }))
    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert any("runtime-dom-parity" in r.label for r in failures), results



def test_gate_runtime_dom_parity_missing_status_fails(tmp_path: Path) -> None:
    """STATUS_REQUIRED: artifact present but `status` field absent → fail."""
    from ui_clone.gate import Gate
    ref = tmp_path / "ref"
    ref.mkdir()
    _baseline_post_implement_inputs(ref)
    _make_verification_plan(
        ref, "runtime-dom-parity", "runtime-dom-parity.json",
    )
    # Missing status field on a STATUS_REQUIRED check_id.
    (ref / "runtime-dom-parity.json").write_text(json.dumps({
        "schemaVersion": 1,
        "violations": [],
    }))
    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert any(
        "runtime-dom-parity" in r.label
        and "status` field" in (r.message or "")
        for r in failures
    ), failures



def test_gate_rapid_phase_downgrades_block_to_warn(tmp_path: Path) -> None:
    """UI_CLONE_PHASE=rapid: non-anti-cheat block check fails → emits warn."""
    import os

    from ui_clone.gate import Gate
    ref = tmp_path / "ref"
    ref.mkdir()
    _baseline_post_implement_inputs(ref)
    _make_verification_plan(
        ref, "tree-diff", "tree-diff-status.json",
        severity="block", tier="standard",
    )
    (ref / "tree-diff-status.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "fail",
        "violations": [{"kind": "test"}],
    }))
    old = os.environ.get("UI_CLONE_PHASE")
    os.environ["UI_CLONE_PHASE"] = "rapid"
    try:
        results = Gate(ref).gate_post_implement()
    finally:
        if old is None:
            os.environ.pop("UI_CLONE_PHASE", None)
        else:
            os.environ["UI_CLONE_PHASE"] = old
    # tree-diff is NOT in STRICT_ALWAYS → rapid mode downgrades to warn.
    fails = [r for r in results if r.status == "fail" and "tree-diff" in r.label]
    warns = [r for r in results if r.status == "warn" and "tree-diff" in r.label]
    assert not fails, f"rapid mode should downgrade tree-diff: {results}"
    assert warns, f"rapid mode should emit warn for tree-diff: {results}"



def test_gate_rapid_phase_does_not_downgrade_anti_cheat(tmp_path: Path) -> None:
    """UI_CLONE_PHASE=rapid: anti-cheat gates (ref-screenshot-asset) still fail."""
    import os

    from ui_clone.gate import Gate
    ref = tmp_path / "ref"
    ref.mkdir()
    _baseline_post_implement_inputs(ref)
    _make_verification_plan(
        ref, "ref-screenshot-asset", "ref-screenshot-asset.json",
        severity="block", tier="quick",
    )
    (ref / "ref-screenshot-asset.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "fail",
        "violations": [{"kind": "byte-identical-copy"}],
    }))
    old = os.environ.get("UI_CLONE_PHASE")
    os.environ["UI_CLONE_PHASE"] = "rapid"
    try:
        results = Gate(ref).gate_post_implement()
    finally:
        if old is None:
            os.environ.pop("UI_CLONE_PHASE", None)
        else:
            os.environ["UI_CLONE_PHASE"] = old
    # ref-screenshot-asset IS in STRICT_ALWAYS → rapid does NOT downgrade.
    fails = [r for r in results
             if r.status == "fail" and "ref-screenshot-asset" in r.label]
    assert fails, f"anti-cheat gate must stay strict even in rapid: {results}"



def test_gate_status_warn_does_not_fail_block_severity(tmp_path: Path) -> None:
    """Script-declared status=warn must NOT be upgraded to fail by block severity.

    Regression for the Codex L60 image-fidelity FP fix: gate.py must
    honor a script's explicit warn verdict regardless of severity.
    """
    from ui_clone.gate import Gate
    ref = tmp_path / "ref"
    ref.mkdir()
    _baseline_post_implement_inputs(ref)
    _make_verification_plan(
        ref, "hidden-children", "hidden-children.json",
        severity="block", tier="standard",
    )
    (ref / "hidden-children.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "warn",
        "implUrl": "http://localhost:5173",
        "sectionsChecked": 4,
        "violationCount": 0,
        "violations": [],
    }))
    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    # block severity should NOT upgrade a script-declared warn to fail.
    assert not any("hidden-children" in r.label for r in failures), failures
    warns = [r for r in results if r.status == "warn"]
    assert any("hidden-children" in r.label for r in warns), results


def test_gate_transition_health_silent_when_sections_have_zero_passes(
    tmp_path: Path,
) -> None:
    """When the static visual baseline has 0 PASS, transition-compare output
    is not meaningful. post-implement should report the section health failure
    as the primary signal and avoid adding a transition-health duplicate.
    """
    from ui_clone.gate import Gate

    ref = tmp_path / "ref"
    ref.mkdir()
    _baseline_post_implement_inputs(ref)
    (ref / "sections" / "result.txt").write_text(
        "**Result: 0 PASS, 11 FAIL, 3 SKIP, 0 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    (ref / "transitions" / "result.txt").unlink()

    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]

    assert any("sections/result.txt visual health" == r.label for r in failures)
    assert not any(
        "transitions/result.txt visual health" == r.label for r in failures
    ), failures
