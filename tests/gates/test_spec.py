import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from ui_clone.gate import Gate

from ._helpers import (
    _write_min_spec_artifacts,
)


def _write_png(path: Path) -> None:
    Image.new("RGB", (2, 2), color="black").save(path, format="PNG")


def _write_webm(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=16x16",
            "-frames:v",
            "1",
            "-c:v",
            "libvpx-vp9",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _write_spec_gate_fixture(
    ref: Path,
    *,
    bundle_map: dict[str, Any] | None = None,
    transition_spec: dict[str, Any] | None = None,
    runtime_dump: dict[str, Any] | None = None,
) -> None:
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps(bundle_map or {"chunks": ["fixture.js"]}))
    bundles = ref / "bundles"
    bundles.mkdir()
    (bundles / "fixture.js").write_text("// fixture bundle")
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": []}))
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredChecks": []})
    )
    verify = ref / "verify"
    verify.mkdir()
    _write_png(verify / "runtime.png")
    (ref / "transition-spec.json").write_text(
        json.dumps(
            transition_spec
            or {
                "transitions": [
                    {
                        "id": "fixture-runtime-scroll",
                        "trigger": "scroll",
                        "source_chunk": "fixture.js",
                        "bundle_branch": "runtime observed",
                        "target": ".fixture",
                        "animation": {"type": "scroll-scrub"},
                        "reference_frames": ["verify/runtime.png"],
                    }
                ]
            }
        )
    )
    if runtime_dump is not None:
        (ref / "animation-runtime-dump.json").write_text(json.dumps(runtime_dump))


def test_gate_spec_fails_when_transition_spec_missing(tmp_path: Path) -> None:
    """gate_spec must fail when transition-spec.json is absent."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({"chunks": ["a.js"]}))
    # transition-spec.json intentionally absent

    gate = Gate(ref)
    results = gate.gate_spec()
    failures = [r for r in results if r.status == "fail"]
    assert any("transition-spec" in r.label or "transition-spec" in r.message for r in failures), (
        "Missing transition-spec.json must produce a fail"
    )


def test_gate_spec_points_to_runtime_dump_when_motion_exists_but_spec_missing(
    tmp_path: Path,
) -> None:
    """Runtime motion evidence makes a missing transition spec a concrete
    extraction failure, not just a generic missing file.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({"chunks": ["a.js"]}))
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": []}))
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredChecks": []})
    )
    (ref / "animation-runtime-dump.json").write_text(
        json.dumps(
            {
                "gsap": {"version": "3.12.5"},
                "scrollTrigger": [{"trigger": "section.hero", "start": 0, "end": 600}],
                "ix2": {"timelineCount": 0, "eventCount": 0},
            }
        )
    )

    results = Gate(ref).gate_spec()
    failures = [r for r in results if r.status == "fail"]

    assert any(
        r.label == "runtime motion transition-spec coverage"
        and "animation-runtime-dump.json" in r.message
        and "ScrollTrigger" in r.message
        for r in failures
    ), failures


