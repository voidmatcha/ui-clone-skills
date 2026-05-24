"""Bundle gate.

Extracted from ui_clone/gate.py. Each function takes `self: "Gate"` and is
rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import CheckResult

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401

def gate_bundle(self: Gate) -> list[CheckResult]:
    results = []
    results.append(
        self.check_dir(self.ref_dir / "bundles", "bundles/ (downloaded JS chunks)", min_files=1)
    )

    # Advisory: warn if fewer than 3 JS chunks
    bundles_dir = self.ref_dir / "bundles"
    if bundles_dir.is_dir():
        js_count = sum(1 for f in bundles_dir.rglob("*.js") if f.is_file())
        if 1 <= js_count < 3:
            results.append(
                CheckResult(
                    "JS chunk count",
                    "warn",
                    f"Only {js_count} JS chunk(s) — typical SPAs have \u22653. "
                    "Verify all chunks via performance.getEntriesByType('resource').",
                )
            )

    for filename, label in [
        ("interactions-detected.json", "interactions-detected.json"),
        ("scroll-engine.json", "scroll-engine.json"),
    ]:
        results.append(self.check_file(self.ref_dir / filename, label))

    return results

