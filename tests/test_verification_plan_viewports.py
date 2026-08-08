"""D13 (loop-nvti-0): the plan's viewport fan-out (375/1280/1600/1920) omitted
the CANONICAL CAPTURE viewport 1440x900 — the only width where ref screenshots
have a true same-width ground truth. The multi-viewport section-compare then
ran everywhere EXCEPT the viewport the reference was captured at, and the
canonical 1440 run had to be dispatched by hand."""

from __future__ import annotations

import re
from pathlib import Path

PLAN = (Path(__file__).resolve().parents[1]
        / "skills" / "visual-debug" / "scripts" / "verification-plan.sh")


def _viewport_block() -> str:
    src = PLAN.read_text(encoding="utf-8")
    m = re.search(r'"viewports":\s*\[(.*?)\]', src, re.DOTALL)
    assert m, "verification-plan.sh must emit a viewports list"
    return m.group(1)


def test_plan_viewports_include_capture_viewport() -> None:
    block = _viewport_block()
    assert re.search(r'"w":\s*1440,\s*"h":\s*900', block), (
        "plan viewports must include the canonical capture viewport 1440x900 "
        "(D13: fan-out ran at every width except the one the ref was captured at)"
    )


def test_plan_viewports_keep_existing_widths() -> None:
    block = _viewport_block()
    for w in (375, 1280, 1600, 1920):
        assert f'"w": {w}' in block, f"existing fan-out width {w} must remain"
