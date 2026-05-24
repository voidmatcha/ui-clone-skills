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
        except json.JSONDecodeError:
            return cls(component=ref_dir.name)
        except OSError as exc:
            print(f"ui-clone-skills: Cannot read {path}: {exc}", file=sys.stderr)
            return cls(component=ref_dir.name)

    def save(self, ref_dir: Path) -> None:
        """Write the current in-memory state to pipeline-state.json atomically.

        Used by callers that mutate fields outside of `mark_passed` /
        `mark_failed` / `record_unclonable` (e.g. `impl_root` write at
        Phase 1 start in `pipeline.execute_phases`).
        """
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
                    # Emit both keys so find-impl-root.sh (camelCase
                    # `implRoot`) and any internal reader (snake_case
                    # `impl_root`) both work. Omit when empty to keep
                    # legacy state files compact.
                    **({"implRoot": self.impl_root, "impl_root": self.impl_root}
                       if self.impl_root else {}),
                    # closeout_policy persists only when non-default to avoid
                    # diff churn on every save on legacy canonical runs.
                    **({"closeoutPolicy": self.closeout_policy}
                       if self.closeout_policy != "canonical" else {}),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)

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
                    # Emit both keys so find-impl-root.sh (camelCase
                    # `implRoot`) and any internal reader (snake_case
                    # `impl_root`) both work. Omit when empty to keep
                    # legacy state files compact.
                    **({"implRoot": self.impl_root, "impl_root": self.impl_root}
                       if self.impl_root else {}),
                    # closeout_policy persists only when non-default to avoid
                    # diff churn on every save on legacy canonical runs.
                    **({"closeoutPolicy": self.closeout_policy}
                       if self.closeout_policy != "canonical" else {}),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)

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

    def mark_passed(self, gate: str, ref_dir: Path) -> None:
        """Record gate as passed and advance current_gate. Writes file atomically.

        Skips the write when the gate was already recorded and current_gate
        would not advance — avoids unnecessary filesystem churn on re-runs.
        """
        if gate not in GATE_ORDER:
            return
        if self.missing_prerequisites(gate):
            return

        already_recorded = gate in self.completed_steps
        if not already_recorded:
            self.completed_steps.append(gate)
            self._normalize_completed_steps()

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
                    # Emit both keys so find-impl-root.sh (camelCase
                    # `implRoot`) and any internal reader (snake_case
                    # `impl_root`) both work. Omit when empty to keep
                    # legacy state files compact.
                    **({"implRoot": self.impl_root, "impl_root": self.impl_root}
                       if self.impl_root else {}),
                    # closeout_policy persists only when non-default to avoid
                    # diff churn on every save on legacy canonical runs.
                    **({"closeoutPolicy": self.closeout_policy}
                       if self.closeout_policy != "canonical" else {}),
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

        Hard-cap auto-termination: when the bumped counter is at or past
        HARD_CAP_GATE_FAILS, also writes a canonical `category="hard-cap-fail"`
        entry into unclonable_reasons (via record_unclonable, which handles
        dedup + on-disk persistence). This makes terminalization a property
        of the persisted state instead of a banner that hooks/banners/harness
        each interpret separately. Observed before this fix: linear-app hit
        97 consecutive post-implement failures and realfood-gov hit 6 without
        any of them triggering the Stop-hook bypass — abort_banner fired in
        goal.py but pipeline-state.json had no canonical reason, so the Stop
        hook re-enforced the gate forever.
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
                    # Emit both keys so find-impl-root.sh (camelCase
                    # `implRoot`) and any internal reader (snake_case
                    # `impl_root`) both work. Omit when empty to keep
                    # legacy state files compact.
                    **({"implRoot": self.impl_root, "impl_root": self.impl_root}
                       if self.impl_root else {}),
                    # closeout_policy persists only when non-default to avoid
                    # diff churn on every save on legacy canonical runs.
                    **({"closeoutPolicy": self.closeout_policy}
                       if self.closeout_policy != "canonical" else {}),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)

        # Hard-cap terminalization. record_unclonable() dedups by (gate,
        # reason) — the reason string deliberately omits the live count
        # (uses the cap only) so subsequent bumps after the cap don't
        # produce N-duplicate entries. The exact count at the moment of
        # termination is still reconstructable from gate_fail_counts on
        # disk; detected_at is auto-added by record_unclonable.
        if self.gate_fail_counts[gate] >= HARD_CAP_GATE_FAILS:
            self.record_unclonable(
                gate=gate,
                reason=(
                    f"hard cap reached: gate '{gate}' failed "
                    f"{HARD_CAP_GATE_FAILS} consecutive times "
                    f"(auto-terminated by state.mark_failed)"
                ),
                ref_dir=ref_dir,
                category="hard-cap-fail",
            )

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
        for existing in self.unclonable_reasons:
            if existing.get("gate") == gate and existing.get("reason") == reason:
                return

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
                    # Emit both keys so find-impl-root.sh (camelCase
                    # `implRoot`) and any internal reader (snake_case
                    # `impl_root`) both work. Omit when empty to keep
                    # legacy state files compact.
                    **({"implRoot": self.impl_root, "impl_root": self.impl_root}
                       if self.impl_root else {}),
                    # closeout_policy persists only when non-default to avoid
                    # diff churn on every save on legacy canonical runs.
                    **({"closeoutPolicy": self.closeout_policy}
                       if self.closeout_policy != "canonical" else {}),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)
