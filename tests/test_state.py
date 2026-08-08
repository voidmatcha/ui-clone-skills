"""Tests for ui_clone.state — pipeline-state.json read/write."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

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
        "state-coverage",
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


def test_load_corrupted_json_quarantines_file(
    tmp_path: Path, capsys: "pytest.CaptureFixture[str]"
) -> None:
    """Codex review (2026-05-24): corrupt pipeline-state.json must not silently
    erase terminal/abort state (unclonable_reasons, completed_steps). Quarantine
    the corrupt bytes under a timestamped name so audit can recover them; warn
    on stderr so operators don't lose the signal."""
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state_file = ref_dir / "pipeline-state.json"
    state_file.write_text("not json{{{")

    state = PipelineState.load(ref_dir)

    # Original file is moved aside, not overwritten or silently kept-and-ignored.
    assert not state_file.exists(), "corrupt file should be renamed to quarantine"
    quarantined = sorted(ref_dir.glob("pipeline-state.json.corrupt.*"))
    assert len(quarantined) == 1, f"expected exactly one quarantine file, got {quarantined}"
    assert quarantined[0].read_text() == "not json{{{", "quarantine preserves original bytes"

    captured = capsys.readouterr()
    assert "corrupt" in captured.err.lower(), "stderr should warn about corruption"
    assert "quarantine" in captured.err.lower(), "stderr should name the quarantine action"

    # Existing behavior preserved: state falls back to safe defaults.
    assert state.current_gate == "reference"
    # Codex P0 (2026-05-27): load_failed flag is set so callers (mark_failed)
    # can distinguish "no state ever existed" from "state was corrupted and
    # quarantined". Without this, mark_failed would silently bail on
    # current_gate mismatch and the hard-cap auto-record would never fire
    # against a stuck-with-corrupted-state pipeline.
    assert state.load_failed is True, (
        "PipelineState.load must set load_failed=True after quarantining "
        "a corrupt pipeline-state.json"
    )


def test_load_clean_state_has_load_failed_false(tmp_path: Path) -> None:
    """Sanity check: load_failed is False on a valid load and on a missing
    file. Only True after quarantine."""
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()

    # 1. Missing pipeline-state.json — load_failed must be False (no quarantine).
    state_missing = PipelineState.load(ref_dir)
    assert state_missing.load_failed is False

    # 2. Valid pipeline-state.json — load_failed must be False.
    (ref_dir / "pipeline-state.json").write_text(
        '{"component": "comp", "current_gate": "reference", "completed_steps": []}'
    )
    state_valid = PipelineState.load(ref_dir)
    assert state_valid.load_failed is False


def test_mark_failed_after_state_corruption_records_terminal_unclonable(
    tmp_path: Path, capsys: "pytest.CaptureFixture[str]"
) -> None:
    """Codex P0 (2026-05-27) fail-closed fix: when pipeline-state.json was
    corrupted and quarantined, mark_failed must NOT silently bail on the
    current_gate mismatch (fresh state has current_gate='reference'; caller
    passed e.g. 'post-implement'). The previous behavior allowed the
    pipeline to drive on stale context with hard-cap never firing. Fix
    records 'state-corruption' unclonable so termination is explicit.
    """
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    # Plant a corrupt state file. Next PipelineState.load() will quarantine
    # it and return a fresh state with load_failed=True.
    (ref_dir / "pipeline-state.json").write_text("not json{{{")

    # Caller is operating on a stale in-memory state thinking current_gate
    # is 'post-implement' (typical reality after many gate runs).
    stale = PipelineState(component="comp", current_gate="post-implement")
    stale.mark_failed("post-implement", ref_dir)
    # Drain the quarantine stderr warning so it doesn't leak.
    capsys.readouterr()

    # Reload to see what's persisted on disk.
    reloaded = PipelineState.load(ref_dir)
    state_corruption = [
        e for e in reloaded.unclonable_reasons
        if isinstance(e, dict) and e.get("category") == "state-corruption"
    ]
    assert state_corruption, (
        f"mark_failed after quarantine must record state-corruption "
        f"unclonable; got: {reloaded.unclonable_reasons}"
    )
    # Ensure the reason mentions the gate the caller was trying to bump,
    # so operator can correlate with their pipeline driver's last invocation.
    assert "post-implement" in state_corruption[0].get("reason", ""), (
        "state-corruption reason should name the gate the caller passed"
    )


