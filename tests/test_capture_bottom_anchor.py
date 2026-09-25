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

import json
import subprocess
from pathlib import Path

import pytest

import ui_clone.section_capture as section_capture
from ui_clone.section_capture import desired_scroll_y, should_pin_to_bottom


def test_screenshot_retries_one_pixel_browser_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    heights = iter([1.0, 900.0])

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(section_capture, "_run_agent_browser", fake_run)
    monkeypatch.setattr(section_capture, "_canvas_height", lambda path: next(heights))
    monkeypatch.setattr(section_capture.time, "sleep", lambda seconds: None)

    section_capture._run_screenshot("session", tmp_path / "section.png")

    assert len(calls) == 2


def test_screenshot_fails_closed_after_repeated_invalid_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(section_capture, "_run_agent_browser", fake_run)
    monkeypatch.setattr(section_capture, "_canvas_height", lambda path: 1.0)
    monkeypatch.setattr(section_capture.time, "sleep", lambda seconds: None)

    with pytest.raises(RuntimeError, match="screenshot invalid after 3 attempts"):
        section_capture._run_screenshot("session", tmp_path / "section.png")

    assert len(calls) == 3


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


def test_whole_page_single_section_not_pinned() -> None:
    """A coarse single-section match whose own height already spans most of
    the document (an impl matched as one whole-page section instead of
    ref's granular markup) makes top + height land near scroll_height no
    matter where the section actually starts. Real footers never approach
    half the document height, so this degenerate whole-page match must not
    be pinned to maxScroll."""
    assert not should_pin_to_bottom(
        top=0, height=9500, scroll_height=10000, viewport_h=800
    )


def test_just_under_half_height_still_pins() -> None:
    """The whole-page guard only excludes a section spanning >= half the
    document — a genuine near-bottom section just under that boundary must
    still pin normally."""
    assert should_pin_to_bottom(
        top=5001, height=4999, scroll_height=10000, viewport_h=800
    )


