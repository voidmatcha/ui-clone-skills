import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from ui_clone.dag import GENERATION_PLAN_SOURCES, generation_plan_source_hashes
from ui_clone.gate import Gate

from ._helpers import (
    _write_pre_generate_baseline,
    _write_valid_artifact_provenance,
)


def test_gate_pre_generate_requires_hydrated_and_required_media_inventories(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    (ref / "runtime-media.json").unlink()
    (ref / "required-media.json").unlink()

    results = Gate(ref).gate_pre_generate()

    failures = {result.label for result in results if result.status == "fail"}
    assert "runtime-media.json (hydrated media inventory)" in failures
    assert "required-media.json (required media inventory)" in failures


def test_gate_pre_generate_accepts_empty_proven_media_inventories(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    (ref / "runtime-media.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "url": "https://example.com",
                "videos": [],
                "totals": {"video": 0},
                "sources": {
                    "extractor": "runtime-media.sh",
                    "scrollSamples": 5,
                },
            }
        ),
        encoding="utf-8",
    )
    (ref / "required-media.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "videos": [],
                "lottie": [],
                "totals": {"video": 0, "lottie": 0},
                "sources": {
                    "extractor": "required-media.sh",
                    "htmlSectionsScanned": 0,
                    "runtimeMediaScanned": True,
                    "bundlesScanned": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    results = Gate(ref).gate_pre_generate()

    statuses = {
        result.label: result.status
        for result in results
        if result.label
        in {
            "runtime-media.json (hydrated media inventory)",
            "runtime-media.json content validation",
            "runtime-media.json producer receipt",
            "required-media.json (required media inventory)",
            "required-media.json video inventory",
            "required-media.json Lottie inventory",
            "required-media.json producer receipt",
        }
    }
    assert statuses == {
        "runtime-media.json (hydrated media inventory)": "pass",
        "runtime-media.json content validation": "pass",
        "runtime-media.json producer receipt": "pass",
        "required-media.json (required media inventory)": "pass",
        "required-media.json video inventory": "pass",
        "required-media.json Lottie inventory": "pass",
        "required-media.json producer receipt": "pass",
    }


def test_gate_pre_generate_blocks_placeholder_regions_without_static_classification(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    (ref / "regions.json").write_text(
        json.dumps(
            {
                "placeholder": True,
                "detectionRan": False,
                "regions": [
                    {
                        "name": "full-page-placeholder",
                        "selector": "body",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    results = Gate(ref).gate_pre_generate()
    failures = [result for result in results if result.status == "fail"]
    blob = " ".join(f"{result.label} {result.message}" for result in failures).lower()

    assert any(result.label == "regions.json generation readiness" for result in failures)
    assert "placeholder" in blob


def test_gate_pre_generate_accepts_placeholder_with_typed_static_classification(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    (ref / "regions.json").write_text(
        json.dumps(
            {
                "placeholder": True,
                "detectionRan": False,
                "regions": [{"name": "full-page-placeholder", "selector": "body"}],
            }
        ),
        encoding="utf-8",
    )
    (ref / "motion-classification.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "pass",
                "classification": "static",
                "source": "browser-capture",
                "evidence": ["states/scroll/summary.json"],
            }
        ),
        encoding="utf-8",
    )
    summary = ref / "states" / "scroll"
    summary.mkdir(parents=True)
    (summary / "summary.json").write_text(
        json.dumps(
            {
                "checked": True,
                "static": True,
                "stops": [{"pct": 0, "scrollY": 0}],
                "scrollHeight": 900,
                "viewportHeight": 900,
            }
        ),
        encoding="utf-8",
    )

    results = Gate(ref).gate_pre_generate()
    readiness = [
        result for result in results if result.label == "regions.json generation readiness"
    ]

    assert [result.status for result in readiness] == ["pass"]


def test_gate_pre_generate_rejects_static_classification_missing_evidence(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    (ref / "regions.json").write_text(
        json.dumps({"placeholder": True, "detectionRan": False, "regions": []}),
        encoding="utf-8",
    )
    (ref / "motion-classification.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "pass",
                "classification": "static",
                "source": "browser-capture",
                "evidence": ["states/scroll/summary.json"],
            }
        ),
        encoding="utf-8",
    )

    results = Gate(ref).gate_pre_generate()
    failures = [result for result in results if result.status == "fail"]

    assert any(result.label == "regions.json generation readiness" for result in failures)


def test_gate_pre_generate_rejects_static_classification_status_pass_only_evidence(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    (ref / "regions.json").write_text(
        json.dumps({"placeholder": True, "detectionRan": False, "regions": []}),
        encoding="utf-8",
    )
    (ref / "motion-classification.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "pass",
                "classification": "static",
                "source": "browser-capture",
                "evidence": ["some-other-pass.json"],
            }
        ),
        encoding="utf-8",
    )
    (ref / "some-other-pass.json").write_text(
        json.dumps({"status": "pass"}),
        encoding="utf-8",
    )

    results = Gate(ref).gate_pre_generate()
    failures = [result for result in results if result.status == "fail"]

    assert any(result.label == "regions.json generation readiness" for result in failures)


def test_gate_pre_generate_rejects_empty_static_classification_evidence(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    (ref / "regions.json").write_text(
        json.dumps({"placeholder": True, "detectionRan": False, "regions": []}),
        encoding="utf-8",
    )
    (ref / "motion-classification.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "pass",
                "classification": "static",
                "source": "browser-capture",
                "evidence": ["empty.json"],
            }
        ),
        encoding="utf-8",
    )
    (ref / "empty.json").write_text("{}", encoding="utf-8")

    results = Gate(ref).gate_pre_generate()
    failures = [result for result in results if result.status == "fail"]

    assert any(result.label == "regions.json generation readiness" for result in failures)


def test_gate_pre_generate_rejects_static_classification_forged_evidence(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    (ref / "regions.json").write_text(
        json.dumps({"placeholder": True, "detectionRan": False, "regions": []}),
        encoding="utf-8",
    )
    (ref / "motion-classification.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "pass",
                "classification": "static",
                "source": "browser-capture",
                "evidence": ["../outside.json", "states/scroll/summary.json"],
            }
        ),
        encoding="utf-8",
    )
    summary = ref / "states" / "scroll"
    summary.mkdir(parents=True)
    (summary / "summary.json").write_text(
        json.dumps({"checked": True, "static": True}),
        encoding="utf-8",
    )

    results = Gate(ref).gate_pre_generate()
    failures = [result for result in results if result.status == "fail"]

    assert any(result.label == "regions.json generation readiness" for result in failures)


def test_gate_pre_generate_rejects_static_classification_contradicted_by_motion(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    (ref / "regions.json").write_text(
        json.dumps({"placeholder": True, "detectionRan": False, "regions": []}),
        encoding="utf-8",
    )
    (ref / "motion-classification.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "pass",
                "classification": "static",
                "source": "browser-capture",
                "evidence": ["states/scroll/summary.json"],
            }
        ),
        encoding="utf-8",
    )
    summary = ref / "states" / "scroll"
    summary.mkdir(parents=True)
    (summary / "summary.json").write_text(
        json.dumps({"checked": True, "static": True}),
        encoding="utf-8",
    )
    (ref / "verification-plan.json").write_text(
        json.dumps({"signals": {"hasHover": True}}),
        encoding="utf-8",
    )
    (ref / "hover-css-rules.json").write_text(
        json.dumps({"rules": [{"selector": ".card:hover", "properties": ["transform"]}]}),
        encoding="utf-8",
    )

    results = Gate(ref).gate_pre_generate()
    failures = [result for result in results if result.status == "fail"]
    blob = " ".join(f"{result.label} {result.message}" for result in failures).lower()

    assert any(result.label == "regions.json generation readiness" for result in failures)
    assert "motion" in blob or "contradict" in blob


def test_gate_pre_generate_blocks_partial_live_capture_regions(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    (ref / "regions.json").write_text(
        json.dumps(
            {
                "placeholder": False,
                "detectionRan": True,
                "source": "scripts/extract/capture-region-artifacts.py",
                "liveCaptureBacked": True,
                "derivedFrom": ["capture-region-artifacts-summary.json"],
                "regions": [
                    {
                        "name": "button",
                        "triggerType": "hover",
                        "selector": ".button",
                        "artifacts": {
                            "idle": "clip/ref/00-button-idle.png",
                            "active": "clip/ref/00-button-active.png",
                        },
                    },
                    {
                        "name": "signed-in-menu",
                        "triggerType": "hover",
                        "selector": ".signed-in-menu",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "capture-region-artifacts-summary.json").write_text(
        json.dumps(
            {
                "status": "fail",
                "counts": {"captured": 1, "skipped": 1},
                "captured": [
                    {
                        "region": "button",
                        "triggerType": "hover",
                        "selector": ".button",
                        "artifacts": {
                            "idle": "clip/ref/00-button-idle.png",
                            "active": "clip/ref/00-button-active.png",
                        },
                        "observation": {"changedProperties": ["transform"]},
                    }
                ],
                "skipped": [
                    {
                        "region": "signed-in-menu",
                        "triggerType": "hover",
                        "selector": ".signed-in-menu",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    results = Gate(ref).gate_pre_generate()
    failures = [result for result in results if result.status == "fail"]
    blob = " ".join(f"{result.label} {result.message}" for result in failures).lower()

    assert any(result.label == "regions.json generation readiness" for result in failures)
    assert "live-capture" in blob or "partial" in blob


def test_gate_pre_generate_rejects_media_inventories_without_producer_receipts(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    (ref / "runtime-media.json").write_text(
        json.dumps({"schemaVersion": 1, "videos": []}),
        encoding="utf-8",
    )
    (ref / "required-media.json").write_text(
        json.dumps({"schemaVersion": 1, "videos": [], "lottie": []}),
        encoding="utf-8",
    )

    results = Gate(ref).gate_pre_generate()

    failures = {result.label for result in results if result.status == "fail"}
    assert "runtime-media.json producer receipt" in failures
    assert "required-media.json producer receipt" in failures


def test_gate_pre_generate_blocks_when_footer_missing_from_component_map(tmp_path: Path) -> None:
    """section-map has hasFooter=True but component-map has no footer entry → fail."""
    ref = tmp_path / "ref"
    ref.mkdir()

    # Minimal artifacts required by gate_pre_generate
    (ref / "extracted.json").write_text(json.dumps({"sections": [], "url": "https://example.com"}))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "fixture-reveal-on-scroll",
            "trigger": "intersection",
            "source_chunk": "fixture.js",
            "bundle_branch": "main",
            "target": ".fixture",
            "animation": "opacity-translateY",
            "reference_frames": ["frame_00.png"],
        }]
    }))
    (ref / "animation-init-styles.json").write_text(json.dumps({}))
    (ref / "svg-text-elements.json").write_text(json.dumps([]))
    responsive = ref / "responsive"
    responsive.mkdir()
    (responsive / "sizing-expressions.json").write_text(json.dumps({}))
    (ref / "interactions-detected.json").write_text(
        json.dumps({"interactions": [], "hasPreloader": False})
    )
    (ref / "hover-css-rules.json").write_text(json.dumps([]))
    (ref / "transition-coverage.json").write_text(
        json.dumps({"animatedElements": [], "staticElements": []})
    )
    (ref / "element-roles.json").write_text(json.dumps({}))
    (ref / "element-groups.json").write_text(json.dumps({}))
    (ref / "layout-decisions.json").write_text(json.dumps({}))

    # section-map has a <footer>
    (ref / "section-map.json").write_text(
        json.dumps(
            {
                "sections": [{"tag": "main"}],
                "totalCount": 1,
                "hasFooter": True,
            }
        )
    )
    # component-map has NO footer entry
    (ref / "component-map.json").write_text(
        json.dumps(
            {
                "sections": [{"componentName": "HeroSection", "sourceTag": "main"}],
                "sectionCount": 1,
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_pre_generate()
    footer_failures = [r for r in results if r.status == "fail" and "footer" in r.message.lower()]
    assert footer_failures, "Missing footer in component-map must produce a fail result"



def test_gate_pre_generate_fails_when_hover_timing_unknown(tmp_path: Path) -> None:
    """interactions with timingSource='unknown' must cause gate failure."""
    ref = tmp_path / "ref"
    ref.mkdir()

    (ref / "extracted.json").write_text(json.dumps({"sections": [], "url": "https://example.com"}))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "fixture-reveal-on-scroll",
            "trigger": "intersection",
            "source_chunk": "fixture.js",
            "bundle_branch": "main",
            "target": ".fixture",
            "animation": "opacity-translateY",
            "reference_frames": ["frame_00.png"],
        }]
    }))
    (ref / "animation-init-styles.json").write_text(json.dumps({}))
    (ref / "svg-text-elements.json").write_text(json.dumps([]))
    responsive = ref / "responsive"
    responsive.mkdir()
    (responsive / "sizing-expressions.json").write_text(json.dumps({}))
    (ref / "hover-css-rules.json").write_text(json.dumps([]))
    (ref / "transition-coverage.json").write_text(
        json.dumps({"animatedElements": [], "staticElements": []})
    )
    (ref / "element-roles.json").write_text(json.dumps({}))
    (ref / "element-groups.json").write_text(json.dumps({}))
    (ref / "layout-decisions.json").write_text(json.dumps({}))
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [], "totalCount": 0, "hasFooter": False})
    )
    (ref / "component-map.json").write_text(json.dumps({"sections": [], "sectionCount": 0}))
    # interactions with timingSource='unknown'
    (ref / "interactions-detected.json").write_text(
        json.dumps(
            {
                "interactions": [
                    {"trigger": "hover", "timingSource": "unknown", "selector": ".btn"},
                ],
                "hasPreloader": False,
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_pre_generate()
    timing_failures = [r for r in results if r.status == "fail" and "unknown" in r.message.lower()]
    assert timing_failures, "timingSource='unknown' must produce a fail result"



def test_gate_pre_generate_blocks_without_artifact_provenance(tmp_path: Path) -> None:
    """Pre-generation must fail when extraction artifacts have no evidence trail."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)

    gate = Gate(ref)
    results = gate.gate_pre_generate()

    assert any(
        r.status == "fail" and "artifact-provenance.json" in r.message
        for r in results
    ), "Missing artifact provenance must block pre-generation"



def test_gate_pre_generate_blocks_manual_artifact_provenance(tmp_path: Path) -> None:
    """Critical artifacts cannot be declared as hand-written/manual evidence."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)
    provenance = json.loads((ref / "artifact-provenance.json").read_text())
    provenance["artifacts"][0]["source"] = "manual"
    (ref / "artifact-provenance.json").write_text(json.dumps(provenance))

    gate = Gate(ref)
    results = gate.gate_pre_generate()

    assert any(
        r.status == "fail" and "manual" in r.message.lower()
        for r in results
    ), "Manual provenance for critical extraction artifacts must block pre-generation"



def test_gate_pre_generate_accepts_evidence_backed_artifact_provenance(tmp_path: Path) -> None:
    """Valid provenance should not add failures to an otherwise complete extraction."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)

    gate = Gate(ref)
    results = gate.gate_pre_generate()

    provenance_failures = [
        r for r in results
        if r.status == "fail" and "provenance" in r.message.lower()
    ]
    assert not provenance_failures


def test_audit_cross_refs_accept_canonical_ids_for_sections_without_dom_ids(
    tmp_path: Path,
) -> None:
    """Generated audit IDs must agree with id-less section-map entries."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "section-map.json").write_text(
        json.dumps(
            {
                "sections": [
                    {
                        "index": 0,
                        "tag": "header",
                        "id": None,
                        "className": "site-header sticky",
                    },
                    {
                        "index": 1,
                        "tag": "main",
                        "id": "main-content",
                        "className": "main",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    for filename, payload in {
        "element-roles.json": {"roles": []},
        "element-groups.json": {"groups": []},
        "component-map.json": {
            "components": [
                {"sectionId": "section-0-site-header-sticky"},
                {"sectionId": "main-content"},
            ]
        },
        "layout-decisions.json": {
            "decisions": [
                {"sectionId": "section-0-site-header-sticky"},
                {"sectionId": "main-content"},
            ]
        },
    }.items():
        (ref / filename).write_text(json.dumps(payload), encoding="utf-8")

    results = Gate(ref)._check_audit_artifacts()

    assert not [
        result
        for result in results
        if result.status == "warn" and "sectionIds not in section-map.json" in result.message
    ]


def test_gate_pre_generate_refreshes_stale_extracted_after_late_hover_capture(
    tmp_path: Path,
) -> None:
    """Late hover/state capture should refresh extracted.json without bypassing provenance."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)
    (ref / "generation-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "componentList": []}),
        encoding="utf-8",
    )
    # Only extractor-owned handoffs are auto-refreshable. This mirrors the
    # deterministic pipeline output and keeps hand-written extracted.json from
    # being silently overwritten.
    (ref / "extracted.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "source": "ui_clone.extraction_artifacts",
                "sections": [],
                "url": "https://example.com",
            }
        ),
        encoding="utf-8",
    )
    older = time.time() - 10
    newer = time.time()
    os.utime(ref / "extracted.json", (older, older))
    os.utime(ref / "hover-css-rules.json", (newer, newer))

    results = Gate(ref).gate_pre_generate()

    assert (ref / "extracted.json").stat().st_mtime >= newer
    assert not [
        r for r in results
        if r.status == "fail" and "extracted.json — STALE" in r.message
    ]


_HOVER_ONLY_SPEC = {
    "transitions": [
        {"id": "btn-hover", "trigger": "hover", "type": "color", "target": ".btn"}
    ]
}


def test_scroll_spec_coverage_fails_on_gsap_scrolltrigger_bundle(tmp_path: Path) -> None:
    """sticky-elements.json (top-level list) + a JS bundle that constructs a GSAP
    ScrollTrigger with pin/scrub, while scroll-engine.json is EMPTY and
    transition-spec has only a hover entry → gate must FAIL.

    Before the fix, scroll-engine.json is the only scroll-motion signal source,
    so an empty one makes the gate pass (the GSAP bundle evidence is unseen).
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    # sticky-elements.json is a TOP-LEVEL LIST (list-handling must survive).
    (ref / "sticky-elements.json").write_text(
        json.dumps([{"className": "hero-pin", "tag": "section"}])
    )
    # scroll-engine.json is EMPTY — the framer-motion/IO path finds nothing.
    (ref / "scroll-engine.json").write_text(json.dumps({}))
    # The GSAP scroll evidence lives in a JS bundle, not scroll-engine.json.
    bundles = ref / "bundles"
    bundles.mkdir()
    (bundles / "app.bundle.js").write_text(
        "ScrollTrigger.create({ trigger: '.hero', pin:true, scrub:true });"
    )

    gate = Gate(ref)
    results = gate._check_scroll_spec_coverage(_HOVER_ONLY_SPEC)
    assert any(
        r.status == "fail" and r.label == "scroll-spec-coverage" for r in results
    ), "GSAP ScrollTrigger bundle + sticky + no scroll spec entry must FAIL"


def test_scroll_spec_coverage_handles_legacy_list_detected(tmp_path: Path) -> None:
    """Legacy scroll-engine.json sometimes wrote detected as a top-level list.
    That shape must not crash before the gate can inspect sibling evidence.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "sticky-elements.json").write_text(
        json.dumps([{"className": "legacy-pin", "tag": "section"}])
    )
    (ref / "scroll-engine.json").write_text(
        json.dumps({"detected": ["gsap", "ScrollTrigger"]})
    )
    bundles = ref / "bundles"
    bundles.mkdir()
    (bundles / "app.bundle.js").write_text(
        "ScrollTrigger.create({ trigger: '.legacy', pin:true, scrub:true });"
    )

    results = Gate(ref)._check_scroll_spec_coverage(_HOVER_ONLY_SPEC)

    assert any(
        r.status == "fail" and r.label == "scroll-spec-coverage" for r in results
    ), "Legacy list-shaped detected must not hide sibling scroll-motion evidence"


def test_scroll_spec_coverage_passes_when_scroll_spec_entry_present(tmp_path: Path) -> None:
    """Same sticky + GSAP-bundle evidence, but transition-spec HAS a scroll-trigger
    entry → pass (motion will be verified). No regression to the pass path."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "sticky-elements.json").write_text(
        json.dumps([{"className": "hero-pin", "tag": "section"}])
    )
    (ref / "scroll-engine.json").write_text(json.dumps({}))
    bundles = ref / "bundles"
    bundles.mkdir()
    (bundles / "app.bundle.js").write_text(
        "ScrollTrigger.create({ trigger: '.hero', pin:true, scrub:true });"
    )
    spec = {
        "transitions": [
            {"id": "hero-pin", "trigger": "scroll", "mechanism": "scroll-scrub",
             "target": ".hero"}
        ]
    }

    gate = Gate(ref)
    results = gate._check_scroll_spec_coverage(spec)
    assert not any(r.status == "fail" for r in results), (
        "A scroll-trigger spec entry must satisfy the gate"
    )


def test_scroll_spec_coverage_fails_on_nonsticky_observed_parallax(tmp_path: Path) -> None:
    """Non-sticky observed motion: NO sticky-elements.json, but element-tracking.json
    shows an element whose transform changes across scroll positions (parallax),
    while transition-spec has only a hover entry → gate must FAIL.

    Before the fix, the sticky-ONLY precondition (`if not sticky: return []`)
    skips the check entirely for non-sticky parallax/reveal motion, silently
    dropping it from verification.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    # No sticky-elements.json at all.
    (ref / "scroll-engine.json").write_text(json.dumps({}))
    (ref / "element-tracking.json").write_text(json.dumps([
        {"scrollY": 0, "scrollPct": 0, "elements": [
            {"selector": "div.parallax", "inViewport": True, "top": 100,
             "transform": "matrix(1, 0, 0, 1, 0, 0)", "opacity": None,
             "scale": None, "clipPath": None, "position": None},
        ]},
        {"scrollY": 2000, "scrollPct": 50, "elements": [
            {"selector": "div.parallax", "inViewport": True, "top": -300,
             "transform": "matrix(1, 0, 0, 1, 0, -200)", "opacity": None,
             "scale": None, "clipPath": None, "position": None},
        ]},
    ]))

    gate = Gate(ref)
    results = gate._check_scroll_spec_coverage(_HOVER_ONLY_SPEC)
    assert any(
        r.status == "fail" and r.label == "scroll-spec-coverage" for r in results
    ), "Non-sticky observed parallax + no scroll spec entry must FAIL"


def test_scroll_spec_coverage_fails_on_nonsticky_transition_coverage_scroll(tmp_path: Path) -> None:
    """Non-sticky scroll-classified animatedElement in transition-coverage.json,
    no sticky-elements.json, hover-only spec → gate must FAIL."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "scroll-engine.json").write_text(json.dumps({}))
    (ref / "transition-coverage.json").write_text(json.dumps({
        "animatedElements": [
            {"selector": "div.reveal", "trigger": "scroll-driven",
             "decoded": {"position": "relative"}}
        ],
        "staticElements": [],
    }))

    gate = Gate(ref)
    results = gate._check_scroll_spec_coverage(_HOVER_ONLY_SPEC)
    assert any(
        r.status == "fail" and r.label == "scroll-spec-coverage" for r in results
    ), "Non-sticky scroll-classified element + no scroll spec entry must FAIL"


def test_scroll_spec_coverage_passes_fully_static_no_sticky_no_motion(tmp_path: Path) -> None:
    """No-false-dispatch guard: no sticky, no observed motion (element-tracking
    shows no cross-position change, transition-coverage has no animated elements),
    no allowlist token → gate must PASS (return no rows / no fail)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "scroll-engine.json").write_text(json.dumps({}))
    (ref / "transition-coverage.json").write_text(json.dumps({
        "animatedElements": [], "staticElements": [{"selector": "div.static"}],
    }))
    (ref / "element-tracking.json").write_text(json.dumps([
        {"scrollY": 0, "scrollPct": 0, "elements": [
            {"selector": "div.static", "inViewport": True, "top": 100,
             "transform": None, "opacity": None, "scale": None,
             "clipPath": None, "position": None},
        ]},
        {"scrollY": 2000, "scrollPct": 50, "elements": [
            {"selector": "div.static", "inViewport": True, "top": 100,
             "transform": None, "opacity": None, "scale": None,
             "clipPath": None, "position": None},
        ]},
    ]))

    gate = Gate(ref)
    results = gate._check_scroll_spec_coverage(_HOVER_ONLY_SPEC)
    assert not any(r.status == "fail" for r in results), (
        "Fully static page (no sticky, no observed motion) must not fail"
    )


def test_scroll_spec_coverage_passes_static_sticky_no_motion_evidence(tmp_path: Path) -> None:
    """Plain sticky layout with NO scroll-motion evidence anywhere (empty
    scroll-engine, no bundles, no sdk/plan) → must PASS. Don't fail static sticky."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "sticky-elements.json").write_text(
        json.dumps([{"className": "sticky-header", "tag": "header"}])
    )
    (ref / "scroll-engine.json").write_text(json.dumps({}))

    gate = Gate(ref)
    results = gate._check_scroll_spec_coverage(_HOVER_ONLY_SPEC)
    assert not any(r.status == "fail" for r in results), (
        "Static sticky with no scroll-motion evidence must not fail"
    )

def _write_generation_plan(ref: Path) -> None:
    source_hashes = generation_plan_source_hashes(ref)
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "componentList": [],
                "dsComponentsRequired": [],
                "tokens": {
                    "colors": {},
                    "spacing": {},
                    "typography": {},
                    "radius": {},
                    "shadows": {},
                },
                "guidance": {},
                "provenance": {
                    "source": "generation-planner",
                    "generatedAt": "2026-07-29T00:00:00Z",
                    "hashAlgorithm": "sha256",
                    "sourceHashes": source_hashes,
                },
            }
        ),
        encoding="utf-8",
    )


def test_gate_pre_generate_accepts_current_generation_plan_provenance(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)
    _write_generation_plan(ref)

    results = Gate(ref).gate_pre_generate()

    assert not [
        result
        for result in results
        if result.status == "fail"
        and result.label == "generation-plan provenance"
    ]


def test_generation_plan_script_emits_canonical_source_hashes(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "section-map.json").write_text(
        json.dumps({"sections": []}),
        encoding="utf-8",
    )
    (ref / "bundle-extraction.json").write_text(
        json.dumps({"animations": []}),
        encoding="utf-8",
    )
    css = ref / "css"
    css.mkdir()
    (css / "site.css").write_text(".hero { color: #111; }\n", encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "extract" / "generation-plan.sh"), str(ref)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    plan = json.loads((ref / "generation-plan.json").read_text(encoding="utf-8"))
    assert plan["provenance"]["sourceHashes"] == generation_plan_source_hashes(ref)


def test_gate_pre_generate_rejects_forged_generation_plan_provenance(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)
    _write_generation_plan(ref)
    plan = json.loads((ref / "generation-plan.json").read_text(encoding="utf-8"))
    plan["provenance"]["source"] = "manual"
    plan["provenance"]["sourceHashes"]["section-map.json"] = "0" * 64
    (ref / "generation-plan.json").write_text(
        json.dumps(plan),
        encoding="utf-8",
    )

    results = Gate(ref).gate_pre_generate()

    failures = [
        result
        for result in results
        if result.status == "fail"
        and result.label == "generation-plan provenance"
    ]
    assert failures
    assert "generation-planner" in failures[0].message
    assert "section-map.json" in failures[0].message


@pytest.mark.parametrize(
    "relative_path",
    tuple(path for path in GENERATION_PLAN_SOURCES if "*" not in path),
)
def test_gate_pre_generate_rejects_stale_generation_plan_sources(
    tmp_path: Path,
    relative_path: str,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)
    (ref / "styles.json").write_text(
        json.dumps({"computed": {".hero": {"color": "#111"}}}),
        encoding="utf-8",
    )
    (ref / "css").mkdir()
    (ref / "css" / "variables.txt").write_text(
        "--color-text: #111;\n",
        encoding="utf-8",
    )
    (ref / "signature-effects-candidates.json").write_text(
        json.dumps({"candidates": []}),
        encoding="utf-8",
    )
    source_path = ref / relative_path
    source_path.parent.mkdir(parents=True, exist_ok=True)
    if not source_path.exists():
        source_path.write_text("{}\n", encoding="utf-8")
    _write_generation_plan(ref)

    source_path.write_text(
        source_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    results = Gate(ref).gate_pre_generate()

    failures = [
        result
        for result in results
        if result.status == "fail"
        and result.label == "generation-plan provenance"
    ]
    assert failures
    assert relative_path in failures[0].message
    assert failures[0].stale


def test_gate_pre_generate_rejects_stale_generation_plan_css_manifest(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)
    css_dir = ref / "css"
    css_dir.mkdir()
    (css_dir / "variables.txt").write_text(
        "--color-text: #111;\n",
        encoding="utf-8",
    )
    stylesheet = css_dir / "site.css"
    stylesheet.write_text(".hero { color: #111; }\n", encoding="utf-8")
    _write_generation_plan(ref)

    stylesheet.write_text(".hero { color: #222; }\n", encoding="utf-8")

    failures = [
        result
        for result in Gate(ref).gate_pre_generate()
        if result.status == "fail"
        and result.label == "generation-plan provenance"
    ]
    assert failures
    assert "css/*.css" in failures[0].message
    assert failures[0].stale


def test_gate_pre_generate_blocks_unenriched_generation_plan(tmp_path: Path) -> None:
    """The deterministic schema-v1 base is not implementation-ready."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)
    (ref / "generation-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "componentList": []}),
        encoding="utf-8",
    )

    results = Gate(ref).gate_pre_generate()

    failures = [
        result
        for result in results
        if result.status == "fail" and result.label == "generation-plan schema"
    ]
    assert failures
    assert "generation-planner" in failures[0].message


def test_gate_pre_generate_rejects_schema_bump_without_enrichment(
    tmp_path: Path,
) -> None:
    """Changing only schemaVersion must not impersonate planner enrichment."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)
    (ref / "section-map.json").write_text(
        json.dumps(
            {
                "sections": [{"id": "hero", "tag": "section", "className": "hero"}],
                "totalCount": 1,
                "hasFooter": False,
            }
        ),
        encoding="utf-8",
    )
    (ref / "component-map.json").write_text(
        json.dumps(
            {
                "sections": [{"componentName": "Hero", "sourceTag": "section"}],
                "sectionCount": 1,
            }
        ),
        encoding="utf-8",
    )
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "componentList": [],
                "dsComponentsRequired": [],
                "tokens": {
                    "colors": {},
                    "spacing": {},
                    "typography": {},
                    "radius": {},
                    "shadows": {},
                },
            }
        ),
        encoding="utf-8",
    )

    results = Gate(ref).gate_pre_generate()

    failures = [
        result
        for result in results
        if result.status == "fail"
        and result.label == "generation-plan enrichment"
    ]
    assert failures
    assert "componentList" in failures[0].message


def test_gate_pre_generate_rejects_duplicate_component_identity_rows(
    tmp_path: Path,
) -> None:
    """Anonymous or duplicate rows cannot impersonate captured-section coverage."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)
    sections = [
        {"id": "hero", "tag": "section", "className": "hero"},
        {"id": "about", "tag": "section", "className": "about"},
    ]
    (ref / "section-map.json").write_text(
        json.dumps({"sections": sections, "totalCount": 2, "hasFooter": False}),
        encoding="utf-8",
    )
    duplicate = {
        "name": "Hero",
        "matchedSection": "hero",
        "selector": "section.hero",
        "path": "components/sections/Hero.tsx",
        "wires": [],
    }
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "componentList": [duplicate, duplicate],
                "dsComponentsRequired": [],
                "tokens": {
                    "colors": {},
                    "spacing": {},
                    "typography": {},
                    "radius": {},
                    "shadows": {},
                },
            }
        ),
        encoding="utf-8",
    )

    results = Gate(ref).gate_pre_generate()

    failures = [
        result
        for result in results
        if result.status == "fail"
        and result.label == "generation-plan enrichment"
    ]
    assert failures
    assert "about" in failures[0].message


def test_gate_pre_generate_rejects_duplicate_component_output_paths(
    tmp_path: Path,
) -> None:
    """Distinct sections cannot overwrite one shared generated component file."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)
    sections = [
        {"id": "hero", "tag": "section", "className": "hero"},
        {"id": "about", "tag": "section", "className": "about"},
    ]
    (ref / "section-map.json").write_text(
        json.dumps({"sections": sections, "totalCount": 2, "hasFooter": False}),
        encoding="utf-8",
    )
    components = [
        {
            "name": name,
            "matchedSection": section_id,
            "selector": f"section.{section_id}",
            "path": f"components/sections/{name}.tsx",
            "wires": [],
        }
        for section_id, name in (("hero", "Shared"), ("about", "shared"))
    ]
    (ref / "generation-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "componentList": components,
                "dsComponentsRequired": [],
                "tokens": {
                    "colors": {},
                    "spacing": {},
                    "typography": {},
                    "radius": {},
                    "shadows": {},
                },
            }
        ),
        encoding="utf-8",
    )

    results = Gate(ref).gate_pre_generate()

    failures = [
        result
        for result in results
        if result.status == "fail"
        and result.label == "generation-plan enrichment"
    ]
    assert failures
    assert "duplicate" in failures[0].message
    assert "Shared.tsx" in failures[0].message


def test_gate_pre_generate_blocks_motion_rich_missing_state_captures(
    tmp_path: Path,
) -> None:
    """Motion-rich refs must complete Phase A/B/C captures before
    implementation starts, not after a local build hits state-coverage."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(
        json.dumps({"libraries": ["gsap", "ScrollTrigger"]}),
        encoding="utf-8",
    )
    _write_pre_generate_baseline(ref)
    _write_generation_plan(ref)
    _write_valid_artifact_provenance(ref)

    results = Gate(ref).gate_pre_generate()

    failures = [
        r for r in results
        if r.status == "fail" and r.label == "state-capture prerequisites"
    ]
    assert failures, (
        "pre-generate must fail before implementation when motion-rich "
        "state capture artifacts are missing"
    )
    assert "capture-states.sh" in failures[0].fix
    assert "states/splash/summary.json" in failures[0].message


def test_gate_pre_generate_accepts_motion_rich_complete_state_captures(
    tmp_path: Path,
) -> None:
    """The early blocker clears once the shared required state artifacts
    exist; impl-hook checks remain owned by state-coverage."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(
        json.dumps({"libraries": ["gsap", "ScrollTrigger"]}),
        encoding="utf-8",
    )
    _write_pre_generate_baseline(ref)
    _write_generation_plan(ref)
    _write_valid_artifact_provenance(ref)

    splash = ref / "states" / "splash"
    splash.mkdir(parents=True)
    (splash / "summary.json").write_text(
        json.dumps({"checked": True, "polls": 2}),
        encoding="utf-8",
    )
    (splash / "trajectory.json").write_text(
        json.dumps([
            {"ts_ms": 0, "bodyClass": "is-loading", "htmlClass": ""},
            {"ts_ms": 800, "bodyClass": "is-loaded", "htmlClass": ""},
        ]),
        encoding="utf-8",
    )
    scroll = ref / "states" / "scroll"
    scroll.mkdir()
    (scroll / "summary.json").write_text(
        json.dumps({"static": False}),
        encoding="utf-8",
    )
    hover = ref / "states" / "hover"
    hover.mkdir()
    (hover / "manifest.json").write_text(
        json.dumps({"targets": []}),
        encoding="utf-8",
    )

    results = Gate(ref).gate_pre_generate()

    assert not [
        r for r in results
        if r.status == "fail" and r.label == "state-capture prerequisites"
    ]


