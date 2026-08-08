"""
Artifact dependency DAG for the extraction pipeline.

Replaces flat mtime comparisons with transitive dependency tracking.
Uses Kahn's algorithm for topological ordering and cycle detection.

Edge direction convention:
    DEPS maps parent → [children that depend on it].
    "If structure.json changes, section-map.json and extracted.json become stale."
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

GENERATION_PLAN_SOURCES = (
    "section-map.json",
    "styles.json",
    "css/variables.txt",
    "signature-effects-candidates.json",
    "structure.json",
    "animations-detected.json",
    "transition-spec.json",
    "element-roles.json",
    "element-groups.json",
    "layout-decisions.json",
    "component-map.json",
    "asset-substitution.json",
    "font-parity.json",
    "bundle-extraction.json",
    "sticky-elements.json",
    "hidden-elements.json",
    "mobile-swap.json",
    "animation-init-styles.json",
    "external-sdks.json",
    "bundle-map.json",
    "scroll-engine.json",
    "paid-features.json",
    "canvas-webgl-detection.json",
    "required-media.json",
    "dom-scaffold.json",
    "extracted.json",
    "head.json",
    "css/*.css",
)

# Directed edges: artifact → [artifacts that depend on it]
# Read: "if X changes, these artifacts become stale"
DEPS: dict[str, list[str]] = {
    "structure.json": ["section-map.json", "extracted.json"],
    "styles.json": ["extracted.json", "generation-plan.json"],
    "section-map.json": [
        "component-map.json",
        "extracted.json",
        "generation-plan.json",
    ],
    "css/variables.txt": ["generation-plan.json"],
    "component-map.json": ["extracted.json"],
    "interactions-detected.json": ["hover-css-rules.json", "extracted.json"],
    "hover-css-rules.json": ["extracted.json"],
    "transition-coverage.json": ["extracted.json"],
    "animation-init-styles.json": ["extracted.json"],
    "svg-text-elements.json": ["extracted.json"],
    "bundle-map.json": ["transition-spec.json"],
    "transition-spec.json": ["extracted.json"],
    "runtime-media.json": ["required-media.json"],
}

for _generation_plan_source in GENERATION_PLAN_SOURCES:
    if "*" in _generation_plan_source:
        continue
    _dependents = DEPS.setdefault(_generation_plan_source, [])
    if "generation-plan.json" not in _dependents:
        _dependents.append("generation-plan.json")

# Severity: which artifacts are blocking vs advisory when stale
_BLOCK_ARTIFACTS = {
    "extracted.json",
    "generation-plan.json",
    "transition-spec.json",
}

# Fix messages per stale artifact
_FIX_MAP: dict[str, str] = {
    "extracted.json": "Re-run Step 6b: assemble extracted.json from all artifacts",
    "section-map.json": "Re-run Step 2: re-extract DOM structure",
    "component-map.json": "Re-run Step 6c: re-run section audit",
    "generation-plan.json": (
        "Re-run generation-plan.sh, then generation-planner enrichment"
    ),
    "transition-spec.json": "Re-run Step 5d: re-write transition-spec from bundle-map",
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass
class StalenessIssue:
    stale: str
    because_of: str
    severity: Literal["block", "warn"]
    fix: str


def generation_plan_source_hashes(ref_dir: Path) -> dict[str, str | None]:
    """Hash every deterministic and enriched schema-v2 plan input."""
    hashes: dict[str, str | None] = {}
    for relative_path in GENERATION_PLAN_SOURCES:
        if relative_path == "css/*.css":
            css_entries = [
                (
                    path.relative_to(ref_dir).as_posix(),
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
                for path in sorted((ref_dir / "css").glob("*.css"))
                if path.is_file()
            ]
            hashes[relative_path] = (
                hashlib.sha256(
                    json.dumps(
                        css_entries,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ).encode("utf-8")
                ).hexdigest()
                if css_entries
                else None
            )
            continue
        path = ref_dir / relative_path
        try:
            hashes[relative_path] = (
                hashlib.sha256(path.read_bytes()).hexdigest()
                if path.is_file()
                else None
            )
        except OSError:
            hashes[relative_path] = "<unreadable>"
    return hashes


def generation_plan_provenance_issues(
    ref_dir: Path,
    provenance: Any,
) -> tuple[list[str], list[str]]:
    """Return malformed/forged metadata issues and stale source-hash issues."""
    invalid: list[str] = []
    stale: list[str] = []
    if not isinstance(provenance, dict):
        return ["missing provenance object"], []

    if provenance.get("source") != "generation-planner":
        invalid.append("source must be generation-planner")

    generated_at = provenance.get("generatedAt")
    if not isinstance(generated_at, str) or not generated_at.strip():
        invalid.append("generatedAt must be a timezone-aware ISO-8601 string")
    else:
        try:
            parsed_at = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            if parsed_at.tzinfo is None:
                raise ValueError
        except ValueError:
            invalid.append("generatedAt must be a timezone-aware ISO-8601 string")

    if provenance.get("hashAlgorithm") != "sha256":
        invalid.append("hashAlgorithm must be sha256")

    recorded_hashes = provenance.get("sourceHashes")
    if not isinstance(recorded_hashes, dict):
        invalid.append("sourceHashes must be an object")
        return invalid, stale

    current_hashes = generation_plan_source_hashes(ref_dir)
    for relative_path, current_hash in current_hashes.items():
        if relative_path not in recorded_hashes:
            invalid.append(f"sourceHashes missing {relative_path}")
            continue
        recorded_hash = recorded_hashes[relative_path]
        if recorded_hash is not None and (
            not isinstance(recorded_hash, str)
            or not _SHA256_RE.fullmatch(recorded_hash)
        ):
            invalid.append(f"sourceHashes.{relative_path} is not a SHA-256 digest or null")
            continue
        if recorded_hash != current_hash:
            stale.append(relative_path)

    return invalid, stale


def _assert_no_cycles() -> None:
    """Validate that DEPS graph is acyclic. Raises RuntimeError if a cycle is found.

    Uses Kahn's algorithm: if any node remains after topological sort, a cycle exists.
    Called once at module import to catch errors during development.
    """
    all_nodes: set[str] = set(DEPS.keys())
    for targets in DEPS.values():
        all_nodes.update(targets)

    in_degree: dict[str, int] = {n: 0 for n in all_nodes}
    for targets in DEPS.values():
        for t in targets:
            in_degree[t] += 1

    ready: deque[str] = deque(n for n, d in in_degree.items() if d == 0)
    visited = 0
    while ready:
        node = ready.popleft()
        visited += 1
        for dep in DEPS.get(node, []):
            in_degree[dep] -= 1
            if in_degree[dep] == 0:
                ready.append(dep)

    if visited != len(all_nodes):
        remaining = [n for n, d in in_degree.items() if d > 0]
        raise RuntimeError(f"Cycle detected in DEPS graph. Nodes in cycle: {remaining}")


# Validate at import time — catches cycle regressions during development.
_assert_no_cycles()


def stale_set(changed: str) -> list[str]:
    """
    Return topologically sorted list of artifacts that become stale
    when `changed` is re-extracted.

    Uses BFS over DEPS graph, then Kahn's algorithm for ordering.
    """
    # BFS to find all affected nodes
    affected: set[str] = set()
    queue: deque[str] = deque([changed])
    while queue:
        node = queue.popleft()
        for dependent in DEPS.get(node, []):
            if dependent not in affected:
                affected.add(dependent)
                queue.append(dependent)

    if not affected:
        return []

    # Kahn's topological sort on the subgraph of affected nodes
    # Build in-degree map within affected set
    in_degree: dict[str, int] = {n: 0 for n in affected}
    # Forward edges within affected set: src → [dependents that are also affected]
    dependents_within_affected: dict[str, list[str]] = {n: [] for n in affected}

    for src, dependents in DEPS.items():
        for dep in dependents:
            if dep in affected and src in affected:
                in_degree[dep] += 1
                dependents_within_affected[src].append(dep)

    # Start with nodes that have no dependencies within the affected set
    ready: deque[str] = deque(n for n, deg in in_degree.items() if deg == 0)
    result: list[str] = []

    while ready:
        node = ready.popleft()
        result.append(node)
        for dependent in dependents_within_affected.get(node, []):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                ready.append(dependent)

    # Append any remaining (cycle guard — shouldn't happen in our DAG)
    for node in affected:
        if node not in result:
            result.append(node)

    return result


def check_staleness(ref_dir: Path) -> list[StalenessIssue]:
    """
    Scan ref_dir for stale artifacts using Path.stat().st_mtime.
    Propagates staleness transitively: if A→B→C and A is newer than B,
    both B and C are reported as stale (C because its ancestor B is stale).
    Returns list of StalenessIssue sorted by severity (block first).
    """
    issues: list[StalenessIssue] = []
    # Track which artifacts are directly stale (parent mtime > child mtime)
    directly_stale: set[str] = set()
    # Map stale artifact → first parent that caused it (dedup: one issue per artifact)
    stale_first_parent: dict[str, str] = {}

    # Pass 1: detect direct staleness from mtime comparisons
    for parent_name, dependents in DEPS.items():
        parent_path = ref_dir / parent_name
        if not parent_path.exists():
            continue
        try:
            parent_mtime = parent_path.stat().st_mtime
        except OSError:
            continue

        for child_name in dependents:
            child_path = ref_dir / child_name
            if not child_path.exists():
                continue
            try:
                child_mtime = child_path.stat().st_mtime
            except OSError:
                continue

            if parent_mtime > child_mtime:
                directly_stale.add(child_name)
                # Only report the first parent that caused staleness (dedup)
                if child_name not in stale_first_parent:
                    stale_first_parent[child_name] = parent_name
                    sev: Literal["block", "warn"] = (
                        "block" if child_name in _BLOCK_ARTIFACTS else "warn"
                    )
                    fix = _FIX_MAP.get(
                        child_name,
                        f"Re-run the step that produces {child_name}",
                    )
                    issues.append(
                        StalenessIssue(
                            stale=child_name,
                            because_of=parent_name,
                            severity=sev,
                            fix=fix,
                        )
                    )

    # Pass 2: propagate transitively — if a parent is stale, its children
    # are also stale even if their mtime is newer (the parent needs rebuild first).
    already_reported: set[str] = {i.stale for i in issues}
    for stale_parent in directly_stale:
        for transitive_child in stale_set(stale_parent):
            if transitive_child not in already_reported and (ref_dir / transitive_child).exists():
                already_reported.add(transitive_child)
                sev = "block" if transitive_child in _BLOCK_ARTIFACTS else "warn"
                fix = _FIX_MAP.get(
                    transitive_child,
                    f"Re-run the step that produces {transitive_child}",
                )
                issues.append(
                    StalenessIssue(
                        stale=transitive_child,
                        because_of=stale_parent,
                        severity=sev,
                        fix=fix,
                    )
                )

    # Sort: block first, then warn; within each group alphabetically
    issues.sort(key=lambda i: (0 if i.severity == "block" else 1, i.stale))
    return issues