# ── PipelineState.closeout_policy (structural-convergence opt-in) ──


def test_closeout_policy_default_is_canonical(tmp_path: Path) -> None:
    """Fresh state defaults to canonical closeout — the strict verify-stamp.json
    path used by every existing pipeline run. Adding the new field MUST NOT
    silently change behavior for callers that never set it."""
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState.load(ref_dir)
    assert state.closeout_policy == "canonical"


def test_closeout_policy_loaded_from_disk(tmp_path: Path) -> None:
    """A plan that opts into structural closeout writes closeoutPolicy=structural
    into pipeline-state.json; reload must surface it. closeoutPolicy field uses
    camelCase on-disk to match implRoot precedent."""
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    (ref_dir / "pipeline-state.json").write_text(json.dumps({
        "component": "Comp",
        "current_gate": "section-compare",
        "completed_steps": list(GATE_ORDER),
        "closeoutPolicy": "structural",
    }))
    state = PipelineState.load(ref_dir)
    assert state.closeout_policy == "structural"


def test_closeout_policy_survives_save_roundtrip(tmp_path: Path) -> None:
    """When PipelineState is in-memory-mutated to structural and saved via any
    of the write paths (save/mark_passed/mark_failed/record_unclonable/
    demote_to), the field must persist."""
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState.load(ref_dir)
    state.closeout_policy = "structural"
    state.save(ref_dir)
    on_disk = json.loads((ref_dir / "pipeline-state.json").read_text())
    assert on_disk.get("closeoutPolicy") == "structural"
    reloaded = PipelineState.load(ref_dir)
    assert reloaded.closeout_policy == "structural"


def test_closeout_policy_canonical_omitted_from_disk(tmp_path: Path) -> None:
    """Default 'canonical' policy is omitted from on-disk JSON to keep legacy
    state files compact and avoid spurious diff churn on every save. Same
    optionality pattern as impl_root."""
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState.load(ref_dir)  # closeout_policy='canonical' (default)
    state.save(ref_dir)
    on_disk = json.loads((ref_dir / "pipeline-state.json").read_text())
    assert "closeoutPolicy" not in on_disk, (
        f"Default policy should be omitted to avoid diff churn. got={on_disk}"
    )


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


def test_mark_failed_at_hard_cap_auto_records_unclonable(tmp_path: Path) -> None:
    """When mark_failed() bumps gate_fail_counts to HARD_CAP_GATE_FAILS, the
    state automatically appends an unclonable_reasons entry with
    category='hard-cap-fail'. Closes the gap that left abort_banner firing in
    goal.py while pipeline-state.json had no canonical reason — the Stop hook
    then re-enforced the same gate forever and external drivers needed a human
    to call record_unclonable manually (linear-app: 97 fails, realfood-gov: 6
    fails, both saturated without self-termination).

    PipelineState owns the counter, so terminal transition belongs to the
    write point — not goal.py (read-side) and not section_gate.py (which
    would need a parallel fail-count interpretation).
    """
    from ui_clone.state import HARD_CAP_GATE_FAILS

    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="comp", current_gate="post-implement")

    for _ in range(HARD_CAP_GATE_FAILS - 1):
        state.mark_failed("post-implement", ref_dir)
    assert state.unclonable_reasons == []

    state.mark_failed("post-implement", ref_dir)
    assert state.gate_fail_counts["post-implement"] == HARD_CAP_GATE_FAILS
    assert len(state.unclonable_reasons) == 1
    entry = state.unclonable_reasons[0]
    assert entry["gate"] == "post-implement"
    assert entry["category"] == "hard-cap-fail"
    # Reason must mention the cap so a human reading pipeline-state.json
    # understands why the run was terminated.
    assert "hard" in entry["reason"].lower() and "cap" in entry["reason"].lower()
    assert str(HARD_CAP_GATE_FAILS) in entry["reason"]
    assert state.terminal_state["status"] == "unclonable"
    assert state.terminal_state["category"] == "hard-cap-fail"


def test_mark_failed_past_cap_does_not_duplicate_unclonable(tmp_path: Path) -> None:
    """Subsequent mark_failed() bumps after the cap is reached must not append
    duplicate entries. record_unclonable()'s built-in (gate, reason) dedup
    handles this, but the test pins the contract so a future refactor of the
    auto-record reason string can't silently regress to N-duplicates."""
    from ui_clone.state import HARD_CAP_GATE_FAILS

    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="comp", current_gate="extraction")
    for _ in range(HARD_CAP_GATE_FAILS + 5):
        state.mark_failed("extraction", ref_dir)
    assert len(state.unclonable_reasons) == 1


