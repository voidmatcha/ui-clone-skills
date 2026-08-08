#!/usr/bin/env python3
"""Fail-closed aligned SSIM calibration for selector capture phase noise."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

RULE = "retry-cross-early-window-subset-of-reference-self-capture-phase"
COMPLEMENTARY_RULE = "mixed-early-window-and-arc-only-capture-phase"
STATIC_DISCRETE_RULE = "static-discrete-hover-state-source-bin-proof"
SCHEMA_VERSION = 4
EPSILON = 1e-6
WATCHED_STYLE_KEYS = (
    "color",
    "backgroundColor",
    "borderTopColor",
    "borderRightColor",
    "borderBottomColor",
    "borderLeftColor",
    "opacity",
    "transform",
    "filter",
    "boxShadow",
    "textDecorationLine",
    "textDecorationColor",
    "fontWeight",
    "letterSpacing",
)
CSS_TO_COMPUTED = {
    "background": "backgroundColor",
    "background-color": "backgroundColor",
    "border": (
        "borderTopColor",
        "borderRightColor",
        "borderBottomColor",
        "borderLeftColor",
    ),
    "border-top-color": "borderTopColor",
    "border-right-color": "borderRightColor",
    "border-bottom-color": "borderBottomColor",
    "border-left-color": "borderLeftColor",
    "border-color": (
        "borderTopColor",
        "borderRightColor",
        "borderBottomColor",
        "borderLeftColor",
    ),
    "box-shadow": "boxShadow",
    "text-decoration-line": "textDecorationLine",
    "text-decoration-color": "textDecorationColor",
    "font-weight": "fontWeight",
    "letter-spacing": "letterSpacing",
}


def _is_synthetic_hover_helper_class(token: str) -> bool:
    return token.startswith("h_") and token[2:].isdigit()


def _normalize_ancestor_class_path(path: Any) -> list[str] | None:
    if (
        not isinstance(path, list)
        or not path
        or len(path) > 6
        or any(not isinstance(item, str) or not item for item in path)
    ):
        return None
    normalized: list[str] = []
    for item in path:
        if item.startswith("."):
            return None
        if "#" in item:
            normalized.append(item)
            continue
        tag, *classes = item.split(".")
        if not tag:
            return None
        kept = [token for token in classes if not _is_synthetic_hover_helper_class(token)]
        normalized.append(".".join([tag, *kept]) if kept else tag)
    return normalized


def _state_paths_equal(left: Any, right: Any) -> bool:
    return _normalize_ancestor_class_path(left) == _normalize_ancestor_class_path(right)


def _values_sha256(values: list[float]) -> str:
    serialized = "".join(f"{value:.17g}\n" for value in values).encode()
    return hashlib.sha256(serialized).hexdigest()


def _read_series(path: Path) -> list[float]:
    values: list[float] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        value = raw.strip()
        if not value:
            continue
        values.append(float(value))
    if not values:
        raise ValueError(f"SSIM series is empty: {path}")
    return values


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unwrap_json(value: Any) -> Any:
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


def _read_target_payload(path: Path) -> dict[str, Any]:
    value = _unwrap_json(json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(value, dict):
        raise ValueError(f"target payload is not an object: {path}")
    return value


def _failure_rows(values: list[float], threshold: float) -> list[int]:
    return [index + 1 for index, value in enumerate(values) if value < threshold]


def _row_counts_cover_expected_window(
    *,
    reference_self_rows: int,
    first_cross_failure_rows: list[int],
    first_cross_rows: int,
    retry_cross_failure_rows: list[int],
    retry_cross_rows: int,
    expected_rows: int,
) -> bool:
    return (
        reference_self_rows == expected_rows
        and first_cross_rows >= expected_rows
        and retry_cross_rows >= expected_rows
        and all(row <= expected_rows for row in first_cross_failure_rows)
        and all(row <= expected_rows for row in retry_cross_failure_rows)
    )


def _contiguous_early_block(rows: list[int]) -> bool:
    return bool(rows) and rows == list(range(1, rows[-1] + 1))


def _sorted_unique_positive_rows(value: Any) -> list[int] | None:
    if not isinstance(value, list):
        return None
    if not all(isinstance(row, int) and not isinstance(row, bool) for row in value):
        return None
    if value != sorted(set(value)) or any(row <= 0 for row in value):
        return None
    return value


def _receipt_summary(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": receipt.get("schemaVersion"),
        "status": receipt.get("status"),
        "reason": receipt.get("reason"),
        "selector": receipt.get("selector"),
        "threshold": receipt.get("threshold"),
        "rows": receipt.get("rows"),
        "failures": receipt.get("failures"),
        "failureRows": receipt.get("failureRows"),
        "firstStablePassingRow": receipt.get("firstStablePassingRow"),
        "lastFailureRow": receipt.get("lastFailureRow"),
        "earlyWindowRows": receipt.get("earlyWindowRows"),
        "earlyWindowSeconds": receipt.get("earlyWindowSeconds"),
        "extractedFps": receipt.get("extractedFps"),
        "arc": receipt.get("arc"),
        "sourceMetadata": receipt.get("sourceMetadata"),
    }


def _target_summary(payload: dict[str, Any]) -> dict[str, Any]:
    rect = payload.get("rect")
    transition = payload.get("transition")
    return {
        "found": payload.get("found"),
        "selector": payload.get("selector"),
        "matchIndex": payload.get("matchIndex"),
        "matchCount": payload.get("matchCount"),
        "rect": {
            "x": rect.get("x") if isinstance(rect, dict) else None,
            "y": rect.get("y") if isinstance(rect, dict) else None,
            "width": rect.get("width") if isinstance(rect, dict) else None,
            "height": rect.get("height") if isinstance(rect, dict) else None,
        },
        "transition": {
            "property": transition.get("property") if isinstance(transition, dict) else None,
            "duration": transition.get("duration") if isinstance(transition, dict) else None,
            "delay": transition.get("delay") if isinstance(transition, dict) else None,
            "timingFunction": (
                transition.get("timingFunction") if isinstance(transition, dict) else None
            ),
        },
        "state": payload.get("state") if isinstance(payload.get("state"), dict) else None,
    }


def _parse_number_list(raw: Any) -> list[float] | None:
    if not isinstance(raw, str) or not raw:
        return None
    values: list[float] = []
    for part in raw.split(","):
        try:
            value = float(part.strip())
        except ValueError:
            return None
        if not math.isfinite(value):
            return None
        values.append(value)
    return values


def _parse_css_list(raw: Any) -> tuple[str, ...] | None:
    if not isinstance(raw, str) or not raw:
        return None
    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(raw):
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                return None
            depth -= 1
        elif char == "," and depth == 0:
            part = raw[start:index].strip()
            if not part:
                return None
            parts.append(part)
            start = index + 1
    if depth != 0:
        return None
    final = raw[start:].strip()
    if not final:
        return None
    parts.append(final)
    return tuple(parts)


def _transition_contract_key(
    transition: Any,
) -> tuple[
    tuple[str, ...],
    tuple[float, ...],
    tuple[float, ...],
    tuple[str, ...],
] | None:
    if not isinstance(transition, dict):
        return None
    properties = _parse_css_list(transition.get("property"))
    durations = _parse_number_list(transition.get("duration"))
    delays = _parse_number_list(transition.get("delay"))
    timings = _parse_css_list(transition.get("timingFunction"))
    if not properties or not durations or not delays or not timings:
        return None
    count = len(properties)
    effective_durations = tuple(
        durations[index % len(durations)] for index in range(count)
    )
    if max(effective_durations) <= 0:
        return None
    return (
        properties,
        effective_durations,
        tuple(delays[index % len(delays)] for index in range(count)),
        tuple(timings[index % len(timings)] for index in range(count)),
    )


def _hover_rect_delta(
    idle_rect: Any,
    hover_rect: Any,
) -> tuple[float, float, float, float] | None:
    rects: list[tuple[float, float, float, float]] = []
    for rect in (idle_rect, hover_rect):
        if not isinstance(rect, dict):
            return None
        raw_values = tuple(rect.get(key) for key in ("x", "y", "width", "height"))
        if any(
            isinstance(value, bool)
            or not (isinstance(value, int) or isinstance(value, float))
            or not math.isfinite(float(value))
            for value in raw_values
        ):
            return None
        values = (
            float(cast(float, raw_values[0])),
            float(cast(float, raw_values[1])),
            float(cast(float, raw_values[2])),
            float(cast(float, raw_values[3])),
        )
        if values[2] <= 0 or values[3] <= 0:
            return None
        rects.append(values)
    idle, hover = rects
    if (
        max(idle[2], hover[2]) / min(idle[2], hover[2]) > 1.25 + EPSILON
        or max(idle[3], hover[3]) / min(idle[3], hover[3]) > 1.25 + EPSILON
        or abs(hover[0] - idle[0]) > max(idle[2], hover[2]) * 0.25 + EPSILON
        or abs(hover[1] - idle[1]) > max(idle[3], hover[3]) * 0.25 + EPSILON
    ):
        return None
    return (
        hover[0] - idle[0],
        hover[1] - idle[1],
        hover[2] - idle[2],
        hover[3] - idle[3],
    )


def _hover_rect_deltas_match(
    deltas: list[tuple[float, float, float, float]],
) -> bool:
    if not deltas:
        return False
    expected = deltas[0]
    return all(
        all(abs(value - expected[index]) <= EPSILON for index, value in enumerate(delta))
        for delta in deltas[1:]
    )


def _valid_target_payloads(
    payloads: dict[str, dict[str, Any]],
    *,
    selector: str,
) -> tuple[bool, dict[str, Any]]:
    summaries = {name: _target_summary(payload) for name, payload in payloads.items()}
    if set(payloads) != {"firstRef", "firstImpl", "retryRef", "retryImpl"}:
        return False, {"summaries": summaries, "reason": "missing-target-side"}
    first = summaries["firstRef"]
    identity = {
        "selector": first.get("selector"),
        "matchIndex": first.get("matchIndex"),
        "matchCount": first.get("matchCount"),
    }
    transitions = [summary.get("transition") for summary in summaries.values()]
    dimensions: list[tuple[float, float]] = []
    for summary in summaries.values():
        rect = summary.get("rect")
        if (
            summary.get("found") is not True
            or summary.get("selector") != selector
            or {
                "selector": summary.get("selector"),
                "matchIndex": summary.get("matchIndex"),
                "matchCount": summary.get("matchCount"),
            }
            != identity
            or not isinstance(rect, dict)
        ):
            return False, {"summaries": summaries, "reason": "identity-mismatch"}
        width = rect.get("width")
        height = rect.get("height")
        if (
            isinstance(width, bool)
            or isinstance(height, bool)
            or not (isinstance(width, int) or isinstance(width, float))
            or not (isinstance(height, int) or isinstance(height, float))
            or not math.isfinite(float(width))
            or not math.isfinite(float(height))
            or float(width) <= 0
            or float(height) <= 0
        ):
            return False, {"summaries": summaries, "reason": "invalid-dimensions"}
        dimensions.append((float(width), float(height)))
    widths = [item[0] for item in dimensions]
    heights = [item[1] for item in dimensions]
    if max(widths) / min(widths) > 1.25 or max(heights) / min(heights) > 1.25:
        return False, {"summaries": summaries, "reason": "dimension-drift"}
    transition = transitions[0]
    if not isinstance(transition, dict):
        return False, {"summaries": summaries, "reason": "missing-transition-contract"}
    transition_keys = [_transition_contract_key(item) for item in transitions]
    if any(key is None for key in transition_keys):
        return False, {"summaries": summaries, "reason": "invalid-transition-contract"}
    if any(key != transition_keys[0] for key in transition_keys):
        return False, {"summaries": summaries, "reason": "transition-contract-mismatch"}
    prop = transition.get("property")
    properties = [
        part.strip().lower()
        for part in prop.split(",")
    ] if isinstance(prop, str) else []
    if not properties or all(part in {"", "none"} for part in properties):
        return False, {"summaries": summaries, "reason": "empty-transition-property"}
    if not all(
        isinstance(transition.get(field), str) and transition.get(field)
        for field in ("property", "delay", "timingFunction")
    ):
        return False, {"summaries": summaries, "reason": "invalid-transition-contract"}
    return True, {
        "summaries": summaries,
        "identity": identity,
        "transition": transition,
        "dimensionRange": {
            "width": {"min": min(widths), "max": max(widths)},
            "height": {"min": min(heights), "max": max(heights)},
        },
    }


def _valid_early_window_receipt(
    receipt: dict[str, Any],
    *,
    threshold: float,
    expected_rows: int,
    actual_rows: int,
    selector: str,
    expected_failure_rows: list[int] | None = None,
    allow_expected_window_boundary: bool = False,
) -> bool:
    failure_rows = _sorted_unique_positive_rows(receipt.get("failureRows"))
    first_stable = receipt.get("firstStablePassingRow")
    last_failure = receipt.get("lastFailureRow")
    early_window_rows = receipt.get("earlyWindowRows")
    arc = receipt.get("arc")
    if not failure_rows or not isinstance(arc, dict):
        return False
    ref_arc = arc.get("ref")
    impl_arc = arc.get("impl")
    if not isinstance(ref_arc, dict) or not isinstance(impl_arc, dict):
        return False
    ref_duration = ref_arc.get("durationFrames")
    impl_duration = impl_arc.get("durationFrames")
    ref_first = ref_arc.get("firstChange")
    ref_last = ref_arc.get("lastChange")
    impl_first = impl_arc.get("firstChange")
    impl_last = impl_arc.get("lastChange")
    delta = arc.get("deltaFrames")
    max_delta = arc.get("maxDeltaFrames")
    expected_delta = (
        abs(ref_duration - impl_duration)
        if isinstance(ref_duration, int)
        and not isinstance(ref_duration, bool)
        and isinstance(impl_duration, int)
        and not isinstance(impl_duration, bool)
        else None
    )
    arc_valid = (
        isinstance(ref_first, int)
        and not isinstance(ref_first, bool)
        and isinstance(ref_last, int)
        and not isinstance(ref_last, bool)
        and isinstance(impl_first, int)
        and not isinstance(impl_first, bool)
        and isinstance(impl_last, int)
        and not isinstance(impl_last, bool)
        and isinstance(ref_duration, int)
        and not isinstance(ref_duration, bool)
        and isinstance(impl_duration, int)
        and not isinstance(impl_duration, bool)
        and ref_first >= 0
        and ref_last >= ref_first
        and impl_first >= 0
        and impl_last >= impl_first
        and ref_duration == ref_last - ref_first
        and impl_duration == impl_last - impl_first
        and ref_duration > 0
        and impl_duration > 0
        and isinstance(delta, int)
        and not isinstance(delta, bool)
        and isinstance(max_delta, int)
        and not isinstance(max_delta, bool)
        and delta >= 0
        and max_delta >= 0
        and expected_delta is not None
        and delta == expected_delta
        and arc.get("withinTolerance") == (expected_delta <= max_delta)
    )
    return (
        receipt.get("schemaVersion") == 1
        and receipt.get("status") == "retryable-unmeasurable"
        and receipt.get("reason") == "early-window-capture-phase"
        and receipt.get("selector") == selector
        and receipt.get("threshold") == threshold
        and receipt.get("rows") == actual_rows
        and (expected_failure_rows is None or failure_rows == expected_failure_rows)
        and receipt.get("failures") == len(failure_rows)
        and last_failure == max(failure_rows)
        and first_stable == max(failure_rows) + 1
        and isinstance(early_window_rows, int)
        and not isinstance(early_window_rows, bool)
        and max(failure_rows) <= early_window_rows
        and (
            max(failure_rows) <= expected_rows
            if allow_expected_window_boundary
            else max(failure_rows) < expected_rows
        )
        and actual_rows >= expected_rows
        and arc_valid
    )


def _valid_runtime_early_window_receipt(
    receipt: dict[str, Any],
    *,
    threshold: float,
    actual_rows: int,
    selector: str,
    expected_failure_rows: list[int],
    source: dict[str, Any],
) -> bool:
    failure_rows = _sorted_unique_positive_rows(receipt.get("failureRows"))
    first_stable = receipt.get("firstStablePassingRow")
    last_failure = receipt.get("lastFailureRow")
    early_window_rows = receipt.get("earlyWindowRows")
    early_window_seconds = receipt.get("earlyWindowSeconds")
    extracted_fps = receipt.get("extractedFps")
    source_payloads = source.get("payload")
    source_extracted_values = []
    if isinstance(source_payloads, dict):
        for side in ("ref", "impl"):
            payload = source_payloads.get(side)
            if isinstance(payload, dict):
                source_extracted_values.append(payload.get("extractedFps"))
    source_extracted_fps = (
        source_extracted_values[0]
        if len(source_extracted_values) == 2
        and source_extracted_values[0] == source_extracted_values[1]
        else None
    )
    source_extracted_fps_number = (
        float(source_extracted_fps)
        if isinstance(source_extracted_fps, (int, float))  # noqa: UP038 - wrapper runs on system Python.
        and not isinstance(source_extracted_fps, bool)
        and math.isfinite(float(source_extracted_fps))
        and float(source_extracted_fps) > 0
        and float(source_extracted_fps).is_integer()
        else None
    )
    if not failure_rows:
        return False
    if (
        not (isinstance(early_window_seconds, int) or isinstance(early_window_seconds, float))
        or isinstance(early_window_seconds, bool)
        or not math.isfinite(float(early_window_seconds))
        or float(early_window_seconds) <= 0
        or float(early_window_seconds) > 0.5
        or not (isinstance(extracted_fps, int) or isinstance(extracted_fps, float))
        or isinstance(extracted_fps, bool)
        or not math.isfinite(float(extracted_fps))
        or float(extracted_fps) <= 0
        or not float(extracted_fps).is_integer()
        or source_extracted_fps_number is None
        or float(extracted_fps) != source_extracted_fps_number
    ):
        return False
    expected_early_rows = max(
        1,
        math.ceil(float(early_window_seconds) * float(extracted_fps)),
    )
    return (
        receipt.get("schemaVersion") == 1
        and receipt.get("status") == "retryable-unmeasurable"
        and receipt.get("reason") == "early-window-capture-phase"
        and receipt.get("selector") == selector
        and receipt.get("threshold") == threshold
        and receipt.get("rows") == actual_rows
        and failure_rows == expected_failure_rows
        and receipt.get("failures") == len(failure_rows)
        and last_failure == max(failure_rows)
        and first_stable == max(failure_rows) + 1
        and isinstance(early_window_rows, int)
        and not isinstance(early_window_rows, bool)
        and early_window_rows == expected_early_rows
        and actual_rows > early_window_rows
        and first_stable <= actual_rows
        and max(failure_rows) <= early_window_rows
    )


def _valid_runtime_row_count_drift(
    *,
    reference_self_rows: int,
    first_cross_rows: int,
    retry_cross_rows: int,
    expected_rows: int,
    ratio: int | None,
    first_early_window_rows: Any,
    retry_early_window_rows: Any,
) -> bool:
    if (
        not isinstance(ratio, int)
        or isinstance(ratio, bool)
        or ratio <= 0
        or not isinstance(first_early_window_rows, int)
        or isinstance(first_early_window_rows, bool)
        or not isinstance(retry_early_window_rows, int)
        or isinstance(retry_early_window_rows, bool)
    ):
        return False
    return (
        reference_self_rows == expected_rows
        and first_cross_rows >= first_early_window_rows
        and retry_cross_rows >= retry_early_window_rows
        and abs(first_cross_rows - expected_rows) <= ratio
        and abs(retry_cross_rows - expected_rows) <= ratio
        and abs(first_cross_rows - retry_cross_rows) <= 2 * ratio
    )


def _valid_arc_only_receipt(
    receipt: dict[str, Any],
    *,
    threshold: float,
    expected_rows: int,
    actual_rows: int,
    selector: str,
    expected_min_ssim: float,
    epsilon: float = EPSILON,
) -> bool:
    failure_rows = receipt.get("failureRows")
    arc = receipt.get("arc")
    minimum = receipt.get("minSsim")
    return (
        receipt.get("schemaVersion") == 1
        and receipt.get("status") == "retryable-unmeasurable"
        and receipt.get("reason") == "arc-only-capture-jitter"
        and receipt.get("selector") == selector
        and receipt.get("threshold") == threshold
        and receipt.get("rows") == actual_rows
        and actual_rows >= expected_rows
        and receipt.get("failures") == 0
        and failure_rows == []
        and receipt.get("firstStablePassingRow") == 1
        and receipt.get("lastFailureRow") == 0
        and (isinstance(minimum, int) or isinstance(minimum, float))
        and not isinstance(minimum, bool)
        and abs(float(minimum) - expected_min_ssim) <= epsilon
        and isinstance(arc, dict)
        and _arc_fields_valid(arc)
    )


def _arc_fields_valid(arc: dict[str, Any]) -> bool:
    ref_arc = arc.get("ref")
    impl_arc = arc.get("impl")
    if not isinstance(ref_arc, dict) or not isinstance(impl_arc, dict):
        return False
    ref_duration = ref_arc.get("durationFrames")
    impl_duration = impl_arc.get("durationFrames")
    ref_first = ref_arc.get("firstChange")
    ref_last = ref_arc.get("lastChange")
    impl_first = impl_arc.get("firstChange")
    impl_last = impl_arc.get("lastChange")
    delta = arc.get("deltaFrames")
    max_delta = arc.get("maxDeltaFrames")
    expected_delta = (
        abs(ref_duration - impl_duration)
        if isinstance(ref_duration, int)
        and not isinstance(ref_duration, bool)
        and isinstance(impl_duration, int)
        and not isinstance(impl_duration, bool)
        else None
    )
    return (
        isinstance(ref_first, int)
        and not isinstance(ref_first, bool)
        and isinstance(ref_last, int)
        and not isinstance(ref_last, bool)
        and isinstance(impl_first, int)
        and not isinstance(impl_first, bool)
        and isinstance(impl_last, int)
        and not isinstance(impl_last, bool)
        and isinstance(ref_duration, int)
        and not isinstance(ref_duration, bool)
        and isinstance(impl_duration, int)
        and not isinstance(impl_duration, bool)
        and ref_first >= 0
        and ref_last >= ref_first
        and impl_first >= 0
        and impl_last >= impl_first
        and ref_duration == ref_last - ref_first
        and impl_duration == impl_last - impl_first
        and ref_duration > 0
        and impl_duration > 0
        and isinstance(delta, int)
        and not isinstance(delta, bool)
        and isinstance(max_delta, int)
        and not isinstance(max_delta, bool)
        and delta >= 0
        and max_delta >= 0
        and expected_delta is not None
        and delta == expected_delta
        and arc.get("withinTolerance") == (expected_delta <= max_delta)
    )


def _arc_within_tolerance(receipt: dict[str, Any]) -> bool:
    arc = receipt.get("arc")
    return isinstance(arc, dict) and arc.get("withinTolerance") is True


def _arc_timing_summary(receipt: dict[str, Any]) -> dict[str, int] | None:
    arc = receipt.get("arc")
    if not isinstance(arc, dict) or not _arc_fields_valid(arc):
        return None
    ref_arc = arc["ref"]
    impl_arc = arc["impl"]
    ref_duration = ref_arc["durationFrames"]
    impl_duration = impl_arc["durationFrames"]
    return {
        "refDuration": ref_duration,
        "implDuration": impl_duration,
        "signedDelta": impl_duration - ref_duration,
        "maxDeltaFrames": arc["maxDeltaFrames"],
    }


def _source_bins(rows: list[int], ratio: int, offset: int = 0) -> list[int]:
    return sorted({((offset + row - 1) // ratio) + 1 for row in rows})


def _runtime_cross_source_bin_shape(
    first_source_bins: list[int],
    retry_source_bins: list[int],
    source_ratio: int,
) -> tuple[bool, dict[str, Any]]:
    def attempt_shape(source_bins: list[int]) -> dict[str, Any]:
        contiguous = bool(source_bins) and source_bins == list(
            range(source_bins[0], source_bins[-1] + 1)
        )
        return {
            "sourceBins": source_bins,
            "nonEmpty": bool(source_bins),
            "contiguous": contiguous,
            "boundedToTwoBins": 0 < len(source_bins) <= 2,
        }

    first_shape = attempt_shape(first_source_bins)
    retry_shape = attempt_shape(retry_source_bins)
    valid = (
        source_ratio > 1
        and all(
            bool(shape["nonEmpty"])
            and bool(shape["contiguous"])
            and bool(shape["boundedToTwoBins"])
            for shape in (first_shape, retry_shape)
        )
    )
    return valid, {
        "sourceRatio": source_ratio,
        "resampled": source_ratio > 1,
        "first": first_shape,
        "retry": retry_shape,
    }


def _valid_source_metadata(
    receipt: dict[str, Any],
    expected: dict[str, dict[str, Any]],
) -> tuple[bool, dict[str, Any]]:
    bound = receipt.get("sourceMetadata")
    if not isinstance(bound, dict) or set(bound) != {"ref", "impl"}:
        return False, {"reason": "missing-source-metadata"}
    observed: dict[str, Any] = {}
    ratios: list[int] = []
    for side in ("ref", "impl"):
        item = bound.get(side)
        exp = expected.get(side)
        if not isinstance(item, dict) or not isinstance(exp, dict):
            return False, {"reason": "missing-source-side"}
        payload = item.get("payload")
        if item.get("sha256") != exp.get("sha256") or payload != exp.get("payload"):
            return False, {"reason": "source-metadata-hash-mismatch"}
        if not isinstance(payload, dict):
            return False, {"reason": "invalid-source-payload"}
        ratio = payload.get("sourceToExtractedRatio")
        source_fps = payload.get("sourceFps")
        extracted_fps = payload.get("extractedFps")
        extracted_fps_number = (
            float(extracted_fps)
            if isinstance(extracted_fps, (int, float))  # noqa: UP038 - wrapper runs on system Python.
            and not isinstance(extracted_fps, bool)
            and math.isfinite(float(extracted_fps))
            and float(extracted_fps) > 0
            and float(extracted_fps).is_integer()
            else None
        )
        r_rate_raw = payload.get("rFrameRate")
        avg_rate_raw = payload.get("avgFrameRate")
        if (
            isinstance(r_rate_raw, bool)
            or isinstance(avg_rate_raw, bool)
            or not isinstance(r_rate_raw, (str, int, float))  # noqa: UP038 - system Python 3.9.
            or not isinstance(avg_rate_raw, (str, int, float))  # noqa: UP038 - system Python 3.9.
        ):
            return False, {"reason": "invalid-source-cadence"}
        try:
            r_rate = Fraction(str(r_rate_raw))
            avg_rate = Fraction(str(avg_rate_raw))
        except (TypeError, ValueError, ZeroDivisionError):
            return False, {"reason": "invalid-source-cadence"}
        if (
            payload.get("cfr") is not True
            or r_rate <= 0
            or avg_rate <= 0
            or r_rate != avg_rate
            or not isinstance(ratio, int)
            or isinstance(ratio, bool)
            or ratio <= 0
            or extracted_fps_number is None
            or not (isinstance(source_fps, int) or isinstance(source_fps, float))
            or isinstance(source_fps, bool)
            or not math.isfinite(float(source_fps))
            or source_fps <= 0
            or abs(float(source_fps) - float(r_rate)) > EPSILON
            or Fraction(int(extracted_fps_number), 1) / r_rate != ratio
        ):
            return False, {"reason": "invalid-source-cadence"}
        ratios.append(ratio)
        observed[side] = payload
    if ratios[0] != ratios[1]:
        return False, {"reason": "unequal-source-cadence"}
    return True, {"ratio": ratios[0], "payload": observed}


def _state_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    state = _unwrap_json(payload).get("state") if isinstance(_unwrap_json(payload), dict) else None
    if not isinstance(state, dict):
        return None
    watched = state.get("watchedStyle")
    path = _normalize_ancestor_class_path(state.get("ancestorClassPath"))
    if not isinstance(watched, dict) or path is None:
        return None
    bounded = {key: watched.get(key) for key in WATCHED_STYLE_KEYS}
    if any(not isinstance(value, str) for value in bounded.values()):
        return None
    return {"watchedStyle": bounded, "ancestorClassPath": path}


def _style_delta(idle: dict[str, str], hover: dict[str, str]) -> dict[str, dict[str, str]]:
    return {
        key: {"idle": idle[key], "hover": hover[key]}
        for key in WATCHED_STYLE_KEYS
        if idle[key] != hover[key]
    }


def _transition_properties(transition: dict[str, Any]) -> list[str] | None:
    raw = transition.get("property")
    if not isinstance(raw, str) or not raw.strip():
        return None
    values = [part.strip().lower() for part in raw.split(",") if part.strip()]
    if not values or any(value in {"", "none"} for value in values):
        return None
    return values


def _declared_computed_keys(properties: list[str]) -> set[str] | None:
    result: set[str] = set()
    for prop in properties:
        if prop == "all":
            result.update(WATCHED_STYLE_KEYS)
            continue
        mapped = CSS_TO_COMPUTED.get(prop)
        if mapped is None:
            mapped = prop
        if isinstance(mapped, tuple):
            result.update(mapped)
        elif isinstance(mapped, str) and mapped in WATCHED_STYLE_KEYS:
            result.add(mapped)
        else:
            return None
    return result


def _covered_computed_keys(
    declared_keys: set[str],
    idle_style: dict[str, str],
    hover_style: dict[str, str],
) -> set[str]:
    covered = set(declared_keys)
    if "color" not in declared_keys:
        return covered
    for key in (
        "borderTopColor",
        "borderRightColor",
        "borderBottomColor",
        "borderLeftColor",
        "textDecorationColor",
    ):
        if (
            idle_style[key] == idle_style["color"]
            and hover_style[key] == hover_style["color"]
        ):
            covered.add(key)
    return covered


def _valid_state_proof(
    *,
    target_payloads: dict[str, dict[str, Any]],
    action_payloads: dict[str, dict[str, Any]],
    selector: str,
) -> tuple[bool, dict[str, Any]]:
    targets_ok, target_summary = _valid_target_payloads(target_payloads, selector=selector)
    if not targets_ok:
        return False, {"reason": "target-payload-invalid", "target": target_summary}
    if set(action_payloads) != {"firstRef", "firstImpl", "retryRef", "retryImpl"}:
        return False, {"reason": "missing-action-payload"}
    idle_states: dict[str, dict[str, Any]] = {}
    hover_states: dict[str, dict[str, Any]] = {}
    action_targets: dict[str, Any] = {}
    hover_rect_deltas: list[tuple[float, float, float, float]] = []
    for name, target in target_payloads.items():
        idle = _state_payload(target)
        action = _unwrap_json(action_payloads[name])
        if not isinstance(action, dict):
            return False, {"reason": "invalid-action-payload"}
        hover = _state_payload(action)
        if idle is None or hover is None:
            return False, {"reason": "missing-state-snapshot"}
        target_summary = _target_summary(_unwrap_json(target))
        action_summary = _target_summary(action)
        target_transition_key = _transition_contract_key(target_summary.get("transition"))
        action_transition_key = _transition_contract_key(action_summary.get("transition"))
        hover_rect_delta = _hover_rect_delta(
            target_summary.get("rect"),
            action_summary.get("rect"),
        )
        if (
            action.get("found") is not True
            or action.get("hovered") is not True
            or action.get("pointerReachable") is not True
            or action_summary.get("selector") != target_summary.get("selector")
            or action_summary.get("matchIndex") != target_summary.get("matchIndex")
            or action_summary.get("matchCount") != target_summary.get("matchCount")
            or hover_rect_delta is None
            or action_transition_key is None
            or action_transition_key != target_transition_key
        ):
            return False, {"reason": "action-target-mismatch"}
        hover_rect_deltas.append(hover_rect_delta)
        idle_states[name] = idle
        hover_states[name] = hover
        action_targets[name] = _target_summary(_unwrap_json(action_payloads[name]))
    if not _hover_rect_deltas_match(hover_rect_deltas):
        return False, {"reason": "action-target-mismatch"}
    if any(idle_states[name] != idle_states["firstRef"] for name in idle_states):
        return False, {"reason": "idle-state-mismatch"}
    if any(hover_states[name] != hover_states["firstRef"] for name in hover_states):
        return False, {"reason": "hover-state-mismatch"}
    idle_style = idle_states["firstRef"]["watchedStyle"]
    hover_style = hover_states["firstRef"]["watchedStyle"]
    delta = _style_delta(idle_style, hover_style)
    if not delta:
        return False, {"reason": "no-discrete-style-delta"}
    idle_paths = {name: idle_states[name]["ancestorClassPath"] for name in idle_states}
    hover_paths = {name: hover_states[name]["ancestorClassPath"] for name in hover_states}
    ancestor_pair = {
        "idle": idle_paths["firstRef"],
        "hover": hover_paths["firstRef"],
    }
    if any(
        {"idle": idle_paths[name], "hover": hover_paths[name]} != ancestor_pair
        for name in idle_paths
    ):
        return False, {"reason": "ancestor-class-delta-mismatch"}
    ancestor_delta = {
        **ancestor_pair,
        "changed": ancestor_pair["idle"] != ancestor_pair["hover"],
    }
    transition = target_summary["transition"]
    if not isinstance(transition, dict):
        return False, {"reason": "missing-transition"}
    properties = _transition_properties(transition)
    if properties is None:
        return False, {"reason": "invalid-transition-properties"}
    transition_key = _transition_contract_key(transition)
    if transition_key is None:
        return False, {"reason": "invalid-transition-contract"}
    declared_keys = _declared_computed_keys(properties)
    if declared_keys is None:
        return False, {"reason": "unsupported-transition-property"}
    changed_keys = set(delta)
    if "all" in properties and changed_keys.intersection(declared_keys):
        return False, {
            "reason": "unbounded-transition-property-changed",
            "changedDeclared": sorted(changed_keys.intersection(declared_keys)),
        }
    positive_duration_properties = [
        prop
        for prop, duration in zip(properties, transition_key[1])  # noqa: B905 - system Python 3.9.
        if duration > 0
    ]
    positive_duration_keys = _declared_computed_keys(positive_duration_properties)
    if positive_duration_keys is None:
        return False, {"reason": "unsupported-transition-property"}
    covered_keys = _covered_computed_keys(
        positive_duration_keys,
        idle_style,
        hover_style,
    )
    changed_declared = sorted(changed_keys.intersection(covered_keys))
    if not changed_declared:
        state_change_mode = "static-discrete"
    elif changed_keys.issubset(covered_keys):
        state_change_mode = "declared-transition"
    else:
        return False, {
            "reason": "mixed-declared-transition-change",
            "changedDeclared": changed_declared,
            "uncoveredChanges": sorted(changed_keys.difference(covered_keys)),
        }
    return True, {
        "reason": "ok",
        "delta": delta,
        "ancestorDelta": ancestor_delta,
        "stateChangeMode": state_change_mode,
        "transitionProperties": properties,
        "declaredComputedKeys": sorted(declared_keys),
        "positiveDurationComputedKeys": sorted(positive_duration_keys),
        "coveredComputedKeys": sorted(covered_keys),
        "declaredTransitionChanges": changed_declared,
        "actionTargets": action_targets,
        "hoverRectDelta": hover_rect_deltas[0],
        "target": target_summary,
    }


def _arc_explained_by_reference_self(
    first_receipt: dict[str, Any],
    retry_receipt: dict[str, Any],
    ref_self_receipt: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    _ = ref_self_receipt
    first_arc = first_receipt.get("arc")
    retry_arc = retry_receipt.get("arc")
    if not isinstance(first_arc, dict) or not isinstance(retry_arc, dict):
        return False, {"reason": "invalid-reference-self-arc-drift"}
    if not _arc_fields_valid(first_arc) or not _arc_fields_valid(retry_arc):
        return False, {"reason": "invalid-reference-self-arc-drift"}
    drift = abs(
        first_arc["ref"]["durationFrames"] - retry_arc["ref"]["durationFrames"]
    )
    summaries = {}
    for name, receipt in (("first", first_receipt), ("retry", retry_receipt)):
        arc = receipt.get("arc")
        if not isinstance(arc, dict) or not _arc_fields_valid(arc):
            return False, {"reason": "invalid-arc", "attempt": name}
        ref_duration = arc["ref"]["durationFrames"]
        impl_duration = arc["impl"]["durationFrames"]
        if ref_duration <= 0 or impl_duration <= 0:
            return False, {"reason": "one-sided-or-no-motion", "attempt": name}
        delta = arc["deltaFrames"]
        within = arc["withinTolerance"] is True
        explained = within or delta <= drift
        summaries[name] = {
            "deltaFrames": delta,
            "withinTolerance": within,
            "refVsRefDurationDriftFrames": drift,
            "explained": explained,
        }
        if not explained:
            return False, {"reason": "unexplained-arc-outlier", "arc": summaries}
    return True, {"reason": "ok", "arc": summaries}


def _finite_time(value: Any) -> bool:
    return (
        (isinstance(value, int) or isinstance(value, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _as_finite_float(value: Any) -> float | None:
    if not _finite_time(value):
        return None
    return float(value)


def _declared_transition_duration_ms(
    transition: Any,
    *,
    changed_keys: set[str],
    idle_style: dict[str, str],
    hover_style: dict[str, str],
) -> float | None:
    contract = _transition_contract_key(transition)
    if contract is None or not changed_keys:
        return None
    properties, durations, delays, _ = contract
    effective: list[float] = []
    covered_changed_keys: set[str] = set()
    for prop, duration, delay in zip(properties, durations, delays):
        declared_keys = _declared_computed_keys([prop.lower()])
        if declared_keys is None:
            return None
        covered_keys = _covered_computed_keys(
            declared_keys,
            idle_style,
            hover_style,
        )
        changed_for_property = changed_keys.intersection(covered_keys)
        if duration <= 0 or not changed_for_property:
            continue
        effective_ms = max(0.0, duration + delay) * 1000.0
        if effective_ms <= 0:
            return None
        effective.append(effective_ms)
        covered_changed_keys.update(changed_for_property)
    if covered_changed_keys != changed_keys or not effective:
        return None
    return max(effective)


def _valid_hover_runtime_proof(
    *,
    target_payloads: dict[str, dict[str, Any]],
    action_payloads: dict[str, dict[str, Any]],
    state_summary: dict[str, Any],
    selector: str,
) -> tuple[bool, dict[str, Any]]:
    if state_summary.get("reason") != "ok":
        return False, {"reason": "state-proof-invalid"}
    state_change_mode = state_summary.get("stateChangeMode")
    if state_change_mode not in {"static-discrete", "declared-transition"}:
        return False, {"reason": "invalid-state-change-mode"}
    expected_delta = sorted(state_summary.get("delta", {}).keys())
    proofs: dict[str, dict[str, Any]] = {}
    latencies: list[float] = []
    for name in ("firstRef", "firstImpl", "retryRef", "retryImpl"):
        target = _unwrap_json(target_payloads.get(name, {}))
        action = _unwrap_json(action_payloads.get(name, {}))
        if not isinstance(target, dict) or not isinstance(action, dict):
            return False, {"reason": "missing-payload", "attempt": name}
        proof = action.get("hoverProof")
        if not isinstance(proof, dict):
            return False, {"reason": "missing-proof", "attempt": name}
        if (
            proof.get("schemaVersion") != 1
            or proof.get("selector") != selector
            or proof.get("matchIndex") != target.get("matchIndex")
            or proof.get("matchCount") != target.get("matchCount")
        ):
            return False, {"reason": "proof-identity-mismatch", "attempt": name}
        armed = proof.get("armedAt")
        move = proof.get("moveAt")
        pointer = proof.get("firstPointerEvent")
        first_commit = proof.get("firstCommitRaf")
        first_hover = proof.get("firstHoverRaf")
        stable = proof.get("stableAt")
        parsed_times = [
            _as_finite_float(value)
            for value in (armed, move, pointer, first_commit, first_hover, stable)
        ]
        if any(value is None for value in parsed_times):
            return False, {"reason": "invalid-proof-timestamps", "attempt": name}
        (
            armed_f,
            move_f,
            pointer_f,
            first_commit_f,
            first_hover_f,
            stable_f,
        ) = [
            value for value in parsed_times if value is not None
        ]
        if not (
            armed_f
            < move_f
            <= pointer_f
            <= first_hover_f
            <= first_commit_f
            <= stable_f
        ):
            return False, {"reason": "nonmonotonic-proof-timestamps", "attempt": name}
        latency = first_commit_f - pointer_f
        stable_count = proof.get("stableHoverRafCount")
        max_active_animation_count = proof.get("maxActiveAnimationCount")
        if (
            proof.get("pointerObserved") is not True
            or proof.get("rafObserved") is not True
            or proof.get("done") is not True
            or not isinstance(stable_count, int)
            or isinstance(stable_count, bool)
            or stable_count < 2
            or not isinstance(max_active_animation_count, int)
            or isinstance(max_active_animation_count, bool)
        ):
            return False, {"reason": "incomplete-runtime-proof", "attempt": name}
        initial = proof.get("initial")
        commit = proof.get("commit")
        final = proof.get("final")
        if (
            not isinstance(initial, dict)
            or not isinstance(commit, dict)
            or not isinstance(final, dict)
        ):
            return False, {"reason": "missing-proof-snapshots", "attempt": name}
        target_state = target.get("state")
        action_state = action.get("state")
        if not isinstance(target_state, dict) or not isinstance(action_state, dict):
            return False, {"reason": "missing-bound-state", "attempt": name}
        if (
            initial.get("watchedStyle") != target_state.get("watchedStyle")
            or not _state_paths_equal(
                initial.get("ancestorClassPath"), target_state.get("ancestorClassPath")
            )
            or initial.get("activeAnimationCount") != 0
            or commit.get("watchedStyle") != action_state.get("watchedStyle")
            or not _state_paths_equal(
                commit.get("ancestorClassPath"), action_state.get("ancestorClassPath")
            )
            or commit.get("hovered") is not True
            or final.get("watchedStyle") != action_state.get("watchedStyle")
            or not _state_paths_equal(
                final.get("ancestorClassPath"), action_state.get("ancestorClassPath")
            )
            or final.get("hovered") is not True
            or sorted(proof.get("changedStyleKeys", [])) != expected_delta
        ):
            return False, {"reason": "runtime-proof-state-mismatch", "attempt": name}
        mutation = _as_finite_float(proof.get("firstMutation"))
        mutation_snapshot = proof.get("mutation")
        mutation_observed = proof.get("mutationObserved")
        if state_change_mode == "static-discrete":
            if mutation is None:
                return False, {"reason": "invalid-proof-timestamps", "attempt": name}
            if not (pointer_f <= mutation <= first_commit_f):
                return False, {
                    "reason": "nonmonotonic-proof-timestamps",
                    "attempt": name,
                }
            if latency > 50:
                return False, {"reason": "delayed-hover-commit", "attempt": name}
            if mutation_observed is not True or max_active_animation_count != 0:
                return False, {"reason": "incomplete-runtime-proof", "attempt": name}
            if (
                not isinstance(mutation_snapshot, dict)
                or mutation_snapshot.get("time") != proof.get("firstMutation")
                or not _state_paths_equal(
                    mutation_snapshot.get("ancestorClassPath"),
                    action_state.get("ancestorClassPath"),
                )
            ):
                return False, {"reason": "runtime-proof-state-mismatch", "attempt": name}
        elif state_change_mode == "declared-transition":
            if max_active_animation_count < 1:
                return False, {"reason": "incomplete-runtime-proof", "attempt": name}
            if first_hover_f - pointer_f > 50:
                return False, {"reason": "delayed-hover-start", "attempt": name}
            expected_duration_ms = _declared_transition_duration_ms(
                target.get("transition"),
                changed_keys=set(expected_delta),
                idle_style=cast(dict[str, str], target_state.get("watchedStyle")),
                hover_style=cast(dict[str, str], action_state.get("watchedStyle")),
            )
            if expected_duration_ms is None:
                return False, {
                    "reason": "invalid-declared-transition-contract",
                    "attempt": name,
                }
            if abs(latency - expected_duration_ms) > 50:
                return False, {
                    "reason": "declared-transition-duration-mismatch",
                    "attempt": name,
                    "expectedMs": expected_duration_ms,
                    "observedMs": latency,
                }
            if mutation is None:
                if mutation_observed is not False or mutation_snapshot is not None:
                    return False, {
                        "reason": "incomplete-runtime-proof",
                        "attempt": name,
                    }
            elif (
                mutation_observed is not True
                or not isinstance(mutation_snapshot, dict)
                or not (pointer_f <= mutation <= first_commit_f)
                or mutation_snapshot.get("time") != proof.get("firstMutation")
                or not _state_paths_equal(
                    mutation_snapshot.get("ancestorClassPath"),
                    action_state.get("ancestorClassPath"),
                )
            ):
                return False, {"reason": "runtime-proof-state-mismatch", "attempt": name}
        proofs[name] = {
            "pointerToFirstHoverMs": latency,
            "stableHoverRafCount": proof.get("stableHoverRafCount"),
        }
        latencies.append(latency)
    if max(latencies) - min(latencies) > 25:
        return False, {"reason": "commit-latency-drift", "latencies": proofs}
    return True, {"reason": "ok", "latencies": proofs}


def calibrate_distributions(
    self_values: list[float],
    cross_values: list[float],
    *,
    threshold: float,
    expected_rows: int,
    first_offset: int = 0,
    retry_offset: int = 0,
    first_attempt: str = "first",
    retry_attempt: str = "retry",
    action: str = "hover:.target",
    reference_self_sha256: str = "",
    first_cross_sha256: str = "",
    retry_cross_sha256: str = "",
    first_cross_values: list[float] | None = None,
    first_capture_receipt: dict[str, Any] | None = None,
    retry_capture_receipt: dict[str, Any] | None = None,
    first_capture_receipt_sha256: str = "",
    retry_capture_receipt_sha256: str = "",
    epsilon: float = EPSILON,
) -> dict[str, Any]:
    """Accept only proven duplicate early-window capture-phase failures."""
    if not self_values or not cross_values:
        raise ValueError("reference-self and retry-cross SSIM series must be non-empty")
    if first_cross_values is not None and not first_cross_values:
        raise ValueError("first-cross SSIM series must be non-empty")
    if expected_rows <= 0:
        raise ValueError("expected_rows must be positive")
    if first_offset < 0 or retry_offset < 0:
        raise ValueError("frame offsets must be non-negative")
    if not first_attempt or not retry_attempt or not action:
        raise ValueError("attempt identifiers and action must be non-empty")
    finite_values = [*self_values, *cross_values]
    if first_cross_values is not None:
        finite_values.extend(first_cross_values)
    if not math.isfinite(threshold) or any(
        not math.isfinite(value) for value in finite_values
    ):
        raise ValueError("threshold and SSIM values must be finite")
    reference_self_sha256 = reference_self_sha256 or _values_sha256(self_values)
    first_cross_values = first_cross_values or []
    first_cross_sha256 = first_cross_sha256 or _values_sha256(first_cross_values)
    retry_cross_sha256 = retry_cross_sha256 or _values_sha256(cross_values)
    first_capture_receipt = first_capture_receipt or {}
    retry_capture_receipt = retry_capture_receipt or {}
    selector = action.split(":", 1)[1] if ":" in action else action

    row_count_matches = (
        len(self_values) == expected_rows
        and len(first_cross_values) == expected_rows
        and len(cross_values) == expected_rows
    )
    self_failure_rows = _failure_rows(self_values, threshold)
    first_cross_failure_rows = _failure_rows(first_cross_values, threshold)
    cross_failure_rows = _failure_rows(cross_values, threshold)
    row_counts_cover_window = _row_counts_cover_expected_window(
        reference_self_rows=len(self_values),
        first_cross_failure_rows=first_cross_failure_rows,
        first_cross_rows=len(first_cross_values),
        retry_cross_failure_rows=cross_failure_rows,
        retry_cross_rows=len(cross_values),
        expected_rows=expected_rows,
    )
    self_failures = len(self_failure_rows)
    cross_failures = len(cross_failure_rows)
    self_block_ok = (
        _contiguous_early_block(self_failure_rows)
        and self_failure_rows[-1] < expected_rows
    )
    cross_subset_ok = bool(cross_failure_rows) and set(cross_failure_rows).issubset(
        set(self_failure_rows)
    )
    first_cross_subset_ok = bool(first_cross_failure_rows) and set(
        first_cross_failure_rows
    ).issubset(set(self_failure_rows))
    cross_post_block_ok = all(
        value >= threshold
        for index, value in enumerate(cross_values, start=1)
        if not self_failure_rows or index > self_failure_rows[-1]
    )
    first_cross_post_block_ok = all(
        value >= threshold
        for index, value in enumerate(first_cross_values, start=1)
        if not self_failure_rows or index > self_failure_rows[-1]
    )
    receipts_ok = (
        _valid_early_window_receipt(
            first_capture_receipt,
            threshold=threshold,
            expected_rows=expected_rows,
            actual_rows=len(first_cross_values),
            selector=selector,
            expected_failure_rows=first_cross_failure_rows,
        )
        and _valid_early_window_receipt(
            retry_capture_receipt,
            threshold=threshold,
            expected_rows=expected_rows,
            actual_rows=len(cross_values),
            selector=selector,
            expected_failure_rows=cross_failure_rows,
        )
        and (
            _arc_within_tolerance(first_capture_receipt)
            or _arc_within_tolerance(retry_capture_receipt)
        )
    )
    passed = (
        row_counts_cover_window
        and self_block_ok
        and first_cross_subset_ok
        and cross_subset_ok
        and first_cross_post_block_ok
        and cross_post_block_ok
        and receipts_ok
    )

    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": (
            "pass-after-reference-self-calibration"
            if passed
            else "reference-self-calibration-failed"
        ),
        "rule": RULE,
        "threshold": threshold,
        "epsilon": epsilon,
        "expectedRows": expected_rows,
        "attempts": {
            "first": {"id": first_attempt, "offset": first_offset},
            "retry": {"id": retry_attempt, "offset": retry_offset},
        },
        "action": action,
        "series": {
            "referenceSelf": {
                "sha256": reference_self_sha256,
                "rows": len(self_values),
            },
            "firstCross": {
                "sha256": first_cross_sha256,
                "rows": len(first_cross_values),
            },
            "retryCross": {
                "sha256": retry_cross_sha256,
                "rows": len(cross_values),
            },
        },
        "receipts": {
            "firstCaptureRetry": {
                "sha256": first_capture_receipt_sha256,
                "payload": _receipt_summary(first_capture_receipt),
            },
            "retryCaptureRetry": {
                "sha256": retry_capture_receipt_sha256,
                "payload": _receipt_summary(retry_capture_receipt),
            },
        },
        "metrics": {
            "referenceSelf": {
                "rows": len(self_values),
                "failures": self_failures,
                "failureRows": self_failure_rows,
                "minSsim": min(self_values),
                "contiguousEarlyBlock": self_block_ok,
            },
            "firstCross": {
                "rows": len(first_cross_values),
                "failures": len(first_cross_failure_rows),
                "failureRows": first_cross_failure_rows,
                "minSsim": min(first_cross_values) if first_cross_values else None,
                "failureRowsSubsetOfReferenceSelf": first_cross_subset_ok,
                "postReferenceSelfBlockPassing": first_cross_post_block_ok,
            },
            "retryCross": {
                "rows": len(cross_values),
                "failures": cross_failures,
                "failureRows": cross_failure_rows,
                "minSsim": min(cross_values),
                "failureRowsSubsetOfReferenceSelf": cross_subset_ok,
                "postReferenceSelfBlockPassing": cross_post_block_ok,
            },
            "rowCountsMatchExpected": row_count_matches,
            "rowCountsCoverExpectedWindow": row_counts_cover_window,
            "captureReceiptsValid": receipts_ok,
        },
    }


def calibrate_complementary(
    self_values: list[float],
    retry_cross_values: list[float],
    *,
    threshold: float,
    expected_rows: int,
    first_offset: int = 0,
    retry_offset: int = 0,
    first_attempt: str = "first",
    retry_attempt: str = "retry",
    action: str = "hover:.target",
    reference_self_sha256: str = "",
    first_cross_sha256: str = "",
    retry_cross_sha256: str = "",
    first_cross_values: list[float] | None = None,
    first_capture_receipt: dict[str, Any] | None = None,
    retry_capture_receipt: dict[str, Any] | None = None,
    first_capture_receipt_sha256: str = "",
    retry_capture_receipt_sha256: str = "",
    target_payloads: dict[str, dict[str, Any]] | None = None,
    target_payload_sha256: dict[str, str] | None = None,
    trigger_type: str = "",
    provenance: str = "",
    epsilon: float = EPSILON,
) -> dict[str, Any]:
    """Accept one early-window receipt complemented by one arc-only receipt."""
    if not self_values or not retry_cross_values:
        raise ValueError("reference-self and retry-cross SSIM series must be non-empty")
    if first_cross_values is not None and not first_cross_values:
        raise ValueError("first-cross SSIM series must be non-empty")
    if expected_rows <= 0:
        raise ValueError("expected_rows must be positive")
    if first_offset < 0 or retry_offset < 0:
        raise ValueError("frame offsets must be non-negative")
    if not first_attempt or not retry_attempt or not action:
        raise ValueError("attempt identifiers and action must be non-empty")
    first_cross_values = first_cross_values or []
    finite_values = [*self_values, *first_cross_values, *retry_cross_values]
    if not math.isfinite(threshold) or any(
        not math.isfinite(value) for value in finite_values
    ):
        raise ValueError("threshold and SSIM values must be finite")

    first_capture_receipt = first_capture_receipt or {}
    retry_capture_receipt = retry_capture_receipt or {}
    selector = action.split(":", 1)[1] if ":" in action else action
    reference_self_sha256 = reference_self_sha256 or _values_sha256(self_values)
    first_cross_sha256 = first_cross_sha256 or _values_sha256(first_cross_values)
    retry_cross_sha256 = retry_cross_sha256 or _values_sha256(retry_cross_values)

    row_count_matches = (
        len(self_values) == expected_rows
        and len(first_cross_values) == expected_rows
        and len(retry_cross_values) == expected_rows
    )
    self_failure_rows = _failure_rows(self_values, threshold)
    first_cross_failure_rows = _failure_rows(first_cross_values, threshold)
    retry_cross_failure_rows = _failure_rows(retry_cross_values, threshold)
    row_counts_cover_window = _row_counts_cover_expected_window(
        reference_self_rows=len(self_values),
        first_cross_failure_rows=first_cross_failure_rows,
        first_cross_rows=len(first_cross_values),
        retry_cross_failure_rows=retry_cross_failure_rows,
        retry_cross_rows=len(retry_cross_values),
        expected_rows=expected_rows,
    )
    self_block_ok = (
        _contiguous_early_block(self_failure_rows)
        and self_failure_rows[-1] < expected_rows
    )

    receipt_pairs = [
        ("first", first_capture_receipt, first_cross_values, first_cross_failure_rows),
        ("retry", retry_capture_receipt, retry_cross_values, retry_cross_failure_rows),
    ]
    early_candidates = [
        item for item in receipt_pairs if item[1].get("reason") == "early-window-capture-phase"
    ]
    arc_candidates = [
        item for item in receipt_pairs if item[1].get("reason") == "arc-only-capture-jitter"
    ]
    exactly_mixed = len(early_candidates) == 1 and len(arc_candidates) == 1
    early_side = early_candidates[0][0] if exactly_mixed else ""
    arc_only_side = arc_candidates[0][0] if exactly_mixed else ""
    early_receipt = early_candidates[0][1] if exactly_mixed else {}
    early_values = early_candidates[0][2] if exactly_mixed else []
    early_failure_rows = early_candidates[0][3] if exactly_mixed else []
    arc_receipt = arc_candidates[0][1] if exactly_mixed else {}
    arc_values = arc_candidates[0][2] if exactly_mixed else []
    arc_failure_rows = arc_candidates[0][3] if exactly_mixed else []

    early_subset_ok = bool(early_failure_rows) and set(early_failure_rows).issubset(
        set(self_failure_rows)
    )
    early_post_block_ok = all(
        value >= threshold
        for index, value in enumerate(early_values, start=1)
        if not self_failure_rows or index > self_failure_rows[-1]
    )
    arc_only_pixels_ok = (
        not arc_failure_rows
        and bool(arc_values)
        and all(value >= threshold for value in arc_values)
    )
    early_receipt_ok = exactly_mixed and _valid_early_window_receipt(
        early_receipt,
        threshold=threshold,
        expected_rows=expected_rows,
        actual_rows=len(early_values),
        selector=selector,
        expected_failure_rows=early_failure_rows,
    )
    arc_receipt_ok = exactly_mixed and _valid_arc_only_receipt(
        arc_receipt,
        threshold=threshold,
        expected_rows=expected_rows,
        actual_rows=len(arc_values),
        selector=selector,
        expected_min_ssim=min(arc_values) if arc_values else math.inf,
        epsilon=epsilon,
    )
    arc_timing_ok = (
        exactly_mixed
        and _arc_within_tolerance(early_receipt)
        and not _arc_within_tolerance(arc_receipt)
    )
    target_payloads = target_payloads or {}
    target_payload_sha256 = target_payload_sha256 or {}
    target_payloads_ok, target_payload_summary = _valid_target_payloads(
        target_payloads,
        selector=selector,
    )
    provenance_ok = trigger_type in {"css-hover", "synth-hover-css"}
    provenance_payload = {
        "triggerType": trigger_type,
        "provenance": provenance or trigger_type,
    }
    early_arc = _arc_timing_summary(early_receipt)
    arc_only_arc = _arc_timing_summary(arc_receipt)
    arc_drift_ok = False
    if early_arc is not None and arc_only_arc is not None:
        max_delta = max(early_arc["maxDeltaFrames"], arc_only_arc["maxDeltaFrames"])
        ref_drift = abs(early_arc["refDuration"] - arc_only_arc["refDuration"])
        impl_drift = abs(early_arc["implDuration"] - arc_only_arc["implDuration"])
        signed_delta_drift = abs(early_arc["signedDelta"] - arc_only_arc["signedDelta"])
        arc_drift_ok = (
            ref_drift <= 2 * max_delta
            and impl_drift <= max_delta
            and signed_delta_drift <= 2 * max_delta
        )
    else:
        max_delta = 0
        ref_drift = 0
        impl_drift = 0
        signed_delta_drift = 0
    passed = (
        row_counts_cover_window
        and self_block_ok
        and exactly_mixed
        and early_subset_ok
        and early_post_block_ok
        and arc_only_pixels_ok
        and early_receipt_ok
        and arc_receipt_ok
        and arc_timing_ok
        and target_payloads_ok
        and provenance_ok
        and arc_drift_ok
    )

    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": (
            "pass-after-complementary-reference-self-calibration"
            if passed
            else "complementary-reference-self-calibration-failed"
        ),
        "rule": COMPLEMENTARY_RULE,
        "threshold": threshold,
        "epsilon": epsilon,
        "expectedRows": expected_rows,
        "attempts": {
            "first": {"id": first_attempt, "offset": first_offset},
            "retry": {"id": retry_attempt, "offset": retry_offset},
        },
        "action": action,
        "series": {
            "referenceSelf": {
                "sha256": reference_self_sha256,
                "rows": len(self_values),
            },
            "firstCross": {
                "sha256": first_cross_sha256,
                "rows": len(first_cross_values),
            },
            "retryCross": {
                "sha256": retry_cross_sha256,
                "rows": len(retry_cross_values),
            },
        },
        "receipts": {
            "firstCaptureRetry": {
                "sha256": first_capture_receipt_sha256,
                "payload": _receipt_summary(first_capture_receipt),
            },
            "retryCaptureRetry": {
                "sha256": retry_capture_receipt_sha256,
                "payload": _receipt_summary(retry_capture_receipt),
            },
        },
        "targets": {
            name: {
                "sha256": target_payload_sha256.get(name, ""),
                "payload": target_payload_summary.get("summaries", {}).get(name),
            }
            for name in ("firstRef", "firstImpl", "retryRef", "retryImpl")
        },
        "provenance": provenance_payload,
        "metrics": {
            "referenceSelf": {
                "rows": len(self_values),
                "failures": len(self_failure_rows),
                "failureRows": self_failure_rows,
                "minSsim": min(self_values),
                "contiguousEarlyBlock": self_block_ok,
            },
            "firstCross": {
                "rows": len(first_cross_values),
                "failures": len(first_cross_failure_rows),
                "failureRows": first_cross_failure_rows,
                "minSsim": min(first_cross_values) if first_cross_values else None,
            },
            "retryCross": {
                "rows": len(retry_cross_values),
                "failures": len(retry_cross_failure_rows),
                "failureRows": retry_cross_failure_rows,
                "minSsim": min(retry_cross_values),
            },
            "rowCountsMatchExpected": row_count_matches,
            "rowCountsCoverExpectedWindow": row_counts_cover_window,
            "exactlyMixedReceipts": exactly_mixed,
            "earlySide": early_side,
            "arcOnlySide": arc_only_side,
            "earlyFailureRowsSubsetOfReferenceSelf": early_subset_ok,
            "earlyPostReferenceSelfBlockPassing": early_post_block_ok,
            "arcOnlyPixelsPassing": arc_only_pixels_ok,
            "captureReceiptsValid": early_receipt_ok and arc_receipt_ok,
            "arcTimingComplementary": arc_timing_ok,
            "targetPayloadsValid": target_payloads_ok,
            "targetPayloadReason": target_payload_summary.get("reason", "ok"),
            "provenanceValid": provenance_ok,
            "arcDriftWithinBounds": arc_drift_ok,
            "arcDrift": {
                "maxDeltaFrames": max_delta,
                "refDurationDrift": ref_drift,
                "implDurationDrift": impl_drift,
                "signedDeltaDrift": signed_delta_drift,
            },
        },
    }


def calibrate_static_discrete(
    self_values: list[float],
    retry_cross_values: list[float],
    *,
    threshold: float,
    expected_rows: int,
    first_offset: int = 0,
    retry_offset: int = 0,
    first_attempt: str = "first",
    retry_attempt: str = "retry",
    action: str = "hover:.target",
    reference_self_sha256: str = "",
    first_cross_sha256: str = "",
    retry_cross_sha256: str = "",
    first_cross_values: list[float] | None = None,
    first_capture_receipt: dict[str, Any] | None = None,
    retry_capture_receipt: dict[str, Any] | None = None,
    reference_self_receipt: dict[str, Any] | None = None,
    first_capture_receipt_sha256: str = "",
    retry_capture_receipt_sha256: str = "",
    reference_self_receipt_sha256: str = "",
    target_payloads: dict[str, dict[str, Any]] | None = None,
    target_payload_sha256: dict[str, str] | None = None,
    action_payloads: dict[str, dict[str, Any]] | None = None,
    action_payload_sha256: dict[str, str] | None = None,
    source_metadata: dict[str, dict[str, dict[str, Any]]] | None = None,
    source_metadata_sha256: dict[str, dict[str, str]] | None = None,
    epsilon: float = EPSILON,
) -> dict[str, Any]:
    """Accept a static/discrete hover state when source-bin phase proves capture noise."""
    if not self_values or not retry_cross_values:
        raise ValueError("reference-self and retry-cross SSIM series must be non-empty")
    if first_cross_values is not None and not first_cross_values:
        raise ValueError("first-cross SSIM series must be non-empty")
    if expected_rows <= 0:
        raise ValueError("expected_rows must be positive")
    if first_offset < 0 or retry_offset < 0:
        raise ValueError("frame offsets must be non-negative")
    if not first_attempt or not retry_attempt or not action:
        raise ValueError("attempt identifiers and action must be non-empty")
    first_cross_values = first_cross_values or []
    finite_values = [*self_values, *first_cross_values, *retry_cross_values]
    if not math.isfinite(threshold) or any(
        not math.isfinite(value) for value in finite_values
    ):
        raise ValueError("threshold and SSIM values must be finite")
    selector = action.split(":", 1)[1] if ":" in action else action
    reference_self_sha256 = reference_self_sha256 or _values_sha256(self_values)
    first_cross_sha256 = first_cross_sha256 or _values_sha256(first_cross_values)
    retry_cross_sha256 = retry_cross_sha256 or _values_sha256(retry_cross_values)
    first_capture_receipt = first_capture_receipt or {}
    retry_capture_receipt = retry_capture_receipt or {}
    reference_self_receipt = reference_self_receipt or {}
    target_payloads = target_payloads or {}
    target_payload_sha256 = target_payload_sha256 or {}
    action_payloads = action_payloads or {}
    action_payload_sha256 = action_payload_sha256 or {}
    source_metadata = source_metadata or {}
    source_metadata_sha256 = source_metadata_sha256 or {}

    self_failure_rows = _failure_rows(self_values, threshold)
    first_cross_failure_rows = _failure_rows(first_cross_values, threshold)
    retry_cross_failure_rows = _failure_rows(retry_cross_values, threshold)
    row_counts_cover_window = _row_counts_cover_expected_window(
        reference_self_rows=len(self_values),
        first_cross_failure_rows=first_cross_failure_rows,
        first_cross_rows=len(first_cross_values),
        retry_cross_failure_rows=retry_cross_failure_rows,
        retry_cross_rows=len(retry_cross_values),
        expected_rows=expected_rows,
    )
    self_failures_inside_window = bool(self_failure_rows) and all(
        row <= expected_rows for row in self_failure_rows
    )

    source_expected_by_attempt = {
        attempt: {
            side: {
                "sha256": source_metadata_sha256.get(attempt, {}).get(side, ""),
                "payload": source_metadata.get(attempt, {}).get(side),
            }
            for side in ("ref", "impl")
        }
        for attempt in ("first", "retry")
    }
    first_source_ok, first_source = _valid_source_metadata(
        first_capture_receipt,
        source_expected_by_attempt["first"],
    )
    retry_source_ok, retry_source = _valid_source_metadata(
        retry_capture_receipt,
        source_expected_by_attempt["retry"],
    )
    ratio = (
        first_source.get("ratio")
        if first_source_ok and first_source.get("ratio") == retry_source.get("ratio")
        else None
    )
    source_metadata_ok = (
        first_source_ok
        and retry_source_ok
        and isinstance(ratio, int)
        and not isinstance(ratio, bool)
    )
    source_ratio = ratio if source_metadata_ok and isinstance(ratio, int) else 1
    first_self_source_bins = (
        _source_bins(self_failure_rows, source_ratio, first_offset)
        if source_metadata_ok
        else []
    )
    retry_self_source_bins = (
        _source_bins(self_failure_rows, source_ratio, retry_offset)
        if source_metadata_ok
        else []
    )
    self_source_bins = sorted({*first_self_source_bins, *retry_self_source_bins})
    first_source_bins = (
        _source_bins(first_cross_failure_rows, source_ratio, first_offset)
        if source_metadata_ok
        else []
    )
    retry_source_bins = (
        _source_bins(retry_cross_failure_rows, source_ratio, retry_offset)
        if source_metadata_ok
        else []
    )
    source_bin_subset_ok = (
        bool(first_self_source_bins)
        and bool(retry_self_source_bins)
        and bool(first_source_bins)
        and bool(retry_source_bins)
        and first_self_source_bins
        == list(range(first_self_source_bins[0], first_self_source_bins[-1] + 1))
        and retry_self_source_bins
        == list(range(retry_self_source_bins[0], retry_self_source_bins[-1] + 1))
        and set(first_source_bins).issubset(set(first_self_source_bins))
        and set(retry_source_bins).issubset(set(retry_self_source_bins))
    )
    tail_rows_ok = all(
        value >= threshold
        for index, value in enumerate(first_cross_values, start=1)
        if source_metadata_ok
        and (((first_offset + index - 1) // source_ratio) + 1)
        not in first_self_source_bins
    ) and all(
        value >= threshold
        for index, value in enumerate(retry_cross_values, start=1)
        if source_metadata_ok
        and (((retry_offset + index - 1) // source_ratio) + 1)
        not in retry_self_source_bins
    )
    receipts_ok = (
        _valid_early_window_receipt(
            first_capture_receipt,
            threshold=threshold,
            expected_rows=expected_rows,
            actual_rows=len(first_cross_values),
            selector=selector,
            expected_failure_rows=first_cross_failure_rows,
            allow_expected_window_boundary=True,
        )
        and _valid_early_window_receipt(
            retry_capture_receipt,
            threshold=threshold,
            expected_rows=expected_rows,
            actual_rows=len(retry_cross_values),
            selector=selector,
            expected_failure_rows=retry_cross_failure_rows,
            allow_expected_window_boundary=True,
        )
    )
    runtime_capture_receipts_ok = (
        _valid_runtime_early_window_receipt(
            first_capture_receipt,
            threshold=threshold,
            actual_rows=len(first_cross_values),
            selector=selector,
            expected_failure_rows=first_cross_failure_rows,
            source=first_source,
        )
        and _valid_runtime_early_window_receipt(
            retry_capture_receipt,
            threshold=threshold,
            actual_rows=len(retry_cross_values),
            selector=selector,
            expected_failure_rows=retry_cross_failure_rows,
            source=retry_source,
        )
    )
    runtime_row_count_drift_ok = _valid_runtime_row_count_drift(
        reference_self_rows=len(self_values),
        first_cross_rows=len(first_cross_values),
        retry_cross_rows=len(retry_cross_values),
        expected_rows=expected_rows,
        ratio=ratio if isinstance(ratio, int) and not isinstance(ratio, bool) else None,
        first_early_window_rows=first_capture_receipt.get("earlyWindowRows"),
        retry_early_window_rows=retry_capture_receipt.get("earlyWindowRows"),
    )
    state_ok, state_summary = _valid_state_proof(
        target_payloads=target_payloads,
        action_payloads=action_payloads,
        selector=selector,
    )
    runtime_timing_ok, runtime_timing_summary = _valid_hover_runtime_proof(
        target_payloads=target_payloads,
        action_payloads=action_payloads,
        state_summary=state_summary,
        selector=selector,
    )
    runtime_cross_source_bins_ok, runtime_cross_source_bins_summary = (
        _runtime_cross_source_bin_shape(
            first_source_bins,
            retry_source_bins,
            source_ratio,
        )
    )
    arc_ok, arc_summary = _arc_explained_by_reference_self(
        first_capture_receipt,
        retry_capture_receipt,
        reference_self_receipt,
    )
    reference_self_receipt_ok = (
        reference_self_receipt.get("status") == "reference-self-calibration-failed"
        and isinstance(reference_self_receipt.get("metrics"), dict)
        and reference_self_receipt["metrics"].get("referenceSelf", {}).get("failureRows")
        == self_failure_rows
    )
    early_window_rows_bound = (
        isinstance(first_capture_receipt.get("earlyWindowRows"), int)
        and isinstance(retry_capture_receipt.get("earlyWindowRows"), int)
        and all(row <= first_capture_receipt["earlyWindowRows"] for row in first_cross_failure_rows)
        and all(row <= retry_capture_receipt["earlyWindowRows"] for row in retry_cross_failure_rows)
    )
    early_window_tail_rows_ok = (
        isinstance(first_capture_receipt.get("earlyWindowRows"), int)
        and isinstance(retry_capture_receipt.get("earlyWindowRows"), int)
        and all(
            value >= threshold
            for index, value in enumerate(first_cross_values, start=1)
            if index > first_capture_receipt["earlyWindowRows"]
        )
        and all(
            value >= threshold
            for index, value in enumerate(retry_cross_values, start=1)
            if index > retry_capture_receipt["earlyWindowRows"]
        )
    )
    base_gates_pass = (
        row_counts_cover_window
        and self_failures_inside_window
        and receipts_ok
        and early_window_rows_bound
        and source_metadata_ok
        and state_ok
        and reference_self_receipt_ok
    )
    strict_trio_ok = source_bin_subset_ok and tail_rows_ok and arc_ok
    strict_static_pass = base_gates_pass and strict_trio_ok
    self_clean_or_failures_inside_window = self_failures_inside_window or not self_failure_rows
    runtime_timing_relaxation_used = (
        self_clean_or_failures_inside_window
        and runtime_capture_receipts_ok
        and runtime_row_count_drift_ok
        and early_window_rows_bound
        and source_metadata_ok
        and state_ok
        and reference_self_receipt_ok
        and early_window_tail_rows_ok
        and not strict_trio_ok
        and runtime_timing_ok
        and runtime_cross_source_bins_ok
    )
    passed = strict_static_pass or runtime_timing_relaxation_used
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": (
            "pass-after-static-discrete-hover-state-calibration"
            if passed
            else "static-discrete-hover-state-calibration-failed"
        ),
        "rule": STATIC_DISCRETE_RULE,
        "threshold": threshold,
        "epsilon": epsilon,
        "expectedRows": expected_rows,
        "attempts": {
            "first": {"id": first_attempt, "offset": first_offset},
            "retry": {"id": retry_attempt, "offset": retry_offset},
        },
        "action": action,
        "series": {
            "referenceSelf": {"sha256": reference_self_sha256, "rows": len(self_values)},
            "firstCross": {"sha256": first_cross_sha256, "rows": len(first_cross_values)},
            "retryCross": {"sha256": retry_cross_sha256, "rows": len(retry_cross_values)},
        },
        "receipts": {
            "firstCaptureRetry": {
                "sha256": first_capture_receipt_sha256,
                "payload": _receipt_summary(first_capture_receipt),
            },
            "retryCaptureRetry": {
                "sha256": retry_capture_receipt_sha256,
                "payload": _receipt_summary(retry_capture_receipt),
            },
            "referenceSelf": {
                "sha256": reference_self_receipt_sha256,
                "status": reference_self_receipt.get("status"),
                "rule": reference_self_receipt.get("rule"),
            },
        },
        "targets": {
            name: {
                "sha256": target_payload_sha256.get(name, ""),
                "payload": _target_summary(_unwrap_json(target_payloads.get(name, {}))),
            }
            for name in ("firstRef", "firstImpl", "retryRef", "retryImpl")
        },
        "actions": {
            name: {
                "sha256": action_payload_sha256.get(name, ""),
                "payload": _target_summary(_unwrap_json(action_payloads.get(name, {}))),
            }
            for name in ("firstRef", "firstImpl", "retryRef", "retryImpl")
        },
        "sourceMetadata": source_expected_by_attempt,
        "metrics": {
            "referenceSelf": {
                "rows": len(self_values),
                "failureRows": self_failure_rows,
                "sourceBins": self_source_bins,
                "sourceBinsByAttempt": {
                    "first": first_self_source_bins,
                    "retry": retry_self_source_bins,
                },
                "failuresInsideExpectedWindow": self_failures_inside_window,
                "contiguousSourceBins": (
                    bool(first_self_source_bins)
                    and bool(retry_self_source_bins)
                    and first_self_source_bins
                    == list(range(first_self_source_bins[0], first_self_source_bins[-1] + 1))
                    and retry_self_source_bins
                    == list(range(retry_self_source_bins[0], retry_self_source_bins[-1] + 1))
                ),
            },
            "firstCross": {
                "rows": len(first_cross_values),
                "failureRows": first_cross_failure_rows,
                "sourceBins": first_source_bins,
            },
            "retryCross": {
                "rows": len(retry_cross_values),
                "failureRows": retry_cross_failure_rows,
                "sourceBins": retry_source_bins,
            },
            "rowCountsCoverExpectedWindow": row_counts_cover_window,
            "captureReceiptsValid": receipts_ok,
            "runtimeCaptureReceiptsValid": runtime_capture_receipts_ok,
            "runtimeRowCountDriftValid": runtime_row_count_drift_ok,
            "earlyWindowRowsBoundFailureRows": early_window_rows_bound,
            "earlyWindowTailRowsPassing": early_window_tail_rows_ok,
            "sourceMetadataValid": source_metadata_ok,
            "sourceBinSubsetOfReferenceSelf": source_bin_subset_ok,
            "tailRowsPassingOutsideReferenceSelfBins": tail_rows_ok,
            "statePayloadsValid": state_ok,
            "statePayloadReason": state_summary.get("reason"),
            "state": state_summary,
            "referenceSelfStandardFailed": reference_self_receipt_ok,
            "referenceSelfCleanOrFailuresInsideExpectedWindow": (
                self_clean_or_failures_inside_window
            ),
            "arcExplained": arc_ok,
            "arc": arc_summary,
            "runtimeTimingProofValid": runtime_timing_ok,
            "runtimeTimingProof": runtime_timing_summary,
            "runtimeCrossSourceBinsValid": runtime_cross_source_bins_ok,
            "runtimeCrossSourceBins": runtime_cross_source_bins_summary,
            "runtimeTimingRelaxationUsed": runtime_timing_relaxation_used,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-self-series", type=Path, required=True)
    parser.add_argument("--first-cross-series", type=Path, required=True)
    parser.add_argument("--retry-cross-series", type=Path, required=True)
    parser.add_argument("--threshold", type=float, required=True)
    parser.add_argument("--expected-rows", type=int, required=True)
    parser.add_argument("--first-offset", type=int, required=True)
    parser.add_argument("--retry-offset", type=int, required=True)
    parser.add_argument("--first-attempt", required=True)
    parser.add_argument("--retry-attempt", required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--first-capture-retry", type=Path, required=True)
    parser.add_argument("--retry-capture-retry", type=Path, required=True)
    parser.add_argument("--first-ref-target", type=Path)
    parser.add_argument("--first-impl-target", type=Path)
    parser.add_argument("--retry-ref-target", type=Path)
    parser.add_argument("--retry-impl-target", type=Path)
    parser.add_argument("--first-ref-action", type=Path)
    parser.add_argument("--first-impl-action", type=Path)
    parser.add_argument("--retry-ref-action", type=Path)
    parser.add_argument("--retry-impl-action", type=Path)
    parser.add_argument("--first-ref-source-metadata", type=Path)
    parser.add_argument("--first-impl-source-metadata", type=Path)
    parser.add_argument("--retry-ref-source-metadata", type=Path)
    parser.add_argument("--retry-impl-source-metadata", type=Path)
    parser.add_argument("--standard-calibration-receipt", type=Path)
    parser.add_argument("--trigger-type", default="")
    parser.add_argument("--provenance", default="")
    parser.add_argument("--complementary", action="store_true")
    parser.add_argument("--static-discrete", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    try:
        common_kwargs = {
            "threshold": args.threshold,
            "expected_rows": args.expected_rows,
            "first_offset": args.first_offset,
            "retry_offset": args.retry_offset,
            "first_attempt": args.first_attempt,
            "retry_attempt": args.retry_attempt,
            "action": args.action,
            "reference_self_sha256": _sha256(args.reference_self_series),
            "first_cross_values": _read_series(args.first_cross_series),
            "first_cross_sha256": _sha256(args.first_cross_series),
            "retry_cross_sha256": _sha256(args.retry_cross_series),
            "first_capture_receipt": json.loads(
                args.first_capture_retry.read_text(encoding="utf-8")
            ),
            "retry_capture_receipt": json.loads(
                args.retry_capture_retry.read_text(encoding="utf-8")
            ),
            "first_capture_receipt_sha256": _sha256(args.first_capture_retry),
            "retry_capture_receipt_sha256": _sha256(args.retry_capture_retry),
        }
        if args.static_discrete and args.complementary:
            raise ValueError("--static-discrete and --complementary are mutually exclusive")
        calibration: Any
        if args.static_discrete:
            calibration = calibrate_static_discrete
        elif args.complementary:
            calibration = calibrate_complementary
        else:
            calibration = calibrate_distributions
        if args.complementary or args.static_discrete:
            target_paths = {
                "firstRef": args.first_ref_target,
                "firstImpl": args.first_impl_target,
                "retryRef": args.retry_ref_target,
                "retryImpl": args.retry_impl_target,
            }
            if any(path is None for path in target_paths.values()):
                raise ValueError("complementary calibration requires all target payloads")
            common_kwargs["target_payloads"] = {
                name: _read_target_payload(path)
                for name, path in target_paths.items()
                if path is not None
            }
            common_kwargs["target_payload_sha256"] = {
                name: _sha256(path)
                for name, path in target_paths.items()
                if path is not None
            }
            if args.complementary:
                common_kwargs["trigger_type"] = args.trigger_type
                common_kwargs["provenance"] = args.provenance or args.trigger_type
        if args.static_discrete:
            action_paths = {
                "firstRef": args.first_ref_action,
                "firstImpl": args.first_impl_action,
                "retryRef": args.retry_ref_action,
                "retryImpl": args.retry_impl_action,
            }
            source_paths = {
                "first": {
                    "ref": args.first_ref_source_metadata,
                    "impl": args.first_impl_source_metadata,
                },
                "retry": {
                    "ref": args.retry_ref_source_metadata,
                    "impl": args.retry_impl_source_metadata,
                },
            }
            if any(path is None for path in action_paths.values()):
                raise ValueError("static-discrete calibration requires all action payloads")
            if any(path is None for sides in source_paths.values() for path in sides.values()):
                raise ValueError("static-discrete calibration requires all source metadata")
            if args.standard_calibration_receipt is None:
                raise ValueError("static-discrete calibration requires standard receipt")
            common_kwargs["action_payloads"] = {
                name: _read_target_payload(path)
                for name, path in action_paths.items()
                if path is not None
            }
            common_kwargs["action_payload_sha256"] = {
                name: _sha256(path)
                for name, path in action_paths.items()
                if path is not None
            }
            common_kwargs["source_metadata"] = {
                attempt: {
                    side: json.loads(path.read_text(encoding="utf-8"))
                    for side, path in sides.items()
                    if path is not None
                }
                for attempt, sides in source_paths.items()
            }
            common_kwargs["source_metadata_sha256"] = {
                attempt: {
                    side: _sha256(path)
                    for side, path in sides.items()
                    if path is not None
                }
                for attempt, sides in source_paths.items()
            }
            common_kwargs["reference_self_receipt"] = json.loads(
                args.standard_calibration_receipt.read_text(encoding="utf-8")
            )
            common_kwargs["reference_self_receipt_sha256"] = _sha256(
                args.standard_calibration_receipt
            )
        payload = calibration(
            _read_series(args.reference_self_series),
            _read_series(args.retry_cross_series),
            **common_kwargs,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return (
        0
        if payload["status"]
        in {
            "pass-after-reference-self-calibration",
            "pass-after-complementary-reference-self-calibration",
            "pass-after-static-discrete-hover-state-calibration",
        }
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
