from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from ui_clone import measure

from ._helpers import (
    _project_root,
    _run_script,
)


def test_transition_compare_does_not_lock_section_threshold() -> None:
    """transition-compare has its own scoring; the SECTION_THRESHOLD lock
    is irrelevant there. Only section-compare gets the AE-classifier lock.
    """
    captured_env: dict[str, str] = {}

    def fake_run(cmd: list[str], env: dict[str, str], **kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        captured_env.update(env)
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    args = mock.Mock(
        ref_dir="/tmp/fake-ref",
        orig_url="https://ref.example",
        impl_url="http://localhost:3000",
        session="test",
    )
    with mock.patch.dict(os.environ, {"SECTION_THRESHOLD": "999"}, clear=False), \
         mock.patch.object(subprocess, "run", side_effect=fake_run):
        measure.cmd_transition_compare(args)
    # transition-compare doesn't override SECTION_THRESHOLD — caller's value passes through.
    assert captured_env["SECTION_THRESHOLD"] == "999"



def test_all_skips_transition_compare_when_no_spec(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """No transition-spec.json → transition-compare skipped (recorded as skip
    in summary). The bash script would otherwise error on missing input.
    """
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    # No transition-spec.json

    invoked_scripts: list[str] = []

    def fake_run(cmd: list[str], env: dict[str, str], **kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        for c in cmd:
            if c.endswith(".sh"):
                invoked_scripts.append(Path(c).name)
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    args = mock.Mock(
        ref_dir=str(ref_dir),
        orig_url="https://ref.example",
        impl_url="http://localhost:3000",
        session="test",
        impl_src=None,
        impl_pkg=None,
    )
    with mock.patch.object(subprocess, "run", side_effect=fake_run):
        measure.cmd_all(args)

    assert "transition-compare.sh" not in invoked_scripts, (
        f"transition-compare must be skipped when no spec exists; invoked: {invoked_scripts}"
    )
    out = capsys.readouterr().out.strip().splitlines()
    final = json.loads(out[-1])
    skip_entry = next((s for s in final["summary"] if s["step"] == "transition-compare"), None)
    assert skip_entry is not None
    assert skip_entry["exit_code"] == "skip"



def test_video_play_proof_script_present() -> None:
    """2026-05-22 codex-rescue Rank 3: video must actually play, not
    just exist. Gate catches the static-poster-only cheat where
    required-media-coverage passes on the .mp4 file but the <video>
    never advances currentTime.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "video-play-proof-check.sh"
    assert script.is_file(), "video-play-proof-check.sh missing"
    body = script.read_text(encoding="utf-8")
    assert "currentTime" in body, "must probe currentTime advancement"
    assert "play()" in body or "v.play" in body, "must call play() on muted videos"
    assert "video-play-proof.json" in body
    assert "skip" in body, "must skip when ref has no video signal"


def test_video_play_proof_skips_zero_video_totals(tmp_path: Path) -> None:
    """A `video: 0` count field is not a video signal.

    Regression for ORDR: required-media.json legitimately records zero
    videos, and the gate must parse that structurally instead of grepping
    for the literal key name "video".
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "required-media.json").write_text(json.dumps({
        "schemaVersion": 1,
        "videos": [],
        "lottie": [],
        "svgs": [],
        "totals": {"video": 0, "lottie": 0, "svg": 0},
    }))
    (ref / "required-media-coverage.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "pass",
        "summary": "ref has no required video, Lottie, or SVG media",
    }))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_agent = bin_dir / "agent-browser"
    fake_agent.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
    fake_agent.chmod(0o755)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "video-play-proof-check.sh"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    proc = subprocess.run(
        ["bash", str(script), "vpp-test", "http://127.0.0.1:1/", str(ref)],
        capture_output=True, text=True, timeout=10, env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "video-play-proof.json").read_text())
    assert artifact["status"] == "skip"



