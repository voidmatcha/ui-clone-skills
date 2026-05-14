"""
Pipeline state tracking for ui-clone-skills.

Reads/writes tmp/ref/<component>/pipeline-state.json.
Single source of truth for which gate the pipeline is currently at.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

GATE_ORDER: list[str] = [
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


@dataclass
class PipelineState:
    component: str = ""
    started_at: str = ""
    completed_steps: list[str] = field(default_factory=list)
    current_gate: str = "reference"
    last_updated: str = ""
    # Consecutive-failure counts per gate. Bumped on BLOCKED runs of the active
    # gate, reset to 0 when that gate finally passes. Surfaced in the goal card
    # so external loop drivers (codex /goal, codex exec, headless claude -p) can detect
    # "stuck on same gate" before they exhaust max-iterations.
    gate_fail_counts: dict[str, int] = field(default_factory=dict)
    # Hard-blocker reasons populated by gates/scripts when they detect a
    # condition the pipeline cannot work past (e.g., commercial DRM canvas,
    # auth-gated content, paid font with decision='use' and no substitution).
    # Non-empty list → goal.py --check-done exits 2 instead of 1, so external
    # loops stop iterating on an unwinnable target instead of grinding to
    # max-iterations.
    unclonable_reasons: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, ref_dir: Path) -> PipelineState:
        """Load state from pipeline-state.json. Returns defaults if missing or corrupt."""
        path = ref_dir / "pipeline-state.json"
        if not path.exists():
            return cls(component=ref_dir.name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                component=data.get("component", ref_dir.name),
                started_at=data.get("started_at", ""),
                completed_steps=data.get("completed_steps", []),
                current_gate=data.get("current_gate", "reference"),
                last_updated=data.get("last_updated", ""),
                gate_fail_counts=data.get("gate_fail_counts", {}) or {},
                unclonable_reasons=data.get("unclonable_reasons", []) or [],
            )
        except json.JSONDecodeError:
            return cls(component=ref_dir.name)
        except OSError as exc:
            print(f"ui-clone-skills: Cannot read {path}: {exc}", file=sys.stderr)
            return cls(component=ref_dir.name)

    def demote_to(self, gate: str, ref_dir: Path) -> None:
        """Reset current_gate back to `gate` and remove it (and later gates) from completed.

        Used when downstream artifacts are invalidated (e.g., a component file was
        edited after section-compare passed — the visual verification is now stale).
        Writes file atomically.
        """
        if gate not in GATE_ORDER:
            return
        target_idx = GATE_ORDER.index(gate)

        # Remove `gate` and any later gates from completed_steps
        self.completed_steps = [
            g for g in self.completed_steps
            if g not in GATE_ORDER or GATE_ORDER.index(g) < target_idx
        ]

        # Only retreat — never set current_gate forward via this method
        if self.current_gate == "done":
            self.current_gate = gate
        elif self.current_gate in GATE_ORDER:
            cur_idx = GATE_ORDER.index(self.current_gate)
            if cur_idx > target_idx:
                self.current_gate = gate
        else:
            self.current_gate = gate

        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not self.started_at:
            self.started_at = now
        self.last_updated = now

        path = ref_dir / "pipeline-state.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "component": self.component,
                    "started_at": self.started_at,
                    "completed_steps": self.completed_steps,
                    "current_gate": self.current_gate,
                    "last_updated": self.last_updated,
                    "gate_fail_counts": self.gate_fail_counts,
                    "unclonable_reasons": self.unclonable_reasons,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)

    def mark_passed(self, gate: str, ref_dir: Path) -> None:
        """Record gate as passed and advance current_gate. Writes file atomically.

        Skips the write when the gate was already recorded and current_gate
        would not advance — avoids unnecessary filesystem churn on re-runs.
        """
        already_recorded = gate in self.completed_steps
        if not already_recorded:
            self.completed_steps.append(gate)

        # Reset the consecutive-fail counter for this gate now that it passed.
        fail_reset = self.gate_fail_counts.pop(gate, 0) > 0

        # Compute next gate — only advance, never retreat.
        # If current_gate is already ahead of `gate` (e.g. re-running an earlier
        # step), preserve the current position instead of regressing.
        next_gate = self.current_gate
        if self.current_gate == "done":
            pass  # Terminal state — never regress
        elif gate in GATE_ORDER:
            idx = GATE_ORDER.index(gate)
            next_idx = idx + 1
            candidate = GATE_ORDER[next_idx] if next_idx < len(GATE_ORDER) else "done"
            # Only advance if candidate is strictly later than current_gate
            current_idx = (
                GATE_ORDER.index(self.current_gate) if self.current_gate in GATE_ORDER else -1
            )
            candidate_idx = (
                GATE_ORDER.index(candidate) if candidate in GATE_ORDER else len(GATE_ORDER)
            )
            if candidate_idx > current_idx:
                next_gate = candidate

        # Skip write if nothing would change
        if already_recorded and next_gate == self.current_gate and not fail_reset:
            return

        self.current_gate = next_gate

        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not self.started_at:
            self.started_at = now
        self.last_updated = now

        path = ref_dir / "pipeline-state.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "component": self.component,
                    "started_at": self.started_at,
                    "completed_steps": self.completed_steps,
                    "current_gate": self.current_gate,
                    "last_updated": self.last_updated,
                    "gate_fail_counts": self.gate_fail_counts,
                    "unclonable_reasons": self.unclonable_reasons,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)

    def mark_failed(self, gate: str, ref_dir: Path) -> None:
        """Increment the consecutive-fail counter for `gate`.

        Only bumps when `gate` is the current_gate — failing a gate earlier
        than the cursor (e.g. re-running `reference` after pipeline is at
        `extraction`) does not count as "stuck on the active gate".
        """
        if gate not in GATE_ORDER:
            return
        if self.current_gate != gate:
            return

        self.gate_fail_counts[gate] = self.gate_fail_counts.get(gate, 0) + 1
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not self.started_at:
            self.started_at = now
        self.last_updated = now

        path = ref_dir / "pipeline-state.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "component": self.component,
                    "started_at": self.started_at,
                    "completed_steps": self.completed_steps,
                    "current_gate": self.current_gate,
                    "last_updated": self.last_updated,
                    "gate_fail_counts": self.gate_fail_counts,
                    "unclonable_reasons": self.unclonable_reasons,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)

    def record_unclonable(
        self, gate: str, reason: str, ref_dir: Path, detail: dict | None = None
    ) -> None:
        """Record a hard-blocker that the pipeline cannot work past.

        Deduplicates by (gate, reason). Triggers goal.py --check-done exit
        code 2 (distinct from 1 = not-yet-done) so external loop drivers can
        stop on an unwinnable target instead of burning iterations.
        """
        for existing in self.unclonable_reasons:
            if existing.get("gate") == gate and existing.get("reason") == reason:
                return

        entry: dict = {
            "gate": gate,
            "reason": reason,
            "detected_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if detail is not None:
            entry["detail"] = detail
        self.unclonable_reasons.append(entry)

        now = entry["detected_at"]
        if not self.started_at:
            self.started_at = now
        self.last_updated = now

        path = ref_dir / "pipeline-state.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "component": self.component,
                    "started_at": self.started_at,
                    "completed_steps": self.completed_steps,
                    "current_gate": self.current_gate,
                    "last_updated": self.last_updated,
                    "gate_fail_counts": self.gate_fail_counts,
                    "unclonable_reasons": self.unclonable_reasons,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)
