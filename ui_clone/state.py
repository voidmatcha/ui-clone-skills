"""
Pipeline state tracking for ui-clone-skills.

Reads/writes tmp/ref/<component>/pipeline-state.json.
Single source of truth for which gate the pipeline is currently at.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
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
    "state-coverage",
    "post-implement",
    "boundary",
    "font-parity",
    "section-compare",
]

# Hard cap on consecutive failures of any single gate. When mark_failed()
# pushes gate_fail_counts[<gate>] to this value, the state self-records a
# canonical `category="hard-cap-fail"` reason into unclonable_reasons so
# every downstream consumer (Stop hook, goal card, benchmark harness,
# `--check-done` exit code) terminates from one persisted source of truth
# instead of inventing terminal semantics independently. goal.py re-exports
# this constant as `_MAX_GATE_FAILS` for back-compat with benchmark_harness.
HARD_CAP_GATE_FAILS = 10


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
    ("post-implement", "class-signature-preservation-mismatch"): [
        "Re-run extraction with --interactions to capture more class tokens.",
        "Verify pipeline Phase 1-6 ran (tmp/ref/<c>/ should have html/, dom-scaffold.json, structure.json). If not, the impl is freehand and won't preserve signatures.",
        "Increase scope: include hover/focus states so dynamic classes are captured.",
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
    # Hard-blocker reasons populated by gates/scripts when they detect a
    # condition the pipeline cannot work past (e.g., commercial DRM canvas,
    # auth-gated content, paid font with decision='use' and no substitution).
    # Non-empty list → goal.py --check-done exits 2 instead of 1, so external
    # loops stop iterating on an unwinnable target instead of grinding to
    # max-iterations.
    unclonable_reasons: list[dict] = field(default_factory=list)
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
            )
        except json.JSONDecodeError as exc:
            # Codex review (2026-05-24): silently returning defaults on a
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
            return cls(component=ref_dir.name)
        except OSError as exc:
            print(f"ui-clone-skills: Cannot read {path}: {exc}", file=sys.stderr)
            return cls(component=ref_dir.name)

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
        self.unclonable_reasons = [dict(r) for r in other.unclonable_reasons]
        self.impl_root = other.impl_root
        self.closeout_policy = other.closeout_policy

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
        clobbered (codex review 2026-05-24).
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
        return True

    def mark_failed(self, gate: str, ref_dir: Path) -> None:
        """Increment the consecutive-fail counter for `gate`.

        Only bumps when `gate` is the current_gate — failing a gate earlier
        than the cursor (e.g. re-running `reference` after pipeline is at
        `extraction`) does not count as "stuck on the active gate".

        Hard-cap auto-termination: when the bumped counter is at or past
        HARD_CAP_GATE_FAILS, also writes a canonical `category="hard-cap-fail"`
        entry into unclonable_reasons. Both the bump and the auto-record
        happen inside one cross-process write lock so two parallel
        mark_failed callers can't lose increments (codex review 2026-05-24).
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
            # Re-check inside the lock against the freshly-loaded snapshot.
            if authoritative.current_gate != gate:
                if authoritative is not self:
                    self._mirror_from(authoritative)
                return

            authoritative.gate_fail_counts[gate] = (
                authoritative.gate_fail_counts.get(gate, 0) + 1
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
                    reason=(
                        f"hard cap reached: gate '{gate}' failed "
                        f"{HARD_CAP_GATE_FAILS} consecutive times "
                        f"(auto-terminated by state.mark_failed)"
                    ),
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
        contributes to the dedup decision instead of being clobbered
        (codex review 2026-05-24).

        category (Step G): short machine-readable kind name (e.g.,
            "drm-canvas", "commercial-font", "auth-gated", "hard-cap-fail",
            "class-signature-preservation-mismatch"). Used by the receipt
            HTML to look up downstream documentation.

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
