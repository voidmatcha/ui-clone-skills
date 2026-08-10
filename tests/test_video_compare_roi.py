from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from scripts.verify.lib.hover_action_receipt import validation_error
from scripts.verify.lib.reference_self_calibration import (
    _transition_contract_key,
    calibrate_complementary,
    calibrate_distributions,
    calibrate_static_discrete,
)
from scripts.verify.lib.selector_capture_retry import build_retry_receipt
from scripts.verify.lib.video_compare_roi import build_plan, load_target_rect

ROOT = Path(__file__).resolve().parents[1]
VIDEO_COMPARE = ROOT / "scripts" / "verify" / "video-transition-compare.sh"
FRAME_ALIGN = ROOT / "scripts" / "verify" / "lib" / "frame-align.sh"
needs_video_tools = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("magick") is None,
    reason="ffmpeg/imagemagick not installed",
)


def _early_window_receipt(
    *,
    rows: int,
    failure_rows: list[int],
    threshold: float = 0.90,
    selector: str = ".target",
    arc_within_tolerance: bool = True,
    arc_delta_frames: int | None = None,
    arc_max_delta_frames: int = 18,
    ref_duration_frames: int = 20,
    impl_duration_frames: int = 21,
    early_window_seconds: float | None = None,
    extracted_fps: float = 60.0,
) -> dict[str, object]:
    if arc_delta_frames is None:
        arc_delta_frames = abs(ref_duration_frames - impl_duration_frames)
    early_window_rows = max(failure_rows)
    if early_window_seconds is None:
        early_window_seconds = early_window_rows / extracted_fps
    return {
        "schemaVersion": 1,
        "status": "retryable-unmeasurable",
        "reason": "early-window-capture-phase",
        "selector": selector,
        "threshold": threshold,
        "rows": rows,
        "failures": len(failure_rows),
        "failureRows": failure_rows,
        "firstStablePassingRow": max(failure_rows) + 1,
        "lastFailureRow": max(failure_rows),
        "earlyWindowRows": early_window_rows,
        "earlyWindowSeconds": early_window_seconds,
        "extractedFps": extracted_fps,
        "minSsim": 0.80,
        "arc": {
            "ref": {
                "firstChange": 10,
                "lastChange": 10 + ref_duration_frames,
                "durationFrames": ref_duration_frames,
            },
            "impl": {
                "firstChange": 10,
                "lastChange": 10 + impl_duration_frames,
                "durationFrames": impl_duration_frames,
            },
            "deltaFrames": arc_delta_frames,
            "maxDeltaFrames": arc_max_delta_frames,
            "withinTolerance": arc_within_tolerance,
        },
    }


def _arc_only_receipt(
    *,
    rows: int,
    min_ssim: float = 0.96,
    threshold: float = 0.90,
    selector: str = ".target",
    ref_duration_frames: int = 36,
    impl_duration_frames: int = 12,
    arc_max_delta_frames: int = 18,
) -> dict[str, object]:
    delta = abs(ref_duration_frames - impl_duration_frames)
    return {
        "schemaVersion": 1,
        "status": "retryable-unmeasurable",
        "reason": "arc-only-capture-jitter",
        "selector": selector,
        "threshold": threshold,
        "rows": rows,
        "failures": 0,
        "failureRows": [],
        "firstStablePassingRow": 1,
        "lastFailureRow": 0,
        "minSsim": min_ssim,
        "arc": {
            "ref": {
                "firstChange": 10,
                "lastChange": 10 + ref_duration_frames,
                "durationFrames": ref_duration_frames,
            },
            "impl": {
                "firstChange": 10,
                "lastChange": 10 + impl_duration_frames,
                "durationFrames": impl_duration_frames,
            },
            "deltaFrames": delta,
            "maxDeltaFrames": arc_max_delta_frames,
            "withinTolerance": delta <= arc_max_delta_frames,
        },
    }


def _target_payload(
    *,
    selector: str = ".target",
    match_index: int = 0,
    match_count: int = 1,
    width: float = 160,
    height: float = 120,
    duration: str = "0.2,0.2",
    delay: str = "0,0",
    prop: str = "background-color,border-color",
    timing: str = "cubic-bezier(0.33, 1, 0.68, 1), cubic-bezier(0.33, 1, 0.68, 1)",
) -> dict[str, object]:
    return {
        "found": True,
        "selector": selector,
        "matchIndex": match_index,
        "matchCount": match_count,
        "rect": {"x": 80, "y": 60, "width": width, "height": height},
        "transition": {
            "property": prop,
            "duration": duration,
            "delay": delay,
            "timingFunction": timing,
        },
        "state": {
            "phase": "idle",
            "watchedStyle": {
                "color": "rgb(0, 0, 0)",
                "backgroundColor": "rgba(0, 0, 0, 0)",
                "borderTopColor": "rgb(0, 0, 0)",
                "borderRightColor": "rgb(0, 0, 0)",
                "borderBottomColor": "rgb(0, 0, 0)",
                "borderLeftColor": "rgb(0, 0, 0)",
                "opacity": "1",
                "transform": "none",
                "filter": "none",
                "boxShadow": "none",
                "textDecorationLine": "none",
                "textDecorationColor": "rgb(0, 0, 0)",
                "fontWeight": "400",
                "letterSpacing": "normal",
            },
            "ancestorClassPath": ["a.link", "li.item", "ul.menu"],
        },
    }


def _target_payloads(**overrides: object) -> dict[str, dict[str, object]]:
    payloads = {
        "firstRef": _target_payload(),
        "firstImpl": _target_payload(),
        "retryRef": _target_payload(),
        "retryImpl": _target_payload(),
    }
    if "prop" in overrides:
        transition = _target_payload(prop=str(overrides.pop("prop")))["transition"]
        for payload in payloads.values():
            payload["transition"] = transition
    for name, patch in overrides.items():
        payloads[name] = {**payloads[name], **cast(dict[str, object], patch)}
    return payloads


def _hover_action_payloads(**overrides: object) -> dict[str, dict[str, object]]:
    payloads = _target_payloads(**({"prop": overrides.pop("prop")} if "prop" in overrides else {}))
    for payload in payloads.values():
        payload["hovered"] = True
        payload["pointerReachable"] = True
        state = dict(cast(dict[str, object], payload["state"]))
        watched = dict(cast(dict[str, object], state["watchedStyle"]))
        watched["fontWeight"] = "600"
        state["watchedStyle"] = watched
        state["phase"] = "hover"
        state["ancestorClassPath"] = ["a.link.active", "li.item.hover", "ul.menu"]
        payload["state"] = state
    for name, patch in overrides.items():
        payloads[name] = {**payloads[name], **cast(dict[str, object], patch)}
    return payloads


def _replace_watched_style(
    payload: dict[str, object],
    **style_patch: str,
) -> dict[str, object]:
    state = dict(cast(dict[str, object], payload["state"]))
    watched = dict(cast(dict[str, object], state["watchedStyle"]))
    watched.update(style_patch)
    state["watchedStyle"] = watched
    return {**payload, "state": state}


def _add_ancestor_class_token(payload: dict[str, object], token: str) -> dict[str, object]:
    state = dict(cast(dict[str, object], payload["state"]))
    path = list(cast(list[str], state["ancestorClassPath"]))
    path[0] = f"{path[0]}.{token}"
    state["ancestorClassPath"] = path
    return {**payload, "state": state}


def _replace_ancestor_class_path(
    payload: dict[str, object],
    path: list[str],
) -> dict[str, object]:
    state = dict(cast(dict[str, object], payload["state"]))
    state["ancestorClassPath"] = path
    return {**payload, "state": state}


def _hover_proof(
    *,
    selector: str = ".target",
    match_index: int = 0,
    match_count: int = 1,
    armed_at: float = 1000.0,
    move_at: float = 1001.0,
    first_pointer_event: float = 1002.0,
    first_mutation: float | None = 1003.0,
    first_commit_raf: float = 1010.0,
    first_hover_raf: float = 1010.0,
    stable_at: float = 1026.0,
    stable_hover_raf_count: object = 2,
    max_active_animation_count: int = 0,
    changed_style_keys: list[str] | None = None,
    commit_patch: dict[str, object] | None = None,
    mutation_patch: dict[str, object] | None = None,
    initial_state: dict[str, object] | None = None,
    action_state: dict[str, object] | None = None,
) -> dict[str, object]:
    initial = {
        **(initial_state or cast(dict[str, object], _target_payload()["state"])),
        "activeAnimationCount": 0,
    }
    action_state = action_state or cast(dict[str, object], _hover_action_payloads()["firstRef"]["state"])
    final = {
        "watchedStyle": action_state["watchedStyle"],
        "ancestorClassPath": action_state["ancestorClassPath"],
        "hovered": True,
    }
    commit = {
        "watchedStyle": final["watchedStyle"],
        "ancestorClassPath": final["ancestorClassPath"],
        "hovered": True,
    }
    mutation = (
        {
            "time": first_mutation,
            "ancestorClassPath": final["ancestorClassPath"],
        }
        if first_mutation is not None
        else None
    )
    if commit_patch:
        commit.update(commit_patch)
    if mutation_patch and mutation is not None:
        mutation.update(mutation_patch)
    return {
        "schemaVersion": 1,
        "selector": selector,
        "matchIndex": match_index,
        "matchCount": match_count,
        "armedAt": armed_at,
        "moveAt": move_at,
        "firstPointerEvent": first_pointer_event,
        "firstMutation": first_mutation,
        "firstCommitRaf": first_commit_raf,
        "firstHoverRaf": first_hover_raf,
        "stableAt": stable_at,
        "stableHoverRafCount": stable_hover_raf_count,
        "initial": initial,
        "commit": commit,
        "mutation": mutation,
        "final": final,
        "changedStyleKeys": changed_style_keys or ["fontWeight"],
        "pointerObserved": True,
        "mutationObserved": first_mutation is not None,
        "rafObserved": True,
        "done": True,
        "maxActiveAnimationCount": max_active_animation_count,
    }


def _with_hover_proofs(
    payloads: dict[str, dict[str, object]],
    **proof_overrides: dict[str, object],
) -> dict[str, dict[str, object]]:
    result = {name: dict(payload) for name, payload in payloads.items()}
    for index, name in enumerate(("firstRef", "firstImpl", "retryRef", "retryImpl")):
        overrides = proof_overrides.get(name, {})
        assert isinstance(overrides, dict)
        proof_kwargs: dict[str, Any] = {
            "armed_at": 1000.0 + index,
            "move_at": 1001.0 + index,
            "first_pointer_event": 1002.0 + index,
            "first_mutation": 1003.0 + index,
            "first_commit_raf": 1010.0 + index,
            "first_hover_raf": 1010.0 + index,
            "stable_at": 1026.0 + index,
            **overrides,
        }
        result[name]["hoverProof"] = _hover_proof(**proof_kwargs)
    return result


def _with_bound_hover_proofs(
    payloads: dict[str, dict[str, object]],
    target_payloads: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    result = {name: dict(payload) for name, payload in payloads.items()}
    for index, name in enumerate(("firstRef", "firstImpl", "retryRef", "retryImpl")):
        result[name]["hoverProof"] = _hover_proof(
            armed_at=1000.0 + index,
            move_at=1001.0 + index,
            first_pointer_event=1002.0 + index,
            first_mutation=1003.0 + index,
            first_commit_raf=1010.0 + index,
            first_hover_raf=1010.0 + index,
            stable_at=1026.0 + index,
            initial_state=cast(dict[str, object], target_payloads[name]["state"]),
            action_state=cast(dict[str, object], result[name]["state"]),
        )
    return result


def _with_bound_declared_transition_proofs(
    payloads: dict[str, dict[str, object]],
    target_payloads: dict[str, dict[str, object]],
    *,
    max_active_animation_count: int = 2,
) -> dict[str, dict[str, object]]:
    result = {name: dict(payload) for name, payload in payloads.items()}
    for index, name in enumerate(("firstRef", "firstImpl", "retryRef", "retryImpl")):
        result[name]["hoverProof"] = _hover_proof(
            armed_at=1000.0 + index,
            move_at=1001.0 + index,
            first_pointer_event=1002.0 + index,
            first_mutation=None,
            first_hover_raf=1010.0 + index,
            first_commit_raf=1168.0 + index,
            stable_at=1176.0 + index,
            max_active_animation_count=max_active_animation_count,
            initial_state=cast(dict[str, object], target_payloads[name]["state"]),
            action_state=cast(dict[str, object], result[name]["state"]),
        )
    return result


def _source_metadata_payloads(ratio: int = 6) -> dict[str, dict[str, dict[str, object]]]:
    source_fps = 60.0 / ratio
    payload = {
        "schemaVersion": 1,
        "rawWebmSha256": "0" * 64,
        "rFrameRate": f"60/{ratio}",
        "avgFrameRate": f"60/{ratio}",
        "sourceFps": source_fps,
        "cfr": True,
        "extractedFps": 60,
        "sourceToExtractedRatio": ratio,
    }
    return {
        "first": {"ref": dict(payload), "impl": dict(payload)},
        "retry": {"ref": dict(payload), "impl": dict(payload)},
    }


def _source_metadata_hashes() -> dict[str, dict[str, str]]:
    return {
        "first": {"ref": "first-ref", "impl": "first-impl"},
        "retry": {"ref": "retry-ref", "impl": "retry-impl"},
    }


def _with_source_binding(
    receipt: dict[str, object],
    *,
    attempt: str,
    metadata: dict[str, dict[str, dict[str, object]]],
    hashes: dict[str, dict[str, str]],
) -> dict[str, object]:
    return {
        **receipt,
        "sourceMetadata": {
            side: {
                "sha256": hashes[attempt][side],
                "payload": metadata[attempt][side],
            }
            for side in ("ref", "impl")
        },
    }


def _failed_standard_receipt(failure_rows: list[int]) -> dict[str, object]:
    return {
        "status": "reference-self-calibration-failed",
        "rule": "retry-cross-early-window-subset-of-reference-self-capture-phase",
        "arcDurationDriftFrames": 30,
        "metrics": {
            "referenceSelf": {
                "failureRows": failure_rows,
            }
        },
    }


def _calibrate_static_discrete_with_payloads(
    target_payloads: dict[str, dict[str, object]],
    action_payloads: dict[str, dict[str, object]],
) -> dict[str, Any]:
    self_failure_rows = [8, 9, 10, 11, 13, 14, 15, 16, 17, 18]
    self_values = [0.80 if index in self_failure_rows else 0.96 for index in range(1, 19)]
    first_values = [0.80 if index in (13, 14, 15, 16, 17, 18) else 0.96 for index in range(1, 20)]
    retry_values = [0.80 if index in (7, 8, 9, 10, 11, 12) else 0.96 for index in range(1, 19)]
    metadata = _source_metadata_payloads()
    hashes = _source_metadata_hashes()
    return cast(dict[str, Any], calibrate_static_discrete(
        self_values,
        retry_values,
        threshold=0.90,
        expected_rows=18,
        first_cross_values=first_values,
        first_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=19,
                failure_rows=[13, 14, 15, 16, 17, 18],
                arc_within_tolerance=False,
                arc_delta_frames=24,
                arc_max_delta_frames=18,
                ref_duration_frames=24,
                impl_duration_frames=48,
            ),
            attempt="first",
            metadata=metadata,
            hashes=hashes,
        ),
        retry_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=18,
                failure_rows=[7, 8, 9, 10, 11, 12],
                arc_within_tolerance=False,
                arc_delta_frames=24,
                arc_max_delta_frames=18,
                ref_duration_frames=24,
                impl_duration_frames=48,
            ),
            attempt="retry",
            metadata=metadata,
            hashes=hashes,
        ),
        reference_self_receipt=_failed_standard_receipt(self_failure_rows),
        target_payloads=target_payloads,
        action_payloads=action_payloads,
        source_metadata=metadata,
        source_metadata_sha256=hashes,
    ))


def test_build_plan_uses_equal_size_side_local_crops() -> None:
    plan = build_plan(
        {"x": 20.0, "y": 30.0, "width": 100.0, "height": 40.0},
        {"x": 700.0, "y": 400.0, "width": 120.0, "height": 60.0},
        viewport_width=1000,
        viewport_height=700,
        padding=20,
        selector=".button",
    )

    assert plan["ref"]["crop"]["width"] == 160
    assert plan["impl"]["crop"]["width"] == 160
    assert plan["ref"]["crop"]["height"] == 100
    assert plan["impl"]["crop"]["height"] == 100
    assert plan["ref"]["crop"]["x"] == 0
    assert plan["impl"]["crop"]["x"] == 680


def test_load_target_rect_unwraps_agent_browser_string(tmp_path: Path) -> None:
    raw = tmp_path / "rect.json"
    raw.write_text(
        json.dumps(
            json.dumps(
                {
                    "found": True,
                    "rect": {"x": 1, "y": 2, "width": 30, "height": 40},
                }
            )
        ),
        encoding="utf-8",
    )

    assert load_target_rect(raw) == {
        "x": 1.0,
        "y": 2.0,
        "width": 30.0,
        "height": 40.0,
    }


def test_load_target_rect_rejects_missing_selector(tmp_path: Path) -> None:
    raw = tmp_path / "rect.json"
    raw.write_text(json.dumps({"found": False}), encoding="utf-8")

    with pytest.raises(ValueError, match="not resolved"):
        load_target_rect(raw)


