from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ui_clone.replay_track import (
    build_recording_manifest,
    compare_tracks,
    track_sha256,
    validate_track,
    verify_recording_manifest,
)


def _pin_baseline(track: dict[str, object]) -> dict[str, object]:
    baseline = track["baseline"]
    assert isinstance(baseline, dict)
    baseline["trackSha256"] = track_sha256(track)
    return track


def _failures(result: dict[str, object]) -> list[str]:
    failures = result["failures"]
    assert isinstance(failures, list)
    assert all(isinstance(failure, str) for failure in failures)
    return failures


def _int_metric(result: dict[str, object], key: str) -> int:
    value = result[key]
    assert isinstance(value, int)
    return value


def _float_metric(result: dict[str, object], key: str) -> float:
    value = result[key]
    assert isinstance(value, int | float)
    return float(value)


def _valid_track() -> dict[str, object]:
    samples: list[dict[str, object]] = []
    for index in range(21):
        # Seven gradual samples make the observed change band measurable. The
        # rest remain stable so a full-page 21-point sweep cannot masquerade as
        # adequate sampling of a short transition.
        phase = min(max(index - 7, 0), 6) / 6
        samples.append(
            {
                "index": index,
                "progress": index / 20,
                "scrollY": index * 50,
                "properties": {
                    "transform": {"translateX": 0.0, "translateY": -40.0 * phase},
                    "opacity": 1.0 - (0.2 * phase),
                    "clipPath": "inset(0px 0px 0px 0px)",
                    "backgroundColor": [255, 255, 255, 1.0],
                    "height": 80.0 - (20.0 * phase),
                    "position": "sticky" if index >= 8 else "relative",
                },
                "box": {
                    "x": 0.0,
                    "y": 0.0,
                    "width": 1440.0,
                    "height": 80.0 - (20.0 * phase),
                },
                "settle": {"status": "settled", "frames": 2},
            }
        )
    track: dict[str, object] = {
        "schemaVersion": 1,
        "trackId": "desktop-shell-nav",
        "trigger": {
            "type": "scroll-progress",
            "sampleDenominator": 20,
            "startPx": 0.0,
            "endPx": 1000.0,
        },
        "node": {
            "selector": "header nav",
            "fingerprint": {"role": "navigation", "text": "Menu", "path": "header/nav"},
        },
        "samples": samples,
        "baseline": {"recording": 1, "trackSha256": "a" * 64},
    }
    return _pin_baseline(track)


def _valid_scroll_action_track() -> dict[str, object]:
    denominator = 1000
    samples: list[dict[str, object]] = []
    for index in range(21):
        phase = index / 20
        height = 80.0 - (20.0 * phase)
        samples.append(
            {
                "index": index,
                "elapsedMs": round(index * denominator / 20, 3),
                "scrollY": 100 if index == 0 else 700,
                "properties": {
                    "transform": {"translateX": 0.0, "translateY": -80.0 * phase},
                    "opacity": 1.0 - (0.4 * phase),
                    "clipPath": "inset(0px 0px 0px 0px)",
                    "backgroundColor": [255, 255, 255, 1.0],
                    "height": height,
                    "position": "sticky",
                },
                "box": {
                    "x": 0.0,
                    "y": 0.0,
                    "width": 1440.0,
                    "height": height,
                },
                "settle": {
                    "status": "paused" if 1 <= index <= 19 else "settled",
                    "frames": 2,
                },
            }
        )
    track: dict[str, object] = {
        "schemaVersion": 1,
        "trackId": "scroll-jump-nav",
        "trigger": {
            "type": "scroll-action",
            "action": "scrollTo",
            "driver": "animation-pause",
            "fromScrollY": 100,
            "toScrollY": 700,
            "denominatorMs": denominator,
        },
        "node": {
            "selector": "header nav",
            "fingerprint": {"role": "navigation", "text": "Menu", "path": "header/nav"},
        },
        "samples": samples,
        "baseline": {"recording": 1, "trackSha256": "a" * 64},
    }
    return _pin_baseline(track)


