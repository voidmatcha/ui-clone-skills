"""Reference gate.

Extracted from ui_clone/gate.py. Each function takes `self: "Gate"` and is
rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import CheckResult

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401

def gate_reference(self: Gate) -> list[CheckResult]:
    results = []
    results.append(
        self.check_dir(
            self.ref_dir / "static" / "ref",
            "static/ref screenshots",
            min_files=5,
            fix="Run Phase 1: invoke /ui-capture <url> to capture reference screenshots",
        )
    )
    results.append(
        self.check_dir(
            self.ref_dir / "transitions" / "ref",
            "transitions/ref (transition videos)",
            min_files=1,
            fix="Run Phase 1: invoke /ui-capture <url> to capture transition videos",
        )
    )
    results.append(
        self.check_file(
            self.ref_dir / "regions.json",
            "regions.json (transition regions)",
            fix="Run Phase 1: invoke /ui-capture <url> to generate regions.json",
        )
    )
    return results

