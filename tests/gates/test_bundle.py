import json
from pathlib import Path

from ui_clone.gate import Gate


def test_gate_bundle_fails_when_no_js_files(tmp_path: Path) -> None:
    """gate_bundle must fail when bundles/ directory has no JS files."""
    ref = tmp_path / "ref"
    ref.mkdir()
    bundles = ref / "bundles"
    bundles.mkdir()
    # No JS files

    gate = Gate(ref)
    results = gate.gate_bundle()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "Empty bundles/ must produce a fail result"


def test_gate_bundle_fails_when_required_json_missing(tmp_path: Path) -> None:
    """gate_bundle must fail when interactions-detected.json is missing."""
    ref = tmp_path / "ref"
    ref.mkdir()
    bundles = ref / "bundles"
    bundles.mkdir()
    (bundles / "chunk-0.js").write_text("// bundle")
    # interactions-detected.json, scroll-engine.json, external-sdks.json intentionally absent

    gate = Gate(ref)
    results = gate.gate_bundle()
    failures = [r for r in results if r.status == "fail"]
    assert any(
        "interactions-detected" in r.label or "interactions-detected" in r.message for r in failures
    ), "Missing interactions-detected.json must produce a fail"


def test_gate_bundle_passes_with_required_files(tmp_path: Path) -> None:
    """gate_bundle must pass when bundles/ has JS files and all required JSON files exist."""
    ref = tmp_path / "ref"
    ref.mkdir()
    bundles = ref / "bundles"
    bundles.mkdir()
    for i in range(3):
        (bundles / f"chunk-{i}.js").write_text("// bundle")
    (ref / "interactions-detected.json").write_text(
        json.dumps({"interactions": [], "hasPreloader": False})
    )
    (ref / "scroll-engine.json").write_text(json.dumps({"engine": "native"}))
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": [], "gsap": False}))

    gate = Gate(ref)
    results = gate.gate_bundle()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"gate_bundle must pass with required files: {failures}"


def _bundle_ref_with_required_files(tmp_path: Path) -> Path:
    ref = tmp_path / "ref"
    ref.mkdir()
    bundles = ref / "bundles"
    bundles.mkdir()
    for i in range(3):
        (bundles / f"chunk-{i}.js").write_text("// bundle")
    (ref / "interactions-detected.json").write_text(
        json.dumps({"interactions": [], "hasPreloader": False})
    )
    (ref / "scroll-engine.json").write_text(json.dumps({"engine": "native"}))
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": [], "gsap": False}))
    return ref


def test_gate_bundle_fails_when_extraction_status_incomplete(tmp_path: Path) -> None:
    """Directive item 1 (bundle fail-closed): a site that HAS bundles/ but whose
    deterministic bundle parser crashed leaves bundle-extraction-status.json
    {"completed": false} (written by pipeline.execute). gate_bundle must FAIL so
    the run cannot close out green on a silently-incomplete bundle pass."""
    ref = _bundle_ref_with_required_files(tmp_path)
    (ref / "bundle-extraction-status.json").write_text(
        json.dumps({"completed": False, "writtenBy": "pipeline.execute", "advisory": "crash"})
    )
    gate = Gate(ref)
    results = gate.gate_bundle()
    failures = [r for r in results if r.status == "fail"]
    assert any("completed=false" in r.message or "completion" in r.label for r in failures), (
        f"completed=false status must fail the bundle gate: {results}"
    )


def test_gate_bundle_passes_when_extraction_status_completed(tmp_path: Path) -> None:
    """A completed (or absent) status artifact must NOT add a failure — only an
    explicit completed=false (a real producer crash) is fail-closed."""
    ref = _bundle_ref_with_required_files(tmp_path)
    (ref / "bundle-extraction-status.json").write_text(
        json.dumps({"completed": True, "writtenBy": "pipeline.execute"})
    )
    gate = Gate(ref)
    results = gate.gate_bundle()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"completed=true status must not fail the bundle gate: {failures}"


