"""Validation helpers for scroll-completion runtime evidence."""

from __future__ import annotations

import math
from typing import Any

_ENDPOINT_FIELDS = (
    "scrollY",
    "maxScroll",
    "scrollHeight",
    "viewportHeight",
    "remaining",
)
_ENDPOINT_TOLERANCE_PX = 2.0


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def validate_scroll_completion(data: Any) -> tuple[bool, str]:
    """Require a measured document endpoint for every captured viewport."""

    if not isinstance(data, dict):
        return False, "artifact must be an object"
    status = data.get("status")
    if status == "skip":
        return True, "skipped"
    if status != "pass":
        return False, f"status={status}"

    viewports = data.get("viewports")
    if not isinstance(viewports, list) or not viewports:
        return False, "pass but no viewport endpoint measurements"

    total_candidates = 0
    for index, viewport in enumerate(viewports):
        if not isinstance(viewport, dict):
            return False, f"viewport {index} must be an object"
        endpoint = viewport.get("endpoint")
        if not isinstance(endpoint, dict):
            return False, f"viewport {index} has no endpoint measurement"

        for field in _ENDPOINT_FIELDS:
            if not _finite_number(endpoint.get(field)):
                return False, f"viewport {index} endpoint.{field} is not finite"

        scroll_y = float(endpoint["scrollY"])
        max_scroll = float(endpoint["maxScroll"])
        scroll_height = float(endpoint["scrollHeight"])
        viewport_height = float(endpoint["viewportHeight"])
        remaining = float(endpoint["remaining"])
        if min(scroll_y, max_scroll, remaining) < 0:
            return False, f"viewport {index} endpoint contains a negative scroll coordinate"
        if scroll_height <= 0 or viewport_height <= 0:
            return False, f"viewport {index} endpoint contains a non-positive page dimension"

        clipped = endpoint.get("clippedLandmarks")
        if not isinstance(clipped, list):
            return False, f"viewport {index} endpoint.clippedLandmarks is not an array"
        if clipped:
            return False, f"viewport {index} endpoint has clipped landmarks: {clipped}"
        if endpoint.get("reached") is not True:
            return False, f"viewport {index} document end was not reached"

        expected_max = max(0.0, scroll_height - viewport_height)
        if abs(max_scroll - expected_max) > _ENDPOINT_TOLERANCE_PX:
            return False, f"viewport {index} endpoint.maxScroll does not match page dimensions"
        expected_remaining = max(0.0, max_scroll - scroll_y)
        if abs(remaining - expected_remaining) > _ENDPOINT_TOLERANCE_PX:
            return False, f"viewport {index} endpoint.remaining is inconsistent"
        if remaining > _ENDPOINT_TOLERANCE_PX or abs(max_scroll - scroll_y) > _ENDPOINT_TOLERANCE_PX:
            return False, f"viewport {index} stopped {remaining:g}px before document end"

        candidates = viewport.get("candidates", 0)
        if not _finite_number(candidates) or float(candidates) < 0:
            return False, f"viewport {index} candidates is not a non-negative number"
        total_candidates += int(candidates)

    return True, f"{len(viewports)} viewport endpoint(s), candidates={total_candidates}"
