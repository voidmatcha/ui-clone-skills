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
