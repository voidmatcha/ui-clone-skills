"""Detect local source reuse contamination in clone automation.

Clone benchmarks are only meaningful when the implementation is derived from
the reference URL and generated artifacts. Copying a local exemplar, fixture, or
previous implementation into the impl tree makes the run contaminated even if
the rendered page looks good.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

_COPY_FROM_LOCAL_RE = re.compile(r"\b(?:cp|rsync|ditto|tar|pax)\b")
_TEXT_SOURCE_SUFFIXES = {
    ".css",
    ".cjs",
    ".js",
    ".jsx",
    ".json",
    ".md",
    ".mjs",
    ".scss",
    ".ts",
    ".tsx",
}


def _iter_impl_text_files(impl_dir: Path) -> list[Path]:
    if not impl_dir.is_dir():
        return []
    skip = {"node_modules", ".next", "dist", "build", ".git"}
    paths: list[Path] = []
    for path in impl_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _TEXT_SOURCE_SUFFIXES:
            continue
        if any(part in skip for part in path.parts):
            continue
        paths.append(path)
    return paths


def detect_local_source_reuse(
    *,
    impl_dir: Path,
    protected_roots: Sequence[Path],
    log_paths: Sequence[Path] | None = None,
    source_label: str = "local source",
) -> list[str]:
    """Return findings when clone work copied or embedded protected roots.

    `protected_roots` can be any local exemplar/seed implementation that is
    allowed for orientation but not as source material for the clone.
    """
    roots = [str(path.expanduser().resolve()) for path in protected_roots]
    if not roots:
        return []

    findings: list[str] = []
    logs = list(log_paths or [])
    for log_path in logs:
        if not log_path.is_file():
            continue
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            if not _COPY_FROM_LOCAL_RE.search(line):
                continue
            matched = next((root for root in roots if root in line), None)
            if matched:
                findings.append(f"{log_path.name}:{line_no} copies {source_label} path {matched}")
                break

    for source_path in _iter_impl_text_files(impl_dir):
        try:
            text = source_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        matched = next((root for root in roots if root in text), None)
        if matched:
            rel = source_path.relative_to(impl_dir)
            findings.append(f"impl/{rel} embeds absolute {source_label} path {matched}")
            break

    return findings[:20]
