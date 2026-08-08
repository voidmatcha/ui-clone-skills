"""Canonical verify-stamp hash pin + goal --check-done stamp parity.

omx postmortem follow-up: the structural-convergence stamp already pins
sections/result.txt by sha256 (tamper-after-stamp detection), but the
CANONICAL verify-stamp did not — and `goal --check-done` passed on
current_gate=="done" + result.txt counts without requiring the stamp at all,
so external loop drivers could declare success that the Stop hook would have
blocked.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ui_clone.pipeline_phases.verify import build_verify_stamp
from ui_clone.state import POST_IMPL_VERIFY_GATES

RESULT = (
    "| Section | AE | AE/Mpx | Severity | Status |\n"
    "| hero | 0 | 0 | ok | ✅ |\n"
    "\n**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n"
)


def test_verify_phase_imports_under_macos_system_python() -> None:
    """Canonical verify can be launched by macOS /usr/bin/python3."""
    host_python = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else shutil.which("python3")
    if not host_python:
        pytest.skip("python3 not available")

    proc = subprocess.run(
        [
            host_python,
            "-c",
            "import importlib; importlib.import_module('ui_clone.pipeline_phases.verify')",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr


def _ref_with_result(tmp_path: Path) -> Path:
    ref = tmp_path / "tmp" / "ref" / "comp"
    (ref / "sections").mkdir(parents=True)
    (ref / "sections" / "result.txt").write_text(RESULT)
    return ref


def test_build_verify_stamp_pins_result_sha(tmp_path: Path) -> None:
    ref = _ref_with_result(tmp_path)
    impl = tmp_path / "impl"
    impl.mkdir()
    stamp = build_verify_stamp(ref, impl, ["post-implement"])
    expected = hashlib.sha256(RESULT.encode("utf-8")).hexdigest()
    assert stamp["sectionsResultSha256"] == expected
    assert stamp["stampedBy"] == "pipeline.execute_verify"


def test_build_verify_stamp_without_result_has_no_pin(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    impl.mkdir()
    stamp = build_verify_stamp(ref, impl, ["post-implement"])
    assert "sectionsResultSha256" not in stamp


def test_build_verify_stamp_pins_motion_evidence(tmp_path: Path) -> None:
    ref = _ref_with_result(tmp_path)
    impl = tmp_path / "impl"
    impl.mkdir()
    spec = b'{"transitions":[{"id":"hero"}]}'
    fires = b'{"entries":[{"id":"hero","status":"pass"}]}'
    (ref / "transition-spec.json").write_bytes(spec)
    (ref / "transition-fires.json").write_bytes(fires)

    stamp = build_verify_stamp(ref, impl, ["post-implement"])

    assert stamp["transitionSpecSha256"] == hashlib.sha256(spec).hexdigest()
    assert stamp["transitionFiresSha256"] == hashlib.sha256(fires).hexdigest()


def _write_canonical_stamp(ref: Path, *, pin: str | None) -> None:
    stamp = {
        "verifiedAt": datetime.datetime.now(datetime.UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "gatesPassed": list(POST_IMPL_VERIFY_GATES),
        "stampedBy": "pipeline.execute_verify",
        "implDir": str(ref / "impl"),
        "refDir": str(ref),
    }
    if pin is not None:
        stamp["sectionsResultSha256"] = pin
    (ref / "verify-stamp.json").write_text(json.dumps(stamp))


def test_stop_hook_blocks_tampered_canonical_pin(tmp_path: Path) -> None:
    from ui_clone.hooks.section_gate import _enforce_verify_stamp
    from ui_clone.state import PipelineState

    ref = _ref_with_result(tmp_path)
    impl = tmp_path / "impl"
    impl.mkdir()
    state = PipelineState.load(ref)
    state.impl_root = str(impl)
    state.save(ref)
    _write_canonical_stamp(ref, pin="0" * 64)  # wrong pin
    reason = _enforce_verify_stamp(ref)
    assert reason is not None and "sha256" in reason, reason


def test_stop_hook_accepts_matching_canonical_pin(tmp_path: Path) -> None:
    from ui_clone.hooks.section_gate import _enforce_verify_stamp
    from ui_clone.state import PipelineState

    ref = _ref_with_result(tmp_path)
    impl = tmp_path / "impl"
    impl.mkdir()
    state = PipelineState.load(ref)
    state.impl_root = str(impl)
    state.save(ref)
    pin = hashlib.sha256(RESULT.encode("utf-8")).hexdigest()
    _write_canonical_stamp(ref, pin=pin)
    assert _enforce_verify_stamp(ref) is None


@pytest.mark.parametrize(
    ("artifact_name", "mutated"),
    [
        ("transition-spec.json", '{"transitions":[{"id":"changed"}]}'),
        (
            "transition-fires.json",
            '{"entries":[{"id":"hero","status":"unmeasurable"}]}',
        ),
    ],
)
def test_mutated_motion_evidence_invalidates_canonical_and_stop_stamp(
    tmp_path: Path,
    artifact_name: str,
    mutated: str,
) -> None:
    from ui_clone.hooks.section_gate import _enforce_verify_stamp
    from ui_clone.pipeline_phases.verify import canonical_stamp_problem
    from ui_clone.state import PipelineState

    ref = _ref_with_result(tmp_path)
    impl = tmp_path / "impl"
    impl.mkdir()
    state = PipelineState.load(ref)
    state.impl_root = str(impl)
    state.save(ref)
    spec = ref / "transition-spec.json"
    fires = ref / "transition-fires.json"
    spec.write_text('{"transitions":[{"id":"hero"}]}', encoding="utf-8")
    fires.write_text(
        '{"entries":[{"id":"hero","status":"pass"}]}',
        encoding="utf-8",
    )
    stamp = build_verify_stamp(ref, impl, list(POST_IMPL_VERIFY_GATES))
    (ref / "verify-stamp.json").write_text(json.dumps(stamp), encoding="utf-8")

    (ref / artifact_name).write_text(mutated, encoding="utf-8")

    canonical_problem = canonical_stamp_problem(ref)
    stop_problem = _enforce_verify_stamp(ref)
    assert canonical_problem is not None
    assert f"{artifact_name} changed" in canonical_problem
    assert stop_problem is not None
    assert f"{artifact_name} changed" in stop_problem


def _write_done_state(ref: Path) -> None:
    (ref / "pipeline-state.json").write_text(
        json.dumps(
            {
                "component": "comp",
                "current_gate": "done",
                "completed_steps": [
                    "reference",
                    "extraction",
                    "bundle",
                    "paid-features",
                    "spec",
                    "pre-generate",
                    "state-coverage",
                    "post-implement",
                    "boundary",
                    "font-parity",
                    "section-compare",
                ],
            }
        )
    )


def _check_done(ref: Path) -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "ui_clone.goal", str(ref), "--check-done"],
        capture_output=True,
        text=True,
    )
    return proc.returncode


def test_check_done_requires_canonical_stamp(tmp_path: Path) -> None:
    """current_gate done + clean result.txt is NOT enough — external loop
    drivers must see the same stamp requirement the Stop hook enforces."""
    ref = _ref_with_result(tmp_path)
    _write_done_state(ref)
    assert _check_done(ref) != 0, "missing stamp must fail --check-done"


def test_check_done_passes_with_fresh_pinned_stamp(tmp_path: Path) -> None:
    ref = _ref_with_result(tmp_path)
    _write_done_state(ref)
    pin = hashlib.sha256(RESULT.encode("utf-8")).hexdigest()
    _write_canonical_stamp(ref, pin=pin)
    assert _check_done(ref) == 0


def test_check_done_rejects_tampered_pin(tmp_path: Path) -> None:
    ref = _ref_with_result(tmp_path)
    _write_done_state(ref)
    _write_canonical_stamp(ref, pin="0" * 64)
    assert _check_done(ref) != 0


def test_canonical_stamp_problem_rejects_impl_changed_after_stamp(tmp_path: Path) -> None:
    from ui_clone.pipeline_phases.verify import canonical_stamp_problem
    from ui_clone.state import PipelineState

    ref = _ref_with_result(tmp_path)
    impl = tmp_path / "impl"
    impl.mkdir()
    (impl / "src").mkdir()
    app = impl / "src" / "App.tsx"
    app.write_text("export default function App() { return null }\n", encoding="utf-8")
    state = PipelineState.load(ref)
    state.impl_root = str(impl)
    state.save(ref)
    pin = hashlib.sha256(RESULT.encode("utf-8")).hexdigest()
    _write_canonical_stamp(ref, pin=pin)
    stamp_path = ref / "verify-stamp.json"

    old = time.time() - 5
    os.utime(stamp_path, (old, old))
    app.write_text("export default function App() { return <main /> }\n", encoding="utf-8")

    problem = canonical_stamp_problem(ref)
    assert problem is not None
    assert "impl changed after verify" in problem
    assert "App.tsx" in problem
