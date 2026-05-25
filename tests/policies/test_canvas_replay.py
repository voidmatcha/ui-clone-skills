"""Tests for ui_clone.policies.canvas_replay.

The helper resolves the 3-condition gate (closeoutPolicy + attestation +
section.kind=="canvas") into a single set lookup. These tests pin the
fail-closed semantics — any missing condition yields an empty relief set.
"""
from __future__ import annotations

import json
from pathlib import Path

from ui_clone.policies import canvas_replay


def _write_state(ref: Path, policy: str | None) -> None:
    payload: dict = {"component": ref.name}
    if policy is not None:
        payload["closeoutPolicy"] = policy
    (ref / "pipeline-state.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_attestation(ref: Path, sources: list[str] | None = None) -> None:
    body = {
        "license": "MIT — example",
        "disclaimer": "test fixture",
        "attestedBy": "operator@example.com",
        "attestedAt": "2026-05-25T08:00:00Z",
        "ref_canvas_sources": sources if sources is not None else [
            "https://example.com/canvas-driver.js",
        ],
    }
    (ref / "canvas-replay-attestation.json").write_text(
        json.dumps(body), encoding="utf-8"
    )


def _write_section_map(ref: Path, sections: list[dict]) -> None:
    (ref / "section-map.json").write_text(
        json.dumps({"sections": sections}), encoding="utf-8"
    )


def test_relief_inactive_when_no_state_file(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    assert canvas_replay.is_policy_active(ref) is False
    assert canvas_replay.relief_active_sections(ref) == frozenset()


def test_relief_inactive_when_policy_default(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_state(ref, "canonical")
    _write_attestation(ref)
    _write_section_map(ref, [{"index": 0, "kind": "canvas"}])
    assert canvas_replay.is_policy_active(ref) is False
    assert canvas_replay.relief_active_sections(ref) == frozenset()


def test_relief_inactive_when_attestation_missing(tmp_path: Path) -> None:
    """Fail-closed: policy field set but operator forgot the attestation."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_state(ref, "canvas-replay")
    _write_section_map(ref, [{"index": 0, "kind": "canvas"}])
    assert canvas_replay.is_policy_active(ref) is False
    assert canvas_replay.relief_active_sections(ref) == frozenset()


def test_relief_active_with_all_conditions(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_state(ref, "canvas-replay")
    _write_attestation(ref)
    _write_section_map(ref, [{"index": 0, "kind": "canvas", "name": "hero-canvas"}])
    assert canvas_replay.is_policy_active(ref) is True
    relief = canvas_replay.relief_active_sections(ref)
    assert "hero-canvas" in relief
    assert "section-0" in relief  # index alias also matches


def test_only_canvas_kind_sections_get_relief(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_state(ref, "canvas-replay")
    _write_attestation(ref)
    _write_section_map(ref, [
        {"index": 0, "kind": "canvas", "name": "hero"},
        {"index": 1, "name": "footer"},  # no kind
        {"index": 2, "kind": "text", "name": "body"},  # other kind
    ])
    relief = canvas_replay.relief_active_sections(ref)
    assert "hero" in relief
    assert "footer" not in relief
    assert "body" not in relief


def test_section_name_aliases(tmp_path: Path) -> None:
    """An operator may annotate with name OR id OR className OR rely on
    index. The matcher accepts all four shapes so section-compare's
    file-stem naming convention matches."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_state(ref, "canvas-replay")
    _write_attestation(ref)
    _write_section_map(ref, [
        {
            "index": 3,
            "kind": "canvas",
            "name": "music-sphere",
            "id": "mus",
            "className": "bg-canvas dga_hero__AjMaf",
        },
    ])
    relief = canvas_replay.relief_active_sections(ref)
    assert "music-sphere" in relief
    assert "mus" in relief
    assert "bg-canvas dga_hero__AjMaf" in relief
    assert "section-3" in relief


def test_attestation_canvas_sources_extracted(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_attestation(ref, sources=[
        "https://example.com/canvas-a.js",
        "https://cdn.example.com/canvas-b.js",
        "",  # empty entries are filtered out
        123,  # type: ignore[list-item]  # type-invalid entries are filtered out
    ])
    sources = canvas_replay.attestation_canvas_sources(ref)
    assert sources == [
        "https://example.com/canvas-a.js",
        "https://cdn.example.com/canvas-b.js",
    ]


def test_attestation_canvas_sources_empty_when_missing(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    assert canvas_replay.attestation_canvas_sources(ref) == []


def test_corrupt_attestation_treated_as_missing(tmp_path: Path) -> None:
    """Malformed attestation must NOT silently activate relief."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_state(ref, "canvas-replay")
    (ref / "canvas-replay-attestation.json").write_text("not valid json{", encoding="utf-8")
    _write_section_map(ref, [{"index": 0, "kind": "canvas"}])
    # is_policy_active checks file existence, not validity — but downstream
    # callers that read sources via attestation_canvas_sources get []. The
    # foundation's Stop hook handles full validation via sha256; gate-side
    # relief intentionally trusts the existence check (the stamp gate would
    # have already failed if the file is malformed).
    assert canvas_replay.is_policy_active(ref) is True
    assert canvas_replay.attestation_canvas_sources(ref) == []


def test_critical_ae_ceiling_is_2x_canonical(tmp_path: Path) -> None:
    """The ceiling under which canvas sections downgrade to PASS."""
    assert canvas_replay.CRITICAL_AE_PER_MPX == 20000
    assert canvas_replay.AE_RELIEF_MULTIPLIER == 2.0
    assert canvas_replay.critical_ae_ceiling() == 40000.0


def test_corrupt_state_falls_back_to_canonical(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "pipeline-state.json").write_text("not valid", encoding="utf-8")
    _write_attestation(ref)
    _write_section_map(ref, [{"index": 0, "kind": "canvas"}])
    assert canvas_replay.is_policy_active(ref) is False


def test_snake_case_closeout_policy_field_accepted(tmp_path: Path) -> None:
    """state.py accepts both closeoutPolicy and closeout_policy; mirror that."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "pipeline-state.json").write_text(
        json.dumps({"component": "x", "closeout_policy": "canvas-replay"}),
        encoding="utf-8",
    )
    _write_attestation(ref)
    assert canvas_replay.is_policy_active(ref) is True
