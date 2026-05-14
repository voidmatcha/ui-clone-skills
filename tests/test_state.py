"""Tests for ui_clone.state — pipeline-state.json read/write."""

import json
from pathlib import Path

from ui_clone.state import GATE_ORDER, PipelineState

# ── GATE_ORDER ──


def test_gate_order_contains_all_gates() -> None:
    expected = [
        "reference",
        "extraction",
        "bundle",
        "paid-features",
        "spec",
        "pre-generate",
        "post-implement",
        "boundary",
        "font-parity",
        "section-compare",
    ]
    assert GATE_ORDER == expected


# ── PipelineState.load ──


def test_load_missing_file_returns_defaults(tmp_path: Path) -> None:
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState.load(ref_dir)
    assert state.current_gate == "reference"
    assert state.completed_steps == []


def test_load_existing_file(tmp_path: Path) -> None:
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    data = {
        "component": "Comp",
        "started_at": "2026-01-01T00:00:00Z",
        "completed_steps": ["reference", "extraction"],
        "current_gate": "bundle",
        "last_updated": "2026-01-01T01:00:00Z",
    }
    (ref_dir / "pipeline-state.json").write_text(json.dumps(data))
    state = PipelineState.load(ref_dir)
    assert state.current_gate == "bundle"
    assert state.completed_steps == ["reference", "extraction"]


def test_load_corrupted_json_returns_defaults(tmp_path: Path) -> None:
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    (ref_dir / "pipeline-state.json").write_text("not json{{{")
    state = PipelineState.load(ref_dir)
    assert state.current_gate == "reference"


# ── PipelineState.mark_passed ──


def test_mark_passed_advances_current_gate(tmp_path: Path) -> None:
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState.load(ref_dir)
    state.mark_passed("reference", ref_dir)
    reloaded = PipelineState.load(ref_dir)
    assert "reference" in reloaded.completed_steps
    assert reloaded.current_gate == "extraction"


def test_mark_passed_idempotent(tmp_path: Path) -> None:
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState.load(ref_dir)
    state.mark_passed("reference", ref_dir)
    state2 = PipelineState.load(ref_dir)
    state2.mark_passed("reference", ref_dir)
    state3 = PipelineState.load(ref_dir)
    assert state3.completed_steps.count("reference") == 1


def test_mark_passed_last_gate_sets_done(tmp_path: Path) -> None:
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    data = {
        "component": "Comp",
        "started_at": "2026-01-01T00:00:00Z",
        "completed_steps": list(GATE_ORDER[:-1]),
        "current_gate": "section-compare",
        "last_updated": "2026-01-01T01:00:00Z",
    }
    (ref_dir / "pipeline-state.json").write_text(json.dumps(data))
    state = PipelineState.load(ref_dir)
    state.mark_passed("section-compare", ref_dir)
    reloaded = PipelineState.load(ref_dir)
    assert reloaded.current_gate == "done"
    assert "section-compare" in reloaded.completed_steps


def test_mark_passed_writes_file(tmp_path: Path) -> None:
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState.load(ref_dir)
    state.mark_passed("reference", ref_dir)
    assert (ref_dir / "pipeline-state.json").exists()
    data = json.loads((ref_dir / "pipeline-state.json").read_text())
    assert data["current_gate"] == "extraction"


def test_mark_passed_does_not_regress_gate(tmp_path: Path) -> None:
    """Calling mark_passed on an earlier gate must not move current_gate backwards.

    Regression test for: out-of-order mark_passed() regressing current_gate.
    """
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    # Advance to "bundle"
    state = PipelineState.load(ref_dir)
    state.mark_passed("reference", ref_dir)
    state = PipelineState.load(ref_dir)
    state.mark_passed("extraction", ref_dir)
    state = PipelineState.load(ref_dir)
    assert state.current_gate == "bundle"

    # Re-run an earlier gate (e.g. reference re-checked)
    state.mark_passed("reference", ref_dir)
    reloaded = PipelineState.load(ref_dir)
    # Must stay at "bundle", not regress to "extraction"
    assert reloaded.current_gate == "bundle"


def test_mark_passed_does_not_regress_from_done(tmp_path: Path) -> None:
    """current_gate='done' must not be overwritten by mark_passed on any gate."""
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    data = {
        "component": "Comp",
        "started_at": "2026-01-01T00:00:00Z",
        "completed_steps": list(GATE_ORDER),
        "current_gate": "done",
        "last_updated": "2026-01-01T02:00:00Z",
    }
    (ref_dir / "pipeline-state.json").write_text(json.dumps(data))
    state = PipelineState.load(ref_dir)
    # Re-mark an earlier gate — must not regress from "done"
    state.mark_passed("reference", ref_dir)
    reloaded = PipelineState.load(ref_dir)
    assert reloaded.current_gate == "done"