def _valid_virtual_clock_track() -> dict[str, object]:
    denominator = 640
    samples: list[dict[str, object]] = []
    for index in range(21):
        phase = index / 20
        height = 80.0 - (20.0 * phase)
        samples.append(
            {
                "index": index,
                "elapsedMs": round(index * denominator / 20, 3),
                "scrollY": 100 if index == 0 else 200 + index,
                "properties": {
                    "transform": {"translateX": 0.0, "translateY": -80.0 * phase},
                    "opacity": 1.0 - (0.4 * phase),
                    "clipPath": "inset(0px 0px 0px 0px)",
                    "backgroundColor": [255, 255, 255, 1.0],
                    "height": height,
                    "position": "sticky",
                },
                "box": {
                    "x": 0.0,
                    "y": 0.0,
                    "width": 1440.0,
                    "height": height,
                },
                "settle": {
                    "status": "paused" if 1 <= index <= 19 else "settled",
                    "frames": 2,
                },
            }
        )
    track: dict[str, object] = {
        "schemaVersion": 1,
        "trackId": "virtual-clock-nav",
        "trigger": {
            "type": "scroll-action",
            "action": "scrollTo",
            "driver": "virtual-clock",
            "fromScrollY": 100,
            "toScrollY": 700,
            "denominatorMs": denominator,
            "clock": {"epochMs": 1700000000000, "anchorMs": 1700000000320},
        },
        "node": {
            "selector": "header nav",
            "fingerprint": {"role": "navigation", "text": "Menu", "path": "header/nav"},
        },
        "samples": samples,
        "baseline": {"recording": 1, "trackSha256": "a" * 64},
    }
    return _pin_baseline(track)


def _gradual_scroll_progress_track() -> dict[str, object]:
    samples: list[dict[str, object]] = []
    for index in range(21):
        samples.append(
            {
                "index": index,
                "progress": index / 20,
                "scrollY": index * 50,
                "properties": {
                    "transform": {"translateX": 0.0, "translateY": -2.0 * index},
                    "opacity": 1.0 - (0.01 * index),
                    "clipPath": "inset(0px 0px 0px 0px)",
                    "backgroundColor": [255, 255, 255, 1.0],
                    "height": 80.0 - index,
                    "position": "relative",
                },
                "box": {
                    "x": 0.0,
                    "y": 0.0,
                    "width": 1440.0,
                    "height": 80.0 - index,
                },
                "settle": {"status": "settled", "frames": 2},
            }
        )
    track: dict[str, object] = {
        "schemaVersion": 1,
        "trackId": "linear-scroll-nav",
        "trigger": {
            "type": "scroll-progress",
            "sampleDenominator": 20,
            "startPx": 0.0,
            "endPx": 1000.0,
        },
        "node": {
            "selector": "header nav",
            "fingerprint": {"role": "navigation", "text": "Menu", "path": "header/nav"},
        },
        "samples": samples,
        "baseline": {"recording": 1, "trackSha256": "a" * 64},
    }
    return _pin_baseline(track)


def test_valid_track_has_explicit_comparison_denominator() -> None:
    track = _valid_track()

    assert validate_track(track) == []
    result = compare_tracks(track, copy.deepcopy(track), minimum_score=0.95)

    assert result["status"] == "pass"
    assert result["matchedPairs"] == 147
    assert result["totalPairs"] == 147
    assert result["score"] == 1.0


def test_scroll_progress_requires_explicit_sample_denominator() -> None:
    track = _valid_track()
    trigger = track["trigger"]
    assert isinstance(trigger, dict)
    del trigger["sampleDenominator"]

    errors = validate_track(track)

    assert any("sampleDenominator" in error for error in errors)


def test_scroll_progress_accepts_lenis_wheel_transport() -> None:
    track = _valid_track()
    trigger = track["trigger"]
    assert isinstance(trigger, dict)
    trigger["transport"] = "lenis-wheel"

    assert validate_track(track) == []


def test_scroll_progress_accepts_ready_wait_provenance() -> None:
    track = _valid_track()
    trigger = track["trigger"]
    assert isinstance(trigger, dict)
    trigger["readyWaitMs"] = 250

    assert validate_track(track) == []


@pytest.mark.parametrize("transport", ["native", "wheel", "", None])
def test_scroll_progress_rejects_unknown_transport(transport: object) -> None:
    track = _valid_track()
    trigger = track["trigger"]
    assert isinstance(trigger, dict)
    trigger["transport"] = transport

    errors = validate_track(track)

    assert any("transport" in error for error in errors)


