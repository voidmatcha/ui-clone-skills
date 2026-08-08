"""Command-position anchoring for the section-compare precondition (review MINOR 4).

The section-compare precondition matcher used an UNANCHORED pattern, so a
diagnostic like `grep .../section-compare.sh` was read as a real invocation and
blocked. The pattern now reuses _common.py's CMD_POSITION_PREFIX so the script
is matched only when invoked at command position (optionally via a bash/sh
interpreter), and the python form only at command position; a bare path as a
grep/argument is data, not a run.
"""

from __future__ import annotations

import json
from pathlib import Path

from ui_clone.hooks.pre_bash_rules.section_compare import (
    _section_compare_precondition_reason,
)


def _ref(tmp_path: Path) -> Path:
    (tmp_path / "verification-plan.json").write_text(
        json.dumps(
            {
                "requiredChecks": [
                    {
                        "id": "dom-mirror-check",
                        "severity": "block",
                        "produces": "dom-mirror-check.json",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_grep_of_section_compare_not_blocked(tmp_path: Path) -> None:
    """The false positive: a grep diagnostic referencing the script path must
    NOT be read as a real invocation."""
    ref = _ref(tmp_path)
    assert (
        _section_compare_precondition_reason(
            ref, "grep -n foo skills/visual-debug/scripts/section-compare.sh"
        )
        is None
    )


def test_real_bash_invocation_blocked(tmp_path: Path) -> None:
    ref = _ref(tmp_path)
    reason = _section_compare_precondition_reason(
        ref, "bash skills/visual-debug/scripts/section-compare.sh ref impl"
    )
    assert reason is not None and "section-compare blocked" in reason


def test_python_measure_invocation_blocked(tmp_path: Path) -> None:
    ref = _ref(tmp_path)
    reason = _section_compare_precondition_reason(
        ref, "python3 -m ui_clone.measure section-compare ref"
    )
    assert reason is not None


def test_env_prefixed_invocation_blocked(tmp_path: Path) -> None:
    ref = _ref(tmp_path)
    reason = _section_compare_precondition_reason(
        ref, "FOO=1 bash skills/visual-debug/scripts/section-compare.sh ref impl"
    )
    assert reason is not None
