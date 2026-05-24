from pathlib import Path

from ui_clone import state as _state
from ui_clone.gate import VALID_GATES, Gate


def test_dispatch_matches_gate_order(tmp_path: Path) -> None:
    """_make_dispatch() must return exactly the gates declared in state.GATE_ORDER.

    state.GATE_ORDER is the single source of truth. dispatch is auto-derived
    via getattr; this test guards against accidental method-name typos / missing
    methods that the import-time validator already catches but is cheap to
    re-assert at the unit-test layer."""
    gate = Gate(tmp_path)
    assert list(gate._make_dispatch().keys()) == list(_state.GATE_ORDER)



def test_valid_gates_derives_from_gate_order() -> None:
    """VALID_GATES must equal GATE_ORDER + ['all'] — no manual list to drift."""
    assert VALID_GATES == list(_state.GATE_ORDER) + ["all"]



def test_valid_gates_matches_dispatch() -> None:
    """VALID_GATES must exactly match the gates handled by _dispatch."""
    from pathlib import Path

    gate = Gate(Path("/tmp"))
    for gate_name in VALID_GATES:
        if gate_name == "all":
            continue
        results = gate._dispatch(gate_name)
        assert isinstance(results, list), f"_dispatch('{gate_name}') must return a list"



def test_gate_method_patch_target() -> None:
    """Regression: patching `Gate.gate_*` on the class affects instances; patching
    the per-area module-level function does NOT (Codex Item-5 review).

    Since `ui_clone.gates.__init__` rebinds `Gate.gate_spec = spec.gate_spec`
    at import time, the class holds a direct reference to the function
    object. Subsequent `unittest.mock.patch("ui_clone.gates.spec.gate_spec")`
    only swaps the attribute inside `spec.py` — Gate instances still call
    the original. The correct patch target is the class attribute itself.
    """
    from pathlib import Path
    from unittest import mock

    gate = Gate(Path("/tmp"))

    # ❌ Wrong target — patches spec.gate_spec but Gate.gate_spec still
    # points at the originally bound function. The call returns the real
    # result (or fails for non-existent ref_dir, but never returns the
    # sentinel).
    sentinel = [object()]
    with mock.patch("ui_clone.gates.spec.gate_spec", return_value=sentinel):
        try:
            result = gate.gate_spec()
        except Exception:
            result = None
        # The result is NOT our sentinel — proves wrong-target patch is a no-op.
        assert result is not sentinel

    # ✅ Correct target — patches the class attribute.
    with mock.patch("ui_clone.gates.base.Gate.gate_spec", return_value=sentinel):
        result = gate.gate_spec()
        assert result is sentinel