def test_gate_spec_points_to_runtime_dump_when_motion_exists_but_spec_empty(
    tmp_path: Path,
) -> None:
    """Runtime dump activity must force Step 5d to populate the spec."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_min_spec_artifacts(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredChecks": []})
    )
    (ref / "animation-runtime-dump.json").write_text(
        json.dumps(
            {
                "gsap": None,
                "scrollTrigger": [],
                "framer": {"motionValues": [{"selector": ".card"}]},
                "ix2": {"timelineCount": 2, "eventCount": 3},
            }
        )
    )

    results = Gate(ref).gate_spec()
    failures = [r for r in results if r.status == "fail"]

    assert any(
        r.label == "runtime motion transition-spec coverage"
        and "Framer" in r.message
        and "Webflow IX2" in r.message
        for r in failures
    ), failures


def test_gate_spec_fails_motion_rich_runtime_capture_error_first(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    _write_spec_gate_fixture(
        ref,
        bundle_map={"chunks": ["fixture.js"], "libraries": ["gsap", "ScrollTrigger"]},
        transition_spec={"transitions": []},
        runtime_dump={
            "captureStatus": "error",
            "captureError": {"name": "EvalError", "message": "page closed"},
        },
    )

    failures = [r for r in Gate(ref).gate_spec() if r.status == "fail"]

    assert failures[0].label == "runtime capture integrity"
    assert "captureError" in failures[0].message
    assert "page closed" in failures[0].message


def test_gate_spec_fails_motion_rich_legacy_empty_runtime_capture(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    _write_spec_gate_fixture(
        ref,
        bundle_map={"chunks": ["fixture.js"], "libraries": ["framer-motion"]},
        runtime_dump={"note": "eval returned empty"},
    )

    failures = [r for r in Gate(ref).gate_spec() if r.status == "fail"]

    assert any(
        r.label == "runtime capture integrity"
        and "eval returned empty" in r.message
        and "animation-runtime-dump.json" in r.message
        for r in failures
    ), failures


def test_gate_spec_allows_measured_static_runtime_capture(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    _write_spec_gate_fixture(
        ref,
        bundle_map={"chunks": ["fixture.js"]},
        runtime_dump={
            "captureStatus": "ok",
            "scrollAudit": {
                "engine": "native",
                "maxScroll": 0,
                "samples": {"requested": [0, 0.5, 1], "observed": [0, 0, 0]},
            },
            "scrollLinkedStyles": [],
        },
    )

    integrity = [r for r in Gate(ref).gate_spec() if r.label == "runtime capture integrity"]

    assert integrity == []


def test_gate_spec_keeps_legacy_runtime_dump_without_failure_marker_compatible(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    _write_spec_gate_fixture(
        ref,
        bundle_map={"chunks": ["fixture.js"], "libraries": ["gsap"]},
        runtime_dump={"scrollTrigger": []},
    )

    integrity = [r for r in Gate(ref).gate_spec() if r.label == "runtime capture integrity"]

    assert integrity == []


def test_gate_spec_fails_motion_rich_ok_capture_with_immobile_scroll_audit(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    _write_spec_gate_fixture(
        ref,
        bundle_map={"chunks": ["fixture.js"], "libraries": ["gsap"]},
        runtime_dump={
            "captureStatus": "ok",
            "scrollAudit": {
                "engine": "native",
                "maxScroll": 1200,
                "samples": {
                    "requested": [0, 0.25, 0.5, 0.75, 1],
                    "observed": [0, 0.00001, 0.00002, 0.00002, 0.00001],
                },
            },
            "scrollLinkedStyles": [],
        },
    )

    failures = [r for r in Gate(ref).gate_spec() if r.status == "fail"]

    assert any(
        r.label == "runtime capture integrity"
        and "observed normalized positions" in r.message
        and "maxScroll" in r.message
        for r in failures
    ), failures


def test_gate_spec_fails_new_format_ok_capture_with_positive_static_scroll_audit(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    _write_spec_gate_fixture(
        ref,
        bundle_map={"chunks": ["fixture.js"]},
        runtime_dump={
            "captureStatus": "ok",
            "scrollAudit": {
                "engine": "native",
                "maxScroll": 1200,
                "samples": {
                    "requested": [0, 0.25, 0.5, 0.75, 1],
                    "observed": [0, 0, 0.00001, 0.00001, 0],
                },
            },
            "scrollLinkedStyles": [],
        },
    )

    failures = [r for r in Gate(ref).gate_spec() if r.status == "fail"]

    assert any(
        r.label == "runtime capture integrity"
        and "observed normalized positions" in r.message
        and "maxScroll" in r.message
        for r in failures
    ), failures


@pytest.mark.parametrize("max_scroll", ["not-a-number", "NaN", "Infinity"])
def test_gate_spec_fails_new_format_ok_capture_with_malformed_max_scroll(
    tmp_path: Path,
    max_scroll: str,
) -> None:
    ref = tmp_path / "ref"
    _write_spec_gate_fixture(
        ref,
        bundle_map={"chunks": ["fixture.js"]},
        runtime_dump={
            "captureStatus": "ok",
            "scrollAudit": {
                "engine": "native",
                "maxScroll": max_scroll,
                "samples": {"observed": [0, 0.5, 1]},
            },
            "scrollLinkedStyles": [],
        },
    )

    failures = [r for r in Gate(ref).gate_spec() if r.status == "fail"]

    assert any(
        r.label == "runtime capture integrity"
        and "maxScroll" in r.message
        and "finite numeric" in r.message
        for r in failures
    ), failures


def test_gate_spec_fails_new_format_ok_capture_with_nonfinite_observed_positions(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    _write_spec_gate_fixture(
        ref,
        bundle_map={"chunks": ["fixture.js"]},
        runtime_dump={
            "captureStatus": "ok",
            "scrollAudit": {
                "engine": "native",
                "maxScroll": 1200,
                "samples": {"observed": ["NaN", "NaN", "NaN"]},
            },
            "scrollLinkedStyles": [],
        },
    )

    failures = [r for r in Gate(ref).gate_spec() if r.status == "fail"]

    assert any(
        r.label == "runtime capture integrity"
        and "observed normalized positions" in r.message
        and "[]" in r.message
        for r in failures
    ), failures


def test_gate_spec_accepts_runtime_audit_producer_sample_objects(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    _write_spec_gate_fixture(
        ref,
        bundle_map={"chunks": ["fixture.js"]},
        runtime_dump={
            "captureStatus": "ok",
            "scrollAudit": {
                "engine": "native",
                "maxScroll": 18429,
                "samples": [
                    {"requested": 0, "observed": 0, "method": "native"},
                    {"requested": 0.05, "observed": 0.04998, "method": "native"},
                    {"requested": 0.1, "observed": 0.10002, "method": "native"},
                    {"requested": 0.15, "observed": 0.15001, "method": "native"},
                ],
            },
            "scrollLinkedStyles": [],
        },
    )

    integrity = [r for r in Gate(ref).gate_spec() if r.label == "runtime capture integrity"]

    assert integrity == []


def test_gate_spec_fails_runtime_audit_sample_objects_without_distinct_observed(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    _write_spec_gate_fixture(
        ref,
        bundle_map={"chunks": ["fixture.js"]},
        runtime_dump={
            "captureStatus": "ok",
            "scrollAudit": {
                "engine": "native",
                "maxScroll": 18429,
                "samples": [
                    {"requested": 0, "observed": "NaN", "method": "native"},
                    {"requested": 0.25, "observed": 0, "method": "native"},
                    {"requested": 0.5, "observed": 0.00001, "method": "native"},
                    {"requested": 0.75, "observed": "Infinity", "method": "native"},
                ],
            },
            "scrollLinkedStyles": [],
        },
    )

    failures = [r for r in Gate(ref).gate_spec() if r.status == "fail"]

    assert any(
        r.label == "runtime capture integrity"
        and "observed normalized positions" in r.message
        and "[0.0, 1e-05]" in r.message
        for r in failures
    ), failures


def test_gate_spec_ignores_raw_numeric_runtime_audit_sample_list(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    _write_spec_gate_fixture(
        ref,
        bundle_map={"chunks": ["fixture.js"]},
        runtime_dump={
            "captureStatus": "ok",
            "scrollAudit": {
                "engine": "native",
                "maxScroll": 18429,
                "samples": [0, 0.5, 1],
            },
            "scrollLinkedStyles": [],
        },
    )

    failures = [r for r in Gate(ref).gate_spec() if r.status == "fail"]

    assert any(
        r.label == "runtime capture integrity"
        and "observed normalized positions" in r.message
        and "[]" in r.message
        for r in failures
    ), failures


def test_gate_spec_fails_motion_rich_ok_capture_missing_scroll_audit(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    _write_spec_gate_fixture(
        ref,
        bundle_map={"chunks": ["fixture.js"], "libraries": ["gsap"]},
        runtime_dump={"captureStatus": "ok", "scrollLinkedStyles": []},
    )

    failures = [r for r in Gate(ref).gate_spec() if r.status == "fail"]

    assert any(
        r.label == "runtime capture integrity"
        and "no scrollAudit" in r.message
        and "motion-rich" in r.message
        for r in failures
    ), failures


def test_gate_spec_accepts_covered_runtime_scroll_linked_site(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    _write_spec_gate_fixture(
        ref,
        transition_spec={
            "transitions": [
                {
                    "id": "hero-runtime-scroll",
                    "trigger": "scroll",
                    "source_chunk": "fixture.js",
                    "sourceArtifact": "animation-runtime-dump.json",
                    "sourceId": "runtime-scroll-hero",
                    "bundle_branch": "runtime observed",
                    "target": ".hero",
                    "animation": {"type": "scroll-scrub"},
                    "reference_frames": ["verify/runtime.png"],
                }
            ]
        },
        runtime_dump={
            "captureStatus": "ok",
            "scrollAudit": {
                "maxScroll": 900,
                "samples": {"observed": [0, 0.5, 1]},
            },
            "scrollLinkedStyles": [
                {"sourceId": "runtime-scroll-hero", "selector": ".hero", "varies": ["transform"]}
            ],
        },
    )

    coverage = [r for r in Gate(ref).gate_spec() if r.label == "spec-runtime-site-coverage"]

    assert coverage == []


def test_gate_spec_accepts_unambiguous_runtime_site_selector_fallback(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    _write_spec_gate_fixture(
        ref,
        transition_spec={
            "transitions": [
                {
                    "id": "legacy-hero-scroll",
                    "trigger": "scroll",
                    "source_chunk": "fixture.js",
                    "bundle_branch": "runtime observed before source ids",
                    "target": ".hero",
                    "animation": {"type": "scroll-scrub"},
                    "reference_frames": ["verify/runtime.png"],
                }
            ]
        },
        runtime_dump={
            "captureStatus": "ok",
            "scrollAudit": {"maxScroll": 900, "samples": {"observed": [0, 0.5, 1]}},
            "scrollLinkedStyles": [
                {"sourceId": "runtime-scroll-hero", "selector": ".hero", "varies": ["transform"]}
            ],
        },
    )

    coverage = [r for r in Gate(ref).gate_spec() if r.label == "spec-runtime-site-coverage"]

    assert coverage == []


def test_gate_spec_rejects_selector_fallback_for_duplicate_runtime_selectors(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    _write_spec_gate_fixture(
        ref,
        transition_spec={
            "transitions": [
                {
                    "id": "legacy-hero-scroll",
                    "trigger": "scroll",
                    "source_chunk": "fixture.js",
                    "bundle_branch": "runtime observed before source ids",
                    "target": ".hero",
                    "animation": {"type": "scroll-scrub"},
                    "reference_frames": ["verify/runtime.png"],
                }
            ]
        },
        runtime_dump={
            "captureStatus": "ok",
            "scrollAudit": {"maxScroll": 900, "samples": {"observed": [0, 0.5, 1]}},
            "scrollLinkedStyles": [
                {"sourceId": "runtime-scroll-hero-a", "selector": ".hero", "varies": ["transform"]},
                {"sourceId": "runtime-scroll-hero-b", "selector": ".hero", "varies": ["opacity"]},
            ],
        },
    )

    failures = [r for r in Gate(ref).gate_spec() if r.status == "fail"]

    assert any(
        r.label == "spec-runtime-site-coverage"
        and "runtime-scroll-hero-a" in r.message
        and "runtime-scroll-hero-b" in r.message
        for r in failures
    ), failures


def test_gate_spec_rejects_selector_fallback_for_duplicate_transition_targets(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    _write_spec_gate_fixture(
        ref,
        transition_spec={
            "transitions": [
                {
                    "id": "legacy-hero-scroll-a",
                    "trigger": "scroll",
                    "source_chunk": "fixture.js",
                    "bundle_branch": "runtime observed before source ids",
                    "target": ".hero",
                    "animation": {"type": "scroll-scrub"},
                    "reference_frames": ["verify/runtime.png"],
                },
                {
                    "id": "legacy-hero-scroll-b",
                    "trigger": "scroll",
                    "source_chunk": "fixture.js",
                    "bundle_branch": "runtime observed before source ids",
                    "target": ".hero",
                    "animation": {"type": "scroll-scrub"},
                    "reference_frames": ["verify/runtime.png"],
                },
            ]
        },
        runtime_dump={
            "captureStatus": "ok",
            "scrollAudit": {"maxScroll": 900, "samples": {"observed": [0, 0.5, 1]}},
            "scrollLinkedStyles": [
                {"sourceId": "runtime-scroll-hero", "selector": ".hero", "varies": ["transform"]}
            ],
        },
    )

    failures = [r for r in Gate(ref).gate_spec() if r.status == "fail"]

    assert any(
        r.label == "spec-runtime-site-coverage"
        and "runtime-scroll-hero" in r.message
        and ".hero" in r.message
        for r in failures
    ), failures


def test_gate_spec_accepts_skipped_runtime_scroll_linked_site(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    _write_spec_gate_fixture(
        ref,
        transition_spec={
            "transitions": [
                {
                    "id": "other-scroll",
                    "trigger": "scroll",
                    "source_chunk": "fixture.js",
                    "bundle_branch": "runtime observed",
                    "target": ".other",
                    "animation": {"type": "scroll-scrub"},
                    "reference_frames": ["verify/runtime.png"],
                }
            ],
            "skipped": [
                {
                    "sourceArtifact": "animation-runtime-dump.json",
                    "sourceId": "runtime-scroll-hero",
                    "reason": "virtualized by sticky native scroll only",
                }
            ],
        },
        runtime_dump={
            "captureStatus": "ok",
            "scrollAudit": {"maxScroll": 900, "samples": {"observed": [0, 0.5, 1]}},
            "scrollLinkedStyles": [
                {"sourceId": "runtime-scroll-hero", "selector": ".hero", "varies": ["transform"]}
            ],
        },
    )

    coverage = [r for r in Gate(ref).gate_spec() if r.label == "spec-runtime-site-coverage"]

    assert coverage == []


def test_gate_spec_fails_uncovered_runtime_scroll_linked_site(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    _write_spec_gate_fixture(
        ref,
        runtime_dump={
            "captureStatus": "ok",
            "scrollAudit": {"maxScroll": 900, "samples": {"observed": [0, 0.5, 1]}},
            "scrollLinkedStyles": [
                {"sourceId": "runtime-scroll-hero", "selector": ".hero", "varies": ["transform"]},
                {"selector": ".legacy-no-source-id", "varies": ["opacity"]},
            ],
            "animations": [{"selector": ".non-scroll", "sourceId": "runtime-non-scroll"}],
        },
    )

    failures = [r for r in Gate(ref).gate_spec() if r.status == "fail"]

    assert any(
        r.label == "spec-runtime-site-coverage"
        and "runtime-scroll-hero" in r.message
        and ".hero" in r.message
        and "legacy-no-source-id" not in r.message
        and "runtime-non-scroll" not in r.message
        for r in failures
    ), failures


def test_gate_spec_fails_when_bundle_map_missing(tmp_path: Path) -> None:
    """gate_spec must fail when bundle-map.json is absent."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "fixture-reveal-on-scroll",
                        "trigger": "intersection",
                        "source_chunk": "fixture.js",
                        "bundle_branch": "main",
                        "target": ".fixture",
                        "animation": "opacity-translateY",
                        "reference_frames": ["frame_00.png"],
                    }
                ]
            }
        )
    )
    # bundle-map.json intentionally absent

    gate = Gate(ref)
    results = gate.gate_spec()
    failures = [r for r in results if r.status == "fail"]
    assert any("bundle-map" in r.label or "bundle-map" in r.message for r in failures), (
        "Missing bundle-map.json must produce a fail"
    )


