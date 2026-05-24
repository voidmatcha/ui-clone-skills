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



def test_required_media_pass_when_artifact_absent(tmp_path: Path) -> None:
    """Coverage gate dispatched unconditionally; absent required-media.json → pass."""
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    proc = _run_script(
        "skills/visual-debug/scripts/required-media-coverage-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "required-media-coverage.json").read_text())
    assert art["status"] == "pass"



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