def test_exact_half_height_is_excluded_from_pinning() -> None:
    """The whole-page guard boundary is inclusive: a section spanning
    exactly half the document is excluded from pinning, matching
    `height >= scroll_height * 0.5`."""
    assert not should_pin_to_bottom(
        top=5000, height=5000, scroll_height=10000, viewport_h=800
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


def test_bottom_sticky_capture_retries_at_containing_block_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    targets: list[float] = []

    monkeypatch.setattr(
        section_capture,
        "_scroll_metrics",
        lambda session, scroller_selector: {
            "y": targets[-1] if targets else 0.0,
            "vh": 200.0,
            "sh": 1000.0,
        },
    )
    monkeypatch.setattr(section_capture, "_run_agent_eval", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_run_screenshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_run_crop", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        section_capture,
        "_resolve_live_section_rect",
        lambda *args, **kwargs: {
            "top": 100.0,
            "left": 0.0,
            "width": 300.0,
            "height": 100.0,
            "documentTop": 150.0,
            "bottomSticky": True,
            "stickyEndScrollY": 500.0,
        },
    )

    def fake_scroll_js(target_y: float, scroller_selector: str) -> str:
        targets.append(target_y)
        return "scroll"

    monkeypatch.setattr(section_capture, "_scroll_js", fake_scroll_js)

    meta = section_capture._capture_one(
        session="session",
        section_dir=tmp_path,
        side="ref",
        name="footer",
        rect={"top": 100, "height": 100, "width": 300, "left": 0},
        identity={"tag": "footer", "selector": "footer.site-footer"},
        scroller_selector="__document__",
        pause_js="",
        finish_js="",
        skip_finish=True,
        wait_scroll_settle=0,
    )

    assert targets == [50.0, 500.0]
    assert meta is not None
    assert meta["actualY"] == 500.0
    assert meta["bottomAnchored"] is True
    assert meta["stickyEndRecapture"] is True


def test_forced_impl_capture_does_not_replan_bottom_sticky_scroll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    targets: list[float] = []
    monkeypatch.setattr(
        section_capture,
        "_scroll_metrics",
        lambda session, scroller_selector: {
            "y": targets[-1] if targets else 0.0,
            "vh": 200.0,
            "sh": 1000.0,
        },
    )
    monkeypatch.setattr(section_capture, "_run_agent_eval", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_run_screenshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_run_crop", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        section_capture,
        "_resolve_live_section_rect",
        lambda *args, **kwargs: {
            "top": 100.0,
            "left": 0.0,
            "width": 300.0,
            "height": 100.0,
            "documentTop": 150.0,
            "bottomSticky": True,
            "stickyEndScrollY": 500.0,
        },
    )

    def fake_scroll_js(target_y: float, scroller_selector: str) -> str:
        targets.append(target_y)
        return "scroll"

    monkeypatch.setattr(
        section_capture,
        "_scroll_js",
        fake_scroll_js,
    )

    meta = section_capture._capture_one(
        session="session",
        section_dir=tmp_path,
        side="impl",
        name="footer",
        rect={"top": 100, "height": 100, "width": 300, "left": 0},
        identity={"tag": "footer", "selector": "footer.site-footer"},
        scroller_selector="__document__",
        pause_js="",
        finish_js="",
        skip_finish=True,
        wait_scroll_settle=0,
        forced_scroll_y=50.0,
    )

    assert targets == [50.0]
    assert meta is not None
    assert "stickyEndRecapture" not in meta


def test_fixed_blank_capture_seeks_scrolled_signal_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    targets: list[float] = []
    monkeypatch.setattr(
        section_capture,
        "_scroll_metrics",
        lambda session, scroller_selector: {
            "y": targets[-1] if targets else 0.0,
            "vh": 200.0,
            "sh": 1000.0,
        },
    )
    monkeypatch.setattr(section_capture, "_run_agent_eval", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_run_screenshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_run_crop", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        section_capture,
        "_resolve_live_section_rect",
        lambda *args, **kwargs: {
            "top": 0.0,
            "left": 0.0,
            "width": 300.0,
            "height": 80.0,
            "documentTop": targets[-1] if targets else 0.0,
            "position": "fixed",
        },
    )

    def fake_scroll_js(target_y: float, scroller_selector: str) -> str:
        targets.append(target_y)
        return "scroll"

    monkeypatch.setattr(
        section_capture,
        "_scroll_js",
        fake_scroll_js,
    )
    monkeypatch.setattr(
        section_capture,
        "crop_unique_colors",
        lambda path: 2 if not targets or targets[-1] == 0 else 20,
    )
    monkeypatch.setattr(
        section_capture,
        "_crop_is_blank",
        lambda path: not targets or targets[-1] == 0,
    )

    meta = section_capture._capture_one(
        session="session",
        section_dir=tmp_path,
        side="ref",
        name="nav",
        rect={"top": 0, "height": 80, "width": 300, "left": 0},
        identity={"tag": "header", "className": "site-nav"},
        scroller_selector="__document__",
        pause_js="",
        finish_js="",
        skip_finish=True,
        wait_scroll_settle=0,
    )

    assert targets == [0.0, 150.0]
    assert meta is not None
    assert meta["signalRecovery"] is True
    assert meta["actualY"] == 150.0
    assert meta["signalRecoveryUniqueBefore"] == 2
    assert meta["signalRecoveryUniqueAfter"] == 20


def test_reference_capture_reapplies_opt_in_scroll_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    eval_scripts: list[str] = []
    monkeypatch.setenv("REF_SCROLL_CAP_SELECTOR", "#scroll-cap")
    monkeypatch.setenv("REF_RESET_SCROLLLEFT_SELECTOR", ".carousel")
    monkeypatch.setattr(
        section_capture,
        "_scroll_metrics",
        lambda session, scroller_selector: {"y": 0.0, "vh": 200.0, "sh": 1000.0},
    )
    monkeypatch.setattr(
        section_capture,
        "_run_agent_eval",
        lambda session, script: eval_scripts.append(script),
    )
    monkeypatch.setattr(section_capture, "_run_screenshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_run_crop", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture.time, "sleep", lambda seconds: None)

    section_capture._capture_one(
        session="ref-session",
        section_dir=tmp_path,
        side="ref-calib",
        name="sessions",
        rect={"top": 300, "height": 100, "width": 300, "left": 0},
        scroller_selector="__document__",
        pause_js="",
        finish_js="",
        skip_finish=True,
        wait_scroll_settle=0,
    )

    normalization = [
        script for script in eval_scripts if "const capSelector" in script
    ]
    assert len(normalization) >= 3
    assert 'const capSelector = "#scroll-cap"' in normalization[0]
    assert 'const resetSelector = ".carousel"' in normalization[0]
    assert "setProperty('max-height','none','important')" in normalization[0]


def test_implementation_capture_does_not_apply_reference_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    eval_scripts: list[str] = []
    monkeypatch.setenv("REF_SCROLL_CAP_SELECTOR", "#scroll-cap")
    monkeypatch.setattr(
        section_capture,
        "_scroll_metrics",
        lambda session, scroller_selector: {"y": 0.0, "vh": 200.0, "sh": 1000.0},
    )
    monkeypatch.setattr(
        section_capture,
        "_run_agent_eval",
        lambda session, script: eval_scripts.append(script),
    )
    monkeypatch.setattr(section_capture, "_run_screenshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_run_crop", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture.time, "sleep", lambda seconds: None)

    section_capture._capture_one(
        session="impl-session",
        section_dir=tmp_path,
        side="impl",
        name="sessions",
        rect={"top": 300, "height": 100, "width": 300, "left": 0},
        scroller_selector="__document__",
        pause_js="",
        finish_js="",
        skip_finish=True,
        wait_scroll_settle=0,
        forced_scroll_y=250.0,
    )

    assert all("const capSelector" not in script for script in eval_scripts)


def test_impl_path_reference_capture_applies_reference_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    eval_scripts: list[str] = []
    monkeypatch.setenv("REF_SCROLL_CAP_SELECTOR", "#scroll-cap")
    monkeypatch.setenv("SECTION_CAPTURE_IMPL_IS_REFERENCE", "1")
    monkeypatch.setattr(
        section_capture,
        "_scroll_metrics",
        lambda session, scroller_selector: {"y": 0.0, "vh": 200.0, "sh": 1000.0},
    )
    monkeypatch.setattr(
        section_capture,
        "_run_agent_eval",
        lambda session, script: eval_scripts.append(script),
    )
    monkeypatch.setattr(section_capture, "_run_screenshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_run_crop", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture.time, "sleep", lambda seconds: None)

    section_capture._capture_one(
        session="impl-path-ref-session",
        section_dir=tmp_path,
        side="impl",
        name="sessions",
        rect={"top": 300, "height": 100, "width": 300, "left": 0},
        scroller_selector="__document__",
        pause_js="",
        finish_js="",
        skip_finish=True,
        wait_scroll_settle=0,
        forced_scroll_y=250.0,
    )

    assert any("const capSelector" in script for script in eval_scripts)


def test_required_scroll_cap_normalization_fails_without_matching_element(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REF_SCROLL_CAP_SELECTOR", "#missing-cap")
    monkeypatch.setenv("SECTION_CAPTURE_REQUIRE_SCROLL_CAP_NORMALIZED", "1")
    monkeypatch.setattr(
        section_capture,
        "_run_agent_eval_text",
        lambda session, script: (
            '"{\\"capCount\\":0,\\"resetCount\\":0,'
            '\\"residualCap\\":0,\\"height\\":900}"'
        ),
    )

    with pytest.raises(RuntimeError, match="did not hold"):
        section_capture._apply_reference_runtime_normalization("ref-session")


def test_canvas_underlay_is_deterministic_and_preserves_stacking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECTION_CAPTURE_CANVAS_UNDERLAY", "1")

    js = section_capture._canvas_underlay_js()

    assert "visibility: visible !important" in js
    assert "background: #202020 !important" in js
    assert "filter: brightness(0) !important" in js
    assert "position" not in js
    assert "z-index" not in js


def test_capture_writes_reference_anchored_foreground_roi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crops: list[tuple[Path, dict[str, object], float]] = []
    (tmp_path / "impl").mkdir()
    monkeypatch.setenv("SECTION_CAPTURE_CANVAS_UNDERLAY", "1")
    monkeypatch.setattr(
        section_capture,
        "_scroll_metrics",
        lambda session, scroller_selector: {"y": 0.0, "vh": 200.0, "sh": 1000.0},
    )
    monkeypatch.setattr(section_capture, "_run_agent_eval", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        section_capture,
        "_resolve_live_section_rect",
        lambda *args, **kwargs: {
            "top": 0.0,
            "left": 0.0,
            "width": 300.0,
            "height": 100.0,
            "documentTop": 0.0,
            "foregroundRoi": {"top": 20.0, "left": 30.0, "width": 120.0, "height": 40.0},
            "foregroundRectCount": 3,
            "underlayCanvasCount": 1,
        },
    )
    monkeypatch.setattr(
        section_capture,
        "_run_screenshot",
        lambda session, path: path.write_bytes(b"raster"),
    )

    def fake_run_crop(path: Path, rect: dict[str, object], top: float) -> None:
        crops.append((path, rect, top))

    monkeypatch.setattr(
        section_capture,
        "_run_crop",
        fake_run_crop,
    )

    forced_roi = {"top": 10.0, "left": 15.0, "width": 100.0, "height": 50.0}
    meta = section_capture._capture_one(
        session="impl-session",
        section_dir=tmp_path,
        side="impl",
        name="site-footer",
        rect={"top": 0, "height": 100, "width": 300, "left": 0},
        identity={"tag": "footer", "id": "site-footer"},
        scroller_selector="__document__",
        pause_js="",
        finish_js="",
        skip_finish=True,
        wait_scroll_settle=0,
        forced_scroll_y=0.0,
        forced_foreground_roi=forced_roi,
    )

    assert meta is not None
    assert meta["foregroundRoiUsed"] == forced_roi
    assert meta["foregroundRectCount"] == 3
    assert crops[0][0] == tmp_path / "foreground-roi" / "impl" / "site-footer.png"
    assert crops[0][1] == forced_roi
    assert crops[0][2] == 10.0
    assert crops[1][0] == tmp_path / "impl" / "site-footer.png"


def test_forced_foreground_roi_is_captured_without_impl_own_roi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crops: list[tuple[Path, dict[str, object], float]] = []
    (tmp_path / "impl").mkdir()
    monkeypatch.setenv("SECTION_CAPTURE_CANVAS_UNDERLAY", "1")
    monkeypatch.setattr(
        section_capture,
        "_scroll_metrics",
        lambda session, scroller_selector: {"y": 0.0, "vh": 200.0, "sh": 1000.0},
    )
    monkeypatch.setattr(section_capture, "_run_agent_eval", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        section_capture,
        "_resolve_live_section_rect",
        lambda *args, **kwargs: {
            "top": 0.0,
            "left": 0.0,
            "width": 300.0,
            "height": 100.0,
            "documentTop": 0.0,
        },
    )
    monkeypatch.setattr(
        section_capture,
        "_run_screenshot",
        lambda session, path: path.write_bytes(b"raster"),
    )

    def fake_run_crop(path: Path, rect: dict[str, object], top: float) -> None:
        crops.append((path, rect, top))

    monkeypatch.setattr(
        section_capture,
        "_run_crop",
        fake_run_crop,
    )

    forced_roi = {"top": 10.0, "left": 15.0, "width": 100.0, "height": 50.0}
    meta = section_capture._capture_one(
        session="impl-session",
        section_dir=tmp_path,
        side="impl",
        name="site-footer",
        rect={"top": 0, "height": 100, "width": 300, "left": 0},
        identity={"tag": "footer", "id": "site-footer"},
        scroller_selector="__document__",
        pause_js="",
        finish_js="",
        skip_finish=True,
        wait_scroll_settle=0,
        forced_scroll_y=0.0,
        forced_foreground_roi=forced_roi,
    )

    assert meta is not None
    assert meta["foregroundRoiUsed"] == forced_roi
    assert "foregroundRoi" not in meta
    assert crops[0][0] == tmp_path / "foreground-roi" / "impl" / "site-footer.png"
    assert crops[0][1] == forced_roi
    assert crops[0][2] == 10.0
    assert crops[1][0] == tmp_path / "impl" / "site-footer.png"


def test_sticky_blank_capture_seeks_section_local_signal_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    targets: list[float] = []
    monkeypatch.setattr(
        section_capture,
        "_scroll_metrics",
        lambda session, scroller_selector: {
            "y": targets[-1] if targets else 0.0,
            "vh": 200.0,
            "sh": 2000.0,
        },
    )
    monkeypatch.setattr(section_capture, "_run_agent_eval", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_run_screenshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_run_crop", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        section_capture,
        "_resolve_live_section_rect",
        lambda *args, **kwargs: {
            "top": 100.0,
            "left": 0.0,
            "width": 300.0,
            "height": 400.0,
            "documentTop": 600.0,
            "position": "sticky",
        },
    )

    def fake_scroll_js(target_y: float, scroller_selector: str) -> str:
        targets.append(target_y)
        return "scroll"

    monkeypatch.setattr(
        section_capture,
        "_scroll_js",
        fake_scroll_js,
    )
    monkeypatch.setattr(
        section_capture,
        "crop_unique_colors",
        lambda path: 20 if targets and targets[-1] == 600.0 else 2,
    )
    monkeypatch.setattr(
        section_capture,
        "_crop_is_blank",
        lambda path: not targets or targets[-1] != 600.0,
    )

    meta = section_capture._capture_one(
        session="session",
        section_dir=tmp_path,
        side="ref",
        name="sticky",
        rect={"top": 600, "height": 400, "width": 300, "left": 0},
        identity={"tag": "section", "className": "sticky"},
        scroller_selector="__document__",
        pause_js="",
        finish_js="",
        skip_finish=True,
        wait_scroll_settle=0,
    )

    assert targets == [550.0, 600.0]
    assert meta is not None
    assert meta["signalRecovery"] is True
    assert meta["actualY"] == 600.0


def test_live_capture_pairs_impl_with_recovered_reference_scroll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, float | None]] = []
    section_dir = tmp_path / "sections"
    (section_dir / "ref").mkdir(parents=True)
    (section_dir / "impl").mkdir(parents=True)
    monkeypatch.setenv("SECTION_CAPTURE_DIR", str(tmp_path))
    monkeypatch.setenv("SECTION_CAPTURE_SESSION_REF", "ref-session")
    monkeypatch.setenv("SECTION_CAPTURE_SESSION_IMPL", "impl-session")
    monkeypatch.delenv("SECTION_CAPTURE_REUSE_FROZEN_REF", raising=False)
    monkeypatch.delenv("SECTION_CAPTURE_REF_CALIB", raising=False)

    def fake_capture_one(**kwargs: object) -> dict[str, float]:
        side = str(kwargs["side"])
        forced = kwargs.get("forced_scroll_y")
        forced_value = float(forced) if isinstance(forced, int | float) else None
        calls.append((side, forced_value))
        return {"actualY": 600.0 if side == "ref" else float(forced_value or 0.0)}

    monkeypatch.setattr(section_capture, "_capture_one", fake_capture_one)

    section_capture.capture_matched_sections(
        [
            {
                "name": "sticky",
                "ref": {"rect": {"top": 550, "left": 0, "width": 300, "height": 400}},
                "impl": {"rect": {"top": 550, "left": 0, "width": 300, "height": 400}},
            }
        ]
    )

    assert calls == [("ref", None), ("impl", 600.0)]