def test_roi_helper_keeps_python39_runtime_compatible() -> None:
    for relative_path in (
        "scripts/verify/lib/video_compare_roi.py",
        "scripts/verify/lib/reference_self_calibration.py",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "isinstance(raw, int | float)" not in source
        assert "isinstance(extracted_fps, int | float)" not in source
        assert "isinstance(source_extracted_fps, int | float)" not in source


def test_reference_self_calibration_accepts_cross_series_no_worse_at_each_aligned_row() -> None:
    payload = calibrate_distributions(
        [0.72, 0.81, 0.94, 0.98],
        [0.73, 0.82, 0.95, 0.99],
        threshold=0.90,
        expected_rows=4,
        first_cross_values=[0.72, 0.81, 0.94, 0.98],
        first_capture_receipt=_early_window_receipt(rows=4, failure_rows=[1, 2]),
        retry_capture_receipt=_early_window_receipt(rows=4, failure_rows=[1, 2]),
    )

    assert payload["status"] == "pass-after-reference-self-calibration"


def test_static_discrete_calibration_accepts_naver_noncontiguous_source_bins() -> None:
    self_failure_rows = [8, 9, 10, 11, 13, 14, 15, 16, 17, 18]
    self_values = [0.96] * 18
    for row in self_failure_rows:
        self_values[row - 1] = 0.80
    first_values = [0.96] * 18
    retry_values = [0.96] * 18
    for row in (13, 14, 15, 16, 17, 18):
        first_values[row - 1] = 0.80
    for row in (7, 8, 9, 10, 11, 12):
        retry_values[row - 1] = 0.80
    metadata = _source_metadata_payloads()
    hashes = _source_metadata_hashes()

    payload = calibrate_static_discrete(
        self_values,
        retry_values,
        threshold=0.90,
        expected_rows=18,
        first_cross_values=first_values,
        first_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=18,
                failure_rows=[13, 14, 15, 16, 17, 18],
                arc_within_tolerance=False,
                arc_delta_frames=24,
                arc_max_delta_frames=18,
                ref_duration_frames=54,
                impl_duration_frames=78,
            ),
            attempt="first",
            metadata=metadata,
            hashes=hashes,
        ),
        retry_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=18,
                failure_rows=[7, 8, 9, 10, 11, 12],
                arc_within_tolerance=True,
                arc_delta_frames=6,
                arc_max_delta_frames=18,
                ref_duration_frames=24,
                impl_duration_frames=30,
            ),
            attempt="retry",
            metadata=metadata,
            hashes=hashes,
        ),
        reference_self_receipt=_failed_standard_receipt(self_failure_rows),
        target_payloads=_target_payloads(prop="color"),
        action_payloads=_hover_action_payloads(prop="color"),
        source_metadata=metadata,
        source_metadata_sha256=hashes,
    )

    assert payload["status"] == "pass-after-static-discrete-hover-state-calibration"
    assert payload["metrics"]["referenceSelf"]["sourceBins"] == [2, 3]
    assert payload["metrics"]["firstCross"]["sourceBins"] == [3]
    assert payload["metrics"]["retryCross"]["sourceBins"] == [2]
    assert payload["metrics"]["arcExplained"] is True


def test_static_discrete_calibration_rejects_adjacent_source_bin_not_in_reference_self() -> None:
    self_failure_rows = [13, 14, 15, 16, 17, 18]
    self_values = [0.96] * 18
    retry_values = [0.96] * 18
    for row in self_failure_rows:
        self_values[row - 1] = 0.80
    for row in (7, 8, 9, 10, 11, 12):
        retry_values[row - 1] = 0.80
    metadata = _source_metadata_payloads()
    hashes = _source_metadata_hashes()

    payload = calibrate_static_discrete(
        self_values,
        retry_values,
        threshold=0.90,
        expected_rows=18,
        first_cross_values=[0.80 if index in self_failure_rows else 0.96 for index in range(1, 19)],
        first_capture_receipt=_with_source_binding(
            _early_window_receipt(rows=18, failure_rows=self_failure_rows),
            attempt="first",
            metadata=metadata,
            hashes=hashes,
        ),
        retry_capture_receipt=_with_source_binding(
            _early_window_receipt(rows=18, failure_rows=[7, 8, 9, 10, 11, 12]),
            attempt="retry",
            metadata=metadata,
            hashes=hashes,
        ),
        reference_self_receipt=_failed_standard_receipt(self_failure_rows),
        target_payloads=_target_payloads(prop="color"),
        action_payloads=_hover_action_payloads(prop="color"),
        source_metadata=metadata,
        source_metadata_sha256=hashes,
    )

    assert payload["status"] == "static-discrete-hover-state-calibration-failed"
    assert payload["metrics"]["sourceBinSubsetOfReferenceSelf"] is False


def test_static_discrete_calibration_uses_attempt_offsets_for_source_bins() -> None:
    self_values = [0.96] * 18
    first_values = [0.96] * 18
    retry_values = [0.96] * 18
    for row in (5, 6, 7):
        self_values[row - 1] = 0.80
    for row in (6, 7):
        first_values[row - 1] = 0.80
    for row in (5, 6):
        retry_values[row - 1] = 0.80
    metadata = _source_metadata_payloads()
    hashes = _source_metadata_hashes()

    payload = calibrate_static_discrete(
        self_values,
        retry_values,
        threshold=0.90,
        expected_rows=18,
        first_offset=7,
        retry_offset=8,
        first_cross_values=first_values,
        first_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=18,
                failure_rows=[6, 7],
                arc_within_tolerance=False,
                arc_delta_frames=24,
                arc_max_delta_frames=18,
                ref_duration_frames=54,
                impl_duration_frames=78,
            ),
            attempt="first",
            metadata=metadata,
            hashes=hashes,
        ),
        retry_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=18,
                failure_rows=[5, 6],
                arc_within_tolerance=True,
                arc_delta_frames=6,
                arc_max_delta_frames=18,
                ref_duration_frames=24,
                impl_duration_frames=30,
            ),
            attempt="retry",
            metadata=metadata,
            hashes=hashes,
        ),
        reference_self_receipt=_failed_standard_receipt([5, 6, 7]),
        target_payloads=_target_payloads(prop="color"),
        action_payloads=_hover_action_payloads(prop="color"),
        source_metadata=metadata,
        source_metadata_sha256=hashes,
    )

    assert payload["status"] == "pass-after-static-discrete-hover-state-calibration"
    assert payload["metrics"]["referenceSelf"]["sourceBins"] == [2, 3]
    assert payload["metrics"]["referenceSelf"]["sourceBinsByAttempt"] == {
        "first": [2, 3],
        "retry": [3],
    }
    assert payload["metrics"]["firstCross"]["sourceBins"] == [3]
    assert payload["metrics"]["retryCross"]["sourceBins"] == [3]
    assert payload["metrics"]["tailRowsPassingOutsideReferenceSelfBins"] is True


def test_static_discrete_calibration_rejects_tail_failures_after_offset_binning() -> None:
    self_values = [0.96] * 18
    first_values = [0.96] * 18
    retry_values = [0.96] * 18
    for row in (5, 6, 7):
        self_values[row - 1] = 0.80
    for row in (6, 7):
        first_values[row - 1] = 0.80
    for row in (5, 6, 12):
        retry_values[row - 1] = 0.80
    metadata = _source_metadata_payloads()
    hashes = _source_metadata_hashes()

    payload = calibrate_static_discrete(
        self_values,
        retry_values,
        threshold=0.90,
        expected_rows=18,
        first_offset=7,
        retry_offset=8,
        first_cross_values=first_values,
        first_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=18,
                failure_rows=[6, 7],
                arc_within_tolerance=False,
                arc_delta_frames=24,
                arc_max_delta_frames=18,
                ref_duration_frames=54,
                impl_duration_frames=78,
            ),
            attempt="first",
            metadata=metadata,
            hashes=hashes,
        ),
        retry_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=18,
                failure_rows=[5, 6, 12],
                arc_within_tolerance=True,
                arc_delta_frames=6,
                arc_max_delta_frames=18,
                ref_duration_frames=24,
                impl_duration_frames=30,
            ),
            attempt="retry",
            metadata=metadata,
            hashes=hashes,
        ),
        reference_self_receipt=_failed_standard_receipt([5, 6, 7]),
        target_payloads=_target_payloads(prop="color"),
        action_payloads=_hover_action_payloads(prop="color"),
        source_metadata=metadata,
        source_metadata_sha256=hashes,
    )

    assert payload["status"] == "static-discrete-hover-state-calibration-failed"
    assert payload["metrics"]["retryCross"]["sourceBins"] == [3, 4]
    assert payload["metrics"]["sourceBinSubsetOfReferenceSelf"] is False
    assert payload["metrics"]["tailRowsPassingOutsideReferenceSelfBins"] is False


def test_static_discrete_calibration_rejects_retry_bins_outside_retry_self_projection() -> None:
    self_values = [0.96] * 18
    first_values = [0.96] * 18
    retry_values = [0.96] * 18
    for row in (1, 2, 3, 4, 5, 6):
        self_values[row - 1] = 0.80
        first_values[row - 1] = 0.80
    for row in (7, 8, 9, 10, 11, 12):
        retry_values[row - 1] = 0.80
    metadata = _source_metadata_payloads()
    hashes = _source_metadata_hashes()

    payload = calibrate_static_discrete(
        self_values,
        retry_values,
        threshold=0.90,
        expected_rows=18,
        first_offset=0,
        retry_offset=5,
        first_cross_values=first_values,
        first_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=18,
                failure_rows=[1, 2, 3, 4, 5, 6],
                arc_within_tolerance=False,
                arc_delta_frames=24,
                arc_max_delta_frames=18,
                ref_duration_frames=54,
                impl_duration_frames=78,
            ),
            attempt="first",
            metadata=metadata,
            hashes=hashes,
        ),
        retry_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=18,
                failure_rows=[7, 8, 9, 10, 11, 12],
                arc_within_tolerance=True,
                arc_delta_frames=6,
                arc_max_delta_frames=18,
                ref_duration_frames=24,
                impl_duration_frames=30,
            ),
            attempt="retry",
            metadata=metadata,
            hashes=hashes,
        ),
        reference_self_receipt=_failed_standard_receipt([1, 2, 3, 4, 5, 6]),
        target_payloads=_target_payloads(prop="color"),
        action_payloads=_hover_action_payloads(prop="color"),
        source_metadata=metadata,
        source_metadata_sha256=hashes,
    )

    assert payload["status"] == "static-discrete-hover-state-calibration-failed"
    assert payload["metrics"]["referenceSelf"]["sourceBinsByAttempt"] == {
        "first": [1],
        "retry": [1, 2],
    }
    assert payload["metrics"]["retryCross"]["sourceBins"] == [2, 3]
    assert payload["metrics"]["sourceBinSubsetOfReferenceSelf"] is False
    assert payload["metrics"]["tailRowsPassingOutsideReferenceSelfBins"] is False


def test_static_discrete_cli_accepts_provenance_flags_without_type_error(
    tmp_path: Path,
) -> None:
    def write_json(path: Path, payload: object) -> Path:
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return path

    def write_series(path: Path, values: list[float]) -> Path:
        path.write_text("".join(f"{value}\n" for value in values), encoding="utf-8")
        return path

    metadata = _source_metadata_payloads()
    source_paths: dict[str, dict[str, Path]] = {"first": {}, "retry": {}}
    source_hashes: dict[str, dict[str, str]] = {"first": {}, "retry": {}}
    for attempt in ("first", "retry"):
        for side in ("ref", "impl"):
            path = write_json(tmp_path / f"{attempt}-{side}-source.json", metadata[attempt][side])
            source_paths[attempt][side] = path
            source_hashes[attempt][side] = hashlib.sha256(path.read_bytes()).hexdigest()

    first_failures = [13, 14, 15, 16, 17, 18]
    retry_failures = [7, 8, 9, 10, 11, 12]
    self_failures = [8, 9, 10, 11, 13, 14, 15, 16, 17, 18]
    out = tmp_path / "static-discrete.json"
    proc = subprocess.run(
        [
            "python3",
            str(ROOT / "scripts" / "verify" / "lib" / "reference_self_calibration.py"),
            "--reference-self-series",
            str(write_series(
                tmp_path / "reference-self.txt",
                [0.80 if index in self_failures else 0.96 for index in range(1, 19)],
            )),
            "--first-cross-series",
            str(write_series(
                tmp_path / "first-cross.txt",
                [0.80 if index in first_failures else 0.96 for index in range(1, 19)],
            )),
            "--retry-cross-series",
            str(write_series(
                tmp_path / "retry-cross.txt",
                [0.80 if index in retry_failures else 0.96 for index in range(1, 19)],
            )),
            "--threshold",
            "0.90",
            "--expected-rows",
            "18",
            "--first-offset",
            "0",
            "--retry-offset",
            "0",
            "--first-attempt",
            "btn",
            "--retry-attempt",
            "btn-retry-1",
            "--action",
            "hover:.target",
            "--first-capture-retry",
            str(write_json(
                tmp_path / "first-capture-retry.json",
                _with_source_binding(
                    _early_window_receipt(
                        rows=18,
                        failure_rows=first_failures,
                        arc_within_tolerance=False,
                        arc_delta_frames=24,
                        arc_max_delta_frames=18,
                        ref_duration_frames=54,
                        impl_duration_frames=78,
                    ),
                    attempt="first",
                    metadata=metadata,
                    hashes=source_hashes,
                ),
            )),
            "--retry-capture-retry",
            str(write_json(
                tmp_path / "retry-capture-retry.json",
                _with_source_binding(
                    _early_window_receipt(
                        rows=18,
                        failure_rows=retry_failures,
                        arc_within_tolerance=True,
                        arc_delta_frames=6,
                        arc_max_delta_frames=18,
                        ref_duration_frames=24,
                        impl_duration_frames=30,
                    ),
                    attempt="retry",
                    metadata=metadata,
                    hashes=source_hashes,
                ),
            )),
            "--first-ref-target",
            str(write_json(tmp_path / "first-ref-target.json", _target_payload(prop="color"))),
            "--first-impl-target",
            str(write_json(tmp_path / "first-impl-target.json", _target_payload(prop="color"))),
            "--retry-ref-target",
            str(write_json(tmp_path / "retry-ref-target.json", _target_payload(prop="color"))),
            "--retry-impl-target",
            str(write_json(tmp_path / "retry-impl-target.json", _target_payload(prop="color"))),
            "--first-ref-action",
            str(write_json(tmp_path / "first-ref-action.json", _hover_action_payloads(prop="color")["firstRef"])),
            "--first-impl-action",
            str(write_json(tmp_path / "first-impl-action.json", _hover_action_payloads(prop="color")["firstImpl"])),
            "--retry-ref-action",
            str(write_json(tmp_path / "retry-ref-action.json", _hover_action_payloads(prop="color")["retryRef"])),
            "--retry-impl-action",
            str(write_json(tmp_path / "retry-impl-action.json", _hover_action_payloads(prop="color")["retryImpl"])),
            "--first-ref-source-metadata",
            str(source_paths["first"]["ref"]),
            "--first-impl-source-metadata",
            str(source_paths["first"]["impl"]),
            "--retry-ref-source-metadata",
            str(source_paths["retry"]["ref"]),
            "--retry-impl-source-metadata",
            str(source_paths["retry"]["impl"]),
            "--standard-calibration-receipt",
            str(write_json(tmp_path / "standard-calibration.json", _failed_standard_receipt(self_failures))),
            "--trigger-type",
            "css-hover",
            "--provenance",
            "css-hover",
            "--static-discrete",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "pass-after-static-discrete-hover-state-calibration"


@pytest.mark.parametrize(
    ("action_patch", "target_patch", "metadata_patch", "expected_metric"),
    [
        ({"pointerReachable": False}, {}, {}, ("statePayloadsValid", False)),
        ({"matchIndex": 1}, {}, {}, ("statePayloadsValid", False)),
        (
            {
                "state": {
                    **cast(dict[str, object], _target_payload()["state"]),
                    "phase": "hover",
                    "watchedStyle": {
                        **cast(
                            dict[str, object],
                            cast(dict[str, object], _target_payload()["state"])["watchedStyle"],
                        ),
                        "color": "rgb(1, 1, 1)",
                        "fontWeight": "600",
                    },
                    "ancestorClassPath": ["a.link.active", "li.item.hover", "ul.menu"],
                }
            },
            {},
            {},
            ("statePayloadsValid", False),
        ),
        (
            {
                "state": {
                    **cast(dict[str, object], _target_payload()["state"]),
                    "phase": "hover",
                    "ancestorClassPath": ["a.link.active", "li.item.hover", "ul.menu"],
                }
            },
            {},
            {},
            ("statePayloadsValid", False),
        ),
        ({}, {"transition": _target_payload(prop="all")["transition"]}, {}, ("statePayloadsValid", False)),
        ({}, {}, {"avgFrameRate": "9/1"}, ("sourceMetadataValid", False)),
        ({}, {}, {"sourceToExtractedRatio": 5}, ("sourceMetadataValid", False)),
        ({}, {}, {"sourceFps": 12.0}, ("sourceMetadataValid", False)),
        ({}, {}, {"extractedFps": 50}, ("sourceMetadataValid", False)),
        ({}, {}, {"extractedFps": True}, ("sourceMetadataValid", False)),
        ({}, {}, {"extractedFps": 0}, ("sourceMetadataValid", False)),
        ({}, {}, {"extractedFps": float("nan")}, ("sourceMetadataValid", False)),
        ({}, {}, {"extractedFps": 60.5}, ("sourceMetadataValid", False)),
    ],
)
def test_static_discrete_calibration_rejects_tampered_state_or_source_contract(
    action_patch: dict[str, object],
    target_patch: dict[str, object],
    metadata_patch: dict[str, object],
    expected_metric: tuple[str, bool],
) -> None:
    self_failure_rows = [8, 9, 10, 11, 13, 14, 15, 16, 17, 18]
    self_values = [0.80 if index in self_failure_rows else 0.96 for index in range(1, 19)]
    first_values = [0.80 if index in (13, 14, 15, 16, 17, 18) else 0.96 for index in range(1, 20)]
    retry_values = [0.80 if index in (7, 8, 9, 10, 11, 12) else 0.96 for index in range(1, 19)]
    metadata = _source_metadata_payloads()
    if metadata_patch:
        metadata["first"]["ref"] = {**metadata["first"]["ref"], **metadata_patch}
    hashes = _source_metadata_hashes()
    target_payloads = _target_payloads(prop="color")
    if target_patch:
        target_payloads["firstRef"] = {**target_payloads["firstRef"], **target_patch}
    action_payloads = _hover_action_payloads(prop="color")
    if action_patch:
        action_payloads["firstRef"] = {**action_payloads["firstRef"], **action_patch}

    payload = calibrate_static_discrete(
        self_values,
        retry_values,
        threshold=0.90,
        expected_rows=18,
        first_cross_values=first_values,
        first_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=19,
                failure_rows=[13, 14, 15, 16, 17, 18],
                arc_within_tolerance=False,
                arc_delta_frames=24,
                arc_max_delta_frames=18,
                ref_duration_frames=54,
                impl_duration_frames=78,
            ),
            attempt="first",
            metadata=metadata,
            hashes=hashes,
        ),
        retry_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=18,
                failure_rows=[7, 8, 9, 10, 11, 12],
                arc_within_tolerance=True,
                arc_delta_frames=6,
                arc_max_delta_frames=18,
                ref_duration_frames=24,
                impl_duration_frames=30,
            ),
            attempt="retry",
            metadata=metadata,
            hashes=hashes,
        ),
        reference_self_receipt=_failed_standard_receipt(self_failure_rows),
        target_payloads=target_payloads,
        action_payloads=action_payloads,
        source_metadata=metadata,
        source_metadata_sha256=hashes,
    )

    metric, expected_value = expected_metric
    assert payload["status"] == "static-discrete-hover-state-calibration-failed"
    assert payload["metrics"][metric] is expected_value
    assert payload["metrics"]["captureReceiptsValid"] is True


