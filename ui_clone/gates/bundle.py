"""Bundle gate.

Extracted from ui_clone/gate.py. Each function takes `self: "Gate"` and is
rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from .base import CheckResult

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401


_REQUIRED_ARTIFACT_STATES = {
    "css-hover": {"idle", "active"},
    "js-class": {"idle", "active"},
    "hover": {"idle", "active"},
    "intersection": {"before", "after"},
    "scroll-driven": {"before", "mid", "after"},
    "click-toggle": {"idle", "active"},
    "click-content-swap": {"video", "idle", "active"},
    "mousemove": {"video"},
    "auto-timer": {"video"},
}


def gate_bundle(self: Gate) -> list[CheckResult]:
    results = []
    results.append(
        self.check_dir(self.ref_dir / "bundles", "bundles/ (downloaded JS chunks)", min_files=1)
    )

    # Advisory: warn if fewer than 3 JS chunks
    bundles_dir = self.ref_dir / "bundles"
    if bundles_dir.is_dir():
        js_count = sum(1 for f in bundles_dir.rglob("*.js") if f.is_file())
        if 1 <= js_count < 3:
            results.append(
                CheckResult(
                    "JS chunk count",
                    "warn",
                    f"Only {js_count} JS chunk(s) — typical SPAs have \u22653. "
                    "Verify all chunks via performance.getEntriesByType('resource').",
                )
            )

    for filename, label in [
        ("interactions-detected.json", "interactions-detected.json"),
        ("scroll-engine.json", "scroll-engine.json"),
    ]:
        results.append(self.check_file(self.ref_dir / filename, label))

    interactions_payload = self._load_json("interactions-detected.json")
    interactions = (
        interactions_payload
        if isinstance(interactions_payload, list)
        else interactions_payload.get("interactions", [])
        if isinstance(interactions_payload, dict)
        else []
    )
    interactions = interactions if isinstance(interactions, list) else []
    has_discovered_interactions = any(
        isinstance(entry, dict)
        and any(
            isinstance(entry.get(key), str) and entry[key].strip()
            for key in ("trigger", "triggerType", "type")
        )
        for entry in interactions
    )
    if not has_discovered_interactions:
        hover_payload = self._load_json("hover-css-rules.json")
        hover_rules = hover_payload.get("rules") if isinstance(hover_payload, dict) else None
        if isinstance(hover_rules, list) and hover_rules:
            results.append(
                CheckResult(
                    "hover transition evidence",
                    "fail",
                    f"hover-css-rules.json lists {len(hover_rules)} hover rules but "
                    "interactions-detected.json has no interactions. An emptied "
                    "interaction set silently disables the transition-evidence "
                    "check instead of satisfying it. Re-run "
                    "scripts/extract/capture-region-artifacts.py.",
                )
            )
    if has_discovered_interactions:
        regions_payload = self._load_json("regions.json")
        region_entries: list[object] = (
            list(regions_payload) if isinstance(regions_payload, list) else []
        )
        if isinstance(regions_payload, dict):
            for value in regions_payload.values():
                if isinstance(value, list):
                    region_entries.extend(entry for entry in value if isinstance(entry, dict))
        regions_are_placeholder = isinstance(regions_payload, dict) and (
            regions_payload.get("placeholder") is True
            or regions_payload.get("detectionRan") is False
        )
        derived_from = (
            regions_payload.get("derivedFrom")
            if isinstance(regions_payload, dict)
            else None
        )
        file_dispatch_provenance = (
            isinstance(regions_payload, dict)
            and regions_payload.get("source") == "derive-from-transition-spec"
            and isinstance(derived_from, list)
            and "transition-spec.json" in derived_from
        )
        trigger_classified_regions = [
            entry
            for entry in region_entries
            if isinstance(entry, dict)
            and isinstance(entry.get("triggerType"), str)
            and entry["triggerType"].strip()
        ]
        if regions_are_placeholder or not trigger_classified_regions:
            results.append(
                CheckResult(
                    "deferred transition evidence",
                    "fail",
                    "interactions-detected.json contains discovered interactions, "
                    "but regions.json is still placeholder or has no trigger-classified "
                    "regions. Run ui-capture detection.md Phase 2, then capture the "
                    "detected transitions with capture-transitions 2B-2E.",
                )
            )
        else:
            artifact_problems: list[str] = []
            ref_root = self.ref_dir.resolve()
            for region in trigger_classified_regions:
                name = str(region.get("name") or region.get("selector") or "unnamed")
                trigger = str(region.get("triggerType") or "")
                # The canonical projector marks transition-spec routing rows as
                # dispatch-only. They are not independent per-state capture
                # proof, so no artifacts manifest is expected. Require the
                # producer's file-level provenance; a hand-set region flag
                # alone must never bypass capture evidence.
                if region.get("dispatchOnly") is True and file_dispatch_provenance:
                    continue
                artifacts = region.get("artifacts")
                if not isinstance(artifacts, dict) or not artifacts:
                    artifact_problems.append(f"{name}: missing artifacts manifest")
                    continue

                if trigger == "click-cycle":
                    state_count_value = region.get("stateCount")
                    state_count = (
                        state_count_value
                        if isinstance(state_count_value, int)
                        and not isinstance(state_count_value, bool)
                        else 0
                    )
                    required_states = (
                        {f"state-{index}" for index in range(state_count)}
                        if state_count > 0
                        else {"state-0", "state-1"}
                    )
                else:
                    required_states = _REQUIRED_ARTIFACT_STATES.get(trigger, set())

                declared_states = {key for key in artifacts if isinstance(key, str)}
                for state in sorted(required_states | declared_states):
                    path_value = artifacts.get(state)
                    if not isinstance(path_value, str) or not path_value.strip():
                        artifact_problems.append(f"{name}: {state}: missing artifact path")
                        continue

                    relative = Path(path_value)
                    if relative.is_absolute() or ".." in relative.parts:
                        artifact_problems.append(
                            f"{name}: {state}: artifact path must be relative "
                            f"under ref-dir: {path_value!r}"
                        )
                        continue
                    if len(relative.parts) < 3 or relative.parts[:2] not in {
                        ("clip", "ref"),
                        ("transitions", "ref"),
                    }:
                        artifact_problems.append(
                            f"{name}: {state}: artifact path must live under "
                            f"clip/ref/ or transitions/ref/: {path_value!r}"
                        )
                        continue
                    if any("placeholder" in part.lower() for part in relative.parts):
                        artifact_problems.append(
                            f"{name}: {state}: placeholder artifact {path_value!r}"
                        )
                        continue

                    candidate = (self.ref_dir / relative).resolve()
                    if not candidate.is_relative_to(ref_root) or not candidate.is_file():
                        artifact_problems.append(
                            f"{name}: {state}: artifact file missing {path_value!r}"
                        )
                        continue
                    try:
                        artifact_size = candidate.stat().st_size
                    except OSError:
                        artifact_size = 0
                    if artifact_size <= 0:
                        artifact_problems.append(
                            f"{name}: {state}: artifact file empty {path_value!r}"
                        )
            if artifact_problems:
                results.append(
                    CheckResult(
                        "deferred transition artifacts",
                        "fail",
                        "Discovered interactions have trigger-classified regions, "
                        "but each capture-backed region must enumerate concrete existing "
                        "ref artifacts; "
                        + "; ".join(artifact_problems[:6])
                        + ". Run ui-capture detection.md Phase 2, then capture the "
                        "detected transitions with capture-transitions 2B-2E.",
                    )
                )

    # Fail-closed on an honest bundle-extraction crash. The deterministic parser
    # is producer-only and best-effort, but pipeline.execute writes
    # bundle-extraction-status.json {"completed": false} EXACTLY when bundles/ are
    # present and the parser errored — i.e. a real site had bundles to parse and
    # the deterministic pass crashed. That is an honest mistake worth surfacing,
    # so the run can't close out green on a silently-incomplete bundle pass. A
    # successful run writes no status artifact (only bundle-extraction.json), so
    # this never trips on the green path; an absent or completed=true status adds
    # no failure. Downstream consumers (hover_probe / state_reveal) deliberately
    # stay fail-open — the Phase-5d bundle-analyzer LLM legitimately covers the
    # minified-bundle gaps the regex parser cannot resolve.
    status_path = self.ref_dir / "bundle-extraction-status.json"
    if bundles_dir.is_dir() and status_path.is_file():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError, UnicodeDecodeError):
            status = None
        if isinstance(status, dict) and status.get("completed") is False:
            advisory = status.get("advisory", "")
            results.append(
                CheckResult(
                    "bundle-extraction completion",
                    "fail",
                    "bundle-extraction-status.json reports completed=false — the "
                    "deterministic bundle parser crashed on a site that HAS bundles/. "
                    + (f"{advisory} " if isinstance(advisory, str) and advisory else "")
                    + "Re-run `bash scripts/extract/bundle-extraction.sh <ref-dir>` "
                    "and resolve the parse error (or dispatch the Phase-5d "
                    "bundle-analyzer to cover the unresolved params).",
                )
            )

    spec_payload = self._load_json("transition-spec.json")
    spec_transitions = spec_payload.get("transitions") if isinstance(spec_payload, dict) else None
    if isinstance(spec_transitions, list):
        style_only = [
            str(entry.get("id") or entry.get("target") or "transition")
            for entry in spec_transitions
            if isinstance(entry, dict)
            and isinstance(entry.get("animation"), dict)
            and entry["animation"].get("pixelCorroborated") is False
        ]
        if style_only:
            results.append(
                CheckResult(
                    "style-only transition evidence",
                    "warn",
                    f"{len(style_only)} transition(s) rest on a computed-style "
                    "delta with no pixel corroboration, because the change "
                    "renders outside the element box: "
                    + ", ".join(sorted(style_only))
                    + ". Compare these against the implementation by style, not "
                    "by the cropped frames.",
                )
            )

    return results
