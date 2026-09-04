from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any

_SCHEMA_VERSION = 1
_SAMPLE_COUNT = 21
_PROPERTIES = {
    "transform",
    "opacity",
    "clipPath",
    "backgroundColor",
    "height",
    "position",
}
_POSITIONS = {"static", "relative", "absolute", "fixed", "sticky"}
_TRACK_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_SCROLL_ACTION = "scroll-action"
_SCROLL_PROGRESS = "scroll-progress"
_DRIVER_ANIMATION_PAUSE = "animation-pause"
_DRIVER_VIRTUAL_CLOCK = "virtual-clock"
_SCROLL_ACTION_TRIGGER_FIELDS = {
    "type",
    "action",
    "driver",
    "fromScrollY",
    "toScrollY",
    "denominatorMs",
    "clock",
    "readyWaitMs",
}
_CLOCK_FIELDS = {"epochMs", "anchorMs"}
_VIRTUAL_CLOCK_EPOCH_MS = 1_700_000_000_000
_SCROLL_PROGRESS_TRIGGER_FIELDS = {
    "type",
    "startPx",
    "endPx",
    "sampleDenominator",
    "transport",
    "readyWaitMs",
}
_CHANGE_EPSILON_OPACITY = 0.001
_CHANGE_EPSILON_PX = 0.1