# --- Responsive sizing sentinel content validation (design fix #5) ----------

_SIZING_LABEL = "sizing-expressions.json content validation"


def _write_sentinel_sizing(ref: Path) -> None:
    """Overwrite the baseline sizing artifact with the deterministic finalizer's
    single-viewport sentinel shape (sentinel:true, empty expressions)."""
    (ref / "responsive" / "sizing-expressions.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "source": "ui_clone.extraction_artifacts",
                "observation": "single-viewport-sizing-summary",
                "sentinel": True,
                "expressions": [],
                "root": {"width": "1440px"},
            }
        )
    )


def _write_media_css(ref: Path) -> None:
    css = ref / "css"
    css.mkdir(exist_ok=True)
    (css / "main.css").write_text(
        ".hero { width: 100%; }\n"
        "@media (max-width: 768px) { .hero { width: 50%; } }\n"
    )


def test_gate_pre_generate_rejects_responsive_sentinel_with_media_signals(
    tmp_path: Path,
) -> None:
    """The single-viewport sentinel must NOT satisfy Step 4-C2 when the ref CSS
    carries @media rules — the real multi-viewport sweep still owes expressions."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)
    _write_sentinel_sizing(ref)
    _write_media_css(ref)

    results = Gate(ref).gate_pre_generate()

    sizing_fails = [
        r for r in results if r.status == "fail" and r.label == _SIZING_LABEL
    ]
    assert sizing_fails, "sentinel + @media must fail content validation"
    assert "Step 4-C2" in sizing_fails[0].fix


def test_gate_pre_generate_rejects_responsive_sentinel_with_vw_signals(
    tmp_path: Path,
) -> None:
    """vw units in the ref CSS are a responsive signal that a settled-only
    sentinel cannot satisfy."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)
    _write_sentinel_sizing(ref)
    css = ref / "css"
    css.mkdir(exist_ok=True)
    (css / "main.css").write_text(".hero { width: 80vw; }\n")

    results = Gate(ref).gate_pre_generate()

    assert any(
        r.status == "fail" and r.label == _SIZING_LABEL for r in results
    ), "sentinel + vw units must fail content validation"