def test_static_discrete_calibration_ignores_synthetic_hover_helper_class_path_tokens() -> None:
    self_failure_rows = [8, 9, 10, 11, 13, 14, 15, 16, 17, 18]
    self_values = [0.80 if index in self_failure_rows else 0.96 for index in range(1, 19)]
    first_values = [0.80 if index in (13, 14, 15, 16, 17, 18) else 0.96 for index in range(1, 20)]
    retry_values = [0.80 if index in (7, 8, 9, 10, 11, 12) else 0.96 for index in range(1, 19)]
    metadata = _source_metadata_payloads()
    hashes = _source_metadata_hashes()
    target_payloads = _target_payloads(prop="color")
    action_payloads = _hover_action_payloads(prop="color")
    for name in ("firstImpl", "retryImpl"):
        target_payloads[name] = _add_ancestor_class_token(target_payloads[name], "h_85")
        action_payloads[name] = _add_ancestor_class_token(action_payloads[name], "h_85")

    payload = calibrate_static_discrete(
        self_values,
        retry_values,
        threshold=0.90,
        expected_rows=18,
        first_cross_values=first_values,
        first_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=19,
                failure_rows=[13, 14, 15, 16, 17, 18],
                arc_within_tolerance=False,
                arc_delta_frames=24,
                arc_max_delta_frames=18,
                ref_duration_frames=24,
                impl_duration_frames=48,
            ),
            attempt="first",
            metadata=metadata,
            hashes=hashes,
        ),
        retry_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=18,
                failure_rows=[7, 8, 9, 10, 11, 12],
                arc_within_tolerance=False,
                arc_delta_frames=24,
                arc_max_delta_frames=18,
                ref_duration_frames=24,
                impl_duration_frames=48,
            ),
            attempt="retry",
            metadata=metadata,
            hashes=hashes,
        ),
        reference_self_receipt=_failed_standard_receipt(self_failure_rows),
        target_payloads=target_payloads,
        action_payloads=_with_hover_proofs(action_payloads),
        source_metadata=metadata,
        source_metadata_sha256=hashes,
    )

    assert payload["status"] == "pass-after-static-discrete-hover-state-calibration"
    assert payload["metrics"]["statePayloadsValid"] is True
    assert payload["metrics"]["runtimeTimingProofValid"] is True


def test_static_discrete_accepts_disjoint_static_delta_with_explicit_transition_property() -> None:
    target_payloads = _target_payloads(prop="color")
    action_payloads = _with_bound_hover_proofs(
        _hover_action_payloads(prop="color"),
        target_payloads,
    )

    payload = _calibrate_static_discrete_with_payloads(target_payloads, action_payloads)

    assert payload["status"] == "pass-after-static-discrete-hover-state-calibration"
    assert payload["metrics"]["statePayloadsValid"] is True
    assert payload["metrics"]["state"]["stateChangeMode"] == "static-discrete"


def test_static_discrete_rejects_mixed_declared_and_undeclared_hover_delta() -> None:
    target_payloads = _target_payloads(prop="color")
    action_payloads = _hover_action_payloads(prop="color")
    for name, payload in action_payloads.items():
        action_payloads[name] = _replace_watched_style(
            payload,
            color="rgb(1, 1, 1)",
            fontWeight="600",
        )
    action_payloads = _with_bound_hover_proofs(action_payloads, target_payloads)
    for name, payload in action_payloads.items():
        proof = dict(cast(dict[str, object], payload["hoverProof"]))
        proof["changedStyleKeys"] = ["color", "fontWeight"]
        action_payloads[name] = {**payload, "hoverProof": proof}

    calibration = _calibrate_static_discrete_with_payloads(
        target_payloads,
        action_payloads,
    )

    assert calibration["status"] == "static-discrete-hover-state-calibration-failed"
    assert calibration["metrics"]["statePayloadsValid"] is False
    assert calibration["metrics"]["statePayloadReason"] == "mixed-declared-transition-change"


def test_static_discrete_accepts_color_currentcolor_derived_delta_as_declared_transition() -> None:
    target_payloads = _target_payloads(prop="color")
    action_payloads = _hover_action_payloads(prop="color")
    for name, payload in action_payloads.items():
        action_payloads[name] = _replace_watched_style(
            payload,
            color="rgb(1, 1, 1)",
            borderTopColor="rgb(1, 1, 1)",
            borderRightColor="rgb(1, 1, 1)",
            borderBottomColor="rgb(1, 1, 1)",
            borderLeftColor="rgb(1, 1, 1)",
            textDecorationColor="rgb(1, 1, 1)",
            fontWeight="400",
        )
    action_payloads = _with_bound_declared_transition_proofs(
        action_payloads,
        target_payloads,
    )
    for name, payload in action_payloads.items():
        proof = dict(cast(dict[str, object], payload["hoverProof"]))
        proof["changedStyleKeys"] = [
            "borderBottomColor",
            "borderLeftColor",
            "borderRightColor",
            "borderTopColor",
            "color",
            "textDecorationColor",
        ]
        action_payloads[name] = {**payload, "hoverProof": proof}

    calibration = _calibrate_static_discrete_with_payloads(
        target_payloads,
        action_payloads,
    )

    assert calibration["status"] == "pass-after-static-discrete-hover-state-calibration"
    assert calibration["metrics"]["statePayloadsValid"] is True
    assert calibration["metrics"]["state"]["stateChangeMode"] == "declared-transition"
    assert calibration["metrics"]["state"]["declaredTransitionChanges"] == [
        "borderBottomColor",
        "borderLeftColor",
        "borderRightColor",
        "borderTopColor",
        "color",
        "textDecorationColor",
    ]


def test_static_discrete_does_not_classify_zero_duration_property_as_declared_transition() -> None:
    transition = _target_payload(
        prop="color,border-color",
        duration="0,0.2",
    )["transition"]
    target_payloads = _target_payloads()
    action_payloads = _hover_action_payloads()
    for name in target_payloads:
        target_payloads[name] = {**target_payloads[name], "transition": transition}
        action_payloads[name] = _replace_watched_style(
            {**action_payloads[name], "transition": transition},
            color="rgb(1, 1, 1)",
            fontWeight="400",
        )
    action_payloads = _with_bound_hover_proofs(action_payloads, target_payloads)
    for name, payload in action_payloads.items():
        proof = dict(cast(dict[str, object], payload["hoverProof"]))
        proof["changedStyleKeys"] = ["color"]
        action_payloads[name] = {**payload, "hoverProof": proof}

    calibration = _calibrate_static_discrete_with_payloads(
        target_payloads,
        action_payloads,
    )

    assert calibration["status"] == "pass-after-static-discrete-hover-state-calibration"
    assert calibration["metrics"]["statePayloadsValid"] is True
    assert calibration["metrics"]["state"]["stateChangeMode"] == "static-discrete"
    assert calibration["metrics"]["state"]["declaredTransitionChanges"] == []


def test_static_discrete_calibration_rejects_non_synthetic_class_path_tamper() -> None:
    self_failure_rows = [8, 9, 10, 11, 13, 14, 15, 16, 17, 18]
    self_values = [0.80 if index in self_failure_rows else 0.96 for index in range(1, 19)]
    first_values = [0.80 if index in (13, 14, 15, 16, 17, 18) else 0.96 for index in range(1, 20)]
    retry_values = [0.80 if index in (7, 8, 9, 10, 11, 12) else 0.96 for index in range(1, 19)]
    metadata = _source_metadata_payloads()
    hashes = _source_metadata_hashes()
    target_payloads = _target_payloads(prop="color")
    action_payloads = _hover_action_payloads(prop="color")
    target_payloads["firstImpl"] = _add_ancestor_class_token(target_payloads["firstImpl"], "other")
    action_payloads["firstImpl"] = _add_ancestor_class_token(action_payloads["firstImpl"], "other")

    payload = calibrate_static_discrete(
        self_values,
        retry_values,
        threshold=0.90,
        expected_rows=18,
        first_cross_values=first_values,
        first_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=19,
                failure_rows=[13, 14, 15, 16, 17, 18],
                arc_within_tolerance=False,
                arc_delta_frames=24,
                arc_max_delta_frames=18,
                ref_duration_frames=24,
                impl_duration_frames=48,
            ),
            attempt="first",
            metadata=metadata,
            hashes=hashes,
        ),
        retry_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=18,
                failure_rows=[7, 8, 9, 10, 11, 12],
                arc_within_tolerance=False,
                arc_delta_frames=24,
                arc_max_delta_frames=18,
                ref_duration_frames=24,
                impl_duration_frames=48,
            ),
            attempt="retry",
            metadata=metadata,
            hashes=hashes,
        ),
        reference_self_receipt=_failed_standard_receipt(self_failure_rows),
        target_payloads=target_payloads,
        action_payloads=_with_hover_proofs(action_payloads),
        source_metadata=metadata,
        source_metadata_sha256=hashes,
    )

    assert payload["status"] == "static-discrete-hover-state-calibration-failed"
    assert payload["metrics"]["statePayloadsValid"] is False


def test_static_discrete_calibration_keeps_id_class_path_segments_exact() -> None:
    target_payloads = _target_payloads(prop="color")
    action_payloads = _hover_action_payloads(prop="color")
    for name in ("firstRef", "firstImpl", "retryRef", "retryImpl"):
        target_payloads[name] = _replace_ancestor_class_path(
            target_payloads[name],
            ["div#panel.h_85", "li.item", "ul.menu"],
        )
        action_payloads[name] = _replace_ancestor_class_path(
            action_payloads[name],
            ["div#panel.h_85.active", "li.item.hover", "ul.menu"],
        )

    payload = _calibrate_static_discrete_with_payloads(
        target_payloads,
        _with_bound_hover_proofs(action_payloads, target_payloads),
    )
    metrics = cast(dict[str, object], payload["metrics"])

    assert payload["status"] == "pass-after-static-discrete-hover-state-calibration"
    assert metrics["statePayloadsValid"] is True
    assert metrics["runtimeTimingProofValid"] is True


def test_static_discrete_calibration_rejects_id_class_path_mismatch() -> None:
    target_payloads = _target_payloads(prop="color")
    action_payloads = _hover_action_payloads(prop="color")
    target_payloads["firstImpl"] = _replace_ancestor_class_path(
        target_payloads["firstImpl"],
        ["div#panel.h_85", "li.item", "ul.menu"],
    )
    action_payloads["firstImpl"] = _replace_ancestor_class_path(
        action_payloads["firstImpl"],
        ["div#panel.h_85.active", "li.item.hover", "ul.menu"],
    )

    payload = _calibrate_static_discrete_with_payloads(
        target_payloads,
        _with_hover_proofs(action_payloads),
    )
    metrics = cast(dict[str, object], payload["metrics"])

    assert payload["status"] == "static-discrete-hover-state-calibration-failed"
    assert metrics["statePayloadsValid"] is False


def test_static_discrete_calibration_rejects_malformed_helper_class_path() -> None:
    target_payloads = _target_payloads(prop="color")
    action_payloads = _hover_action_payloads(prop="color")
    target_payloads["firstImpl"] = _replace_ancestor_class_path(
        target_payloads["firstImpl"],
        [".h_85", "li.item", "ul.menu"],
    )
    action_payloads["firstImpl"] = _replace_ancestor_class_path(
        action_payloads["firstImpl"],
        [".h_85.active", "li.item.hover", "ul.menu"],
    )

    payload = _calibrate_static_discrete_with_payloads(
        target_payloads,
        _with_hover_proofs(action_payloads),
    )
    metrics = cast(dict[str, object], payload["metrics"])

    assert payload["status"] == "static-discrete-hover-state-calibration-failed"
    assert metrics["statePayloadsValid"] is False


def test_static_discrete_runtime_timing_proof_accepts_unexplained_arc_outlier() -> None:
    self_failure_rows = [8, 9, 10, 11, 13, 14, 15, 16, 17, 18]
    self_values = [0.80 if index in self_failure_rows else 0.96 for index in range(1, 19)]
    first_values = [0.80 if index in (13, 14, 15, 16, 17, 18) else 0.96 for index in range(1, 20)]
    retry_values = [0.80 if index in (7, 8, 9, 10, 11, 12) else 0.96 for index in range(1, 19)]
    metadata = _source_metadata_payloads()
    hashes = _source_metadata_hashes()

    payload = calibrate_static_discrete(
        self_values,
        retry_values,
        threshold=0.90,
        expected_rows=18,
        first_cross_values=first_values,
        first_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=19,
                failure_rows=[13, 14, 15, 16, 17, 18],
                arc_within_tolerance=False,
                arc_delta_frames=24,
                arc_max_delta_frames=18,
                ref_duration_frames=24,
                impl_duration_frames=48,
            ),
            attempt="first",
            metadata=metadata,
            hashes=hashes,
        ),
        retry_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=18,
                failure_rows=[7, 8, 9, 10, 11, 12],
                arc_within_tolerance=False,
                arc_delta_frames=24,
                arc_max_delta_frames=18,
                ref_duration_frames=24,
                impl_duration_frames=48,
            ),
            attempt="retry",
            metadata=metadata,
            hashes=hashes,
        ),
        reference_self_receipt=_failed_standard_receipt(self_failure_rows),
        target_payloads=_target_payloads(prop="color"),
        action_payloads=_with_hover_proofs(_hover_action_payloads(prop="color")),
        source_metadata=metadata,
        source_metadata_sha256=hashes,
    )

    assert payload["status"] == "pass-after-static-discrete-hover-state-calibration"
    assert payload["metrics"]["arcExplained"] is False
    assert payload["metrics"]["runtimeTimingProofValid"] is True
    assert payload["metrics"]["runtimeTimingRelaxationUsed"] is True


def test_static_discrete_runtime_timing_proof_accepts_naver_adjacent_bin_compression() -> None:
    self_failure_rows = [13, 14, 15, 16, 17, 18]
    first_failure_rows = [13, 14, 15, 16, 17, 18]
    retry_failure_rows = [8, 9, 10, 11, 13, 14, 15, 16, 17, 18]
    expected_rows = 342
    self_values = [0.80 if index in self_failure_rows else 0.96 for index in range(1, expected_rows + 1)]
    first_values = [0.80 if index in first_failure_rows else 0.96 for index in range(1, expected_rows + 1)]
    retry_values = [0.80 if index in retry_failure_rows else 0.96 for index in range(1, expected_rows + 1)]
    metadata = _source_metadata_payloads(ratio=6)
    hashes = _source_metadata_hashes()

    payload = calibrate_static_discrete(
        self_values,
        retry_values,
        threshold=0.90,
        expected_rows=expected_rows,
        first_offset=492,
        retry_offset=486,
        first_cross_values=first_values,
        first_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=expected_rows,
                failure_rows=first_failure_rows,
                arc_within_tolerance=True,
                arc_delta_frames=24,
                arc_max_delta_frames=30,
                ref_duration_frames=42,
                impl_duration_frames=18,
            ),
            attempt="first",
            metadata=metadata,
            hashes=hashes,
        ),
        retry_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=expected_rows,
                failure_rows=retry_failure_rows,
                arc_within_tolerance=True,
                arc_delta_frames=30,
                arc_max_delta_frames=30,
                ref_duration_frames=42,
                impl_duration_frames=12,
            ),
            attempt="retry",
            metadata=metadata,
            hashes=hashes,
        ),
        reference_self_receipt=_failed_standard_receipt(self_failure_rows),
        target_payloads=_target_payloads(prop="color"),
        action_payloads=_with_hover_proofs(_hover_action_payloads(prop="color")),
        source_metadata=metadata,
        source_metadata_sha256=hashes,
    )

    assert payload["status"] == "pass-after-static-discrete-hover-state-calibration"
    assert payload["attempts"] == {
        "first": {"id": "first", "offset": 492},
        "retry": {"id": "retry", "offset": 486},
    }
    assert payload["expectedRows"] == 342
    assert payload["metrics"]["firstCross"]["sourceBins"] == [85]
    assert payload["metrics"]["retryCross"]["sourceBins"] == [83, 84]
    assert payload["metrics"]["referenceSelf"]["sourceBinsByAttempt"]["retry"] == [84]
    assert payload["metrics"]["sourceBinSubsetOfReferenceSelf"] is False
    assert payload["metrics"]["tailRowsPassingOutsideReferenceSelfBins"] is False
    assert payload["metrics"]["earlyWindowTailRowsPassing"] is True
    assert payload["metrics"]["arcExplained"] is True
    assert payload["metrics"]["runtimeTimingProofValid"] is True
    assert payload["metrics"]["runtimeTimingRelaxationUsed"] is True


def _live_header_runtime_row_drift_payload(
    *,
    first_rows: int = 342,
    retry_rows: int = 348,
    first_early_window_rows: int = 18,
    retry_early_window_rows: int = 18,
    self_failure_rows: list[int] | None = None,
    first_failure_rows: list[int] | None = None,
    retry_failure_rows: list[int] | None = None,
    first_offset: int = 534,
    retry_offset: int = 528,
    source_ratio: int = 6,
    first_receipt_patch: dict[str, object] | None = None,
    retry_receipt_patch: dict[str, object] | None = None,
    metadata_patch: dict[str, object] | None = None,
    first_values_patch: dict[int, float] | None = None,
) -> dict[str, object]:
    expected_rows = 348
    self_failure_rows = (
        [13, 14, 15, 16, 17, 18] if self_failure_rows is None else self_failure_rows
    )
    first_failure_rows = (
        [8, 9, 10, 11, 13, 14, 15, 16, 17, 18]
        if first_failure_rows is None
        else first_failure_rows
    )
    retry_failure_rows = (
        [13, 14, 15, 16, 17, 18]
        if retry_failure_rows is None
        else retry_failure_rows
    )
    self_values = [0.80 if index in self_failure_rows else 0.96 for index in range(1, expected_rows + 1)]
    first_values = [0.80 if index in first_failure_rows else 0.96 for index in range(1, first_rows + 1)]
    if first_values_patch:
        for row, value in first_values_patch.items():
            if 1 <= row <= len(first_values):
                first_values[row - 1] = value
    retry_values = [0.80 if index in retry_failure_rows else 0.96 for index in range(1, retry_rows + 1)]
    metadata = _source_metadata_payloads(ratio=source_ratio)
    extracted_fps = 60.0
    if metadata_patch:
        for attempt in ("first", "retry"):
            for side in ("ref", "impl"):
                metadata[attempt][side].update(metadata_patch)
    hashes = _source_metadata_hashes()
    first_receipt = _early_window_receipt(
        rows=first_rows,
        failure_rows=[row for row in first_failure_rows if row <= first_rows],
        early_window_seconds=first_early_window_rows / extracted_fps,
        extracted_fps=extracted_fps,
        arc_within_tolerance=False,
        arc_delta_frames=18,
        arc_max_delta_frames=18,
        ref_duration_frames=0,
        impl_duration_frames=18,
    )
    first_receipt["earlyWindowRows"] = first_early_window_rows
    if first_receipt_patch:
        first_receipt.update(first_receipt_patch)
    retry_receipt = _early_window_receipt(
        rows=retry_rows,
        failure_rows=retry_failure_rows,
        early_window_seconds=retry_early_window_rows / extracted_fps,
        arc_within_tolerance=True,
        arc_delta_frames=6,
        arc_max_delta_frames=18,
        ref_duration_frames=42,
        impl_duration_frames=48,
        extracted_fps=extracted_fps,
    )
    retry_receipt["earlyWindowRows"] = retry_early_window_rows
    if retry_receipt_patch:
        retry_receipt.update(retry_receipt_patch)

    return calibrate_static_discrete(
        self_values,
        retry_values,
        threshold=0.90,
        expected_rows=expected_rows,
        first_offset=first_offset,
        retry_offset=retry_offset,
        first_cross_values=first_values,
        first_capture_receipt=_with_source_binding(
            first_receipt,
            attempt="first",
            metadata=metadata,
            hashes=hashes,
        ),
        retry_capture_receipt=_with_source_binding(
            retry_receipt,
            attempt="retry",
            metadata=metadata,
            hashes=hashes,
        ),
        reference_self_receipt=_failed_standard_receipt(self_failure_rows),
        target_payloads=_target_payloads(prop="color"),
        action_payloads=_with_hover_proofs(_hover_action_payloads(prop="color")),
        source_metadata=metadata,
        source_metadata_sha256=hashes,
    )