# ── PipelineState.demote_to ──


def test_demote_to_from_done_moves_back_to_section_compare(tmp_path: Path) -> None:
    """When state is 'done', demote_to('section-compare') retreats current_gate
    and removes section-compare from completed_steps."""
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    data = {
        "component": "Comp",
        "started_at": "2026-01-01T00:00:00Z",
        "completed_steps": list(GATE_ORDER),
        "current_gate": "done",
        "last_updated": "2026-01-01T02:00:00Z",
    }
    (ref_dir / "pipeline-state.json").write_text(json.dumps(data))
    state = PipelineState.load(ref_dir)
    state.demote_to("section-compare", ref_dir)
    reloaded = PipelineState.load(ref_dir)
    assert reloaded.current_gate == "section-compare"
    assert "section-compare" not in reloaded.completed_steps
    # Earlier gates remain completed
    assert "post-implement" in reloaded.completed_steps
    assert "pre-generate" in reloaded.completed_steps


def test_demote_to_does_not_advance(tmp_path: Path) -> None:
    """demote_to must never move current_gate forward — only backward or stay."""
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState.load(ref_dir)
    # Currently at "reference" — demote_to("section-compare") must not advance
    state.demote_to("section-compare", ref_dir)
    reloaded = PipelineState.load(ref_dir)
    assert reloaded.current_gate == "reference"


def test_demote_to_unknown_gate_is_noop(tmp_path: Path) -> None:
    """demote_to with a gate not in GATE_ORDER → no state change."""
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    data = {
        "component": "Comp",
        "started_at": "2026-01-01T00:00:00Z",
        "completed_steps": list(GATE_ORDER),
        "current_gate": "done",
        "last_updated": "2026-01-01T02:00:00Z",
    }
    (ref_dir / "pipeline-state.json").write_text(json.dumps(data))
    state = PipelineState.load(ref_dir)
    state.demote_to("nonexistent-gate", ref_dir)
    reloaded = PipelineState.load(ref_dir)
    assert reloaded.current_gate == "done"


# ── PipelineState.mark_failed ──


def test_mark_failed_bumps_counter(tmp_path: Path) -> None:
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="Comp", current_gate="reference")
    state.mark_failed("reference", ref_dir)
    state.mark_failed("reference", ref_dir)
    reloaded = PipelineState.load(ref_dir)
    assert reloaded.gate_fail_counts == {"reference": 2}


def test_mark_failed_ignores_non_active_gate(tmp_path: Path) -> None:
    """Bumping a gate that is NOT current_gate is a no-op.

    Why: a stale `python -m ui_clone.gate <c> reference` after the pipeline
    advanced to `extraction` shouldn't pollute the stuck counter for the
    active gate.
    """
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="Comp", current_gate="extraction")
    state.mark_failed("reference", ref_dir)
    assert state.gate_fail_counts == {}
    # No file written either, since nothing changed.
    assert not (ref_dir / "pipeline-state.json").exists()


def test_mark_passed_resets_fail_counter(tmp_path: Path) -> None:
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="Comp", current_gate="reference")
    state.mark_failed("reference", ref_dir)
    state.mark_failed("reference", ref_dir)
    state.mark_passed("reference", ref_dir)
    reloaded = PipelineState.load(ref_dir)
    assert reloaded.gate_fail_counts == {}
    assert "reference" in reloaded.completed_steps
    assert reloaded.current_gate == "extraction"


# ── PipelineState.record_unclonable ──


def test_record_unclonable_appends_entry(tmp_path: Path) -> None:
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="Comp", current_gate="paid-features")
    state.record_unclonable(
        "paid-features",
        "Helvetica Now Display has no free substitution",
        ref_dir,
        detail={"family": "Helvetica Now Display"},
    )
    reloaded = PipelineState.load(ref_dir)
    assert len(reloaded.unclonable_reasons) == 1
    entry = reloaded.unclonable_reasons[0]
    assert entry["gate"] == "paid-features"
    assert "Helvetica" in entry["reason"]
    assert entry["detail"] == {"family": "Helvetica Now Display"}
    assert "detected_at" in entry


def test_record_unclonable_is_idempotent(tmp_path: Path) -> None:
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="Comp", current_gate="paid-features")
    state.record_unclonable("paid-features", "same reason", ref_dir)
    state.record_unclonable("paid-features", "same reason", ref_dir)
    reloaded = PipelineState.load(ref_dir)
    assert len(reloaded.unclonable_reasons) == 1