def test_gate_pre_generate_accepts_responsive_sentinel_without_signals(
    tmp_path: Path,
) -> None:
    """A genuinely single-viewport ref (no @media / vw / breakpoints) may keep
    the sentinel — the content check must not fire."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)
    _write_sentinel_sizing(ref)
    css = ref / "css"
    css.mkdir(exist_ok=True)
    (css / "main.css").write_text(".hero { width: 1440px; }\n")

    results = Gate(ref).gate_pre_generate()

    assert not [
        r for r in results if r.status == "fail" and r.label == _SIZING_LABEL
    ], "sentinel with no responsive signals must not fail content validation"


def test_gate_pre_generate_accepts_real_sizing_sweep_with_signals(
    tmp_path: Path,
) -> None:
    """A real Step 4-C2 sweep (selector-keyed expression map) passes even when
    the ref is responsive — only the unfilled sentinel is rejected."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)
    _write_media_css(ref)
    (ref / "responsive" / "sizing-expressions.json").write_text(
        json.dumps(
            {
                ".hero": {
                    "width": {"768": 384, "1280": 1216, "1440": 1376},
                    "expression": "calc(100vw - 64px)",
                }
            }
        )
    )

    results = Gate(ref).gate_pre_generate()

    assert not [
        r for r in results if r.status == "fail" and r.label == _SIZING_LABEL
    ], "a real multi-viewport sweep must satisfy the responsive gate"


