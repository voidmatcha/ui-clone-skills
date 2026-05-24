import json
from pathlib import Path

from ui_clone.gate import Gate

from ._helpers import (
    _write_min_spec_artifacts,
)


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
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1, "requiredChecks": []
    }))
    (ref / "animation-runtime-dump.json").write_text(json.dumps({
        "gsap": {"version": "3.12.5"},
        "scrollTrigger": [{"trigger": "section.hero", "start": 0, "end": 600}],
        "ix2": {"timelineCount": 0, "eventCount": 0},
    }))

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
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1, "requiredChecks": []
    }))
    (ref / "animation-runtime-dump.json").write_text(json.dumps({
        "gsap": None,
        "scrollTrigger": [],
        "framer": {"motionValues": [{"selector": ".card"}]},
        "ix2": {"timelineCount": 2, "eventCount": 3},
    }))

    results = Gate(ref).gate_spec()
    failures = [r for r in results if r.status == "fail"]

    assert any(
        r.label == "runtime motion transition-spec coverage"
        and "Framer" in r.message
        and "Webflow IX2" in r.message
        for r in failures
    ), failures



def test_gate_spec_fails_when_bundle_map_missing(tmp_path: Path) -> None:
    """gate_spec must fail when bundle-map.json is absent."""
    ref = tmp_path / "ref"
    ref.mkdir()
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
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": []}))
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1, "requiredChecks": []
    }))
    verify = ref / "verify"
    verify.mkdir()
    for i in range(5):
        (verify / f"frame_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)

    gate = Gate(ref)
    results = gate.gate_spec()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"gate_spec must pass with required files present: {failures}"



def test_gate_spec_fails_when_any_transition_missing_documented_fields(tmp_path: Path) -> None:
    """Every transition entry must carry the documented bundle-to-code handoff fields."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({"chunks": ["a.js"]}))
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": []}))
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1, "requiredChecks": []
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
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
    }))
    verify = ref / "verify"
    verify.mkdir()
    for i in range(5):
        (verify / f"frame_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)

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
        r
        for r in results
        if r.status == "fail" and "paid-font substitution" in r.label
    ]
    assert not sub_failures, (
        f"decision=use must not trigger substitution failure: {sub_failures}"
    )



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
    failures = [
        r
        for r in results
        if r.status == "fail" and "paid-font substitution" in r.label
    ]
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
    (ref / "asset-substitution.json").write_text(
        json.dumps({"images": [{"from": "a", "to": "b"}]})
    )

    gate = Gate(ref)
    results = gate.gate_spec()
    failures = [
        r
        for r in results
        if r.status == "fail" and "paid-font substitution" in r.label
    ]
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
            {
                "fonts": [
                    {"from": "Adobe Garamond Pro", "to": "EB Garamond", "reason": "paid"}
                ]
            }
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
    failures = [
        r
        for r in results
        if r.status == "fail" and "paid-font substitution" in r.label
    ]
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
        json.dumps(
            {"fonts": [{"from": "Die Grotesk", "to": "Inter Variable"}]}
        )
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
    assert any(
        "download attempt missing" in label for label in fail_labels
    ), f"loop-38 regression — must fail without download attempt: {fail_labels}"