def test_gate_spec_passes_with_required_files(tmp_path: Path) -> None:
    """gate_spec must pass when all required artifacts exist."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({"chunks": ["a.js"]}))
    bundles = ref / "bundles"
    bundles.mkdir()
    (bundles / "fixture.js").write_text("// fixture bundle")
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "fixture-reveal-on-scroll",
                        "trigger": "intersection",
                        "source_chunk": "fixture.js",
                        "bundle_branch": "main",
                        "target": ".fixture",
                        "animation": "opacity-translateY",
                        "reference_frames": ["frame_00.png"],
                    }
                ]
            }
        )
    )
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": []}))
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredChecks": []})
    )
    verify = ref / "verify"
    verify.mkdir()
    for i in range(5):
        _write_png(verify / f"frame_{i:02d}.png")

    gate = Gate(ref)
    results = gate.gate_spec()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"gate_spec must pass with required files present: {failures}"


@pytest.mark.parametrize(
    ("dynamic", "bundle_branch", "animation", "should_fail"),
    [
        (
            None,
            "scale timeline",
            {"type": "scroll-scrub", "randomScaleRange": [0.8, 2]},
            True,
        ),
        (
            False,
            "scale timeline",
            {"type": "scroll-scrub", "randomScaleRange": [0.8, 2]},
            True,
        ),
        (
            True,
            "scale timeline",
            {"type": "scroll-scrub", "randomScaleRange": [0.8, 2]},
            False,
        ),
        (None, "gsap.utils.random(0.8, 2)", {"type": "scroll-scrub"}, True),
        (None, "deterministic scale timeline", {"type": "scroll-scrub"}, False),
    ],
)
def test_gate_spec_requires_dynamic_for_stochastic_animation(
    tmp_path: Path,
    dynamic: bool | None,
    bundle_branch: str,
    animation: dict[str, Any],
    should_fail: bool,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({"chunks": ["hero.js"]}))
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": []}))
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredChecks": []})
    )
    bundles = ref / "bundles"
    bundles.mkdir()
    (bundles / "hero.js").write_text("// random scale fixture")
    verify = ref / "verify"
    verify.mkdir()
    _write_png(verify / "hero.png")
    transition: dict[str, Any] = {
        "id": "hero-random-parallax",
        "trigger": "scroll",
        "source_chunk": "hero.js",
        "bundle_branch": bundle_branch,
        "target": ".hero .item-outer",
        "animation": animation,
        "reference_frames": ["verify/hero.png"],
    }
    if dynamic is not None:
        transition["dynamic"] = dynamic
    (ref / "transition-spec.json").write_text(json.dumps({"transitions": [transition]}))

    stochastic_failures = [
        result
        for result in Gate(ref).gate_spec()
        if result.status == "fail" and result.label == "stochastic transition dynamic mask"
    ]

    assert bool(stochastic_failures) is should_fail


def test_gate_spec_fails_when_source_chunk_is_not_grounded(tmp_path: Path) -> None:
    """Spec must reject source_chunk values that post-implement will reject later."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({"chunks": ["a.js"]}))
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": []}))
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredChecks": []})
    )
    (ref / "canvas-webgl-detection.json").write_text(
        json.dumps(
            {
                "primaryRenderType": "canvas",
                "canvasCount": 1,
                "hasWebGL": False,
            }
        )
    )
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "hero-canvas-runtime",
                        "trigger": "page load",
                        "source_chunk": "canvas-webgl-detection.json",
                        "bundle_branch": "canvas-webgl-detection.json: canvasCount=1",
                        "target": "canvas",
                        "animation": {"engine": "canvas-2d", "dynamic": True},
                        "reference_frames": "verify/page-scroll/f000.png",
                    }
                ]
            }
        )
    )
    verify = ref / "verify"
    verify.mkdir()
    for i in range(5):
        _write_png(verify / f"frame_{i:02d}.png")

    failures = [
        r
        for r in Gate(ref).gate_spec()
        if r.status == "fail" and r.label == "spec-bundle-grounding"
    ]

    assert failures, "spec gate must catch ungrounded source_chunk before generation"
    assert "canvas-webgl-detection.json" in failures[0].message