def test_static_discrete_runtime_timing_proof_accepts_live_header_row_drift() -> None:
    payload = _live_header_runtime_row_drift_payload()
    metrics = cast(dict[str, Any], payload["metrics"])

    assert payload["status"] == "pass-after-static-discrete-hover-state-calibration"
    assert payload["expectedRows"] == 348
    assert cast(dict[str, object], metrics["referenceSelf"])["rows"] == 348
    assert cast(dict[str, object], metrics["firstCross"])["rows"] == 342
    assert cast(dict[str, object], metrics["retryCross"])["rows"] == 348
    assert metrics["rowCountsCoverExpectedWindow"] is False
    assert metrics["captureReceiptsValid"] is False
    assert metrics["runtimeCaptureReceiptsValid"] is True
    assert metrics["runtimeRowCountDriftValid"] is True
    assert metrics["runtimeTimingProofValid"] is True
    assert metrics["runtimeCrossSourceBinsValid"] is True
    assert metrics["runtimeTimingRelaxationUsed"] is True


def test_static_discrete_runtime_timing_proof_accepts_two_source_frame_cross_attempt_drift() -> None:
    payload = _live_header_runtime_row_drift_payload(first_rows=342, retry_rows=354)
    metrics = cast(dict[str, Any], payload["metrics"])

    assert payload["status"] == "pass-after-static-discrete-hover-state-calibration"
    assert metrics["runtimeRowCountDriftValid"] is True
    assert metrics["runtimeTimingProofValid"] is True
    assert metrics["runtimeTimingRelaxationUsed"] is True


def test_static_discrete_runtime_timing_proof_rejects_more_than_two_source_frame_runtime_row_drift() -> None:
    payload = _live_header_runtime_row_drift_payload(first_rows=342, retry_rows=355)
    metrics = cast(dict[str, Any], payload["metrics"])

    assert payload["status"] == "static-discrete-hover-state-calibration-failed"
    assert metrics["runtimeRowCountDriftValid"] is False
    assert metrics["runtimeTimingProofValid"] is True
    assert metrics["runtimeTimingRelaxationUsed"] is False


def test_static_discrete_runtime_timing_proof_accepts_final15_cross_source_bins() -> None:
    payload = _live_header_runtime_row_drift_payload(
        first_offset=570,
        retry_offset=534,
        first_failure_rows=[1, 2, 3, 4, 5, 6],
        retry_failure_rows=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
    )
    metrics = cast(dict[str, Any], payload["metrics"])

    assert payload["status"] == "pass-after-static-discrete-hover-state-calibration"
    assert cast(dict[str, object], metrics["firstCross"])["sourceBins"] == [96]
    assert cast(dict[str, object], metrics["retryCross"])["sourceBins"] == [90, 91]
    assert metrics["runtimeTimingProofValid"] is True
    assert metrics["runtimeCrossSourceBinsValid"] is True
    assert metrics["runtimeTimingRelaxationUsed"] is True


def _assert_runtime_timing_rejects_unsafe_cross_source_bins(
    payload: dict[str, object],
    expected_first_bins: list[int],
    expected_retry_bins: list[int],
    *,
    expected_resampled: bool = True,
) -> None:
    metrics = cast(dict[str, Any], payload["metrics"])
    shape = cast(dict[str, object], metrics["runtimeCrossSourceBins"])

    assert payload["status"] == "static-discrete-hover-state-calibration-failed"
    assert cast(dict[str, object], metrics["firstCross"])["sourceBins"] == expected_first_bins
    assert cast(dict[str, object], metrics["retryCross"])["sourceBins"] == expected_retry_bins
    assert metrics["runtimeCaptureReceiptsValid"] is True
    assert metrics["runtimeTimingProofValid"] is True
    assert metrics["runtimeCrossSourceBinsValid"] is False
    assert metrics["runtimeTimingRelaxationUsed"] is False
    assert shape["resampled"] is expected_resampled
    if expected_resampled:
        assert metrics["runtimeRowCountDriftValid"] is True


def test_static_discrete_runtime_timing_proof_rejects_scattered_cross_source_bins() -> None:
    _assert_runtime_timing_rejects_unsafe_cross_source_bins(
        _live_header_runtime_row_drift_payload(
            first_offset=570,
            first_failure_rows=[1, 13],
        ),
        [96, 98],
        [91],
    )


def test_static_discrete_runtime_timing_proof_rejects_more_than_two_cross_source_bins() -> None:
    _assert_runtime_timing_rejects_unsafe_cross_source_bins(
        _live_header_runtime_row_drift_payload(
            first_offset=570,
            first_failure_rows=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13],
        ),
        [96, 97, 98],
        [91],
    )


def test_static_discrete_runtime_timing_proof_rejects_unresampled_source_ratio() -> None:
    _assert_runtime_timing_rejects_unsafe_cross_source_bins(
        _live_header_runtime_row_drift_payload(source_ratio=1),
        [542, 543, 544, 545, 547, 548, 549, 550, 551, 552],
        [541, 542, 543, 544, 545, 546],
        expected_resampled=False,
    )


def test_static_discrete_runtime_timing_proof_accepts_clean_reference_self() -> None:
    payload = _live_header_runtime_row_drift_payload(self_failure_rows=[])
    metrics = cast(dict[str, Any], payload["metrics"])

    assert payload["status"] == "pass-after-static-discrete-hover-state-calibration"
    assert cast(dict[str, object], metrics["referenceSelf"])["failureRows"] == []
    assert metrics["sourceBinSubsetOfReferenceSelf"] is False
    assert metrics["tailRowsPassingOutsideReferenceSelfBins"] is False
    assert metrics["referenceSelfCleanOrFailuresInsideExpectedWindow"] is True
    assert metrics["runtimeCaptureReceiptsValid"] is True
    assert metrics["runtimeRowCountDriftValid"] is True
    assert metrics["runtimeTimingProofValid"] is True
    assert metrics["runtimeCrossSourceBinsValid"] is True
    assert metrics["statePayloadsValid"] is True
    assert metrics["runtimeTimingRelaxationUsed"] is True


def test_static_discrete_runtime_timing_proof_accepts_integral_float_source_fps() -> None:
    payload = _live_header_runtime_row_drift_payload(metadata_patch={"extractedFps": 60.0})
    metrics = cast(dict[str, Any], payload["metrics"])

    assert payload["status"] == "pass-after-static-discrete-hover-state-calibration"
    assert metrics["sourceMetadataValid"] is True
    assert metrics["runtimeCaptureReceiptsValid"] is True


@pytest.mark.parametrize(
    ("sidecar_selector", "expected_pass"),
    [
        (".btn", True),
        (".other", False),
    ],
)
def test_static_discrete_runtime_timing_proof_binds_sidecar_selector_to_action(
    sidecar_selector: str,
    expected_pass: bool,
) -> None:
    metadata = _source_metadata_payloads(ratio=6)
    hashes = _source_metadata_hashes()
    expected_rows = 348
    self_failure_rows = [13, 14, 15, 16, 17, 18]
    first_failure_rows = [8, 9, 10, 11, 13, 14, 15, 16, 17, 18]
    retry_failure_rows = [13, 14, 15, 16, 17, 18]
    first_receipt = _early_window_receipt(
        rows=342,
        failure_rows=first_failure_rows,
        selector=".btn",
        early_window_seconds=18 / 60,
        arc_within_tolerance=False,
        arc_delta_frames=18,
        arc_max_delta_frames=18,
        ref_duration_frames=0,
        impl_duration_frames=18,
    )
    first_receipt["earlyWindowRows"] = 18
    sidecar_patch: dict[str, dict[str, object]] = {
        name: {"selector": sidecar_selector}
        for name in ("firstRef", "firstImpl", "retryRef", "retryImpl")
    }

    payload = calibrate_static_discrete(
        [0.80 if index in self_failure_rows else 0.96 for index in range(1, expected_rows + 1)],
        [0.80 if index in retry_failure_rows else 0.96 for index in range(1, expected_rows + 1)],
        threshold=0.90,
        expected_rows=expected_rows,
        first_offset=534,
        retry_offset=528,
        action="hover:.btn",
        first_cross_values=[
            0.80 if index in first_failure_rows else 0.96
            for index in range(1, 343)
        ],
        first_capture_receipt=_with_source_binding(
            first_receipt,
            attempt="first",
            metadata=metadata,
            hashes=hashes,
        ),
        retry_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=expected_rows,
                failure_rows=retry_failure_rows,
                selector=".btn",
                arc_within_tolerance=True,
                arc_delta_frames=6,
                arc_max_delta_frames=18,
                ref_duration_frames=42,
                impl_duration_frames=48,
            ),
            attempt="retry",
            metadata=metadata,
            hashes=hashes,
        ),
        reference_self_receipt=_failed_standard_receipt(self_failure_rows),
        target_payloads=_target_payloads(prop="color", **sidecar_patch),
        action_payloads=_with_hover_proofs(
            _hover_action_payloads(prop="color", **sidecar_patch),
            **sidecar_patch,
        ),
        source_metadata=metadata,
        source_metadata_sha256=hashes,
    )
    metrics = cast(dict[str, Any], payload["metrics"])

    assert (payload["status"] == "pass-after-static-discrete-hover-state-calibration") is expected_pass
    assert metrics["statePayloadsValid"] is expected_pass
    if not expected_pass:
        assert metrics["statePayloadReason"] == "target-payload-invalid"
        assert metrics["runtimeTimingProofValid"] is False


@pytest.mark.parametrize(
    (
        "target_rect_patch",
        "action_rect_patch",
        "all_action_rect_patch",
        "expected_pass",
    ),
    [
        ({"x": 96, "y": 72, "width": 160, "height": 120}, None, None, True),
        (None, {"x": 96, "y": 72, "width": 160, "height": 120}, None, False),
        (None, None, {"x": 80, "y": 60, "width": 200, "height": 120}, True),
        (None, None, {"x": 80, "y": 60, "width": 201, "height": 120}, False),
    ],
)
def test_static_discrete_runtime_timing_proof_binds_symmetric_hover_rect_delta(
    target_rect_patch: dict[str, object] | None,
    action_rect_patch: dict[str, object] | None,
    all_action_rect_patch: dict[str, object] | None,
    expected_pass: bool,
) -> None:
    target_payloads = _target_payloads(prop="color")
    action_payloads = _with_hover_proofs(_hover_action_payloads(prop="color"))
    if target_rect_patch is not None:
        target_payloads["retryImpl"] = {
            **target_payloads["retryImpl"],
            "rect": target_rect_patch,
        }
        action_payloads["retryImpl"] = {
            **action_payloads["retryImpl"],
            "rect": target_rect_patch,
        }
    if action_rect_patch is not None:
        action_payloads["retryImpl"] = {
            **action_payloads["retryImpl"],
            "rect": action_rect_patch,
        }
    if all_action_rect_patch is not None:
        for name in action_payloads:
            action_payloads[name] = {
                **action_payloads[name],
                "rect": all_action_rect_patch,
            }

    metadata = _source_metadata_payloads(ratio=6)
    hashes = _source_metadata_hashes()
    expected_rows = 348
    self_failure_rows = [13, 14, 15, 16, 17, 18]
    first_failure_rows = [8, 9, 10, 11, 13, 14, 15, 16, 17, 18]
    retry_failure_rows = [13, 14, 15, 16, 17, 18]
    first_receipt = _early_window_receipt(
        rows=342,
        failure_rows=first_failure_rows,
        early_window_seconds=18 / 60,
        arc_within_tolerance=False,
        arc_delta_frames=18,
        arc_max_delta_frames=18,
        ref_duration_frames=0,
        impl_duration_frames=18,
    )
    first_receipt["earlyWindowRows"] = 18
    payload = calibrate_static_discrete(
        [0.80 if index in self_failure_rows else 0.96 for index in range(1, expected_rows + 1)],
        [0.80 if index in retry_failure_rows else 0.96 for index in range(1, expected_rows + 1)],
        threshold=0.90,
        expected_rows=expected_rows,
        first_offset=534,
        retry_offset=528,
        first_cross_values=[
            0.80 if index in first_failure_rows else 0.96
            for index in range(1, 343)
        ],
        first_capture_receipt=_with_source_binding(
            first_receipt,
            attempt="first",
            metadata=metadata,
            hashes=hashes,
        ),
        retry_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=expected_rows,
                failure_rows=retry_failure_rows,
                arc_within_tolerance=True,
                arc_delta_frames=6,
                arc_max_delta_frames=18,
                ref_duration_frames=42,
                impl_duration_frames=48,
            ),
            attempt="retry",
            metadata=metadata,
            hashes=hashes,
        ),
        reference_self_receipt=_failed_standard_receipt(self_failure_rows),
        target_payloads=target_payloads,
        action_payloads=action_payloads,
        source_metadata=metadata,
        source_metadata_sha256=hashes,
    )
    metrics = cast(dict[str, Any], payload["metrics"])

    assert (payload["status"] == "pass-after-static-discrete-hover-state-calibration") is expected_pass
    if expected_pass:
        assert metrics["statePayloadsValid"] is True
    else:
        assert metrics["statePayloadsValid"] is False
        assert metrics["statePayloadReason"] == "action-target-mismatch"


def test_static_discrete_accepts_pure_css_hover_without_ancestor_class_mutation() -> None:
    target_payloads = _target_payloads(prop="color")
    action_payloads = _hover_action_payloads(prop="color")
    for name in action_payloads:
        state = dict(cast(dict[str, object], action_payloads[name]["state"]))
        watched = dict(cast(dict[str, object], state["watchedStyle"]))
        watched["fontWeight"] = "400"
        watched["color"] = "rgb(1, 1, 1)"
        state["watchedStyle"] = watched
        action_payloads[name] = {**action_payloads[name], "state": state}
        action_payloads[name] = _replace_ancestor_class_path(
            action_payloads[name],
            cast(
                list[str],
                cast(dict[str, object], target_payloads[name]["state"])[
                    "ancestorClassPath"
                ],
            ),
        )

    action_payloads = _with_bound_declared_transition_proofs(
        action_payloads,
        target_payloads,
    )
    for name in action_payloads:
        proof = dict(cast(dict[str, object], action_payloads[name]["hoverProof"]))
        proof["changedStyleKeys"] = ["color"]
        action_payloads[name] = {**action_payloads[name], "hoverProof": proof}

    payload = _calibrate_static_discrete_with_payloads(target_payloads, action_payloads)

    assert payload["status"] == "pass-after-static-discrete-hover-state-calibration"
    assert payload["metrics"]["statePayloadsValid"] is True
    assert payload["metrics"]["state"]["ancestorDelta"]["changed"] is False
    assert payload["metrics"]["state"]["declaredTransitionChanges"] == ["color"]
    assert payload["metrics"]["state"]["stateChangeMode"] == "declared-transition"
    assert payload["metrics"]["runtimeTimingProofValid"] is True


def test_declared_transition_runtime_proof_requires_observed_animation() -> None:
    target_payloads = _target_payloads(prop="color")
    action_payloads = _hover_action_payloads(prop="color")
    for name in action_payloads:
        action_payloads[name] = _replace_watched_style(
            action_payloads[name],
            color="rgb(1, 1, 1)",
            fontWeight="400",
        )
        action_payloads[name] = _replace_ancestor_class_path(
            action_payloads[name],
            cast(
                list[str],
                cast(dict[str, object], target_payloads[name]["state"])[
                    "ancestorClassPath"
                ],
            ),
        )
    action_payloads = _with_bound_declared_transition_proofs(
        action_payloads,
        target_payloads,
        max_active_animation_count=0,
    )
    for name in action_payloads:
        proof = dict(cast(dict[str, object], action_payloads[name]["hoverProof"]))
        proof["changedStyleKeys"] = ["color"]
        action_payloads[name] = {**action_payloads[name], "hoverProof": proof}

    payload = _calibrate_static_discrete_with_payloads(target_payloads, action_payloads)

    assert payload["status"] == "static-discrete-hover-state-calibration-failed"
    assert payload["metrics"]["state"]["stateChangeMode"] == "declared-transition"
    assert payload["metrics"]["runtimeTimingProofValid"] is False
    assert payload["metrics"]["runtimeTimingProof"]["reason"] == "incomplete-runtime-proof"


def test_declared_transition_runtime_proof_uses_changed_property_duration() -> None:
    transition = _target_payload(
        prop="color,transform",
        duration="0.2,1",
    )["transition"]
    target_payloads = _target_payloads()
    action_payloads = _hover_action_payloads()
    for name in target_payloads:
        target_payloads[name] = {**target_payloads[name], "transition": transition}
        action_payloads[name] = _replace_watched_style(
            {**action_payloads[name], "transition": transition},
            color="rgb(1, 1, 1)",
            fontWeight="400",
        )
        action_payloads[name] = _replace_ancestor_class_path(
            action_payloads[name],
            cast(
                list[str],
                cast(dict[str, object], target_payloads[name]["state"])[
                    "ancestorClassPath"
                ],
            ),
        )
    action_payloads = _with_bound_declared_transition_proofs(
        action_payloads,
        target_payloads,
    )
    for name in action_payloads:
        proof = dict(cast(dict[str, object], action_payloads[name]["hoverProof"]))
        proof["changedStyleKeys"] = ["color"]
        action_payloads[name] = {**action_payloads[name], "hoverProof": proof}

    payload = _calibrate_static_discrete_with_payloads(target_payloads, action_payloads)

    assert payload["status"] == "pass-after-static-discrete-hover-state-calibration"
    assert payload["metrics"]["state"]["stateChangeMode"] == "declared-transition"
    assert payload["metrics"]["runtimeTimingProofValid"] is True