def test_responsive_sweep_remediation_helper_matrix(tmp_path: Path) -> None:
    """Direct coverage of the helper the gate and status driver both consume."""
    from ui_clone.extraction_artifacts import responsive_sweep_remediation

    def _mk(name: str) -> Path:
        ref = tmp_path / name
        (ref / "responsive").mkdir(parents=True)
        return ref

    # sentinel + @media → remediation
    ref = _mk("media")
    _write_sentinel_sizing(ref)
    _write_media_css(ref)
    assert responsive_sweep_remediation(ref) is not None

    # sentinel + detected-breakpoints → remediation (no CSS on disk)
    ref = _mk("breakpoints")
    _write_sentinel_sizing(ref)
    (ref / "detected-breakpoints.json").write_text(
        json.dumps({"breakpoints": ["768px"], "summary": {"count": 1}})
    )
    assert responsive_sweep_remediation(ref) is not None

    # empty-expressions sentinel shape (no explicit sentinel flag) + vw
    ref = _mk("empty-expr")
    (ref / "responsive" / "sizing-expressions.json").write_text(
        json.dumps({"expressions": []})
    )
    (ref / "css").mkdir()
    (ref / "css" / "a.css").write_text(".x { height: 50vw; }")
    assert responsive_sweep_remediation(ref) is not None

    # sentinel + no signals → None
    ref = _mk("no-signals")
    _write_sentinel_sizing(ref)
    (ref / "css").mkdir()
    (ref / "css" / "a.css").write_text(".x { width: 100px; }")
    assert responsive_sweep_remediation(ref) is None

    # real selector-keyed sweep + signals → None
    ref = _mk("real")
    _write_media_css(ref)
    (ref / "responsive" / "sizing-expressions.json").write_text(
        json.dumps({".hero": {"width": {"768": 384, "1440": 1376}}})
    )
    assert responsive_sweep_remediation(ref) is None


