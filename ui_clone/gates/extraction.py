"""Extraction gate.

Extracted from ui_clone/gate.py. Each function takes `self: "Gate"` and is
rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import CheckResult

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401

def gate_extraction(self: Gate) -> list[CheckResult]:
    results = []
    for filename, label in [
        ("structure.json", "structure.json (DOM hierarchy)"),
        ("head.json", "head.json (metadata)"),
        ("styles.json", "styles.json (computed styles)"),
        ("fonts.json", "fonts.json (font faces)"),
        ("visible-images.json", "visible-images.json"),
        ("inline-svgs.json", "inline-svgs.json"),
        ("body-state.json", "body-state.json"),
        ("design-bundles.json", "design-bundles.json"),
    ]:
        results.append(self.check_file(self.ref_dir / filename, label))

    results.append(
        self.check_file(
            self.ref_dir / "css" / "variables.txt", "css/variables.txt (CSS custom properties)"
        )
    )

    # Viewport-scaled font em-conversion gate
    typo = self._load_json("typography.json")
    if typo:
        scaling = typo.get("scalingSystem", "")
        if scaling and any(k in scaling.lower() for k in ("viewport-scaled", "em-based")):
            results.append(
                self.check_file(
                    self.ref_dir / "em-conversion.json",
                    f"em-conversion.json (REQUIRED: scalingSystem={scaling})",
                )
            )

    return results