def test_mark_failed_auto_unclonable_persists_to_disk(tmp_path: Path) -> None:
    """The auto-recorded entry must survive a load roundtrip so the Stop hook's
    PipelineState.load() sees it on the next invocation. Without persistence,
    the Stop hook would still re-enforce on the next response because it loads
    from disk, not from the live state object."""
    from ui_clone.state import HARD_CAP_GATE_FAILS

    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="comp", current_gate="extraction")
    for _ in range(HARD_CAP_GATE_FAILS):
        state.mark_failed("extraction", ref_dir)

    reloaded = PipelineState.load(ref_dir)
    assert len(reloaded.unclonable_reasons) == 1
    entry = reloaded.unclonable_reasons[0]
    assert entry["gate"] == "extraction"
    assert entry["category"] == "hard-cap-fail"
    assert reloaded.terminal_state["status"] == "unclonable"
    assert reloaded.terminal_state["gate"] == "extraction"


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


def test_mark_passed_rejects_out_of_order_gate(tmp_path: Path) -> None:
    """A later gate must not be recorded before all earlier gates passed.

    Loop feedback: a state file with current_gate='post-implement' but no
    reference/extraction completion let agents start closing out a partially
    traversed pipeline. State writes must preserve the gate prefix invariant.
    """
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState.load(ref_dir)

    state.mark_passed("post-implement", ref_dir)

    reloaded = PipelineState.load(ref_dir)
    assert reloaded.current_gate == "reference"
    assert "post-implement" not in reloaded.completed_steps


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
    assert reloaded.terminal_state["status"] == "unclonable"
    assert reloaded.terminal_state["gate"] == "paid-features"


def test_record_unclonable_is_idempotent(tmp_path: Path) -> None:
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="Comp", current_gate="paid-features")
    state.record_unclonable("paid-features", "same reason", ref_dir)
    state.record_unclonable("paid-features", "same reason", ref_dir)
    reloaded = PipelineState.load(ref_dir)
    assert len(reloaded.unclonable_reasons) == 1
    assert reloaded.terminal_state["status"] == "unclonable"


def test_mark_terminal_roundtrips_and_clear_removes_state(tmp_path: Path) -> None:
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="Comp", current_gate="post-implement")
    state.mark_terminal(
        ref_dir,
        status="failed",
        category="canonical-verify-failed",
        gate="post-implement",
        reason="canonical verify failed 1 gate: post-implement",
        detail={"failed_gates": ["post-implement"]},
        next_action="read verify-report.json and patch failures",
    )

    reloaded = PipelineState.load(ref_dir)
    assert reloaded.terminal_state["status"] == "failed"
    assert reloaded.terminal_state["category"] == "canonical-verify-failed"
    assert reloaded.terminal_state["detail"] == {"failed_gates": ["post-implement"]}
    assert reloaded.terminal_state["next_action"].startswith("read verify")

    reloaded.clear_terminal(ref_dir)
    cleared = PipelineState.load(ref_dir)
    assert cleared.terminal_state == {}
    raw = json.loads((ref_dir / "pipeline-state.json").read_text(encoding="utf-8"))
    assert "terminalState" not in raw


def test_mark_terminal_self_attested_pins_result_sha(tmp_path: Path) -> None:
    """Item 5: a self-attested terminal write (default written_by='cli') records
    writtenBy + a sectionsResultSha256 pin when sections/result.txt exists — for
    ALL statuses, so unclonable/failed via the CLI are bound too (hole-1)."""
    import hashlib

    for status in ("incomplete", "abandoned", "unclonable", "failed"):
        ref_dir = tmp_path / status
        (ref_dir / "sections").mkdir(parents=True)
        (ref_dir / "sections" / "result.txt").write_text("**Result: 1 PASS**\n")
        PipelineState(component="C", current_gate="post-implement").mark_terminal(
            ref_dir, status=status, category="x", reason="y"
        )
        t = PipelineState.load(ref_dir).terminal_state
        assert t["writtenBy"] == "cli"
        expected = hashlib.sha256(
            (ref_dir / "sections" / "result.txt").read_bytes()
        ).hexdigest()
        assert t["sectionsResultSha256"] == expected