def test_gate_bundle_fails_when_interactions_have_only_placeholder_regions(
    tmp_path: Path,
) -> None:
    """Detected interactions defer to capture, but placeholder regions must not pass."""
    ref = _bundle_ref_with_required_files(tmp_path)
    (ref / "interactions-detected.json").write_text(
        json.dumps(
            {"interactions": [{"id": "hover-0", "trigger": "hover", "target": ".listing-card"}]}
        )
    )
    (ref / "regions.json").write_text(
        json.dumps(
            {
                "placeholder": True,
                "detectionRan": False,
                "regions": [{"name": "full-page", "selector": "body"}],
            }
        )
    )

    failures = [r for r in Gate(ref).gate_bundle() if r.status == "fail"]

    assert any(
        "ui-capture detection.md Phase 2" in r.message and "capture-transitions 2B-2E" in r.message
        for r in failures
    ), f"placeholder regions must fail when interactions exist: {failures}"


def test_gate_bundle_passes_placeholder_regions_for_static_site(tmp_path: Path) -> None:
    """A truly static site may retain placeholder regions without transition capture."""
    ref = _bundle_ref_with_required_files(tmp_path)
    (ref / "regions.json").write_text(
        json.dumps(
            {
                "placeholder": True,
                "detectionRan": False,
                "regions": [{"name": "full-page", "selector": "body"}],
            }
        )
    )

    failures = [r for r in Gate(ref).gate_bundle() if r.status == "fail"]

    assert not failures, f"static sites must keep passing the bundle gate: {failures}"


def test_gate_bundle_fails_when_interactions_have_unclassified_regions(
    tmp_path: Path,
) -> None:
    """Non-placeholder regions still owe trigger classification for interactions."""
    ref = _bundle_ref_with_required_files(tmp_path)
    (ref / "interactions-detected.json").write_text(
        json.dumps(
            {"interactions": [{"id": "hover-0", "trigger": "hover", "target": ".listing-card"}]}
        )
    )
    (ref / "regions.json").write_text(
        json.dumps(
            {
                "placeholder": False,
                "detectionRan": True,
                "regions": [{"name": "listing-card-hover", "selector": ".listing-card"}],
            }
        )
    )

    failures = [r for r in Gate(ref).gate_bundle() if r.status == "fail"]

    assert any(r.label == "deferred transition evidence" for r in failures), (
        f"unclassified regions must fail when interactions exist: {failures}"
    )


def test_gate_bundle_passes_trigger_classified_regions_for_interactions(
    tmp_path: Path,
) -> None:
    """A real capture with concrete region artifacts satisfies the deferred check."""
    ref = _bundle_ref_with_required_files(tmp_path)
    (ref / "clip" / "ref").mkdir(parents=True)
    for state in ("idle", "active"):
        (ref / "clip" / "ref" / f"listing-card-{state}.png").write_bytes(b"\x89PNG" + b"0" * 128)
    (ref / "interactions-detected.json").write_text(
        json.dumps(
            {"interactions": [{"id": "hover-0", "trigger": "hover", "target": ".listing-card"}]}
        )
    )
    (ref / "regions.json").write_text(
        json.dumps(
            {
                "placeholder": False,
                "detectionRan": True,
                "hover": [
                    {
                        "name": "listing-card-hover",
                        "selector": ".listing-card",
                        "triggerType": "css-hover",
                        "artifacts": {
                            "idle": "clip/ref/listing-card-idle.png",
                            "active": "clip/ref/listing-card-active.png",
                        },
                    }
                ],
            }
        )
    )

    failures = [r for r in Gate(ref).gate_bundle() if r.status == "fail"]

    assert not failures, f"trigger-classified regions must pass the bundle gate: {failures}"


def test_gate_bundle_passes_spec_derived_dispatch_only_regions(
    tmp_path: Path,
) -> None:
    """The canonical region projector deliberately emits no capture manifest."""
    ref = _bundle_ref_with_required_files(tmp_path)
    (ref / "interactions-detected.json").write_text(
        json.dumps(
            {"interactions": [{"id": "hover-0", "trigger": "hover", "target": ".button"}]}
        )
    )
    (ref / "regions.json").write_text(
        json.dumps(
            {
                "placeholder": False,
                "detectionRan": True,
                "source": "derive-from-transition-spec",
                "derivedFrom": ["transition-spec.json", "section-map.json"],
                "regions": [
                    {
                        "name": "hover-0",
                        "selector": ".button",
                        "triggerType": "hover",
                        "dispatchOnly": True,
                        "referenceFrames": ["static/ref/section-0.png"],
                    }
                ],
            }
        )
    )

    failures = [r for r in Gate(ref).gate_bundle() if r.status == "fail"]

    assert not failures, f"spec-derived dispatch regions must pass: {failures}"