@pytest.mark.parametrize("ready_wait_ms", [-1, 30001, 0.5, "250", None])
def test_trigger_rejects_invalid_ready_wait_provenance(ready_wait_ms: object) -> None:
    for track in (_valid_track(), _valid_scroll_action_track()):
        trigger = track["trigger"]
        assert isinstance(trigger, dict)
        trigger["readyWaitMs"] = ready_wait_ms

        errors = validate_track(track)

        assert any("readyWaitMs" in error for error in errors)


def test_compare_refuses_scroll_progress_transport_mismatch() -> None:
    reference = _valid_track()
    candidate = copy.deepcopy(reference)
    trigger = candidate["trigger"]
    assert isinstance(trigger, dict)
    trigger["transport"] = "lenis-wheel"

    result = compare_tracks(reference, candidate, minimum_score=0.95)

    assert result["status"] == "fail"
    assert any("trigger" in failure for failure in _failures(result))


def test_compare_refuses_ready_wait_mismatch() -> None:
    reference = _valid_track()
    candidate = copy.deepcopy(reference)
    reference_trigger = reference["trigger"]
    candidate_trigger = candidate["trigger"]
    assert isinstance(reference_trigger, dict)
    assert isinstance(candidate_trigger, dict)
    reference_trigger["readyWaitMs"] = 250
    candidate_trigger["readyWaitMs"] = 500

    result = compare_tracks(reference, candidate, minimum_score=0.95)

    assert result["status"] == "fail"
    assert any("trigger" in failure for failure in _failures(result))


@pytest.mark.parametrize(
    ("index", "field", "value", "expected"),
    [
        (10, "progress", 0.51, "progress"),
        (10, "scrollY", 501.0, "scrollY"),
    ],
)
def test_scroll_progress_rejects_off_grid_samples(
    index: int,
    field: str,
    value: object,
    expected: str,
) -> None:
    track = _valid_track()
    samples = track["samples"]
    assert isinstance(samples, list)
    samples[index][field] = value

    errors = validate_track(track)

    assert any(expected in error and "grid" in error for error in errors)


@pytest.mark.parametrize(
    ("trigger_type", "extra_field"),
    [
        ("scroll-progress", "action"),
        ("scroll-action", "mode"),
    ],
)
def test_trigger_discriminator_rejects_unknown_fields(
    trigger_type: str,
    extra_field: str,
) -> None:
    track = _valid_track() if trigger_type == "scroll-progress" else _valid_scroll_action_track()
    trigger = track["trigger"]
    assert isinstance(trigger, dict)
    trigger[extra_field] = "scrollTo"

    errors = validate_track(track)

    assert any(extra_field in error and "unknown" in error for error in errors)


def test_scroll_action_trigger_is_accepted() -> None:
    track = _valid_scroll_action_track()

    assert validate_track(track) == []
    result = compare_tracks(track, copy.deepcopy(track), minimum_score=1.0)

    assert result["status"] == "pass"
    assert result["matchedPairs"] == 147
    assert result["totalPairs"] == 147


def test_scroll_action_accepts_ready_wait_provenance() -> None:
    track = _valid_scroll_action_track()
    trigger = track["trigger"]
    assert isinstance(trigger, dict)
    trigger["readyWaitMs"] = 30000

    assert validate_track(track) == []


def test_virtual_clock_scroll_action_accepts_actual_scroll_drift() -> None:
    track = _valid_virtual_clock_track()

    assert validate_track(track) == []
    result = compare_tracks(track, copy.deepcopy(track), minimum_score=1.0)

    assert result["status"] == "pass"


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("action", "smoothScroll", "action"),
        ("driver", None, "driver"),
        ("driver", "raf", "driver"),
        ("fromScrollY", 100.5, "fromScrollY"),
        ("toScrollY", 100, "toScrollY"),
        ("denominatorMs", 49, "denominatorMs"),
    ],
)
def test_scroll_action_rejects_bad_trigger_contract(
    field: str,
    value: object,
    expected: str,
) -> None:
    track = _valid_scroll_action_track()
    trigger = track["trigger"]
    assert isinstance(trigger, dict)
    trigger[field] = value

    errors = validate_track(track)

    assert any(expected in error for error in errors)


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (lambda trigger: trigger.pop("driver"), "driver"),
        (lambda trigger: trigger.__setitem__("driver", "raf"), "driver"),
        (lambda trigger: trigger.__setitem__("clock", {"epochMs": 1000, "anchorMs": 1328}), "clock"),
    ],
)
def test_animation_pause_rejects_missing_unknown_driver_or_clock(
    mutator: object,
    expected: str,
) -> None:
    track = _valid_scroll_action_track()
    trigger = track["trigger"]
    assert isinstance(trigger, dict)
    assert callable(mutator)
    mutator(trigger)

    errors = validate_track(track)

    assert any(expected in error for error in errors)


