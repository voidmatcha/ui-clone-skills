"""Real-browser regressions for runtime-text-sequence-check.sh."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest


def _close_session(session: str) -> None:
    subprocess.run(
        ["agent-browser", "--session", session, "close"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_runtime_text_excludes_closed_menus_and_captures_lazy_middle_section(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    script = (
        repo_root
        / "skills"
        / "visual-debug"
        / "scripts"
        / "runtime-text-sequence-check.sh"
    )
    ref_url = f"{http_server}runtime-text-sequence.html?capture-side=ref"
    impl_url = f"{http_server}runtime-text-sequence.html?capture-side=impl"
    session = f"runtime-text-{uuid.uuid4().hex[:12]}"
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    try:
        proc = subprocess.run(
            ["bash", str(script), session, ref_url, impl_url, str(ref_dir)],
            cwd=repo_root,
            env={**os.environ},
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        _close_session(f"{session}-text-ref")
        _close_session(f"{session}-text-impl")

    assert proc.returncode == 0, (
        f"runtime text check failed (rc={proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    artifact = json.loads(
        (ref_dir / "runtime-text-sequence.json").read_text(encoding="utf-8")
    )
    assert artifact["status"] == "pass"
    blocks = artifact["ref"]["blocks"]
    assert "Lazy intermediate copy" in blocks
    assert any(
        record["text"] == "Transient staggered copy"
        for sample in artifact["ref"]["samples"]
        for record in sample
    )
    assert "Closed menu secret" not in blocks
    assert "Clipped menu secret" not in blocks
    assert "Vertical offcanvas secret" not in blocks
    assert "Transparent color secret" not in blocks
    assert "Transparent fill secret" not in blocks
    assert "Visible explicit fill copy" in blocks
    assert "Visible gradient text copy" in blocks
    assert all(
        record["text"] != "Vertical offcanvas secret"
        for sample in artifact["ref"]["samples"]
        for record in sample
    )
    assert blocks == artifact["impl"]["blocks"]
    assert artifact["actualRefUrl"] == ref_url
    assert artifact["actualImplUrl"] == impl_url
    assert artifact["captureReceipt"]["ref"]["responseStatus"] == 200
    assert artifact["captureReceipt"]["impl"]["responseStatus"] == 200


def test_runtime_text_phase_window_starts_after_smooth_scroll_reset_settles(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    script = (
        repo_root
        / "skills"
        / "visual-debug"
        / "scripts"
        / "runtime-text-sequence-check.sh"
    )
    ref_url = f"{http_server}runtime-text-sequence.html?capture-side=ref"
    impl_url = (
        f"{http_server}runtime-text-sequence.html?"
        "capture-side=impl&smooth-reset=1"
    )
    session = f"runtime-text-smooth-reset-{uuid.uuid4().hex[:12]}"
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    proc = subprocess.run(
        ["bash", str(script), session, ref_url, impl_url, str(ref_dir)],
        cwd=repo_root,
        env={**os.environ},
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, (
        f"smooth reset created a false phase mismatch (rc={proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    artifact = json.loads(
        (ref_dir / "runtime-text-sequence.json").read_text(encoding="utf-8")
    )
    assert artifact["status"] == "pass"
    assert artifact["ref"]["blocks"] == artifact["impl"]["blocks"]
    assert artifact["violations"] == []


def test_runtime_text_honors_horizontal_only_ancestor_clipping(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    script = (
        repo_root
        / "skills"
        / "visual-debug"
        / "scripts"
        / "runtime-text-sequence-check.sh"
    )
    ref_url = f"{http_server}runtime-text-sequence.html?capture-side=ref"
    impl_url = (
        f"{http_server}runtime-text-sequence.html?"
        "capture-side=impl&body-horizontal-clip=1"
    )
    session = f"runtime-text-horizontal-clip-{uuid.uuid4().hex[:12]}"
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    try:
        proc = subprocess.run(
            ["bash", str(script), session, ref_url, impl_url, str(ref_dir)],
            cwd=repo_root,
            env={**os.environ},
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        _close_session(f"{session}-text-ref")
        _close_session(f"{session}-text-impl")

    artifact = json.loads(
        (ref_dir / "runtime-text-sequence.json").read_text(encoding="utf-8")
    )
    assert proc.returncode == 0, (
        "horizontal-only clipping rejected vertically overflowing text "
        f"(rc={proc.returncode})\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert artifact["status"] == "pass"
    assert "Final visible copy" in artifact["impl"]["blocks"]
    assert artifact["ref"]["blocks"] == artifact["impl"]["blocks"]


def test_runtime_text_ignores_generated_pseudo_text(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    script = (
        repo_root
        / "skills"
        / "visual-debug"
        / "scripts"
        / "runtime-text-sequence-check.sh"
    )
    ref_url = f"{http_server}runtime-text-sequence.html"
    impl_url = f"{ref_url}?generated-pseudo=1"
    session = f"runtime-text-pseudo-{uuid.uuid4().hex[:12]}"
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    try:
        proc = subprocess.run(
            ["bash", str(script), session, ref_url, impl_url, str(ref_dir)],
            cwd=repo_root,
            env={**os.environ},
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        _close_session(f"{session}-text-ref")
        _close_session(f"{session}-text-impl")

    assert proc.returncode == 0, (
        "synthetic pseudo text must not count as authored runtime copy "
        f"(rc={proc.returncode})\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    artifact = json.loads(
        (ref_dir / "runtime-text-sequence.json").read_text(encoding="utf-8")
    )
    assert artifact["status"] == "pass"
    assert artifact["ref"]["blocks"] == artifact["impl"]["blocks"]


def test_runtime_text_still_fails_wrong_non_dynamic_hero(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    script = (
        repo_root
        / "skills"
        / "visual-debug"
        / "scripts"
        / "runtime-text-sequence-check.sh"
    )
    ref_url = f"{http_server}runtime-text-sequence.html"
    impl_url = f"{ref_url}?wrong-hero=1"
    session = f"runtime-text-wrong-hero-{uuid.uuid4().hex[:12]}"
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    proc = subprocess.run(
        ["bash", str(script), session, ref_url, impl_url, str(ref_dir)],
        cwd=repo_root,
        env={**os.environ},
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 1, (
        f"wrong hero unexpectedly passed (rc={proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    artifact = json.loads(
        (ref_dir / "runtime-text-sequence.json").read_text(encoding="utf-8")
    )
    assert artifact["status"] == "fail"
    assert "Visible page heading" in artifact["ref"]["blocks"]
    assert "Incorrect page heading" in artifact["impl"]["blocks"]
    assert "canonical-block-sequence-mismatch" in {
        violation["kind"] for violation in artifact["violations"]
    }


def test_runtime_text_preserves_prefix_around_nested_semantic_link(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    script = (
        repo_root
        / "skills"
        / "visual-debug"
        / "scripts"
        / "runtime-text-sequence-check.sh"
    )
    ref_url = f"{http_server}runtime-text-sequence.html"
    impl_url = f"{ref_url}?wrong-prefix=1"
    session = f"runtime-text-wrong-prefix-{uuid.uuid4().hex[:12]}"
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    proc = subprocess.run(
        ["bash", str(script), session, ref_url, impl_url, str(ref_dir)],
        cwd=repo_root,
        env={**os.environ},
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 1, (
        f"wrong prefix unexpectedly passed (rc={proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    artifact = json.loads(
        (ref_dir / "runtime-text-sequence.json").read_text(encoding="utf-8")
    )
    assert artifact["status"] == "fail"
    ref_start = artifact["ref"]["blocks"].index("Correct prefix")
    impl_start = artifact["impl"]["blocks"].index("Wrong prefix")
    assert artifact["ref"]["blocks"][ref_start : ref_start + 3] == [
        "Correct prefix",
        "Inline link",
        "suffix",
    ]
    assert artifact["impl"]["blocks"][impl_start : impl_start + 3] == [
        "Wrong prefix",
        "Inline link",
        "suffix",
    ]
    assert [
        record["slot"].rsplit("::", 1)[-1]
        for record in artifact["ref"]["records"][ref_start : ref_start + 3]
    ] == ["run(1)", "run(1)", "run(2)"]
    assert "canonical-block-sequence-mismatch" in {
        violation["kind"] for violation in artifact["violations"]
    }


@pytest.mark.parametrize(
    ("ref_phase", "impl_phase", "proof_key"),
    [
        ("off", "on", "matchedReferenceCandidatePresentSample"),
        ("on", "off", "matchedImplementationCandidateSample"),
    ],
)
def test_runtime_text_confirms_timer_phase_that_ignores_reduced_motion(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
    ref_phase: str,
    impl_phase: str,
    proof_key: str,
) -> None:
    script = (
        repo_root
        / "skills"
        / "visual-debug"
        / "scripts"
        / "runtime-text-sequence-check.sh"
    )
    ref_url = (
        f"{http_server}runtime-text-sequence.html?phase-final={ref_phase}"
    )
    impl_url = (
        f"{http_server}runtime-text-sequence.html?phase-final={impl_phase}"
    )
    session = f"runtime-text-phase-{uuid.uuid4().hex[:12]}"
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    proc = subprocess.run(
        ["bash", str(script), session, ref_url, impl_url, str(ref_dir)],
        cwd=repo_root,
        env={**os.environ},
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, (
        f"proven timer phase failed (rc={proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    artifact = json.loads(
        (ref_dir / "runtime-text-sequence.json").read_text(encoding="utf-8")
    )
    assert artifact["status"] == "pass"
    assert artifact["ref"]["blocks"] != artifact["impl"]["blocks"]
    assert artifact["phaseVariance"]["accepted"] is True
    assert artifact["phaseVariance"]["advisory"]
    proof = artifact["phaseVariance"]["proof"][0]
    assert proof_key in proof
    assert "matchedReferenceCandidatePresentSample" in proof
    assert "matchedReferenceCandidateAbsentStartSample" in proof
    assert proof["referenceCyclePolarity"] in {
        "present-absent-present",
        "absent-present-absent",
    }
    if proof["referenceCyclePolarity"] == "present-absent-present":
        cycle = (
            proof["matchedReferenceCandidatePresentSample"],
            proof["matchedReferenceCandidateAbsentStartSample"],
            proof["matchedReferenceCandidateRecurredSample"],
        )
    else:
        cycle = (
            proof["matchedReferenceCandidateAbsentStartSample"],
            proof["matchedReferenceCandidatePresentSample"],
            proof["matchedReferenceCandidateRecurredSample"],
        )
    assert cycle[0] < cycle[1] < cycle[2]
    assert proof["referenceAbsenceRunLength"] >= 1
    assert artifact["violations"] == []