@pytest.mark.parametrize("frozen", [False, True])
def test_recapture_replaces_empty_metadata_without_destroying_frozen_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen: bool
) -> None:
    section_dir = tmp_path / "sections"
    section_dir.mkdir()
    old_roi = {"hero": {"top": 20, "left": 10, "width": 100, "height": 30}}
    for side in ("ref", "impl"):
        (section_dir / f"{side}-scroll-positions.json").write_text('{"hero": 900}')
        (section_dir / f"{side}-foreground-rois.json").write_text(json.dumps(old_roi))
    monkeypatch.setenv("SECTION_CAPTURE_DIR", str(tmp_path))
    monkeypatch.setenv("SECTION_CAPTURE_SESSION_REF", "ref-session")
    monkeypatch.setenv("SECTION_CAPTURE_SESSION_IMPL", "impl-session")
    monkeypatch.setenv("SECTION_CAPTURE_REUSE_FROZEN_REF", "1" if frozen else "0")
    monkeypatch.setenv("SECTION_CAPTURE_REF_CALIB", "0")
    monkeypatch.setattr(section_capture, "_capture_one", lambda **kwargs: {})
    section_capture.capture_matched_sections([
        {"name": "hero", **{
            side: {"rect": {"top": 0, "left": 0, "width": 300, "height": 400}}
            for side in ("ref", "impl")
        }}
    ])
    for side in ("ref", "impl"):
        expected_positions = {"hero": 900} if frozen and side == "ref" else {}
        expected_rois = old_roi if frozen and side == "ref" else {}
        assert json.loads((section_dir / f"{side}-scroll-positions.json").read_text()) == expected_positions
        assert json.loads((section_dir / f"{side}-foreground-rois.json").read_text()) == expected_rois