def test_virtual_clock_requires_clock_and_known_clock_keys() -> None:
    missing = _valid_virtual_clock_track()
    missing_trigger = missing["trigger"]
    assert isinstance(missing_trigger, dict)
    del missing_trigger["clock"]

    unknown = _valid_virtual_clock_track()
    unknown_trigger = unknown["trigger"]
    assert isinstance(unknown_trigger, dict)
    clock = unknown_trigger["clock"]
    assert isinstance(clock, dict)
    clock["phase"] = 0

    assert any("clock" in error for error in validate_track(missing))
    assert any("phase" in error and "unknown" in error for error in validate_track(unknown))


def test_virtual_clock_requires_fixed_epoch() -> None:
    track = _valid_virtual_clock_track()
    trigger = track["trigger"]
    assert isinstance(trigger, dict)
    clock = trigger["clock"]
    assert isinstance(clock, dict)
    clock["epochMs"] = 1700000000016
    clock["anchorMs"] = 1700000000336

    errors = validate_track(track)

    assert any("epochMs" in error and "1700000000000" in error for error in errors)


@pytest.mark.parametrize(
    ("epoch_ms", "anchor_ms", "expected"),
    [
        (1700000000000, 1700000000000, "greater"),
        (1700000000000, 1700000000321, "16"),
    ],
)
def test_virtual_clock_rejects_bad_anchor_phase(
    epoch_ms: int,
    anchor_ms: int,
    expected: str,
) -> None:
    track = _valid_virtual_clock_track()
    trigger = track["trigger"]
    assert isinstance(trigger, dict)
    clock = trigger["clock"]
    assert isinstance(clock, dict)
    clock["epochMs"] = epoch_ms
    clock["anchorMs"] = anchor_ms

    errors = validate_track(track)

    assert any(expected in error for error in errors)


@pytest.mark.parametrize("denominator", [319, 5000, 641])
def test_virtual_clock_rejects_bad_denominator(denominator: int) -> None:
    track = _valid_virtual_clock_track()
    trigger = track["trigger"]
    assert isinstance(trigger, dict)
    trigger["denominatorMs"] = denominator

    errors = validate_track(track)

    assert any("denominatorMs" in error for error in errors)


def test_virtual_clock_rejects_noninteger_actual_scroll() -> None:
    track = _valid_virtual_clock_track()
    samples = track["samples"]
    assert isinstance(samples, list)
    samples[3]["scrollY"] = 203.5

    errors = validate_track(track)

    assert any("scrollY" in error and "integer" in error for error in errors)


def test_virtual_clock_compare_rejects_clock_mismatch() -> None:
    reference = _valid_virtual_clock_track()
    candidate = copy.deepcopy(reference)
    trigger = candidate["trigger"]
    assert isinstance(trigger, dict)
    clock = trigger["clock"]
    assert isinstance(clock, dict)
    clock["anchorMs"] = 1648

    result = compare_tracks(reference, candidate, minimum_score=1.0)

    assert result["status"] == "fail"
    assert any("trigger" in failure for failure in _failures(result))


def test_virtual_clock_compare_rejects_one_pixel_scroll_mismatch() -> None:
    reference = _valid_virtual_clock_track()
    candidate = copy.deepcopy(reference)
    samples = candidate["samples"]
    assert isinstance(samples, list)
    samples[10]["scrollY"] = 211

    result = compare_tracks(reference, candidate, minimum_score=1.0)

    assert result["status"] == "fail"
    assert any("scrollY" in failure for failure in _failures(result))


