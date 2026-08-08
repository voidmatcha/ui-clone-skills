#!/usr/bin/env python3
"""Build equal-size, target-local crop plans for selector video comparisons."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any


def _unwrap_browser_result(value: Any) -> Any:
    for _ in range(4):
        if (
            isinstance(value, dict)
            and isinstance(value.get("data"), dict)
            and "result" in value["data"]
        ):
            value = value["data"]["result"]
            continue
        if isinstance(value, str):
            value = json.loads(value)
            continue
        break
    return value


def load_target_rect(path: Path) -> dict[str, float]:
    value = _unwrap_browser_result(json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(value, dict) or value.get("found") is not True:
        raise ValueError(f"target was not resolved in {path}")
    rect = value.get("rect")
    if not isinstance(rect, dict):
        raise ValueError(f"target rect is missing in {path}")
    normalized: dict[str, float] = {}
    for field in ("x", "y", "width", "height"):
        raw = rect.get(field)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):  # noqa: UP038
            raise ValueError(f"target rect {field} is invalid in {path}")
        number = float(raw)
        if not math.isfinite(number):
            raise ValueError(f"target rect {field} is not finite in {path}")
        normalized[field] = number
    if normalized["width"] <= 0 or normalized["height"] <= 0:
        raise ValueError(f"target rect has no visible area in {path}")
    return normalized


def _crop_for(
    rect: dict[str, float],
    crop_width: int,
    crop_height: int,
    viewport_width: int,
    viewport_height: int,
) -> dict[str, int]:
    center_x = rect["x"] + rect["width"] / 2
    center_y = rect["y"] + rect["height"] / 2
    x = round(center_x - crop_width / 2)
    y = round(center_y - crop_height / 2)
    x = max(0, min(x, viewport_width - crop_width))
    y = max(0, min(y, viewport_height - crop_height))
    return {
        "x": x,
        "y": y,
        "width": crop_width,
        "height": crop_height,
    }


def build_plan(
    ref_rect: dict[str, float],
    impl_rect: dict[str, float],
    *,
    viewport_width: int,
    viewport_height: int,
    padding: int,
    selector: str,
) -> dict[str, Any]:
    if viewport_width <= 0 or viewport_height <= 0:
        raise ValueError("viewport dimensions must be positive")
    if padding < 0:
        raise ValueError("padding must be non-negative")
    crop_width = min(
        viewport_width,
        max(1, math.ceil(max(ref_rect["width"], impl_rect["width"]) + 2 * padding)),
    )
    crop_height = min(
        viewport_height,
        max(1, math.ceil(max(ref_rect["height"], impl_rect["height"]) + 2 * padding)),
    )
    ref_crop = _crop_for(
        ref_rect,
        crop_width,
        crop_height,
        viewport_width,
        viewport_height,
    )
    impl_crop = _crop_for(
        impl_rect,
        crop_width,
        crop_height,
        viewport_width,
        viewport_height,
    )
    return {
        "schemaVersion": 1,
        "status": "pass",
        "selector": selector,
        "viewport": {
            "width": viewport_width,
            "height": viewport_height,
        },
        "padding": padding,
        "comparison": "target-local-delta-from-pre-action-frame",
        "ref": {"target": ref_rect, "crop": ref_crop},
        "impl": {"target": impl_rect, "crop": impl_crop},
    }


def _filter(crop: dict[str, int]) -> str:
    return (
        f"crop={crop['width']}:{crop['height']}:"
        f"{crop['x']}:{crop['y']}"
    )


def main(argv: list[str]) -> int:
    if len(argv) != 9 or argv[1] != "plan":
        print(
            "usage: video_compare_roi.py plan "
            "<ref-raw> <impl-raw> <viewport-w> <viewport-h> "
            "<padding> <selector> <out>",
            file=sys.stderr,
        )
        return 2
    try:
        ref_rect = load_target_rect(Path(argv[2]))
        impl_rect = load_target_rect(Path(argv[3]))
        plan = build_plan(
            ref_rect,
            impl_rect,
            viewport_width=int(argv[4]),
            viewport_height=int(argv[5]),
            padding=int(argv[6]),
            selector=argv[7],
        )
        out = Path(argv[8])
        out.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"{_filter(plan['ref']['crop'])}\t"
            f"{_filter(plan['impl']['crop'])}"
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"target ROI error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
