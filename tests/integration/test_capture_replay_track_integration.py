from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ui_clone.replay_track import compare_tracks, validate_track


def _run_recorder(
    repo_root: Path,
    url: str,
    out: Path,
    *,
    mode: str = "scroll-action",
    driver: str = "animation-pause",
    transport: str = "native",
    ready_wait_ms: int | None = None,
    denominator_ms: int | None = None,
    anchor_ms: int | None = None,
    selector: str | None = None,
) -> subprocess.CompletedProcess[str]:
    if selector is None:
        selector = (
            ".action-label"
            if "css-transition" in url
            else ".js-label"
            if "js-timed" in url
            else ".scroll-label"
        )
    command = [
        "node",
        str(repo_root / "scripts" / "extract" / "capture-replay-track.mjs"),
        "--url",
        url,
        "--selector",
        selector,
        "--out",
        str(out),
        "--start-px",
        "0",
        "--end-px",
        "200",
        "--mode",
        mode,
    ]
    if mode == "scroll-action":
        command.extend(["--driver", driver])
    if transport != "native":
        command.extend(["--transport", transport])
    if ready_wait_ms is not None:
        command.extend(["--ready-wait-ms", str(ready_wait_ms)])
    if denominator_ms is not None:
        command.extend(["--denominator-ms", str(denominator_ms)])
    if anchor_ms is not None:
        command.extend(["--anchor-ms", str(anchor_ms)])
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _supports_scroll_timeline(repo_root: Path) -> bool:
    proc = subprocess.run(
        [
            "node",
            "-e",
            """
const { chromium } = require('playwright-core');
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage();
    const supported = await page.evaluate(() =>
      CSS.supports('animation-timeline: scroll(root)') &&
      typeof ScrollTimeline === 'function'
    );
    process.stdout.write(supported ? 'yes\\n' : 'no\\n');
  } finally {
    await browser.close();
  }
})().catch((error) => {
  process.stderr.write(String(error && error.message ? error.message : error));
  process.exit(2);
});
            """,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode == 0 and proc.stdout.strip() == "yes"


def test_scroll_action_css_transition_is_deterministic(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    url = f"{http_server}replay-track-css-transition.html"
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"

    first = _run_recorder(repo_root, url, first_path)
    second = _run_recorder(repo_root, url, second_path)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    first_track = json.loads(first_path.read_text())
    second_track = json.loads(second_path.read_text())
    assert first_track["trigger"] == {
        "type": "scroll-action",
        "action": "scrollTo",
        "driver": "animation-pause",
        "fromScrollY": 0,
        "toScrollY": 200,
        "denominatorMs": 500,
    }
    assert second_track["trigger"] == first_track["trigger"]
    assert "progress" not in first_track["samples"][0]
    assert "sampleDenominator" not in first_track["samples"][0]
    assert [sample["index"] for sample in first_track["samples"]] == list(range(21))
    assert [sample["elapsedMs"] for sample in first_track["samples"]] == [
        index * 25 for index in range(21)
    ]
    assert [sample["scrollY"] for sample in first_track["samples"]] == [0] + [200] * 20
    assert [sample["settle"]["status"] for sample in first_track["samples"]] == [
        "settled",
        *["paused"] * 19,
        "settled",
    ]
    assert validate_track(first_track) == []
    assert validate_track(second_track) == []
    assert first_track == second_track
    assert compare_tracks(first_track, second_track, minimum_score=1.0)["status"] == "pass"


def test_scroll_action_waapi_animation_is_deterministic(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    url = f"{http_server}replay-track-waapi-action.html"
    first_path = tmp_path / "waapi-first.json"
    second_path = tmp_path / "waapi-second.json"

    first = _run_recorder(
        repo_root,
        url,
        first_path,
        selector=".waapi-label",
    )
    second = _run_recorder(
        repo_root,
        url,
        second_path,
        selector=".waapi-label",
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    first_track = json.loads(first_path.read_text())
    second_track = json.loads(second_path.read_text())
    assert first_track["trigger"] == {
        "type": "scroll-action",
        "action": "scrollTo",
        "driver": "animation-pause",
        "fromScrollY": 0,
        "toScrollY": 200,
        "denominatorMs": 640,
    }
    assert first_track["samples"][0]["properties"]["transform"]["translateY"] == 0
    assert first_track["samples"][-1]["properties"]["transform"]["translateY"] == 80
    assert validate_track(first_track) == []
    assert validate_track(second_track) == []
    assert first_track == second_track
    assert compare_tracks(first_track, second_track, minimum_score=1.0)["status"] == "pass"


def test_scroll_progress_allows_scroll_timeline_translation(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    if not _supports_scroll_timeline(repo_root):
        pytest.skip("browser lacks CSS ScrollTimeline support")

    url = f"{http_server}replay-track-scroll-timeline-translation.html"
    first_path = tmp_path / "scroll-timeline-first.json"
    second_path = tmp_path / "scroll-timeline-second.json"
    first = _run_recorder(
        repo_root,
        url,
        first_path,
        mode="scroll-progress",
        selector=".scroll-translation",
    )
    second = _run_recorder(
        repo_root,
        url,
        second_path,
        mode="scroll-progress",
        selector=".scroll-translation",
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    first_track = json.loads(first_path.read_text())
    second_track = json.loads(second_path.read_text())
    assert first_track["trigger"] == {
        "type": "scroll-progress",
        "startPx": 0,
        "endPx": 200,
        "sampleDenominator": 20,
    }
    assert first_track["samples"][0]["properties"]["transform"]["translateY"] == 0
    assert first_track["samples"][-1]["properties"]["transform"]["translateY"] == 80
    assert [sample["progress"] for sample in first_track["samples"]] == [
        index / 20 for index in range(21)
    ]
    assert validate_track(first_track) == []
    assert validate_track(second_track) == []
    assert first_track == second_track
    assert compare_tracks(first_track, second_track, minimum_score=1.0)["status"] == "pass"


def test_scroll_progress_lenis_wheel_transport_is_deterministic(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    url = f"{http_server}replay-track-lenis-wheel.html"
    first_path = tmp_path / "lenis-wheel-first.json"
    second_path = tmp_path / "lenis-wheel-second.json"
    first = _run_recorder(
        repo_root,
        url,
        first_path,
        mode="scroll-progress",
        transport="lenis-wheel",
        selector=".lenis-wheel-label",
    )
    second = _run_recorder(
        repo_root,
        url,
        second_path,
        mode="scroll-progress",
        transport="lenis-wheel",
        selector=".lenis-wheel-label",
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    first_summary = json.loads(first.stdout)
    assert first_summary["scrollTransport"] == "lenis"
    assert "scrollTo instance" in first_summary["scrollTransportReason"]
    first_track = json.loads(first_path.read_text())
    second_track = json.loads(second_path.read_text())
    assert first_track["trigger"] == {
        "type": "scroll-progress",
        "startPx": 0,
        "endPx": 200,
        "sampleDenominator": 20,
        "transport": "lenis-wheel",
    }
    assert [sample["scrollY"] for sample in first_track["samples"]] == [
        index * 10 for index in range(21)
    ]
    assert first_track["samples"][0]["properties"]["transform"]["translateY"] == 0
    assert first_track["samples"][-1]["properties"]["transform"]["translateY"] == 80
    assert validate_track(first_track) == []
    assert validate_track(second_track) == []
    assert first_track == second_track
    assert compare_tracks(first_track, second_track, minimum_score=1.0)["status"] == "pass"


def test_scroll_progress_lenis_marker_requires_lenis_wheel_transport(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    out_path = tmp_path / "lenis-no-transport.json"
    proc = _run_recorder(
        repo_root,
        f"{http_server}replay-track-lenis-wheel.html",
        out_path,
        mode="scroll-progress",
        selector=".lenis-wheel-label",
    )

    assert proc.returncode != 0
    assert "custom-scroll-transport-unsupported: lenis" in proc.stderr
    assert not out_path.exists()


def test_scroll_progress_lenis_wheel_transport_requires_lenis_detection(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    out_path = tmp_path / "native-with-lenis-wheel.json"
    proc = _run_recorder(
        repo_root,
        f"{http_server}replay-track-scroll-progress.html",
        out_path,
        mode="scroll-progress",
        transport="lenis-wheel",
        selector=".progress-box",
    )

    assert proc.returncode != 0
    assert "--transport lenis-wheel requires detected Lenis, got native" in proc.stderr
    assert not out_path.exists()


def test_scroll_progress_ready_wait_captures_post_delay_geometry(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    url = f"{http_server}replay-track-delayed-layout-ready.html"
    no_wait_path = tmp_path / "ready-no-wait.json"
    wait_first_path = tmp_path / "ready-wait-first.json"
    wait_second_path = tmp_path / "ready-wait-second.json"

    no_wait = _run_recorder(
        repo_root,
        url,
        no_wait_path,
        mode="scroll-progress",
        selector=".delayed-ready-box",
    )
    wait_first = _run_recorder(
        repo_root,
        url,
        wait_first_path,
        mode="scroll-progress",
        ready_wait_ms=500,
        selector=".delayed-ready-box",
    )
    wait_second = _run_recorder(
        repo_root,
        url,
        wait_second_path,
        mode="scroll-progress",
        ready_wait_ms=500,
        selector=".delayed-ready-box",
    )

    assert no_wait.returncode == 0, no_wait.stderr
    assert wait_first.returncode == 0, wait_first.stderr
    assert wait_second.returncode == 0, wait_second.stderr
    no_wait_track = json.loads(no_wait_path.read_text())
    wait_first_track = json.loads(wait_first_path.read_text())
    wait_second_track = json.loads(wait_second_path.read_text())
    assert "readyWaitMs" not in no_wait_track["trigger"]
    assert wait_first_track["trigger"]["readyWaitMs"] == 500
    assert wait_second_track["trigger"]["readyWaitMs"] == 500
    assert no_wait_track["samples"][0]["properties"]["height"] == 40
    assert no_wait_track["samples"][0]["properties"]["transform"]["translateY"] == 0
    assert wait_first_track["samples"][0]["properties"]["height"] == 90
    assert wait_first_track["samples"][0]["properties"]["transform"]["translateY"] == 20
    assert wait_first_track == wait_second_track


def test_scroll_action_virtual_clock_raf_spring_is_deterministic(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    url = f"{http_server}replay-track-virtual-raf-spring.html"
    first_path = tmp_path / "virtual-first.json"
    second_path = tmp_path / "virtual-second.json"

    first = _run_recorder(
        repo_root,
        url,
        first_path,
        driver="virtual-clock",
        denominator_ms=640,
        selector=".virtual-label",
    )
    second = _run_recorder(
        repo_root,
        url,
        second_path,
        driver="virtual-clock",
        denominator_ms=640,
        selector=".virtual-label",
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    first_track = json.loads(first_path.read_text())
    second_track = json.loads(second_path.read_text())
    assert first_track["trigger"] == {
        "type": "scroll-action",
        "action": "scrollTo",
        "driver": "virtual-clock",
        "fromScrollY": 0,
        "toScrollY": 200,
        "denominatorMs": 640,
        "clock": {
            "epochMs": 1700000000000,
            "anchorMs": 1700000060000,
        },
    }
    assert [sample["elapsedMs"] for sample in first_track["samples"]] == [
        index * 32 for index in range(21)
    ]
    assert [sample["scrollY"] for sample in first_track["samples"]] == [0] + [200] * 20
    assert [sample["settle"]["status"] for sample in first_track["samples"]] == [
        "settled",
        *["paused"] * 19,
        "settled",
    ]
    assert first_track["samples"][-1]["properties"]["height"] == 121
    assert validate_track(first_track) == []
    assert validate_track(second_track) == []
    assert first_track == second_track
    assert compare_tracks(first_track, second_track, minimum_score=1.0)["status"] == "pass"


def test_scroll_action_virtual_clock_layout_shift_is_deterministic(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    url = f"{http_server}replay-track-virtual-layout-shift.html"
    first_path = tmp_path / "layout-first.json"
    second_path = tmp_path / "layout-second.json"

    first = _run_recorder(
        repo_root,
        url,
        first_path,
        driver="virtual-clock",
        denominator_ms=640,
        selector=".layout-box",
    )
    second = _run_recorder(
        repo_root,
        url,
        second_path,
        driver="virtual-clock",
        denominator_ms=640,
        selector=".layout-box",
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    first_track = json.loads(first_path.read_text())
    second_track = json.loads(second_path.read_text())
    assert first_track["trigger"]["type"] == "scroll-action"
    assert first_track["trigger"]["driver"] == "virtual-clock"
    assert first_track["trigger"]["denominatorMs"] == 640
    assert first_track["samples"][0]["box"]["y"] != first_track["samples"][-1]["box"]["y"]
    assert first_track["samples"][0]["properties"]["height"] == 40
    assert first_track["samples"][-1]["properties"]["height"] == 80
    assert validate_track(first_track) == []
    assert validate_track(second_track) == []
    assert first_track == second_track


def test_scroll_action_virtual_clock_rejects_unaligned_anchor(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    proc = _run_recorder(
        repo_root,
        f"{http_server}replay-track-virtual-raf-spring.html",
        tmp_path / "bad-anchor.json",
        driver="virtual-clock",
        denominator_ms=640,
        anchor_ms=1700000060008,
        selector=".virtual-label",
    )

    assert proc.returncode != 0
    assert "--anchor-ms must be divisible by 16" in proc.stderr
    assert not (tmp_path / "bad-anchor.json").exists()


def test_scroll_action_virtual_clock_accepts_next_frame_anchor(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    default_path = tmp_path / "default-anchor.json"
    next_path = tmp_path / "next-anchor.json"
    url = f"{http_server}replay-track-virtual-raf-spring.html"

    default = _run_recorder(
        repo_root,
        url,
        default_path,
        driver="virtual-clock",
        denominator_ms=640,
        selector=".virtual-label",
    )
    next_frame = _run_recorder(
        repo_root,
        url,
        next_path,
        driver="virtual-clock",
        denominator_ms=640,
        anchor_ms=1700000060016,
        selector=".virtual-label",
    )

    assert default.returncode == 0, default.stderr
    assert next_frame.returncode == 0, next_frame.stderr
    default_track = json.loads(default_path.read_text())
    next_track = json.loads(next_path.read_text())
    assert default_track["trigger"]["clock"]["anchorMs"] == 1700000060000
    assert next_track["trigger"]["clock"]["anchorMs"] == 1700000060016
    assert validate_track(default_track) == []
    assert validate_track(next_track) == []


def test_scroll_action_virtual_clock_rejects_css_transition(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    proc = _run_recorder(
        repo_root,
        f"{http_server}replay-track-css-transition.html",
        tmp_path / "virtual-css.json",
        driver="virtual-clock",
        denominator_ms=640,
    )

    assert proc.returncode != 0
    assert "target owns Animation objects" in proc.stderr
    assert not (tmp_path / "virtual-css.json").exists()


@pytest.mark.parametrize(
    ("fixture_name", "selector", "expected_name"),
    [
        ("replay-track-lenis-shim.html", ".lenis-label", "lenis"),
        ("replay-track-lenis-marker-only.html", ".transport-label", "lenis-unproven"),
        ("replay-track-locomotive-v4-marker.html", ".transport-label", "locomotive"),
        ("replay-track-locomotive-v5-global.html", ".transport-label", "locomotive"),
        ("replay-track-scrollsmoother-global.html", ".transport-label", "ScrollSmoother"),
    ],
)
def test_recorder_rejects_custom_scroll_transport_before_capture(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
    fixture_name: str,
    selector: str,
    expected_name: str,
) -> None:
    out_path = tmp_path / f"{Path(fixture_name).stem}.json"
    proc = _run_recorder(
        repo_root,
        f"{http_server}{fixture_name}",
        out_path,
        mode="scroll-progress",
        selector=selector,
    )

    assert proc.returncode != 0
    assert f"custom-scroll-transport-unsupported: {expected_name}" in proc.stderr
    assert not out_path.exists()


def test_recorder_rejects_markerless_root_wheel_hijack_before_capture(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    out_path = tmp_path / "markerless-wheel.json"
    proc = _run_recorder(
        repo_root,
        f"{http_server}replay-track-markerless-wheel-hijack.html",
        out_path,
        mode="scroll-progress",
        selector=".wheel-label",
    )

    assert proc.returncode != 0
    assert "custom-scroll-transport-unsupported: custom-wheel" in proc.stderr
    assert "root non-passive wheel listener" in proc.stderr
    assert not out_path.exists()


def test_scroll_progress_lenis_wheel_rejects_marker_only_spoof(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    out_path = tmp_path / "lenis-marker-spoof.json"
    proc = _run_recorder(
        repo_root,
        f"{http_server}replay-track-lenis-marker-spoof.html",
        out_path,
        mode="scroll-progress",
        transport="lenis-wheel",
        selector=".spoof-label",
    )

    assert proc.returncode != 0
    assert "--transport lenis-wheel requires detected Lenis, got lenis-unproven" in proc.stderr
    assert not out_path.exists()


def test_scroll_progress_trigger_schema_is_exact(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    track_path = tmp_path / "progress-ok.json"
    proc = _run_recorder(
        repo_root,
        f"{http_server}replay-track-scroll-progress.html",
        track_path,
        mode="scroll-progress",
        selector=".progress-box",
    )

    assert proc.returncode == 0, proc.stderr
    track = json.loads(track_path.read_text())
    assert track["trigger"] == {
        "type": "scroll-progress",
        "startPx": 0,
        "endPx": 200,
        "sampleDenominator": 20,
    }
    assert [sample["index"] for sample in track["samples"]] == list(range(21))
    assert [sample["progress"] for sample in track["samples"]] == [
        index / 20 for index in range(21)
    ]
    assert "sampleDenominator" not in track["samples"][0]
    assert [sample["settle"] for sample in track["samples"]] == [
        {"status": "settled", "frames": 2}
    ] * 21
    assert validate_track(track) == []


def test_scroll_progress_rejects_timed_transition(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    proc = _run_recorder(
        repo_root,
        f"{http_server}replay-track-css-transition.html",
        tmp_path / "progress.json",
        mode="scroll-progress",
    )

    assert proc.returncode != 0
    assert "scroll-progress mode does not support timed animations" in proc.stderr
    assert not (tmp_path / "progress.json").exists()


def test_scroll_action_rejects_js_timed_mutation(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    proc = _run_recorder(
        repo_root,
        f"{http_server}replay-track-js-timed.html",
        tmp_path / "js.json",
    )

    assert proc.returncode != 0
    assert "JS timed mutations are not supported" in proc.stderr
    assert not (tmp_path / "js.json").exists()


def test_scroll_action_rejects_scroll_timeline(
    tmp_path: Path,
    http_server: str,
    repo_root: Path,
) -> None:
    proc = _run_recorder(
        repo_root,
        f"{http_server}replay-track-scroll-timeline.html",
        tmp_path / "timeline.json",
    )

    assert proc.returncode != 0
    assert "ScrollTimeline" in proc.stderr or "no fresh CSS Animation objects" in proc.stderr
    assert not (tmp_path / "timeline.json").exists()
