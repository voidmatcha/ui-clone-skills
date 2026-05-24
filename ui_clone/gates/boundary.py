"""Boundary gate.

Extracted from ui_clone/gate.py. Each function takes `self: "Gate"` and is
rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .base import CheckResult

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401

def gate_boundary(self: Gate) -> list[CheckResult]:
    """Check that breakpoint-collision-check.sh has been run and reports no collisions.

    Reads tmp/ref/<c>/responsive/boundary-collisions.json — must exist and be `[]`.
    Catches the Tailwind ↔ project @media boundary collision class
    (see diagnosis.md → Root Cause J). The bug only manifests at exactly the
    breakpoint width and Step 4-C2 measurements happen to land on those widths,
    so it never appears as a sweep change — only an isolated overflow spike.
    """
    results = []
    path = self.ref_dir / "responsive" / "boundary-collisions.json"
    fix_msg = (
        "Run: bash skills/visual-debug/scripts/breakpoint-collision-check.sh "
        "<session> <impl-url>"
    )
    if not path.is_file():
        results.append(
            CheckResult(
                "responsive/boundary-collisions.json",
                "fail",
                "responsive/boundary-collisions.json — MISSING (breakpoint-collision-check.sh has not been run)",
                fix=fix_msg,
            )
        )
        return results

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        results.append(
            CheckResult(
                "responsive/boundary-collisions.json",
                "fail",
                f"responsive/boundary-collisions.json — unreadable ({e})",
                fix=fix_msg,
            )
        )
        return results

    if not isinstance(data, list):
        results.append(
            CheckResult(
                "responsive/boundary-collisions.json",
                "fail",
                "responsive/boundary-collisions.json — must be a JSON array",
                fix=fix_msg,
            )
        )
        return results

    if not data:
        results.append(
            CheckResult(
                "responsive/boundary-collisions.json",
                "pass",
                "No breakpoint collisions detected",
            )
        )
        return results

    bp_summary = ", ".join(str(d.get("bp", "?")) for d in data if isinstance(d, dict))
    results.append(
        CheckResult(
            "boundary collisions",
            "fail",
            f"{len(data)} breakpoint collision(s) detected at: {bp_summary}. "
            "See diagnosis.md → Root Cause J for fix patterns.",
            fix=(
                "Pick ONE side: (A) shift project @media to (max-width: <bp - 0.02>px), "
                "or (B) bump Tailwind variant up one tier (md: → lg:). "
                "Re-run breakpoint-collision-check.sh until the array is []."
            ),
        )
    )
    return results

