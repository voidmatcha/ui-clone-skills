"""Bottom-anchored capture planning in ui_clone.section_capture.

Loop-9 regression class: the footer's rect top sits past maxScroll, so the
legacy `scroll_y = top - 50` request silently CLAMPS at maxScroll while
`clip_top` still assumed 50 — the crop grabbed the wrong band, and the
near-bottom reveal (which only mounts when the page is scrolled to the end)
never fired. Result: byte-identical background-only crops on both sides and
an AE=0 vacuous pass.

Fix: plan the scroll from real page metrics — sections whose bottom is within
~1.5 viewports of the page end pin to maxScroll (both sides), and clip_top is
derived from the ACTUAL post-scroll position, never the requested one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import ui_clone.section_capture as section_capture
from ui_clone.section_capture import desired_scroll_y, should_pin_to_bottom


def test_mid_page_section_not_pinned() -> None:
    assert not should_pin_to_bottom(
        top=2000, height=600, scroll_height=20000, viewport_h=800
    )


def test_footer_within_1_5_viewports_of_end_is_pinned() -> None:
    # bottom = 19450, end zone starts at 20000 - 1.5*800 = 18800
    assert should_pin_to_bottom(
        top=18600, height=850, scroll_height=20000, viewport_h=800
    )


def test_exact_boundary_is_pinned() -> None:
    assert should_pin_to_bottom(
        top=18000, height=800, scroll_height=20000, viewport_h=800
    )


def test_zero_viewport_never_pins() -> None:
    assert not should_pin_to_bottom(
        top=18600, height=850, scroll_height=20000, viewport_h=0
    )


def test_desired_scroll_mid_page_is_top_minus_50() -> None:
    assert desired_scroll_y(
        top=2000, height=600, scroll_height=20000, viewport_h=800
    ) == 1950


def test_desired_scroll_pinned_is_max_scroll() -> None:
    assert desired_scroll_y(
        top=18600, height=850, scroll_height=20000, viewport_h=800
    ) == 19200  # scroll_height - viewport_h


def test_desired_scroll_never_negative() -> None:
    assert desired_scroll_y(top=10, height=300, scroll_height=600, viewport_h=800) == 0


def test_pinned_flat_crop_retries_top_aligned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    targets: list[float] = []

    monkeypatch.setattr(
        section_capture,
        "_scroll_metrics",
        lambda session, scroller_selector: {"y": targets[-1] if targets else 0.0, "vh": 200.0, "sh": 1000.0},
    )
    monkeypatch.setattr(section_capture, "_run_agent_eval", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_run_screenshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_run_crop", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_crop_is_blank", lambda *args, **kwargs: False)
    monkeypatch.setattr(section_capture, "crop_unique_colors", lambda path: 2)
    monkeypatch.setattr(section_capture.time, "sleep", lambda seconds: None)

    def fake_scroll_js(target_y: float, scroller_selector: str) -> str:
        targets.append(target_y)
        return "scroll"

    monkeypatch.setattr(section_capture, "_scroll_js", fake_scroll_js)

    meta = section_capture._capture_one(
        session="session",
        section_dir=tmp_path,
        side="ref",
        name="cta",
        rect={"top": 700, "height": 250, "width": 300, "left": 0},
        scroller_selector="__document__",
        pause_js="",
        finish_js="",
        skip_finish=True,
        wait_scroll_settle=0,
    )

    assert targets == [800.0, 650.0]
    assert meta is not None
    assert meta["topAlignedRetry"] is True
    assert meta["topAlignedRetryUniqueBefore"] == 2
    assert meta["actualY"] == 650.0


def test_capture_uses_live_rect_after_scroll_settle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crops: list[tuple[dict[str, object], float]] = []

    monkeypatch.setattr(
        section_capture,
        "_scroll_metrics",
        lambda session, scroller_selector: {"y": 10880.0, "vh": 800.0, "sh": 19229.0},
    )
    monkeypatch.setattr(section_capture, "_run_agent_eval", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_run_screenshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        section_capture,
        "_resolve_live_section_rect",
        lambda *args, **kwargs: {
            "top": 193.656,
            "left": 0.0,
            "width": 860.0,
            "height": 136.375,
            "documentTop": 11073.656,
        },
    )

    def fake_crop(image_path: Path, rect: dict[str, object], clip_top: float) -> None:
        crops.append((rect, clip_top))

    monkeypatch.setattr(section_capture, "_run_crop", fake_crop)

    meta = section_capture._capture_one(
        session="session",
        section_dir=tmp_path,
        side="ref",
        name="text",
        rect={"top": 10930, "height": 136, "width": 860, "left": 0},
        identity={"tag": "section", "className": "section sections_text", "text": "Better health begins on your plate"},
        scroller_selector="__document__",
        pause_js="",
        finish_js="",
        skip_finish=True,
        wait_scroll_settle=0,
    )

    assert len(crops) == 1
    assert crops[0][0]["top"] == 193.656
    assert crops[0][1] == 193.656
    assert meta is not None
    assert meta["liveRectResolved"] is True
    assert meta["plannedCropTop"] == 50.0
    assert meta["cropDriftPx"] == pytest.approx(143.656)


def test_capture_falls_back_to_planned_crop_when_live_rect_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crops: list[tuple[dict[str, object], float]] = []

    monkeypatch.setattr(
        section_capture,
        "_scroll_metrics",
        lambda session, scroller_selector: {"y": 10880.0, "vh": 800.0, "sh": 19229.0},
    )
    monkeypatch.setattr(section_capture, "_run_agent_eval", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_run_screenshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(section_capture, "_resolve_live_section_rect", lambda *args, **kwargs: None)

    def fake_crop(image_path: Path, rect: dict[str, object], clip_top: float) -> None:
        crops.append((rect, clip_top))

    monkeypatch.setattr(section_capture, "_run_crop", fake_crop)

    meta = section_capture._capture_one(
        session="session",
        section_dir=tmp_path,
        side="ref",
        name="text",
        rect={"top": 10930, "height": 136, "width": 860, "left": 0},
        identity={"tag": "section", "className": "section sections_text", "text": "Better health begins on your plate"},
        scroller_selector="__document__",
        pause_js="",
        finish_js="",
        skip_finish=True,
        wait_scroll_settle=0,
    )

    assert len(crops) == 1
    assert crops[0][0]["top"] == 10930
    assert crops[0][1] == 50.0
    assert meta is not None
    assert meta["liveRectResolved"] is False
    assert meta["plannedCropTop"] == 50.0


def test_capture_reads_scroll_position_after_viewport_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_y = {"value": 10880.0}
    crop_tops: list[float] = []

    monkeypatch.setenv("SECTION_CAPTURE_VIEW_W", "1280")
    monkeypatch.setattr(
        section_capture,
        "_scroll_metrics",
        lambda session, scroller_selector: {
            "y": current_y["value"],
            "vh": 800.0,
            "sh": 19229.0,
        },
    )
    monkeypatch.setattr(section_capture, "_run_agent_eval", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_run_screenshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_resolve_live_section_rect", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        section_capture,
        "_ensure_viewport",
        lambda session, width: current_y.update(value=10700.0),
    )
    monkeypatch.setattr(
        section_capture,
        "_run_crop",
        lambda image_path, rect, clip_top: crop_tops.append(clip_top),
    )

    meta = section_capture._capture_one(
        session="session",
        section_dir=tmp_path,
        side="impl",
        name="text",
        rect={"top": 10930, "height": 136, "width": 860, "left": 0},
        scroller_selector="__document__",
        pause_js="",
        finish_js="",
        skip_finish=True,
        wait_scroll_settle=0,
        forced_scroll_y=10880,
    )

    assert crop_tops == [230.0]
    assert meta is not None
    assert meta["actualY"] == 10700.0
    assert meta["plannedCropTop"] == 230.0