def test_gate_bundle_rejects_handset_dispatch_only_without_file_provenance(
    tmp_path: Path,
) -> None:
    """A per-region flag alone must not bypass real capture obligations."""
    ref = _bundle_ref_with_required_files(tmp_path)
    (ref / "interactions-detected.json").write_text(
        json.dumps(
            {"interactions": [{"id": "hover-0", "trigger": "hover", "target": ".button"}]}
        )
    )
    (ref / "regions.json").write_text(
        json.dumps(
            {
                "placeholder": False,
                "detectionRan": True,
                "regions": [
                    {
                        "name": "hover-0",
                        "selector": ".button",
                        "triggerType": "hover",
                        "dispatchOnly": True,
                    }
                ],
            }
        )
    )

    failures = [r for r in Gate(ref).gate_bundle() if r.status == "fail"]

    assert any("missing artifacts manifest" in r.message for r in failures)


def test_gate_bundle_rejects_dispatch_only_source_without_derived_provenance(
    tmp_path: Path,
) -> None:
    """The producer source string alone is not a derivation record."""
    ref = _bundle_ref_with_required_files(tmp_path)
    (ref / "interactions-detected.json").write_text(
        json.dumps(
            {"interactions": [{"id": "hover-0", "trigger": "hover", "target": ".button"}]}
        )
    )
    (ref / "regions.json").write_text(
        json.dumps(
            {
                "placeholder": False,
                "detectionRan": True,
                "source": "derive-from-transition-spec",
                "regions": [
                    {
                        "name": "hover-0",
                        "selector": ".button",
                        "triggerType": "hover",
                        "dispatchOnly": True,
                    }
                ],
            }
        )
    )

    failures = [r for r in Gate(ref).gate_bundle() if r.status == "fail"]

    assert any("missing artifacts manifest" in r.message for r in failures)


def test_gate_bundle_fails_when_region_artifact_file_is_missing(tmp_path: Path) -> None:
    """Artifact paths are evidence only when the referenced files exist."""
    ref = _bundle_ref_with_required_files(tmp_path)
    (ref / "interactions-detected.json").write_text(
        json.dumps(
            {"interactions": [{"id": "hover-0", "trigger": "hover", "target": ".listing-card"}]}
        )
    )
    (ref / "regions.json").write_text(
        json.dumps(
            {
                "placeholder": False,
                "detectionRan": True,
                "hover": [
                    {
                        "name": "listing-card-hover",
                        "selector": ".listing-card",
                        "triggerType": "css-hover",
                        "artifacts": {
                            "idle": "clip/ref/listing-card-idle.png",
                            "active": "clip/ref/listing-card-active.png",
                        },
                    }
                ],
            }
        )
    )

    failures = [r for r in Gate(ref).gate_bundle() if r.status == "fail"]

    assert any("existing ref artifacts" in r.message for r in failures), (
        f"missing region artifact files must fail the deferred check: {failures}"
    )


def test_gate_bundle_rejects_placeholder_transition_as_region_evidence(
    tmp_path: Path,
) -> None:
    """The Phase-1 placeholder video must never count as transition evidence."""
    ref = _bundle_ref_with_required_files(tmp_path)
    placeholder = ref / "transitions" / "ref" / "placeholder.webm"
    placeholder.parent.mkdir(parents=True)
    placeholder.write_bytes(b"placeholder")
    (ref / "interactions-detected.json").write_text(
        json.dumps(
            {"interactions": [{"id": "hover-0", "trigger": "hover", "target": ".listing-card"}]}
        )
    )
    (ref / "regions.json").write_text(
        json.dumps(
            {
                "placeholder": False,
                "detectionRan": True,
                "hover": [
                    {
                        "name": "listing-card-hover",
                        "selector": ".listing-card",
                        "triggerType": "css-hover",
                        "artifacts": {
                            "video": "transitions/ref/placeholder.webm",
                        },
                    }
                ],
            }
        )
    )

    failures = [r for r in Gate(ref).gate_bundle() if r.status == "fail"]

    assert any("placeholder" in r.message for r in failures), (
        f"placeholder.webm must not satisfy transition evidence: {failures}"
    )


