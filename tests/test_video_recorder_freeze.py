"""Recorder fix (loop-e2e-6 follow-up): video-transition-compare.sh must
freeze time-coupled media — play()-stub + pause + currentTime=0 on every
<video>, identically on the ref and impl sessions — in each newly created
recording context, in every mode. Without it, frame-aligned SSIM compares video frame
PHASE, not the recorded transition: a hover target overlaying the autoplaying
hero video read flat SSIM 0.84 across all 534 frames on a clone whose
section-compare was 14/14 (verify-report.json, realfood-e2e-6).

The test stubs `agent-browser` with a PATH shim that logs every invocation,
runs the recorder in hover and splash modes, and asserts call ordering from
the log. ffmpeg frame extraction fails after the recording phase (no real
video is produced) — only the recording-phase ordering is asserted.

Also locks the splash-symmetry fix: the ref splash path used to record
seconds 3-8 after navigation while the impl recorded 0-5 (open AFTER record
start); both sides must now record from navigation start.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "verify" / "video-transition-compare.sh"

STUB = """#!/usr/bin/env bash
# logs every agent-browser invocation, one line per call
echo "$@" >> "$AB_CALL_LOG"
# answer the navigation-anchor poll (review-2 finding 5) so the recorder
# proceeds past the anchored splash window in the stubbed environment
case "$*" in
  *"hovered: el.matches"*) echo '{"found":true,"selector":".x","matchIndex":1,"matchCount":2,"hovered":true,"pointerReachable":true,"rect":{"x":40,"y":30,"width":40,"height":30}}' ;;
  *"const matches = Array.from(document.querySelectorAll"*) echo '{"found":true,"matchIndex":1,"matchCount":2,"scrollState":{"scrollY":0},"rect":{"x":40,"y":30,"width":40,"height":30}}' ;;
  *"performance.now() - epoch"*) echo '"0.100000"' ;;
  *readyState*) echo true ;;
