from pathlib import Path

VIDEO_COMPARE = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "verify"
    / "video-transition-compare.sh"
)


def test_click_actions_use_target_roi_and_restore_it_before_dispatch() -> None:
    script = VIDEO_COMPARE.read_text(encoding="utf-8")
    action_case = script.split('TARGET_ROI_SELECTOR=""', 1)[1].split("esac", 1)[0]
    click_branch = script.split('elif [[ "$action" == click:* ]]', 1)[1].split(
        'elif [[ "$action" == hover:* ]]', 1
    )[0]

    assert 'click:*) TARGET_ROI_SELECTOR="${ACTION#click:}"' in action_case
    assert 'restore_visible_target_rect "$session" "$selector" "$target_rect"' in click_branch
    assert 'capture_action_onset "$session" "$action_onset_file"' in click_branch
    assert click_branch.index("restore_visible_target_rect") < click_branch.index("el.click()")
    assert click_branch.index("capture_action_onset") < click_branch.index("el.click()")


def test_selector_scroll_settle_waits_for_lenis_to_stop() -> None:
    script = VIDEO_COMPARE.read_text(encoding="utf-8")

    assert script.count("stableScrollSamples") >= 2
    assert "Math.abs(window.scrollY - lastScrollY) < 0.5" in script
    assert "performance.now() - settleStartedAt < 3000" in script


def test_selector_tail_trim_does_not_duplicate_final_fps_resampling() -> None:
    script = VIDEO_COMPARE.read_text(encoding="utf-8")
    trim_block = script.split("_selector_working_video()", 1)[1].split(
        "build_target_roi_delta_frames()", 1
    )[0]

    assert '-vf "fps=$FPS"' not in trim_block
    assert 'local filter="fps=$FPS"' in script