def test_gate_bundle_rejects_non_capture_file_as_region_evidence(
    tmp_path: Path,
) -> None:
    """An arbitrary in-tree file must not count as a captured transition state."""
    ref = _bundle_ref_with_required_files(tmp_path)
    (ref / "clip" / "ref").mkdir(parents=True)
    (ref / "clip" / "ref" / "listing-card-idle.png").write_bytes(b"idle")
    (ref / "notes.txt").write_text("not capture evidence")
    (ref / "interactions-detected.json").write_text(
        json.dumps(
            {"interactions": [{"id": "hover-0", "trigger": "hover", "target": ".listing-card"}]}
        )
    )
    (ref / "regions.json").write_text(
        json.dumps(
            {
                "placeholder": False,
                "detectionRan": True,
                "hover": [
                    {
                        "name": "listing-card-hover",
                        "selector": ".listing-card",
                        "triggerType": "css-hover",
                        "artifacts": {
                            "idle": "clip/ref/listing-card-idle.png",
                            "active": "notes.txt",
                        },
                    }
                ],
            }
        )
    )

    failures = [r for r in Gate(ref).gate_bundle() if r.status == "fail"]

    assert any("clip/ref/" in r.message or "transitions/ref/" in r.message for r in failures), (
        f"non-capture files must not satisfy transition evidence: {failures}"
    )


def test_gate_bundle_rejects_incomplete_trigger_state_manifest(
    tmp_path: Path,
) -> None:
    """A css-hover capture owes both idle and active state artifacts."""
    ref = _bundle_ref_with_required_files(tmp_path)
    artifact = ref / "clip" / "ref" / "listing-card-idle.png"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"idle")
    (ref / "interactions-detected.json").write_text(
        json.dumps(
            {"interactions": [{"id": "hover-0", "trigger": "hover", "target": ".listing-card"}]}
        )
    )
    (ref / "regions.json").write_text(
        json.dumps(
            {
                "placeholder": False,
                "detectionRan": True,
                "hover": [
                    {
                        "name": "listing-card-hover",
                        "selector": ".listing-card",
                        "triggerType": "css-hover",
                        "artifacts": {
                            "idle": "clip/ref/listing-card-idle.png",
                        },
                    }
                ],
            }
        )
    )

    failures = [r for r in Gate(ref).gate_bundle() if r.status == "fail"]

    assert any("active" in r.message and "missing artifact path" in r.message for r in failures), (
        f"incomplete trigger state manifests must fail: {failures}"
    )


def test_gate_bundle_fails_when_hover_rules_exist_but_interactions_are_empty(
    tmp_path: Path,
) -> None:
    """Emptying the interaction set must not switch the evidence check off.

    The transition-evidence check only runs when interactions are present, so an
    interaction list emptied by a failed capture used to skip the check rather
    than fail it, and the gate advanced a reference with zero captured motion.
    """
    ref = _bundle_ref_with_required_files(tmp_path)
    (ref / "hover-css-rules.json").write_text(
        json.dumps({"rules": [{"selector": ".cta:hover"}], "summary": {"count": 1}})
    )

    failures = [r for r in Gate(ref).gate_bundle() if r.status == "fail"]

    assert [r.label for r in failures] == ["hover transition evidence"]


def test_gate_bundle_passes_when_no_hover_rules_exist(tmp_path: Path) -> None:
    """A reference with no hover CSS at all is legitimately interaction-free."""
    ref = _bundle_ref_with_required_files(tmp_path)
    (ref / "hover-css-rules.json").write_text(json.dumps({"rules": [], "summary": {"count": 0}}))

    failures = [r for r in Gate(ref).gate_bundle() if r.status == "fail"]

    assert not failures, f"a hover-free reference must still pass: {failures}"
