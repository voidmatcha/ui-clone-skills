#!/usr/bin/env python3
"""Block maintainer-specific labels from shipped source surfaces."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from re import Pattern

EXCLUDED_DIRS = {
    ".git",
    ".handover",
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
    ".codex",
    ".codex-plugin",
    ".claude-plugin",
    ".omx",
    ".serena",
    ".tokensave",
}
EXCLUDED_FILES = {
    "CHANGELOG.md",
    "handover",
    "check-universality.sh",
    "check_universality.py",
}
INCLUDED_SUFFIXES = {".py", ".sh", ".md", ".json", ".toml", ".yml", ".yaml"}
HANGUL = re.compile(r"[\uac00-\ud7a3]")


@dataclass(frozen=True)
class Rule:
    label: str
    pattern: Pattern[str]
    suffixes: frozenset[str] = frozenset(INCLUDED_SUFFIXES)


RULES = (
    Rule(
        "Maintainer loop identifiers (loop-codex-N, loop-claude-N, scratch/loop-N)",
        re.compile(
            r"(scratch/)?loop-(codex|claude)-[0-9]+|scratch/loop-[0-9N]+"
        ),
    ),
    Rule(
        "Per-loop finding labels (L33, L62, loop-37, etc.)",
        re.compile(
            r"(?<![A-Za-z0-9_])L[0-9]{2,3}(?![A-Za-z0-9_])"
            r"|(?<![A-Za-z0-9_])loop-[0-9]+(?![A-Za-z0-9_])"
        ),
    ),
    Rule(
        "Benchmark site names (realfood.gov / realfood-bench / tmp/ref/realfood)",
        re.compile(r"realfood\.gov|realfood-bench|tmp/ref/realfood"),
    ),
    Rule(
        "Brand / company leakage (NAVER, dga_, kakao, coupang, nexon)",
        re.compile(r"\bNAVER\b|\bNaver\b|naver\.com|\bdga_|\bkakao\b|\bcoupang\b|\bnexon\b"),
    ),
    Rule(
        "Codex iteration labels (codex-1N / Codex LN QN / Round N)",
        re.compile(r"\bcodex-(1[0-9]|[2-9][0-9])\b|Codex L[0-9]+ Q[0-9]+|\bRound [12]\b"),
    ),
    Rule(
        "Personal absolute paths (/Users/<name>/)",
        re.compile(r"/Users/[a-z][a-z0-9_-]+/"),
    ),
    Rule(
        "Personal plan files (~/.claude/plans/<name>.md, happy-finding-pelican)",
        re.compile(r"happy-finding-pelican|~/\.claude/plans/"),
    ),
    Rule(
        "Hangul (non-English) in production .py/.sh",
        HANGUL,
        frozenset({".py", ".sh"}),
    ),
)


def _is_svg_path_label_false_positive(line: str, match: re.Match[str]) -> bool:
    token = match.group(0)
    if not re.fullmatch(r"L[0-9]{2,3}", token):
        return False
    return bool(re.match(r" [0-9]", line[match.end() :]))


def _line_matches(rule: Rule, line: str) -> bool:
    for match in rule.pattern.finditer(line):
        if _is_svg_path_label_false_positive(line, match):
            continue
        return True
    return False


def find_hits(root: Path) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {rule.label: [] for rule in RULES}
    for current_root, dir_names, file_names in os.walk(root):
        dir_names[:] = sorted(name for name in dir_names if name not in EXCLUDED_DIRS)
        current_dir = Path(current_root)
        for file_name in sorted(file_names):
            path = current_dir / file_name
            if path.suffix not in INCLUDED_SUFFIXES:
                continue
            if path.name in EXCLUDED_FILES:
                continue
            relative = path.relative_to(root)
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for lineno, line in enumerate(lines, 1):
                for rule in RULES:
                    if path.suffix in rule.suffixes and _line_matches(rule, line):
                        hits[rule.label].append(f"{relative}:{lineno}: {line}")
    return hits


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(".")
    hits = find_hits(root)
    violations = 0

    print("-- check-universality --")
    print()
    for label, rule_hits in hits.items():
        if not rule_hits:
            continue
        violations += 1
        print(f"FAIL {label}")
        for hit in rule_hits[:20]:
            print(f"   {hit}")
        count = len(rule_hits)
        suffix = "" if count == 1 else "es"
        print(f"   ({count} match{suffix})")
        print()

    if violations == 0:
        print("PASS check-universality: 0 violations")
        return 0

    print("-------------------------------------")
    print(f"FAIL check-universality: {violations} violation class(es) found")
    print()
    print("How to fix:")
    print("  - Replace concrete site/loop/finding identifiers with generic descriptors")
    print("    (\"observed failure mode\", \"<component>\", \"opaque-hashed-class\", etc.).")
    print("  - Move maintainer-only context to handover (gitignored) or research/.")
    print("  - For test fixtures, the test belongs under tests/ - that path is exempt.")
    print()
    print("If you genuinely need to ship one of these, justify it in review")
    print("or bypass with UI_CLONE_SKIP_UNIVERSALITY=1.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
