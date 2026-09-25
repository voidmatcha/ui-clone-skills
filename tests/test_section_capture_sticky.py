from __future__ import annotations

from pathlib import Path

import pytest

import ui_clone.section_capture as capture


def _capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    live_rects: list[dict[str, object] | None],
) -> tuple[dict[str, object] | None, list[Path]]:
    metrics = iter(
        [
            {"y": 0.0, "vh": 900.0, "sh": 10_855.0},
            {"y": 308.0, "vh": 900.0, "sh": 10_855.0},
            {"y": 9_955.0, "vh": 900.0, "sh": 10_855.0},
        ]
    )
    resolved = iter(live_rects)
    screenshots: list[Path] = []
    monkeypatch.delenv("SECTION_CAPTURE_VIEW_W", raising=False)
    monkeypatch.setattr(capture, "_scroll_metrics", lambda *_args: next(metrics))
    monkeypatch.setattr(capture, "_run_agent_eval", lambda *_args: None)
    monkeypatch.setattr(
        capture,
        "_run_agent_eval_text",
        lambda *_args: '{"quiescent":true,"rounds":0,"runningAnimations":0}',
    )
    monkeypatch.setattr(
        capture,
        "_resolve_live_section_rect",
        lambda *_args: next(resolved),
    )
    monkeypatch.setattr(capture, "_run_crop", lambda *_args: None)
    monkeypatch.setattr(
        capture,
        "_run_screenshot",
        lambda _session, path: screenshots.append(path),
    )
    monkeypatch.setattr(capture, "crop_unique_colors", lambda *_args: 100)
    monkeypatch.setattr(capture.time, "sleep", lambda *_args: None)

    meta = capture._capture_one(
        session="footer",
        section_dir=tmp_path,
        side="ref",
        name="site-footer",
        rect={"top": 358, "left": 0, "width": 1440, "height": 542},
        scroller_selector="__document__",
        pause_js="undefined",
        finish_js="undefined",
        skip_finish=False,
        wait_scroll_settle=0,
        identity={
            "tag": "footer",
            "id": "extraction-name-not-dom-id",
            "className": "site-footer truncated-partial-token fr",
            "fingerprint": "brand punctuation stripped footer copy",
            "selector": "footer.site-footer",
        },
    )
    return meta, screenshots


def _live_rect(
    *,
    bottom_sticky: bool,
    hit_visible: bool,
    sticky_end_scroll_y: float | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "top": 358.0,
        "left": 0.0,
        "width": 1440.0,
        "height": 542.0,
        "documentTop": 666.0,
        "bottomSticky": bottom_sticky,
        "hitVisible": hit_visible,
    }
    if sticky_end_scroll_y is not None:
        result["stickyEndScrollY"] = sticky_end_scroll_y
    return result


def test_bottom_sticky_flow_end_recaptures_even_when_hit_test_claims_visible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    meta, screenshots = _capture(
        monkeypatch,
        tmp_path,
        [
            _live_rect(
                bottom_sticky=True,
                hit_visible=True,
                sticky_end_scroll_y=9_955.0,
            ),
            _live_rect(bottom_sticky=True, hit_visible=True),
        ],
    )

    assert meta is not None
    assert meta["actualY"] == 9_955.0
    assert meta["bottomAnchored"] is True
    assert meta["stickyEndRecapture"] is True
    assert meta["liveRectResolved"] is True
    assert meta["hitVisible"] is True
    assert len(screenshots) == 2


@pytest.mark.parametrize(
    "live_rect",
    [
        None,
        _live_rect(bottom_sticky=False, hit_visible=False),
        _live_rect(bottom_sticky=True, hit_visible=True),
    ],
)
def test_nonsticky_visible_and_unresolved_sections_keep_normal_scroll(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    live_rect: dict[str, object] | None,
) -> None:
    meta, screenshots = _capture(monkeypatch, tmp_path, [live_rect])

    assert meta is not None
    assert meta["actualY"] == 308.0
    assert "stickyEndRecapture" not in meta
    assert len(screenshots) == 1
