from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ui_clone.replay_track import build_recording_manifest, track_sha256

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "replay-track-compare.py"


def _valid_track() -> dict[str, object]:
    samples: list[dict[str, object]] = []
    for index in range(21):
        phase = min(max(index - 7, 0), 6) / 6
        height = 80.0 - (20.0 * phase)
        samples.append(
            {
                "index": index,
                "progress": index / 20,
                "scrollY": index * 50,
                "properties": {
                    "transform": {"translateX": 0.0, "translateY": -40.0 * phase},
                    "opacity": 1.0 - (0.2 * phase),
                    "clipPath": "inset(0px 0px 0px 0px)",
                    "backgroundColor": [255, 255, 255, 1.0],
                    "height": height,
                    "position": "sticky" if index >= 8 else "relative",
                },
                "box": {
                    "x": 0.0,
                    "y": 0.0,
                    "width": 1440.0,
                    "height": height,
                },
                "settle": {"status": "settled", "frames": 2},
            }
        )
    track: dict[str, object] = {
        "schemaVersion": 1,
        "trackId": "hero-zoom",
        "trigger": {
            "type": "scroll-progress",
            "sampleDenominator": 20,
            "startPx": 0.0,
            "endPx": 1000.0,
        },
        "node": {
            "selector": ".hero",
            "fingerprint": {"role": "region", "text": "Hero", "path": "main/section"},
        },
        "samples": samples,
        "baseline": {"recording": 1, "trackSha256": "0" * 64},
    }
    baseline = track["baseline"]
    assert isinstance(baseline, dict)
    baseline["trackSha256"] = track_sha256(track)
    return track


def _valid_lenis_wheel_track() -> dict[str, object]:
    track = _valid_track()
    trigger = track["trigger"]
    assert isinstance(trigger, dict)
    trigger["transport"] = "lenis-wheel"
    baseline = track["baseline"]
    assert isinstance(baseline, dict)
    baseline["trackSha256"] = track_sha256(track)
    return track


def _with_ready_wait_ms(track: dict[str, object], ready_wait_ms: int) -> dict[str, object]:
    trigger = track["trigger"]
    assert isinstance(trigger, dict)
    trigger["readyWaitMs"] = ready_wait_ms
    baseline = track["baseline"]
    assert isinstance(baseline, dict)
    baseline["trackSha256"] = track_sha256(track)
    return track


def _valid_scroll_action_track() -> dict[str, object]:
    denominator = 640
    samples: list[dict[str, object]] = []
    for index in range(21):
        phase = index / 20
        height = 80.0 - (20.0 * phase)
        samples.append(
            {
                "index": index,
                "elapsedMs": round(index * denominator / 20, 3),
                "scrollY": 100 if index == 0 else 200 + index,
                "properties": {
                    "transform": {"translateX": 0.0, "translateY": -80.0 * phase},
                    "opacity": 1.0 - (0.4 * phase),
                    "clipPath": "inset(0px 0px 0px 0px)",
                    "backgroundColor": [255, 255, 255, 1.0],
                    "height": height,
                    "position": "sticky",
                },
                "box": {
                    "x": 0.0,
                    "y": 0.0,
                    "width": 1440.0,
                    "height": height,
                },
                "settle": {
                    "status": "paused" if 1 <= index <= 19 else "settled",
                    "frames": 2,
                },
            }
        )
    track: dict[str, object] = {
        "schemaVersion": 1,
        "trackId": "hero-virtual",
        "trigger": {
            "type": "scroll-action",
            "action": "scrollTo",
            "driver": "virtual-clock",
            "fromScrollY": 100,
            "toScrollY": 700,
            "denominatorMs": denominator,
            "clock": {"epochMs": 1700000000000, "anchorMs": 1700000000320},
        },
        "node": {
            "selector": ".hero",
            "fingerprint": {"role": "region", "text": "Hero", "path": "main/section"},
        },
        "samples": samples,
        "baseline": {"recording": 1, "trackSha256": "0" * 64},
    }
    baseline = track["baseline"]
    assert isinstance(baseline, dict)
    baseline["trackSha256"] = track_sha256(track)
    return track


