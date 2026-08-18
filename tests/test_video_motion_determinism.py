"""Loop-10 video-motion determinism fixes (item 8).

Five identical-impl runs produced FAIL(120)/FAIL(79)/vacuous-PASS/FAIL(96)/
FAIL(54) plus one dispatcher truncation to 0 rows. Three documented modes:

  (a) `ffmpeg ... 2>/dev/null` swallowed extraction failures and the verdict
      silently ran on the PREVIOUS run's frames — extraction is now wiped-
      before, fail-hard, stderr-surfaced, and frame dirs are fingerprinted
      to their source recording.
  (b) recording-window luck — a tail-only capture produced a vacuous
      all-PASS; splash verdicts now require a sane captured window (>= half
      the expected frames, first change within the first two thirds) and the
      recording is anchored on a navigation-complete event, else the run is
      an UNMEASURABLE hard error (exit 2), never a verdict.
  (c) truncation to 0 measurement rows must be a hard error — in-process
      (0 rows after the SSIM loop → exit 2) and at the consumer (the result
      file carries a completion sentinel; the rollup rejects files without it).

UI_CLONE_VMC_SKIP_RECORD=1 drives extraction/compare against pre-seeded
recordings so these behaviors are testable without a browser.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPARE = ROOT / "scripts" / "verify" / "video-transition-compare.sh"
WRAPPER = ROOT / "skills" / "visual-debug" / "scripts" / "video-motion-compare.sh"
ROLLUP = ROOT / "skills" / "visual-debug" / "scripts" / "transition-proof-rollup.sh"
ROLLUP_DRIVER = ROLLUP.with_name("transition_proof_rollup.py")

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("magick") is None,
    reason="ffmpeg/imagemagick not installed",
)


def _make_video(path: Path, *, seconds: float = 2.0, color_shift: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # testsrc gives per-frame visual change (a real "arc"); smptebars is static.
    src = "testsrc=size=320x240:rate=30" if color_shift else "smptebars=size=320x240:rate=30"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"{src}:duration={seconds}",
         "-c:v", "libvpx", "-b:v", "200k", str(path)],
        capture_output=True, text=True, timeout=120, check=True,
    )


def _run_compare(out_dir: Path, *, action: str = "splash",
                 env_extra: dict | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, UI_CLONE_VMC_SKIP_RECORD="1",
               RECORD_DURATION="2", FPS="30")
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", str(COMPARE), "test-vmc", "http://localhost:9/ref",
         "http://localhost:9/impl", str(out_dir), action],
        capture_output=True, text=True, timeout=300, env=env,
    )


def test_video_motion_rejects_concurrent_writers_for_one_ref(tmp_path: Path) -> None:
    """Two recorders must never overwrite the same raw videos and frames."""
    fake_root = tmp_path / "project"
    lib = fake_root / "scripts" / "verify" / "lib"
    lib.mkdir(parents=True)
    (lib / "position-compare.sh").write_text(
        "dynamic_selectors_from_spec() { printf '%s' ''; }\n",
        encoding="utf-8",
    )
    lock_lib = fake_root / "scripts" / "lib"
    lock_lib.mkdir()
    shutil.copy(ROOT / "scripts" / "lib" / "exclusive-lock.sh", lock_lib)
    compare = fake_root / "scripts" / "verify" / "video-transition-compare.sh"
    compare.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'touch "${HOLD_STARTED:?}"\n'
        'while [ ! -f "${HOLD_RELEASE:?}" ]; do sleep 0.05; done\n'
        "printf '%s\\n' 'Pass: 1, Fail: 0'\n",
        encoding="utf-8",
    )
    compare.chmod(0o755)

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(
        '{"signals":{"hasSplash":true}}\n',
        encoding="utf-8",
    )
    started = tmp_path / "started"
    release = tmp_path / "release"
    command = [
        "bash",
        str(WRAPPER),
        "https://example.test",
        "http://127.0.0.1:1",
        "video-lock-test",
        str(ref),
    ]
    env = os.environ.copy()
    env.update(
        {
            "PLUGIN_ROOT": str(fake_root),
            "PRE_FILTER": "0",
            "HOLD_STARTED": str(started),
            "HOLD_RELEASE": str(release),
        }
    )
    first = subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not started.exists() and first.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert started.exists(), first.communicate(timeout=2)

        second = subprocess.run(
            command,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert second.returncode == 2, second.stdout + second.stderr
        assert "already running for motion artifact dir" in second.stderr

        release.touch()
        stdout, stderr = first.communicate(timeout=20)
        assert first.returncode == 0, stdout + stderr
        assert not (ref / "transitions" / "video-motion" / ".compare.lock").exists()
    finally:
        release.touch(exist_ok=True)
        if first.poll() is None:
            first.terminate()
            first.communicate(timeout=10)


def test_direct_video_compare_rejects_an_active_output_writer(tmp_path: Path) -> None:
    out = tmp_path / "out"
    lock = out / ".transition-compare.lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    (lock / "token").write_text("existing-owner\n", encoding="utf-8")
    (lock / "session").write_text("existing-session\n", encoding="utf-8")

    proc = _run_compare(out)

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "already running for transition artifact dir" in proc.stderr
    assert (lock / "token").read_text(encoding="utf-8").strip() == "existing-owner"


@needs_ffmpeg
def test_corrupted_recording_fails_loudly_and_wipes_stale_frames(tmp_path: Path) -> None:
    """(a): a garbage recording must be a HARD error with ffmpeg stderr
    surfaced — and the previous run's frames must be gone, so a verdict can
    never run on stale frames."""
    out = tmp_path / "out"
    (out / "ref-video").mkdir(parents=True)
    (out / "impl-video").mkdir(parents=True)
    (out / "ref-frames").mkdir()
    (out / "impl-frames").mkdir()
    (out / "diff-frames").mkdir()
    (out / "ref-video" / "raw.webm").write_bytes(b"not a video at all")
    (out / "impl-video" / "raw.webm").write_bytes(b"also garbage")
    stale = out / "ref-frames" / "f-000001.png"
    stale.write_bytes(b"\x89PNG stale frame from a previous run")

    proc = _run_compare(out)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "ffmpeg" in (proc.stdout + proc.stderr).lower()
    assert not stale.exists(), "stale frames must be wiped before extraction"


@needs_ffmpeg
def test_identical_videos_pass_deterministically(tmp_path: Path) -> None:
    """Self-compare must be a deterministic PASS with measurement rows —
    run twice, same verdict."""
    out = tmp_path / "out"
    _make_video(out / "ref-video" / "raw.webm")
    shutil.copy(out / "ref-video" / "raw.webm", _mk(out, "impl-video") / "raw.webm")
    codes = []
    for _ in range(2):
        proc = _run_compare(out)
        codes.append(proc.returncode)
        assert "Pass:" in proc.stdout, proc.stdout[-2000:]
    assert codes == [0, 0], codes


def _mk(base: Path, name: str) -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    return d


@needs_ffmpeg
def test_truncated_window_is_unmeasurable_not_pass(tmp_path: Path) -> None:
    """(b): a tail-only/truncated capture (far fewer frames than the
    expected window) is exit 2 UNMEASURABLE — the loop-10 run-5 vacuous
    all-PASS class."""
    out = tmp_path / "out"
    _make_video(out / "ref-video" / "raw.webm", seconds=0.4)
    _make_video(out / "impl-video" / "raw.webm", seconds=0.4)
    proc = _run_compare(out)  # RECORD_DURATION=2 @30fps → expects >=30 frames
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "UNMEASURABLE" in proc.stdout


def test_extraction_has_no_silenced_ffmpeg() -> None:
    """(a) lock: the frame-extraction ffmpeg calls must not silence stderr."""
    text = COMPARE.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "-vf" in line and "fps=" in line and "ffmpeg" in line:
            assert "2>/dev/null" not in line, line
    assert "_extract_frames" in text
    assert ".fingerprint" in text


def test_wrapper_writes_completion_sentinel_on_all_paths() -> None:
    """(c) lock: every result-writing path stamps the completion sentinel,
    and the rollup rejects results without it."""
    wrapper = WRAPPER.read_text(encoding="utf-8")
    assert wrapper.count("# video-motion-compare: COMPLETE") >= 3
    rollup_wrapper = ROLLUP.read_text(encoding="utf-8")
    assert 'python3 "$SCRIPTS_DIR/transition_proof_rollup.py"' in rollup_wrapper
    rollup_driver = ROLLUP_DRIVER.read_text(encoding="utf-8")
    assert "missing completion sentinel" in rollup_driver


def test_zero_measurement_rows_guard_present() -> None:
    """(c) lock: a comparison loop that measured nothing must hard-error."""
    text = COMPARE.read_text(encoding="utf-8")
    assert "0 measurement rows" in text
    assert "did not actually run" in text


def test_rollup_rejects_truncated_result_without_sentinel(tmp_path: Path) -> None:
    """(c): a result file truncated mid-run (dispatcher timeout/kill) lacks
    the completion sentinel — the rollup must reject it even when surviving
    markers look passable."""
    import json

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
            {"selector": ".hero",
             "samples": [{"scrollY": 0, "opacity": "0"}, {"scrollY": 800, "opacity": "1"}]},
        ],
    }))
    transitions = ref / "transitions"
    transitions.mkdir()
    # looks passing, but no completion sentinel — truncated run
    (transitions / "video-motion-result.txt").write_text(
        "Pass: 100\nFail: 0\n", encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(ROLLUP), str(ref)], capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "transition-proof.json").read_text())
    assert any("sentinel" in r for r in artifact["reasons"]), artifact["reasons"]


def test_anchor_timeout_is_attempt_failure() -> None:
    """Review-2 finding 5 lock: a side that never reaches the navigation
    anchor must fail the attempt (retry → unmeasurable exit 2), never
    continue into a silently shifted recording window. (Behavioral fixture:
    a never-completing page exits 2 after 3 attempts when run live.)"""
    text = COMPARE.read_text(encoding="utf-8")
    assert "never reached the navigation anchor" in text
    # the timeout path returns failure, not success
    idx = text.index("navigation anchor timed out")
    window = text[idx: idx + 200]
    assert "return 1" in window, window
    assert "window may be late-shifted" not in text


def test_signals_read_without_jq_dependency() -> None:
    """F13: reading the plan signals must NOT be gated on jq being installed.
    Previously `if [ -f PLAN ] && command -v jq` meant a host without jq left every
    signal 'false' -> RUN_COUNT 0 -> 'no motion signals — skipped' + exit 0 even
    when the plan declared scroll/splash: a silent clean pass for unmeasured motion.
    Signals are now parsed with python (already a hard dependency)."""
    txt = WRAPPER.read_text(encoding="utf-8")
    assert "jq -r '.signals" not in txt, (
        "signal reading must not depend on jq (F13 silent-skip on jq-less hosts)"
    )
    assert ".get(\"signals\")" in txt or "hasScrollScrub" in txt, (
        "signals must be parsed (via python) from the plan"
    )


def test_wrapper_skips_full_frame_scroll_only_for_target_local_state_machines() -> None:
    """Realfood class: verification-plan may say hasScrollScrub=true even when
    the transition-spec only declares target-local scroll state machines. After
    trajectory passes, full-frame scroll SSIM is redundant and noisy; the wrapper
    may skip it only when every scroll-state-machine target is a dispatchOnly
    region from regions.json."""
    txt = WRAPPER.read_text(encoding="utf-8")
    assert "target_local_scroll_state_only_mode()" in txt
    helper = txt.split("target_local_scroll_state_only_mode()", 1)[1].split(
        "TARGET_LOCAL_SCROLL_STATE_ONLY", 1
    )[0]
    assert 'trigger == "scroll-state-machine"' in helper
    assert 'animation_type == "scroll-state-machine"' in helper
    assert 'transition.get("dynamic") is True' in helper
    assert 'region.get("dispatchOnly") is True' in helper
    assert "all(target in dispatch_selectors" in helper
    dispatch = txt.split('if [ "$HAS_SCROLL" = "true" ] || [ "$HAS_IO" = "true" ]; then', 1)[1]
    assert '[ "$TARGET_LOCAL_SCROLL_STATE_ONLY" = "true" ]' in dispatch
    assert '[ "$TRAJECTORY_PREFILTER_PASSED" = "1" ]' in dispatch
    assert "target-local scroll state-machine trajectory passed" in dispatch
    assert 'run_mode scroll "Scroll-driven motion"' in dispatch


def test_wrapper_target_local_scroll_skip_stays_fail_closed_for_ambiguous_motion() -> None:
    txt = WRAPPER.read_text(encoding="utf-8")
    helper = txt.split("target_local_scroll_state_only_mode()", 1)[1].split(
        "TARGET_LOCAL_SCROLL_STATE_ONLY", 1
    )[0]
    assert "has_io" in helper and 'print("false")' in helper
    assert "mentions_scroll and not is_scroll_state" in helper
    assert "not dispatch_selectors" in helper
    assert "not scroll_state_targets" in helper
    assert 'measurement not in {' in helper


def _write_wrapper_motion_ref(
    ref: Path,
    *,
    dispatch_only: bool,
    dynamic: bool = False,
    trigger: str = "scroll-state-machine",
) -> None:
    ref.mkdir(parents=True)
    (ref / "transitions").mkdir()
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "signals": {
                    "hasScrollScrub": True,
                    "hasSplash": False,
                    "hasIOReveal": False,
                }
            }
        )
    )
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "nav-scroll",
                        "trigger": trigger,
                        "target": ".nav",
                        "dynamic": dynamic,
                        "animation": {
                            "type": trigger,
                            "measurement": "target-and-descendants",
                        },
                    }
                ]
            }
        )
    )
    (ref / "regions.json").write_text(
        json.dumps({"regions": [{"selector": ".nav", "dispatchOnly": dispatch_only}]})
    )


def _write_fake_motion_scripts(tmp_path: Path) -> tuple[Path, Path]:
    traj = tmp_path / "fake-trajectory.sh"
    traj.write_text(
        "#!/usr/bin/env bash\n"
        "echo '# transition-trajectory-compare'\n"
        "echo '✅ all 1/1 declared target trajectory samples within thresholds'\n"
        "exit 0\n"
    )
    compare = tmp_path / "fake-compare.sh"
    compare.write_text(
        "#!/usr/bin/env bash\n"
        "echo called >> \"$UI_CLONE_FAKE_COMPARE_CALLS\"\n"
        "echo 'Total frames compared: 1'\n"
        "echo 'Pass: 1, Fail: 0'\n"
        "exit 0\n"
    )
    traj.chmod(0o755)
    compare.chmod(0o755)
    return traj, compare


def test_wrapper_behavior_skips_compare_for_dispatch_only_scroll_state(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    _write_wrapper_motion_ref(ref, dispatch_only=True)
    traj, compare = _write_fake_motion_scripts(tmp_path)
    calls = tmp_path / "compare.calls"
    env = dict(
        os.environ,
        UI_CLONE_VMC_TRAJECTORY_SCRIPT=str(traj),
        UI_CLONE_VMC_COMPARE_SCRIPT=str(compare),
        UI_CLONE_FAKE_COMPARE_CALLS=str(calls),
    )
    proc = subprocess.run(
        ["bash", str(WRAPPER), "https://example.test", "http://impl.test", "stub", str(ref)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = (ref / "transitions" / "video-motion-result.txt").read_text()
    assert "target-local scroll state-machine trajectory passed" in result
    assert not calls.exists(), "full-frame compare must not run for dispatchOnly target-local state"


def test_wrapper_behavior_runs_compare_for_non_dispatch_scroll_state(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    _write_wrapper_motion_ref(ref, dispatch_only=False)
    traj, compare = _write_fake_motion_scripts(tmp_path)
    calls = tmp_path / "compare.calls"
    env = dict(
        os.environ,
        UI_CLONE_VMC_TRAJECTORY_SCRIPT=str(traj),
        UI_CLONE_VMC_COMPARE_SCRIPT=str(compare),
        UI_CLONE_FAKE_COMPARE_CALLS=str(calls),
    )
    proc = subprocess.run(
        ["bash", str(WRAPPER), "https://example.test", "http://impl.test", "stub", str(ref)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls.read_text().strip() == "called"
