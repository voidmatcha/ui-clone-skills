"""Reference gate.

Extracted from ui_clone/gate.py. Each function takes `self: "Gate"` and is
rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..extraction_artifacts import (
    _HOVER_CANDIDATE_INPUTS,
    _hover_candidate_input_fingerprint,
    _is_valid_selector,
)
from .base import CheckResult

# Mirrors scripts/extract/capture-region-artifacts.py RESOLVED_ABSENCE_MARKER:
# the prober tags a skip row with it when the browser answered that the
# candidate carries no runtime motion, and retires the region.
RESOLVED_ABSENCE_MARKER = "absence-measured"

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401


def _region_entries(regions: Any) -> list[dict[str, Any]]:
    """Return trigger-classified entries from either supported regions schema.

    Deterministic projectors emit ``{"regions": [...]}``, while the public
    ui-capture workflow groups entries under ``scroll``/``hover``/``click`` and
    related trigger families. Gates must consume the same contract the skill
    tells agents to write.
    """
    entries: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("triggerType"), str):
                entries.append(node)
                return
            for key, value in node.items():
                # A measured-negative receipt names retired candidates, not
                # regions that the page still claims or needs artifacts for.
                if key == "resolvedAutoCandidates":
                    continue
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(regions)
    return entries


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
    page = regions.get("page")
    if isinstance(page, dict):
        try:
            total_height = float(page.get("totalHeight") or 0)
        except (TypeError, ValueError):
            total_height = 0
        if total_height > 0:
            return total_height
    for r in _region_entries(regions):
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
    bounds = entry.get("bounds")
    bounds = bounds if isinstance(bounds, dict) else {}
    raw_geometry = {
        "x": entry.get("x", bounds.get("x")),
        "y": entry.get("y", bounds.get("y", entry.get("from"))),
        "width": entry.get("width", bounds.get("width", bounds.get("w"))),
        "height": entry.get("height", bounds.get("height", bounds.get("h"))),
    }
    if any(value is not None for value in raw_geometry.values()):
        geometry: dict[str, float] = {}
        try:
            geometry = {
                key: float(value)
                for key, value in raw_geometry.items()
                if value is not None
            }
        except (TypeError, ValueError):
            reasons.append(f"{name}: non-numeric geometry")
            return reasons
        declared_size_keys = any(
            key in entry for key in ("x", "y", "width", "height")
        ) or any(key in bounds for key in ("x", "y", "width", "height", "w", "h"))
        if declared_size_keys and not {"width", "height"} <= geometry.keys():
            # A region that claims geometry must claim all of it; dropping the
            # absent keys would skip every size and page-bound check below.
            reasons.append(f"{name}: incomplete geometry")
        elif (
            geometry.get("x", 0) < 0
            or geometry.get("y", 0) < 0
            or ("width" in geometry and geometry["width"] <= 0)
            or ("height" in geometry and geometry["height"] <= 0)
        ):
            reasons.append(f"{name}: degenerate/negative geometry")
        elif (
            page_height is not None
            and "y" in geometry
            and "height" in geometry
            and geometry["y"] + geometry["height"] > page_height + 1
        ):
            reasons.append(
                f"{name}: geometry y+height="
                f"{geometry['y'] + geometry['height']:g} exceeds page bound "
                f"{page_height:g}"
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

    entries = _region_entries(regions)
    by_name = {str(entry.get("name") or ""): entry for entry in entries}
    captured_names: set[str] = set()
    counts = summary.get("counts")
    counts = counts if isinstance(counts, dict) else {}
    for key in ("skipped", "unsupported", "notInstantiated"):
        rows = summary.get(key)
        if isinstance(rows, list) and rows:
            # A skip the browser actually answered — the rule's target is not
            # in this document, or it resolves to the value already shown — is
            # measured absence, and the prober retires that candidate from
            # regions.json. Its audit row is evidence, not unproven work. Any
            # other row, and any row whose region is still claimed, is fatal.
            if key != "skipped" or any(
                not isinstance(row, dict)
                or row.get("resolution") != RESOLVED_ABSENCE_MARKER
                or str(row.get("region") or "") in by_name
                for row in rows
            ):
                return False
            # The producer writes counts[key] == len(rows) after the split. A
            # summary whose count disagrees with its own rows was edited or
            # came from a different run, so the carve-out must not release it.
            count = counts.get(key)
            if isinstance(count, bool) or not isinstance(count, int) or count != len(rows):
                return False
            continue
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


def _has_resolved_auto_absence_provenance(
    self: Gate,
    regions: dict[str, Any],
) -> bool:
    """Validate a complete, fresh browser proof that auto hover candidates are inert."""
    if not (
        regions.get("source") == "scripts/extract/capture-region-artifacts.py"
        and regions.get("placeholder") is False
        and regions.get("detectionRan") is True
        and _region_entries(regions) == []
    ):
        return False
    derived = regions.get("derivedFrom")
    if not isinstance(derived, list) or set(derived) != set(_HOVER_CANDIDATE_INPUTS):
        return False
    if any(not (self.ref_dir / relative).is_file() for relative in _HOVER_CANDIDATE_INPUTS):
        return False

    fingerprint = _hover_candidate_input_fingerprint(self.ref_dir)
    if not fingerprint or regions.get("autoCandidateInputFingerprint") != fingerprint:
        return False
    spec = self._load_json("transition-spec.json")
    if not isinstance(spec, dict) or not (
        spec.get("source") == "ui_clone.extraction_artifacts"
        and spec.get("placeholder") is True
        and spec.get("transitions") == []
    ):
        return False
    summary = self._load_json("capture-region-artifacts-summary.json")
    if not isinstance(summary, dict) or not (
        summary.get("status") == "pass"
        and summary.get("autoSpec") is True
        and summary.get("autoCandidateInputFingerprint") == fingerprint
    ):
        return False

    attempted = summary.get("attempted")
    skipped = summary.get("skipped")
    resolved = regions.get("resolvedAutoCandidates")
    if not all(isinstance(rows, list) for rows in (attempted, skipped, resolved)):
        return False
    assert isinstance(attempted, list)
    assert isinstance(skipped, list)
    assert isinstance(resolved, list)
    if not attempted or len(attempted) != len(skipped) or len(attempted) != len(resolved):
        return False

    def direct_key(row: object) -> tuple[str, str] | None:
        if not isinstance(row, dict):
            return None
        trigger = str(row.get("triggerType") or "").strip().lower()
        selector = " ".join(str(row.get("selector") or "").split())
        if trigger != "hover" or not _is_valid_selector(selector):
            return None
        return trigger, selector

    attempted_keys = [direct_key(row) for row in attempted]
    resolved_keys = [direct_key(row) for row in resolved]
    if None in attempted_keys or None in resolved_keys:
        return False
    if len(set(attempted_keys)) != len(attempted_keys):
        return False
    if set(attempted_keys) != set(resolved_keys):
        return False

    skipped_keys: list[tuple[str, str] | None] = []
    for row in skipped:
        if not isinstance(row, dict) or not (
            row.get("resolution") == RESOLVED_ABSENCE_MARKER
            and row.get("autoCandidateInputFingerprint") == fingerprint
        ):
            return False
        candidate = row.get("candidateKey")
        candidate_key = direct_key(candidate)
        if candidate_key is None or direct_key(row) != candidate_key:
            return False
        skipped_keys.append(candidate_key)
    if len(set(skipped_keys)) != len(skipped_keys) or set(skipped_keys) != set(attempted_keys):
        return False

    for key in ("captured", "unsupported", "notInstantiated"):
        rows = summary.get(key, [])
        if not isinstance(rows, list) or rows:
            return False
    counts = summary.get("counts")
    expected_counts = {
        "attempted": len(attempted),
        "captured": 0,
        "skipped": len(skipped),
        "unsupported": 0,
        "notInstantiated": 0,
    }
    if not isinstance(counts, dict) or any(
        isinstance(counts.get(key), bool) or counts.get(key) != expected
        for key, expected in expected_counts.items()
    ):
        return False
    return True


def _has_real_detection_provenance(self: Gate, regions: dict[str, Any]) -> bool:
    """Accept deterministic projections or browser-measured live captures."""
    return (
        _has_derived_detection_provenance(regions)
        or _has_live_capture_provenance(self, regions)
        or _has_resolved_auto_absence_provenance(self, regions)
        or _has_inventory_capture_provenance(self, regions)
    )


def _has_transition_artifact_shape(self: Gate, regions: dict[str, Any]) -> bool:
    """Require a supported video or distinct paths for documented state pairs."""
    for entry in _region_entries(regions):
        artifacts = entry.get("artifacts")
        if not isinstance(artifacts, dict):
            continue
        video = artifacts.get("video")
        if isinstance(video, str) and video.lower().endswith((".webm", ".mp4")):
            return True
        pairs = [("idle", "active"), ("before", "after")]
        cycle_keys = [
            key for key in artifacts
            if isinstance(key, str) and key.startswith("state-") and key[6:].isdigit()
        ]
        if len(cycle_keys) >= 2:
            pairs.extend((cycle_keys[0], key) for key in cycle_keys[1:])
        for first, second in pairs:
            first_path, second_path = artifacts.get(first), artifacts.get(second)
            if not isinstance(first_path, str) or not isinstance(second_path, str):
                continue
            if not first_path.lower().endswith(".png") or not second_path.lower().endswith(".png"):
                continue
            if (self.ref_dir / first_path).resolve() != (self.ref_dir / second_path).resolve():
                return True
    return False


def _transition_evidence_result(self: Gate) -> CheckResult:
    """Accept motion videos or browser-measured interactive state pairs.

    Hover and click capture deliberately use idle/active PNG pairs rather than
    video. Requiring a WebM for every site contradicts that public contract and
    creates a false blocker after the live bridge or curated capture inventory
    has already proved the transition with matching region provenance. Curated
    inventories also support motion videos in formats other than WebM.
    """
    video_result = self.check_dir(
        self.ref_dir / "transitions" / "ref",
        "transitions/ref motion evidence",
        min_files=1,
        pattern="*.webm",
        fix=(
            "Capture a transition video, or run "
            "scripts/extract/capture-region-artifacts.py for measured idle/active states"
        ),
    )
    if video_result.status == "pass":
        return video_result
    regions = _load_regions(self)
    if isinstance(regions, dict) and (
        _has_live_capture_provenance(self, regions)
        or _has_inventory_capture_provenance(self, regions)
    ) and _has_transition_artifact_shape(self, regions):
        return CheckResult(
            "interactive transition state evidence",
            "pass",
            "captured transition artifacts with matching live or inventory provenance",
        )
    if isinstance(regions, dict) and _has_resolved_auto_absence_provenance(self, regions):
        return CheckResult(
            "measured transition absence",
            "pass",
            "all auto hover candidates were freshly probed and measured inert",
        )
    return video_result


def _has_inventory_capture_provenance(self: Gate, regions: dict[str, Any]) -> bool:
    """Validate the public ui-capture artifact-inventory handoff.

    Interactive ui-capture runs are intentionally curated rather than emitted
    by the deterministic bridge. Their machine-checkable provenance is the
    inventory report: every declared region and concrete artifact path must be
    present, non-empty, and reported by the inventory checker.
    """
    entries = _region_entries(regions)
    if not entries:
        return False
    inventory = self._load_json("capture-artifact-inventory.json")
    if not isinstance(inventory, dict) or inventory.get("status") != "pass":
        return False
    if inventory.get("regionsChecked") != len(entries):
        return False
    missing = inventory.get("missingArtifacts")
    if not isinstance(missing, list) or missing:
        return False
    checked = inventory.get("checkedArtifacts")
    if not isinstance(checked, list) or not checked:
        return False
    checked_rows: set[tuple[str, str, str]] = set()
    ref_root = self.ref_dir.resolve()
    for row in checked:
        if not isinstance(row, dict):
            return False
        relative = row.get("path")
        byte_count = row.get("bytes")
        if not isinstance(relative, str) or not relative or not isinstance(byte_count, int):
            return False
        candidate = (self.ref_dir / relative).resolve()
        try:
            candidate.relative_to(ref_root)
        except ValueError:
            return False
        if not candidate.is_file() or byte_count <= 0 or candidate.stat().st_size != byte_count:
            return False
        checked_rows.add(
            (
                str(row.get("region") or ""),
                str(row.get("triggerType") or ""),
                relative,
            )
        )
    for index, entry in enumerate(entries):
        name = str(entry.get("name") or entry.get("selector") or f"region-{index}")
        trigger = str(entry.get("triggerType") or "")
        artifacts = entry.get("artifacts")
        if not isinstance(artifacts, dict) or not artifacts:
            return False
        if any(not isinstance(value, str) or not value for value in artifacts.values()):
            return False
        paths = set(artifacts.values())
        region_rows = {
            path
            for row_name, row_trigger, path in checked_rows
            if row_name == name and row_trigger == trigger
        }
        # Extra exploratory inventory rows remain allowed, but every artifact
        # declared by the region must be present and byte-verified.
        if not paths or not paths.issubset(region_rows):
            return False
    return True


def _load_regions(self: Gate) -> dict[str, Any] | None:
    """Load regions.json, normalizing a bare-list root to ``{"regions": [...]}``.

    ``_region_entries`` already walks list roots; coercing them to None here
    would skip the placeholder/geometry/provenance checks entirely.
    """
    data = self._load_json_any("regions.json")
    if isinstance(data, list):
        return {"regions": data}
    return data if isinstance(data, dict) else None


def _check_regions_not_placeholder(self: Gate) -> CheckResult | None:
    """Warn on provisional placeholders and fail fabricated real regions.

    The pipeline driver auto-mints a single full-page region (tagged
    placeholder/detectionRan=false) purely to satisfy the existence row —
    Phase-2 transition detection then never has to run. When the site
    shows motion evidence, detection must actually replace the placeholder
    before generation. Reference acquisition stays nonblocking so Phase 2 can
    run and produce that evidence."""
    regions = _load_regions(self)
    if not isinstance(regions, dict):
        if (self.ref_dir / "regions.json").exists():
            return CheckResult(
                "regions.json from real detection",
                "fail",
                "regions.json exists but is not a JSON object or region list; "
                "malformed detection output cannot be validated.",
                fix="Re-run ui-capture detection so regions.json is regenerated.",
            )
        return None
    entries = _region_entries(regions)
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
        # provenance or matching capture-inventory evidence.
        if not _has_real_detection_provenance(self, regions):
            return CheckResult(
                "regions.json real-detection provenance",
                "fail",
                "regions.json claims real detection (non-placeholder with "
                "triggerType entries) but carries neither transition-spec "
                "derivation nor matching capture-inventory evidence — fabricated "
                "region bands are rejected.",
                fix="Re-derive regions.json from transition-spec.json + section-map.json, "
                "run the ui-capture artifact inventory check, or rerun "
                "scripts/extract/capture-region-artifacts.py so the summary "
                "and measured artifact files match regions.json.",
            )
        return None
    if not entries and _has_resolved_auto_absence_provenance(self, regions):
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
    screenshots = self.ref_dir / "static" / "ref"
    screenshot_count = sum(path.is_file() for path in screenshots.glob("*.png"))
    results.append(
        CheckResult(
            "static/ref screenshots",
            "pass" if screenshot_count >= 5 else "fail",
            f"static/ref contains {screenshot_count} direct PNG files (need ≥5)",
            fix="Run Phase 1: invoke /ui-capture <url> to capture reference screenshots",
        )
    )
    results.append(_transition_evidence_result(self))
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