def _write_ref_track(
    ref: Path,
    *,
    track: dict[str, object] | None = None,
    corrupt_manifest: bool = False,
    regions_shape: str = "regions",
) -> Path:
    track_path = ref / "clip" / "ref" / "hero-replay-track.json"
    manifest_path = ref / "clip" / "ref" / "hero-replay-track.manifest.json"
    track_path.parent.mkdir(parents=True)
    track_path.write_text(json.dumps(track or _valid_track()), encoding="utf-8")
    manifest = build_recording_manifest(
        ROOT,
        [track_path.relative_to(ROOT).as_posix()],
        browser_version="Chromium/test",
        tool_version="playwright-core/test",
    )
    if corrupt_manifest:
        manifest["files"][0]["sha256"] = "f" * 64  # type: ignore[index]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    region = {
        "name": "hero",
        "triggerType": "scroll-driven",
        "artifacts": {
            "replayTrack": "clip/ref/hero-replay-track.json",
            "replayTrackManifest": "clip/ref/hero-replay-track.manifest.json",
        },
    }
    regions_payload = {"scroll": [region], "hover": []} if regions_shape == "top-level-scroll" else {"regions": [region]}
    (ref / "regions.json").write_text(json.dumps(regions_payload), encoding="utf-8")
    return track_path


