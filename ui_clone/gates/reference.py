"""Reference gate.

Extracted from ui_clone/gate.py. Each function takes `self: "Gate"` and is
rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..extraction_artifacts import _is_valid_selector
from .base import CheckResult

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401


def _page_height_bound(regions: dict[str, Any], section_map: Any) -> float | None:
    """Best-effort page height for in-bounds checks.

    Prefers section-map ground truth (max top+height across sections); falls
    back to the largest region height (the placeholder full-page entry).
    Returns None when neither is decodable — then only degenerate/negative
    geometry is rejected, never the upper bound."""
    bounds: list[float] = []
    if isinstance(section_map, dict):
        for s in section_map.get("sections") or []:
            if isinstance(s, dict):
                try:
                    bounds.append(float(s.get("top") or 0) + float(s.get("height") or 0))
                except (TypeError, ValueError):
                    continue
    if bounds:
        return max(bounds)
    heights: list[float] = []
    for r in regions.get("regions") or []:
        if isinstance(r, dict) and "height" in r:
            try:
                heights.append(float(r["height"]))
            except (TypeError, ValueError):
                continue
    return max(heights) if heights else None


def _region_geometry_problems(entry: dict[str, Any], page_height: float | None) -> list[str]:
    """Reasons a real-detection region entry is invalid (empty list = ok).

    Only entries that claim real detection (carry a triggerType) are checked:
    they must resolve a valid selector, and any geometry they carry must be
    non-degenerate and within page bounds. This lets the reference gate FAIL
    fabricated regions like {x:-99,y:-99,width:0,height:0,triggerType:scroll}
    instead of rubber-stamping them."""
    trigger = entry.get("triggerType")
    if not (isinstance(trigger, str) and trigger.strip()):
        return []
    name = str(entry.get("name") or trigger)
    reasons: list[str] = []
    selector = entry.get("selector") or entry.get("target")
    if not _is_valid_selector(selector):
        reasons.append(f"{name}: missing/invalid selector")
    if any(k in entry for k in ("x", "y", "width", "height")):
        try:
            x = float(entry.get("x", 0))
            y = float(entry.get("y", 0))
            w = float(entry.get("width", 0))
            h = float(entry.get("height", 0))
        except (TypeError, ValueError):
            reasons.append(f"{name}: non-numeric geometry")
            return reasons
        if x < 0 or y < 0 or w <= 0 or h <= 0:
            reasons.append(f"{name}: degenerate/negative geometry")
        elif page_height is not None and y + h > page_height + 1:
            reasons.append(
                f"{name}: geometry y+height={y + h:g} exceeds page bound {page_height:g}"
            )
    return reasons


def _capture_error_result(self: Gate) -> CheckResult | None:
    """Surface structured capture diagnostics next to reference gate failures."""
    path = self.ref_dir / "capture-error.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return CheckResult(
            "capture-error.json",
            "warn",
            f"capture-error.json is present but unreadable: {exc}",
            fix=f"Inspect or regenerate {path}",
        )

    stage = str(payload.get("stage") or "unknown-stage")
    artifact = payload.get("artifact")
    message = str(payload.get("message") or "").strip()
    artifact_s = f" while writing {artifact}" if artifact else ""
    message_s = f": {message}" if message else ""
    return CheckResult(
        "capture-error.json",
        "warn",
        f"Phase 1 capture diagnostic: {stage}{artifact_s}{message_s}",
        fix=f"Inspect {path} before retrying Phase 1",
    )


def _has_derived_detection_provenance(regions: dict[str, Any]) -> bool:
    """Return whether regions are a deterministic transition-spec projection."""
    if regions.get("source") != "derive-from-transition-spec":
        return False
    derived = regions.get("derivedFrom")
    return isinstance(derived, list) and any("transition-spec" in str(d) for d in derived)


def _has_live_capture_provenance(self: Gate, regions: dict[str, Any]) -> bool:
    """Return whether the live bridge left matching browser-measured evidence."""
    summary_name = "capture-region-artifacts-summary.json"
    if regions.get("source") != "scripts/extract/capture-region-artifacts.py":
        return False
    if regions.get("liveCaptureBacked") is not True:
        return False
    derived = regions.get("derivedFrom")
    if not isinstance(derived, list) or summary_name not in derived:
        return False

    summary = self._load_json(summary_name)
    if not isinstance(summary, dict) or summary.get("status") != "pass":
        return False
    captured = summary.get("captured") if isinstance(summary, dict) else None
    if not isinstance(captured, list) or not captured:
        return False

    entries = [entry for entry in regions.get("regions") or [] if isinstance(entry, dict)]
    by_name = {str(entry.get("name") or ""): entry for entry in entries}
    captured_names: set[str] = set()
    counts = summary.get("counts")
    counts = counts if isinstance(counts, dict) else {}
    for key in ("skipped", "unsupported", "notInstantiated"):
        rows = summary.get(key)
        if isinstance(rows, list) and rows:
            return False
        count = counts.get(key)
        if isinstance(count, int) and count > 0:
            return False
    captured_names.update(
        str(row.get("region") or "") for row in captured if isinstance(row, dict)
    )
    if any(str(entry.get("name") or "") not in captured_names for entry in entries):
        return False

    ref_root = self.ref_dir.resolve()
    for evidence in captured:
        if not isinstance(evidence, dict):
            return False
        name = str(evidence.get("region") or "")
        region = by_name.get(name)
        if region is None:
            return False
        if str(region.get("triggerType") or "").strip().lower() != str(
            evidence.get("triggerType") or ""
        ).strip().lower():
            return False
        if str(region.get("selector") or "").strip() != str(
            evidence.get("selector") or ""
        ).strip():
            return False

        artifacts = evidence.get("artifacts")
        if not isinstance(artifacts, dict) or len(artifacts) < 2:
            return False
        if region.get("artifacts") != artifacts:
            return False
        for relative in artifacts.values():
            if not isinstance(relative, str) or not relative:
                return False
            try:
                path = (ref_root / relative).resolve()
                path.relative_to(ref_root)
                if not path.is_file() or path.stat().st_size <= 0:
                    return False
            except (OSError, ValueError):
                return False

        observation = evidence.get("observation")
        changed = (
            observation.get("changedProperties")
            if isinstance(observation, dict)
            else None
        )
        if not isinstance(changed, list) or not any(
            isinstance(prop, str) and prop.strip() for prop in changed
        ):
            return False
    return True


def _has_real_detection_provenance(self: Gate, regions: dict[str, Any]) -> bool:
    """Accept deterministic projections or browser-measured live captures."""
    return _has_derived_detection_provenance(regions) or _has_live_capture_provenance(
        self, regions
    )


def _check_regions_not_placeholder(self: Gate) -> CheckResult | None:
    """Warn on provisional placeholders and fail fabricated real regions.

    The pipeline driver auto-mints a single full-page region (tagged
    placeholder/detectionRan=false) purely to satisfy the existence row —
    Phase-2 transition detection then never has to run. When the site
    shows motion evidence, detection must actually replace the placeholder
    before generation. Reference acquisition stays nonblocking so Phase 2 can
    run and produce that evidence."""
    regions = self._load_json("regions.json")
    if not isinstance(regions, dict):
        return None
    entries = regions.get("regions")
    entries = entries if isinstance(entries, list) else []
    is_placeholder = bool(regions.get("placeholder")) or (
        regions.get("detectionRan") is False
    )
    # Motion evidence is independent of regions.json. A provisional placeholder
    # is acceptable only before Phase 2 has discovered any motion signal; once
    # motion exists, keeping the placeholder would hide a skipped detection pass.
    plan = self._load_json("verification-plan.json")
    signals = plan.get("signals") if isinstance(plan, dict) else {}
    signals = signals if isinstance(signals, dict) else {}
    motion_signals = any(
        signals.get(k)
        for k in ("hasScrollScrub", "hasScrollStateMachine", "hasIOReveal", "hasHover")
    )
    hover = self._load_json("hover-css-rules.json")
    hover_rules = hover.get("rules") if isinstance(hover, dict) else []
    if is_placeholder:
        if motion_signals or hover_rules:
            return CheckResult(
                "regions.json from real detection",
                "fail",
                "regions.json is the auto-minted placeholder while the site shows "
                "motion evidence — Phase 2 transition detection never ran. Run "
                "ui-capture detection.md Phase 2 + capture-transitions.md 2B-2E "
                "to replace the placeholder with real trigger-classified regions.",
            )
        return CheckResult(
            "regions.json from real detection",
            "warn",
            "regions.json is an auto placeholder (placeholder=true or "
            "detectionRan=false). Phase 1 may continue with provisional "
            "capture evidence, but this is not generation-ready transition "
            "evidence.",
            fix="Run Phase 1/2 capture so regions.json is replaced with browser-measured "
            "or transition-spec-derived regions. For a genuinely static page, add a "
            "typed static/no-motion artifact contract before relying on placeholders.",
        )
    # Geometry/selector validity: a non-placeholder regions.json is real
    # detection and must survive validation, otherwise the gate is a tautology
    # that rubber-stamps fabricated bands (Fix 5 review finding).
    if not is_placeholder:
        page_height = _page_height_bound(regions, self._load_json("section-map.json"))
        problems: list[str] = []
        for r in entries:
            if isinstance(r, dict):
                problems.extend(_region_geometry_problems(r, page_height))
        if problems:
            return CheckResult(
                "regions.json geometry/selector validity",
                "fail",
                "regions.json claims real detection but carries invalid "
                "region(s): " + "; ".join(problems[:6]),
                fix="Re-derive regions.json from transition-spec.json + "
                "section-map.json ground truth (selectors + in-bounds geometry).",
            )
    has_trigger_types = any(
        isinstance(r, dict) and r.get("triggerType") for r in entries
    )
    if not is_placeholder and has_trigger_types:
        # The real-detection pass path: a non-placeholder file with triggerType
        # entries claims Phase-2 detection ran. Mere shape (triggerType + valid
        # selector) is forgeable, so require either deterministic projection
        # provenance or matching live-capture evidence.
        if not _has_real_detection_provenance(self, regions):
            return CheckResult(
                "regions.json real-detection provenance",
                "fail",
                "regions.json claims real detection (non-placeholder with "
                "triggerType entries) but carries neither transition-spec "
                "derivation nor matching live-capture evidence — fabricated "
                "region bands are rejected.",
                fix="Re-derive regions.json from transition-spec.json + section-map.json, "
                "or rerun scripts/extract/capture-region-artifacts.py so the summary "
                "and measured artifact files match regions.json.",
            )
        return None
    if not motion_signals and not hover_rules:
        return None
    return CheckResult(
        "regions.json from real detection",
        "fail",
        "regions.json is the auto-minted placeholder (or carries no "
        "triggerType entries) while the site shows motion evidence — "
        "Phase 2 transition detection never ran. Run ui-capture "
        "detection.md Phase 2 + capture-transitions.md 2B-2E to replace "
        "the placeholder with real trigger-classified regions.",
    )


def gate_reference(self: Gate) -> list[CheckResult]:
    results = []
    results.append(
        self.check_dir(
            self.ref_dir / "static" / "ref",
            "static/ref screenshots",
            min_files=5,
            fix="Run Phase 1: invoke /ui-capture <url> to capture reference screenshots",
        )
    )
    results.append(
        self.check_dir(
            self.ref_dir / "transitions" / "ref",
            "transitions/ref (transition videos)",
            min_files=1,
            fix="Run Phase 1: invoke /ui-capture <url> to capture transition videos",
        )
    )
    results.append(
        self.check_file(
            self.ref_dir / "regions.json",
            "regions.json (transition regions)",
            fix="Run Phase 1: invoke /ui-capture <url> to generate regions.json",
        )
    )
    regions_placeholder = _check_regions_not_placeholder(self)
    if regions_placeholder is not None:
        results.append(regions_placeholder)
    capture_error = _capture_error_result(self)
    if capture_error is not None:
        results.append(capture_error)
    return results