@pytest.mark.parametrize(
    ("index", "field", "value", "expected"),
    [
        (1, "elapsedMs", 55.0, "elapsedMs"),
        (0, "scrollY", 700, "scroll-action target"),
        (10, "settle", {"status": "settled", "frames": 2}, "paused"),
        (20, "settle", {"status": "paused", "frames": 2}, "settled"),
        (5, "settle", {"status": "paused", "frames": 3}, "exactly 2"),
    ],
)
def test_scroll_action_rejects_bad_sample_grid_or_settle(
    index: int,
    field: str,
    value: object,
    expected: str,
) -> None:
    track = _valid_scroll_action_track()
    samples = track["samples"]
    assert isinstance(samples, list)
    samples[index][field] = value

    errors = validate_track(track)

    assert any(expected in error for error in errors)


def test_scroll_action_compare_requires_threshold_one() -> None:
    track = _valid_scroll_action_track()

    result = compare_tracks(track, copy.deepcopy(track), minimum_score=0.99)

    assert result["status"] == "fail"
    assert any("minimumScore 1.0" in failure for failure in _failures(result))


def test_scroll_action_compare_rejects_trigger_denominator_mismatch() -> None:
    reference = _valid_scroll_action_track()
    candidate = copy.deepcopy(reference)
    trigger = candidate["trigger"]
    assert isinstance(trigger, dict)
    trigger["denominatorMs"] = 1200

    result = compare_tracks(reference, candidate, minimum_score=1.0)

    assert result["status"] == "fail"
    assert any("trigger" in failure or "denominatorMs" in failure for failure in _failures(result))


@pytest.mark.parametrize("variant", ["jump-cut", "wrong-easing", "shorter-duration"])
def test_scroll_action_negative_controls_fail_exact_threshold(variant: str) -> None:
    reference = _valid_scroll_action_track()
    candidate = copy.deepcopy(reference)
    samples = candidate["samples"]
    assert isinstance(samples, list)
    for sample in samples:
        assert isinstance(sample, dict)
        properties = sample["properties"]
        box = sample["box"]
        assert isinstance(properties, dict)
        assert isinstance(box, dict)
        index = sample["index"]
        assert isinstance(index, int)
        if variant == "jump-cut":
            phase = min(index, 4) / 4
        elif variant == "wrong-easing":
            phase = (index / 20) ** 2
        else:
            phase = min(index / 10, 1)
        height = 80.0 - (20.0 * phase)
        properties["transform"] = {"translateX": 0.0, "translateY": -80.0 * phase}
        properties["opacity"] = 1.0 - (0.4 * phase)
        properties["height"] = height
        box["height"] = height

    assert validate_track(candidate) == []
    result = compare_tracks(reference, candidate, minimum_score=1.0)

    assert result["status"] == "fail"
    assert _int_metric(result, "matchedPairs") < _int_metric(result, "totalPairs")
    assert result["failures"]


@pytest.mark.parametrize(
    ("property_name", "invalid_value"),
    [
        ("transform", {"translateX": float("nan"), "translateY": 0.0}),
        ("opacity", 1.1),
        ("clipPath", ""),
        ("backgroundColor", [256, 0, 0, 1.0]),
        ("height", -1.0),
        ("position", "pinned"),
    ],
)
def test_rejects_invalid_normalized_property(
    property_name: str,
    invalid_value: object,
) -> None:
    track = _valid_track()
    samples = track["samples"]
    assert isinstance(samples, list)
    properties = samples[10]["properties"]
    assert isinstance(properties, dict)
    properties[property_name] = invalid_value

    errors = validate_track(track)

    assert any(property_name in error for error in errors)


def test_rejects_unknown_property_and_invalid_box() -> None:
    track = _valid_track()
    samples = track["samples"]
    assert isinstance(samples, list)
    properties = samples[0]["properties"]
    box = samples[0]["box"]
    assert isinstance(properties, dict)
    assert isinstance(box, dict)
    properties["filter"] = "blur(2px)"
    box["width"] = 0

    errors = validate_track(track)

    assert any("filter" in error for error in errors)
    assert any("width" in error for error in errors)


def test_rejects_track_with_fewer_than_five_samples_in_change_band() -> None:
    track = _valid_track()
    samples = track["samples"]
    assert isinstance(samples, list)
    for index, sample in enumerate(samples):
        properties = sample["properties"]
        assert isinstance(properties, dict)
        transform = properties["transform"]
        assert isinstance(transform, dict)
        transform["translateY"] = -40.0 if index >= 10 else 0.0
        properties["opacity"] = 1.0
        properties["height"] = 80.0
        properties["position"] = "relative"
        box = sample["box"]
        assert isinstance(box, dict)
        box["height"] = 80.0

    errors = validate_track(track)

    assert any("change band" in error for error in errors)