def _write_regions_with_replay_key_but_no_usable_track(ref: Path) -> None:
    (ref / "regions.json").write_text(
        json.dumps(
            {
                "scroll": [
                    {
                        "name": "hero",
                        "triggerType": "scroll-driven",
                        "artifacts": {
                            "replayTrack": "clip/ref/missing-track.json",
                            "replayTrackManifest": "clip/ref/missing-track.manifest.json",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _write_fake_capture(tmp_root: Path, candidate: dict[str, object]) -> Path:
    candidate_json = tmp_root / "candidate.json"
    candidate_json.write_text(json.dumps(candidate), encoding="utf-8")
    wrapper = tmp_root / "fake-capture.py"
    wrapper.write_text(
        "from __future__ import annotations\n"
        "import json\n"
        "import os\n"
        "import shutil\n"
        "import sys\n"
        "from pathlib import Path\n"
        "from ui_clone.replay_track import build_recording_manifest\n"
        "Path(os.environ['FAKE_CAPTURE_LOG']).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
        "out = Path(sys.argv[3])\n"
        "out.parent.mkdir(parents=True, exist_ok=True)\n"
        "shutil.copyfile(os.environ['FAKE_CANDIDATE'], out)\n"
        "manifest = out.with_name(out.stem + '.manifest.json')\n"
        "if os.environ.get('FAKE_INVALID_CANDIDATE_MANIFEST') == '1':\n"
        "    manifest.write_text(json.dumps({'schemaVersion': 1, 'fake': True}), encoding='utf-8')\n"
        "else:\n"
        "    payload = build_recording_manifest(\n"
        "        Path(os.environ['FAKE_REPO_ROOT']),\n"
        "        [out.relative_to(Path(os.environ['FAKE_REPO_ROOT'])).as_posix()],\n"
        "        browser_version='Chromium/test',\n"
        "        tool_version='playwright-core/test',\n"
        "    )\n"
        "    manifest.write_text(json.dumps(payload), encoding='utf-8')\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper


def _run_compare(ref: Path, wrapper: Path, log_path: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["UI_CLONE_REPLAY_TRACK_CAPTURE"] = str(wrapper)
    env["FAKE_CAPTURE_LOG"] = str(log_path)
    env["FAKE_CANDIDATE"] = str(wrapper.parent / "candidate.json")
    env["FAKE_REPO_ROOT"] = str(ROOT)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "http://127.0.0.1:3000", str(ref)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_replay_track_compare_captures_declared_track_and_writes_aggregate() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as tmp_name:
        tmp_root = Path(tmp_name)
        ref = tmp_root / "ref"
        ref.mkdir()
        reference_path = _write_ref_track(ref)
        candidate = json.loads(reference_path.read_text(encoding="utf-8"))
        wrapper = _write_fake_capture(tmp_root, candidate)
        log_path = tmp_root / "fake-capture.log"

        proc = _run_compare(ref, wrapper, log_path)

        assert proc.returncode == 0, proc.stdout + proc.stderr
        report = json.loads((ref / "transitions" / "replay-track-compare.json").read_text())
        assert report["status"] == "pass"
        assert report["matchedPairs"] == 147
        assert report["totalPairs"] == 147
        assert report["failures"] == []
        assert json.loads(log_path.read_text(encoding="utf-8"))[0] == "http://127.0.0.1:3000"
        assert (ref / "transitions" / "replay-tracks" / "impl" / "hero-replay-track.json").is_file()
        assert (ref / "transitions" / "replay-tracks" / "impl" / "hero-replay-track.manifest.json").is_file()


def test_replay_track_compare_does_not_reuse_stale_candidate() -> None:
    """A wrapper that exits 0 without writing must not have the previous run's
    candidate track read as this run's fresh capture."""
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as tmp_name:
        tmp_root = Path(tmp_name)
        ref = tmp_root / "ref"
        ref.mkdir()
        reference_path = _write_ref_track(ref)
        candidate = json.loads(reference_path.read_text(encoding="utf-8"))
        wrapper = _write_fake_capture(tmp_root, candidate)
        log_path = tmp_root / "fake-capture.log"

        # First run writes a passing candidate.
        first = _run_compare(ref, wrapper, log_path)
        assert first.returncode == 0, first.stdout + first.stderr
        impl_track = ref / "transitions" / "replay-tracks" / "impl" / "hero-replay-track.json"
        assert impl_track.is_file()

        # Second run uses a wrapper that succeeds but writes nothing.
        silent = tmp_root / "silent-capture.py"
        silent.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        silent.chmod(0o755)

        second = _run_compare(ref, silent, log_path)

        assert second.returncode != 0, second.stdout
        report = json.loads((ref / "transitions" / "replay-track-compare.json").read_text())
        assert report["status"] == "fail", report
        assert not impl_track.is_file(), "stale candidate track must be cleared"


def test_replay_track_compare_derives_scroll_action_capture_args_from_reference_trigger() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as tmp_name:
        tmp_root = Path(tmp_name)
        ref = tmp_root / "ref"
        ref.mkdir()
        reference_path = _write_ref_track(ref, track=_valid_scroll_action_track())
        candidate = json.loads(reference_path.read_text(encoding="utf-8"))
        wrapper = _write_fake_capture(tmp_root, candidate)
        log_path = tmp_root / "fake-capture.log"

        proc = _run_compare(ref, wrapper, log_path)

        assert proc.returncode == 0, proc.stdout + proc.stderr
        args = json.loads(log_path.read_text(encoding="utf-8"))
        assert args[:5] == [
            "http://127.0.0.1:3000",
            ".hero",
            str(ref / "transitions" / "replay-tracks" / "impl" / "hero-replay-track.json"),
            "100",
            "700",
        ]
        assert "--mode" in args
        assert args[args.index("--mode") + 1] == "scroll-action"
        assert "--driver" in args
        assert args[args.index("--driver") + 1] == "virtual-clock"
        assert "--denominator-ms" in args
        assert args[args.index("--denominator-ms") + 1] == "640"
        assert "--anchor-ms" in args
        assert args[args.index("--anchor-ms") + 1] == "1700000000320"


def test_replay_track_compare_propagates_lenis_wheel_transport_arg() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as tmp_name:
        tmp_root = Path(tmp_name)
        ref = tmp_root / "ref"
        ref.mkdir()
        reference_path = _write_ref_track(ref, track=_valid_lenis_wheel_track())
        candidate = json.loads(reference_path.read_text(encoding="utf-8"))
        wrapper = _write_fake_capture(tmp_root, candidate)
        log_path = tmp_root / "fake-capture.log"

        proc = _run_compare(ref, wrapper, log_path)

        assert proc.returncode == 0, proc.stdout + proc.stderr
        args = json.loads(log_path.read_text(encoding="utf-8"))
        assert "--mode" in args
        assert args[args.index("--mode") + 1] == "scroll-progress"
        assert "--transport" in args
        assert args[args.index("--transport") + 1] == "lenis-wheel"


def test_replay_track_compare_propagates_ready_wait_arg_for_scroll_progress() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as tmp_name:
        tmp_root = Path(tmp_name)
        ref = tmp_root / "ref"
        ref.mkdir()
        reference_path = _write_ref_track(ref, track=_with_ready_wait_ms(_valid_track(), 250))
        candidate = json.loads(reference_path.read_text(encoding="utf-8"))
        wrapper = _write_fake_capture(tmp_root, candidate)
        log_path = tmp_root / "fake-capture.log"

        proc = _run_compare(ref, wrapper, log_path)

        assert proc.returncode == 0, proc.stdout + proc.stderr
        args = json.loads(log_path.read_text(encoding="utf-8"))
        assert "--ready-wait-ms" in args
        assert args[args.index("--ready-wait-ms") + 1] == "250"


def test_replay_track_compare_propagates_ready_wait_arg_for_scroll_action() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as tmp_name:
        tmp_root = Path(tmp_name)
        ref = tmp_root / "ref"
        ref.mkdir()
        reference_path = _write_ref_track(ref, track=_with_ready_wait_ms(_valid_scroll_action_track(), 320))
        candidate = json.loads(reference_path.read_text(encoding="utf-8"))
        wrapper = _write_fake_capture(tmp_root, candidate)
        log_path = tmp_root / "fake-capture.log"

        proc = _run_compare(ref, wrapper, log_path)

        assert proc.returncode == 0, proc.stdout + proc.stderr
        args = json.loads(log_path.read_text(encoding="utf-8"))
        assert "--ready-wait-ms" in args
        assert args[args.index("--ready-wait-ms") + 1] == "320"


def test_replay_track_compare_fails_when_candidate_omits_lenis_wheel_transport() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as tmp_name:
        tmp_root = Path(tmp_name)
        ref = tmp_root / "ref"
        ref.mkdir()
        reference_path = _write_ref_track(ref, track=_valid_lenis_wheel_track())
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
        candidate = copy.deepcopy(reference)
        trigger = candidate["trigger"]
        assert isinstance(trigger, dict)
        trigger.pop("transport")
        wrapper = _write_fake_capture(tmp_root, candidate)
        log_path = tmp_root / "fake-capture.log"

        proc = _run_compare(ref, wrapper, log_path)

        assert proc.returncode == 1
        report = json.loads((ref / "transitions" / "replay-track-compare.json").read_text())
        assert report["status"] == "fail"
        assert any("trigger must match" in failure for failure in report["failures"])


def test_replay_track_compare_fails_when_candidate_uses_wrong_transport() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as tmp_name:
        tmp_root = Path(tmp_name)
        ref = tmp_root / "ref"
        ref.mkdir()
        reference_path = _write_ref_track(ref, track=_valid_lenis_wheel_track())
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
        candidate = copy.deepcopy(reference)
        trigger = candidate["trigger"]
        assert isinstance(trigger, dict)
        trigger["transport"] = "native"
        wrapper = _write_fake_capture(tmp_root, candidate)
        log_path = tmp_root / "fake-capture.log"

        proc = _run_compare(ref, wrapper, log_path)

        assert proc.returncode == 1
        report = json.loads((ref / "transitions" / "replay-track-compare.json").read_text())
        assert report["status"] == "fail"
        assert any("trigger.transport must be lenis-wheel" in failure for failure in report["failures"])


def test_replay_track_compare_collects_top_level_scroll_schema() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as tmp_name:
        tmp_root = Path(tmp_name)
        ref = tmp_root / "ref"
        ref.mkdir()
        reference_path = _write_ref_track(ref, regions_shape="top-level-scroll")
        candidate = json.loads(reference_path.read_text(encoding="utf-8"))
        wrapper = _write_fake_capture(tmp_root, candidate)
        log_path = tmp_root / "fake-capture.log"

        proc = _run_compare(ref, wrapper, log_path)

        assert proc.returncode == 0, proc.stdout + proc.stderr
        report = json.loads((ref / "transitions" / "replay-track-compare.json").read_text())
        assert report["status"] == "pass"
        assert report["totalPairs"] == 147
        assert log_path.exists()


def test_replay_track_compare_fails_when_replay_keys_yield_zero_usable_tracks() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as tmp_name:
        tmp_root = Path(tmp_name)
        ref = tmp_root / "ref"
        ref.mkdir()
        _write_regions_with_replay_key_but_no_usable_track(ref)
        wrapper = _write_fake_capture(tmp_root, _valid_track())
        log_path = tmp_root / "fake-capture.log"

        proc = _run_compare(ref, wrapper, log_path)

        assert proc.returncode == 1
        report = json.loads((ref / "transitions" / "replay-track-compare.json").read_text())
        assert report["status"] == "fail"
        assert report["matchedPairs"] == 0
        assert report["totalPairs"] >= 1
        assert any("zero usable replay tracks" in failure for failure in report["failures"])
        assert not log_path.exists()


def test_replay_track_compare_verifies_candidate_manifest_before_compare() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as tmp_name:
        tmp_root = Path(tmp_name)
        ref = tmp_root / "ref"
        ref.mkdir()
        reference_path = _write_ref_track(ref)
        candidate = json.loads(reference_path.read_text(encoding="utf-8"))
        wrapper = _write_fake_capture(tmp_root, candidate)
        log_path = tmp_root / "fake-capture.log"

        env = os.environ.copy()
        env["UI_CLONE_REPLAY_TRACK_CAPTURE"] = str(wrapper)
        env["FAKE_CAPTURE_LOG"] = str(log_path)
        env["FAKE_CANDIDATE"] = str(wrapper.parent / "candidate.json")
        env["FAKE_REPO_ROOT"] = str(ROOT)
        env["FAKE_INVALID_CANDIDATE_MANIFEST"] = "1"
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "http://127.0.0.1:3000", str(ref)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert proc.returncode == 1
        report = json.loads((ref / "transitions" / "replay-track-compare.json").read_text())
        assert report["status"] == "fail"
        assert report["matchedPairs"] == 0
        assert report["totalPairs"] == 1
        assert any("candidate manifest" in failure for failure in report["failures"])


def test_replay_track_compare_refuses_manifest_mismatch_before_capture() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as tmp_name:
        tmp_root = Path(tmp_name)
        ref = tmp_root / "ref"
        ref.mkdir()
        reference_path = _write_ref_track(ref, corrupt_manifest=True)
        candidate = copy.deepcopy(json.loads(reference_path.read_text(encoding="utf-8")))
        wrapper = _write_fake_capture(tmp_root, candidate)
        log_path = tmp_root / "fake-capture.log"

        proc = _run_compare(ref, wrapper, log_path)

        assert proc.returncode == 1
        report = json.loads((ref / "transitions" / "replay-track-compare.json").read_text())
        assert report["status"] == "fail"
        assert report["matchedPairs"] == 0
        assert report["totalPairs"] == 1
        assert any("manifest" in failure and "sha256" in failure for failure in report["failures"])
        assert not log_path.exists()
