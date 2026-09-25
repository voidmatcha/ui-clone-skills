"""`python -m ui_clone.scoped_check` — the scoped-clone completion command —
and its Stop / declaration hook wiring."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from ui_clone import scoped_check
from ui_clone.hooks._common import mark_ref_session
from ui_clone.scoped_frames import SSIM_THRESHOLD
from ui_clone.scoped_provenance import record_checksum

from ._helpers import run_hook, set_active_marker, write_extracted_json
from ._scoped_fixtures import (
    IMPL_URL,
    REF_URL,
    build_scoped_evidence,
    element_target_payload,
    produce_diff,
    sample_styles,
    sample_subtree,
    set_mtime,
    write_computed,
    write_diff,
    write_manifest,
    write_png,
    write_sequence,
)

SESSION = "scoped-check-session"
SECTION_GATE = "ui_clone.hooks.section_gate"


def _codes(ref: Path) -> set[str]:
    result = scoped_check.check(ref)
    assert result["status"] == "failed", result
    return {f["code"] for f in result["failures"]}


def _reasons(ref: Path) -> dict[str, str]:
    result = scoped_check.check(ref)
    assert result["status"] == "failed", result
    return {f["code"]: f["reason"] for f in result["failures"]}


def _diff(ref: Path) -> dict[str, Any]:
    data = json.loads((ref / "pixel-perfect-diff.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _recapture_impl(ref: Path, frame: str, color: tuple[int, int, int]) -> None:
    """Replace an impl frame the way a real re-capture does (manifest + diff re-produced)."""
    path = ref / "frames" / "impl" / frame
    write_png(path, color)
    set_mtime(path, time.time() - 50)
    write_manifest(ref, "impl")
    produce_diff(ref)


# -- pass ---------------------------------------------------------------------


def test_complete_hover_evidence_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ref = build_scoped_evidence(tmp_path)
    assert scoped_check.main([str(ref), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "passed"
    assert out["failures"] == []
    assert out["frames_compared"] == 2
    assert out["trigger_opened"] is False
    assert any(p.endswith("Hero.tsx") for p in out["impl_sources"])


def test_complete_modal_evidence_passes(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path, "pricing-modal", modal=True, component="PricingModal.tsx")
    result = scoped_check.check(ref)
    assert result["status"] == "passed", result["failures"]
    assert result["trigger_opened"] is True
    assert set(result["sequences"]) == {"open", "close"}


def test_ref_as_impl_self_pass(tmp_path: Path) -> None:
    """Achievability (docs/gates.md ref-vs-ref invariant): byte-identical frames
    PASS when the impl manifest proves they came from the implementation origin."""
    ref = build_scoped_evidence(tmp_path)
    for frame in ("idle.png", "active.png"):
        (ref / "frames" / "impl" / frame).write_bytes((ref / "frames" / "ref" / frame).read_bytes())
        set_mtime(ref / "frames" / "impl" / frame, time.time() - 50)
    write_manifest(ref, "impl")
    produce_diff(ref)
    assert scoped_check.check(ref)["status"] == "passed"


def test_text_output_and_usage_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ref = build_scoped_evidence(tmp_path)
    assert scoped_check.main([str(ref)]) == 0
    assert "scoped_check PASS" in capsys.readouterr().out
    assert scoped_check.main([str(tmp_path / "missing")]) == 2
    assert scoped_check.main([str(ref), "--impl-root", str(tmp_path / "nope")]) == 2
    assert scoped_check.main(["--bogus"]) == 2


# -- element target -----------------------------------------------------------


def test_missing_element_target_fails(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    (ref / "element-target.json").unlink()
    assert "element-target-missing" in _codes(ref)


def test_invalid_element_target_fails(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    (ref / "element-target.json").write_text(json.dumps({"schemaVersion": 1, "ok": True}))
    set_mtime(ref / "element-target.json", time.time() - 100)
    assert "element-target-invalid" in _codes(ref)


def test_page_level_marker_fails(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    (ref / "extracted.json").write_text("{}")
    assert "page-level-run" in _codes(ref)


# -- frames -------------------------------------------------------------------


def test_missing_ref_frames_fail(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    for p in (ref / "frames" / "ref").glob("*.png"):
        p.unlink()
    assert "ref-frames-missing" in _codes(ref)


def test_reference_only_run_fails(tmp_path: Path) -> None:
    """Reference frames and a diff alone are not implementation evidence."""
    ref = build_scoped_evidence(tmp_path)
    for p in (ref / "frames" / "impl").glob("*.png"):
        p.unlink()
    assert "impl-frames-missing" in _codes(ref)


def test_unmatched_frame_names_fail(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    (ref / "frames" / "impl" / "active.png").rename(ref / "frames" / "impl" / "hover.png")
    assert "impl-frames-unmatched" in _codes(ref)


def test_nonzero_ae_frame_fails(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    _recapture_impl(ref, "active.png", (250, 0, 0))
    reasons = _reasons(ref)
    assert "active.png (AE 48)" in reasons["frame-ae-nonzero"]
    assert "diff-result" in reasons  # the producer saw the same AE


def test_size_mismatch_frame_fails(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    frame = ref / "frames" / "impl" / "idle.png"
    write_png(frame, (0, 40, 60), size=(9, 6))
    set_mtime(frame, time.time() - 50)
    write_manifest(ref, "impl")
    produce_diff(ref)
    assert "frame-ae-nonzero" in _codes(ref)


def test_impl_frame_linked_to_reference_fails(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    impl = ref / "frames" / "impl" / "idle.png"
    impl.unlink()
    impl.symlink_to(ref / "frames" / "ref" / "idle.png")
    assert "impl-frame-is-reference" in _codes(ref)


def test_impl_frame_hardlinked_to_reference_fails(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    impl = ref / "frames" / "impl" / "idle.png"
    impl.unlink()
    os.link(ref / "frames" / "ref" / "idle.png", impl)
    assert "impl-frame-is-reference" in _codes(ref)


def test_impl_frames_older_than_target_fail(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    set_mtime(ref / "element-target.json", time.time() - 20)
    assert "impl-frames-predate-target" in _codes(ref)


# -- capture provenance -------------------------------------------------------


def test_impl_manifest_missing_fails(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    (ref / "frames" / "impl" / "capture-manifest.json").unlink()
    codes = _codes(ref)
    assert "impl-manifest-missing" in codes
    assert "diff-stale" in codes


def test_ref_manifest_missing_fails(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    (ref / "frames" / "ref" / "capture-manifest.json").unlink()
    assert "ref-manifest-missing" in _codes(ref)


def test_byte_copied_reference_frame_without_impl_capture_fails(tmp_path: Path) -> None:
    """A pixel-identical impl frame that the impl manifest does not cover is not a capture."""
    ref = build_scoped_evidence(tmp_path)
    (ref / "frames" / "impl" / "extra.png").write_bytes((ref / "frames" / "ref" / "idle.png").read_bytes())
    (ref / "frames" / "ref" / "extra.png").write_bytes((ref / "frames" / "ref" / "idle.png").read_bytes())
    write_manifest(ref, "ref")
    reasons = _reasons(ref)
    assert "extra.png" in reasons["impl-frame-unmanifested"]


def test_impl_frames_captured_from_reference_origin_fail(tmp_path: Path) -> None:
    """Frames recorded by the script while the session was on the reference site
    are reference captures, however identical they are."""
    ref = build_scoped_evidence(tmp_path)
    write_manifest(ref, "impl", url=REF_URL)
    produce_diff(ref)
    reasons = _reasons(ref)
    assert "non-reference origin" in reasons["impl-frame-origin"]


def test_ref_frames_captured_from_other_origin_fail(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    write_manifest(ref, "ref", url=IMPL_URL)
    produce_diff(ref)
    assert "ref-frame-origin" in _codes(ref)


def _rewrite_same_pixels(frame: Path) -> None:
    """Re-encode a PNG with identical pixels but different bytes (a metadata chunk)."""
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("Comment", "re-encoded")
    with Image.open(frame) as image:
        pixels = image.copy()
    pixels.save(frame, pnginfo=info)
    set_mtime(frame, time.time() - 50)


def test_frame_replaced_after_capture_fails(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    _rewrite_same_pixels(ref / "frames" / "impl" / "idle.png")
    codes = _codes(ref)
    assert "impl-frame-hash-mismatch" in codes
    assert "frame-ae-nonzero" not in codes


def test_manifest_from_other_producer_is_not_provenance(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    path = ref / "frames" / "impl" / "capture-manifest.json"
    data = json.loads(path.read_text())
    data["producer"] = "hand"
    path.write_text(json.dumps(data))
    assert "impl-manifest-missing" in _codes(ref)


# -- target sanity ------------------------------------------------------------


def test_old_schema_element_target_needs_reprobe(tmp_path: Path) -> None:
    """A schemaVersion 1 record (no match count / visibility) still marks a
    scoped run for the hooks but cannot complete one."""
    ref = build_scoped_evidence(tmp_path)
    target = ref / "element-target.json"
    target.write_text(json.dumps(element_target_payload(schema_version=1)), encoding="utf-8")
    set_mtime(target, time.time() - 100)
    produce_diff(ref)
    reasons = _reasons(ref)
    assert "older element-evidence.sh (schemaVersion 1" in reasons["element-target-schema"]
    assert "re-probe" in reasons["element-target-schema"]
    assert "element-target-invalid" not in reasons


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"matchCount": 2}, "selector matches 2 element(s), expected exactly 1"),
        ({"matchCount": 0}, "selector matches 0 element(s)"),
        ({"bbox": {"x": 0, "y": 0, "width": 1440, "height": 7}}, "bbox 1440x7 is degenerate (minimum 8x8 CSS px)"),
        (
            {"visible": False, "visibility": {"display": "none", "visibility": "visible", "opacity": "1", "hiddenBy": "target display:none"}},
            "not visible in the probed state (target display:none)",
        ),
    ],
)
def test_element_target_sanity_failures(tmp_path: Path, overrides: dict[str, object], expected: str) -> None:
    ref = build_scoped_evidence(tmp_path)
    payload = element_target_payload()
    annotation = payload["annotation"]
    assert isinstance(annotation, dict)
    annotation.update(overrides)
    target = ref / "element-target.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    set_mtime(target, time.time() - 100)
    reasons = _reasons(ref)
    assert expected in reasons["element-target-sanity"]
    assert "re-probe a selector that matches one visible element" in reasons["element-target-sanity"]


def test_clip_captured_from_unsound_target_fails(tmp_path: Path) -> None:
    """The manifest re-validates each clip's recorded match count, box, and visibility."""
    ref = build_scoped_evidence(tmp_path)
    write_manifest(ref, "impl", match_count=3)
    produce_diff(ref)
    assert "selector matches 3 element(s)" in _reasons(ref)["impl-target-sanity"]
    write_manifest(ref, "impl", visible=False)
    produce_diff(ref)
    assert "target display:none" in _reasons(ref)["impl-target-sanity"]
    write_manifest(ref, "impl", bbox={"x": 0, "y": 0, "width": 8, "height": 6})
    produce_diff(ref)
    assert "8x6 is degenerate" in _reasons(ref)["impl-target-sanity"]
    write_manifest(ref, "impl")
    produce_diff(ref)
    assert scoped_check.check(ref)["status"] == "passed"


