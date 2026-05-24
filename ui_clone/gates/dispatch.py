"""Gate dispatch + CLI entry point.

`Gate.run()` lives here. The dispatch table is derived from
`state.GATE_ORDER` so adding a new gate is a 2-step change: (1) add to
GATE_ORDER, (2) add a `gate_<name>` method to Gate (via one of the
ui_clone/gates/<area>.py modules). The import-time validator in
`ui_clone.gates.__init__` catches any drift.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ui_clone import state as _state
from ui_clone.hooks._common import GREEN as _GREEN
from ui_clone.hooks._common import NC as _NC
from ui_clone.hooks._common import RED as _RED
from ui_clone.hooks._common import YELLOW as _YELLOW

from .base import CheckResult, Gate

VALID_GATES = list(_state.GATE_ORDER) + ["all"]


def _gate_method_name(gate: str) -> str:
    """Map gate name (kebab-case) to Gate method name (snake_case)."""
    return f"gate_{gate.replace('-', '_')}"


def _make_dispatch(self: Gate) -> dict[str, Any]:
    """Build {gate_name: bound_method} from state.GATE_ORDER.

    Method names follow the convention `gate_<name>` with `-` → `_`. The
    import-time validator in `ui_clone.gates.__init__` ensures every gate
    in GATE_ORDER has a matching method, so getattr() here cannot raise
    at runtime.
    """
    return {gate: getattr(self, _gate_method_name(gate)) for gate in _state.GATE_ORDER}


def _dispatch(self: Gate, gate: str) -> list[CheckResult]:
    dispatch = self._make_dispatch()
    if gate == "all":
        results = []
        for fn in dispatch.values():
            results.extend(fn())
        return results
    if gate not in dispatch:
        return []
    return list(dispatch[gate]())


def _check_pipeline_state_prerequisites(self: Gate, gate: str) -> CheckResult | None:
    """Fail closed when pipeline-state skipped required earlier gates."""
    if gate == "all" or gate not in _state.GATE_ORDER:
        return None
    if not (self.ref_dir / "pipeline-state.json").is_file():
        return None
    ps = _state.PipelineState.load(self.ref_dir)
    missing = ps.missing_prerequisites(gate)
    if not missing:
        return None
    missing_s = ", ".join(missing)
    return CheckResult(
        "pipeline-state prerequisites",
        "fail",
        (
            f"pipeline-state.json is out of order: gate {gate!r} cannot pass "
            f"until earlier gate(s) are completed: {missing_s}."
        ),
        fix=(
            "Resume at the earliest missing gate instead of continuing closeout. "
            f"Run: python -m ui_clone.goal {self.ref_dir}"
        ),
    )


def _render_text(self: Gate, results: list[CheckResult]) -> None:
    for r in results:
        if r.status == "pass":
            print(f"  {_GREEN}✓{_NC} {r.message}")
        elif r.status == "fail":
            print(f"  {_RED}✗{_NC} {r.message}")
            if r.fix:
                print(f"    → {r.fix}")
        else:  # warn
            print(f"  {_YELLOW}⚠{_NC}  {r.message}")


def _render_json(self: Gate, results: list[CheckResult]) -> None:
    failures = [
        {"label": r.label, "reason": r.message, "fix": r.fix}
        for r in results
        if r.status == "fail"
    ]
    output = {
        "passed": len(failures) == 0,
        "fail_count": len(failures),
        "warn_count": sum(1 for r in results if r.status == "warn"),
        "pass_count": sum(1 for r in results if r.status == "pass"),
        "failures": failures,
    }
    print(json.dumps(output, ensure_ascii=False))


def run(self: Gate, gate: str, json_output: bool = False) -> int:
    """Run gate checks. Returns 0=PASS, 1=BLOCKED, 2=usage error."""
    if gate not in VALID_GATES:
        if json_output:
            print(json.dumps({"error": f"Unknown gate: {gate}", "valid": VALID_GATES}))
        else:
            print(f"Unknown gate: {gate}")
            print(f"Valid gates: {' | '.join(VALID_GATES)}")
        return 2

    if not json_output:
        print(f"Gate: {gate}")

    state_prereq = self._check_pipeline_state_prerequisites(gate)
    results = [state_prereq] if state_prereq is not None else self._dispatch(gate)

    if json_output:
        self._render_json(results)
    else:
        self._render_text(results)
        fail_count = sum(1 for r in results if r.status == "fail")
        total = len(results)
        print()
        if fail_count > 0:
            print(
                f"{_RED}BLOCKED{_NC}: {fail_count}/{total} checks failed. Fix before proceeding."
            )
        else:
            print(f"{_GREEN}PASS{_NC}: {total}/{total} checks passed. May proceed.")

    passed = not any(r.status == "fail" for r in results)

    # Record gate result in pipeline-state.json. Skip "all" (composite run).
    # PASS resets the consecutive-fail counter for this gate inside
    # mark_passed; BLOCKED increments it inside mark_failed when this gate
    # is the active one. The counter is what the goal card uses to surface
    # "STUCK after N — read diagnosis.md" so loop drivers don't grind.
    if gate != "all":
        try:
            ps = _state.PipelineState.load(self.ref_dir)
            if passed:
                ps.mark_passed(gate, self.ref_dir)
            else:
                ps.mark_failed(gate, self.ref_dir)
        except OSError:
            pass  # Non-fatal — state tracking is best-effort

    return 0 if passed else 1


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate ui-clone-skills pipeline gate",
        usage="python -m ui_clone.gate <ref-dir> <gate> [--json]",
    )
    parser.add_argument("ref_dir", type=Path)
    parser.add_argument("gate", choices=VALID_GATES)
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output structured JSON instead of colored text",
    )
    args = parser.parse_args()

    gate = Gate(args.ref_dir)
    sys.exit(gate.run(args.gate, json_output=args.json_output))