def test_mark_terminal_pipeline_provenance_is_unpinned(tmp_path: Path) -> None:
    """A gate-bound write (written_by='pipeline') is exempt from the pin."""
    ref_dir = tmp_path / "comp"
    (ref_dir / "sections").mkdir(parents=True)
    (ref_dir / "sections" / "result.txt").write_text("x\n")
    PipelineState(component="C", current_gate="post-implement").mark_terminal(
        ref_dir, status="failed", category="x", reason="y", written_by="pipeline"
    )
    t = PipelineState.load(ref_dir).terminal_state
    assert t["writtenBy"] == "pipeline"
    assert "sectionsResultSha256" not in t


# ── Step G: fallback_suggestions on unclonable_reasons ──


def test_record_unclonable_with_category_autopopulates_fallbacks(tmp_path: Path) -> None:
    """Passing a known category resolves to canonical fallback_suggestions."""
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="Comp", current_gate="post-implement")
    state.record_unclonable(
        "post-implement",
        "Reference renders inside a paywalled canvas.",
        ref_dir,
        category="drm-canvas",
    )
    reloaded = PipelineState.load(ref_dir)
    entry = reloaded.unclonable_reasons[0]
    assert entry["category"] == "drm-canvas"
    assert "fallback_suggestions" in entry
    fallbacks = entry["fallback_suggestions"]
    assert isinstance(fallbacks, list) and len(fallbacks) >= 2
    assert any("SVG/PNG placeholder" in s for s in fallbacks)


def test_record_unclonable_explicit_fallbacks_wins(tmp_path: Path) -> None:
    """Explicit fallback_suggestions list overrides category defaults."""
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="Comp", current_gate="post-implement")
    custom = ["Custom suggestion 1", "Custom suggestion 2"]
    state.record_unclonable(
        "post-implement",
        "site uses a paid React component library",
        ref_dir,
        category="drm-canvas",
        fallback_suggestions=custom,
    )
    reloaded = PipelineState.load(ref_dir)
    entry = reloaded.unclonable_reasons[0]
    assert entry["fallback_suggestions"] == custom


def test_record_unclonable_no_category_no_fallbacks(tmp_path: Path) -> None:
    """Backward-compat: legacy callers without category get no fallback field."""
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="Comp", current_gate="paid-features")
    state.record_unclonable("paid-features", "legacy reason", ref_dir)
    reloaded = PipelineState.load(ref_dir)
    entry = reloaded.unclonable_reasons[0]
    assert "category" not in entry
    assert "fallback_suggestions" not in entry


def test_record_unclonable_unknown_category_no_autopopulate(tmp_path: Path) -> None:
    """Unknown category records the field but no suggestions auto-added."""
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="Comp", current_gate="post-implement")
    state.record_unclonable(
        "post-implement",
        "novel failure shape",
        ref_dir,
        category="undocumented-novel-failure",
    )
    reloaded = PipelineState.load(ref_dir)
    entry = reloaded.unclonable_reasons[0]
    assert entry["category"] == "undocumented-novel-failure"
    assert "fallback_suggestions" not in entry


def test_suggest_fallbacks_returns_copy() -> None:
    """Caller can mutate returned list without affecting the canonical table."""
    from ui_clone.state import suggest_fallbacks
    a = suggest_fallbacks("post-implement", "drm-canvas")
    a.append("MUTATED")
    b = suggest_fallbacks("post-implement", "drm-canvas")
    assert "MUTATED" not in b, "internal table was leaked"


def test_suggest_fallbacks_known_categories() -> None:
    """Each curated entry returns a non-empty list of usable strings."""
    from ui_clone.state import suggest_fallbacks
    pairs = [
        ("paid-features", "commercial-font"),
        ("post-implement", "drm-canvas"),
        ("post-implement", "auth-gated"),
        ("section-compare", "hard-cap-fail"),
        ("font-parity", "subpixel-rendering-diff"),
    ]
    for gate, category in pairs:
        suggestions = suggest_fallbacks(gate, category)
        assert suggestions, f"empty suggestions for ({gate}, {category})"
        assert all(isinstance(s, str) and len(s) > 10 for s in suggestions)


# ── PipelineState write serialization (codex review 2026-05-24) ──


def test_pipeline_state_lock_helper_exists() -> None:
    """The lock context-manager backing mark_*/record_unclonable serialization
    must be importable from ui_clone.state. Lifted pattern from
    ui_clone.driver_session — same fcntl.flock semantics."""
    from ui_clone.state import _pipeline_state_lock
    assert callable(_pipeline_state_lock)