def test_pass_output_names_the_resolved_target(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ref = build_scoped_evidence(tmp_path)
    assert scoped_check.main([str(ref)]) == 0
    out = capsys.readouterr().out
    assert "target: 'section.hero' (matches: 1, bbox 1440x900 at (0, 0))" in out
    assert "confirm it is the intended element" in out
    assert scoped_check.main([str(ref), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["target"] == {
        "selector": "section.hero",
        "match_count": 1,
        "bbox": {"x": 0, "y": 0, "width": 1440, "height": 900},
        "url": REF_URL,
    }


# -- release hash manifest ------------------------------------------------------


def test_modified_producer_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The evidence hashes, the shipped manifest, and the installed files must
    agree; a manifest that names other hashes fails the run and every record."""
    from ui_clone import scoped_producers

    ref = build_scoped_evidence(tmp_path)
    shipped = scoped_producers.load_manifest()
    assert shipped is not None
    forged = {**shipped, scoped_producers.CAPTURE_MODULE: "0" * 64}
    path = tmp_path / "scoped_producers.sha256.json"
    path.write_text(json.dumps({"schemaVersion": 1, "files": forged}), encoding="utf-8")
    monkeypatch.setattr(scoped_producers, "MANIFEST_PATH", path)
    reasons = _reasons(ref)
    assert "ui_clone/element_capture.py: installed file differs" in reasons["producers-modified"]
    assert "recorder module differs from the shipped ui_clone/element_capture.py" in reasons["impl-frame-producer"]
    forged = {**shipped, scoped_producers.DIFF_MODULE: "0" * 64}
    path.write_text(json.dumps({"schemaVersion": 1, "files": forged}), encoding="utf-8")
    reasons = _reasons(ref)
    assert "scoped_diff module that differs from the shipped one" in reasons["diff-provenance"]
    path.write_text(json.dumps({"schemaVersion": 1, "files": shipped}), encoding="utf-8")
    assert scoped_check.check(ref)["status"] == "passed"


# -- pixel-perfect-diff.json --------------------------------------------------


def test_missing_diff_fails(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    (ref / "pixel-perfect-diff.json").unlink()
    assert "scoped_diff" in _reasons(ref)["diff-missing"]


def test_unparseable_diff_fails(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    write_diff(ref, "{not json")
    assert "diff-invalid" in _codes(ref)


def test_hand_written_diff_is_rejected(tmp_path: Path) -> None:
    """The old agent-written shape (no producer/provenance) never passes."""
    ref = build_scoped_evidence(tmp_path)
    write_diff(
        ref,
        {
            "result": "pass",
            "mismatches": 0,
            "elements": [
                {"selector": ".target", "state": s, "ae": 0, "ssim": 1.0, "status": "pass"}
                for s in ("idle", "active")
            ],
        },
    )
    assert "diff-provenance" in _codes(ref)


def test_edited_producer_output_is_stale(tmp_path: Path) -> None:
    """Flipping a failing producer verdict by hand does not survive re-validation."""
    ref = build_scoped_evidence(tmp_path)
    write_computed(ref, "impl", "active", sample_styles(fontSize="15px"))
    write_manifest(ref, "impl")
    produce_diff(ref)
    assert {"diff-result", "diff-mismatches", "diff-element-fail"} <= _codes(ref)
    data = _diff(ref)
    for row in data["elements"]:
        row.update({"status": "pass", "mismatches": 0, "diff": []})
    write_diff(ref, {**data, "result": "pass", "mismatches": 0})
    reasons = _reasons(ref)
    assert "recomputed 1 computed-style mismatch(es): fontSize" in reasons["diff-element-fail"]
    assert "diff-state-uncovered" in reasons


def test_diff_with_other_property_list_fails(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    write_diff(ref, {**_diff(ref), "propertiesSha256": "0" * 64})
    assert "diff-provenance" in _codes(ref)


def test_diff_result_and_mismatches_fail(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    data = _diff(ref)
    write_diff(ref, {**data, "result": "fail", "mismatches": 2})
    assert {"diff-result", "diff-mismatches"} <= _codes(ref)
    write_diff(ref, {k: v for k, v in data.items() if k != "mismatches"})
    assert "diff-mismatches" in _codes(ref)
    write_diff(ref, {**data, "mismatches": False})
    assert "diff-mismatches" in _codes(ref)


def test_diff_without_rows_fails(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    write_diff(ref, {**_diff(ref), "elements": []})
    assert "diff-elements" in _codes(ref)


@pytest.mark.parametrize(
    "row",
    [
        {"selector": ".target", "state": "idle", "ae": 12, "mismatches": 0, "status": "fail"},
        {"selector": ".target", "state": "idle", "status": "pass"},
        {"selector": ".target", "state": "idle", "ae": 40, "mismatches": 0, "status": "pass"},
        {"selector": ".target", "state": "idle", "ae": 0, "mismatches": 1, "status": "pass"},
        {"selector": ".target", "ae": 0, "mismatches": 0, "status": "pass"},
    ],
)
def test_failing_element_row_fails(tmp_path: Path, row: dict[str, object]) -> None:
    ref = build_scoped_evidence(tmp_path)
    data = _diff(ref)
    rows = [r for r in data["elements"] if isinstance(r, dict) and r["state"] != "idle"]
    write_diff(ref, {**data, "elements": [*rows, row]})
    assert "diff-element-fail" in _codes(ref)


def test_resting_state_without_row_fails(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    data = _diff(ref)
    rows = [r for r in data["elements"] if isinstance(r, dict) and r["state"] == "idle"]
    write_diff(ref, {**data, "elements": rows})
    assert "diff-state-uncovered" in _codes(ref)


def test_computed_style_mismatch_fails_even_with_identical_pixels(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    write_computed(ref, "impl", "idle", sample_styles(fontWeight="700"))
    write_manifest(ref, "impl")
    produce_diff(ref)
    reasons = _reasons(ref)
    assert "mismatches=1" in reasons["diff-mismatches"]
    assert "frame-ae-nonzero" not in reasons


# -- producer records and self checksum ---------------------------------------


def test_diff_built_through_the_api_is_rejected(tmp_path: Path) -> None:
    """`from ui_clone import scoped_diff; scoped_diff.build(...)` records
    `entry: api`; only the CLI's record passes."""
    ref = build_scoped_evidence(tmp_path)
    produce_diff(ref, entry="api")
    reasons = _reasons(ref)
    assert "python -m ui_clone.scoped_diff` CLI" in reasons["diff-provenance"]
    assert "entry 'api'" in reasons["diff-provenance"]
    produce_diff(ref, entry="cli")
    assert scoped_check.check(ref)["status"] == "passed"


def test_diff_edited_after_production_fails_checksum(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    data = _diff(ref)
    write_diff(ref, {**data, "generatedAt": "2000-01-01T00:00:00Z"})
    assert "self checksum differs" in _reasons(ref)["diff-provenance"]
    write_diff(ref, {k: v for k, v in data.items() if k != "recordSha256"})
    assert "self checksum differs" in _reasons(ref)["diff-provenance"]


def test_manifest_without_cli_producer_record_fails(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    write_manifest(ref, "impl", producer={"entry": "api", "argv": [], "moduleSha256": None, "driver": None})
    produce_diff(ref)
    reasons = _reasons(ref)
    assert "not the CLI" in reasons["impl-frame-producer"]
    write_manifest(ref, "impl", producer={"entry": "cli", "argv": [], "driver": {"sha256": "0" * 64}})
    produce_diff(ref)
    assert "differs from the shipped element-state-capture.sh" in _reasons(ref)["impl-frame-producer"]
    write_manifest(ref, "impl")
    produce_diff(ref)
    assert scoped_check.check(ref)["status"] == "passed"


# -- implementation provenance --------------------------------------------------


def test_impl_frames_from_a_page_loading_the_reference_fail(tmp_path: Path) -> None:
    """A local proxy or iframe of the reference site serves a non-reference
    origin, but its resource inventory names the reference host."""
    ref = build_scoped_evidence(tmp_path)
    write_manifest(ref, "impl", resource_origins={"http://localhost:5173": ["script"], "https://example.org:443": ["iframe"]})
    produce_diff(ref)
    reasons = _reasons(ref)
    assert "https://example.org:443 (iframe)" in reasons["impl-frame-loads-reference"]
    assert "ref-frame-loads-reference" not in reasons
    # Subdomains of the reference host count; unrelated hosts do not.
    write_manifest(ref, "impl", resource_origins={"https://cdn.example.org:443": ["script"]})
    produce_diff(ref)
    assert "impl-frame-loads-reference" in _codes(ref)
    write_manifest(ref, "impl", resource_origins={"https://fonts.gstatic.com:443": ["css"], "http://localhost:5173": ["script"]})
    produce_diff(ref)
    assert scoped_check.check(ref)["status"] == "passed"


def test_media_hotlinks_from_the_reference_host_are_allowed(tmp_path: Path) -> None:
    """AGENTS.md "Source fidelity": image/video/font URLs of the reference are
    preserved, so loading them from the reference host is not a proxy signal."""
    ref = build_scoped_evidence(tmp_path)
    write_manifest(
        ref,
        "impl",
        resource_origins={
            "http://localhost:5173": ["script", "stylesheet"],
            "https://example.org:443": ["img", "video", "css", "preload"],
            "https://cdn.example.org:443": ["font"],
        },
    )
    produce_diff(ref)
    assert scoped_check.check(ref)["status"] == "passed"


REF_CODE = {
    "https://example.org/assets/app.css": ["link"],
    "https://example.org/assets/app.js": ["script"],
    "https://cdn.contentful-host.net/site/main.[hash].js": ["script"],
    "https://cdn.contentful-host.net/site/theme.css": ["stylesheet"],
}


@pytest.mark.parametrize(
    "impl_code, expected",
    [
        # the reference CDN bundle (re-deployed hash normalizes to the inventory entry)
        ({"https://cdn.contentful-host.net/site/main.[hash].js": ["script"]}, "main.[hash].js (script; reference code)"),
        # the reference stylesheet
        ({"https://cdn.contentful-host.net/site/theme.css": ["stylesheet"]}, "theme.css (stylesheet; reference code)"),
        # any other code from an origin that served reference code
        ({"https://cdn.contentful-host.net/site/vendor.js": ["script"]}, "https://cdn.contentful-host.net serves reference code"),
    ],
)
def test_impl_loading_reference_code_fails(tmp_path: Path, impl_code: dict[str, list[str]], expected: str) -> None:
    ref = build_scoped_evidence(tmp_path)
    write_manifest(ref, "ref", code_resources=REF_CODE)
    write_manifest(ref, "impl", code_resources={"http://localhost:5173/assets/index.js": ["script"], **impl_code})
    produce_diff(ref)
    reasons = _reasons(ref)
    assert expected in reasons["impl-loads-reference-code"]
    assert "media and font hotlinks are allowed" in reasons["impl-loads-reference-code"]
    assert "reference runtime" in reasons["diff-result"]


def test_unrelated_third_party_code_is_allowed(tmp_path: Path) -> None:
    """Analytics or a library host the reference never loaded is not the
    reference runtime; the reference's own origin counts only for code."""
    ref = build_scoped_evidence(tmp_path)
    write_manifest(ref, "ref", code_resources=REF_CODE)
    write_manifest(
        ref,
        "impl",
        code_resources={
            "http://localhost:5173/assets/index.js": ["script"],
            "https://www.googletagmanager.com/gtag/js": ["script"],
            "https://unpkg.com/gsap@3/dist/gsap.min.js": ["script"],
        },
    )
    produce_diff(ref)
    assert scoped_check.check(ref)["status"] == "passed"


def test_old_schema_manifest_needs_recapture(tmp_path: Path) -> None:
    """A manifest from the previous capture script (schemaVersion 1, no code
    inventory) is named as such, not reported as missing."""
    ref = build_scoped_evidence(tmp_path)
    write_manifest(ref, "impl", schema_version=1)
    produce_diff(ref)
    reasons = _reasons(ref)
    assert "impl-manifest-missing" not in reasons
    assert "older element-state-capture.sh (schemaVersion 1" in reasons["impl-manifest-schema"]
    assert "re-capture frames/impl/" in reasons["impl-manifest-schema"]
    # A current-version manifest without the inventory key is the same case.
    path = ref / "frames" / "impl" / "capture-manifest.json"
    write_manifest(ref, "impl")
    data = json.loads(path.read_text())
    data.pop("codeResources")
    path.write_text(json.dumps(data))
    assert "impl-manifest-schema" in _codes(ref)
    write_manifest(ref, "impl")
    produce_diff(ref)
    assert scoped_check.check(ref)["status"] == "passed"


def test_manifest_without_resource_inventory_fails(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    manifest = json.loads((ref / "frames" / "impl" / "capture-manifest.json").read_text())
    for entry in manifest["entries"].values():
        entry.pop("resourceOrigins")
    (ref / "frames" / "impl" / "capture-manifest.json").write_text(json.dumps(manifest))
    produce_diff(ref)
    assert "no resourceOrigins" in _reasons(ref)["impl-frame-producer"]


def test_reference_iframe_in_component_fails_provenance(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    source = tmp_path / "impl" / "src" / "components" / "Hero.tsx"
    source.write_text('export default () => <iframe src="https://www.example.org/" />\n', encoding="utf-8")
    # Source changed after the diff: stale first.
    assert "Hero.tsx" in _reasons(ref)["diff-stale"]
    produce_diff(ref)
    reasons = _reasons(ref)
    assert "reference-load" in reasons["impl-provenance"] and "Hero.tsx" in reasons["impl-provenance"]
    assert "implementation provenance" in reasons["diff-result"]


def test_proxy_server_added_after_diff_is_caught(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    assert scoped_check.check(ref)["status"] == "passed"
    server = tmp_path / "impl" / "server.js"
    server.write_text('const upstream = "https://example.org";\nrequire("http-proxy");\n', encoding="utf-8")
    reasons = _reasons(ref)
    assert "new source(s)" in reasons["diff-stale"] and "server.js" in reasons["diff-stale"]
    produce_diff(ref)
    reasons = _reasons(ref)
    assert "proxy-mirror-check: fail" in reasons["impl-provenance"]
    assert "upstream-proxy" in reasons["impl-provenance"]
    server.unlink()
    produce_diff(ref)
    assert scoped_check.check(ref)["status"] == "passed"


def test_edited_no_cheat_output_is_stale(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    output = ref / "proxy-mirror-check.json"
    output.write_text(json.dumps({"schemaVersion": 1, "status": "pass", "findings": []}), encoding="utf-8")
    assert "proxy-mirror-check.json" in _reasons(ref)["diff-stale"]
    data = _diff(ref)
    data["noCheat"]["pageLevel"]["proxy-mirror-check"]["status"] = "fail"
    data["recordSha256"] = record_checksum(data)
    write_diff(ref, data)
    assert "proxy-mirror-check: fail" in _reasons(ref)["impl-provenance"]
    data = _diff(ref)
    del data["noCheat"]
    write_diff(ref, {**data, "recordSha256": record_checksum({k: v for k, v in data.items() if k != "recordSha256"})})
    assert "no implementation provenance recorded" in _reasons(ref)["impl-provenance"]


# -- target subtree --------------------------------------------------------------


def test_subtree_style_mismatch_fails_even_with_identical_target(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    write_computed(ref, "impl", "idle", subtree=sample_subtree(color="rgb(9, 9, 9)"))
    write_manifest(ref, "impl")
    produce_diff(ref)
    reasons = _reasons(ref)
    assert "mismatches=1" in reasons["diff-mismatches"]
    row = next(r for r in _diff(ref)["elements"] if r["state"] == "idle")
    assert row["diff"] == [{"path": "div[0]/span[0]", "property": "color", "ref": "rgb(0, 0, 0)", "impl": "rgb(9, 9, 9)"}]
    # Forging the row does not survive the recomputation, which names the node path.
    data = _diff(ref)
    for r in data["elements"]:
        r.update({"status": "pass", "mismatches": 0, "diff": []})
    data.update({"result": "pass", "mismatches": 0})
    write_diff(ref, {**data, "recordSha256": record_checksum({k: v for k, v in data.items() if k != "recordSha256"})})
    assert "div[0]/span[0]/color" in _reasons(ref)["diff-element-fail"]


def test_subtree_structure_mismatch_fails_with_paths(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    nodes = sample_subtree()
    nodes[1] = {"path": "div[0]/a[0]", "tag": "a", "computedStyle": sample_styles()}
    write_computed(ref, "impl", "active", subtree=nodes)
    write_manifest(ref, "impl")
    produce_diff(ref)
    row = next(r for r in _diff(ref)["elements"] if r["state"] == "active")
    assert [(d["path"], d["property"], d["ref"], d["impl"]) for d in row["diff"]] == [
        ("div[0]/a[0]", "-", "NOT FOUND", "found"),
        ("div[0]/span[0]", "-", "found", "NOT FOUND"),
    ]
    write_computed(ref, "impl", "active", descendant_count=7)
    write_manifest(ref, "impl")
    produce_diff(ref)
    row = next(r for r in _diff(ref)["elements"] if r["state"] == "active")
    assert row["diff"] == [{"path": "", "property": "descendantCount", "ref": "2", "impl": "7"}]


def test_computed_record_without_subtree_fails(tmp_path: Path) -> None:
    """A record from the previous capture contract is re-captured, not trusted."""
    ref = build_scoped_evidence(tmp_path)
    write_computed(ref, "impl", "idle", schema_version=1)
    write_manifest(ref, "impl")
    produce_diff(ref)
    row = next(r for r in _diff(ref)["elements"] if r["state"] == "idle")
    assert row["diff"] == [{"path": "", "property": "subtree", "ref": "recorded", "impl": "NOT RECORDED"}]
    assert "diff-element-fail" in _codes(ref)


# -- freshness (content fingerprints) -----------------------------------------


def test_recaptured_impl_frame_makes_diff_stale(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    _rewrite_same_pixels(ref / "frames" / "impl" / "active.png")
    write_manifest(ref, "impl")
    reasons = _reasons(ref)
    assert "frames/impl/active.png" in reasons["diff-stale"]
    assert "impl-frame-hash-mismatch" not in reasons


def test_touching_without_changing_is_not_stale(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    set_mtime(ref / "frames" / "impl" / "active.png", time.time())
    set_mtime(tmp_path / "impl" / "src" / "components" / "Hero.tsx", time.time())
    assert scoped_check.check(ref)["status"] == "passed"


def test_changed_impl_source_makes_diff_stale(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    source = tmp_path / "impl" / "src" / "components" / "Hero.tsx"
    source.write_text("export default function C() { return <div/> }\n")
    reasons = _reasons(ref)
    assert "Hero.tsx" in reasons["diff-stale"]


def test_new_source_named_after_target_makes_diff_stale(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    extra = tmp_path / "impl" / "src" / "hero" / "styles.css"
    extra.parent.mkdir(parents=True)
    extra.write_text("a{}")
    assert "new source(s)" in _reasons(ref)["diff-stale"]


def test_declared_impl_files_are_fingerprinted(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    other = tmp_path / "impl" / "src" / "styles" / "hero-extra.css"
    other.parent.mkdir(parents=True)
    other.write_text("a{}")
    produce_diff(ref, impl_files=["impl/src/styles/hero-extra.css"])
    assert scoped_check.check(ref)["status"] == "passed"
    other.write_text("a{color:red}")
    assert "hero-extra.css" in _reasons(ref)["diff-stale"]
    write_diff(ref, {**_diff(ref), "implFiles": ["impl/src/Missing.tsx"]})
    assert "impl-files-missing" in _codes(ref)


def test_impl_root_override(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path / "project")
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "src").mkdir(parents=True)
    (elsewhere / "src" / "Hero.tsx").write_text("x")
    assert scoped_check.check(ref)["status"] == "passed"
    assert scoped_check.main([str(ref), "--impl-root", str(elsewhere)]) == 1


# -- motion sequences (page-level video criteria) ------------------------------


def _ramp(steps: int, start: int = 0, end: int = 200) -> list[tuple[int, int, int]]:
    return [(start + (end - start) * i // (steps - 1), 40, 60) for i in range(steps)]


def _with_sequence(
    tmp_path: Path, ref_colors: list[tuple[int, int, int]], impl_colors: list[tuple[int, int, int]]
) -> Path:
    ref = build_scoped_evidence(tmp_path)
    write_sequence(ref, "ref", "frame", ref_colors)
    write_sequence(ref, "impl", "frame", impl_colors)
    write_manifest(ref, "ref")
    write_manifest(ref, "impl")
    produce_diff(ref)
    return ref


def test_motion_sequence_with_one_frame_phase_jitter_passes(tmp_path: Path) -> None:
    """Two independent recordings that start one frame apart are the same motion."""
    still = [(0, 40, 60)] * 3
    ref = _with_sequence(tmp_path, still + _ramp(12) + [(200, 40, 60)] * 3, still + [(0, 40, 60)] + _ramp(12) + [(200, 40, 60)] * 2)
    result = scoped_check.check(ref)
    assert result["status"] == "passed", result["failures"]
    verdict = result["sequences"]["frame"]
    assert verdict["impl_first"] == verdict["ref_first"] + 1 > 1
    assert verdict["min_ssim"] >= SSIM_THRESHOLD


def test_motion_sequence_with_different_content_fails(tmp_path: Path) -> None:
    still = [(0, 40, 60)] * 3
    ref = _with_sequence(tmp_path, still + _ramp(12) + [(200, 40, 60)] * 3, still + _ramp(12, end=60) + [(60, 40, 60)] * 3)
    reasons = _reasons(ref)
    assert "below SSIM 0.9" in reasons["motion-sequence-diverged"]


def test_motion_sequence_missing_on_impl_fails(tmp_path: Path) -> None:
    still = [(0, 40, 60)] * 3
    ref = _with_sequence(tmp_path, still + _ramp(12) + [(200, 40, 60)] * 3, [(0, 40, 60)] * 18)
    assert "one side has no detected motion" in _reasons(ref)["motion-sequence-diverged"]


def test_motion_sequence_arc_too_different_fails(tmp_path: Path) -> None:
    ref = _with_sequence(tmp_path, [(0, 40, 60)] * 2 + _ramp(60), [(0, 40, 60)] * 2 + _ramp(30) + [(200, 40, 60)] * 30)
    assert "arc timing" in _reasons(ref)["motion-sequence-diverged"]


def test_motion_sequence_verdict_is_cached_by_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    still = [(0, 40, 60)] * 3
    ref = _with_sequence(tmp_path, still + _ramp(12), still + _ramp(12))
    assert scoped_check.check(ref)["status"] == "passed"
    cache = json.loads((ref / ".scoped-check-cache.json").read_text())
    assert len(cache["sequences"]) == 1
    calls: list[int] = []

    def boom(*_: object, **__: object) -> dict[str, object]:
        calls.append(1)
        raise AssertionError("sequence should be served from cache")

    monkeypatch.setattr(scoped_check, "compare_sequence", boom)
    assert scoped_check.check(ref)["status"] == "passed"
    assert not calls
    # A changed frame invalidates the cache entry; a forged cache cannot pass it.
    write_sequence(ref, "impl", "frame", still + _ramp(12, end=60))
    write_manifest(ref, "impl")
    produce_diff(ref)
    with pytest.raises(AssertionError):
        scoped_check.check(ref)


# -- trigger-opened UI --------------------------------------------------------


def _modal(tmp_path: Path) -> Path:
    return build_scoped_evidence(tmp_path, "pricing-modal", modal=True, component="PricingModal.tsx")


def test_modal_missing_close_frames_fails(tmp_path: Path) -> None:
    ref = _modal(tmp_path)
    for side in ("ref", "impl"):
        for p in (ref / "frames" / side).glob("close-*.png"):
            p.unlink()
    assert "trigger-close-missing" in _codes(ref)


def test_modal_missing_impl_close_frames_fails(tmp_path: Path) -> None:
    ref = _modal(tmp_path)
    for p in (ref / "frames" / "impl").glob("close-*.png"):
        p.unlink()
    assert {"trigger-close-missing", "impl-frames-unmatched"} <= _codes(ref)


def test_modal_missing_close_recording_fails(tmp_path: Path) -> None:
    ref = _modal(tmp_path)
    (ref / "close.webm").unlink()
    assert "trigger-recording-missing" in _codes(ref)


def test_modal_missing_open_row_fails(tmp_path: Path) -> None:
    ref = _modal(tmp_path)
    data = _diff(ref)
    rows = [r for r in data["elements"] if isinstance(r, dict) and r["state"] != "open"]
    write_diff(ref, {**data, "elements": rows})
    assert {"trigger-open-missing", "diff-state-uncovered"} <= _codes(ref)


def test_dialog_role_marks_trigger_opened(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    target = ref / "element-target.json"
    target.write_text(json.dumps(element_target_payload(role="dialog")))
    set_mtime(target, time.time() - 100)
    result = scoped_check.check(ref)
    assert result["trigger_opened"] is True
    assert "trigger-close-missing" in {f["code"] for f in result["failures"]}


# -- hook wiring --------------------------------------------------------------


def _stop(root: Path, *, active: bool = False) -> str:
    result = run_hook(
        SECTION_GATE,
        stdin_data=json.dumps({"session_id": SESSION, "stop_hook_active": active}),
        env={"CLAUDE_PROJECT_DIR": str(root), "UI_CLONE_HOOK_HOST": "codex"},
    )
    assert result.returncode == 0, result.stderr
    return str(result.stdout) + str(result.stderr)


def _commit(root: Path) -> str:
    result = run_hook(
        "ui_clone.hooks.pre_bash",
        stdin_data=json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git commit -m 'hero clone'"},
                "session_id": SESSION,
                "cwd": str(root),
            }
        ),
        env={"CLAUDE_PROJECT_DIR": str(root)},
    )
    return str(result.stdout) + str(result.stderr)


def _break_impl_frame(ref: Path) -> None:
    _recapture_impl(ref, "active.png", (250, 0, 0))


def _fix_impl_frame(ref: Path) -> None:
    _recapture_impl(ref, "active.png", (10, 40, 60))


def test_stop_blocks_touched_scoped_run_until_check_passes(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    _break_impl_frame(ref)
    mark_ref_session(ref, SESSION, source="test")
    out = _stop(tmp_path)
    assert '"decision": "block"' in out, out
    assert "scoped completion gate" in out
    assert "frame-ae-nonzero" in out
    # Same failure again: the page-level repeat text, pointing at scoped_check.
    out = _stop(tmp_path)
    assert '"decision": "block"' in out, out
    assert "same failure as the last stop" in out
    assert f"python -m ui_clone.scoped_check {ref}" in out
    # Fixed and re-verified: the stop is allowed.
    _fix_impl_frame(ref)
    out = _stop(tmp_path)
    assert '"decision": "block"' not in out, out
    assert "scoped_check passed" in out


def test_stop_scoped_block_respects_retry_cap(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    _break_impl_frame(ref)
    mark_ref_session(ref, SESSION, source="test")
    for _ in range(3):
        assert '"decision": "block"' in _stop(tmp_path)
    out = _stop(tmp_path)
    assert '"decision": "block"' not in out, out
    assert "systemMessage" in out
    assert "element-target.json" in out


def test_stop_does_not_hold_capture_only_session(tmp_path: Path) -> None:
    """An unclaimed scoped dir with no writes from this session is not enforced."""
    ref = build_scoped_evidence(tmp_path)
    _break_impl_frame(ref)
    out = _stop(tmp_path)
    assert '"decision": "block"' not in out, out
    assert "scoped_check" in out


def test_stop_page_level_block_unchanged_beside_failing_scoped_run(tmp_path: Path) -> None:
    page = tmp_path / "tmp" / "ref" / "landing"
    page.mkdir(parents=True)
    write_extracted_json(page)
    set_active_marker(page)
    mark_ref_session(page, SESSION, source="test")
    build_scoped_evidence(tmp_path)
    out = _stop(tmp_path)
    assert '"decision": "block"' in out, out
    assert "scoped completion gate" not in out


def test_commit_denied_until_scoped_check_passes(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    _break_impl_frame(ref)
    mark_ref_session(ref, SESSION, source="test")
    out = _commit(tmp_path)
    assert '"deny"' in out, out
    assert "has not passed scoped_check" in out
    _fix_impl_frame(ref)
    assert '"deny"' not in _commit(tmp_path)