def validate_track(data: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["track must be an object"]

    if data.get("schemaVersion") != _SCHEMA_VERSION:
        errors.append("schemaVersion must be 1")
    if not _nonempty_string(data.get("trackId")):
        errors.append("trackId must be nonempty")

    trigger = data.get("trigger")
    trigger_kind, start_px, end_px = _validate_trigger(trigger, errors)
    _validate_node(data.get("node"), errors)
    _validate_baseline(data.get("baseline"), errors)

    samples = data.get("samples")
    if not isinstance(samples, list):
        errors.append("samples must be a list")
        return errors
    if len(samples) != _SAMPLE_COUNT:
        errors.append("samples must contain exactly 21 entries")

    previous_progress: float | None = None
    changed_pair_indexes: list[int] = []
    previous_observation: dict[str, object] | None = None
    for expected_index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            errors.append(f"samples[{expected_index}] must be an object")
            previous_observation = None
            continue

        if sample.get("index") != expected_index:
            errors.append(f"samples[{expected_index}].index must be {expected_index}")

        if trigger_kind == _SCROLL_ACTION and isinstance(trigger, dict):
            _validate_scroll_action_sample(trigger, sample, expected_index, errors)
        else:
            previous_progress = _validate_scroll_progress_sample(
                sample,
                expected_index,
                start_px,
                end_px,
                previous_progress,
                errors,
            )

        properties = _validate_properties(
            sample.get("properties"),
            f"samples[{expected_index}].properties",
            errors,
        )
        box = _validate_box(sample.get("box"), f"samples[{expected_index}].box", errors)
        if trigger_kind != _SCROLL_ACTION:
            _validate_settle(
                sample.get("settle"),
                f"samples[{expected_index}].settle",
                errors,
                expected_status="settled",
            )

        observation: dict[str, object] = {}
        if properties is not None:
            observation["properties"] = properties
        if box is not None:
            observation["box"] = box
        if previous_observation is not None and _observation_changed(previous_observation, observation):
            changed_pair_indexes.append(expected_index - 1)
        previous_observation = observation if observation else None

    if changed_pair_indexes:
        change_band_samples = changed_pair_indexes[-1] - changed_pair_indexes[0] + 2
        if change_band_samples < 5:
            errors.append("change band must span at least 5 inclusive samples")
        # Span alone is satisfied by a jump-cut that moves once at each end and
        # freezes in between, which is the capture failure this gate exists to
        # reject. Require the band to be populated, not merely wide.
        elif len(changed_pair_indexes) < 4:
            errors.append(
                "change band must contain at least 4 changed sample pairs "
                f"(found {len(changed_pair_indexes)})"
            )
    else:
        errors.append("change band must include observed property or box differences")

    return errors


def build_recording_manifest(
    root: Path,
    relative_files: Sequence[str],
    *,
    browser_version: str,
    tool_version: str,
) -> dict[str, object]:
    if not browser_version:
        raise ValueError("browserVersion must be nonempty")
    if not tool_version:
        raise ValueError("toolVersion must be nonempty")

    paths = _normalize_manifest_paths(relative_files)
    entries = [{"path": path, "sha256": _file_sha256(root / path)} for path in paths]
    return {
        "schemaVersion": _SCHEMA_VERSION,
        "algorithm": "sha256",
        "browserVersion": browser_version,
        "toolVersion": tool_version,
        "files": entries,
        "rootSha256": _root_sha256(entries),
    }


def verify_recording_manifest(root: Path, manifest: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest must be an object"]

    if manifest.get("schemaVersion") != _SCHEMA_VERSION:
        errors.append("schemaVersion must be 1")
    if manifest.get("algorithm") != "sha256":
        errors.append("algorithm must be sha256")
    if not manifest.get("browserVersion"):
        errors.append("browserVersion must be nonempty")
    if not manifest.get("toolVersion"):
        errors.append("toolVersion must be nonempty")

    files = manifest.get("files")
    if not isinstance(files, list):
        errors.append("files must be a list")
        return errors
    if not files:
        # The manifest exists to bind a track to the recorder sources that
        # produced it; an empty list binds nothing while still verifying.
        errors.append("files must not be empty")
        return errors

    paths: list[str] = []
    recomputed_entries: list[dict[str, str]] = []
    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            errors.append(f"files[{index}] must be an object")
            continue
        path = entry.get("path")
        expected_sha = entry.get("sha256")
        if not isinstance(path, str):
            errors.append(f"files[{index}].path must be a string")
            continue
        try:
            normalized = _normalize_manifest_paths([path])[0]
        except ValueError as exc:
            errors.append(f"files[{index}].path invalid: {exc}")
            continue
        paths.append(normalized)
        file_path = root / normalized
        if not file_path.is_file():
            errors.append(f"{normalized} missing")
            continue
        actual_sha = _file_sha256(file_path)
        if actual_sha != expected_sha:
            errors.append(f"{normalized} sha256 mismatch")
        recomputed_entries.append({"path": normalized, "sha256": actual_sha})

    if paths != sorted(paths):
        errors.append("files must be sorted by path")
    if len(paths) != len(set(paths)):
        errors.append("files must be unique")

    expected_root = manifest.get("rootSha256")
    actual_root = _root_sha256(recomputed_entries)
    if expected_root != actual_root:
        errors.append("rootSha256 mismatch")

    return errors


def compare_tracks(
    reference: object,
    candidate: object,
    *,
    minimum_score: float,
) -> dict[str, object]:
    contract_failures: list[str] = []
    pair_failures: list[str] = []
    reference_errors = validate_track(reference)
    candidate_errors = validate_track(candidate)
    if reference_errors:
        contract_failures.extend(f"reference: {error}" for error in reference_errors)
    if candidate_errors:
        contract_failures.extend(f"candidate: {error}" for error in candidate_errors)
    minimum_score_value = _number(minimum_score)
    if minimum_score_value is None:
        contract_failures.append("minimumScore must be finite")
        minimum_score_value = 1.0
    elif minimum_score_value < 0 or minimum_score_value > 1:
        contract_failures.append("minimumScore must be within 0..1")
    elif _is_scroll_action_track(reference) or _is_scroll_action_track(candidate):
        if minimum_score_value != 1.0:
            contract_failures.append("scroll-action comparisons require minimumScore 1.0")

    if not isinstance(reference, dict) or not isinstance(candidate, dict):
        return _comparison_result(0, 0, 0.0, minimum_score_value, contract_failures, True)

    _validate_compare_contract(reference, candidate, contract_failures)

    if contract_failures:
        return _comparison_result(0, 0, 0.0, minimum_score_value, contract_failures, True)

    reference_samples = reference["samples"]
    candidate_samples = candidate["samples"]
    if not isinstance(reference_samples, list) or not isinstance(candidate_samples, list):
        return _comparison_result(0, 0, 0.0, minimum_score_value, contract_failures, True)

    matched_pairs = 0
    total_pairs = 0
    candidate_by_index = {
        sample.get("index"): sample
        for sample in candidate_samples
        if isinstance(sample, dict)
    }
    for reference_sample in reference_samples:
        if not isinstance(reference_sample, dict):
            continue
        sample_index = reference_sample.get("index")
        candidate_sample = candidate_by_index.get(sample_index)
        if not isinstance(candidate_sample, dict):
            pair_failures.append(f"samples[{sample_index}] missing in candidate")
            continue

        reference_properties = reference_sample.get("properties")
        candidate_properties = candidate_sample.get("properties")
        if isinstance(reference_properties, dict):
            for name, reference_value in reference_properties.items():
                if name not in _PROPERTIES:
                    continue
                total_pairs += 1
                if not isinstance(candidate_properties, dict) or name not in candidate_properties:
                    pair_failures.append(f"samples[{sample_index}].properties.{name} missing")
                    continue
                if _property_matches(name, reference_value, candidate_properties[name]):
                    matched_pairs += 1
                else:
                    pair_failures.append(f"samples[{sample_index}].properties.{name} mismatch")

        total_pairs += 1
        if _box_matches(reference_sample.get("box"), candidate_sample.get("box")):
            matched_pairs += 1
        else:
            pair_failures.append(f"samples[{sample_index}].box mismatch")

    score = matched_pairs / total_pairs if total_pairs else 0.0
    return _comparison_result(
        matched_pairs,
        total_pairs,
        score,
        minimum_score_value,
        pair_failures,
        False,
    )


def track_sha256(data: object) -> str:
    return hashlib.sha256(_canonical_json(_without_track_sha(data)).encode("utf-8")).hexdigest()


def _comparison_result(
    matched_pairs: int,
    total_pairs: int,
    score: float,
    minimum_score: float,
    failures: list[str],
    has_contract_failures: bool,
) -> dict[str, object]:
    status = "pass" if not has_contract_failures and score >= minimum_score else "fail"
    return {
        "matchedPairs": matched_pairs,
        "totalPairs": total_pairs,
        "score": score,
        "minimumScore": minimum_score,
        "status": status,
        "failures": failures,
    }


def _validate_trigger(
    trigger: object,
    errors: list[str],
) -> tuple[str | None, float | None, float | None]:
    if not isinstance(trigger, dict):
        errors.append("trigger must be an object")
        return None, None, None
    trigger_type = trigger.get("type")
    if trigger_type == _SCROLL_ACTION:
        _validate_known_fields(trigger, _SCROLL_ACTION_TRIGGER_FIELDS, "trigger", errors)
        _validate_ready_wait(trigger, errors)
        _validate_scroll_action_trigger(trigger, errors)
        return _SCROLL_ACTION, None, None
    if trigger_type != _SCROLL_PROGRESS:
        errors.append("trigger.type must be scroll-progress or scroll-action")
        return None, None, None
    _validate_known_fields(trigger, _SCROLL_PROGRESS_TRIGGER_FIELDS, "trigger", errors)
    _validate_ready_wait(trigger, errors)
    if "transport" in trigger and trigger.get("transport") != "lenis-wheel":
        errors.append("trigger.transport must be lenis-wheel")
    if trigger.get("sampleDenominator") != 20:
        errors.append("trigger.sampleDenominator must be 20")
    start_px = _number(trigger.get("startPx"))
    end_px = _number(trigger.get("endPx"))
    if start_px is None:
        errors.append("trigger.startPx must be finite")
        return _SCROLL_PROGRESS, None, None
    if end_px is None:
        errors.append("trigger.endPx must be finite")
        return _SCROLL_PROGRESS, start_px, None
    if end_px <= start_px:
        errors.append("trigger.endPx must be greater than startPx")
    return _SCROLL_PROGRESS, start_px, end_px


def _validate_scroll_action_trigger(trigger: dict[object, object], errors: list[str]) -> None:
    if trigger.get("action") != "scrollTo":
        errors.append("trigger.action must be scrollTo")
    driver = trigger.get("driver")
    if driver not in {_DRIVER_ANIMATION_PAUSE, _DRIVER_VIRTUAL_CLOCK}:
        errors.append("trigger.driver must be animation-pause or virtual-clock")
    from_scroll_y = trigger.get("fromScrollY")
    to_scroll_y = trigger.get("toScrollY")
    if type(from_scroll_y) is not int:
        errors.append("trigger.fromScrollY must be an integer")
    if type(to_scroll_y) is not int:
        errors.append("trigger.toScrollY must be an integer")
    if type(from_scroll_y) is int and type(to_scroll_y) is int and to_scroll_y <= from_scroll_y:
        errors.append("trigger.toScrollY must be greater than fromScrollY")
    denominator = trigger.get("denominatorMs")
    if driver == _DRIVER_VIRTUAL_CLOCK:
        if type(denominator) is not int or denominator < 320 or denominator > 4800 or denominator % 320 != 0:
            errors.append("trigger.denominatorMs must be an integer 320..4800 divisible by 320")
        _validate_virtual_clock(trigger.get("clock"), errors)
    else:
        if "clock" in trigger:
            errors.append("trigger.clock is only allowed for virtual-clock")
        if type(denominator) is not int or denominator < 50 or denominator > 5000:
            errors.append("trigger.denominatorMs must be an integer 50..5000")


def _validate_ready_wait(trigger: dict[object, object], errors: list[str]) -> None:
    if "readyWaitMs" not in trigger:
        return
    ready_wait_ms = trigger.get("readyWaitMs")
    if type(ready_wait_ms) is not int or ready_wait_ms < 0 or ready_wait_ms > 30000:
        errors.append("trigger.readyWaitMs must be an integer 0..30000")


def _validate_virtual_clock(clock: object, errors: list[str]) -> None:
    if not isinstance(clock, dict):
        errors.append("trigger.clock must be an object for virtual-clock")
        return
    _validate_known_fields(clock, _CLOCK_FIELDS, "trigger.clock", errors)
    epoch_ms = clock.get("epochMs")
    anchor_ms = clock.get("anchorMs")
    if type(epoch_ms) is not int:
        errors.append("trigger.clock.epochMs must be an integer")
    elif epoch_ms != _VIRTUAL_CLOCK_EPOCH_MS:
        errors.append(f"trigger.clock.epochMs must be {_VIRTUAL_CLOCK_EPOCH_MS}")
    if type(anchor_ms) is not int:
        errors.append("trigger.clock.anchorMs must be an integer")
    if type(epoch_ms) is int and type(anchor_ms) is int and anchor_ms <= epoch_ms:
        errors.append("trigger.clock.anchorMs must be greater than epochMs")
    if type(anchor_ms) is int and anchor_ms % 16 != 0:
        errors.append("trigger.clock.anchorMs must be divisible by 16")


def _validate_scroll_progress_sample(
    sample: dict[object, object],
    expected_index: int,
    start_px: float | None,
    end_px: float | None,
    previous_progress: float | None,
    errors: list[str],
) -> float | None:
    progress = _number(sample.get("progress"))
    if progress is None:
        errors.append(f"samples[{expected_index}].progress must be finite")
        return previous_progress
    if progress < 0 or progress > 1:
        errors.append(f"samples[{expected_index}].progress must be within 0..1")
    expected_progress = expected_index / 20
    if abs(progress - expected_progress) > 0.001:
        errors.append(f"samples[{expected_index}].progress must match sampleDenominator grid")
    if previous_progress is not None and progress < previous_progress:
        errors.append(f"samples[{expected_index}].progress must be ordered")

    scroll_y = _number(sample.get("scrollY"))
    if scroll_y is None:
        errors.append(f"samples[{expected_index}].scrollY must be finite")
    elif start_px is not None and end_px is not None:
        expected_scroll_y = round(start_px + ((end_px - start_px) * expected_index / 20), 4)
        if scroll_y < start_px or scroll_y > end_px:
            errors.append(f"samples[{expected_index}].scrollY must be within trigger range")
        if abs(scroll_y - expected_scroll_y) > 0.0001:
            errors.append(f"samples[{expected_index}].scrollY must match sampleDenominator grid")
    return progress


def _validate_scroll_action_sample(
    trigger: dict[object, object],
    sample: dict[object, object],
    expected_index: int,
    errors: list[str],
) -> None:
    denominator = trigger.get("denominatorMs")
    from_scroll_y = trigger.get("fromScrollY")
    to_scroll_y = trigger.get("toScrollY")
    if type(denominator) is not int or type(from_scroll_y) is not int or type(to_scroll_y) is not int:
        return

    elapsed = _number(sample.get("elapsedMs"))
    expected_elapsed = round(expected_index * denominator / 20, 3)
    if elapsed is None or abs(elapsed - expected_elapsed) > 0.001:
        errors.append(f"samples[{expected_index}].elapsedMs must match denominatorMs grid")

    scroll_y = sample.get("scrollY")
    if trigger.get("driver") == _DRIVER_VIRTUAL_CLOCK:
        if type(scroll_y) is not int:
            errors.append(f"samples[{expected_index}].scrollY must be an integer")
        elif expected_index == 0 and scroll_y != from_scroll_y:
            errors.append(f"samples[{expected_index}].scrollY must match scroll-action origin")
    else:
        expected_scroll_y = from_scroll_y if expected_index == 0 else to_scroll_y
        if scroll_y != expected_scroll_y:
            errors.append(f"samples[{expected_index}].scrollY must match scroll-action target")

    expected_status = "paused" if 1 <= expected_index <= 19 else "settled"
    _validate_settle(
        sample.get("settle"),
        f"samples[{expected_index}].settle",
        errors,
        expected_status=expected_status,
    )


def _validate_node(node: object, errors: list[str]) -> None:
    if not isinstance(node, dict):
        errors.append("node must be an object")
        return
    if not _nonempty_string(node.get("selector")):
        errors.append("node.selector must be nonempty")
    fingerprint = node.get("fingerprint")
    if not isinstance(fingerprint, dict) or not fingerprint:
        errors.append("node.fingerprint must be a nonempty object")


def _validate_known_fields(
    data: dict[object, object],
    allowed: set[str],
    path: str,
    errors: list[str],
) -> None:
    for key in data:
        if not isinstance(key, str) or key not in allowed:
            errors.append(f"{path}.{key} is an unknown field")


def _validate_baseline(baseline: object, errors: list[str]) -> None:
    if not isinstance(baseline, dict):
        errors.append("baseline must be an object")
        return
    if type(baseline.get("recording")) is not int or baseline.get("recording") != 1:
        errors.append("baseline.recording must be integer 1")
    track_hash = baseline.get("trackSha256")
    if not isinstance(track_hash, str) or _TRACK_SHA_RE.fullmatch(track_hash) is None:
        errors.append("baseline.trackSha256 must be 64 lowercase hex characters")


def _validate_properties(
    properties: object,
    path: str,
    errors: list[str],
) -> dict[str, object] | None:
    if not isinstance(properties, dict):
        errors.append(f"{path} must be an object")
        return None
    if not properties:
        errors.append(f"{path} must contain observed properties")
        return None

    normalized: dict[str, object] = {}
    for name, value in properties.items():
        if name not in _PROPERTIES:
            errors.append(f"{path}.{name} is not a supported property")
            continue
        normalized_value: object | None
        if name == "transform":
            normalized_value = _validate_transform(value, f"{path}.transform", errors)
        elif name == "opacity":
            normalized_value = _validate_opacity(value, f"{path}.opacity", errors)
        elif name == "clipPath":
            normalized_value = _validate_clip_path(value, f"{path}.clipPath", errors)
        elif name == "backgroundColor":
            normalized_value = _validate_background_color(value, f"{path}.backgroundColor", errors)
        elif name == "height":
            normalized_value = _validate_height(value, f"{path}.height", errors)
        else:
            normalized_value = _validate_position(value, f"{path}.position", errors)
        if normalized_value is not None:
            normalized[name] = normalized_value
    return normalized


def _validate_transform(value: object, path: str, errors: list[str]) -> dict[str, float] | None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return None
    translate_x = _number(value.get("translateX"))
    translate_y = _number(value.get("translateY"))
    if translate_x is None or translate_y is None:
        errors.append(f"{path} translateX and translateY must be finite")
        return None
    return {"translateX": translate_x, "translateY": translate_y}


def _validate_opacity(value: object, path: str, errors: list[str]) -> float | None:
    opacity = _number(value)
    if opacity is None:
        errors.append(f"{path} must be finite")
        return None
    if opacity < 0 or opacity > 1:
        errors.append(f"{path} must be within 0..1")
        return None
    return opacity


def _validate_clip_path(value: object, path: str, errors: list[str]) -> str | None:
    if not _nonempty_string(value):
        errors.append(f"{path} must be a nonempty string")
        return None
    stripped = str(value).strip()
    canonical = " ".join(stripped.split())
    if stripped != canonical:
        errors.append(f"{path} must use canonical whitespace")
        return None
    return stripped


def _validate_background_color(
    value: object,
    path: str,
    errors: list[str],
) -> list[int | float] | None:
    if not isinstance(value, list) or len(value) != 4:
        errors.append(f"{path} must be [r,g,b,a]")
        return None
    red, green, blue, alpha = value
    rgb = [red, green, blue]
    if any(type(component) is not int or component < 0 or component > 255 for component in rgb):
        errors.append(f"{path} RGB values must be integers 0..255")
        return None
    alpha_float = _number(alpha)
    if alpha_float is None:
        errors.append(f"{path} alpha must be finite")
        return None
    if alpha_float < 0 or alpha_float > 1:
        errors.append(f"{path} alpha must be within 0..1")
        return None
    return [int(red), int(green), int(blue), alpha_float]


def _validate_height(value: object, path: str, errors: list[str]) -> float | None:
    height = _number(value)
    if height is None:
        errors.append(f"{path} must be finite")
        return None
    if height < 0:
        errors.append(f"{path} must be >= 0")
        return None
    return height


def _validate_position(value: object, path: str, errors: list[str]) -> str | None:
    if value not in _POSITIONS:
        errors.append(f"{path} must be a CSS position keyword")
        return None
    return str(value)


def _validate_box(value: object, path: str, errors: list[str]) -> dict[str, float] | None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return None
    normalized: dict[str, float] = {}
    for key in ("x", "y", "width", "height"):
        raw = _number(value.get(key))
        if raw is None:
            errors.append(f"{path}.{key} must be finite")
            return None
        normalized[key] = raw
    if normalized["width"] <= 0:
        errors.append(f"{path}.width must be > 0")
        return None
    if normalized["height"] <= 0:
        errors.append(f"{path}.height must be > 0")
        return None
    return normalized


def _validate_settle(
    settle: object,
    path: str,
    errors: list[str],
    *,
    expected_status: str,
) -> None:
    if not isinstance(settle, dict):
        errors.append(f"{path} must be an object")
        return
    if settle.get("status") != expected_status or settle.get("frames") != 2:
        errors.append(f"{path} must be {expected_status} for exactly 2 frames")


def _property_matches(name: str, reference: object, candidate: object) -> bool:
    if name == "transform":
        if not isinstance(reference, dict) or not isinstance(candidate, dict):
            return False
        return (
            _within(reference.get("translateX"), candidate.get("translateX"), 2)
            and _within(reference.get("translateY"), candidate.get("translateY"), 2)
        )
    if name == "opacity":
        return _within(reference, candidate, 0.02)
    if name == "height":
        return _within(reference, candidate, 2)
    if name == "box":
        return _box_matches(reference, candidate)
    return reference == candidate


def _observation_changed(previous: dict[str, object], current: dict[str, object]) -> bool:
    previous_properties = previous.get("properties")
    current_properties = current.get("properties")
    if isinstance(previous_properties, dict) and isinstance(current_properties, dict):
        names = set(previous_properties) | set(current_properties)
        for name in names:
            if name not in _PROPERTIES:
                continue
            if name not in previous_properties or name not in current_properties:
                return True
            if not _band_property_matches(name, previous_properties[name], current_properties[name]):
                return True
    elif previous_properties != current_properties:
        return True
    return not _box_matches(
        previous.get("box"),
        current.get("box"),
        tolerance=_CHANGE_EPSILON_PX,
    )


def _band_property_matches(name: str, previous: object, current: object) -> bool:
    if name == "transform":
        if not isinstance(previous, dict) or not isinstance(current, dict):
            return False
        return _within(
            previous.get("translateX"),
            current.get("translateX"),
            _CHANGE_EPSILON_PX,
        ) and _within(
            previous.get("translateY"),
            current.get("translateY"),
            _CHANGE_EPSILON_PX,
        )
    if name == "opacity":
        return _within(previous, current, _CHANGE_EPSILON_OPACITY)
    if name == "height":
        return _within(previous, current, _CHANGE_EPSILON_PX)
    return previous == current


def _box_matches(
    reference: object,
    candidate: object,
    *,
    tolerance: float = 2,
) -> bool:
    if not isinstance(reference, dict) or not isinstance(candidate, dict):
        return False
    return all(
        _within(reference.get(key), candidate.get(key), tolerance)
        for key in ("x", "y", "width", "height")
    )


def _within(left: object, right: object, tolerance: float) -> bool:
    left_number = _number(left)
    right_number = _number(right)
    if left_number is None or right_number is None:
        return False
    return abs(left_number - right_number) <= tolerance


def _normalize_manifest_paths(relative_files: Sequence[str]) -> list[str]:
    paths: list[str] = []
    for raw_path in relative_files:
        if not isinstance(raw_path, str):
            raise ValueError("manifest paths must be strings")
        if not raw_path or "\\" in raw_path:
            raise ValueError(f"{raw_path!r} is not a relative POSIX path")
        path = PurePosixPath(raw_path)
        if path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
            raise ValueError(f"{raw_path!r} escapes the manifest root")
        paths.append(path.as_posix())
    if len(paths) != len(set(paths)):
        raise ValueError("manifest paths must be unique")
    return sorted(paths)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _root_sha256(entries: list[dict[str, str]]) -> str:
    return hashlib.sha256(_canonical_json(entries).encode("utf-8")).hexdigest()


def _canonical_json(data: object) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _without_track_sha(data: object) -> object:
    clone = deepcopy(data)
    if isinstance(clone, dict):
        baseline = clone.get("baseline")
        if isinstance(baseline, dict):
            baseline.pop("trackSha256", None)
    return clone


def _validate_compare_contract(
    reference: dict[object, object],
    candidate: dict[object, object],
    errors: list[str],
) -> None:
    if reference.get("trackId") != candidate.get("trackId"):
        errors.append("trackId must match")
    if reference.get("trigger") != candidate.get("trigger"):
        errors.append("trigger must match")
    _validate_node_identity_match(reference.get("node"), candidate.get("node"), errors)

    reference_hash = track_sha256(reference)
    reference_baseline = reference.get("baseline")
    candidate_baseline = candidate.get("baseline")
    if not isinstance(reference_baseline, dict) or reference_baseline.get("trackSha256") != reference_hash:
        errors.append("reference baseline.trackSha256 must match reference track hash")
    if not isinstance(candidate_baseline, dict) or candidate_baseline.get("trackSha256") != reference_hash:
        errors.append("candidate baseline.trackSha256 must match reference track hash")

    reference_samples = reference.get("samples")
    candidate_samples = candidate.get("samples")
    if not isinstance(reference_samples, list) or not isinstance(candidate_samples, list):
        return
    candidate_by_index = {
        sample.get("index"): sample
        for sample in candidate_samples
        if isinstance(sample, dict)
    }
    for reference_sample in reference_samples:
        if not isinstance(reference_sample, dict):
            continue
        sample_index = reference_sample.get("index")
        candidate_sample = candidate_by_index.get(sample_index)
        if not isinstance(candidate_sample, dict):
            errors.append(f"samples[{sample_index}] missing in candidate")
            continue
        if reference_sample.get("progress") != candidate_sample.get("progress"):
            errors.append(f"samples[{sample_index}].progress must match")
        if reference_sample.get("elapsedMs") != candidate_sample.get("elapsedMs"):
            errors.append(f"samples[{sample_index}].elapsedMs must match")
        if reference_sample.get("scrollY") != candidate_sample.get("scrollY"):
            errors.append(f"samples[{sample_index}].scrollY must match")


def _is_scroll_action_track(track: object) -> bool:
    return (
        isinstance(track, dict)
        and isinstance(track.get("trigger"), dict)
        and track["trigger"].get("type") == _SCROLL_ACTION
    )


def _validate_node_identity_match(reference_node: object, candidate_node: object, errors: list[str]) -> None:
    if not isinstance(reference_node, dict) or not isinstance(candidate_node, dict):
        return
    if reference_node.get("selector") != candidate_node.get("selector"):
        errors.append("node.selector must match")
    if reference_node.get("fingerprint") != candidate_node.get("fingerprint"):
        errors.append("node.fingerprint must match")


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _read_json(path: Path) -> object:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and compare replay tracks")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("track", type=Path)

    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("reference", type=Path)
    compare_parser.add_argument("candidate", type=Path)
    compare_parser.add_argument("--minimum-score", type=float, required=True)

    build_parser = subparsers.add_parser("manifest-build")
    build_parser.add_argument("root", type=Path)
    build_parser.add_argument("output", type=Path)
    build_parser.add_argument("files", nargs="+")
    build_parser.add_argument("--browser-version", required=True)
    build_parser.add_argument("--tool-version", required=True)

    verify_parser = subparsers.add_parser("manifest-verify")
    verify_parser.add_argument("root", type=Path)
    verify_parser.add_argument("manifest", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload: dict[str, Any]
    if args.command == "validate":
        errors = validate_track(_read_json(args.track))
        payload = {"status": "fail" if errors else "pass", "errors": errors}
    elif args.command == "compare":
        payload = compare_tracks(
            _read_json(args.reference),
            _read_json(args.candidate),
            minimum_score=args.minimum_score,
        )
    elif args.command == "manifest-build":
        manifest = build_recording_manifest(
            args.root,
            args.files,
            browser_version=args.browser_version,
            tool_version=args.tool_version,
        )
        _atomic_write_json(args.output, manifest)
        payload = {
            "status": "pass",
            "output": str(args.output),
            "rootSha256": manifest["rootSha256"],
        }
    elif args.command == "manifest-verify":
        errors = verify_recording_manifest(args.root, _read_json(args.manifest))
        payload = {"status": "fail" if errors else "pass", "errors": errors}
    else:  # pragma: no cover - argparse enforces the command set.
        raise AssertionError(f"unknown command: {args.command}")
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
