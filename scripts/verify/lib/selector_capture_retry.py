#!/usr/bin/env python3
"""Classify selector SSIM failures that are confined to capture onset."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

# One independent 10fps source sample can phase-shift a 400ms CSS transition
# across its full observed arc after 60fps duplication. The shell caller uses
# 0.3s normally and may extend only to the measured arc, with this hard cap.
MAX_EARLY_WINDOW_SECONDS = 0.5


def build_retry_receipt(
    values: list[float],
    *,
    threshold: float,
    fps: float,
    early_window_seconds: float,
    selector: str,
    ref_first_change: int | None = None,
    ref_last_change: int | None = None,
    impl_first_change: int | None = None,
    impl_last_change: int | None = None,
    arc_max_delta: int | None = None,
) -> dict[str, object] | None:
    """Return a retry receipt only when every failure is in the early window."""
    if (
        not values
        or fps <= 0
        or early_window_seconds <= 0
        or early_window_seconds > MAX_EARLY_WINDOW_SECONDS
    ):
        return None

    failure_rows = [
        index + 1 for index, value in enumerate(values) if value < threshold
    ]
    if not failure_rows:
        return None

    early_window_rows = max(1, math.ceil(fps * early_window_seconds))
    last_failure_row = failure_rows[-1]
    if last_failure_row > early_window_rows or last_failure_row >= len(values):
        return None

    receipt: dict[str, object] = {
        "schemaVersion": 1,
        "status": "retryable-unmeasurable",
        "reason": "early-window-capture-phase",
        "selector": selector,
        "threshold": threshold,
        "rows": len(values),
        "failures": len(failure_rows),
        "failureRows": failure_rows,
        "firstStablePassingRow": last_failure_row + 1,
        "lastFailureRow": last_failure_row,
        "earlyWindowSeconds": early_window_seconds,
        "earlyWindowRows": early_window_rows,
        "extractedFps": fps,
        "minSsim": min(values),
    }
    arc_values = (
        ref_first_change,
        ref_last_change,
        impl_first_change,
        impl_last_change,
        arc_max_delta,
    )
    if any(value is not None for value in arc_values):
        if (
            any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in arc_values
            )
            or min(value for value in arc_values if value is not None) < 0
        ):
            raise ValueError("arc receipt fields must be non-negative integers")
        assert ref_first_change is not None
        assert ref_last_change is not None
        assert impl_first_change is not None
        assert impl_last_change is not None
        assert arc_max_delta is not None
        ref_duration = max(0, ref_last_change - ref_first_change)
        impl_duration = max(0, impl_last_change - impl_first_change)
        delta = abs(ref_duration - impl_duration)
        one_side_has_no_motion = (ref_duration == 0) != (impl_duration == 0)
        receipt["arc"] = {
            "ref": {
                "firstChange": ref_first_change,
                "lastChange": ref_last_change,
                "durationFrames": ref_duration,
            },
            "impl": {
                "firstChange": impl_first_change,
                "lastChange": impl_last_change,
                "durationFrames": impl_duration,
            },
            "deltaFrames": delta,
            "maxDeltaFrames": arc_max_delta,
            "withinTolerance": (
                not one_side_has_no_motion and delta <= arc_max_delta
            ),
        }
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("series", type=Path)
    parser.add_argument("threshold", type=float)
    parser.add_argument("fps", type=float)
    parser.add_argument("early_window_seconds", type=float)
    parser.add_argument("out", type=Path)
    parser.add_argument("selector")
    parser.add_argument("--ref-first-change", type=int)
    parser.add_argument("--ref-last-change", type=int)
    parser.add_argument("--impl-first-change", type=int)
    parser.add_argument("--impl-last-change", type=int)
    parser.add_argument("--arc-max-delta", type=int)
    args = parser.parse_args()

    values = [
        float(line.strip())
        for line in args.series.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    receipt = build_retry_receipt(
        values,
        threshold=args.threshold,
        fps=args.fps,
        early_window_seconds=args.early_window_seconds,
        selector=args.selector,
        ref_first_change=args.ref_first_change,
        ref_last_change=args.ref_last_change,
        impl_first_change=args.impl_first_change,
        impl_last_change=args.impl_last_change,
        arc_max_delta=args.arc_max_delta,
    )
    if receipt is None:
        return 1

    args.out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