def test_recording_manifest_rejects_empty_file_list(tmp_path: Path) -> None:
    """A manifest binding zero recorder sources must not verify — the manifest
    exists precisely to pin the track to the sources that produced it."""
    manifest = {
        "schemaVersion": 1,
        "algorithm": "sha256",
        "browserVersion": "chromium/1.2.3",
        "toolVersion": "ui-clone/0.7.47",
        "files": [],
        "rootSha256": "0" * 64,
    }

    errors = verify_recording_manifest(tmp_path, manifest)

    assert any("files must not be empty" in error for error in errors), errors


def test_change_band_rejects_jump_cut_with_frozen_middle() -> None:
    """A capture that snaps to the end state instead of interpolating changes
    only at the first and last pair. The band still spans the full sweep, so a
    span-only check accepts it — the density check is what rejects it."""
    track = _valid_track()
    samples = track["samples"]
    assert isinstance(samples, list)
    for index, sample in enumerate(samples):
        phase = 0.0 if index == 0 else (1.0 if index == len(samples) - 1 else 0.5)
        properties = sample["properties"]
        box = sample["box"]
        assert isinstance(properties, dict)
        assert isinstance(box, dict)
        transform = properties["transform"]
        assert isinstance(transform, dict)
        transform["translateY"] = -40.0 * phase
        properties["opacity"] = 1.0 - (0.2 * phase)
        properties["height"] = 80.0 - (20.0 * phase)
        properties["position"] = "relative" if index == 0 else "sticky"
        box["height"] = 80.0 - (20.0 * phase)

    errors = validate_track(track)

    assert any("change band" in error for error in errors), errors


def test_change_band_ignores_sub_tolerance_geometry_jitter() -> None:
    track = _valid_track()
    samples = track["samples"]
    assert isinstance(samples, list)
    for index, sample in enumerate(samples):
        properties = sample["properties"]
        box = sample["box"]
        assert isinstance(properties, dict)
        assert isinstance(box, dict)
        properties["transform"] = {"translateX": 0.0, "translateY": index * 0.05}
        properties["opacity"] = 1.0
        properties["height"] = 80.0
        properties["position"] = "relative"
        box["x"] = index * 0.05
        box["height"] = 80.0

    errors = validate_track(track)

    assert any("change band" in error for error in errors)


def test_change_band_accepts_gradual_linear_track_below_compare_tolerance() -> None:
    track = _gradual_scroll_progress_track()

    assert validate_track(track) == []


def test_rejects_unsettled_or_over_cap_sample() -> None:
    track = _valid_track()
    samples = track["samples"]
    assert isinstance(samples, list)
    samples[10]["settle"] = {"status": "cap-exceeded", "frames": 3}

    errors = validate_track(track)

    assert any("settle" in error for error in errors)


@pytest.mark.parametrize("recording", [0, 2, "latest", None])
def test_baseline_is_pinned_to_first_recording(recording: object) -> None:
    track = _valid_track()
    baseline = track["baseline"]
    assert isinstance(baseline, dict)
    baseline["recording"] = recording

    errors = validate_track(track)

    assert any("baseline" in error for error in errors)


def test_compare_refuses_unpinned_reference_hash() -> None:
    reference = _valid_track()
    candidate = copy.deepcopy(reference)
    baseline = reference["baseline"]
    assert isinstance(baseline, dict)
    baseline["trackSha256"] = "0" * 64

    result = compare_tracks(reference, candidate, minimum_score=0.90)

    assert result["status"] == "fail"
    assert any("baseline" in failure for failure in _failures(result))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("selector", "main nav"),
        ("fingerprint", {"role": "navigation", "text": "Menu", "path": "aside/nav"}),
    ],
)
def test_compare_refuses_node_identity_mismatch(field: str, value: object) -> None:
    reference = _valid_track()
    candidate = copy.deepcopy(reference)
    node = candidate["node"]
    assert isinstance(node, dict)
    node[field] = value

    result = compare_tracks(reference, candidate, minimum_score=0.90)

    assert result["status"] == "fail"
    assert any(f"node.{field}" in failure for failure in _failures(result))