def test_declared_transition_runtime_proof_supports_background_border_shorthands() -> None:
    target_payloads = _target_payloads()
    action_payloads = _hover_action_payloads()
    for name in target_payloads:
        delay = "0" if name.endswith("Ref") else "0,0"
        transition = _target_payload(
            prop="background,border",
            duration="0.2",
            delay=delay,
        )["transition"]
        target_payloads[name] = {**target_payloads[name], "transition": transition}
        action_payloads[name] = _replace_watched_style(
            {**action_payloads[name], "transition": transition},
            backgroundColor="rgb(113, 118, 128)",
            borderTopColor="rgb(113, 118, 128)",
            borderRightColor="rgb(113, 118, 128)",
            borderBottomColor="rgb(113, 118, 128)",
            borderLeftColor="rgb(113, 118, 128)",
            fontWeight="400",
        )
        action_payloads[name] = _replace_ancestor_class_path(
            action_payloads[name],
            cast(
                list[str],
                cast(dict[str, object], target_payloads[name]["state"])[
                    "ancestorClassPath"
                ],
            ),
        )
    action_payloads = _with_bound_declared_transition_proofs(
        action_payloads,
        target_payloads,
        max_active_animation_count=5,
    )
    changed_keys = [
        "backgroundColor",
        "borderBottomColor",
        "borderLeftColor",
        "borderRightColor",
        "borderTopColor",
    ]
    for name in action_payloads:
        proof = dict(cast(dict[str, object], action_payloads[name]["hoverProof"]))
        proof["changedStyleKeys"] = changed_keys
        action_payloads[name] = {**action_payloads[name], "hoverProof": proof}

    payload = _calibrate_static_discrete_with_payloads(target_payloads, action_payloads)

    assert payload["status"] == "pass-after-static-discrete-hover-state-calibration"
    assert payload["metrics"]["state"]["stateChangeMode"] == "declared-transition"
    assert payload["metrics"]["state"]["declaredTransitionChanges"] == changed_keys
    assert payload["metrics"]["runtimeTimingProofValid"] is True


@pytest.mark.parametrize(
    ("first_rows", "first_early_window_rows"),
    [
        (341, 18),
        (342, 343),
    ],
)
def test_static_discrete_runtime_timing_proof_rejects_unbounded_runtime_row_drift(
    first_rows: int,
    first_early_window_rows: int,
) -> None:
    payload = _live_header_runtime_row_drift_payload(
        first_rows=first_rows,
        first_early_window_rows=first_early_window_rows,
    )
    metrics = cast(dict[str, Any], payload["metrics"])

    assert payload["status"] == "static-discrete-hover-state-calibration-failed"
    assert metrics["runtimeRowCountDriftValid"] is False
    assert metrics["runtimeTimingProofValid"] is True
    assert metrics["runtimeTimingRelaxationUsed"] is False


@pytest.mark.parametrize(
    "first_receipt_patch",
    [
        {"earlyWindowRows": 30, "earlyWindowSeconds": 18 / 60},
        {"earlyWindowSeconds": None},
        {"earlyWindowSeconds": True},
        {"earlyWindowSeconds": 0.0},
        {"earlyWindowSeconds": 0.6},
        {"extractedFps": None},
        {"extractedFps": True},
        {"extractedFps": 30},
        {"extractedFps": 60.5},
    ],
)
def test_static_discrete_runtime_timing_proof_rejects_forged_runtime_receipt_window(
    first_receipt_patch: dict[str, object],
) -> None:
    payload = _live_header_runtime_row_drift_payload(first_receipt_patch=first_receipt_patch)
    metrics = cast(dict[str, Any], payload["metrics"])

    assert payload["status"] == "static-discrete-hover-state-calibration-failed"
    assert metrics["runtimeCaptureReceiptsValid"] is False
    assert metrics["runtimeTimingProofValid"] is True
    assert metrics["runtimeTimingRelaxationUsed"] is False


def test_static_discrete_runtime_timing_proof_rejects_runtime_receipt_fps_source_mismatch() -> None:
    payload = _live_header_runtime_row_drift_payload(first_receipt_patch={"extractedFps": 30})
    metrics = cast(dict[str, Any], payload["metrics"])

    assert payload["status"] == "static-discrete-hover-state-calibration-failed"
    assert metrics["runtimeCaptureReceiptsValid"] is False
    assert metrics["runtimeTimingRelaxationUsed"] is False


def test_static_discrete_runtime_timing_proof_rejects_runtime_receipt_without_stable_tail() -> None:
    payload = _live_header_runtime_row_drift_payload(first_rows=18, first_early_window_rows=18)
    metrics = cast(dict[str, Any], payload["metrics"])

    assert payload["status"] == "static-discrete-hover-state-calibration-failed"
    assert metrics["runtimeCaptureReceiptsValid"] is False
    assert metrics["runtimeTimingRelaxationUsed"] is False


def test_static_discrete_runtime_timing_proof_rejects_late_row_forged_window_bypass() -> None:
    payload = _live_header_runtime_row_drift_payload(
        first_rows=342,
        first_early_window_rows=300,
        first_receipt_patch={"earlyWindowSeconds": 18 / 60},
        first_values_patch={300: 0.80},
    )
    metrics = cast(dict[str, Any], payload["metrics"])

    assert payload["status"] == "static-discrete-hover-state-calibration-failed"
    assert metrics["runtimeCaptureReceiptsValid"] is False
    assert metrics["earlyWindowTailRowsPassing"] is True
    assert metrics["runtimeTimingRelaxationUsed"] is False


def test_static_discrete_runtime_timing_proof_rejects_missing_proof() -> None:
    self_failure_rows = [8, 9, 10, 11, 13, 14, 15, 16, 17, 18]
    self_values = [0.80 if index in self_failure_rows else 0.96 for index in range(1, 19)]
    first_values = [0.80 if index in (13, 14, 15, 16, 17, 18) else 0.96 for index in range(1, 19)]
    retry_values = [0.80 if index in (7, 8, 9, 10, 11, 12) else 0.96 for index in range(1, 19)]
    metadata = _source_metadata_payloads()
    hashes = _source_metadata_hashes()

    payload = calibrate_static_discrete(
        self_values,
        retry_values,
        threshold=0.90,
        expected_rows=18,
        first_cross_values=first_values,
        first_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=18,
                failure_rows=[13, 14, 15, 16, 17, 18],
                arc_within_tolerance=False,
                arc_delta_frames=24,
                arc_max_delta_frames=18,
                ref_duration_frames=24,
                impl_duration_frames=48,
            ),
            attempt="first",
            metadata=metadata,
            hashes=hashes,
        ),
        retry_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=18,
                failure_rows=[7, 8, 9, 10, 11, 12],
                arc_within_tolerance=False,
                arc_delta_frames=24,
                arc_max_delta_frames=18,
                ref_duration_frames=24,
                impl_duration_frames=48,
            ),
            attempt="retry",
            metadata=metadata,
            hashes=hashes,
        ),
        reference_self_receipt=_failed_standard_receipt(self_failure_rows),
        target_payloads=_target_payloads(prop="color"),
        action_payloads=_hover_action_payloads(prop="color"),
        source_metadata=metadata,
        source_metadata_sha256=hashes,
    )

    assert payload["status"] == "static-discrete-hover-state-calibration-failed"
    assert payload["metrics"]["runtimeTimingProofValid"] is False
    assert payload["metrics"]["runtimeTimingProof"]["reason"] == "missing-proof"


@pytest.mark.parametrize(
    ("proof_patch", "expected_reason"),
    [
        ({"firstRef": {"armed_at": 1005.0}}, "nonmonotonic-proof-timestamps"),
        (
            {
                "firstRef": {
                    "first_hover_raf": 1003.0,
                    "first_mutation": 1100.0,
                    "first_commit_raf": 1102.0,
                    "stable_at": 1120.0,
                }
            },
            "delayed-hover-commit",
        ),
        ({"firstRef": {"first_commit_raf": 1040.0, "stable_at": 1050.0}}, "commit-latency-drift"),
        ({"firstRef": {"stable_hover_raf_count": 1}}, "incomplete-runtime-proof"),
        ({"firstRef": {"stable_hover_raf_count": True}}, "incomplete-runtime-proof"),
        ({"firstRef": {"stable_hover_raf_count": 2.5}}, "incomplete-runtime-proof"),
        ({"firstRef": {"changed_style_keys": ["color", "fontWeight"]}}, "runtime-proof-state-mismatch"),
        ({"firstRef": {"commit_patch": {"hovered": False}}}, "runtime-proof-state-mismatch"),
        (
            {"firstRef": {"mutation_patch": {"ancestorClassPath": ["a.link", "li.item", "ul.menu"]}}},
            "runtime-proof-state-mismatch",
        ),
        ({"firstRef": {"max_active_animation_count": 1}}, "incomplete-runtime-proof"),
        ({"firstRef": {"match_index": 1}}, "proof-identity-mismatch"),
    ],
)
def test_static_discrete_runtime_timing_proof_rejects_tampered_runtime(
    proof_patch: dict[str, dict[str, object]],
    expected_reason: str,
) -> None:
    self_failure_rows = [8, 9, 10, 11, 13, 14, 15, 16, 17, 18]
    self_values = [0.80 if index in self_failure_rows else 0.96 for index in range(1, 19)]
    first_values = [0.80 if index in (13, 14, 15, 16, 17, 18) else 0.96 for index in range(1, 19)]
    retry_values = [0.80 if index in (7, 8, 9, 10, 11, 12) else 0.96 for index in range(1, 19)]
    metadata = _source_metadata_payloads()
    hashes = _source_metadata_hashes()

    payload = calibrate_static_discrete(
        self_values,
        retry_values,
        threshold=0.90,
        expected_rows=18,
        first_cross_values=first_values,
        first_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=18,
                failure_rows=[13, 14, 15, 16, 17, 18],
                arc_within_tolerance=False,
                arc_delta_frames=24,
                arc_max_delta_frames=18,
                ref_duration_frames=24,
                impl_duration_frames=48,
            ),
            attempt="first",
            metadata=metadata,
            hashes=hashes,
        ),
        retry_capture_receipt=_with_source_binding(
            _early_window_receipt(
                rows=18,
                failure_rows=[7, 8, 9, 10, 11, 12],
                arc_within_tolerance=False,
                arc_delta_frames=24,
                arc_max_delta_frames=18,
                ref_duration_frames=24,
                impl_duration_frames=48,
            ),
            attempt="retry",
            metadata=metadata,
            hashes=hashes,
        ),
        reference_self_receipt=_failed_standard_receipt(self_failure_rows),
        target_payloads=_target_payloads(prop="color"),
        action_payloads=_with_hover_proofs(_hover_action_payloads(prop="color"), **proof_patch),
        source_metadata=metadata,
        source_metadata_sha256=hashes,
    )

    assert payload["status"] == "static-discrete-hover-state-calibration-failed"
    assert payload["metrics"]["runtimeTimingProofValid"] is False
    assert payload["metrics"]["runtimeTimingProof"]["reason"] == expected_reason


@pytest.mark.parametrize(
    ("self_values", "cross_values", "expected_metric"),
    [
        ([0.72, 0.94, 0.95], [0.71, 0.82, 0.95], "alignedViolationCount"),
        ([0.50], [0.60, 0.80], "rowCountsMatchExpected"),
    ],
)
def test_reference_self_calibration_rejects_worse_or_wrong_cross_row_count(
    self_values: list[float],
    cross_values: list[float],
    expected_metric: str,
) -> None:
    payload = calibrate_distributions(
        self_values,
        cross_values,
        threshold=0.90,
        expected_rows=len(self_values),
        first_cross_values=self_values,
        first_capture_receipt=_early_window_receipt(
            rows=len(self_values),
            failure_rows=[1],
        ),
        retry_capture_receipt=_early_window_receipt(
            rows=len(self_values),
            failure_rows=[1],
        ),
    )

    assert payload["status"] == "reference-self-calibration-failed"
    if expected_metric == "rowCountsMatchExpected":
        assert payload["metrics"][expected_metric] is False
    else:
        assert (
            payload["metrics"]["retryCross"]["failureRowsSubsetOfReferenceSelf"]
            is False
        )


def test_reference_self_calibration_rejects_temporal_permutation() -> None:
    payload = calibrate_distributions(
        [0.70, 0.95, 0.98],
        [0.95, 0.70, 0.98],
        threshold=0.90,
        expected_rows=3,
        first_cross_values=[0.70, 0.95, 0.98],
        first_capture_receipt=_early_window_receipt(rows=3, failure_rows=[1]),
        retry_capture_receipt=_early_window_receipt(rows=3, failure_rows=[1]),
    )

    assert payload["status"] == "reference-self-calibration-failed"
    assert payload["metrics"]["retryCross"]["failureRowsSubsetOfReferenceSelf"] is False


def test_reference_self_calibration_rejects_truncated_cross_distribution() -> None:
    payload = calibrate_distributions(
        [0.70, 0.80, 0.95],
        [0.71, 0.81],
        threshold=0.90,
        expected_rows=3,
        first_cross_values=[0.70, 0.80, 0.95],
        first_capture_receipt=_early_window_receipt(rows=3, failure_rows=[1, 2]),
        retry_capture_receipt=_early_window_receipt(rows=3, failure_rows=[1, 2]),
    )

    assert payload["status"] == "reference-self-calibration-failed"
    assert payload["metrics"]["rowCountsMatchExpected"] is False


def test_reference_self_calibration_accepts_naver_shaped_retry_arc_recovery() -> None:
    values = [0.80] * 6 + [0.96] * 12
    payload = calibrate_distributions(
        values,
        values,
        threshold=0.90,
        expected_rows=18,
        first_cross_values=values,
        first_capture_receipt=_early_window_receipt(
            rows=18,
            failure_rows=list(range(1, 7)),
            arc_within_tolerance=False,
            arc_delta_frames=24,
            arc_max_delta_frames=18,
            ref_duration_frames=24,
            impl_duration_frames=48,
        ),
        retry_capture_receipt=_early_window_receipt(
            rows=18,
            failure_rows=list(range(1, 7)),
            arc_within_tolerance=True,
            arc_delta_frames=6,
            arc_max_delta_frames=18,
            ref_duration_frames=24,
            impl_duration_frames=30,
        ),
    )

    assert payload["status"] == "pass-after-reference-self-calibration"
    assert payload["metrics"]["captureReceiptsValid"] is True
    assert payload["metrics"]["retryCross"]["failureRows"] == list(range(1, 7))


def test_reference_self_calibration_accepts_long_stable_cross_tails() -> None:
    self_values = [0.80] * 6 + [0.96] * 12
    first_values = [0.80] * 6 + [0.96] * 336
    retry_values = [0.80] * 6 + [0.96] * 342

    payload = calibrate_distributions(
        self_values,
        retry_values,
        threshold=0.90,
        expected_rows=18,
        first_cross_values=first_values,
        first_capture_receipt=_early_window_receipt(
            rows=342,
            failure_rows=list(range(1, 7)),
        ),
        retry_capture_receipt=_early_window_receipt(
            rows=348,
            failure_rows=list(range(1, 7)),
        ),
    )

    assert payload["status"] == "pass-after-reference-self-calibration"
    assert payload["series"]["firstCross"]["rows"] == 342
    assert payload["series"]["retryCross"]["rows"] == 348
    assert payload["metrics"]["rowCountsMatchExpected"] is False
    assert payload["metrics"]["rowCountsCoverExpectedWindow"] is True


@pytest.mark.parametrize(
    ("first_values", "retry_values", "first_receipt", "retry_receipt", "metric"),
    [
        (
            [0.80] * 6 + [0.96] * 11,
            [0.80] * 6 + [0.96] * 12,
            _early_window_receipt(rows=17, failure_rows=list(range(1, 7))),
            _early_window_receipt(rows=18, failure_rows=list(range(1, 7))),
            ("rowCountsCoverExpectedWindow", None),
        ),
        (
            [0.80] * 6 + [0.96] * 12 + [0.80],
            [0.80] * 6 + [0.96] * 12,
            _early_window_receipt(rows=19, failure_rows=[*range(1, 7), 19]),
            _early_window_receipt(rows=18, failure_rows=list(range(1, 7))),
            ("rowCountsCoverExpectedWindow", None),
        ),
        (
            [0.80] * 6 + [0.96] * 336,
            [0.80] * 6 + [0.96] * 342,
            _early_window_receipt(rows=18, failure_rows=list(range(1, 7))),
            _early_window_receipt(rows=348, failure_rows=list(range(1, 7))),
            ("captureReceiptsValid", None),
        ),
    ],
)
def test_reference_self_calibration_rejects_cross_window_contract_breaks(
    first_values: list[float],
    retry_values: list[float],
    first_receipt: dict[str, object],
    retry_receipt: dict[str, object],
    metric: tuple[str, str | None],
) -> None:
    payload = calibrate_distributions(
        [0.80] * 6 + [0.96] * 12,
        retry_values,
        threshold=0.90,
        expected_rows=18,
        first_cross_values=first_values,
        first_capture_receipt=first_receipt,
        retry_capture_receipt=retry_receipt,
    )

    assert payload["status"] == "reference-self-calibration-failed"
    top, nested = metric
    metrics = payload["metrics"]
    if nested is None:
        assert metrics[top] is False
    else:
        assert metrics[top][nested] is False


def test_complementary_calibration_accepts_early_then_arc_only_real_shape() -> None:
    self_values = [0.80] * 5 + [0.96] * 13
    early_values = [0.81] * 5 + [0.97] * 13
    arc_values = [0.961] * 18

    payload = calibrate_complementary(
        self_values,
        arc_values,
        threshold=0.90,
        expected_rows=18,
        first_cross_values=early_values,
        first_capture_receipt=_early_window_receipt(
            rows=18,
            failure_rows=list(range(1, 6)),
            ref_duration_frames=6,
            impl_duration_frames=12,
            arc_max_delta_frames=18,
        ),
        retry_capture_receipt=_arc_only_receipt(rows=18, min_ssim=0.961),
        target_payloads=_target_payloads(),
        trigger_type="css-hover",
        provenance="css-hover",
    )

    assert payload["status"] == "pass-after-complementary-reference-self-calibration"
    assert payload["rule"] == "mixed-early-window-and-arc-only-capture-phase"
    assert payload["metrics"]["earlySide"] == "first"
    assert payload["metrics"]["arcOnlySide"] == "retry"
    assert payload["metrics"]["arcOnlyPixelsPassing"] is True