esac
exit 0
"""


def _run(mode: str, tmp_path: Path) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "agent-browser"
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / f"calls-{mode.replace(':', '_').replace('.', '_')}.log"
    log.write_text("")
    out_dir = tmp_path / f"out-{mode.replace(':', '_').replace('.', '_')}"
    env = dict(
        os.environ,
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        AB_CALL_LOG=str(log),
        RECORD_DURATION="0",
        PRE_ACTION_WAIT="0",
    )
    subprocess.run(  # exits nonzero at ffmpeg phase — only the log matters
        [
            "bash",
            str(SCRIPT),
            "tst",
            "http://ref.example",
            "http://impl.example",
            str(out_dir),
            mode,
        ],
        env=env,
        capture_output=True,
        timeout=120,
        check=False,
    )
    return log.read_text().splitlines()


def _first_index(lines: list[str], *needles: str) -> int:
    for i, line in enumerate(lines):
        if all(n in line for n in needles):
            return i
    return -1


def _assert_freeze_after_record_context_starts(lines: list[str], session: str) -> None:
    freeze = _first_index(lines, "eval", "currentTime = 0", session)
    record = _first_index(lines, "record start", session)
    assert freeze != -1, f"no video-freeze eval for session {session}: {lines}"
    assert record != -1, f"no record start for session {session}: {lines}"
    assert record < freeze, (
        f"video freeze must run AFTER record start creates the captured context for {session} "
        f"(freeze@{freeze}, record@{record})"
    )


def _assert_play_stubbed(lines: list[str], session: str) -> None:
    # site JS (ref bundle AND impl controllers) re-kick play() on scroll —
    # a one-shot pause without the prototype stub un-freezes mid-recording.
    idx = _first_index(lines, "eval", "HTMLMediaElement.prototype.play", session)
    assert idx != -1, f"play() stub missing for session {session}"


def test_hover_mode_freezes_video_on_both_sessions(tmp_path: Path) -> None:
    lines = _run("hover:.x", tmp_path)
    for session in ("tst-orig", "tst-impl"):
        _assert_freeze_after_record_context_starts(lines, session)
        _assert_play_stubbed(lines, session)
        assert _first_index(lines, session, "mouse move 60 45") != -1, lines


def test_scroll_mode_freezes_video_without_recording(tmp_path: Path) -> None:
    # Scroll mode no longer records video (position-aligned still compare —
    # see tests/test_scroll_position_compare.py for capture-order coverage);
    # the media freeze must still run on both sessions so position frames
    # don't diff on video playback phase.
    lines = _run("scroll", tmp_path)
    assert not any("record start" in ln for ln in lines), lines
    for session in ("tst-orig", "tst-impl"):
        freeze = _first_index(lines, "eval", "currentTime = 0", session)
        assert freeze != -1, f"no video-freeze eval for session {session}: {lines}"
        _assert_play_stubbed(lines, session)


def test_splash_mode_freezes_video_on_both_sessions(tmp_path: Path) -> None:
    # splash records from navigation start: record start precedes the page
    # open, so the freeze evals immediately AFTER open (earliest possible).
    lines = _run("splash", tmp_path)
    for session in ("tst-orig", "tst-impl"):
        freeze = _first_index(lines, "eval", "currentTime = 0", session)
        assert freeze != -1, f"no video-freeze eval for splash {session}: {lines}"
        _assert_play_stubbed(lines, session)


def test_splash_mode_records_both_sides_from_navigation(tmp_path: Path) -> None:
    # symmetry: the ref side must not pre-wait 3s before recording while the
    # impl records from t=0 — both record start calls precede their page open.
    lines = _run("splash", tmp_path)
    for session, url in (("tst-orig", "http://ref.example"), ("tst-impl", "http://impl.example")):
        record = _first_index(lines, "record start", session)
        page_open = _first_index(lines, "open", url)
        assert record != -1 and page_open != -1, lines
        assert record < page_open, (
            f"splash recording for {session} must start before opening {url} "
            f"(record@{record}, open@{page_open})"
        )


# ── First-change alignment (codex-required synthetic-frame cases) ─────────

LIB = REPO / "scripts" / "verify" / "lib" / "frame-align.sh"


def _make_frames(d: Path, colors: list[str]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(colors, start=1):
        subprocess.run(
            ["magick", "-size", "128x128", f"xc:{c}", str(d / f"f-{i:06d}.png")],
            check=True,
            capture_output=True,
        )


def _first_change(d: Path, search_start: int = 1) -> str:
    subprocess.run(
        [
            "bash",
            "-c",
            f'source "{LIB}"; analyze_timing "{d}" t "{search_start}"',
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return (d / ".first-change").read_text().strip()


def _timing_bounds(d: Path, cluster_gap: int) -> tuple[str, str]:
    subprocess.run(
        [
            "bash",
            "-c",
            (
                f'source "{LIB}"; '
                f'FRAME_CHANGE_CLUSTER_GAP_FRAMES="{cluster_gap}" '
                f'analyze_timing "{d}" t 1'
            ),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return (
        (d / ".first-change").read_text().strip(),
        (d / ".last-change").read_text().strip(),
    )


def test_first_change_index_detects_transition_start(tmp_path: Path) -> None:
    # black until frame 3, white from frame 4 -> first change at index 4
    _make_frames(tmp_path / "a", ["black", "black", "black", "white", "white"])
    assert _first_change(tmp_path / "a") == "4"


def test_first_change_defaults_to_one_when_static(tmp_path: Path) -> None:
    # anti-bypass: a side with NO visual change keeps offset 0 (index 1) —
    # a missing transition cannot self-align to the other side's arc.
    _make_frames(tmp_path / "b", ["gray", "gray", "gray"])
    assert _first_change(tmp_path / "b") == "1"


def test_first_change_ignores_roi_noise_before_action_floor(tmp_path: Path) -> None:
    # Pre-action noise at frame 2 must not become the alignment point. The
    # action-era transition begins at frame 5.
    _make_frames(
        tmp_path / "post-action",
        ["black", "white", "white", "white", "black", "black"],
    )
    assert _first_change(tmp_path / "post-action", search_start=4) == "5"


def test_pre_action_noise_does_not_hide_missing_action_motion(tmp_path: Path) -> None:
    # Anti-bypass: if the only change happened before hover onset, post-action
    # analysis remains the no-motion sentinel instead of self-aligning to noise.
    _make_frames(
        tmp_path / "missing-action",
        ["black", "white", "white", "white", "white"],
    )
    assert _first_change(tmp_path / "missing-action", search_start=4) == "1"


def test_timing_cluster_ignores_isolated_late_codec_change(tmp_path: Path) -> None:
    colors = ["black"] * 3 + ["white"] * 117 + ["gray"] * 3
    frames = tmp_path / "late-codec-change"
    _make_frames(frames, colors)

    assert _timing_bounds(frames, cluster_gap=30) == ("4", "4")


def test_selector_timing_floor_uses_recorded_per_side_action_onsets() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "capture_action_onset" in script
    assert 'VIDEO_COMPARE_TARGET_TIMING_CLUSTER_GAP_FRAMES="30"' in script
    assert 'REF_ACTION_ONSET_SECONDS=$(cat "$OUT_DIR/ref-video/action-onset-seconds.txt"' in script
    assert 'IMPL_ACTION_ONSET_SECONDS=$(cat "$OUT_DIR/impl-video/action-onset-seconds.txt"' in script
    assert 'int((seconds + 0) * (fps + 0)) + 1' in script
    assert 'TIMING_REF_FRAMES="$OUT_DIR/ref-delta-timing-frames"' in script
    assert 'TIMING_IMPL_FRAMES="$OUT_DIR/impl-delta-timing-frames"' in script
    assert 'analyze_timing "$TIMING_REF_FRAMES" "Original" "$REF_TIMING_SEARCH_START"' in script
    assert 'analyze_timing "$TIMING_IMPL_FRAMES" "Implementation" "$IMPL_TIMING_SEARCH_START"' in script


def test_analyze_timing_is_pipefail_safe_for_long_frame_paths(tmp_path: Path) -> None:
    frames = tmp_path / ("long-frame-directory-" + ("x" * 80))
    _make_frames(frames, ["gray"])
    first = frames / "f-000001.png"
    for index in range(2, 61):
        shutil.copyfile(first, frames / f"f-{index:06d}.png")

    proc = subprocess.run(
        [
            "bash",
            "-c",
            f'set -euo pipefail; source "{LIB}"; analyze_timing "{frames}" t',
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (frames / ".first-change").read_text().strip() == "1"
