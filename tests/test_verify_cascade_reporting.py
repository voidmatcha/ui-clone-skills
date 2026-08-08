"""Canonical verify must not report ordering cascades as independent failures.

The eBay Playbook run recorded
`reason: "canonical verify failed 4 gate(s): post-implement, boundary,
font-parity, section-compare"`. Only post-implement actually failed on its own
evidence — the other three exited non-zero solely because
`_check_pipeline_state_prerequisites` refuses to let a gate pass while an earlier
gate is incomplete. An agent reading that reason sees 4x the real work and loses
the one actionable defect.

The gate layer already makes this distinction internally ("1 real, 14
stale-artifact"); this pushes the same honesty up to the gate level, using the
structural signal (`missing_prerequisites`) rather than parsing gate stdout.
"""

from pathlib import Path

from ui_clone.pipeline_phases.verify import _split_root_cause_and_cascade
from ui_clone.state import PipelineState


def _state_with(tmp_path: Path, completed: list[str]) -> PipelineState:
    state = PipelineState.load(tmp_path)
    state.completed_steps = list(completed)
    return state


def test_ordering_cascade_is_separated_from_the_root_cause(tmp_path: Path) -> None:
    state = _state_with(
        tmp_path,
        ["reference", "extraction", "bundle", "paid-features", "spec",
         "pre-generate", "state-coverage"],
    )
    failures = ["post-implement", "boundary", "font-parity", "section-compare"]
    root, cascade = _split_root_cause_and_cascade(failures, state)
    assert root == ["post-implement"]
    assert cascade == ["boundary", "font-parity", "section-compare"]


def test_independent_failures_are_all_root_causes(tmp_path: Path) -> None:
    # Every prerequisite satisfied — nothing here is explained by ordering, so
    # both gates must keep counting as real failures.
    state = _state_with(
        tmp_path,
        ["reference", "extraction", "bundle", "paid-features", "spec",
         "pre-generate", "state-coverage", "post-implement", "boundary"],
    )
    root, cascade = _split_root_cause_and_cascade(["font-parity"], state)
    assert root == ["font-parity"]
    assert cascade == []


def test_no_failures_yields_empty_split(tmp_path: Path) -> None:
    state = _state_with(tmp_path, ["reference"])
    assert _split_root_cause_and_cascade([], state) == ([], [])
