#!/usr/bin/env python3
"""Report Hangul found in shipped Python and shell source."""

from __future__ import annotations

import re
import sys
from pathlib import Path

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "tmp",
    "scratch",
    "benchmark",
    "CHANGELOG_archive",
    "tests",
    "research",
    ".mypy_cache",
    ".sisyphus",
    ".claude",
    ".codex-plugin",
    ".claude-plugin",
    ".omx",
    ".serena",
    ".tokensave",
}
EXCLUDED_FILES = {"CHANGELOG.md", "handover", "check-universality.sh"}
HANGUL = re.compile(r"[\uac00-\ud7a3]")


def find_hits(root: Path) -> list[str]:
    hits: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix not in {".py", ".sh"}:
            continue
        if path.name in EXCLUDED_FILES:
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(lines, 1):
            if HANGUL.search(line):
                hits.append(f"{relative}:{lineno}: {line}")
    return hits


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(".")
    for hit in find_hits(root):
        print(hit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