def test_gate_spec_fails_when_any_transition_missing_documented_fields(tmp_path: Path) -> None:
    """Every transition entry must carry the documented bundle-to-code handoff fields."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({"chunks": ["a.js"]}))
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": []}))
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredChecks": []})
    )
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "hero-reveal",
                        "trigger": "page load",
                        "source_chunk": "a.js",
                        "bundle_branch": "first visit",
                        "target": ".hero",
                        "animation": {"property": "opacity", "from": 0, "to": 1},
                        "reference_frames": "verify/hero/f001.png",
                    },
                    {
                        "id": "cards-scroll",
                        "trigger": "scroll",
                        "bundle_branch": "desktop",
                    },
                ]
            }
        )
    )
    verify = ref / "verify"
    verify.mkdir()
    for i in range(5):
        _write_png(verify / f"frame_{i:02d}.png")

    gate = Gate(ref)
    results = gate.gate_spec()
    failures = [r for r in results if r.status == "fail"]

    assert any(
        r.label == "transitions[1] keys"
        and "source_chunk" in r.message
        and "target" in r.message
        and "animation" in r.message
        and "reference_frames" in r.message
        for r in failures
    ), f"Missing documented transition fields must fail gate_spec: {failures}"


@pytest.mark.parametrize(
    "reference_frames",
    [
        "none",
        "",
        [],
        "verify/hero/missing.png",
    ],
)
def test_gate_spec_fails_without_existing_reference_frame_evidence(
    tmp_path: Path,
    reference_frames: object,
) -> None:
    """Declared transitions need real local image/video evidence."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({"chunks": ["fixture.js"]}))
    bundles = ref / "bundles"
    bundles.mkdir()
    (bundles / "fixture.js").write_text("// fixture bundle")
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": []}))
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredChecks": []})
    )
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "hero-reveal",
                        "trigger": "page load",
                        "source_chunk": "fixture.js",
                        "bundle_branch": "first visit",
                        "target": ".hero",
                        "animation": {"property": "opacity", "from": 0, "to": 1},
                        "reference_frames": reference_frames,
                    }
                ]
            }
        )
    )

    failures = [result for result in Gate(ref).gate_spec() if result.status == "fail"]

    assert any(
        result.label == "transitions[0] reference frame evidence"
        and "existing local image/video" in result.message
        for result in failures
    ), failures


