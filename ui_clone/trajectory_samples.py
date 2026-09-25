"""Derive local trajectory probes from captured document-progress samples."""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def _target_source_ids(
    spec: dict[str, Any], targets: list[str]
) -> dict[str, set[str]]:
    """Map each spec target selector to the sourceId(s) it links via the same
    sourceArtifact/sourceId contract `ui_clone/gates/spec.py` accepts (a spec
    target may cite a runtime site whose captured selector differs textually,
    e.g. `#hero` linked to `div#hero.card`)."""
    wanted = set(targets)
    out: dict[str, set[str]] = {}
    transitions = spec.get("transitions")
    if not isinstance(transitions, list):
        return out
    for entry in transitions:
        if not isinstance(entry, dict):
            continue
        target = str(entry.get("target") or "").strip()
        source_id = entry.get("sourceId")
        if (
            target in wanted
            and entry.get("sourceArtifact") == "animation-runtime-dump.json"
            and isinstance(source_id, str)
            and source_id.strip()
        ):
            out.setdefault(target, set()).add(source_id.strip())
    return out


def _targets_requiring_runtime_evidence(
    spec: dict[str, Any], targets: list[str]
) -> set[str]:
    """Which protected targets are contractually obligated to have a captured
    scrollLinkedStyles/byScroll row.

    Only `sourceArtifact: animation-runtime-dump.json` transitions are backed
    by the runtime dump's inline-style probe (`sampleInlineMotion` in
    `scripts/extract/extract-animation-runtime.js` scans `[style]` elements
    only), so only THOSE targets can be held to "missing evidence == exit 2".
    A bundle-sourced GSAP entry (`sourceArtifact: bundle-extraction.json`) or a
    class-toggle scroll-state-machine with no runtime-dump linkage structurally
    never gets a byScroll row no matter how many times it is recaptured —
    treating its absence as a hard failure created a dead end (follow-up
    review: F1's identity-resolution fix closed the textual-selector
    mismatch but not this "no evidence is even possible" branch)."""
    wanted = set(targets)
    out: set[str] = set()
    transitions = spec.get("transitions")
    if not isinstance(transitions, list):
        return out
    for entry in transitions:
        if not isinstance(entry, dict):
            continue
        target = str(entry.get("target") or "").strip()
        if target in wanted and entry.get("sourceArtifact") == "animation-runtime-dump.json":
            out.add(target)
    return out


def sample_points(
    targets: list[str],
    runtime: dict[str, Any],
    defaults: Sequence[float],
    source_ids: dict[str, set[str]] | None = None,
    requires_evidence: set[str] | None = None,
) -> tuple[list[float], list[str], list[str]]:
    points = set(defaults)
    missing: list[str] = []
    unresolved: list[str] = []
    requires_evidence = requires_evidence or set()
    for target in targets:
        usable = False
        target_ids = (source_ids or {}).get(target)
        for entry in runtime.get("scrollLinkedStyles", []):
            if not isinstance(entry, dict):
                continue
            entry_matches = entry.get("selector") == target or (
                target_ids and entry.get("sourceId") in target_ids
            )
            if not entry_matches:
                continue
            samples = entry.get("byScroll")
            if not isinstance(samples, dict):
                continue
            properties = entry.get("varies") or entry.get("filter") or []
            pairs = []
            for progress, value in samples.items():
                try:
                    fraction = float(progress)
                except (TypeError, ValueError):
                    continue
                if (
                    not math.isfinite(fraction)
                    or not 0 <= fraction <= 1
                    or not isinstance(value, dict)
                ):
                    continue
                observed = {
                    key: val
                    for key, val in value.items()
                    if val is not None and (not properties or key in properties)
                }
                if observed:
                    pairs.append((fraction, observed))
            pairs.sort(key=lambda pair: pair[0])
            if len(pairs) < 2:
                continue
            usable = True
            for (start, before), (end, after) in zip(pairs, pairs[1:]):
                if before != after:
                    points.update((start * 100, (start + end) * 50, end * 100))
        if not usable:
            (missing if target in requires_evidence else unresolved).append(target)
    return sorted(points), missing, unresolved


def main(args: list[str]) -> int:
    ref = Path(args[0])
    targets = [s.strip() for s in args[1].split(",") if s.strip()]
    try:
        runtime = json.loads((ref / "animation-runtime-dump.json").read_text())
    except (OSError, ValueError):
        runtime = {}
    try:
        spec = json.loads((ref / "transition-spec.json").read_text())
    except (OSError, ValueError):
        spec = {}
    spec_dict = spec if isinstance(spec, dict) else {}
    source_ids = _target_source_ids(spec_dict, targets)
    requires_evidence = _targets_requiring_runtime_evidence(spec_dict, targets)
    points, missing, unresolved = sample_points(
        targets,
        runtime if isinstance(runtime, dict) else {},
        [float(s) for s in args[2:]],
        source_ids,
        requires_evidence,
    )
    receipt = ref / "transitions/trajectory-sampling.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "error" if missing else ("warn" if unresolved else "pass"),
                "pointsPct": points,
                "missingRangeTargets": missing,
                "unresolvedLocalRangeTargets": unresolved,
                "source": "animation-runtime-dump.json#scrollLinkedStyles.byScroll",
                "action": "Capture document-progress style samples for missing targets; do not infer local motion from global quarter-page samples. unresolvedLocalRangeTargets are bundle/class-toggle-sourced targets the runtime-dump probe structurally cannot capture (no inline style to sample) — global quarter-page points still apply to them.",
            },
            indent=2,
        )
        + "\n"
    )
    if missing:
        print("Missing captured local trajectory ranges: " + ", ".join(missing), file=sys.stderr)
        return 2
    if unresolved:
        print(
            "No inline-style evidence for (using global points only): " + ", ".join(unresolved),
            file=sys.stderr,
        )
    print(" ".join(format(value, ".8g") for value in points))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
