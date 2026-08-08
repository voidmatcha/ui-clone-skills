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


def _write_passing_transition_fire(ref: Path, transition_id: str) -> None:
    (ref / "transition-fires.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "pass",
                "total": 1,
                "fired": 1,
                "known_skip": 0,
                "failed": 0,
                "unmeasurable": 0,
                "entries": [{"id": transition_id, "status": "pass"}],
            }
        ),
        encoding="utf-8",
    )


def test_transition_compare_does_not_lock_section_threshold() -> None:
    """transition-compare has its own scoring; the SECTION_THRESHOLD lock
    is irrelevant there. Only section-compare gets the AE-classifier lock.
    """
    captured_env: dict[str, str] = {}

    def fake_run(
        cmd: list[str],
        env: dict[str, str] | None = None,
        **kw: object,  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        if any("BASH_VERSION" in str(part) for part in cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="5")
        if env is not None:
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


def test_transition_compare_filters_card_tile_noise() -> None:
    """Card/content carousel anchors must not starve global controls.

    Content grids can contain dozens of animated card anchors. If
    transition-compare lets those consume the first MAX_TRANSITIONS slots, the
    gate misses semantic controls such as banner CTAs, tabs, nav links, and
    footer/header links.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "transition-compare.sh"
    detect_helper = script.parent / "lib" / "transition-detect.js"
    body = script.read_text(encoding="utf-8") + detect_helper.read_text(encoding="utf-8")
    assert "[class*=news-list]" in body
    assert "masonry-list" in body
    assert "[class*=card-list]" in body
    assert "COMPARE_LIMIT" in body
    assert "(btn|button|cta|tab)" in body
    assert "Offscreen reveal wrappers" in body
    assert "card/image/runtime/live-parity" in body



def test_all_skips_transition_compare_when_no_spec(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """No transition-spec.json → transition-compare skipped (recorded as skip
    in summary). The bash script would otherwise error on missing input.
    """
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    # No transition-spec.json

    invoked_scripts: list[str] = []

    def fake_run(
        cmd: list[str],
        env: dict[str, str] | None = None,  # noqa: ARG001
        **kw: object,  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        if any("BASH_VERSION" in str(part) for part in cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="5")
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


def test_direct_transition_scripts_use_standalone_helpers_without_heredocs() -> None:
    """Homebrew Bash 5.1+ pipe-backed heredocs can block before child startup."""
    scripts_dir = _project_root() / "skills" / "visual-debug" / "scripts"
    expected_helpers = (
        scripts_dir / "video_play_proof.py",
        scripts_dir / "transition_capture_hover.py",
        scripts_dir / "transition_compare_report.py",
        scripts_dir / "lib" / "transition-detect.js",
    )
    for helper in expected_helpers:
        assert helper.is_file(), f"standalone helper missing: {helper}"

    for name in ("video-play-proof-check.sh", "transition-compare.sh"):
        body = (scripts_dir / name).read_text(encoding="utf-8")
        assert "<<" not in body, f"{name} must not use inline heredocs"


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
    env.pop("BASH_COMPAT", None)
    proc = subprocess.run(
        ["bash", str(script), "vpp-test", "http://127.0.0.1:1/", str(ref)],
        capture_output=True, text=True, timeout=10, env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "video-play-proof.json").read_text())
    assert artifact["status"] == "skip"


def test_video_play_proof_detects_live_dynamic_video_signal(tmp_path: Path) -> None:
    """live-parity-sweep can observe runtime video elements even when
    required-media has no concrete mp4/webm URL.

    A ref-side `video:pause` action plus a concrete runtime video URL means the
    impl must have video runtime parity; the gate must not skip just because
    required-media videos=[].
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
    (ref / "live-dynamic-state.json").write_text(json.dumps({
        "schemaVersion": 1,
        "ref": {
            "actions": ["css-animation-transition-freeze", "video:pause"],
            "videos": [{"src": "https://cdn.example.com/hero.mp4"}],
        },
        "impl": {"actions": ["css-animation-transition-freeze"]},
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

    assert proc.returncode == 1, (
        "video signal should reach runtime probe instead of skip; fake "
        f"agent-browser then yields a failing proof artifact: {proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "video-play-proof.json").read_text())
    assert artifact["status"] == "fail"


def test_video_play_proof_ignores_empty_runtime_video_shells(tmp_path: Path) -> None:
    """Empty <video src=""> shells are not playable media requirements.

    Some hydrated pages create muted/autoplay video nodes with no src/currentSrc;
    the browser may report video pause actions or a text/html resource request
    against the page URL. That must not force the impl to invent a video.
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
    (ref / "live-dynamic-state.json").write_text(json.dumps({
        "schemaVersion": 1,
        "ref": {"actions": ["css-animation-transition-freeze", "video:pause"]},
        "impl": {"actions": ["css-animation-transition-freeze"]},
    }))
    (ref / "resource-manifest.json").write_text(json.dumps({
        "schemaVersion": 1,
        "resources": [
            {
                "url": "https://example.com/",
                "initiatorType": "video",
                "contentType": "text/html",
            }
        ],
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
    dispatcher = _project_root() / "scripts" / "verify" / "build_required_dispatch.py"
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

    Video evidence can carry declaration-only coverage after transition-fires
    binds the runtime observation to the declared transition identity.
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
    _write_passing_transition_fire(ref, "patch-scroll-parallax")
    transitions = ref / "transitions"
    transitions.mkdir()
    (transitions / "video-motion-result.txt").write_text(
        "✓ trajectory pre-filter passed\n✅ structural motion trajectory passed\n"
        "# video-motion-compare: COMPLETE\n",
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
    """Quick-tier hover proof can carry single-sample ref-side coverage."""
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
    _write_passing_transition_fire(ref, "readymag-nav-hover-transition")
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
        "recording completed but report parser crashed before verdict\n"
        "# video-motion-compare: COMPLETE\n",
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


def test_runtime_spec_coverage_fails_on_low_gsap_target_coverage(tmp_path: Path) -> None:
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

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "runtime-spec-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["gsapTimelineTargetCount"] == 4
    assert artifact["gsapTimelineTargetCoveredCount"] == 1
    assert any("GSAP timeline target coverage low" in m for m in artifact["missing"])


def test_runtime_spec_coverage_accepts_grouped_gsap_target_plan(tmp_path: Path) -> None:
    """One grouped plan selector may intentionally cover several runtime tweens."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "animation-runtime-dump.json").write_text(json.dumps({
        "gsapTimelines": [
            {
                "kind": "Timeline",
                "targets": [
                    ".hero .title",
                    ".hero .card",
                    ".hero .badge",
                    ".hero .cta",
                ],
            }
        ],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {
                "id": "hero-load",
                "trigger": "page-load",
                "selector": ".hero > *",
                "animation": {"type": "stagger"},
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
    assert artifact["gsapTimelineTargetCoveredCount"] == 4


def test_runtime_spec_coverage_accepts_leaf_gsap_target_plan(tmp_path: Path) -> None:
    """A specific leaf selector may identify the same nested runtime target."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "animation-runtime-dump.json").write_text(json.dumps({
        "gsapTimelines": [{"kind": "Tween", "targets": [".hero .title"]}],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {
                "id": "hero-title",
                "trigger": "page-load",
                "selector": ".title",
            }
        ],
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-spec-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "runtime-spec-coverage.json").read_text())
    assert artifact["gsapTimelineTargetCoveredCount"] == 1


def test_runtime_spec_coverage_rejects_shared_parent_and_prose_only_match(
    tmp_path: Path,
) -> None:
    """A parent selector or prose mention must not claim child target coverage."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "animation-runtime-dump.json").write_text(json.dumps({
        "gsapTimelines": [
            {
                "kind": "Timeline",
                "targets": [
                    ".hero .title",
                    ".hero .card",
                    ".hero .badge",
                    ".hero .cta",
                ],
            }
        ],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {
                "id": "hero-load",
                "trigger": "page-load",
                "selector": ".hero",
                "notes": "Animate title, card, badge, and cta children.",
            }
        ],
        "skipped": [".title"],
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-spec-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "runtime-spec-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["gsapTimelineTargetCoveredCount"] == 0
    assert any("mentions none of their targets" in m for m in artifact["missing"])


def test_transition_proof_rollup_dispatcher_wired() -> None:
    import re
    dispatcher = _project_root() / "scripts" / "verify" / "build_required_dispatch.py"
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
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lib" / "extract-dom.js"
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
    env.pop("BASH_COMPAT", None)

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
        timeout=120,
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



def test_required_media_merges_runtime_media_videos(tmp_path: Path) -> None:
    """JS-created background videos appear only in runtime-media.json.

    required-media must promote those live DOM findings; otherwise video-proof
    can detect ref videos while the generator never receives a video entry and
    ships an impl with zero <video> elements.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "runtime-media.json").write_text(json.dumps({
        "schemaVersion": 1,
        "videos": [
            {
                "section": "hero",
                "currentSrc": "https://cdn.example.com/media/hero.mp4?token=1",
                "src": "",
                "sources": [
                    {"src": "https://cdn.example.com/media/hero.webm", "type": "video/webm"},
                ],
                "poster": "https://cdn.example.com/media/hero.jpg",
                "autoplay": True,
                "loop": True,
                "muted": True,
                "playsInline": True,
                "rect": {"w": 1440, "h": 810},
            }
        ],
    }), encoding="utf-8")

    script = _project_root() / "scripts" / "extract" / "required-media.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    data = json.loads((ref / "required-media.json").read_text())
    video_srcs = {v["src"] for v in data.get("videos", [])}
    assert "https://cdn.example.com/media/hero.mp4?token=1" in video_srcs
    assert "https://cdn.example.com/media/hero.webm" in video_srcs
    hero = next(v for v in data["videos"] if v["src"].endswith("hero.webm"))
    assert hero["evidenceKind"] == "runtime-video"
    assert hero["section"] == "hero"
    assert hero["muted"] is True
    assert data["sources"]["runtimeMediaScanned"] is True



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
    # Supplemental reveal proof plus canonical transition identity evidence.
    (ref / "reveal-trigger.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass",
    }))
    _write_passing_transition_fire(ref, "nav-hover")

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
    text: str | None = None,
    tag: str = "a",
    href: str = "",
    aria: str = "",
    rect: dict[str, int] | None = None,
) -> dict[str, object]:
    """Build a captured transition element matching transition-compare.sh's
    ref-elements.json / impl-elements.json schema."""
    return {
        "selector": selector,
        "tag": tag,
        "text": text if text is not None else selector.lstrip(".#"),
        "matchKey": {
            "tag": tag,
            "text": text if text is not None else selector.lstrip(".#"),
            "href": href or aria,
            "role": "",
            "aria": aria,
        },
        "rect": rect or {"top": 80, "left": 80, "width": 240, "height": 60},
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
    *,
    expect_success: bool = True,
    ref_hover_style: dict[str, str] | None = None,
    impl_hover_style: dict[str, str] | None = None,
    transition_spec: dict[str, object] | None = None,
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
    ref_hover_file = tmp_path / "ref-hover.json"
    impl_hover_file = tmp_path / "impl-hover.json"
    ref_file.write_text(json.dumps(ref_elements), encoding="utf-8")
    impl_file.write_text(json.dumps(impl_elements), encoding="utf-8")
    ref_hover_file.write_text(json.dumps(ref_hover_style or {}), encoding="utf-8")
    impl_hover_file.write_text(json.dumps(impl_hover_style or {}), encoding="utf-8")
    if transition_spec is not None:
        (out_dir / "transition-spec.json").parent.mkdir(parents=True, exist_ok=True)
        (out_dir / "transition-spec.json").write_text(
            json.dumps(transition_spec),
            encoding="utf-8",
        )

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
        '  elif [[ "$js" == *hovered*matches* ]]; then\n'
        "    printf '{\"found\":true,\"hovered\":true,"
        "\"rect\":{\"x\":80,\"y\":80,\"width\":240,\"height\":60}}'\n"
        '  elif [[ "$js" == *boxShadow* ]]; then\n'
        '    case "$session" in\n'
        '      *-tc-ref) cat "$_SHIM_REF_HOVER" ;;\n'
        '      *-tc-impl) cat "$_SHIM_IMPL_HOVER" ;;\n'
        "      *) printf '{}' ;;\n"
        "    esac\n"
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
        "_SHIM_REF_HOVER": str(ref_hover_file),
        "_SHIM_IMPL_HOVER": str(impl_hover_file),
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
    expected_rc = 0 if expect_success else 1
    assert proc.returncode == expected_rc, proc.stdout + proc.stderr
    return (out_dir / "transitions" / "result.txt").read_text(encoding="utf-8")


def _dynamic_carousel_spec() -> dict[str, object]:
    return {
        "transitions": [
            {
                "dynamic": True,
                "target": ".dynamic-carousel",
                "animation": {"type": "timer carousel"},
            }
        ]
    }


def test_transition_compare_matches_dynamic_carousel_phase_label_by_slot(
    tmp_path: Path,
) -> None:
    rect = {"top": 8430, "left": 1240, "width": 40, "height": 40}
    ref = [
        _trans_el(
            "main > .dynamic-carousel button:nth-of-type(1)",
            ["background-color"],
            ["0.4s"],
            ["ease"],
            tag="button",
            text="",
            aria="Previous slide",
            rect=rect,
        )
    ]
    impl = [
        _trans_el(
            ".h_4",
            ["background-color"],
            ["0.4s"],
            ["ease"],
            tag="button",
            text="",
            aria="Back to final slide",
            rect=rect,
        )
    ]
    result = _run_transition_compare_with_fake_browser(
        tmp_path,
        ref,
        impl,
        transition_spec=_dynamic_carousel_spec(),
    )
    assert "Transition compare: 1 PASS, 0 FAIL" in result


def test_transition_compare_dynamic_carousel_same_slot_beats_far_exact_label(
    tmp_path: Path,
) -> None:
    ref_rect = {"top": 8430, "left": 1240, "width": 40, "height": 40}
    far_rect = {"top": 13778, "left": 398, "width": 40, "height": 40}
    ref = [
        _trans_el(
            "main > .dynamic-carousel button:nth-of-type(1)",
            ["background-color"],
            ["0.4s"],
            ["ease"],
            tag="button",
            text="",
            aria="Previous slide",
            rect=ref_rect,
        )
    ]
    same_slot = _trans_el(
        ".h_4",
        ["background-color"],
        ["0.4s"],
        ["ease"],
        tag="button",
        text="",
        aria="Back to final slide",
        rect=ref_rect,
    )
    far_exact = _trans_el(
        ".other-carousel-prev",
        ["background-color"],
        ["0.4s"],
        ["ease"],
        tag="button",
        text="",
        aria="Previous slide",
        rect=far_rect,
    )
    far_exact["idleStyle"]["opacity"] = "0.3"  # type: ignore[index]
    result = _run_transition_compare_with_fake_browser(
        tmp_path,
        ref,
        [far_exact, same_slot],
        transition_spec=_dynamic_carousel_spec(),
    )
    assert "Transition compare: 1 PASS, 0 FAIL" in result


def test_transition_compare_dynamic_carousel_rejects_far_exact_label_only(
    tmp_path: Path,
) -> None:
    ref = [
        _trans_el(
            "main > .dynamic-carousel button:nth-of-type(1)",
            ["background-color"],
            ["0.4s"],
            ["ease"],
            tag="button",
            text="",
            aria="Previous slide",
            rect={"top": 8430, "left": 1240, "width": 40, "height": 40},
        )
    ]
    impl = [
        _trans_el(
            ".other-carousel-prev",
            ["background-color"],
            ["0.4s"],
            ["ease"],
            tag="button",
            text="",
            aria="Previous slide",
            rect={"top": 13778, "left": 398, "width": 40, "height": 40},
        )
    ]
    result = _run_transition_compare_with_fake_browser(
        tmp_path,
        ref,
        impl,
        expect_success=False,
        transition_spec=_dynamic_carousel_spec(),
    )
    assert "MISSING: no matching element in impl" in result


def test_transition_compare_matches_second_dynamic_carousel_phase_slot(
    tmp_path: Path,
) -> None:
    rect = {"top": 8430, "left": 1292, "width": 40, "height": 40}
    ref = [
        _trans_el(
            "main > .dynamic-carousel button:nth-of-type(2)",
            ["background-color"],
            ["0.4s"],
            ["ease"],
            tag="button",
            text="",
            aria="Back to first slide",
            rect=rect,
        )
    ]
    impl = [
        _trans_el(
            ".h_5",
            ["background-color"],
            ["0.4s"],
            ["ease"],
            tag="button",
            text="",
            aria="Next slide",
            rect=rect,
        )
    ]
    result = _run_transition_compare_with_fake_browser(
        tmp_path,
        ref,
        impl,
        transition_spec=_dynamic_carousel_spec(),
    )
    assert "Transition compare: 1 PASS, 0 FAIL" in result


def test_transition_compare_does_not_slot_match_non_dynamic_controls(
    tmp_path: Path,
) -> None:
    rect = {"top": 120, "left": 120, "width": 40, "height": 40}
    ref = [
        _trans_el(
            ".static-controls .previous",
            ["background-color"],
            ["0.4s"],
            ["ease"],
            tag="button",
            text="",
            aria="Previous item",
            rect=rect,
        )
    ]
    impl = [
        _trans_el(
            ".static-controls .next",
            ["background-color"],
            ["0.4s"],
            ["ease"],
            tag="button",
            text="",
            aria="Next item",
            rect=rect,
        )
    ]
    result = _run_transition_compare_with_fake_browser(
        tmp_path,
        ref,
        impl,
        expect_success=False,
        transition_spec={"transitions": []},
    )
    assert "MISSING: no matching element in impl" in result


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


def test_transition_compare_does_not_pair_shared_tracking_class_with_wrong_element(
    tmp_path: Path,
) -> None:
    """A shared tracking class is not element identity.

    Some sites stamp the same tracking class on the logo, nav links, social
    icons, and buttons. The verifier must match the ref nav link to the impl
    nav link by semantic evidence, not to the first impl element carrying that
    tracking class by selector alone.
    """
    ref = [
        _trans_el(
            ".nclick-target",
            ["color"],
            ["0.4s"],
            ["ease"],
            text="회사소개",
        ),
    ]
    impl = [
        _trans_el(
            ".nclick-target",
            ["all"],
            ["0.4s"],
            ["cubic-bezier(0.33", "1", "0.68", "1)"],
            text="logo",
        ),
        _trans_el(
            ".nav__link",
            ["color"],
            ["0.4s"],
            ["ease"],
            text="회사소개",
        ),
    ]

    result = _run_transition_compare_with_fake_browser(tmp_path, ref, impl)

    assert "1 PASS, 0 FAIL" in result, result


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
    result = _run_transition_compare_with_fake_browser(
        tmp_path,
        ref,
        impl,
        expect_success=False,
    )
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
    result = _run_transition_compare_with_fake_browser(
        tmp_path,
        ref,
        impl,
        expect_success=False,
    )
    assert "0 PASS, 1 FAIL" in result, result
    assert "MISSING_TRANSITION" in result, result


def test_transition_compare_fails_impl_only_hover_opacity_or_transform(
    tmp_path: Path,
) -> None:
    """A clone must not invent hover motion for a ref-static element.

    This catches regressions such as header hover rotation or a card/link
    disappearing on hover when the reference hover state did not change.
    """
    ref = [
        _trans_el(".nav-link", ["color"], ["0.3s"], ["ease"], text="About"),
    ]
    impl = [
        _trans_el(".nav-link", ["color"], ["0.3s"], ["ease"], text="About"),
    ]
    result = _run_transition_compare_with_fake_browser(
        tmp_path,
        ref,
        impl,
        expect_success=False,
        ref_hover_style={
            "opacity": "1",
            "transform": "none",
            "scale": "none",
            "backgroundColor": "rgb(17, 17, 17)",
            "color": "rgb(20, 20, 20)",
        },
        impl_hover_style={
            "opacity": "0",
            "transform": "matrix(0, 1, -1, 0, 0, 0)",
            "scale": "none",
            "backgroundColor": "rgb(17, 17, 17)",
            "color": "rgb(20, 20, 20)",
        },
    )
    assert "0 PASS, 1 FAIL" in result, result
    assert "EXTRA_HOVER_OPACITY_APPLIED" in result, result
    assert "EXTRA_HOVER_TRANSFORM_APPLIED" in result, result


def test_transition_proof_skips_video_hover_for_reset_only_known_skip(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    transitions = ref / "transitions"
    transitions.mkdir(parents=True)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "transition-spec-coverage", "produces": "transition-spec-coverage.json"},
            {"id": "spec-implementation-coverage", "produces": "spec-implementation-coverage.json"},
            {"id": "transition-fires", "produces": "transition-fires.json"},
            {"id": "video-motion-compare", "produces": "transitions/video-motion-result.txt"},
            {"id": "hover-state-compare", "produces": "transitions/hover-state-result.txt"},
        ],
    }), encoding="utf-8")
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "auto-hover-0",
            "trigger": "hover",
            "type": "css-hover",
            "target": "a",
            "animation": {"type": "css-hover", "cssText": "a:hover {text-decoration:none}"},
        }]
    }), encoding="utf-8")
    (ref / "transition-spec-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 1, "covered": 1,
    }), encoding="utf-8")
    (ref / "spec-implementation-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "total": 0, "withMotion": 0,
    }), encoding="utf-8")
    (ref / "transition-fires.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "pass",
        "total": 1,
        "fired": 0,
        "known_skip": 1,
        "failed": 0,
        "unmeasurable": 0,
        "entries": [{"id": "auto-hover-0", "status": "known-skip"}],
    }), encoding="utf-8")
    (ref / "transition-coverage.json").write_text(json.dumps({
        "schemaVersion": 1,
        "animatedElements": [{"id": "auto-hover-0", "selector": "a", "trigger": "hover"}],
    }), encoding="utf-8")
    (ref / "keyframes-diff.json").write_text(json.dumps({"only_ref": [], "shared_diffs": []}), encoding="utf-8")
    (ref / "scroll-completion.json").write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    (transitions / "result.txt").write_text("1 PASS, 0 FAIL\n", encoding="utf-8")
    (transitions / "video-motion-result.txt").write_text(
        "trajectory pre-filter FAILED\n# video-motion-compare: COMPLETE\n", encoding="utf-8")
    (transitions / "hover-state-result.txt").write_text("hover-state: 5/5 target-run(s) diverged\n", encoding="utf-8")

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "transition-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "transition-proof.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "pass"
    notes = " ".join(component["note"] for component in artifact["components"])
    assert "reset-only hover specs" in notes