@pytest.mark.parametrize(
    "reference_frames",
    [
        "verify/intro/f010.png",
        ["verify/intro/f010.png", "verify/intro/intro.webm"],
        "verify/intro/f010.png to f030.png",
    ],
)
def test_gate_spec_accepts_existing_reference_frame_evidence(
    tmp_path: Path,
    reference_frames: object,
) -> None:
    """String, list, and range text forms can point to captured media."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({"chunks": ["fixture.js"]}))
    bundles = ref / "bundles"
    bundles.mkdir()
    (bundles / "fixture.js").write_text("// fixture bundle")
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": []}))
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredChecks": []})
    )
    evidence = ref / "verify" / "intro"
    evidence.mkdir(parents=True)
    _write_png(evidence / "f010.png")
    _write_png(evidence / "f030.png")
    if "webm" in str(reference_frames):
        if shutil.which("ffmpeg") is None:
            pytest.skip("ffmpeg is required to generate valid WebM evidence")
        _write_webm(evidence / "intro.webm")
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "hero-reveal",
                        "trigger": "page load",
                        "source_chunk": "fixture.js",
                        "bundle_branch": "first visit",
                        "target": ".hero",
                        "animation": {"property": "opacity", "from": 0, "to": 1},
                        "reference_frames": reference_frames,
                    }
                ]
            }
        )
    )

    evidence_results = [
        result
        for result in Gate(ref).gate_spec()
        if result.label == "transitions[0] reference frame evidence"
    ]

    assert evidence_results == []


@pytest.mark.parametrize(
    ("filename", "payload"),
    [
        ("corrupt.png", b"\x89PNG\r\n\x1a\nnot-a-decodable-image"),
        ("corrupt.webm", b"\x1aE\xdf\xa3not-a-decodable-video"),
    ],
)
def test_gate_spec_rejects_corrupt_reference_media(
    tmp_path: Path,
    filename: str,
    payload: bytes,
) -> None:
    """A non-empty file with a media extension is not transition evidence."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({"chunks": ["fixture.js"]}))
    bundles = ref / "bundles"
    bundles.mkdir()
    (bundles / "fixture.js").write_text("// fixture bundle")
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": []}))
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredChecks": []})
    )
    evidence = ref / "verify"
    evidence.mkdir()
    (evidence / filename).write_bytes(payload)
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "hero-reveal",
                        "trigger": "page load",
                        "source_chunk": "fixture.js",
                        "bundle_branch": "first visit",
                        "target": ".hero",
                        "animation": {"property": "opacity", "from": 0, "to": 1},
                        "reference_frames": f"verify/{filename}",
                    }
                ]
            }
        )
    )

    failures = [
        result
        for result in Gate(ref).gate_spec()
        if result.label == "transitions[0] reference frame evidence"
    ]

    assert len(failures) == 1
    assert failures[0].status == "fail"
    assert "decodable" in failures[0].message


