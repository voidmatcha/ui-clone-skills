"""
Gate validation for ui-clone pipeline.

Usage:
    python -m ui_clone.gate <ref-dir> <gate> [--json]
    gate: any name from `ui_clone.state.GATE_ORDER`, or `all`
Exit: 0=PASS, 1=BLOCKED, 2=usage error

This module is a thin shim re-exporting from `ui_clone.gates`. The Gate
class, dispatch logic, and per-gate methods live under
`ui_clone/gates/`. Imports of `ui_clone.gate.Gate`, `VALID_GATES`,
`CheckResult`, `_PROVENANCE_REQUIRED_ARTIFACTS`, etc. continue to work
unchanged for tests and out-of-tree callers.
"""

from __future__ import annotations

from ui_clone.gates import (  # noqa: F401
    _AE_GROWTH_MULTIPLIER,
    _DISALLOWED_PROVENANCE_SOURCES,
    _PROVENANCE_REQUIRED_ARTIFACTS,
    _REQUIRED_ARTIFACT_FIELDS,
    _VALID_PROVENANCE_SOURCES,
    _VALID_VERIFIED_BY,
    VALID_GATES,
    CheckResult,
    Gate,
    _gate_method_name,
    _parse_all_section_ae,
    _parse_failed_sections,
    _validate_artifact_entry,
    main,
)

__all__ = [
    "CheckResult",
    "Gate",
    "VALID_GATES",
    "_AE_GROWTH_MULTIPLIER",
    "_DISALLOWED_PROVENANCE_SOURCES",
    "_PROVENANCE_REQUIRED_ARTIFACTS",
    "_REQUIRED_ARTIFACT_FIELDS",
    "_VALID_PROVENANCE_SOURCES",
    "_VALID_VERIFIED_BY",
    "_gate_method_name",
    "_parse_all_section_ae",
    "_parse_failed_sections",
    "_validate_artifact_entry",
    "main",
]


if __name__ == "__main__":
    main()