def test_signal_recovery_restores_original_when_all_candidates_are_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    targets: list[float] = []
    monkeypatch.setattr(
        section_capture,
        "_scroll_metrics",
        lambda session, scroller_selector: {
            "y": targets[-1] if targets else 0.0,
            "vh": 200.0,
            "sh": 1000.0,
        },
    )
    monkeypatch.setattr(section_capture, "_run_agent_eval", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_run_screenshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture, "_run_crop", lambda *args, **kwargs: None)
    monkeypatch.setattr(section_capture.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        section_capture,
        "_resolve_live_section_rect",
        lambda *args, **kwargs: {
            "top": 0.0,
            "left": 0.0,
            "width": 300.0,
            "height": 80.0,
            "documentTop": targets[-1] if targets else 0.0,
            "position": "fixed",
        },
    )

    def fake_scroll_js(target_y: float, scroller_selector: str) -> str:
        targets.append(target_y)
        return "scroll"

    monkeypatch.setattr(
        section_capture,
        "_scroll_js",
        fake_scroll_js,
    )
    monkeypatch.setattr(section_capture, "crop_unique_colors", lambda path: 2)
    monkeypatch.setattr(section_capture, "_crop_is_blank", lambda path: True)

    meta = section_capture._capture_one(
        session="session",
        section_dir=tmp_path,
        side="ref",
        name="nav",
        rect={"top": 0, "height": 80, "width": 300, "left": 0},
        identity={"tag": "header", "className": "site-nav"},
        scroller_selector="__document__",
        pause_js="",
        finish_js="",
        skip_finish=True,
        wait_scroll_settle=0,
    )

    assert targets == [0.0, 150.0, 300.0, 0.0]
    assert meta is not None
    assert meta["actualY"] == 0.0
    assert "signalRecovery" not in meta


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