def test_gate_spec_passes_when_no_substitute_decisions(tmp_path: Path) -> None:
    """No paid-features.json (or no substitute decisions) → cross-check is silent."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_min_spec_artifacts(ref)
    (ref / "paid-features.json").write_text(
        json.dumps(
            {
                "paidFonts": [
                    {
                        "family": None,
                        "cdn": "use.typekit.net",
                        "evidence": "css/main.css:1",
                        "decision": "use",
                    }
                ]
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_spec()
    sub_failures = [
        r for r in results if r.status == "fail" and "paid-font substitution" in r.label
    ]
    assert not sub_failures, f"decision=use must not trigger substitution failure: {sub_failures}"


def test_gate_spec_fails_when_substitute_but_no_asset_substitution_json(tmp_path: Path) -> None:
    """decision='substitute' without asset-substitution.json must fail at spec time
    (otherwise font-parity discovers it much later, after generation)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_min_spec_artifacts(ref)
    (ref / "paid-features.json").write_text(
        json.dumps(
            {
                "paidFonts": [
                    {
                        "family": None,
                        "cdn": "use.typekit.net",
                        "evidence": "css/main.css:1",
                        "decision": "substitute",
                    }
                ]
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_spec()
    failures = [r for r in results if r.status == "fail" and "paid-font substitution" in r.label]
    assert failures, "substitute without asset-substitution.json must fail"
    assert any("use.typekit.net" in r.message for r in failures)


def test_gate_spec_fails_when_asset_substitution_has_no_fonts(tmp_path: Path) -> None:
    """asset-substitution.json present but missing fonts[] → still fail."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_min_spec_artifacts(ref)
    (ref / "paid-features.json").write_text(
        json.dumps(
            {
                "paidFonts": [
                    {
                        "family": None,
                        "cdn": "fast.fonts.net",
                        "evidence": "css/main.css:7",
                        "decision": "substitute",
                    }
                ]
            }
        )
    )
    # Has images but no fonts — schema allows other categories
    (ref / "asset-substitution.json").write_text(json.dumps({"images": [{"from": "a", "to": "b"}]}))

    gate = Gate(ref)
    results = gate.gate_spec()
    failures = [r for r in results if r.status == "fail" and "paid-font substitution" in r.label]
    assert failures, "asset-substitution.json without fonts[] must fail"


def test_gate_spec_passes_when_substitute_and_fonts_declared(tmp_path: Path) -> None:
    """substitute + asset-substitution.json with fonts[] → pass."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_min_spec_artifacts(ref)
    (ref / "paid-features.json").write_text(
        json.dumps(
            {
                "paidFonts": [
                    {
                        "family": None,
                        "cdn": "use.typekit.net",
                        "evidence": "css/main.css:1",
                        "decision": "substitute",
                    }
                ]
            }
        )
    )
    (ref / "asset-substitution.json").write_text(
        json.dumps(
            {"fonts": [{"from": "Adobe Garamond Pro", "to": "EB Garamond", "reason": "paid"}]}
        )
    )
    # Loop-38 fix: substitute decision is only valid AFTER a download attempt.
    # download-log.json must record at least one URL matching the family/CDN.
    (ref / "download-log.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "attempts": [
                    {
                        "url": "https://use.typekit.net/k/whatever-garamond.woff2",
                        "status": 403,
                        "error": "license-blocked",
                    }
                ],
                "succeeded": 0,
                "failed": 1,
                "totalAttempted": 1,
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_spec()
    failures = [r for r in results if r.status == "fail" and "paid-font substitution" in r.label]
    assert not failures, f"declared substitute must pass: {failures}"
    sub_pass = [r for r in results if r.label == "paid-font substitution"]
    assert sub_pass and sub_pass[0].status == "pass"


def test_gate_spec_fails_when_substitute_without_download_attempt(tmp_path: Path) -> None:
    """Loop-38 regression: paid font marked decision='substitute' with
    asset-substitution.json declaration but ZERO download attempt in
    download-log.json must FAIL. Research-mode policy enforced.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_min_spec_artifacts(ref)
    (ref / "paid-features.json").write_text(
        json.dumps(
            {
                "paidFonts": [
                    {
                        "family": None,
                        "cdn": "Die Grotesk",
                        "evidence": "css/variables.txt:38",
                        "decision": "substitute",
                        "substituteFamily": "Inter Variable",
                    }
                ]
            }
        )
    )
    (ref / "asset-substitution.json").write_text(
        json.dumps({"fonts": [{"from": "Die Grotesk", "to": "Inter Variable"}]})
    )
    (ref / "download-log.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "attempts": [
                    # 46 image attempts — no font URL anywhere
                    {"url": "https://cdn.example/hero.png", "status": 200},
                    {"url": "https://cdn.example/logo.svg", "status": 200},
                ],
                "succeeded": 2,
                "failed": 0,
                "totalAttempted": 2,
            }
        )
    )
    gate = Gate(ref)
    results = gate.gate_spec()
    fail_labels = [r.label for r in results if r.status == "fail"]
    assert any("download attempt missing" in label for label in fail_labels), (
        f"loop-38 regression — must fail without download attempt: {fail_labels}"
    )


def test_spec_selectors_present_in_dom_flags_subpage_selectors(tmp_path: Path) -> None:
    """A transition-spec target whose class/id is absent from the captured
    structure.json BLOCKS at draft time — the subpage/late-mount selector that
    otherwise only fails downstream at transition-fires after a full generate.
    Present selectors, runtime-injected (swiper/canvas), and CSS-module hashed
    names must NOT be flagged."""
    from ui_clone.gates.spec import _check_spec_selectors_present_in_dom

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "structure.json").write_text(
        json.dumps(
            {
                "tag": "body",
                "children": [
                    {
                        "tag": "div",
                        "class": "main-header",
                        "children": [
                            {
                                "tag": "div",
                                "class": "item-outer",
                                "children": [
                                    {"tag": "div", "class": "item-inner"},
                                ],
                            },
                        ],
                    },
                    # CSS-module hashed class present on the page
                    {"tag": "section", "class": "dga_text_line__MVXuV"},
                    # Tailwind class with a colon (DOM stores it unescaped)
                    {"tag": "div", "class": "md:flex"},
                ],
            }
        )
    )
    spec = {
        "transitions": [
            # present (exact)
            {"id": "parallax", "target": ".item-inner"},
            # present (CSS-module hash — base name should match)
            {"id": "word", "target": ".dga_text_line"},
            # runtime-injected — exempt even though absent from capture
            {"id": "swiper", "target": ".swiper-wrapper"},
            {"id": "lottie", "target": "canvas"},
            # ABSENT class — bundle-derived subpage selector (the bug)
            {"id": "page-hero", "target": ".page-hero .parallax-items"},
            {"id": "para", "target": ".page-hero-front .paragraph-1"},
            # COMPOUND with present ANCESTOR but absent TARGET LEAF — must be
            # flagged (the `any`-semantics bug Codex caught: .main-header exists
            # but .subpage-leaf does not, so the selector resolves to nothing).
            {"id": "compound", "target": ".main-header .subpage-leaf"},
            # comma selector LIST with one fully-present group — must NOT be
            # flagged (a list matches if any group matches).
            {"id": "list", "target": ".absent-x, .item-inner"},
            # attribute value containing a dot must NOT be mis-parsed as a `.5`
            # class token; .main-header is present so this is NOT flagged.
            {"id": "attrnoise", "target": '.main-header[data-ratio=".5"]'},
            # pseudo-class noise stripped; .item-inner present → NOT flagged.
            {"id": "pseudo", "target": ".item-inner:hover"},
            # Tailwind escaped selector — DOM stores `md:flex` unescaped; the
            # escaped `.md\:flex` token must unescape and match → NOT flagged.
            {"id": "tw", "target": r".md\:flex"},
            # `.swiperless` only matches the runtime allowlist as a substring —
            # the trailing boundary means it is NOT exempted, and it is absent → FLAGGED.
            {"id": "swiperless", "target": ".swiperless"},
            # attr-only / tag-only — not reliably checkable, must be skipped
            {"id": "counter", "target": "[data-value]"},
            {"id": "anchor", "target": "a"},
        ]
    }
    gate = Gate(ref)
    results = _check_spec_selectors_present_in_dom(gate, spec)
    assert len(results) == 1
    r = results[0]
    assert r.status == "fail", "absent same-page/subpage targets must BLOCK, not warn"
    assert r.label == "spec-selectors-present-in-dom"
    # remediation must offer both resolution paths (re-capture OR move to skipped[])
    assert "skipped[]" in r.message
    assert "re-capture" in r.message.lower()
    # the absent targets are named; present/runtime/attr/tag/list are not
    assert "page-hero" in r.message and "paragraph-1" in r.message
    assert "compound" in r.message, "present-ancestor + absent-leaf must be flagged"
    assert "swiperless" in r.message, "substring of an allowlist term must NOT be exempted"
    assert "item-inner" not in r.message
    assert "list" not in r.message, "comma-list with a present group must not flag"
    assert "attrnoise" not in r.message, "attr value dot must not be parsed as a class"
    assert "pseudo" not in r.message, "pseudo-class must be stripped before token check"
    assert "tw" not in r.message, "Tailwind escaped selector must unescape and match"
    assert ".swiper-wrapper" not in r.message and "[data-value]" not in r.message


def test_spec_selectors_present_in_dom_blocks_full_gate_spec(tmp_path: Path) -> None:
    """Integration: an absent spec target propagates a fail-severity result out
    of gate_spec() (the block promotion), while a spec whose targets are all
    present in the captured DOM produces no spec-selectors result at all."""

    def _seed(ref: Path, target: str) -> None:
        (ref / "bundle-map.json").write_text(json.dumps({"chunks": ["a.js"]}))
        bundles = ref / "bundles"
        bundles.mkdir()
        (bundles / "fixture.js").write_text("// fixture bundle")
        (ref / "external-sdks.json").write_text(json.dumps({"sdks": []}))
        (ref / "verification-plan.json").write_text(
            json.dumps({"schemaVersion": 1, "requiredChecks": []})
        )
        (ref / "structure.json").write_text(
            json.dumps(
                {
                    "tag": "body",
                    "children": [
                        {"tag": "div", "class": "hero-present"},
                    ],
                }
            )
        )
        (ref / "transition-spec.json").write_text(
            json.dumps(
                {
                    "transitions": [
                        {
                            "id": "reveal",
                            "trigger": "intersection",
                            "source_chunk": "fixture.js",
                            "bundle_branch": "main",
                            "target": target,
                            "animation": "opacity",
                            "reference_frames": ["frame_00.png"],
                        }
                    ]
                }
            )
        )
        verify = ref / "verify"
        verify.mkdir()
        for i in range(5):
            _write_png(verify / f"frame_{i:02d}.png")

    absent_ref = tmp_path / "absent"
    absent_ref.mkdir()
    _seed(absent_ref, ".subpage-only-leaf")
    absent = [r for r in Gate(absent_ref).gate_spec() if r.label == "spec-selectors-present-in-dom"]
    assert len(absent) == 1 and absent[0].status == "fail"

    present_ref = tmp_path / "present"
    present_ref.mkdir()
    _seed(present_ref, ".hero-present")
    present = [
        r for r in Gate(present_ref).gate_spec() if r.label == "spec-selectors-present-in-dom"
    ]
    assert present == [], "target present in captured DOM must not block"


def test_spec_selectors_present_in_dom_silent_without_structure(tmp_path: Path) -> None:
    """No structure.json (or empty capture) must never block — return no results."""
    from ui_clone.gates.spec import _check_spec_selectors_present_in_dom

    ref = tmp_path / "ref"
    ref.mkdir()
    spec = {"transitions": [{"id": "x", "target": ".whatever-absent"}]}
    gate = Gate(ref)
    assert _check_spec_selectors_present_in_dom(gate, spec) == []