def test_complementary_calibration_accepts_repeated_transition_time_lists() -> None:
    ref_transition = _target_payload(duration="0.2", delay="0")["transition"]
    payload = calibrate_complementary(
        [0.80] * 5 + [0.96] * 13,
        [0.961] * 18,
        threshold=0.90,
        expected_rows=18,
        first_cross_values=[0.81] * 5 + [0.97] * 13,
        first_capture_receipt=_early_window_receipt(
            rows=18,
            failure_rows=list(range(1, 6)),
            ref_duration_frames=6,
            impl_duration_frames=12,
            arc_max_delta_frames=18,
        ),
        retry_capture_receipt=_arc_only_receipt(rows=18, min_ssim=0.961),
        target_payloads=_target_payloads(
            firstRef={"transition": ref_transition},
            retryRef={"transition": ref_transition},
        ),
        trigger_type="css-hover",
        provenance="css-hover",
    )

    assert payload["status"] == "pass-after-complementary-reference-self-calibration"
    assert payload["metrics"]["targetPayloadsValid"] is True


def test_complementary_calibration_accepts_repeated_transition_timing_lists() -> None:
    ref_transition = _target_payload(
        timing="cubic-bezier(0.33, 1, 0.68, 1)"
    )["transition"]
    payload = calibrate_complementary(
        [0.80] * 5 + [0.96] * 13,
        [0.961] * 18,
        threshold=0.90,
        expected_rows=18,
        first_cross_values=[0.81] * 5 + [0.97] * 13,
        first_capture_receipt=_early_window_receipt(
            rows=18,
            failure_rows=list(range(1, 6)),
            ref_duration_frames=6,
            impl_duration_frames=12,
            arc_max_delta_frames=18,
        ),
        retry_capture_receipt=_arc_only_receipt(rows=18, min_ssim=0.961),
        target_payloads=_target_payloads(
            firstRef={"transition": ref_transition},
            retryRef={"transition": ref_transition},
        ),
        trigger_type="css-hover",
        provenance="css-hover",
    )

    assert payload["status"] == "pass-after-complementary-reference-self-calibration"
    assert payload["metrics"]["targetPayloadsValid"] is True


@pytest.mark.parametrize(
    "timing",
    [
        "steps(4, jump-start)",
        "linear(0, 0.2 20%, 1)",
    ],
)
def test_transition_contract_key_repeats_parenthesized_timing_lists(
    timing: str,
) -> None:
    transition = _target_payload(timing=timing)["transition"]

    key = _transition_contract_key(transition)

    assert key is not None
    assert key[3] == (timing, timing)


def test_transition_contract_key_rejects_zero_effective_duration_for_single_property() -> None:
    transition = _target_payload(prop="color", duration="0,0.2")["transition"]

    assert _transition_contract_key(transition) is None


def test_transition_contract_key_uses_effective_duration_per_transition_property() -> None:
    transition = _target_payload(prop="color,border-color", duration="0,0.2")["transition"]

    key = _transition_contract_key(transition)

    assert key is not None
    assert key[1] == (0.0, 0.2)


@pytest.mark.parametrize(
    "timing",
    [
        "cubic-bezier(0.33, 1",
        "steps(4, jump-start",
        "ease,,linear",
        ",ease",
    ],
)
def test_transition_contract_key_rejects_malformed_timing_lists(timing: str) -> None:
    transition = _target_payload(timing=timing)["transition"]

    assert _transition_contract_key(transition) is None


def test_complementary_calibration_accepts_long_stable_cross_tails() -> None:
    payload = calibrate_complementary(
        [0.80] * 5 + [0.96] * 13,
        [0.961] * 348,
        threshold=0.90,
        expected_rows=18,
        first_cross_values=[0.81] * 5 + [0.97] * 337,
        first_capture_receipt=_early_window_receipt(
            rows=342,
            failure_rows=list(range(1, 6)),
            ref_duration_frames=6,
            impl_duration_frames=12,
            arc_max_delta_frames=18,
        ),
        retry_capture_receipt=_arc_only_receipt(rows=348, min_ssim=0.961),
        target_payloads=_target_payloads(),
        trigger_type="css-hover",
        provenance="css-hover",
    )

    assert payload["status"] == "pass-after-complementary-reference-self-calibration"
    assert payload["series"]["firstCross"]["rows"] == 342
    assert payload["series"]["retryCross"]["rows"] == 348
    assert payload["metrics"]["rowCountsMatchExpected"] is False
    assert payload["metrics"]["rowCountsCoverExpectedWindow"] is True


@pytest.mark.parametrize(
    (
        "first_values",
        "retry_values",
        "first_receipt",
        "retry_receipt",
        "targets",
        "metric",
    ),
    [
        (
            [0.81] * 5 + [0.97] * 13,
            [0.961] * 17 + [0.80],
            _early_window_receipt(rows=18, failure_rows=list(range(1, 6))),
            _arc_only_receipt(rows=18, min_ssim=0.80),
            _target_payloads(),
            "arcOnlyPixelsPassing",
        ),
        (
            [0.81] * 5 + [0.97] * 12,
            [0.961] * 18,
            _early_window_receipt(rows=17, failure_rows=list(range(1, 6))),
            _arc_only_receipt(rows=18, min_ssim=0.961),
            _target_payloads(),
            "rowCountsCoverExpectedWindow",
        ),
        (
            [0.81] * 5 + [0.97] * 13 + [0.80],
            [0.961] * 18,
            _early_window_receipt(rows=19, failure_rows=[*range(1, 6), 19]),
            _arc_only_receipt(rows=18, min_ssim=0.961),
            _target_payloads(),
            "rowCountsCoverExpectedWindow",
        ),
        (
            [0.81] * 5 + [0.97] * 337,
            [0.961] * 348,
            _early_window_receipt(rows=18, failure_rows=list(range(1, 6))),
            _arc_only_receipt(rows=348, min_ssim=0.961),
            _target_payloads(),
            "captureReceiptsValid",
        ),
        (
            [0.81] * 5 + [0.97] * 13,
            [0.961] * 18,
            _early_window_receipt(rows=18, failure_rows=list(range(1, 6))),
            {**_arc_only_receipt(rows=18, min_ssim=0.961), "minSsim": 0.90},
            _target_payloads(),
            "captureReceiptsValid",
        ),
        (
            [0.81] * 5 + [0.97] * 13,
            [0.961] * 18,
                _early_window_receipt(
                    rows=18,
                    failure_rows=list(range(1, 6)),
                    arc_within_tolerance=False,
                    ref_duration_frames=6,
                    impl_duration_frames=36,
                    arc_max_delta_frames=18,
            ),
            _arc_only_receipt(rows=18, min_ssim=0.961),
            _target_payloads(),
            "arcTimingComplementary",
        ),
        (
            [0.81] * 5 + [0.97] * 13,
            [0.961] * 18,
            _early_window_receipt(rows=18, failure_rows=list(range(1, 6))),
            _early_window_receipt(rows=18, failure_rows=list(range(1, 6))),
            _target_payloads(),
            "exactlyMixedReceipts",
        ),
        (
            [0.81] * 5 + [0.97] * 13,
            [0.961] * 18,
            _early_window_receipt(rows=18, failure_rows=list(range(1, 6))),
            _arc_only_receipt(
                rows=18,
                min_ssim=0.961,
                ref_duration_frames=100,
                impl_duration_frames=1,
                arc_max_delta_frames=18,
            ),
            _target_payloads(),
            "arcDriftWithinBounds",
        ),
        (
            [0.81] * 5 + [0.97] * 13,
            [0.961] * 18,
            _early_window_receipt(rows=18, failure_rows=list(range(1, 6))),
            _arc_only_receipt(rows=18, min_ssim=0.961),
            _target_payloads(firstImpl={"transition": _target_payload(duration="0.4,0.4")["transition"]}),
            "targetPayloadsValid",
        ),
        (
            [0.81] * 5 + [0.97] * 13,
            [0.961] * 18,
            _early_window_receipt(rows=18, failure_rows=list(range(1, 6))),
            _arc_only_receipt(rows=18, min_ssim=0.961),
            _target_payloads(firstImpl={"transition": _target_payload(delay="0.1,0")["transition"]}),
            "targetPayloadsValid",
        ),
        (
            [0.81] * 5 + [0.97] * 13,
            [0.961] * 18,
            _early_window_receipt(rows=18, failure_rows=list(range(1, 6))),
            _arc_only_receipt(rows=18, min_ssim=0.961),
            _target_payloads(
                firstImpl={
                    "transition": _target_payload(
                        timing="ease-in,ease-out"
                    )["transition"]
                }
            ),
            "targetPayloadsValid",
        ),
        (
            [0.81] * 5 + [0.97] * 13,
            [0.961] * 18,
            _early_window_receipt(rows=18, failure_rows=list(range(1, 6))),
            _arc_only_receipt(rows=18, min_ssim=0.961),
            _target_payloads(firstRef={"transition": _target_payload(duration="0,0")["transition"]}),
            "targetPayloadsValid",
        ),
        (
            [0.81] * 5 + [0.97] * 13,
            [0.961] * 18,
            _early_window_receipt(rows=18, failure_rows=list(range(1, 6))),
            _arc_only_receipt(rows=18, min_ssim=0.961),
            _target_payloads(firstRef={"transition": _target_payload(prop="none")["transition"]}),
            "targetPayloadsValid",
        ),
        (
            [0.81] * 5 + [0.97] * 13,
            [0.961] * 18,
            _early_window_receipt(rows=18, failure_rows=list(range(1, 6))),
            _arc_only_receipt(rows=18, min_ssim=0.961),
            _target_payloads(firstRef={"transition": _target_payload(prop="none, none")["transition"]}),
            "targetPayloadsValid",
        ),
        (
            [0.81] * 5 + [0.97] * 13,
            [0.961] * 18,
            _early_window_receipt(rows=18, failure_rows=list(range(1, 6))),
            _arc_only_receipt(rows=18, min_ssim=0.961),
            _target_payloads(firstRef={"transition": _target_payload(prop="")["transition"]}),
            "targetPayloadsValid",
        ),
        (
            [0.81] * 5 + [0.97] * 13,
            [0.961] * 18,
            _early_window_receipt(rows=18, failure_rows=list(range(1, 6))),
            _arc_only_receipt(rows=18, min_ssim=0.961),
            _target_payloads(retryImpl={"matchIndex": 1}),
            "targetPayloadsValid",
        ),
    ],
)
def test_complementary_calibration_blocks_forged_or_persistent_shapes(
    first_values: list[float],
    retry_values: list[float],
    first_receipt: dict[str, object],
    retry_receipt: dict[str, object],
    targets: dict[str, dict[str, object]],
    metric: str,
) -> None:
    payload = calibrate_complementary(
        [0.80] * 5 + [0.96] * 13,
        retry_values,
        threshold=0.90,
        expected_rows=18,
        first_cross_values=first_values,
        first_capture_receipt=first_receipt,
        retry_capture_receipt=retry_receipt,
        target_payloads=targets,
        trigger_type="css-hover",
        provenance="css-hover",
    )

    assert payload["status"] == "complementary-reference-self-calibration-failed"
    assert payload["metrics"][metric] is False


@pytest.mark.parametrize(
    ("trigger_type", "expected_status"),
    [
        ("css-hover", "pass-after-complementary-reference-self-calibration"),
        ("synth-hover-css", "pass-after-complementary-reference-self-calibration"),
        ("hover", "complementary-reference-self-calibration-failed"),
        ("scale-on-hover-target", "complementary-reference-self-calibration-failed"),
        ("synth-hover-candidate", "complementary-reference-self-calibration-failed"),
        ("synth-hover-manifest", "complementary-reference-self-calibration-failed"),
    ],
)
def test_complementary_calibration_requires_css_backed_provenance(
    trigger_type: str,
    expected_status: str,
) -> None:
    payload = calibrate_complementary(
        [0.80] * 5 + [0.96] * 13,
        [0.961] * 18,
        threshold=0.90,
        expected_rows=18,
        first_cross_values=[0.81] * 5 + [0.97] * 13,
        first_capture_receipt=_early_window_receipt(
            rows=18,
            failure_rows=list(range(1, 6)),
            ref_duration_frames=6,
            impl_duration_frames=12,
            arc_max_delta_frames=18,
        ),
        retry_capture_receipt=_arc_only_receipt(rows=18, min_ssim=0.961),
        target_payloads=_target_payloads(),
        trigger_type=trigger_type,
        provenance=trigger_type,
    )

    assert payload["status"] == expected_status
    assert payload["metrics"]["provenanceValid"] is (
        expected_status == "pass-after-complementary-reference-self-calibration"
    )


@pytest.mark.parametrize(
    ("self_values", "cross_values", "first_receipt", "retry_receipt", "metric"),
    [
        (
            [0.95, 0.80, 0.95, 0.96],
            [0.95, 0.80, 0.95, 0.96],
            _early_window_receipt(rows=4, failure_rows=[2]),
            _early_window_receipt(rows=4, failure_rows=[2]),
            ("referenceSelf", "contiguousEarlyBlock"),
        ),
        (
            [0.80, 0.95, 0.96, 0.97],
            [0.80, 0.95, 0.80, 0.97],
            _early_window_receipt(rows=4, failure_rows=[1]),
            _early_window_receipt(rows=4, failure_rows=[1, 3]),
            ("retryCross", "postReferenceSelfBlockPassing"),
        ),
        (
            [0.80, 0.95, 0.96, 0.97],
            [0.80, 0.95, 0.96, 0.97],
            _early_window_receipt(
                rows=4,
                failure_rows=[1],
                arc_within_tolerance=False,
                arc_delta_frames=24,
                arc_max_delta_frames=18,
            ),
            _early_window_receipt(
                rows=4,
                failure_rows=[1],
                arc_within_tolerance=False,
                arc_delta_frames=24,
                arc_max_delta_frames=18,
            ),
            ("captureReceiptsValid", None),
        ),
        (
            [0.80, 0.95, 0.96, 0.97],
            [0.80, 0.95, 0.96, 0.97],
            _early_window_receipt(
                rows=4,
                failure_rows=[1],
                ref_duration_frames=0,
                impl_duration_frames=1,
            ),
            _early_window_receipt(rows=4, failure_rows=[1]),
            ("captureReceiptsValid", None),
        ),
        (
            [0.80, 0.95, 0.96, 0.97],
            [0.80, 0.95, 0.96, 0.97],
            _early_window_receipt(rows=4, failure_rows=[1]),
            _early_window_receipt(rows=4, failure_rows=[1, 2]),
            ("captureReceiptsValid", None),
        ),
        (
            [0.80, 0.95, 0.96, 0.97],
            [0.80, 0.95, 0.96, 0.97],
            _early_window_receipt(
                rows=4,
                failure_rows=[1],
                arc_delta_frames=99,
                arc_max_delta_frames=100,
                ref_duration_frames=20,
                impl_duration_frames=21,
            ),
            _early_window_receipt(rows=4, failure_rows=[1]),
            ("captureReceiptsValid", None),
        ),
        (
            [0.80, 0.95, 0.96, 0.97],
            [0.80, 0.95, 0.96, 0.97],
            _early_window_receipt(rows=4, failure_rows=[1, 2]),
            _early_window_receipt(rows=4, failure_rows=[1]),
            ("captureReceiptsValid", None),
        ),
        (
            [0.80, 0.80] + [0.96] * 16,
            [0.80, 0.80] + [0.96] * 16,
            _early_window_receipt(rows=18, failure_rows=list(range(1, 18))),
            _early_window_receipt(rows=18, failure_rows=[1, 2]),
            ("firstCross", "postReferenceSelfBlockPassing"),
        ),
    ],
)
def test_reference_self_calibration_blocks_anti_bypass_shapes(
    self_values: list[float],
    cross_values: list[float],
    first_receipt: dict[str, object],
    retry_receipt: dict[str, object],
    metric: tuple[str, str | None],
) -> None:
    payload = calibrate_distributions(
        self_values,
        cross_values,
        threshold=0.90,
        expected_rows=len(self_values),
        first_cross_values=(
            [0.80] * 17 + [0.96]
            if len(self_values) == 18
            else self_values
        ),
        first_capture_receipt=first_receipt,
        retry_capture_receipt=retry_receipt,
    )

    assert payload["status"] == "reference-self-calibration-failed"
    top, nested = metric
    metrics = payload["metrics"]
    if nested is None:
        assert metrics[top] is False
    else:
        assert metrics[top][nested] is False


def test_selector_video_actions_wire_target_roi() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "verify" / "video-transition-compare.sh"
    ).read_text(encoding="utf-8")

    assert 'hover:*) TARGET_ROI_SELECTOR="${ACTION#hover:}"' in script
    assert 'hover-and-out:*) TARGET_ROI_SELECTOR="${ACTION#hover-and-out:}"' in script
    assert "prepare_target_roi_filters" in script
    assert 'filter="$filter,$TARGET_ROI_REF_FILTER"' in script
    assert 'filter="$filter,$TARGET_ROI_IMPL_FILTER"' in script


def test_selector_roi_resolves_and_drives_the_same_visible_match() -> None:
    script = VIDEO_COMPARE.read_text(encoding="utf-8")
    capture = script.split("capture_target_roi() {", 1)[1].split(
        "\n}\n\ntarget_center_from_rect", 1
    )[0]
    restore = script.split("restore_visible_target_rect() {", 1)[1].split(
        "\n}\n\nhover_visible_target", 1
    )[0]
    hover = script.split("hover_visible_target() {", 1)[1].split(
        "\n}\n\nprepare_target_roi_filters", 1
    )[0]

    assert "document.querySelectorAll" in capture
    assert "matches.find" in capture
    assert "inViewport" in capture
    assert "transitionContract(el)" in capture
    assert "transitionTimingFunction.replace" in script
    assert "scrollIntoView" in capture
    assert 'scrollintoview "$selector"' not in capture
    assert "matches[matchIndex]" in restore
    assert "transitionContract(el)" in restore
    assert "scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' })" in restore
    assert 'target_center_from_rect "$target_rect" "$VIEW_W" "$VIEW_H" >/dev/null' in restore
    assert 'target_center_from_rect "$target_rect" "$VIEW_W" "$VIEW_H"' in hover
    assert "hover_timing_probe_js" in script
    arm = hover.index("window.__uiCloneHoverTimingProofs[key] = proof")
    sampler = hover.index("while (proof.moveAt === null")
    mark_move = hover.index("proof.moveAt = performance.now()")
    mouse_move = hover.index('agent-browser --session "$session" mouse move "$target_x" "$target_y"')
    assert arm < sampler < mark_move < mouse_move
    assert "performance.now() - pointerWaitStart < 1000" in hover
    assert "const timeoutAt = startAt + 250" in hover
    assert "const deadline = performance.now() + 2000" in hover
    assert "while (!proof.done" in hover
    assert "hoverProof: proof || null" in hover
    assert 'mouse move "$target_x" "$target_y"' in hover
    assert "const matchIndex =" in hover
    assert "hovered: el.matches(':hover')" in hover
    assert "pointerReachable: Boolean" in hover
    assert 'python3 "$HOVER_ACTION_RECEIPT_HELPER" "$receipt"' in hover
    hover_branch = script.index('elif [[ "$action" == hover:* ]]')
    restore_call = script.index(
        'restore_visible_target_rect "$session" "$selector" "$target_rect"',
        hover_branch,
    )
    sleep_call = script.index('sleep "$PRE_ACTION_WAIT"', hover_branch)
    hover_call = script.index("hover_visible_target \\", hover_branch)
    assert restore_call < sleep_call < hover_call


