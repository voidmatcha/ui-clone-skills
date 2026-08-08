"""F10: transition-compare must not SILENTLY skip hover verification.

When the reference animates :hover but the matched impl element ranked outside
this side's COMPARE_LIMIT top-N (so hover-states.json['impl'] has no entry for
it), the old code's `if ref_hover and impl_hover and …` guard fell through: no
issue, no marker, and the row PASSED on timing-map equality alone — a silent
unverified hover. The fix emits a NON-FATAL HOVER_UNVERIFIED warning (a hard fail
would false-fail a correct impl whose hover merely wasn't captured).

The comparison lives in transition_compare_report.py, so these tests invoke that
helper directly against pre-seeded fixtures — running the real code with no
duplicated comparison logic.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "transition_compare_report.py"


_IDLE = {
    "opacity": "1",
    "transform": "none",
    "backgroundColor": "rgba(0, 0, 0, 0)",
    "color": "rgb(0, 0, 0)",
}


def _el(selector: str = ".btn") -> dict:
    return {
        "selector": selector,
        "text": "Go",
        "tag": "a",
        "idleStyle": dict(_IDLE),
        "transition": {"properties": []},
    }


def _run(tmp_path: Path, hover_states: dict) -> list[dict]:
    tdir = tmp_path / "transitions"
    tdir.mkdir(parents=True)
    (tdir / "ref-elements.json").write_text(json.dumps([_el()]), encoding="utf-8")
    (tdir / "impl-elements.json").write_text(json.dumps([_el()]), encoding="utf-8")
    (tdir / "hover-states.json").write_text(json.dumps(hover_states), encoding="utf-8")

    subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path), "20"],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    report = json.loads((tdir / "report.json").read_text(encoding="utf-8"))
    assert isinstance(report, list)
    return report


def test_ref_hover_with_uncaptured_impl_hover_warns_not_silent_pass(tmp_path: Path) -> None:
    """ref animates :hover (color), impl hover NOT captured -> non-fatal
    HOVER_UNVERIFIED warning, verdict stays PASS (no false-fail)."""
    report = _run(
        tmp_path,
        {
            "ref": [{"selector": ".btn", "hoverStyle": {"color": "rgb(255, 0, 0)"}}],
            "impl": [],  # impl hover state absent (outside compare window)
        },
    )
    row = next(r for r in report if r["selector"] == ".btn")
    assert any("HOVER_UNVERIFIED" in w for w in row.get("warnings", [])), row
    assert row["status"] == "PASS", (
        f"unverified hover must NOT false-fail a correct impl; got {row['status']}"
    )


def test_reset_only_ref_hover_does_not_warn(tmp_path: Path) -> None:
    """ref hover does not actually change any tracked property (reset-only) -> no
    warning even when impl hover is absent (nothing to verify)."""
    report = _run(
        tmp_path,
        {
            "ref": [{"selector": ".btn", "hoverStyle": dict(_IDLE)}],  # same as idle
            "impl": [],
        },
    )
    row = next(r for r in report if r["selector"] == ".btn")
    assert not any("HOVER_UNVERIFIED" in w for w in row.get("warnings", [])), row


def test_captured_impl_hover_does_not_warn(tmp_path: Path) -> None:
    """When the impl hover IS captured, the normal compare runs — no unverified
    warning (here impl matches ref, so also no issue)."""
    report = _run(
        tmp_path,
        {
            "ref": [{"selector": ".btn", "hoverStyle": {"color": "rgb(255, 0, 0)"}}],
            "impl": [{"selector": ".btn", "hoverStyle": {"color": "rgb(255, 0, 0)"}}],
        },
    )
    row = next(r for r in report if r["selector"] == ".btn")
    assert not any("HOVER_UNVERIFIED" in w for w in row.get("warnings", [])), row
    assert row["status"] == "PASS", row


def test_failed_real_pointer_capture_warns_instead_of_comparing_idle_as_hover(
    tmp_path: Path,
) -> None:
    report = _run(
        tmp_path,
        {
            "ref": [
                {
                    "selector": ".btn",
                    "hoverStyle": {"color": "rgb(255, 0, 0)"},
                    "hoverVerified": True,
                }
            ],
            "impl": [
                {
                    "selector": ".btn",
                    "hoverStyle": {},
                    "hoverVerified": False,
                    "captureError": "real pointer did not reach target",
                }
            ],
        },
    )
    row = next(r for r in report if r["selector"] == ".btn")
    assert any("HOVER_UNVERIFIED" in w for w in row.get("warnings", [])), row
    assert not any("HOVER_COLOR_NOT_APPLIED" in issue for issue in row["issues"])
