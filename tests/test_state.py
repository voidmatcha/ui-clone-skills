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


def test_record_unclonable_is_idempotent(tmp_path: Path) -> None:
    ref_dir = tmp_path / "comp"
    ref_dir.mkdir()
    state = PipelineState(component="Comp", current_gate="paid-features")
    state.record_unclonable("paid-features", "same reason", ref_dir)
    state.record_unclonable("paid-features", "same reason", ref_dir)
    reloaded = PipelineState.load(ref_dir)
    assert len(reloaded.unclonable_reasons) == 1


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
        ("post-implement", "class-signature-preservation-mismatch"),
        ("section-compare", "hard-cap-fail"),
        ("font-parity", "subpixel-rendering-diff"),
    ]
    for gate, category in pairs:
        suggestions = suggest_fallbacks(gate, category)
        assert suggestions, f"empty suggestions for ({gate}, {category})"
        assert all(isinstance(s, str) and len(s) > 10 for s in suggestions)
