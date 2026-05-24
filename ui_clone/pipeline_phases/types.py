"""Shared dataclasses for pipeline phase results.

Lives in its own module so `ui_clone.pipeline_phases.checks` /
`.execute` / `.verify` can import the types without depending on
`ui_clone.pipeline` (which imports them back). Avoids a circular import.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PhaseCheck:
    """Single artifact check within a phase."""

    label: str
    passed: bool
    message: str = ""


@dataclass
class PhaseResult:
    """Result of checking one pipeline phase."""

    name: str
    title: str
    checks: list[PhaseCheck] = field(default_factory=list)
    next_step: str = ""
    skipped: bool = False
    skip_reason: str = ""