# --- verification-plan amend ordering-hole backstop (design fix #9) ---------

_SIG_EFFECTS_GENPLAN = {
    "schemaVersion": 2,
    "componentList": [],
    "signatureEffects": [{"id": "char-scrub", "kind": "per-char-scroll"}],
}


def _write_vplan(ref: Path, ids: list[str]) -> None:
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "requiredChecks": [{"id": i, "produces": f"{i}.json"} for i in ids],
        }),
        encoding="utf-8",
    )


def test_pre_generate_fails_when_plan_missing_signature_effects_row(tmp_path: Path) -> None:
    """generation-plan.json declares signatureEffects but verification-plan.json
    (minted pre-Step-7) lacks signature-effects-coverage → the amend was skipped
    → fail with a re-run-amend remediation."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)
    (ref / "generation-plan.json").write_text(json.dumps(_SIG_EFFECTS_GENPLAN))
    _write_vplan(ref, ["hydration-check", "html-paste"])

    results = Gate(ref).gate_pre_generate()
    fails = [
        r for r in results
        if r.status == "fail" and "signature-effects-coverage" in r.label
    ]
    assert fails, "missing plan-derived row must fail pre-generate"
    assert "--amend" in fails[0].fix


def test_pre_generate_passes_when_signature_effects_row_present(tmp_path: Path) -> None:
    """Once the plan carries signature-effects-coverage, the backstop is silent."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)
    (ref / "generation-plan.json").write_text(json.dumps(_SIG_EFFECTS_GENPLAN))
    _write_vplan(ref, ["hydration-check", "signature-effects-coverage"])

    results = Gate(ref).gate_pre_generate()
    assert not [
        r for r in results
        if r.status == "fail" and "signature-effects-coverage" in r.label
    ]


def test_pre_generate_backstop_silent_without_signature_effects(tmp_path: Path) -> None:
    """A plan with no signatureEffects/scrollScrub scale need not carry the row."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_pre_generate_baseline(ref)
    _write_valid_artifact_provenance(ref)
    (ref / "generation-plan.json").write_text(
        json.dumps({"schemaVersion": 2, "componentList": []})
    )
    _write_vplan(ref, ["hydration-check"])

    results = Gate(ref).gate_pre_generate()
    assert not [
        r for r in results
        if r.status == "fail" and "signature-effects-coverage" in r.label
    ]