def test_concurrent_mark_failed_does_not_lose_updates(tmp_path: Path) -> None:
    """Two subprocesses each call mark_failed once. Without the RMW lock,
    both processes load the same gate_fail_counts, both compute +1, both
    write — one rename wins, the other increment is lost. With the lock,
    the second writer reloads under the lock and sees the first writer's
    +1, ending at 2.

    Codex review (2026-05-24): "All write pipeline-state.json via the same
    temp path without locking, so concurrent gate processes can lose
    completed_steps, fail counts, or abort reasons."
    """
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    # Seed state so current_gate matches what we will bump.
    seed = {
        "component": "comp",
        "current_gate": "reference",
        "completed_steps": [],
        "gate_fail_counts": {},
        "unclonable_reasons": [],
    }
    (ref_dir / "pipeline-state.json").write_text(json.dumps(seed))

    code = (
        "from pathlib import Path; "
        "from ui_clone.state import PipelineState; "
        f"d = Path({str(ref_dir)!r}); "
        "ps = PipelineState.load(d); "
        "ps.mark_failed('reference', d)"
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", code])
        for _ in range(2)
    ]
    for p in procs:
        p.wait(timeout=15)
        assert p.returncode == 0

    final = PipelineState.load(ref_dir)
    assert final.gate_fail_counts.get("reference") == 2, (
        f"lost update: expected counter=2 after two concurrent mark_failed "
        f"calls; got {final.gate_fail_counts!r}"
    )


def test_concurrent_record_unclonable_dedupes_across_processes(tmp_path: Path) -> None:
    """record_unclonable's (gate, reason) dedup must survive concurrent
    writers. Without the lock, both processes load empty unclonable_reasons,
    both append their entry, and the file ends with 2 identical entries
    (dedup runs in-memory pre-write, so it's blind to the parallel writer).
    """
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    seed = {
        "component": "comp",
        "current_gate": "reference",
        "completed_steps": [],
        "gate_fail_counts": {},
        "unclonable_reasons": [],
    }
    (ref_dir / "pipeline-state.json").write_text(json.dumps(seed))

    code = (
        "from pathlib import Path; "
        "from ui_clone.state import PipelineState; "
        f"d = Path({str(ref_dir)!r}); "
        "ps = PipelineState.load(d); "
        "ps.record_unclonable("
        "gate='reference', reason='duplicate-test', ref_dir=d)"
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", code])
        for _ in range(2)
    ]
    for p in procs:
        p.wait(timeout=15)
        assert p.returncode == 0

    final = PipelineState.load(ref_dir)
    reasons = [
        (r.get("gate"), r.get("reason"))
        for r in final.unclonable_reasons
    ]
    assert reasons.count(("reference", "duplicate-test")) == 1, (
        f"dedup failed under concurrency: got {reasons!r}"
    )


def test_recover_hard_cap_clears_only_hard_cap_reasons(tmp_path: Path) -> None:
    """recover_hard_cap lifts a hard-cap termination (constraint resolved
    outside the impl loop) but refuses to clear genuine content blockers."""
    from ui_clone.state import HARD_CAP_GATE_FAILS, HARD_CAP_REASON_TEMPLATE

    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    state = PipelineState(component="c")
    state.gate_fail_counts["post-implement"] = HARD_CAP_GATE_FAILS
    state.unclonable_reasons.append({
        "gate": "post-implement",
        "reason": HARD_CAP_REASON_TEMPLATE.format(
            gate="post-implement", cap=HARD_CAP_GATE_FAILS
        ),
        "category": "hard-cap-fail",
    })
    state.save(ref_dir)

    changed = state.recover_hard_cap(
        "post-implement", "ref crops re-captured idle; fonts wired", ref_dir
    )
    assert changed

    final = PipelineState.load(ref_dir)
    assert final.unclonable_reasons == []
    assert "post-implement" not in final.gate_fail_counts
    assert len(final.recoveries) == 1
    audit = final.recoveries[0]
    assert audit["gate"] == "post-implement"
    assert audit["operator_reason"].startswith("ref crops re-captured")
    assert audit["cleared"][0]["category"] == "hard-cap-fail"


