"""
Pipeline state tracking for ui-clone-skills.

Reads/writes tmp/ref/<component>/pipeline-state.json.
Single source of truth for which gate the pipeline is currently at.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

GATE_ORDER: list[str] = [
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

# Canonical terminal status vocabulary. The `state terminal` CLI below
# validates --status against this tuple (argparse choices) and the Stop hook
# (hooks/section_gate.py) enforces the same set on persisted terminalState,
# so writer and enforcer can never drift.
TERMINAL_STATUSES: tuple[str, ...] = ("failed", "incomplete", "unclonable", "abandoned")

# Gate suite that canonical verify (pipeline_phases/verify.py) runs and that
# the Stop hook (hooks/section_gate.py) requires in verify-stamp.json
# gatesPassed. Stamp writer and stamp enforcer import this single tuple so
# adding a gate to one side cannot deadlock or under-enforce the other.
# `spec` is re-checked at closeout in addition to the post-implement gates.
POST_IMPL_VERIFY_GATES: tuple[str, ...] = (
    "spec",
    "post-implement",
    "boundary",
    "font-parity",
    "section-compare",
)

# Hard cap on consecutive failures of any single gate. When mark_failed()
# pushes gate_fail_counts[<gate>] to this value, the state self-records a
# canonical `category="hard-cap-fail"` reason into unclonable_reasons so
# every downstream consumer (Stop hook, goal card, benchmark harness,
# `--check-done` exit code) terminates from one persisted source of truth
# instead of inventing terminal semantics independently. goal.py re-exports
# this constant as `_MAX_GATE_FAILS` for back-compat with benchmark_harness.
HARD_CAP_GATE_FAILS = 10

UTC = timezone.utc  # noqa: UP017 - macOS /usr/bin/python3 is still 3.9.

# Canonical hard-cap unclonable reason string. Lives next to
# HARD_CAP_GATE_FAILS so the dedup key (gate, reason) inside
# _record_unclonable_unlocked can't drift from the rendered wording.
# Anyone reading state.json (Stop hook, goal card, benchmark) must match
# this exact format to recognize hard-cap terminations.
HARD_CAP_REASON_TEMPLATE = (
    "hard cap reached: gate '{gate}' failed {cap} consecutive times "
    "(auto-terminated by state.mark_failed)"
)


def format_hard_cap_reason(gate: str) -> str:
    """Render the canonical hard-cap unclonable reason for a gate."""
    return HARD_CAP_REASON_TEMPLATE.format(gate=gate, cap=HARD_CAP_GATE_FAILS)


# Secondary absolute backstop for signature-aware fail counting. The
# consecutive counter resets when the failing-check signature changes
# (progress), so an agent cycling between two failure sets (A/B/A/B) could
# otherwise evade HARD_CAP_GATE_FAILS forever. Total failures of one gate —
# regardless of signature churn — terminate at this much-larger cap. 50 is
# unreachable for legitimate multi-turn convergence but reachable for a
# cycling loop. Distinct reason wording so operators can tell
# identical-signature stagnation from signature-cycling exhaustion.
ABSOLUTE_CAP_GATE_FAILS = 50

ABSOLUTE_CAP_REASON_TEMPLATE = (
    "absolute cap reached: gate '{gate}' failed {cap} total times across "
    "changing failure signatures (auto-terminated by state.mark_failed)"
)


def format_absolute_cap_reason(gate: str) -> str:
    """Render the canonical absolute-cap unclonable reason for a gate."""
    return ABSOLUTE_CAP_REASON_TEMPLATE.format(
        gate=gate, cap=ABSOLUTE_CAP_GATE_FAILS
    )


def prerequisite_gates(gate: str) -> list[str]:
    """Return gates that must be completed before `gate` can pass."""
    if gate == "done":
        return list(GATE_ORDER)
    if gate not in GATE_ORDER:
        return []
    return GATE_ORDER[:GATE_ORDER.index(gate)]


# Step G — canonical fallback suggestions keyed by (gate, category).
# When record_unclonable() is called with a known category and no explicit
# suggestions list, we fall back to these. Adding a new category here
# makes the receipt HTML, goal cards, and downstream loop drivers all
# see consistent remediation guidance.
_FALLBACK_SUGGESTIONS: dict[tuple[str, str], list[str]] = {
    ("paid-features", "commercial-font"): [
        "Substitute with a free font near the original metrics (Inter / Manrope for sans, JetBrains Mono / Geist Mono for mono).",
        "If production fidelity matters, license the font from its vendor and ship under impl/public/fonts/.",
        "Render text as <img> for hero copy only — keeps the visual identity without licensing exposure.",
    ],
    ("paid-features", "paid-component"): [
        "Re-implement the component pattern from scratch using captured layout + computed style values.",
        "Substitute with an open equivalent from shadcn/ui or Radix Primitives.",
    ],
    ("post-implement", "drm-canvas"): [
        "Mock the canvas with a static SVG/PNG placeholder that captures the visual end state.",
        "If decorative-only, use CSS @property + animation-timeline for scroll-driven motion (no canvas, GPU-cheap).",
        "Document the section as out-of-scope and link to the live original (with Not-affiliated disclaimer).",
    ],
    ("post-implement", "auth-gated"): [
        "Capture once via agent-browser --profile <profile-with-cookies>, then extract from the logged-in snapshot.",
        "Limit clone scope to the public landing — flag auth-gated sections as out-of-scope.",
    ],
    ("section-compare", "hard-cap-fail"): [
        "Increase iteration budget on highest-AE sections first (goal card → fail_count > 5).",
        "Switch to STRUCTURAL_ONLY scoring if pixel-level match is genuinely unreachable (animated gradients, video poster frames, font subpixel diffs).",
        "Check ref-screenshot freshness — stale capture vs. fresh impl causes phantom diffs.",
    ],
    ("section-compare", "irreproducible-bitmap"): [
        "Captured-asset section: include the original raster directly (impl/public/...) when copyright permits — fastest path to pixel parity.",
        "Generate a procedural equivalent in CSS/SVG; accept STRUCTURAL_ONLY verdict.",
    ],
    ("boundary", "partial-convergence-driver-retired"): [
        "Continue iteration if pixel diff is still trending down across loops.",
        "Stop and accept STRUCTURAL_ONLY if AE saturated for >3 loops at the same value.",
    ],
    ("font-parity", "subpixel-rendering-diff"): [
        "Disable anti-alias subpixel differences via text-rendering: geometricPrecision on impl side.",
        "Capture ref + impl at the same OS / browser to eliminate OS-level font hint differences.",
    ],
}


def suggest_fallbacks(gate: str, category: str) -> list[str]:
    """Return canonical fallback suggestions for a (gate, category) pair.

    Returns empty list when no canonical suggestions exist. Callers can
    fall through to passing their own explicit list to record_unclonable.
    """
    return list(_FALLBACK_SUGGESTIONS.get((gate, category), []))


@contextmanager
def _pipeline_state_lock(ref_dir: Path) -> Iterator[None]:
    """Cross-process advisory exclusive lock on pipeline-state.json.lock.

    Serializes the read-modify-write of pipeline-state.json so concurrent
    writers (gate runs, hook invocations, sub-workspace drivers) don't
    lose increments to gate_fail_counts or duplicate unclonable_reasons
    entries. Lifted pattern from ui_clone.driver_session.register —
    `os.replace` alone has a TOCTOU race because both writers can load
    the same gate_fail_counts={"post-implement": 5}, both compute +1,
    both rename — only one rename survives, the other increment is lost.

    fcntl.flock per-fd semantics (Linux/macOS): a process holding LOCK_EX
    on fd1 will *block* if the same process opens a NEW fd to the same
    lock file and tries LOCK_EX. So mark_failed cannot call the public
    record_unclonable while holding the lock — record_unclonable would
    open a fresh fd and deadlock. The `_record_unclonable_unlocked`
    private variant exists for this nested case.
    """
    ref_dir = Path(ref_dir)
    ref_dir.mkdir(parents=True, exist_ok=True)
    lock_path = ref_dir / "pipeline-state.json.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        # Releasing via close — flock(LOCK_UN) is implicit on fd close.
        os.close(lock_fd)


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
    # Signature-aware fail counting (2026-06-10): last failing-check
    # signature per gate, as provided by the gate dispatcher. When a new
    # failure's signature differs from the stored one, the consecutive
    # counter RESETS — the failing set changed, so the run is converging
    # (or at least hitting a different blocker), not stuck. The hard cap
    # then only fires on HARD_CAP_GATE_FAILS consecutive identical-signature
    # failures, which is the no-progress loop it was built to stop. The
    # per-turn Stop-hook gate evaluation of an actively-iterating agent no
    # longer counts as "stuck" while its failing set keeps shrinking.
    gate_fail_signatures: dict[str, str] = field(default_factory=dict)
    # Total failures per gate regardless of signature churn — backs the
    # ABSOLUTE_CAP_GATE_FAILS anti-cycling backstop.
    gate_total_fail_counts: dict[str, int] = field(default_factory=dict)
    # Hard-blocker reasons populated by gates/scripts when they detect a
    # condition the pipeline cannot work past (e.g., commercial DRM canvas,
    # auth-gated content, paid font with decision='use' and no substitution).
    # Non-empty list → goal.py --check-done exits 2 instead of 1, so external
    # loops stop iterating on an unwinnable target instead of grinding to
    # max-iterations.
    unclonable_reasons: list[dict] = field(default_factory=list)
    # Audit trail for operator-sanctioned recoveries (see recover_hard_cap).
    # Each entry records which gate was recovered, the cleared reason entries,
    # the operator-provided justification, and the timestamp — terminal state
    # is never erased silently.
    recoveries: list[dict] = field(default_factory=list)
    # Absolute path to the impl tree this pipeline run targets. Set
    # by pipeline.execute_extract once at Phase 1 start; consumed by
    # find-impl-root.sh as a universal layout-independent resolver.
    # Empty string when not yet established (pre-extraction).
    impl_root: str = ""
    # Closeout proof contract this pipeline run uses to satisfy the Stop
    # hook. Default "canonical" requires verify-stamp.json written by
    # pipeline.execute_verify after every GATE_ORDER post-impl gate
    # passes — the strict path every legacy run uses. "structural" opts
    # into the section-staged convergence plan's alternate proof:
    # structural-convergence-stamp.json written by check-converged.sh
    # when sections/result.txt shows 0 FAIL. The two stamps are NEVER
    # interchangeable; the field gates which one the Stop hook accepts.
    closeout_policy: str = "canonical"
    # Explicit terminal outcome for runs that should no longer be treated as
    # active WIP by agent Stop hooks. This is deliberately separate from
    # `unclonable_reasons`: reasons explain blockers, while terminal_state is
    # the machine-readable lifecycle decision (failed/incomplete/unclonable/
    # abandoned). Empty dict means the run is still active or not yet terminal.
    terminal_state: dict = field(default_factory=dict)
    # Fail-closed state review: transient flag set to True
    # when PipelineState.load() had to quarantine a corrupt pipeline-state.json.
    # mark_failed() reads this to decide between "advance fail count on fresh
    # state" (false-positive on quarantine, hard-cap never fires) and "treat
    # the quarantine itself as a terminal state-corruption". NOT persisted to
    # disk (excluded from _to_disk_payload + dataclass repr/compare).
    load_failed: bool = field(default=False, repr=False, compare=False)

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
                gate_fail_signatures=data.get("gate_fail_signatures", {}) or {},
                gate_total_fail_counts=data.get("gate_total_fail_counts", {}) or {},
                unclonable_reasons=data.get("unclonable_reasons", []) or [],
                recoveries=data.get("recoveries", []) or [],
                # Accept implRoot OR impl_root from on-disk JSON for
                # forward-compat with the camelCase field find-impl-
                # root.sh reads.
                impl_root=(
                    data.get("implRoot")
                    or data.get("impl_root")
                    or ""
                ),
                # Accept camelCase `closeoutPolicy` (the on-disk wire form,
                # matching implRoot precedent) or snake_case `closeout_policy`
                # (forward-compat). Default to "canonical" when absent so
                # legacy state files keep their existing semantics.
                closeout_policy=(
                    data.get("closeoutPolicy")
                    or data.get("closeout_policy")
                    or "canonical"
                ),
                terminal_state=(
                    data.get("terminalState")
                    or data.get("terminal_state")
                    or {}
                ),
            )
        except json.JSONDecodeError as exc:
            # State-corruption review: silently returning defaults on a
            # corrupt pipeline-state.json erases terminal/abort state
            # (unclonable_reasons, completed_steps) so the loop restarts at
            # "reference" with no audit trail. Quarantine the corrupt bytes
            # under a timestamped name so operators can recover them, then
            # warn loudly on stderr.
            ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            quarantine = path.with_suffix(f".json.corrupt.{ts}")
            try:
                path.rename(quarantine)
            except OSError:
                pass
            print(
                f"ui-clone-skills: pipeline-state.json at {path} is corrupt "
                f"({exc}). Quarantined to {quarantine.name}. Falling back to "
                f"defaults — recover unclonable_reasons / completed_steps from "
                f"the quarantine file if needed.",
                file=sys.stderr,
            )
            fresh = cls(component=ref_dir.name)
            fresh.load_failed = True
            return fresh
        except OSError as exc:
            print(f"ui-clone-skills: Cannot read {path}: {exc}", file=sys.stderr)
            fresh = cls(component=ref_dir.name)
            fresh.load_failed = True
            return fresh

    def _to_disk_payload(self) -> dict:
        """Single source of truth for the on-disk JSON schema.

        Encapsulates the implRoot/closeoutPolicy emit-when-set rules so
        every write path produces identical bytes for identical state.
        """
        payload: dict = {
            "component": self.component,
            "started_at": self.started_at,
            "completed_steps": self.completed_steps,
            "current_gate": self.current_gate,
            "last_updated": self.last_updated,
            "gate_fail_counts": self.gate_fail_counts,
            "unclonable_reasons": self.unclonable_reasons,
        }
        # Signature-aware counting fields persist only when present to keep
        # legacy state files byte-identical.
        if self.gate_fail_signatures:
            payload["gate_fail_signatures"] = self.gate_fail_signatures
        if self.gate_total_fail_counts:
            payload["gate_total_fail_counts"] = self.gate_total_fail_counts
        # Recovery audit entries persist only when present to keep legacy
        # state files byte-identical.
        if self.recoveries:
            payload["recoveries"] = self.recoveries
        # Emit both keys so find-impl-root.sh (camelCase `implRoot`) and any
        # internal reader (snake_case `impl_root`) both work. Omit when empty
        # to keep legacy state files compact.
        if self.impl_root:
            payload["implRoot"] = self.impl_root
            payload["impl_root"] = self.impl_root
        # closeout_policy persists only when non-default to avoid diff churn
        # on every save on legacy canonical runs.
        if self.closeout_policy != "canonical":
            payload["closeoutPolicy"] = self.closeout_policy
        if self.terminal_state:
            payload["terminalState"] = self.terminal_state
            payload["terminal_state"] = self.terminal_state
        return payload

    def _save_unlocked(self, ref_dir: Path) -> None:
        """Atomic JSON write WITHOUT acquiring the lock.

        Caller MUST already hold `_pipeline_state_lock(ref_dir)`. Used by
        the public write methods (save / mark_* / record_unclonable /
        demote_to) and by `_record_unclonable_unlocked` when chained from
        mark_failed under the same critical section.
        """
        path = ref_dir / "pipeline-state.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._to_disk_payload(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    def _mirror_from(self, other: PipelineState) -> None:
        """Copy mutable fields from `other` into `self`.

        Used after lock-protected RMW (mark_* / record_unclonable) so the
        caller's in-memory PipelineState instance reflects the disk truth
        that was actually written under the lock — even when a parallel
        writer's earlier mutation was absorbed during the reload.
        """
        self.component = other.component
        self.started_at = other.started_at
        self.completed_steps = list(other.completed_steps)
        self.current_gate = other.current_gate
        self.last_updated = other.last_updated
        self.gate_fail_counts = dict(other.gate_fail_counts)
        self.gate_fail_signatures = dict(other.gate_fail_signatures)
        self.gate_total_fail_counts = dict(other.gate_total_fail_counts)
        self.unclonable_reasons = [dict(r) for r in other.unclonable_reasons]
        self.recoveries = [dict(r) for r in other.recoveries]
        self.impl_root = other.impl_root
        self.closeout_policy = other.closeout_policy
        self.terminal_state = dict(other.terminal_state)

    def _record_terminal_unlocked(
        self,
        *,
        status: str,
        category: str,
        reason: str,
        gate: str | None = None,
        detail: dict | None = None,
        next_action: str | None = None,
        written_by: str = "cli",
        sections_result_sha256: str | None = None,
    ) -> None:
        """Set the explicit terminal lifecycle state in memory.

        Caller holds the state lock and is responsible for saving. The
        `terminalState` object is the hook/CLI contract; it prevents future
        code from overloading `unclonable_reasons` as a generic completion
        release signal.

        `writtenBy` records provenance: gate-bound internal callers pass
        "pipeline" (verify.execute_verify behind a real gate run, record_unclonable
        behind a content blocker); the self-attested CLI escape defaults to "cli".
        A self-attested write also pins `sectionsResultSha256` so the Stop hook can
        bind the release to the exact evidence snapshot (see _terminal_state_block_reason).
        """
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        terminal: dict = {
            "status": status,
            "category": category,
            "gate": gate or self.current_gate,
            "reason": reason,
            "recorded_at": now,
            "writtenBy": written_by,
        }
        if sections_result_sha256 is not None:
            terminal["sectionsResultSha256"] = sections_result_sha256
        if detail is not None:
            terminal["detail"] = detail
        if next_action:
            terminal["next_action"] = next_action
        self.terminal_state = terminal
        if not self.started_at:
            self.started_at = now
        self.last_updated = now

    def mark_terminal(
        self,
        ref_dir: Path,
        *,
        status: str,
        category: str,
        reason: str,
        gate: str | None = None,
        detail: dict | None = None,
        next_action: str | None = None,
        written_by: str = "cli",
    ) -> None:
        """Persist an explicit terminal lifecycle state.

        Examples:
        - status="failed", category="canonical-verify-failed" for a verify
          run that produced reports but no success stamp.
        - status="incomplete", category="hardening-probe-incomplete" for a
          harvested probe that should no longer block Stop.
        - status="unclonable" for genuine content blockers.

        `written_by` defaults to "cli" (the self-attested operator/agent escape);
        gate-bound internal callers pass "pipeline". For a self-attested write we
        pin sha256(sections/result.txt) so the Stop hook binds the non-success
        release to that exact evidence snapshot — a self-attested terminal state
        recorded against a stale/forged result.txt no longer releases Stop.
        """
        sections_sha: str | None = None
        if written_by != "pipeline":
            result_txt = ref_dir / "sections" / "result.txt"
            if result_txt.is_file():
                try:
                    sections_sha = hashlib.sha256(result_txt.read_bytes()).hexdigest()
                except OSError:
                    sections_sha = None
        with _pipeline_state_lock(ref_dir):
            state_path = ref_dir / "pipeline-state.json"
            authoritative = (
                PipelineState.load(ref_dir) if state_path.is_file() else self
            )
            authoritative._record_terminal_unlocked(
                status=status,
                category=category,
                reason=reason,
                gate=gate,
                detail=detail,
                next_action=next_action,
                written_by=written_by,
                sections_result_sha256=sections_sha,
            )
            authoritative._save_unlocked(ref_dir)
            if authoritative is not self:
                self._mirror_from(authoritative)

    def clear_terminal(self, ref_dir: Path) -> None:
        """Clear terminal_state after deliberate recovery or successful verify."""
        with _pipeline_state_lock(ref_dir):
            state_path = ref_dir / "pipeline-state.json"
            authoritative = (
                PipelineState.load(ref_dir) if state_path.is_file() else self
            )
            if not authoritative.terminal_state:
                if authoritative is not self:
                    self._mirror_from(authoritative)
                return
            authoritative.terminal_state = {}
            authoritative.last_updated = datetime.now(UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            authoritative._save_unlocked(ref_dir)
            if authoritative is not self:
                self._mirror_from(authoritative)

    def save(self, ref_dir: Path) -> None:
        """Write the current in-memory state to pipeline-state.json atomically.

        Used by callers that mutate fields outside of `mark_passed` /
        `mark_failed` / `record_unclonable` (e.g. `impl_root` write at
        Phase 1 start in `pipeline.execute_phases`). Holds the cross-
        process write lock so concurrent gate runs / hook invocations
        don't interleave half-written JSON.
        """
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not self.started_at:
            self.started_at = now
        self.last_updated = now
        with _pipeline_state_lock(ref_dir):
            self._save_unlocked(ref_dir)

    def _demote_to_unlocked(self, gate: str) -> None:
        """Demotion logic without lock/write. Caller holds the lock and saves."""
        target_idx = GATE_ORDER.index(gate)
        self.completed_steps = [
            g for g in self.completed_steps
            if g not in GATE_ORDER or GATE_ORDER.index(g) < target_idx
        ]
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

    def demote_to(self, gate: str, ref_dir: Path) -> None:
        """Reset current_gate back to `gate` and remove it (and later gates) from completed.

        Used when downstream artifacts are invalidated (e.g., a component file was
        edited after section-compare passed — the visual verification is now stale).
        Writes file atomically under the cross-process write lock. The disk is
        re-read inside the lock so a concurrent mark_passed/mark_failed/
        record_unclonable from another process doesn't get clobbered.
        """
        if gate not in GATE_ORDER:
            return
        with _pipeline_state_lock(ref_dir):
            state_path = ref_dir / "pipeline-state.json"
            authoritative = (
                PipelineState.load(ref_dir) if state_path.is_file() else self
            )
            authoritative._demote_to_unlocked(gate)
            authoritative._save_unlocked(ref_dir)
            if authoritative is not self:
                self._mirror_from(authoritative)

    def missing_prerequisites(self, gate: str) -> list[str]:
        """Return prerequisite gates absent from completed_steps."""
        completed = set(self.completed_steps)
        return [g for g in prerequisite_gates(gate) if g not in completed]

    def _normalize_completed_steps(self) -> None:
        """Keep known gates in canonical order and preserve unknown extras last."""
        seen = set(self.completed_steps)
        ordered = [g for g in GATE_ORDER if g in seen]
        extras = [g for g in self.completed_steps if g not in GATE_ORDER]
        self.completed_steps = ordered + extras

    def _mark_passed_unlocked(self, gate: str) -> bool:
        """Apply mark_passed mutation. Returns True if anything changed.

        Caller holds the lock and is responsible for the save. Returning
        False signals "no on-disk change required" so the cross-process
        lock-load-write critical section can short-circuit the write."""
        if self.missing_prerequisites(gate):
            return False

        already_recorded = gate in self.completed_steps
        if not already_recorded:
            self.completed_steps.append(gate)
            self._normalize_completed_steps()

        fail_reset = self.gate_fail_counts.pop(gate, 0) > 0

        next_gate = self.current_gate
        if self.current_gate == "done":
            pass
        elif gate in GATE_ORDER:
            idx = GATE_ORDER.index(gate)
            next_idx = idx + 1
            candidate = GATE_ORDER[next_idx] if next_idx < len(GATE_ORDER) else "done"
            current_idx = (
                GATE_ORDER.index(self.current_gate) if self.current_gate in GATE_ORDER else -1
            )
            candidate_idx = (
                GATE_ORDER.index(candidate) if candidate in GATE_ORDER else len(GATE_ORDER)
            )
            if candidate_idx > current_idx:
                next_gate = candidate

        if already_recorded and next_gate == self.current_gate and not fail_reset:
            return False

        self.current_gate = next_gate

        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not self.started_at:
            self.started_at = now
        self.last_updated = now
        return True

    def mark_passed(self, gate: str, ref_dir: Path) -> None:
        """Record gate as passed and advance current_gate. Writes file atomically
        under the cross-process write lock.

        Skips the write when the gate was already recorded and current_gate
        would not advance — avoids unnecessary filesystem churn on re-runs.
        The disk is re-read inside the lock so a concurrent mark_failed /
        record_unclonable from another process is absorbed rather than
        clobbered by concurrent writers.
        """
        if gate not in GATE_ORDER:
            return
        with _pipeline_state_lock(ref_dir):
            state_path = ref_dir / "pipeline-state.json"
            authoritative = (
                PipelineState.load(ref_dir) if state_path.is_file() else self
            )
            changed = authoritative._mark_passed_unlocked(gate)
            if changed:
                authoritative._save_unlocked(ref_dir)
            if authoritative is not self:
                self._mirror_from(authoritative)

    def _record_unclonable_unlocked(
        self,
        gate: str,
        reason: str,
        detail: dict | None = None,
        category: str | None = None,
        fallback_suggestions: list[str] | None = None,
    ) -> bool:
        """Append an unclonable reason in memory. Caller holds the lock and
        is responsible for the save. Returns True if a new entry was added,
        False on dedup hit (existing (gate, reason) pair)."""
        for existing in self.unclonable_reasons:
            if existing.get("gate") == gate and existing.get("reason") == reason:
                if not self.terminal_state:
                    self._record_terminal_unlocked(
                        status="unclonable",
                        category=category or existing.get("category") or "unclonable",
                        gate=gate,
                        reason=reason,
                        detail=detail,
                        next_action=(
                            "Resolve or document the blocker, then recover the run before continuing."
                        ),
                        written_by="pipeline",
                    )
                    return True
                return False

        # Suggestion resolution: explicit > category-default > none.
        if fallback_suggestions is None and category:
            fallback_suggestions = suggest_fallbacks(gate, category)

        entry: dict = {
            "gate": gate,
            "reason": reason,
            "detected_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if category:
            entry["category"] = category
        if fallback_suggestions:
            entry["fallback_suggestions"] = list(fallback_suggestions)
        if detail is not None:
            entry["detail"] = detail
        self.unclonable_reasons.append(entry)

        now = entry["detected_at"]
        if not self.started_at:
            self.started_at = now
        self.last_updated = now
        # Keep lifecycle state explicit. Downstream hooks no longer infer
        # terminal completion from the presence of unclonable_reasons alone.
        self._record_terminal_unlocked(
            status="unclonable",
            category=category or "unclonable",
            gate=gate,
            reason=reason,
            detail=detail,
            next_action="Resolve or document the blocker, then recover the run before continuing.",
            written_by="pipeline",
        )
        return True

    def mark_failed(
        self,
        gate: str,
        ref_dir: Path,
        failure_signature: str | None = None,
    ) -> None:
        """Increment the consecutive-fail counter for `gate`.

        Only bumps when `gate` is the current_gate — failing a gate earlier
        than the cursor (e.g. re-running `reference` after pipeline is at
        `extraction`) does not count as "stuck on the active gate".

        failure_signature: stable digest of the failing-check set, computed
        by the gate dispatcher. When provided and DIFFERENT from the stored
        signature for this gate, the consecutive counter resets to 1 — the
        failing set changed, so the run is converging or at least facing a
        different blocker, not retrying the same action. When identical (or
        when no signature is provided — legacy callers), the counter
        increments as before. A separate total counter backs the
        ABSOLUTE_CAP_GATE_FAILS anti-cycling backstop.

        Hard-cap auto-termination: when the bumped consecutive counter is at
        or past HARD_CAP_GATE_FAILS (identical-signature stagnation), or the
        total counter reaches ABSOLUTE_CAP_GATE_FAILS (signature cycling),
        also writes a canonical `category="hard-cap-fail"` entry into
        unclonable_reasons. Both the bump and the auto-record happen inside
        one cross-process write lock so two parallel mark_failed callers
        cannot lose increments.
        """
        if gate not in GATE_ORDER:
            return
        if self.current_gate != gate:
            return  # fast-path; preserves test_mark_failed_ignores_non_active_gate

        with _pipeline_state_lock(ref_dir):
            state_path = ref_dir / "pipeline-state.json"
            authoritative = (
                PipelineState.load(ref_dir) if state_path.is_file() else self
            )
            # Fail-closed state review: if PipelineState.load() had
            # to quarantine a corrupt pipeline-state.json, the in-memory
            # `authoritative` is fresh (current_gate='reference', no fail
            # counts, no completed_steps). Without this guard, the next
            # `authoritative.current_gate != gate` check below would
            # silently bail (mismatch with caller's gate) — fail counter
            # never bumps, hard-cap never fires, pipeline drives on stale
            # context. Record state-corruption as canonical terminal
            # unclonable so Stop hook / goal card / benchmark all see one
            # explicit termination signal instead of a phantom no-op.
            if authoritative.load_failed:
                authoritative._record_unclonable_unlocked(
                    gate=gate,
                    reason=(
                        f"pipeline-state.json was corrupt and quarantined "
                        f"by PipelineState.load(); cannot bump fail counter "
                        f"for gate '{gate}' on fresh state without losing "
                        f"audit trail. Recover unclonable_reasons / "
                        f"completed_steps from the .json.corrupt.* quarantine "
                        f"file in {ref_dir} if needed."
                    ),
                    category="state-corruption",
                )
                authoritative._save_unlocked(ref_dir)
                if authoritative is not self:
                    self._mirror_from(authoritative)
                return
            # Re-check inside the lock against the freshly-loaded snapshot.
            if authoritative.current_gate != gate:
                if authoritative is not self:
                    self._mirror_from(authoritative)
                return

            prev_sig = authoritative.gate_fail_signatures.get(gate)
            if failure_signature is not None and failure_signature != prev_sig:
                # Failing set changed → progress (or a new blocker). Reset
                # the consecutive counter; this failure is #1 of a new run.
                authoritative.gate_fail_counts[gate] = 1
                authoritative.gate_fail_signatures[gate] = failure_signature
            else:
                authoritative.gate_fail_counts[gate] = (
                    authoritative.gate_fail_counts.get(gate, 0) + 1
                )
            authoritative.gate_total_fail_counts[gate] = (
                authoritative.gate_total_fail_counts.get(gate, 0) + 1
            )
            now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            if not authoritative.started_at:
                authoritative.started_at = now
            authoritative.last_updated = now

            # Hard-cap terminalization: append the canonical hard-cap-fail
            # reason while still under the same lock so a single save
            # captures both the bumped counter and the new reason.
            # _record_unclonable_unlocked dedups by (gate, reason) — the
            # reason string deliberately uses the cap (not the live count)
            # so subsequent bumps don't produce N-duplicate entries.
            if authoritative.gate_fail_counts[gate] >= HARD_CAP_GATE_FAILS:
                authoritative._record_unclonable_unlocked(
                    gate=gate,
                    reason=format_hard_cap_reason(gate),
                    category="hard-cap-fail",
                )
            if (
                authoritative.gate_total_fail_counts[gate]
                >= ABSOLUTE_CAP_GATE_FAILS
            ):
                authoritative._record_unclonable_unlocked(
                    gate=gate,
                    reason=format_absolute_cap_reason(gate),
                    category="hard-cap-fail",
                )

            authoritative._save_unlocked(ref_dir)
            if authoritative is not self:
                self._mirror_from(authoritative)

    def record_unclonable(
        self,
        gate: str,
        reason: str,
        ref_dir: Path,
        detail: dict | None = None,
        category: str | None = None,
        fallback_suggestions: list[str] | None = None,
    ) -> None:
        """Record a hard-blocker that the pipeline cannot work past.

        Deduplicates by (gate, reason). Triggers goal.py --check-done exit
        code 2 (distinct from 1 = not-yet-done) so external loop drivers can
        stop on an unwinnable target instead of burning iterations.

        Lock-protected RMW: the disk is re-read inside the lock so a
        concurrent record_unclonable / mark_failed from another process
        contributes to the dedup decision instead of being clobbered.

        category (Step G): short machine-readable kind name (e.g.,
            "drm-canvas", "commercial-font", "auth-gated", "hard-cap-fail").
            Used by the receipt HTML to look up downstream documentation.

        fallback_suggestions (Step G): ordered list of human-readable
            remediation suggestions. Callers pass either an explicit list
            OR pass `category` alone and we look up canonical defaults
            from suggest_fallbacks(). Empty / None → field omitted from
            the persisted entry for compat with pre-G readers.
        """
        with _pipeline_state_lock(ref_dir):
            state_path = ref_dir / "pipeline-state.json"
            authoritative = (
                PipelineState.load(ref_dir) if state_path.is_file() else self
            )
            added = authoritative._record_unclonable_unlocked(
                gate=gate,
                reason=reason,
                detail=detail,
                category=category,
                fallback_suggestions=fallback_suggestions,
            )
            if added:
                authoritative._save_unlocked(ref_dir)
            if authoritative is not self:
                self._mirror_from(authoritative)

    def recover_hard_cap(
        self,
        gate: str,
        operator_reason: str,
        ref_dir: Path,
        force: bool = False,
    ) -> bool:
        """Operator-sanctioned recovery from a hard-cap termination.

        The hard cap (HARD_CAP_GATE_FAILS consecutive fails of one gate)
        exists to stop blind impl iteration. When the underlying constraint
        was found and resolved OUTSIDE the impl loop (e.g. polluted reference
        capture artifacts, a pipeline-tool bug), the termination is no longer
        meaningful — but it must be lifted deliberately, with a recorded
        justification, never silently.

        Clears the gate's `category=="hard-cap-fail"` unclonable entries and
        resets its fail count. Genuine content blockers (license, DRM, auth —
        any non-hard-cap category) are NOT cleared unless force=True.
        Appends an audit entry to `recoveries` so history is preserved.
        Returns True when anything was cleared.
        """
        if not operator_reason.strip():
            raise ValueError("recover_hard_cap requires a non-empty operator reason")
        with _pipeline_state_lock(ref_dir):
            state_path = ref_dir / "pipeline-state.json"
            authoritative = (
                PipelineState.load(ref_dir) if state_path.is_file() else self
            )
            gate_entries = [
                r for r in authoritative.unclonable_reasons if r.get("gate") == gate
            ]
            if not gate_entries and gate not in authoritative.gate_fail_counts:
                return False
            blockers = [
                r for r in gate_entries if r.get("category") != "hard-cap-fail"
            ]
            if blockers and not force:
                raise ValueError(
                    f"gate '{gate}' has non-hard-cap unclonable reasons "
                    f"({[r.get('category') for r in blockers]}); these mark real "
                    "content blockers — pass force=True only if they are wrong."
                )
            cleared = gate_entries if force else [
                r for r in gate_entries if r.get("category") == "hard-cap-fail"
            ]
            keep_ids = {id(r) for r in cleared}
            authoritative.unclonable_reasons = [
                r
                for r in authoritative.unclonable_reasons
                if not (r.get("gate") == gate and id(r) in keep_ids)
            ]
            authoritative.gate_fail_counts.pop(gate, None)
            authoritative.gate_fail_signatures.pop(gate, None)
            authoritative.gate_total_fail_counts.pop(gate, None)
            if (
                authoritative.terminal_state
                and authoritative.terminal_state.get("gate") == gate
                and authoritative.terminal_state.get("category") == "hard-cap-fail"
            ):
                authoritative.terminal_state = {}
            now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            authoritative.recoveries.append(
                {
                    "gate": gate,
                    "cleared": cleared,
                    "operator_reason": operator_reason,
                    "forced": bool(force and blockers),
                    "recovered_at": now,
                }
            )
            authoritative.last_updated = now
            authoritative._save_unlocked(ref_dir)
            if authoritative is not self:
                self._mirror_from(authoritative)
            return bool(cleared) or True


def _last_updated_epoch(state: PipelineState) -> float | None:
    raw = (state.last_updated or "").strip()
    if not raw:
        return None
    iso = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def sweep_stale_refs(
    search_root: Path, older_than_days: float, *, execute: bool = False
) -> list[dict[str, object]]:
    """Operator-invoked bulk abandon of stale WIP refs (fix #3).

    DRY-RUN by default (execute=False). For every ref under search_root that is
    a pipeline ref (has pipeline-state.json), is NOT already terminal or
    verified, and whose last_updated is older than older_than_days, record an
    explicit `abandoned`/`stale-reaped` terminal state — UNLESS its
    sections/result.txt looks success-shaped (no honest ❌/FAIL/MISSING marker),
    in which case it is REFUSED (a success-claiming ref must not be silently
    abandoned; it needs real handling). writtenBy stays honestly self-attested
    ("cli") — never "pipeline". This is NOT wired into the Stop turn-end path;
    it is a deliberate human maintenance action with an audit trail.
    """
    results: list[dict[str, object]] = []
    if not search_root.is_dir():
        return results
    now = time.time()
    cutoff = now - older_than_days * 86400
    for ref_dir in sorted(search_root.iterdir()):
        if not ref_dir.is_dir() or not (ref_dir / "pipeline-state.json").is_file():
            continue
        if (ref_dir / "verify-stamp.json").is_file():
            continue
        try:
            state = PipelineState.load(ref_dir)
        except (OSError, ValueError):
            continue
        if state.terminal_state:
            continue
        epoch = _last_updated_epoch(state)
        if epoch is None or epoch > cutoff:
            continue
        age_days = int((now - epoch) // 86400)
        rt = ref_dir / "sections" / "result.txt"
        if rt.is_file():
            try:
                txt = rt.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                txt = ""
            # Honest-failure signal is the per-row ❌ glyph (or a MISSING-impl
            # row) that section-compare emits. Deliberately NOT the bare word
            # "FAIL": a success summary reads "N PASS, 0 FAIL" and must be
            # refused, not swept. Absent ❌/MISSING → treat as success-shaped.
            if not any(m in txt for m in ("❌", "MISSING")):
                results.append(
                    {"ref": str(ref_dir), "ageDays": age_days,
                     "action": "refused-success-shaped"}
                )
                continue
        if execute:
            # A bulk op must be resilient: one ref with malformed legacy state
            # (e.g. an old string-list unclonable_reasons that _mirror_from can't
            # coerce) must not abort the whole sweep. Record it as failed and
            # continue so the rest still get cleaned.
            try:
                state.mark_terminal(
                    ref_dir,
                    status="abandoned",
                    category="stale-reaped",
                    reason=(
                        f"swept: stale WIP, last_updated {age_days}d ago "
                        f"(>{older_than_days}d threshold)"
                    ),
                )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                results.append({
                    "ref": str(ref_dir), "ageDays": age_days,
                    "action": "failed", "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            action = "abandoned"
        else:
            action = "would-abandon"
        results.append({"ref": str(ref_dir), "ageDays": age_days, "action": action})
    return results


def main(argv: list[str] | None = None) -> int:
    """Operator CLI: `python -m ui_clone.state recover <ref-dir> --gate g --reason "..."`."""
    import argparse

    parser = argparse.ArgumentParser(description="Pipeline state operations")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser(
        "recover",
        help=(
            "Lift a hard-cap termination after the underlying constraint was "
            "resolved outside the impl loop. Records an audit entry; refuses "
            "to clear license/DRM/auth blockers without --force."
        ),
    )
    rec.add_argument("ref_dir", type=Path, help="tmp/ref/<component> directory")
    rec.add_argument("--gate", required=True, help="gate whose hard-cap to lift")
    rec.add_argument(
        "--reason", required=True,
        help="operator justification (what constraint was resolved, where)",
    )
    rec.add_argument("--force", action="store_true",
                     help="also clear non-hard-cap (content blocker) reasons")
    rec.add_argument(
        "--demote-to", dest="demote_to", default=None,
        help="optionally demote current_gate back to this gate after recovery",
    )
    term = sub.add_parser(
        "terminal",
        help=(
            "Record an explicit terminal failed/incomplete/unclonable state "
            "without writing verify-stamp.json."
        ),
    )
    term.add_argument("ref_dir", type=Path, help="tmp/ref/<component> or run directory")
    term.add_argument(
        "--status",
        required=True,
        choices=list(TERMINAL_STATUSES),
        help="terminal status (one of: %(choices)s)",
    )
    term.add_argument("--category", required=True, help="machine-readable terminal category")
    term.add_argument("--reason", required=True, help="human-readable terminal reason")
    term.add_argument("--gate", default=None, help="gate associated with the terminal state")
    term.add_argument("--next-action", default=None, help="recommended next action")
    swp = sub.add_parser(
        "sweep",
        help=(
            "Bulk-abandon stale WIP refs (last_updated older than --older-than "
            "days) as an audited operator action. DRY-RUN unless --yes. Refuses "
            "refs whose result.txt looks success-shaped."
        ),
    )
    swp.add_argument(
        "--older-than", dest="older_than", type=float, required=True,
        help="age threshold in days (last_updated older than this)",
    )
    swp.add_argument(
        "--root", type=Path, default=None,
        help="search root (default: <cwd>/tmp/ref)",
    )
    swp.add_argument(
        "--yes", action="store_true",
        help="execute the abandons (default is a dry-run preview)",
    )
    args = parser.parse_args(argv)

    if args.command == "sweep":
        root = args.root if args.root is not None else Path.cwd() / "tmp" / "ref"
        rows = sweep_stale_refs(root, args.older_than, execute=args.yes)
        print(json.dumps(
            {"root": str(root), "dryRun": not args.yes,
             "olderThanDays": args.older_than, "results": rows},
            indent=2,
        ))
        return 0

    if args.command == "terminal":
        state = PipelineState.load(args.ref_dir)
        state.mark_terminal(
            args.ref_dir,
            status=args.status,
            category=args.category,
            reason=args.reason,
            gate=args.gate,
            next_action=args.next_action,
        )
        print(json.dumps({
            "ref_dir": str(args.ref_dir),
            "terminalState": state.terminal_state,
        }, indent=2))
        return 0

    state = PipelineState.load(args.ref_dir)
    try:
        changed = state.recover_hard_cap(
            args.gate, args.reason, args.ref_dir, force=args.force
        )
    except ValueError as exc:
        print(f"recover: {exc}", file=sys.stderr)
        return 2
    if not changed:
        print(f"recover: nothing to clear for gate '{args.gate}'", file=sys.stderr)
        return 1
    if args.demote_to:
        state.demote_to(args.demote_to, args.ref_dir)
    print(json.dumps({
        "recovered_gate": args.gate,
        "current_gate": state.current_gate,
        "unclonable_reasons": state.unclonable_reasons,
        "recoveries": state.recoveries[-1:],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