def test_video_play_proof_dispatcher_wired() -> None:
    import re
    dispatcher = _project_root() / "scripts" / "verify" / "run-required-checks.sh"
    text = dispatcher.read_text(encoding="utf-8")
    m = re.search(r'"video-play-proof-check\.sh":\s*"([^"]+)"', text)
    assert m, "video-play-proof-check.sh missing from dispatcher SIGNATURES"
    recipe = m.group(1)
    assert "{session}" in recipe and "{impl_url}" in recipe and "{ref_dir}" in recipe, (
        f"video-play-proof recipe must include session/impl_url/ref_dir (got: {recipe!r})"
    )



def test_verification_plan_includes_video_play_proof_block_row() -> None:
    plan_script = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    text = plan_script.read_text(encoding="utf-8")
    import re
    block = re.search(
        r'add_check\s+"video-play-proof"[\s\S]+?"(block|warn)"',
        text,
    )
    assert block, "video-play-proof add_check missing or malformed"
    assert block.group(1) == "block"



def test_transition_proof_rollup_fails_partial_coverage(tmp_path: Path) -> None:
    """Composite rollup must FAIL when transition-spec-coverage status=pass
    but covered<total (the static gate's bug class — partial coverage with
    a pass verdict).
    """
    import subprocess
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass",
        "total": 7, "covered": 4, "uncovered": 3,
    }))
    (ref / "spec-implementation-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass",
        "total": 7, "withMotion": 7,
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "transition-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 1, f"partial coverage must compose to FAIL: {proc.stdout}"
    artifact = json.loads((ref / "transition-proof.json").read_text())
    assert artifact["status"] == "fail"
    msg = " ".join(artifact["reasons"])
    assert "partial coverage" in msg or "4/7" in msg, (
        f"reasons must name the partial-coverage issue: {msg}"
    )


def test_transition_proof_accepts_phase6d_declarations_with_video_motion(tmp_path: Path) -> None:
    """Phase 6d transition-coverage may be ref-side declarations only.

    Runtime proof is then carried by reveal/video artifacts, so the rollup
    must not fail solely because animatedElements lack samples arrays.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "patch-scroll-parallax"}],
    }))
    (ref / "transition-spec-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "covered": 1,
    }))
    (ref / "spec-implementation-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "withMotion": 1,
    }))
    (ref / "transition-coverage.json").write_text(json.dumps({
        "animatedElements": [{"selector": ".patch", "transition": "patch-scroll-parallax"}],
    }))
    transitions = ref / "transitions"
    transitions.mkdir()
    (transitions / "video-motion-result.txt").write_text(
        "✓ trajectory pre-filter passed\n✅ structural motion trajectory passed\n",
        encoding="utf-8",
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "transition-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "transition-proof.json").read_text())
    assert artifact["status"] == "pass"


def test_transition_proof_accepts_single_sample_phase6d_with_hover_compare(
    tmp_path: Path,
) -> None:
    """Quick-tier hover proof should carry runtime evidence for ref-side samples."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "tier": "quick",
        "requiredChecks": [
            {"id": "transition-spec-coverage", "produces": "transition-spec-coverage.json"},
            {"id": "transition-proof", "produces": "transition-proof.json"},
        ],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "readymag-nav-hover-transition"}],
    }))
    (ref / "transition-spec-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "covered": 1,
    }))
    (ref / "transition-coverage.json").write_text(json.dumps({
        "animatedElements": [
            {
                "selector": ".rmwidget",
                "trigger": "css-transition",
                "samples": [{"scrollY": 0, "opacity": "1"}],
            }
        ],
    }))
    transitions = ref / "transitions"
    transitions.mkdir()
    (transitions / "result.txt").write_text(
        "Transition compare: 1 PASS, 0 FAIL\n✅ PASS  .rmwidget\n",
        encoding="utf-8",
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "transition-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "transition-proof.json").read_text())
    assert artifact["status"] == "pass"
    assert any(
        "transition-compare" in component["note"]
        for component in artifact["components"]
        if component["artifact"] == "transition-coverage.json"
    )


def test_transition_proof_does_not_use_hover_compare_for_scroll_runtime_proof(
    tmp_path: Path,
) -> None:
    """A hover/end-state compare pass cannot prove a scroll transition fired."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-scroll", "trigger": "scroll"}],
    }))
    (ref / "transition-spec-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "covered": 1,
    }))
    (ref / "spec-implementation-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "withMotion": 1,
    }))
    (ref / "transition-coverage.json").write_text(json.dumps({
        "animatedElements": [
            {
                "selector": ".hero",
                "trigger": "scroll",
                "transition": "hero-scroll",
            }
        ],
    }))
    transitions = ref / "transitions"
    transitions.mkdir()
    (transitions / "result.txt").write_text(
        "Transition compare: 1 PASS, 0 FAIL\n✅ PASS  .hero\n",
        encoding="utf-8",
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "transition-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "transition-proof.json").read_text())
    assert artifact["status"] == "fail"
    assert any("runtime proof" in r for r in artifact["reasons"])


def test_transition_proof_rejects_phase6d_declarations_without_runtime_proof(
    tmp_path: Path,
) -> None:
    """Phase 6d declarations are static ref evidence, not runtime proof alone."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "patch-scroll-parallax"}],
    }))
    (ref / "transition-spec-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "covered": 1,
    }))
    (ref / "spec-implementation-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "withMotion": 1,
    }))
    (ref / "transition-coverage.json").write_text(json.dumps({
        "animatedElements": [{"selector": ".patch", "transition": "patch-scroll-parallax"}],
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "transition-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "transition-proof.json").read_text())
    assert artifact["status"] == "fail"
    assert any("runtime proof" in r for r in artifact["reasons"])


def test_transition_proof_rejects_video_motion_without_verdict_marker(
    tmp_path: Path,
) -> None:
    """A present video-motion artifact without PASS/FAIL text is not proof."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-scroll"}],
    }))
    (ref / "transition-spec-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "covered": 1,
    }))
    (ref / "spec-implementation-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "withMotion": 1,
    }))
    (ref / "transition-coverage.json").write_text(json.dumps({
        "animatedElements": [
            {
                "selector": ".hero",
                "samples": [
                    {"scrollY": 0, "opacity": "0"},
                    {"scrollY": 800, "opacity": "1"},
                ],
            },
        ],
    }))
    transitions = ref / "transitions"
    transitions.mkdir()
    (transitions / "video-motion-result.txt").write_text(
        "recording completed but report parser crashed before verdict\n",
        encoding="utf-8",
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "transition-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "transition-proof.json").read_text())
    assert artifact["status"] == "fail"
    assert any("no PASS/FAIL marker" in r for r in artifact["reasons"])


def test_transition_proof_fails_when_expected_video_motion_missing(
    tmp_path: Path,
) -> None:
    """If verification-plan requires video-motion, the rollup must agree."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(json.dumps({
        "requiredChecks": [
            {
                "id": "video-motion-compare",
                "produces": "transitions/video-motion-result.txt",
            }
        ],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-scroll"}],
    }))
    (ref / "transition-spec-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "covered": 1,
    }))
    (ref / "spec-implementation-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "withMotion": 1,
    }))
    (ref / "transition-coverage.json").write_text(json.dumps({
        "animatedElements": [
            {
                "selector": ".hero",
                "samples": [
                    {"scrollY": 0, "opacity": "0"},
                    {"scrollY": 800, "opacity": "1"},
                ],
            },
        ],
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "transition-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "transition-proof.json").read_text())
    assert artifact["status"] == "fail"
    assert any("video-motion expected" in r for r in artifact["reasons"])


def test_transition_proof_reads_scroll_completion_artifact(tmp_path: Path) -> None:
    """The rollup must consume the artifact name produced by the scroll gate."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(json.dumps({
        "requiredChecks": [
            {
                "id": "scroll-end-completion",
                "produces": "scroll-completion.json",
            }
        ],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-scroll"}],
    }))
    (ref / "transition-spec-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "covered": 1,
    }))
    (ref / "spec-implementation-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "withMotion": 1,
    }))
    (ref / "transition-coverage.json").write_text(json.dumps({
        "animatedElements": [
            {
                "selector": ".hero",
                "samples": [
                    {"scrollY": 0, "opacity": "0"},
                    {"scrollY": 800, "opacity": "1"},
                ],
            },
        ],
    }))
    (ref / "scroll-completion.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "fail",
        "viewports": [{"w": 375, "h": 812, "stuck": [{"selector": ".hero"}]}],
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "transition-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "transition-proof.json").read_text())
    assert artifact["status"] == "fail"
    assert any("scroll-completion.json" in r for r in artifact["reasons"])


def test_transition_trajectory_supports_structural_motion_mode() -> None:
    """Structural-only section comparison needs selector-level motion proof."""
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "transition-trajectory-compare.sh"
    body = script.read_text(encoding="utf-8")
    assert "# mode: structural-motion" in body
    assert "STRUCTURAL_ONLY" in body
    assert "structural motion target" in body
    assert 'exit 1' in body, "full-frame trajectory failures must propagate nonzero"


def test_runtime_spec_coverage_fails_when_gsap_timeline_target_missing(tmp_path: Path) -> None:
    """Runtime GSAP timelines are motion signal even without ScrollTrigger.

    A spec with unrelated hover entries must not satisfy a runtime timeline
    whose captured target is absent from transition-spec.json.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "animation-runtime-dump.json").write_text(json.dumps({
        "scrollTrigger": [],
        "ix2": None,
        "gsapTimelines": [
            {
                "kind": "Tween",
                "duration": 1.2,
                "easeName": "power2.out",
                "targets": [".hero-title", ".hero-title"],
            }
        ],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "button-hover", "trigger": "hover", "selector": ".button"}
        ],
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-spec-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "runtime-spec-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["gsapTimelineCount"] == 1
    assert any(".hero-title" in m for m in artifact["missing"])


def test_runtime_spec_coverage_requires_custom_ease_when_used_by_timeline(tmp_path: Path) -> None:
    """If runtime uses a named CustomEase, the spec must carry that exact key
    or curve data. Otherwise downstream implementation falls back to guessed
    cubic-bezier motion.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "animation-runtime-dump.json").write_text(json.dumps({
        "customEaseRegistry": {
            "heroEase": "M0,0 C0.126,0.382 0.243,1 1,1",
        },
        "gsapTimelines": [
            {
                "kind": "Tween",
                "duration": 1.2,
                "easeName": "heroEase",
                "targets": [".hero-title"],
            }
        ],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {
                "id": "hero-load",
                "trigger": "page-load",
                "selector": ".hero-title",
                "animation": {"duration": 1.2, "ease": "power2.out"},
            }
        ],
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-spec-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "runtime-spec-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["customEaseCount"] == 1
    assert artifact["customEaseUsedCount"] == 1
    assert any("heroEase" in m for m in artifact["missing"])


def test_runtime_spec_coverage_passes_when_gsap_target_and_custom_ease_are_specified(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "animation-runtime-dump.json").write_text(json.dumps({
        "customEaseRegistry": {
            "heroEase": "M0,0 C0.126,0.382 0.243,1 1,1",
        },
        "gsapTimelines": [
            {
                "kind": "Tween",
                "duration": 1.2,
                "easeName": "heroEase",
                "targets": [".hero-title"],
            }
        ],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {
                "id": "hero-load",
                "trigger": "page-load",
                "selector": ".hero-title",
                "animation": {"duration": 1.2, "ease": "heroEase"},
            }
        ],
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-spec-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "runtime-spec-coverage.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["gsapTimelineCount"] == 1
    assert artifact["customEaseUsedCount"] == 1


def test_runtime_spec_coverage_warns_on_low_gsap_target_coverage(tmp_path: Path) -> None:
    """Mentioning one runtime GSAP target is not enough coverage for a rich timeline."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "animation-runtime-dump.json").write_text(json.dumps({
        "gsapTimelines": [
            {
                "kind": "Tween",
                "duration": 1.2,
                "easeName": "power2.out",
                "targets": [".hero-title", ".hero-card", ".stats-grid", ".footer-cta"],
            }
        ],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {
                "id": "hero-load",
                "trigger": "page-load",
                "selector": ".hero-title",
                "animation": {"duration": 1.2, "ease": "power2.out"},
            }
        ],
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-spec-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "runtime-spec-coverage.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["gsapTimelineTargetCount"] == 4
    assert artifact["gsapTimelineTargetCoveredCount"] == 1
    assert any("GSAP timeline target coverage low" in w for w in artifact["warnings"])



def test_transition_proof_rollup_dispatcher_wired() -> None:
    import re
    dispatcher = _project_root() / "scripts" / "verify" / "run-required-checks.sh"
    text = dispatcher.read_text(encoding="utf-8")
    m = re.search(r'"transition-proof-rollup\.sh":\s*"([^"]+)"', text)
    assert m, "transition-proof-rollup.sh missing from dispatcher SIGNATURES"
    assert "{ref_dir}" in m.group(1)



def test_fix16_extract_dom_captures_transitions_and_animations() -> None:
    """Fix 16 — extract-dom.sh's LAYOUT_PROPS must include transition + animation
    properties so the impl renders the same hover/focus/active/keyframe motion
    as the ref. Without these the transpiler emits static JSX and the page
    looks dead. NOISE must also drop the user-agent defaults for these props
    ('all 0s ease 0s' etc.) so every node doesn't carry meaningless data.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "extract-dom.sh"
    text = script.read_text(encoding="utf-8")
    for prop in (
        'transition', 'transition-property', 'transition-duration',
        'animation', 'animation-name', 'animation-duration',
        'cursor',
    ):
        assert f"'{prop}'" in text, (
            f"extract-dom.sh LAYOUT_PROPS must include {prop} (Fix 16)"
        )
    # The default transition computed value Chromium emits — must be filtered.
    assert "'all 0s ease 0s'" in text, (
        "NOISE must drop the user-agent default transition value 'all 0s ease 0s'"
    )



def test_required_media_fails_when_artifact_absent(tmp_path: Path) -> None:
    """Coverage gate must not silently pass when Step 6b-bis was skipped."""
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "public").mkdir()
    (impl / "package.json").write_text(json.dumps({"dependencies": {}}))
    proc = _run_script(
        "skills/visual-debug/scripts/required-media-coverage-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "required-media-coverage.json").read_text())
    assert art["status"] == "fail"
    assert art["reason"].startswith("required-media.json absent")
    assert art["implRoot"] == str(impl)
    assert art["implDir"] == str(impl)
    assert art["implSrcDir"] == str(impl / "src")
    assert art["implPublicDir"] == str(impl / "public")
    assert art["implPkgJson"] == str(impl / "package.json")


def test_transition_compare_no_ref_transitions_writes_result_artifact(tmp_path: Path) -> None:
    """A no-transition skip still needs transitions/result.txt for the gate."""
    root = _project_root()
    out_dir = tmp_path / "ref"
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    agent_browser = shim_dir / "agent-browser"
    agent_browser.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = \"--session\" ]; then shift 2; fi\n"
        "case \"${1:-}\" in\n"
        "  open) echo opened ;;\n"
        "  eval) echo '[]' ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    agent_browser.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "WAIT_REF": "0",
        "WAIT_IMPL": "0",
    }

    proc = subprocess.run(
        [
            "bash",
            str(root / "skills" / "visual-debug" / "scripts" / "transition-compare.sh"),
            "https://example.test",
            "http://127.0.0.1:1",
            "transition-skip-test",
            str(out_dir),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = out_dir / "transitions" / "result.txt"
    assert result.is_file(), proc.stdout + proc.stderr
    assert "0 PASS, 0 FAIL" in result.read_text(encoding="utf-8")


def test_transition_proof_rejects_failed_transition_compare_even_with_runtime_samples(
    tmp_path: Path,
) -> None:
    """transition-proof must be at least as strict as transition-compare."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "transition-compare", "produces": "transitions/result.txt"},
            {"id": "transition-proof", "produces": "transition-proof.json"},
        ],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "btn-arrow", "trigger": "hover"}],
    }))
    (ref / "transition-spec-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "covered": 1,
    }))
    (ref / "spec-implementation-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "withMotion": 1,
    }))
    (ref / "transition-coverage.json").write_text(json.dumps({
        "animatedElements": [
            {
                "selector": ".btn-arrow",
                "samples": [
                    {"scrollY": 0, "opacity": "0", "transform": "matrix(1, 0, 0, 1, 0, 8)"},
                    {"scrollY": 10, "opacity": "1", "transform": "matrix(1, 0, 0, 1, 0, 0)"},
                ],
            }
        ],
    }))
    transitions = ref / "transitions"
    transitions.mkdir()
    (transitions / "result.txt").write_text(
        "Transition compare: 2 PASS, 1 FAIL\n❌ FAIL .btn-arrow timing mismatch\n",
        encoding="utf-8",
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "transition-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "transition-proof.json").read_text())
    assert artifact["status"] == "fail"
    assert any("transition compare" in reason for reason in artifact["reasons"])



def test_required_media_fail_when_impl_missing_videos(tmp_path: Path) -> None:
    """Required video present in required-media.json but missing in impl → fail."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "required-media.json").write_text(json.dumps({
        "schemaVersion": 1,
        "videos": [{
            "section": "hero", "src": "https://cdn/PC_1920x1080_High.mp4",
            "type": "video/mp4",
        }],
        "lottie": [],
        "svgs": [],
        "totals": {"video": 1, "lottie": 0, "svg": 0},
    }))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({"dependencies": {"react": "19"}}))
    (impl / "src" / "App.tsx").write_text(
        "export default function App(){return <div>placeholder</div>}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/required-media-coverage-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "required-media-coverage.json").read_text())
    assert art["status"] == "fail"
    assert art["totals"]["videoMissing"] == 1



def test_required_media_accepts_both_dict_and_list_html_shapes(tmp_path: Path) -> None:
    """Loop-codex-8 finding: required-media.sh's embedded Python called
    `section_data.get("media")` on html/<name>.json — but the producer
    (extract-section-html.sh on some sites including realfood.gov)
    emits a bare list of media entries rather than a dict wrapping the
    list under a "media" key. Result: `AttributeError: 'list' object
    has no attribute 'get'` at the first list-shaped file, before any
    artifact got written.

    Fix: accept both shapes. Dict → look up "media" key; list → use
    the value directly. This test pins behavior for both shapes so a
    future refactor cannot silently revert to the dict-only path.
    """
    ref = tmp_path / "ref"
    html = ref / "html"
    html.mkdir(parents=True)

    # File 1: dict shape (legacy producer)
    (html / "section-dict.json").write_text(json.dumps({
        "media": [
            {"tag": "video", "src": "https://example.com/dict.mp4",
             "type": "video/mp4", "autoplay": True, "loop": True, "muted": True,
             "w": 1280, "h": 720},
        ],
    }))
    # File 2: bare-list shape (codex-8 surfaced this on realfood.gov)
    (html / "section-list.json").write_text(json.dumps([
        {"tag": "video", "src": "https://example.com/list.mp4",
         "type": "video/mp4", "autoplay": False, "loop": False, "muted": False,
         "w": 1920, "h": 1080},
    ]))

    script = _project_root() / "scripts" / "extract" / "required-media.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    data = json.loads((ref / "required-media.json").read_text())
    video_srcs = {v["src"] for v in data.get("videos", [])}
    # Both shapes contributed their video — neither side silently dropped.
    assert "https://example.com/dict.mp4" in video_srcs, video_srcs
    assert "https://example.com/list.mp4" in video_srcs, video_srcs



def test_required_media_skips_unknown_shapes(tmp_path: Path) -> None:
    """Defense in depth: html/<name>.json that is neither dict nor list
    (e.g. a stray string, or null) must NOT crash the extractor."""
    ref = tmp_path / "ref"
    html = ref / "html"
    html.mkdir(parents=True)
    (html / "section-string.json").write_text(json.dumps("just a string"))
    (html / "section-null.json").write_text(json.dumps(None))

    script = _project_root() / "scripts" / "extract" / "required-media.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ── D1: transition-proof-rollup string-list animatedElements ──────────────


def test_transition_proof_rollup_handles_string_list_animated_elements(
    tmp_path: Path,
) -> None:
    """D1: transition-coverage.json may carry animatedElements as a list of
    selector strings (Phase 6d ref-side extraction). The rollup must normalize
    those instead of crashing with AttributeError ('str' has no attribute
    'get'), then compose a verdict like any other input.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "nav-hover"}],
    }))
    (ref / "transition-spec-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "covered": 1,
    }))
    (ref / "spec-implementation-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "withMotion": 1,
    }))
    # animatedElements as bare selector strings — the crashing schema.
    (ref / "transition-coverage.json").write_text(json.dumps({
        "animatedElements": [".nav-link", ".cta-btn", "#hero"],
    }))
    # A passing runtime proof source carries the firing evidence.
    (ref / "reveal-trigger.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass",
    }))

    script = (
        _project_root() / "skills" / "visual-debug" / "scripts"
        / "transition-proof-rollup.sh"
    )
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )

    assert "AttributeError" not in proc.stderr, proc.stderr
    artifact_path = ref / "transition-proof.json"
    assert artifact_path.is_file(), (
        "rollup must produce a verdict artifact, not crash"
    )
    artifact = json.loads(artifact_path.read_text())
    assert artifact["status"] == "pass", artifact
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_transition_proof_rollup_string_list_without_runtime_proof_fails(
    tmp_path: Path,
) -> None:
    """D1 no-loosening guard: string-list animatedElements with NO runtime proof
    source must still FAIL. The normalization is a robustness fix, not a change
    to pass/fail semantics.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "nav-hover"}],
    }))
    (ref / "transition-spec-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "covered": 1,
    }))
    (ref / "spec-implementation-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "withMotion": 1,
    }))
    (ref / "transition-coverage.json").write_text(json.dumps({
        "animatedElements": [".nav-link", ".cta-btn"],
    }))

    script = (
        _project_root() / "skills" / "visual-debug" / "scripts"
        / "transition-proof-rollup.sh"
    )
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )

    assert "AttributeError" not in proc.stderr, proc.stderr
    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "transition-proof.json").read_text())
    assert artifact["status"] == "fail"
    assert any("runtime proof" in r for r in artifact["reasons"]), artifact["reasons"]


# ── B4: transition-compare per-property matching ──────────────────────────

# getComputedStyle joins transition-timing-function with commas, but
# cubic-bezier() contains internal commas, so a naive split over-fragments it
# into four tokens. The element captures below mirror that real on-wire shape.
_CUBIC_TOKENS = ["cubic-bezier(0.165", "0.84", "0.44", "1)"]


def _trans_el(
    selector: str,
    props: list[str],
    durs: list[str],
    easings: list[str],
    *,
    color: str = "rgb(20, 20, 20)",
) -> dict[str, object]:
    """Build a captured transition element matching transition-compare.sh's
    ref-elements.json / impl-elements.json schema."""
    return {
        "selector": selector,
        "tag": "a",
        "text": selector.lstrip(".#"),
        "rect": {"top": 80, "left": 80, "width": 240, "height": 60},
        "transition": {"properties": props, "durations": durs, "easings": easings},
        "idleStyle": {
            "opacity": "1",
            "transform": "none",
            "backgroundColor": "rgb(17, 17, 17)",
            "color": color,
            "scale": "none",
            "filter": "none",
            "boxShadow": "none",
        },
    }


def _run_transition_compare_with_fake_browser(
    tmp_path: Path,
    ref_elements: list[dict[str, object]],
    impl_elements: list[dict[str, object]],
) -> str:
    """Drive transition-compare.sh end-to-end with a fake agent-browser that
    feeds canned element captures, then return transitions/result.txt.

    The fake browser answers the detection eval (identified by the
    'transitionProperty' probe) with ref/impl element JSON keyed by session
    suffix, returns an empty object for hover computed-style evals (so no hover
    issues fire), and 'ok' for everything else. No real browser or served page
    is required, so the pure per-property comparison logic is exercised in
    isolation against realistic captures.
    """
    root = _project_root()
    out_dir = tmp_path / "ref"
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()

    ref_file = tmp_path / "ref-elements.json"
    impl_file = tmp_path / "impl-elements.json"
    ref_file.write_text(json.dumps(ref_elements), encoding="utf-8")
    impl_file.write_text(json.dumps(impl_elements), encoding="utf-8")

    agent_browser = shim_dir / "agent-browser"
    agent_browser.write_text(
        "#!/usr/bin/env bash\n"
        'session=""\n'
        'if [ "$1" = "--session" ]; then session="$2"; shift 2; fi\n'
        'cmd="${1:-}"\n'
        'if [ "$cmd" = "eval" ]; then\n'
        '  js="${2:-}"\n'
        '  if [[ "$js" == *transitionProperty* ]]; then\n'
        '    case "$session" in\n'
        '      *-tc-ref) cat "$_SHIM_REF_ELEMENTS" ;;\n'
        '      *-tc-impl) cat "$_SHIM_IMPL_ELEMENTS" ;;\n'
        "      *) printf '[]' ;;\n"
        "    esac\n"
        '  elif [[ "$js" == *boxShadow* ]]; then\n'
        "    printf '{}'\n"
        "  else\n"
        "    printf 'ok'\n"
        "  fi\n"
        "else\n"
        "  printf 'ok'\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    agent_browser.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "_SHIM_REF_ELEMENTS": str(ref_file),
        "_SHIM_IMPL_ELEMENTS": str(impl_file),
        "WAIT_REF": "0",
        "WAIT_IMPL": "0",
        "TRANSITION_WAIT": "0",
        "_TC_SCROLL_WAIT": "0",
    }

    proc = subprocess.run(
        [
            "bash",
            str(root / "skills" / "visual-debug" / "scripts" / "transition-compare.sh"),
            "https://ref.test",
            "http://127.0.0.1:1",
            "trans-fix",
            str(out_dir),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return (out_dir / "transitions" / "result.txt").read_text(encoding="utf-8")


def test_transition_compare_passes_faithful_clone_with_extra_inert_property(
    tmp_path: Path,
) -> None:
    """B4: a faithful hover (exact ref duration+easing on the property that
    actually animates) must PASS even when the impl additionally declares a
    harmless extra inert transition the ref does not animate. Per-property
    matching, not whole-list equality.
    """
    ref = [
        _trans_el(".cta-btn", ["opacity"], ["0.5s"], list(_CUBIC_TOKENS)),
        _trans_el(".fade-link", ["color"], ["0.3s"], ["ease"]),
    ]
    impl = [
        # faithful opacity 0.5s cubic PLUS an inert transform 0.5s cubic
        _trans_el(
            ".cta-btn",
            ["opacity", "transform"],
            ["0.5s", "0.5s"],
            _CUBIC_TOKENS + _CUBIC_TOKENS,
        ),
        _trans_el(".fade-link", ["color"], ["0.3s"], ["ease"]),
    ]
    result = _run_transition_compare_with_fake_browser(tmp_path, ref, impl)
    assert "2 PASS, 0 FAIL" in result, result


def test_transition_compare_fails_wrong_duration_and_easing(
    tmp_path: Path,
) -> None:
    """B4 no-loosening guard: a WRONG duration+easing on the property the ref
    animates must still FAIL after per-property matching.
    """
    ref = [
        _trans_el(".cta-btn", ["opacity"], ["0.5s"], list(_CUBIC_TOKENS)),
        _trans_el(".fade-link", ["color"], ["0.3s"], ["ease"]),
    ]
    impl = [
        _trans_el(".cta-btn", ["opacity"], ["0.1s"], ["linear"]),
        _trans_el(".fade-link", ["color"], ["0.3s"], ["ease"]),
    ]
    result = _run_transition_compare_with_fake_browser(tmp_path, ref, impl)
    assert "1 PASS, 1 FAIL" in result, result
    assert "DURATION_MISMATCH" in result, result
    assert ".cta-btn" in result, result


def test_transition_compare_fails_when_impl_omits_animated_property(
    tmp_path: Path,
) -> None:
    """B4 no-loosening guard: if the impl element exists but does NOT transition
    a property the ref animates, that is a MISSING transition → FAIL (the impl
    cannot 'drop' a ref-animated property and still pass).
    """
    ref = [
        _trans_el(".cta-btn", ["opacity"], ["0.5s"], list(_CUBIC_TOKENS)),
    ]
    impl = [
        # impl transitions only 'color', never the ref's animated 'opacity'
        _trans_el(".cta-btn", ["color"], ["0.3s"], ["ease"]),
    ]
    result = _run_transition_compare_with_fake_browser(tmp_path, ref, impl)
    assert "0 PASS, 1 FAIL" in result, result
    assert "MISSING_TRANSITION" in result, result