def test_recording_manifest_hashes_exact_sorted_file_set(tmp_path: Path) -> None:
    (tmp_path / "recorder.js").write_text("one", encoding="utf-8")
    (tmp_path / "schema.json").write_text("two", encoding="utf-8")

    manifest = build_recording_manifest(
        tmp_path,
        ["schema.json", "recorder.js"],
        browser_version="Chromium 140",
        tool_version="playwright 1.55",
    )

    entries = manifest["files"]
    assert isinstance(entries, list)
    assert all(isinstance(entry, dict) for entry in entries)
    assert [entry["path"] for entry in entries] == ["recorder.js", "schema.json"]
    assert verify_recording_manifest(tmp_path, manifest) == []


def test_recording_manifest_refuses_changed_or_missing_input(tmp_path: Path) -> None:
    source = tmp_path / "recorder.js"
    source.write_text("one", encoding="utf-8")
    manifest = build_recording_manifest(
        tmp_path,
        ["recorder.js"],
        browser_version="Chromium 140",
        tool_version="playwright 1.55",
    )

    source.write_text("changed", encoding="utf-8")
    changed_errors = verify_recording_manifest(tmp_path, manifest)
    source.unlink()
    missing_errors = verify_recording_manifest(tmp_path, manifest)

    assert any("mismatch" in error for error in changed_errors)
    assert any("missing" in error for error in missing_errors)


def test_negative_control_wrong_track_fails_threshold() -> None:
    reference = _valid_track()
    wrong = copy.deepcopy(reference)
    samples = wrong["samples"]
    assert isinstance(samples, list)
    for sample in samples:
        properties = sample["properties"]
        assert isinstance(properties, dict)
        properties["transform"] = {"translateX": 0.0, "translateY": 200.0}

    result = compare_tracks(reference, wrong, minimum_score=0.90)

    assert result["status"] == "fail"
    assert _int_metric(result, "matchedPairs") < _int_metric(result, "totalPairs")
    assert _float_metric(result, "score") < 0.90
    assert result["failures"]


def test_threshold_allows_bounded_pair_mismatch() -> None:
    reference = _valid_track()
    candidate = copy.deepcopy(reference)
    samples = candidate["samples"]
    assert isinstance(samples, list)
    properties = samples[10]["properties"]
    assert isinstance(properties, dict)
    properties["opacity"] = 0.0

    result = compare_tracks(reference, candidate, minimum_score=0.95)

    assert result["status"] == "pass"
    assert result["matchedPairs"] == 146
    assert result["totalPairs"] == 147
    assert result["failures"]


@pytest.mark.parametrize("minimum_score", [-0.1, 1.1, float("nan"), "high"])
def test_compare_rejects_invalid_threshold(minimum_score: object) -> None:
    track = _valid_track()

    result = compare_tracks(track, copy.deepcopy(track), minimum_score=minimum_score)  # type: ignore[arg-type]

    assert result["status"] == "fail"
    assert any("minimumScore" in failure for failure in _failures(result))


def test_compare_refuses_misaligned_scroll_grid() -> None:
    reference = _valid_track()
    candidate = copy.deepcopy(reference)
    samples = candidate["samples"]
    assert isinstance(samples, list)
    samples[10]["progress"] = 0.51

    result = compare_tracks(reference, candidate, minimum_score=0.90)

    assert result["status"] == "fail"
    assert any("progress" in failure for failure in _failures(result))


def test_cli_emits_machine_readable_validation_result(tmp_path: Path) -> None:
    track_path = tmp_path / "track.json"
    track_path.write_text(json.dumps(_valid_track()), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "ui_clone.replay_track", "validate", str(track_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"status": "pass", "errors": []}


def test_cli_compare_reports_denominator_and_fails_negative_control(
    tmp_path: Path,
) -> None:
    reference = _valid_track()
    candidate = copy.deepcopy(reference)
    samples = candidate["samples"]
    assert isinstance(samples, list)
    for sample in samples:
        properties = sample["properties"]
        assert isinstance(properties, dict)
        properties["transform"] = {"translateX": 0.0, "translateY": 200.0}
    reference_path = tmp_path / "reference.json"
    candidate_path = tmp_path / "candidate.json"
    reference_path.write_text(json.dumps(reference), encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ui_clone.replay_track",
            "compare",
            str(reference_path),
            str(candidate_path),
            "--minimum-score",
            "0.90",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["status"] == "fail"
    assert payload["matchedPairs"] < payload["totalPairs"]