def test_hover_action_receipt_requires_real_pointer_verification() -> None:
    verified = {
        "found": True,
        "selector": ".header .nav__link",
        "matchIndex": 0,
        "hovered": True,
        "pointerReachable": True,
    }

    assert validation_error(verified) is None
    assert validation_error({**verified, "hovered": False}) is not None
    assert validation_error({**verified, "pointerReachable": False}) is not None
    assert validation_error({**verified, "found": False}) is not None


def _target_center(tmp_path: Path, rect: dict[str, float]) -> subprocess.CompletedProcess[str]:
    script = VIDEO_COMPARE.read_text(encoding="utf-8")
    start = script.index("target_center_from_rect() {")
    end = script.index("\n}\n\nhover_visible_target", start) + 3
    harness = tmp_path / "target-center.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + script[start:end]
        + '\ntarget_center_from_rect "$1" 320 240\n',
        encoding="utf-8",
    )
    raw = tmp_path / "target.json"
    raw.write_text(json.dumps({"found": True, "rect": rect}), encoding="utf-8")
    return subprocess.run(
        ["bash", str(harness), str(raw)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_fully_offscreen_target_cannot_collapse_to_y_zero_crop(tmp_path: Path) -> None:
    proc = _target_center(
        tmp_path,
        {"x": 40, "y": -396, "width": 120, "height": 40},
    )

    assert proc.returncode != 0
    assert proc.stdout == ""


def test_partially_visible_target_uses_center_of_visible_intersection(
    tmp_path: Path,
) -> None:
    proc = _target_center(
        tmp_path,
        {"x": 40, "y": -10, "width": 40, "height": 20},
    )

    assert proc.returncode == 0
    assert proc.stdout.strip() == "60\t5"


def test_sparse_early_failures_are_retryable_by_elapsed_time() -> None:
    receipt = build_retry_receipt(
        [0.80, 0.95, 0.82, 0.96, 0.97],
        threshold=0.90,
        fps=10,
        early_window_seconds=0.3,
        selector=".target",
    )

    assert receipt is not None
    assert receipt["reason"] == "early-window-capture-phase"
    assert receipt["failureRows"] == [1, 3]
    assert receipt["earlyWindowRows"] == 3
    assert receipt["firstStablePassingRow"] == 4


def test_observed_400ms_arc_can_extend_capture_phase_window() -> None:
    receipt = build_retry_receipt(
        [0.80] * 24 + [0.95] * 6,
        threshold=0.90,
        fps=60,
        early_window_seconds=0.4,
        selector=".target",
    )

    assert receipt is not None
    assert receipt["lastFailureRow"] == 24
    assert receipt["earlyWindowRows"] == 24
    assert receipt["earlyWindowSeconds"] == pytest.approx(0.4)


def test_retry_receipt_preserves_independent_arc_measurement() -> None:
    receipt = build_retry_receipt(
        [0.80, 0.95, 0.96],
        threshold=0.90,
        fps=10,
        early_window_seconds=0.3,
        selector=".target",
        ref_first_change=10,
        ref_last_change=20,
        impl_first_change=10,
        impl_last_change=28,
        arc_max_delta=3,
    )

    assert receipt is not None
    assert receipt["reason"] == "early-window-capture-phase"
    assert receipt["arc"] == {
        "ref": {
            "firstChange": 10,
            "lastChange": 20,
            "durationFrames": 10,
        },
        "impl": {
            "firstChange": 10,
            "lastChange": 28,
            "durationFrames": 18,
        },
        "deltaFrames": 8,
        "maxDeltaFrames": 3,
        "withinTolerance": False,
    }


def test_retry_receipt_rejects_unbounded_early_window() -> None:
    receipt = build_retry_receipt(
        [0.80, 0.95, 0.95, 0.95],
        threshold=0.90,
        fps=10,
        early_window_seconds=9,
        selector=".target",
    )

    assert receipt is None


def test_retry_receipt_rejects_one_side_no_motion_arc() -> None:
    receipt = build_retry_receipt(
        [0.80, 0.95, 0.96],
        threshold=0.90,
        fps=10,
        early_window_seconds=0.3,
        selector=".target",
        ref_first_change=1,
        ref_last_change=1,
        impl_first_change=1,
        impl_last_change=2,
        arc_max_delta=18,
    )

    assert receipt is not None
    arc = receipt["arc"]
    assert isinstance(arc, dict)
    assert arc["deltaFrames"] == 1
    assert arc["withinTolerance"] is False


def test_60fps_duplicates_of_one_10fps_source_sample_stay_in_time_window() -> None:
    receipt = build_retry_receipt(
        [0.80] * 6 + [0.96] * 12,
        threshold=0.90,
        fps=60,
        early_window_seconds=0.1,
        selector=".target",
    )

    assert receipt is not None
    assert receipt["failureRows"] == list(range(1, 7))
    assert receipt["earlyWindowRows"] == 6


@pytest.mark.parametrize(
    "values",
    [
        [0.95, 0.95, 0.95, 0.80, 0.95],
        [0.80, 0.95, 0.95, 0.95, 0.80],
    ],
)
def test_late_or_unsettled_failure_remains_hard_divergence(
    values: list[float],
) -> None:
    assert build_retry_receipt(
        values,
        threshold=0.90,
        fps=10,
        early_window_seconds=0.3,
        selector=".target",
    ) is None


def test_delta_builder_uses_requested_blur_without_pipefail_sigpipe(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    delta = tmp_path / "delta"
    fake_bin = tmp_path / "bin"
    source.mkdir()
    fake_bin.mkdir()
    for index in range(1, 2001):
        (source / f"f-{index:06d}.png").touch()

    magick_log = tmp_path / "magick.log"
    fake_magick = fake_bin / "magick"
    fake_magick.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$MAGICK_LOG"\nout="${!#}"\ncp "$1" "$out"\n',
        encoding="utf-8",
    )
    fake_magick.chmod(0o755)

    script = VIDEO_COMPARE.read_text(encoding="utf-8")
    start = script.index("build_target_roi_delta_frames() {")
    end = script.index("\n}\n\n_extract_frames", start) + 3
    harness = tmp_path / "run-delta-builder.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + script[start:end]
        + f'\nbuild_target_roi_delta_frames "{source}" "{delta}" "1.7" "1"\n',
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(harness)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "MAGICK_LOG": str(magick_log),
        },
        capture_output=True,
        text=True,
        # 2000 fake-magick invocations: this bound exists to catch a hang, not to
        # assert speed (the assertions below are about blur ARGS). Under
        # `pytest -n` the same work competes with other workers and a 60s
        # ceiling turns into a load-sensitive failure — it was the only one of
        # ~700 subprocess timeouts in the suite to breach at 10 workers.
        timeout=300,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = magick_log.read_text(encoding="utf-8")
    assert "-blur 0x1.7" in calls
    assert "-blur 0x0.3" not in calls


def test_delta_builder_rejects_missing_baseline_index(tmp_path: Path) -> None:
    source = tmp_path / "source"
    delta = tmp_path / "delta"
    source.mkdir()
    (source / "f-000001.png").touch()

    script = VIDEO_COMPARE.read_text(encoding="utf-8")
    start = script.index("build_target_roi_delta_frames() {")
    end = script.index("\n}\n\n_extract_frames", start) + 3
    harness = tmp_path / "run-delta-builder.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + script[start:end]
        + f'\nbuild_target_roi_delta_frames "{source}" "{delta}" "0"\n',
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(harness)],
        env=os.environ,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode != 0
    assert not list(delta.glob("f-*.png"))


def test_delta_builder_uses_requested_pre_action_baseline(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    delta = tmp_path / "delta"
    fake_bin = tmp_path / "bin"
    source.mkdir()
    fake_bin.mkdir()
    for index in range(1, 5):
        (source / f"f-{index:06d}.png").touch()

    magick_log = tmp_path / "magick.log"
    fake_magick = fake_bin / "magick"
    fake_magick.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$MAGICK_LOG"\nout="${!#}"\ncp "$1" "$out"\n',
        encoding="utf-8",
    )
    fake_magick.chmod(0o755)

    script = VIDEO_COMPARE.read_text(encoding="utf-8")
    start = script.index("build_target_roi_delta_frames() {")
    end = script.index("\n}\n\n_extract_frames", start) + 3
    harness = tmp_path / "run-delta-builder.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + script[start:end]
        + f'\nbuild_target_roi_delta_frames "{source}" "{delta}" "0" "3"\n',
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(harness)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "MAGICK_LOG": str(magick_log),
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    baseline = str(source / "f-000003.png")
    calls = magick_log.read_text(encoding="utf-8").splitlines()
    assert calls
    assert all(call.split()[0] == baseline for call in calls)


@needs_video_tools
def test_small_target_roi_uses_area_scaled_change_threshold(
    tmp_path: Path,
) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    for index in (1, 2):
        subprocess.run(
            [
                "magick",
                "-size",
                "40x30",
                "xc:white",
                str(frames / f"f-{index:06d}.png"),
            ],
            check=True,
            capture_output=True,
        )
    subprocess.run(
        [
            "magick",
            "-size",
            "40x30",
            "xc:white",
            "-fill",
            "black",
            "-draw",
            "rectangle 0,0 9,9",
            str(frames / "f-000003.png"),
        ],
        check=True,
        capture_output=True,
    )

    proc = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{FRAME_ALIGN}"; analyze_timing "{frames}" ROI',
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (frames / ".first-change").read_text().strip() == "3"
    assert "changed-pixel threshold: 60" in proc.stdout


def _make_background_video(path: Path, background: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    filters = (
        "drawbox=x=80:y=60:w=160:h=120:color=black:t=fill,"
        "drawbox=x=80:y=60:w=160:h=120:color=white:t=fill:"
        "enable='gte(t,0.5)'"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={background}:s=320x240:r=30:d=1",
            "-vf",
            filters,
            "-c:v",
            "libvpx",
            "-b:v",
            "300k",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _make_static_video(path: Path, background: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={background}:s=320x240:r=30:d=1",
            "-c:v",
            "libvpx",
            "-b:v",
            "300k",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _make_shifted_target_video(path: Path, x: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:r=30:d=1",
            "-vf",
            (f"drawbox=x={x}:y=70:w=140:h=100:color=white:t=fill:enable='gte(t,0.5)'"),
            "-c:v",
            "libvpx",
            "-b:v",
            "300k",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _make_arc_jitter_target_video(path: Path, end_time: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:r=30:d=1",
            "-vf",
            (
                "drawbox=x=100:y=90:w=40:h=25:color=white:t=fill:"
                f"enable='between(t,0.3,{end_time})'"
            ),
            "-c:v",
            "libvpx",
            "-b:v",
            "300k",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _make_selector_phase_video(
    path: Path,
    *,
    gray_start: float | None = None,
    gray_end: float | None = None,
    gray_color: str = "gray",
    rate: int = 30,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    filters = [
        "drawbox=x=100:y=90:w=80:h=50:color=white:t=fill:enable='gte(t,0.3)'"
    ]
    if gray_start is not None and gray_end is not None:
        filters.append(
            f"drawbox=x=100:y=90:w=80:h=50:color={gray_color}:t=fill:"
            f"enable='between(t,{gray_start},{gray_end})'"
        )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=320x240:r={rate}:d=1",
            "-vf",
            ",".join(filters),
            "-c:v",
            "libvpx",
            "-b:v",
            "300k",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _make_static_foreground_delta_video(
    path: Path,
    *,
    foreground_after: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    filters = [
        "drawbox=x=80:y=60:w=160:h=120:color=0x404040:t=fill",
        (
            "drawbox=x=80:y=60:w=160:h=120:color=0x909090:t=fill:"
            "enable='gte(t,0.2)'"
        ),
        "drawbox=x=120:y=90:w=80:h=60:color=black:t=fill",
    ]
    if foreground_after is not None:
        filters.append(
            "drawbox=x=120:y=90:w=80:h=60:"
            f"color={foreground_after}:t=fill:enable='gte(t,0.2)'"
        )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:r=30:d=1",
            "-vf",
            ",".join(filters),
            "-c:v",
            "libvpx",
            "-b:v",
            "800k",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _seed_target_rect(out: Path, *, impl_width: int = 160) -> None:
    ref_payload = {
        "found": True,
        "rect": {"x": 80, "y": 60, "width": 160, "height": 120},
    }
    impl_payload = {
        "found": True,
        "rect": {"x": 80, "y": 60, "width": impl_width, "height": 120},
    }
    (out / "ref-video" / "target-rect.raw.json").write_text(
        json.dumps(ref_payload),
        encoding="utf-8",
    )
    (out / "impl-video" / "target-rect.raw.json").write_text(
        json.dumps(impl_payload),
        encoding="utf-8",
    )


@needs_video_tools
@pytest.mark.parametrize("action_onset", [None, "0", "not-a-number"])
def test_skip_record_selector_requires_explicit_positive_action_onset(
    tmp_path: Path,
    action_onset: str | None,
) -> None:
    out = tmp_path / "missing-action-onset"
    _make_background_video(out / "ref-video" / "raw.webm", "red")
    _make_background_video(out / "impl-video" / "raw.webm", "blue")
    _seed_target_rect(out)
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "VIDEO_COMPARE_ACTION_ONSET_SECONDS"
    }
    env.update(
        {
            "UI_CLONE_VMC_SKIP_RECORD": "1",
            "RECORD_DURATION": "1",
            "FPS": "30",
            "VIEW_W": "320",
            "VIEW_H": "240",
            "VIDEO_COMPARE_TARGET_PADDING": "0",
        }
    )
    if action_onset is not None:
        env["VIDEO_COMPARE_ACTION_ONSET_SECONDS"] = action_onset

    proc = subprocess.run(
        [
            "bash",
            str(VIDEO_COMPARE),
            "selector-onset-required-test",
            "http://ref.invalid",
            "http://impl.invalid",
            str(out),
            "hover:.target",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "selector action onset must be an explicit positive number" in proc.stdout
    assert "normalization" not in json.loads(
        (out / "target-roi.json").read_text(encoding="utf-8")
    )


@needs_video_tools
def test_hover_roi_ignores_static_page_background_but_compares_target_arc(
    tmp_path: Path,
) -> None:
    out = tmp_path / "roi"
    _make_background_video(out / "ref-video" / "raw.webm", "red")
    _make_background_video(out / "impl-video" / "raw.webm", "blue")
    _seed_target_rect(out)
    env = {
        **os.environ,
        "UI_CLONE_VMC_SKIP_RECORD": "1",
        "VIDEO_COMPARE_ACTION_ONSET_SECONDS": "0.2",
        "RECORD_DURATION": "1",
        "FPS": "30",
        "VIEW_W": "320",
        "VIEW_H": "240",
        "VIDEO_COMPARE_TARGET_PADDING": "0",
    }

    proc = subprocess.run(
        [
            "bash",
            str(VIDEO_COMPARE),
            "roi-test",
            "http://ref.invalid",
            "http://impl.invalid",
            str(out),
            "hover:.target",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ALL PASS" in proc.stdout
    plan = json.loads((out / "target-roi.json").read_text(encoding="utf-8"))
    assert plan["comparison"] == "target-local-delta-from-pre-action-frame"
    assert plan["ref"]["crop"] == plan["impl"]["crop"]
    assert plan["normalization"]["baselineFrame"] == 6
    assert plan["normalization"]["baselineFrameName"] == "f-000006.png"
    assert plan["normalization"]["actionOnsetSeconds"] == pytest.approx(0.2)
    assert plan["normalization"]["extractedFps"] == pytest.approx(30)
    assert len(plan["normalization"]["refVideoMd5"]) == 32
    assert len(plan["normalization"]["implVideoMd5"]) == 32


@needs_video_tools
def test_hover_roi_rejects_vacuous_no_change_reference(tmp_path: Path) -> None:
    out = tmp_path / "roi-static"
    _make_static_video(out / "ref-video" / "raw.webm", "red")
    _make_static_video(out / "impl-video" / "raw.webm", "blue")
    _seed_target_rect(out)
    stale_receipt = out / "target-aa-filter.json"
    stale_receipt.write_text('{"status":"stale-pass"}\n', encoding="utf-8")
    stale_capture_retry = out / "capture-retry.json"
    stale_capture_retry.write_text(
        '{"status":"retryable-unmeasurable"}\n',
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "UI_CLONE_VMC_SKIP_RECORD": "1",
        "VIDEO_COMPARE_ACTION_ONSET_SECONDS": "0.2",
        "RECORD_DURATION": "1",
        "FPS": "30",
        "VIEW_W": "320",
        "VIEW_H": "240",
        "VIDEO_COMPARE_TARGET_PADDING": "0",
    }

    proc = subprocess.run(
        [
            "bash",
            str(VIDEO_COMPARE),
            "roi-static-test",
            "http://ref.invalid",
            "http://impl.invalid",
            str(out),
            "hover:.target",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "reference selector interaction produced no visible" in proc.stdout
    assert not stale_receipt.exists()
    assert not stale_capture_retry.exists()


@needs_video_tools
def test_arc_only_target_jitter_writes_retry_receipt_and_exits_two(
    tmp_path: Path,
) -> None:
    out = tmp_path / "arc-only-capture-jitter"
    _make_arc_jitter_target_video(out / "ref-video" / "raw.webm", 0.5)
    _make_arc_jitter_target_video(out / "impl-video" / "raw.webm", 0.9)
    _seed_target_rect(out)
    threshold = 0.50
    env = {
        **os.environ,
        "UI_CLONE_VMC_SKIP_RECORD": "1",
        "VIDEO_COMPARE_ACTION_ONSET_SECONDS": "0.2",
        "RECORD_DURATION": "1",
        "FPS": "30",
        "VIEW_W": "320",
        "VIEW_H": "240",
        "VIDEO_COMPARE_TARGET_PADDING": "0",
        "VIDEO_COMPARE_ARC_DELTA": "0",
        "SSIM_THRESHOLD": str(threshold),
    }

    proc = subprocess.run(
        [
            "bash",
            str(VIDEO_COMPARE),
            "arc-only-capture-jitter-test",
            "http://ref.invalid",
            "http://impl.invalid",
            str(out),
            "hover:.target",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "arc-only capture jitter" in proc.stdout
    receipt = json.loads((out / "capture-retry.json").read_text(encoding="utf-8"))
    raw_values = [
        float(value)
        for value in (out / "diff-frames" / "target-raw-ssim.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert receipt["schemaVersion"] == 1
    assert receipt["status"] == "retryable-unmeasurable"
    assert receipt["reason"] == "arc-only-capture-jitter"
    assert receipt["selector"] == ".target"
    assert receipt["threshold"] == pytest.approx(threshold)
    assert receipt["rows"] == len(raw_values) > 0
    assert receipt["failures"] == 0
    assert receipt["failureRows"] == []
    assert receipt["firstStablePassingRow"] == 1
    assert receipt["lastFailureRow"] == 0
    assert receipt["minSsim"] == pytest.approx(min(raw_values))
    assert receipt["minSsim"] >= threshold
    assert receipt["ref"]["firstChange"] <= receipt["ref"]["lastChange"]
    assert receipt["impl"]["firstChange"] <= receipt["impl"]["lastChange"]
    assert receipt["arc"]["ref"]["durationFrames"] > 0
    assert receipt["arc"]["impl"]["durationFrames"] > 0
    assert receipt["arc"]["deltaFrames"] == abs(
        receipt["arc"]["ref"]["durationFrames"]
        - receipt["arc"]["impl"]["durationFrames"]
    )
    assert receipt["arc"]["withinTolerance"] == (
        receipt["arc"]["deltaFrames"] <= receipt["arc"]["maxDeltaFrames"]
    )
    result = (out / "result.txt").read_text(encoding="utf-8")
    assert "retryable-unmeasurable" in result


@needs_video_tools
def test_early_window_selector_phase_writes_retry_receipt_and_exits_two(
    tmp_path: Path,
) -> None:
    out = tmp_path / "early-window-capture-phase"
    _make_selector_phase_video(
        out / "ref-video" / "raw.webm",
        gray_start=0.3,
        gray_end=0.32,
        gray_color="0x404040",
    )
    _make_selector_phase_video(
        out / "impl-video" / "raw.webm",
        gray_start=0.3,
        gray_end=0.45,
    )
    _seed_target_rect(out)
    env = {
        **os.environ,
        "UI_CLONE_VMC_SKIP_RECORD": "1",
        "VIDEO_COMPARE_ACTION_ONSET_SECONDS": "0.2",
        "RECORD_DURATION": "1",
        "FPS": "30",
        "VIEW_W": "320",
        "VIEW_H": "240",
        "VIDEO_COMPARE_TARGET_PADDING": "0",
        "SSIM_THRESHOLD": "0.99",
        "VIDEO_COMPARE_CAPTURE_EARLY_WINDOW_SECONDS": "9",
    }

    proc = subprocess.run(
        [
            "bash",
            str(VIDEO_COMPARE),
            "early-window-capture-phase-test",
            "http://ref.invalid",
            "http://impl.invalid",
            str(out),
            "hover:.target",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert proc.returncode == 2, proc.stdout + proc.stderr
    receipt = json.loads((out / "capture-retry.json").read_text(encoding="utf-8"))
    assert receipt["reason"] == "early-window-capture-phase"
    assert receipt["failureRows"] == list(range(1, receipt["failures"] + 1))
    assert 0 < receipt["failures"] < receipt["rows"]
    assert receipt["firstStablePassingRow"] == receipt["failures"] + 1
    assert receipt["lastFailureRow"] <= receipt["earlyWindowRows"]
    assert receipt["earlyWindowSeconds"] == pytest.approx(0.3)
    assert receipt["extractedFps"] == pytest.approx(30)
    assert receipt["minSsim"] < receipt["threshold"]


@needs_video_tools
def test_early_window_pixel_noise_with_arc_mismatch_stays_unmeasurable(
    tmp_path: Path,
) -> None:
    out = tmp_path / "early-window-with-arc-mismatch"
    _make_selector_phase_video(
        out / "ref-video" / "raw.webm",
        gray_start=0.3,
        gray_end=0.32,
        gray_color="0x404040",
        rate=10,
    )
    _make_selector_phase_video(
        out / "impl-video" / "raw.webm",
        gray_start=0.3,
        gray_end=0.58,
        gray_color="gray",
        rate=10,
    )
    _seed_target_rect(out)

    proc = subprocess.run(
        [
            "bash",
            str(VIDEO_COMPARE),
            "early-window-with-arc-mismatch-test",
            "http://ref.invalid",
            "http://impl.invalid",
            str(out),
            "hover:.target",
        ],
        env={
            **os.environ,
            "UI_CLONE_VMC_SKIP_RECORD": "1",
            "VIDEO_COMPARE_ACTION_ONSET_SECONDS": "0.2",
            "RECORD_DURATION": "1",
            "FPS": "60",
            "VIEW_W": "320",
            "VIEW_H": "240",
            "VIDEO_COMPARE_TARGET_PADDING": "0",
            "VIDEO_COMPARE_ARC_DELTA": "0",
            "SSIM_THRESHOLD": "0.99",
        },
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "early-window-capture-phase plus unstable arc" in proc.stdout
    receipt = json.loads((out / "capture-retry.json").read_text(encoding="utf-8"))
    assert receipt["reason"] == "early-window-capture-phase"
    assert receipt["arc"]["withinTolerance"] is False
    assert receipt["arc"]["deltaFrames"] > receipt["arc"]["maxDeltaFrames"]


@needs_video_tools
def test_10fps_source_duplicates_use_60fps_elapsed_time_window(
    tmp_path: Path,
) -> None:
    out = tmp_path / "ten-to-sixty-capture-phase"
    _make_selector_phase_video(
        out / "ref-video" / "raw.webm",
        gray_start=0.3,
        gray_end=0.32,
        gray_color="0x404040",
        rate=10,
    )
    _make_selector_phase_video(
        out / "impl-video" / "raw.webm",
        gray_start=0.3,
        gray_end=0.58,
        gray_color="gray",
        rate=10,
    )
    _seed_target_rect(out)
    source_rate = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(out / "ref-video" / "raw.webm"),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert source_rate == "10/1"

    proc = subprocess.run(
        [
            "bash",
            str(VIDEO_COMPARE),
            "ten-to-sixty-capture-phase-test",
            "http://ref.invalid",
            "http://impl.invalid",
            str(out),
            "hover:.target",
        ],
        env={
            **os.environ,
            "UI_CLONE_VMC_SKIP_RECORD": "1",
            "VIDEO_COMPARE_ACTION_ONSET_SECONDS": "0.2",
            "RECORD_DURATION": "1",
            "FPS": "60",
            "VIEW_W": "320",
            "VIEW_H": "240",
            "VIDEO_COMPARE_TARGET_PADDING": "0",
            "SSIM_THRESHOLD": "0.99",
        },
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert proc.returncode == 2, proc.stdout + proc.stderr
    receipt = json.loads((out / "capture-retry.json").read_text(encoding="utf-8"))
    assert receipt["reason"] == "early-window-capture-phase"
    assert receipt["extractedFps"] == pytest.approx(60)
    assert receipt["earlyWindowRows"] == 18
    assert 12 < receipt["lastFailureRow"] <= receipt["earlyWindowRows"]


@needs_video_tools
def test_interior_selector_phase_failure_remains_divergence_without_receipt(
    tmp_path: Path,
) -> None:
    out = tmp_path / "interior-capture-divergence"
    _make_selector_phase_video(out / "ref-video" / "raw.webm")
    _make_selector_phase_video(
        out / "impl-video" / "raw.webm",
        gray_start=0.5,
        gray_end=0.65,
    )
    _seed_target_rect(out)
    env = {
        **os.environ,
        "UI_CLONE_VMC_SKIP_RECORD": "1",
        "VIDEO_COMPARE_ACTION_ONSET_SECONDS": "0.2",
        "RECORD_DURATION": "1",
        "FPS": "30",
        "VIEW_W": "320",
        "VIEW_H": "240",
        "VIDEO_COMPARE_TARGET_PADDING": "0",
        "SSIM_THRESHOLD": "0.99",
    }

    proc = subprocess.run(
        [
            "bash",
            str(VIDEO_COMPARE),
            "interior-capture-divergence-test",
            "http://ref.invalid",
            "http://impl.invalid",
            str(out),
            "hover:.target",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert not (out / "capture-retry.json").exists()


@needs_video_tools
def test_target_aa_rescue_converts_only_borderline_raw_failure(
    tmp_path: Path,
) -> None:
    out = tmp_path / "positive-aa-rescue"
    _make_shifted_target_video(out / "ref-video" / "raw.webm", 90)
    _make_shifted_target_video(out / "impl-video" / "raw.webm", 93)
    _seed_target_rect(out)
    threshold = 0.8795
    env = {
        **os.environ,
        "UI_CLONE_VMC_SKIP_RECORD": "1",
        "VIDEO_COMPARE_ACTION_ONSET_SECONDS": "0.2",
        "RECORD_DURATION": "1",
        "FPS": "30",
        "VIEW_W": "320",
        "VIEW_H": "240",
        "VIDEO_COMPARE_TARGET_PADDING": "0",
        "SSIM_THRESHOLD": str(threshold),
    }

    proc = subprocess.run(
        [
            "bash",
            str(VIDEO_COMPARE),
            "positive-aa-test",
            "http://ref.invalid",
            "http://impl.invalid",
            str(out),
            "hover:.target",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "pass-by-target-aa-filter" in proc.stdout
    receipt = json.loads((out / "target-aa-filter.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "pass-by-target-aa-filter"
    assert receipt["threshold"] == pytest.approx(threshold)
    assert receipt["rawFloor"] == pytest.approx(threshold - 0.02)
    assert receipt["blurSigma"] == pytest.approx(0.3)
    assert receipt["rawFloor"] <= receipt["rawMinSsim"] < threshold
    assert receipt["filteredMinSsim"] >= threshold
    assert receipt["rawFailures"] == receipt["rows"] > 0
    assert receipt["arcFrames"]["ref"] == receipt["arcFrames"]["impl"]


@needs_video_tools
def test_target_aa_rescue_rejects_target_dimension_delta_over_one_pixel(
    tmp_path: Path,
) -> None:
    out = tmp_path / "dimension-guard"
    _make_shifted_target_video(out / "ref-video" / "raw.webm", 90)
    _make_shifted_target_video(out / "impl-video" / "raw.webm", 93)
    _seed_target_rect(out, impl_width=162)
    threshold = 0.95
    env = {
        **os.environ,
        "UI_CLONE_VMC_SKIP_RECORD": "1",
        "VIDEO_COMPARE_ACTION_ONSET_SECONDS": "0.2",
        "RECORD_DURATION": "1",
        "FPS": "30",
        "VIEW_W": "320",
        "VIEW_H": "240",
        "VIDEO_COMPARE_TARGET_PADDING": "0",
        "SSIM_THRESHOLD": str(threshold),
    }

    proc = subprocess.run(
        [
            "bash",
            str(VIDEO_COMPARE),
            "dimension-guard-test",
            "http://ref.invalid",
            "http://impl.invalid",
            str(out),
            "hover:.target",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    plan = json.loads((out / "target-roi.json").read_text(encoding="utf-8"))
    dimension_delta = abs(plan["ref"]["target"]["width"] - plan["impl"]["target"]["width"])
    raw_values = [
        float(value)
        for value in (out / "diff-frames" / "target-raw-ssim.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert dimension_delta > 1
    assert min(raw_values) >= threshold - 0.02
    assert min(raw_values) < threshold
    assert not (out / "target-aa-filter.json").exists()
    assert not (out / "capture-retry.json").exists()


@needs_video_tools
def test_static_foreground_noise_filter_passes_equal_material_motion(
    tmp_path: Path,
) -> None:
    out = tmp_path / "static-foreground-noise"
    _make_static_foreground_delta_video(out / "ref-video" / "raw.webm")
    _make_static_foreground_delta_video(
        out / "impl-video" / "raw.webm",
        foreground_after="0x0d0d0d",
    )
    _seed_target_rect(out)
    env = {
        **os.environ,
        "UI_CLONE_VMC_SKIP_RECORD": "1",
        "VIDEO_COMPARE_ACTION_ONSET_SECONDS": "0.2",
        "RECORD_DURATION": "1",
        "FPS": "30",
        "VIEW_W": "320",
        "VIEW_H": "240",
        "VIDEO_COMPARE_TARGET_PADDING": "0",
        "SSIM_THRESHOLD": "0.9995",
    }

    proc = subprocess.run(
        [
            "bash",
            str(VIDEO_COMPARE),
            "static-foreground-noise-test",
            "http://ref.invalid",
            "http://impl.invalid",
            str(out),
            "hover:.target",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "pass-by-target-static-foreground-filter" in proc.stdout
    assert not (out / "target-aa-filter.json").exists()
    assert not (out / "capture-retry.json").exists()
    receipt = json.loads(
        (out / "target-static-foreground-filter.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["status"] == "pass-by-target-static-foreground-filter"
    assert receipt["materialThreshold"] == "6%"
    assert receipt["rawFailures"] > 0
    assert receipt["rawMinSsim"] < receipt["threshold"]
    assert receipt["filteredMinSsim"] >= receipt["threshold"]
    assert receipt["arcFrames"]["ref"] == receipt["arcFrames"]["impl"]
    assert receipt["dimensionsClose"] is True


@needs_video_tools
def test_static_foreground_filter_preserves_reference_only_motion(
    tmp_path: Path,
) -> None:
    out = tmp_path / "reference-only-foreground-motion"
    _make_static_foreground_delta_video(
        out / "ref-video" / "raw.webm",
        foreground_after="white",
    )
    _make_static_foreground_delta_video(out / "impl-video" / "raw.webm")
    _seed_target_rect(out)
    env = {
        **os.environ,
        "UI_CLONE_VMC_SKIP_RECORD": "1",
        "VIDEO_COMPARE_ACTION_ONSET_SECONDS": "0.2",
        "RECORD_DURATION": "1",
        "FPS": "30",
        "VIEW_W": "320",
        "VIEW_H": "240",
        "VIDEO_COMPARE_TARGET_PADDING": "0",
        "SSIM_THRESHOLD": "0.99",
    }

    proc = subprocess.run(
        [
            "bash",
            str(VIDEO_COMPARE),
            "reference-only-foreground-motion-test",
            "http://ref.invalid",
            "http://impl.invalid",
            str(out),
            "hover:.target",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "transition differs from original" in proc.stdout
    material_series = [
        float(value)
        for value in (out / "diff-frames" / "target-material-ssim.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert min(material_series) < 0.99
    assert not (out / "target-static-foreground-filter.json").exists()
    assert not (out / "target-aa-filter.json").exists()
    assert not (out / "capture-retry.json").exists()


@needs_video_tools
def test_non_aa_eligible_target_removes_stale_aa_sidecars(
    tmp_path: Path,
) -> None:
    out = tmp_path / "stale-aa-sidecars"
    _make_shifted_target_video(out / "ref-video" / "raw.webm", 90)
    _make_shifted_target_video(out / "impl-video" / "raw.webm", 93)
    _seed_target_rect(out, impl_width=162)

    stale_series = out / "diff-frames" / "target-aa-ssim.txt"
    stale_series.parent.mkdir(parents=True)
    stale_series.write_text("stale-aa-series\n", encoding="utf-8")
    stale_receipt = out / "target-aa-filter.json"
    stale_receipt.write_text('{"status":"stale-aa-rescue"}\n', encoding="utf-8")
    stale_material_series = out / "diff-frames" / "target-material-ssim.txt"
    stale_material_series.write_text("stale-material-series\n", encoding="utf-8")
    stale_material_receipt = out / "target-static-foreground-filter.json"
    stale_material_receipt.write_text(
        '{"status":"stale-static-foreground-filter"}\n',
        encoding="utf-8",
    )
    for side in ("ref", "impl"):
        stale_delta_dir = out / f"{side}-delta-aa-frames"
        stale_delta_dir.mkdir()
        (stale_delta_dir / "stale.txt").write_text("stale-aa-delta\n", encoding="utf-8")
        stale_material_delta_dir = out / f"{side}-delta-material-frames"
        stale_material_delta_dir.mkdir()
        (stale_material_delta_dir / "stale.txt").write_text(
            "stale-material-delta\n",
            encoding="utf-8",
        )

    env = {
        **os.environ,
        "UI_CLONE_VMC_SKIP_RECORD": "1",
        "VIDEO_COMPARE_ACTION_ONSET_SECONDS": "0.2",
        "RECORD_DURATION": "1",
        "FPS": "30",
        "VIEW_W": "320",
        "VIEW_H": "240",
        "VIDEO_COMPARE_TARGET_PADDING": "0",
        "SSIM_THRESHOLD": "0.95",
    }

    proc = subprocess.run(
        [
            "bash",
            str(VIDEO_COMPARE),
            "stale-aa-sidecars-test",
            "http://ref.invalid",
            "http://impl.invalid",
            str(out),
            "hover:.target",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "target-aa-filter" not in proc.stdout
    assert "stale-aa-series" not in proc.stdout
    assert "stale-aa-rescue" not in proc.stdout
    assert "stale-aa-delta" not in proc.stdout
    assert "stale-material-series" not in proc.stdout
    assert "stale-static-foreground-filter" not in proc.stdout
    assert "stale-material-delta" not in proc.stdout
    assert not stale_series.exists()
    assert not stale_receipt.exists()
    assert not stale_material_series.exists()
    assert not stale_material_receipt.exists()
    assert not (out / "ref-delta-aa-frames").exists()
    assert not (out / "impl-delta-aa-frames").exists()
    assert not (out / "ref-delta-material-frames").exists()
    assert not (out / "impl-delta-material-frames").exists()


@needs_video_tools
@pytest.mark.parametrize(
    ("threshold", "band"),
    [
        ("0.90", "1"),
        ("0.89", "0.02"),
    ],
)
def test_target_aa_rescue_bounds_cannot_be_widened_by_environment(
    tmp_path: Path,
    threshold: str,
    band: str,
) -> None:
    out = tmp_path / f"bounded-{threshold}"
    _make_shifted_target_video(out / "ref-video" / "raw.webm", 90)
    _make_shifted_target_video(out / "impl-video" / "raw.webm", 93)
    _seed_target_rect(out)
    env = {
        **os.environ,
        "UI_CLONE_VMC_SKIP_RECORD": "1",
        "VIDEO_COMPARE_ACTION_ONSET_SECONDS": "0.2",
        "RECORD_DURATION": "1",
        "FPS": "30",
        "VIEW_W": "320",
        "VIEW_H": "240",
        "VIDEO_COMPARE_TARGET_PADDING": "0",
        "SSIM_THRESHOLD": threshold,
        "VIDEO_COMPARE_TARGET_AA_RESCUE_BAND": band,
        "VIDEO_COMPARE_TARGET_DELTA_BLUR": "10",
    }

    proc = subprocess.run(
        [
            "bash",
            str(VIDEO_COMPARE),
            "bounded-aa-test",
            "http://ref.invalid",
            "http://impl.invalid",
            str(out),
            "hover:.target",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "transition differs from original" in proc.stdout
    assert not (out / "target-aa-filter.json").exists()
