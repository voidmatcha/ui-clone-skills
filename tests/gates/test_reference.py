import json
from pathlib import Path

from ui_clone.gate import Gate


def test_gate_reference_pass(ref_dir_with_artifacts: Path) -> None:
    gate = Gate(ref_dir_with_artifacts)
    results = gate.gate_reference()
    failures = [r for r in results if r.status == "fail"]
    assert failures == [], f"Unexpected failures: {failures}"


def test_gate_reference_fail_no_screenshots(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    gate = Gate(ref)
    results = gate.gate_reference()
    failures = [r for r in results if r.status == "fail"]
    assert len(failures) > 0


def test_gate_reference_fail_no_transitions_ref(tmp_path: Path) -> None:
    """gate_reference must fail when transitions/ref/ is missing (SKILL.md Phase 1 gate)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    # Has screenshots but no transitions/ref/
    screenshots = ref / "static" / "ref"
    screenshots.mkdir(parents=True)
    for i in range(5):
        (screenshots / f"scroll_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
    (ref / "regions.json").write_text('{"regions": []}')

    gate = Gate(ref)
    results = gate.gate_reference()
    failures = [r for r in results if r.status == "fail"]
    assert any("transitions" in r.label or "transitions" in r.message for r in failures), (
        "Missing transitions/ref/ must produce a fail result"
    )


def test_gate_reference_pass_with_transitions_ref(tmp_path: Path) -> None:
    """gate_reference must pass when all three Phase 1 artifacts exist."""
    ref = tmp_path / "ref"
    ref.mkdir()
    screenshots = ref / "static" / "ref"
    screenshots.mkdir(parents=True)
    for i in range(5):
        (screenshots / f"scroll_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
    transitions = ref / "transitions" / "ref"
    transitions.mkdir(parents=True)
    (transitions / "scroll.webm").write_bytes(b"\x1aE\xdf\xa3" + b"\x00" * 100)
    (ref / "regions.json").write_text('{"regions": []}')

    gate = Gate(ref)
    results = gate.gate_reference()
    failures = [r for r in results if r.status == "fail"]
    assert failures == [], f"Unexpected failures: {failures}"


def _phase1(ref: Path) -> None:
    """Write the Phase-1 existence artifacts a reference gate expects."""
    screenshots = ref / "static" / "ref"
    screenshots.mkdir(parents=True)
    for i in range(5):
        (screenshots / f"scroll_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
    transitions = ref / "transitions" / "ref"
    transitions.mkdir(parents=True)
    (transitions / "scroll.webm").write_bytes(b"\x1aE\xdf\xa3" + b"\x00" * 100)


def test_gate_reference_fails_degenerate_region_geometry(tmp_path: Path) -> None:
    """A non-placeholder region with degenerate/negative geometry must FAIL —
    the reverted detector's gate rubber-stamped {x:-99,y:-99,w:0,h:0}."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _phase1(ref)
    (ref / "regions.json").write_text(json.dumps({
        "placeholder": False,
        "detectionRan": True,
        "regions": [
            {"name": "bogus", "triggerType": "scroll",
             "x": -99, "y": -99, "width": 0, "height": 0},
        ],
    }))
    gate = Gate(ref)
    results = gate.gate_reference()
    failures = [r for r in results if r.status == "fail"]
    blob = " ".join(f"{r.label} {r.message}" for r in failures).lower()
    assert failures, "Degenerate region geometry must produce a gate failure"
    assert "geometr" in blob or "invalid" in blob


def test_gate_reference_fails_out_of_bounds_region_geometry(tmp_path: Path) -> None:
    """A region whose y+height exceeds page bounds must FAIL."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _phase1(ref)
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [{"index": 0, "top": 0, "height": 2000}],
    }))
    (ref / "regions.json").write_text(json.dumps({
        "placeholder": False,
        "detectionRan": True,
        "regions": [
            {"name": "overflow", "triggerType": "scroll", "selector": ".hero",
             "x": 0, "y": 1000, "width": 1440, "height": 9000},
        ],
    }))
    gate = Gate(ref)
    failures = [r for r in gate.gate_reference() if r.status == "fail"]
    assert failures, "Out-of-bounds region geometry must produce a gate failure"


def test_gate_reference_passes_selector_only_real_regions(tmp_path: Path) -> None:
    """Real selector-projection regions (selector + triggerType, no pixel
    geometry) on a motion site must PASS — that's the canonical schema the
    hover/click-state-compare consumers read."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _phase1(ref)
    (ref / "verification-plan.json").write_text(json.dumps({"signals": {"hasHover": True}}))
    (ref / "regions.json").write_text(json.dumps({
        "placeholder": False,
        "detectionRan": True,
        "source": "derive-from-transition-spec",
        "derivedFrom": ["transition-spec.json", "section-map.json"],
        "regions": [
            {"name": "hover-btn-0", "triggerType": "hover",
             "selector": ".nav__download_button"},
        ],
    }))
    gate = Gate(ref)
    failures = [r for r in gate.gate_reference() if r.status == "fail"]
    assert failures == [], f"Valid selector-only regions must not fail: {failures}"


def test_gate_reference_passes_inbounds_geometry_region(tmp_path: Path) -> None:
    """A region carrying valid in-bounds geometry must PASS."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _phase1(ref)
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [{"index": 0, "top": 0, "height": 5000}],
    }))
    (ref / "verification-plan.json").write_text(json.dumps({"signals": {"hasScrollScrub": True}}))
    (ref / "regions.json").write_text(json.dumps({
        "placeholder": False,
        "detectionRan": True,
        "source": "derive-from-transition-spec",
        "derivedFrom": ["transition-spec.json", "section-map.json"],
        "regions": [
            {"name": "hero", "triggerType": "scroll", "selector": ".hero",
             "x": 0, "y": 42, "width": 1440, "height": 638},
        ],
    }))
    gate = Gate(ref)
    failures = [r for r in gate.gate_reference() if r.status == "fail"]
    assert failures == [], f"Valid in-bounds geometry must not fail: {failures}"


def test_gate_reference_fails_real_region_missing_selector(tmp_path: Path) -> None:
    """A non-placeholder region tagged with a triggerType but no resolvable
    selector is not real detection — it must FAIL."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _phase1(ref)
    (ref / "verification-plan.json").write_text(json.dumps({"signals": {"hasHover": True}}))
    (ref / "regions.json").write_text(json.dumps({
        "placeholder": False,
        "detectionRan": True,
        "regions": [
            {"name": "no-selector", "triggerType": "hover", "selector": "transform .2s ease;&"},
        ],
    }))
    gate = Gate(ref)
    failures = [r for r in gate.gate_reference() if r.status == "fail"]
    assert failures, "Region with an invalid selector fragment must fail"


def test_gate_reference_fails_fabricated_regions_without_provenance(tmp_path: Path) -> None:
    """A non-placeholder regions.json that claims real detection (triggerType +
    valid selector) but carries no derive-from-transition-spec provenance is a
    fabricated band on a motion site and must FAIL. Provenance, not mere shape,
    distinguishes honest detection from a hand-written stub.
    Repro: {"placeholder": false, "regions": [{"triggerType": "scroll", "selector": "body"}]}."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _phase1(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps({"signals": {"hasScrollScrub": True}})
    )
    (ref / "regions.json").write_text(json.dumps({
        "placeholder": False,
        "regions": [{"triggerType": "scroll", "selector": "body"}],
    }))
    gate = Gate(ref)
    failures = [r for r in gate.gate_reference() if r.status == "fail"]
    blob = " ".join(f"{r.label} {r.message}" for r in failures).lower()
    assert failures, "Fabricated regions without provenance must fail the reference gate"
    assert "provenance" in blob or "derive-from-transition-spec" in blob


def test_gate_reference_passes_regions_with_derive_provenance(tmp_path: Path) -> None:
    """Legitimately derived regions (source==derive-from-transition-spec and
    derivedFrom listing transition-spec.json — exactly what the Fix-5 producer
    scripts/extract/_capture_artifacts.py stamps) must PASS. Fix 5 must not regress."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _phase1(ref)
    (ref / "verification-plan.json").write_text(
        json.dumps({"signals": {"hasScrollScrub": True}})
    )
    (ref / "regions.json").write_text(json.dumps({
        "placeholder": False,
        "detectionRan": True,
        "source": "derive-from-transition-spec",
        "derivedFrom": ["transition-spec.json", "section-map.json"],
        "regions": [{"name": "hero", "triggerType": "scroll", "selector": ".hero"}],
    }))
    gate = Gate(ref)
    failures = [r for r in gate.gate_reference() if r.status == "fail"]
    assert failures == [], f"Derived regions with provenance must not fail: {failures}"


def test_gate_reference_passes_live_capture_regions_before_transition_spec_exists(
    tmp_path: Path,
) -> None:
    """Browser-measured regions are valid reference evidence before Phase 2.

    The live capture bridge is the producer the reference gate tells agents to
    run when Phase 1 finds motion. Requiring transition-spec provenance here
    creates a cycle because transition-spec.json is produced after this gate.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _phase1(ref)
    clip_dir = ref / "clip" / "ref"
    clip_dir.mkdir(parents=True)
    idle = "clip/ref/00-button-idle.png"
    active = "clip/ref/00-button-active.png"
    (ref / idle).write_bytes(b"\x89PNG\r\n\x1a\nidle")
    (ref / active).write_bytes(b"\x89PNG\r\n\x1a\nactive")
    artifacts = {"idle": idle, "active": active}
    (ref / "verification-plan.json").write_text(
        json.dumps({"signals": {"hasHover": True}}),
        encoding="utf-8",
    )
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
                        "artifacts": artifacts,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "capture-region-artifacts-summary.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "counts": {"captured": 1, "unsupported": 0, "notInstantiated": 0},
                "captured": [
                    {
                        "region": "button",
                        "triggerType": "hover",
                        "selector": ".button",
                        "artifacts": artifacts,
                        "observation": {
                            "changedProperties": ["transform"],
                            "from": {"transform": "none"},
                            "to": {"transform": "matrix(1.1, 0, 0, 1.1, 0, 0)"},
                            "pixelCorroborated": True,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    failures = [r for r in Gate(ref).gate_reference() if r.status == "fail"]

    assert failures == [], f"Live-captured regions must not fail: {failures}"


def test_gate_reference_fails_live_capture_when_summary_failed_or_region_skipped(
    tmp_path: Path,
) -> None:
    """Probe-failed active regions are not browser-measured transition evidence."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _phase1(ref)
    clip_dir = ref / "clip" / "ref"
    clip_dir.mkdir(parents=True)
    idle = "clip/ref/00-button-idle.png"
    active = "clip/ref/00-button-active.png"
    (ref / idle).write_bytes(b"\x89PNG\r\n\x1a\nidle")
    (ref / active).write_bytes(b"\x89PNG\r\n\x1a\nactive")
    artifacts = {"idle": idle, "active": active}
    (ref / "verification-plan.json").write_text(
        json.dumps({"signals": {"hasHover": True}}),
        encoding="utf-8",
    )
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
                        "artifacts": artifacts,
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
                        "artifacts": artifacts,
                        "observation": {
                            "changedProperties": ["transform"],
                            "from": {"transform": "none"},
                            "to": {"transform": "matrix(1.1, 0, 0, 1.1, 0, 0)"},
                            "pixelCorroborated": True,
                        },
                    }
                ],
                "skipped": [
                    {
                        "region": "signed-in-menu",
                        "triggerType": "hover",
                        "selector": ".signed-in-menu",
                        "reason": "selector matches no elements",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    failures = [r for r in Gate(ref).gate_reference() if r.status == "fail"]
    blob = " ".join(f"{r.label} {r.message}" for r in failures).lower()

    assert failures, "Failed/skipped live capture summary must fail the reference gate"
    assert "live-capture" in blob or "provenance" in blob


def test_gate_reference_fails_bridge_source_without_live_capture_backing(
    tmp_path: Path,
) -> None:
    """A bridge source string without matching measured evidence is forgeable."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _phase1(ref)
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
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "capture-region-artifacts-summary.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "counts": {"captured": 0},
                "captured": [],
            }
        ),
        encoding="utf-8",
    )

    failures = [r for r in Gate(ref).gate_reference() if r.status == "fail"]
    blob = " ".join(f"{r.label} {r.message}" for r in failures).lower()

    assert failures, "Unmeasured bridge provenance must fail the reference gate"
    assert "provenance" in blob or "live-capture" in blob


def test_gate_reference_warns_placeholder_without_static_classification(
    tmp_path: Path,
) -> None:
    """Phase 1 placeholders are provisional, not generation-ready proof."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _phase1(ref)
    (ref / "regions.json").write_text(
        json.dumps(
            {
                "placeholder": True,
                "detectionRan": False,
                "regions": [
                    {
                        "name": "full-page-placeholder",
                        "selector": "body",
                        "x": 0,
                        "y": 0,
                        "width": 1440,
                        "height": 900,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    results = Gate(ref).gate_reference()
    failures = [r for r in results if r.status == "fail"]
    warnings = [r for r in results if r.status == "warn"]
    blob = " ".join(f"{r.label} {r.message}" for r in warnings).lower()

    assert failures == []
    assert warnings, "Placeholder regions without typed static evidence must warn"
    assert "placeholder" in blob or "static" in blob


def test_gate_reference_warns_with_capture_error(tmp_path: Path) -> None:
    """Structured Phase 1 failures are surfaced next to reference gate rows."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "capture-error.json").write_text(
        json.dumps(
            {
                "stage": "scroll-video:record-stop",
                "artifact": "scroll-video/ref/full-scroll.webm",
                "message": "✗ No recording in progress",
            }
        )
    )

    gate = Gate(ref)
    results = gate.gate_reference()
    warnings = [r for r in results if r.status == "warn"]
    assert len(warnings) == 1
    assert "scroll-video:record-stop" in warnings[0].message
    assert "scroll-video/ref/full-scroll.webm" in warnings[0].message
    assert "No recording in progress" in warnings[0].message
