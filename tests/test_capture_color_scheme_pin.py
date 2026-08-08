"""W-4 (loop-ebpb-0, campaign-critical): the reference follows
prefers-color-scheme — a host OS theme flip (macOS auto-dark in the evening)
silently captured the ref in dark mode and poisoned an entire compare cycle
(footer dSSIM 0.0000065 -> 0.687 reading as catastrophic regression). Every
browser-driving capture/compare script must pin AGENT_BROWSER_COLOR_SCHEME to
light (caller-overridable) so a host theme flip can never silently invalidate
visual metrics again."""

from __future__ import annotations

from pathlib import Path

import pytest

SKILL_SCRIPTS = (Path(__file__).resolve().parents[1]
                 / "skills" / "visual-debug" / "scripts")
EXTRACT_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "extract"

PINNED = [
    "section-compare.sh",
    "transition-fires-check.sh",
    "transition-compare.sh",
    "visual-fidelity-judge-check.sh",
    "dynamic-behavior-parity.sh",
    "extract-section-map.sh",
    "hover-state-compare.sh",
    "click-state-compare.sh",
    "video-motion-compare.sh",
    "batch-scroll.sh",
    "desktop-band-fluidity-check.sh",
    "resize-behavior-probe.sh",
]


# fable review #2 MAJOR: verify-side pinning without CAPTURE-side pinning
# defeats the purpose — a dark-evening Phase-0 capture bakes dark styles into
# the ref corpus permanently.
PINNED_EXTRACT = [
    "capture.sh",
    "capture-hover.sh",
    "responsive-sweep.sh",
    "canvas-replay-capture.sh",
]


@pytest.mark.parametrize("name", PINNED)
def test_capture_script_pins_light_color_scheme(name: str) -> None:
    src = (SKILL_SCRIPTS / name).read_text(encoding="utf-8")
    assert ': "${AGENT_BROWSER_COLOR_SCHEME:=light}"' in src, (
        f"{name} must pin the light color scheme (W-4: host theme flips "
        "silently poison every visual metric on theme-aware refs)"
    )
    assert "export AGENT_BROWSER_COLOR_SCHEME" in src


@pytest.mark.parametrize("name", PINNED_EXTRACT)
def test_extract_script_pins_light_color_scheme(name: str) -> None:
    src = (EXTRACT_SCRIPTS / name).read_text(encoding="utf-8")
    assert ': "${AGENT_BROWSER_COLOR_SCHEME:=light}"'.replace(chr(92), '') in src
    assert "export AGENT_BROWSER_COLOR_SCHEME" in src


def test_extract_dom_pins_light_color_scheme() -> None:
    src = (SKILL_SCRIPTS / "extract-dom.sh").read_text(encoding="utf-8")
    assert ': "${AGENT_BROWSER_COLOR_SCHEME:=light}"' in src
    assert "export AGENT_BROWSER_COLOR_SCHEME" in src


def test_no_mktemp_templates_with_suffix_after_xs() -> None:
    """L-MEA-13 (loop-ebpb-1/2): macOS mktemp requires the Xs to be TRAILING —
    a suffix after XXXXXX aborts the whole check. Two scripts shipped this
    twice (extract-section-map, then masked-region-static); scan them all."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    bad = []
    for base in (root / "skills", root / "scripts"):
        for sh in base.rglob("*.sh"):
            for i, line in enumerate(sh.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if re.search(r"mktemp[^)\n]*XXXXXX\.[A-Za-z]", line):
                    bad.append(f"{sh.relative_to(root)}:{i}")
    assert bad == [], f"mktemp templates with a suffix after the Xs: {bad}"
