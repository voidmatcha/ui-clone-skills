"""Spec gate.

Extracted from ui_clone/gate.py. Each function takes `self: "Gate"` and is
rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import CheckResult
from .post_implement import _check_spec_bundle_grounding

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401


def _non_empty(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, list | tuple | set | dict | str):
        return len(value) > 0
    if isinstance(value, int | float):
        return value > 0
    return True


def _runtime_motion_signals(dump: dict[str, Any]) -> list[str]:
    signals: list[str] = []

    scroll_trigger = dump.get("scrollTrigger") or dump.get("scrollTriggers")
    if isinstance(scroll_trigger, list) and scroll_trigger:
        signals.append(f"ScrollTrigger[{len(scroll_trigger)}]")
    elif isinstance(scroll_trigger, dict) and _non_empty(scroll_trigger):
        signals.append("ScrollTrigger")

    gsap = dump.get("gsap")
    if isinstance(gsap, dict) and _non_empty(gsap):
        signals.append("GSAP")
    elif isinstance(gsap, list) and gsap:
        signals.append(f"GSAP[{len(gsap)}]")

    for key in ("framer", "framerMotion", "motion"):
        val = dump.get(key)
        if _non_empty(val):
            signals.append("Framer")
            break

    ix2 = dump.get("ix2") or dump.get("webflowIx2") or dump.get("webflow")
    if isinstance(ix2, dict):
        timeline_count = ix2.get("timelineCount") or ix2.get("timelinesCount") or 0
        event_count = ix2.get("eventCount") or ix2.get("eventsCount") or 0
        timelines = ix2.get("timelines") or ix2.get("timelineKeys")
        if _non_empty(timeline_count) or _non_empty(event_count) or _non_empty(timelines):
            signals.append("Webflow IX2")
    elif _non_empty(ix2):
        signals.append("Webflow IX2")

    return signals


def _check_runtime_motion_spec_coverage(self: Gate) -> CheckResult | None:
    dump = self._load_json("animation-runtime-dump.json")
    if not dump:
        return None
    signals = _runtime_motion_signals(dump)
    if not signals:
        return None

    spec = self._load_json("transition-spec.json")
    transitions = spec.get("transitions") if isinstance(spec, dict) else None
    if isinstance(transitions, list) and transitions:
        return None

    if spec is None:
        spec_state = "missing"
    elif isinstance(transitions, list):
        spec_state = "empty"
    else:
        spec_state = "missing valid `transitions` list"

    return CheckResult(
        "runtime motion transition-spec coverage",
        "fail",
        "animation-runtime-dump.json shows runtime motion "
        f"({', '.join(signals)}) but transition-spec.json is {spec_state}. "
        "Re-run Step 5d from bundle-analysis.md and transition-spec-rules.md, "
        "using animation-runtime-dump.json as the evidence source for runtime "
        "motion that bundle grep missed.",
        fix="bash $PLUGIN_ROOT/scripts/extract/extract-animation-runtime.sh "
            "<session> <ref-dir> && python -m ui_clone.gate <ref-dir> spec",
    )


def gate_spec(self: Gate) -> list[CheckResult]:
    results = []
    results.append(
        self.check_file(
            self.ref_dir / "bundle-map.json",
            "bundle-map.json (Step 5d input — {} for static sites)",
        )
    )
    results.append(
        self.check_file(
            self.ref_dir / "external-sdks.json",
            "external-sdks.json (GSAP/Lenis/Framer detection — {} for no SDKs)",
        )
    )
    results.append(
        self.check_file(
            self.ref_dir / "transition-spec.json",
            "transition-spec.json (single source of truth)",
        )
    )
    # verification-plan.json declares site-specific required checks
    # (hydration, scroll-end-completion, reveal-trigger, etc.) derived from
    # the signals in extraction artifacts. It must exist by spec time so
    # gate_post_implement can enforce each declared check; otherwise the
    # universal `hydration-check` row is silently skipped.
    plan = self.ref_dir / "verification-plan.json"
    results.append(
        self.check_file(
            plan,
            "verification-plan.json (run skills/visual-debug/scripts/verification-plan.sh)",
        )
    )

    # Validate transition-spec structure
    spec = self._load_json("transition-spec.json")
    runtime_motion_result = _check_runtime_motion_spec_coverage(self)
    if runtime_motion_result is not None:
        results.append(runtime_motion_result)
    if spec is not None:
        transitions = spec.get("transitions")
        if not isinstance(transitions, list):
            results.append(
                CheckResult(
                    "transitions list",
                    "fail",
                    "transition-spec.json: `transitions` must be a list (got "
                    f"{type(transitions).__name__}). Re-run Step 5d so the "
                    f"spec captures the observed interactions.",
                )
            )
            transitions = []
        elif len(transitions) == 0:
            results.append(
                CheckResult(
                    "transitions non-empty",
                    "fail",
                    "transition-spec.json: `transitions` is empty. Every site "
                    "the cloner targets has at least page-load / hover / scroll "
                    "/ click handlers — re-run Step 5/6 (animation-detection.md "
                    "Phase A-C) and Step 5d to record them. Empty spec = the "
                    "downstream coverage check has nothing to enforce.",
                )
            )
        required_transition_keys = (
            "id",
            "trigger",
            "source_chunk",
            "bundle_branch",
            "target",
            "animation",
            "reference_frames",
        )
        for index, transition in enumerate(transitions):
            missing_keys = [
                k for k in required_transition_keys if k not in transition
            ]
            if missing_keys:
                results.append(
                    CheckResult(
                        f"transitions[{index}] keys",
                        "fail",
                        f"transitions[{index}] missing required keys: {missing_keys}",
                    )
                )
            else:
                results.append(
                    CheckResult(
                        f"transitions[{index}] keys",
                        "pass",
                        f"transitions[{index}] has required keys ({len(transitions)} total)",
                    )
                )
        source_chunk_grounding = _check_spec_bundle_grounding(self)
        if source_chunk_grounding is not None:
            results.append(source_chunk_grounding)

    # Cross-validate against paid-features decisions: any font marked
    # decision='substitute' at 5c-c MUST be declared in asset-substitution.json
    # by spec time, otherwise font-parity will FAIL after generation.
    results.extend(self._check_paid_font_substitution())

    # Capture verification frames
    verify_frames = (
        sum(1 for f in (self.ref_dir / "verify").rglob("*.png") if f.is_file())
        if (self.ref_dir / "verify").is_dir()
        else 0
    )
    if verify_frames >= 5:
        results.append(
            CheckResult(
                "capture verification",
                "pass",
                f"capture verification frames ({verify_frames} frames in verify/)",
            )
        )
    else:
        results.append(
            CheckResult(
                "capture verification",
                "warn",
                f"capture verification missing ({verify_frames} frames — need \u22655). "
                "See interaction-detection.md 'MANDATORY: Capture Verification'.",
            )
        )

    return results