def test_recover_hard_cap_refuses_content_blockers_without_force(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    state = PipelineState(component="c")
    state.unclonable_reasons.append({
        "gate": "paid-features",
        "reason": "paid font with no substitution",
        "category": "paid-font",
    })
    state.save(ref_dir)

    import pytest

    with pytest.raises(ValueError, match="non-hard-cap"):
        state.recover_hard_cap("paid-features", "trying anyway", ref_dir)

    final = PipelineState.load(ref_dir)
    assert len(final.unclonable_reasons) == 1
    assert final.recoveries == []


def test_recover_hard_cap_requires_reason(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    state = PipelineState(component="c")
    state.save(ref_dir)

    import pytest

    with pytest.raises(ValueError, match="operator reason"):
        state.recover_hard_cap("post-implement", "   ", ref_dir)


def test_mark_failed_signature_change_resets_consecutive_counter(
    tmp_path: Path,
) -> None:
    """A failure with a DIFFERENT failure_signature resets the consecutive
    counter to 1 — the failing-check set changed, so the run is converging
    (or facing a new blocker), not retrying the same action. Validated on a
    live E2E run: per-turn Stop-hook gate evaluations of an actively
    iterating agent (failing set shrinking 53→27→16 checks) accumulated 10
    'consecutive' fails and falsely terminated a clonable run."""
    from ui_clone.state import HARD_CAP_GATE_FAILS

    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="comp", current_gate="post-implement")

    # Far more than the cap in total — but the signature changes every time,
    # so the consecutive counter never exceeds 1 and no hard cap fires.
    for i in range(HARD_CAP_GATE_FAILS + 3):
        state.mark_failed("post-implement", ref_dir, failure_signature=f"sig-{i}")
    assert state.gate_fail_counts["post-implement"] == 1
    assert state.unclonable_reasons == []

    # Identical signature N consecutive times → cap fires as before.
    for _ in range(HARD_CAP_GATE_FAILS):
        state.mark_failed("post-implement", ref_dir, failure_signature="stuck")
    assert state.gate_fail_counts["post-implement"] == HARD_CAP_GATE_FAILS
    assert any(
        r.get("category") == "hard-cap-fail" for r in state.unclonable_reasons
    )


def test_mark_failed_no_signature_keeps_legacy_counting(tmp_path: Path) -> None:
    """Legacy callers that pass no failure_signature keep the old semantics:
    every blocked run increments the consecutive counter."""
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="comp", current_gate="extraction")
    for _ in range(3):
        state.mark_failed("extraction", ref_dir)
    assert state.gate_fail_counts["extraction"] == 3


def test_mark_failed_absolute_cap_stops_signature_cycling(tmp_path: Path) -> None:
    """An agent cycling between alternating failure sets (A/B/A/B…) resets
    the consecutive counter forever, so a secondary absolute total cap
    terminates the run with a distinct reason."""
    from ui_clone.state import ABSOLUTE_CAP_GATE_FAILS

    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="comp", current_gate="post-implement")
    for i in range(ABSOLUTE_CAP_GATE_FAILS):
        state.mark_failed(
            "post-implement", ref_dir, failure_signature=("A" if i % 2 else "B")
        )
    assert state.gate_total_fail_counts["post-implement"] == ABSOLUTE_CAP_GATE_FAILS
    reasons = [r["reason"] for r in state.unclonable_reasons]
    assert any("absolute cap" in r for r in reasons)


def test_recover_hard_cap_clears_signature_state(tmp_path: Path) -> None:
    """recover_hard_cap must reset the signature + total counters along with
    the consecutive counter, so a recovered run starts counting fresh."""
    from ui_clone.state import HARD_CAP_GATE_FAILS

    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="comp", current_gate="post-implement")
    for _ in range(HARD_CAP_GATE_FAILS):
        state.mark_failed("post-implement", ref_dir, failure_signature="stuck")
    assert state.unclonable_reasons

    assert state.recover_hard_cap(
        "post-implement", "false cap: tooling bug validated in E2E run", ref_dir
    )
    assert "post-implement" not in state.gate_fail_counts
    assert "post-implement" not in state.gate_fail_signatures
    assert "post-implement" not in state.gate_total_fail_counts
    assert state.recoveries


def test_signature_counters_roundtrip_disk(tmp_path: Path) -> None:
    """gate_fail_signatures / gate_total_fail_counts survive a save+load."""
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="comp", current_gate="extraction")
    state.mark_failed("extraction", ref_dir, failure_signature="s1")
    loaded = PipelineState.load(ref_dir)
    assert loaded.gate_fail_signatures["extraction"] == "s1"
    assert loaded.gate_total_fail_counts["extraction"] == 1
