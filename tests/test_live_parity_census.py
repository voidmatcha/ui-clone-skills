from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from ui_clone.gates.live_parity import (
    classify_image_drift,
    find_accessibility_text_leaks,
    scrollheight_within_tolerance,
)


def test_live_parity_system_python_paths_avoid_runtime_pep604_unions() -> None:
    root = Path(__file__).resolve().parents[1]
    execution_surfaces = (
        root / "ui_clone" / "gates" / "live_parity.py",
        root / "skills" / "visual-debug" / "scripts" / "live-parity-sweep.sh",
    )
    runtime_union = re.compile(
        r"isinstance\([^\n]*\b(?:int|float)\s*\|\s*(?:int|float)"
    )

    offenders = [
        path.relative_to(root).as_posix()
        for path in execution_surfaces
        if runtime_union.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == [], (
        "live-parity-sweep can run under macOS system Python 3.9; runtime "
        f"PEP 604 unions inside isinstance() crash there: {offenders}"
    )


def test_timer_carousel_rotation_is_advisory() -> None:
    """loop-e2e-4: the ref's custom setInterval carousel rotates even while
    off-screen, so census-time food identity is phase noise. Same filename
    vocabulary + per-file delta <= 1 => advisory, not blocking."""
    verdict = classify_image_drift(
        {"butter.webp": 3, "salmon.webp": 2, "steak.webp": 1},
        {"butter.webp": 2, "salmon.webp": 3, "steak.webp": 1},
    )
    assert verdict == "advisory"


def test_missing_file_from_vocabulary_is_blocking() -> None:
    verdict = classify_image_drift(
        {"butter.webp": 2, "salmon.webp": 2},
        {"salmon.webp": 4},
    )
    assert verdict == "blocking"


def test_count_delta_over_one_is_blocking() -> None:
    # a 3-instance gap is structural duplication, not rotation phase
    verdict = classify_image_drift(
        {"butter.webp": 4, "salmon.webp": 1},
        {"butter.webp": 1, "salmon.webp": 4},
    )
    assert verdict == "blocking"


def test_no_drift_is_clean() -> None:
    assert classify_image_drift({"a.webp": 2}, {"a.webp": 2}) == "clean"


def test_scrollheight_within_tolerance() -> None:
    # loop-e2e-4: 19226 vs 19229 on a page whose own height oscillates +-180px
    assert scrollheight_within_tolerance(19229, 19226)
    assert scrollheight_within_tolerance(19229, 19354)  # 125px < 200px floor


def test_scrollheight_balloon_still_fails() -> None:
    assert not scrollheight_within_tolerance(17952, 39092)  # loop-129 balloon
    assert not scrollheight_within_tolerance(19229, 19650)  # 421px > max(0.5%, 200)


def test_scrollheight_missing_values_not_tolerated() -> None:
    assert not scrollheight_within_tolerance(None, 19226)
    assert not scrollheight_within_tolerance(19229, None)


def _find_accessibility_text_leaks(
    candidates: Sequence[Mapping[str, object]], ref_visible: str, impl_visible: str
) -> list[dict[str, str]]:
    return find_accessibility_text_leaks(candidates, ref_visible, impl_visible)


def test_hidden_accessibility_description_visible_only_in_impl_is_blocking() -> None:
    description = (
        "Animated showcase of the Evo design system's color palette, displaying "
        "tone-on-tone color chips with AA or AAA contrast labels"
    )
    leaks = _find_accessibility_text_leaks(
        [
            {
                "text": description,
                "source": "hidden-text",
                "tag": "P",
                "class": "clipped",
            }
        ],
        "AA AAA AA AAA Color palette",
        f"AA AAA AA AAA {description}",
    )

    assert leaks == [
        {
            "text": description,
            "source": "hidden-text",
            "tag": "P",
            "class": "clipped",
        }
    ]


def test_accessibility_description_already_visible_in_ref_is_not_a_leak() -> None:
    description = "A detailed product description that is intentionally visible as a caption"
    candidates = [
        {
            "text": description,
            "source": "aria-label",
            "tag": "IMG",
            "class": "product-image",
        }
    ]

    assert not _find_accessibility_text_leaks(
        candidates,
        f"Product heading {description}",
        f"Product heading {description}",
    )


def test_accessibility_leak_detection_ignores_short_or_unrelated_copy() -> None:
    candidates = [
        {"text": "Open navigation", "source": "aria-label", "tag": "BUTTON", "class": ""},
        {
            "text": "Long screen reader description that does not render in the clone",
            "source": "hidden-text",
            "tag": "SPAN",
            "class": "sr-only",
        },
    ]

    assert not _find_accessibility_text_leaks(
        candidates,
        "Home Products About",
        "Home Products About Open navigation",
    )


def test_accessibility_leak_detection_normalizes_case_and_whitespace() -> None:
    description = "Long accessibility description for the animated color palette"
    leaks = _find_accessibility_text_leaks(
        [{"text": description, "source": "alt", "tag": "CANVAS", "class": ""}],
        "Color palette",
        "LONG  ACCESSIBILITY\nDESCRIPTION FOR THE ANIMATED COLOR PALETTE",
    )

    assert len(leaks) == 1
    assert leaks[0]["source"] == "alt"


def test_live_parity_sweep_wires_accessibility_copy_census() -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "visual-debug"
        / "scripts"
        / "live-parity-sweep.sh"
    ).read_text(encoding="utf-8")

    assert "accessibilityOnlyText" in script
    assert "visibleBodyText" in script
    assert "find_accessibility_text_leaks" in script
    assert "visible-accessibility-copy-leak" in script
