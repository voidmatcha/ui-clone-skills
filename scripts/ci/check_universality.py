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
    ".claude",  # machine-local settings/handovers; plugin manifests live in .claude-plugin (scanned)
    ".omx",
    ".serena",
    ".tokensave",
    # Maintainer-only automation that is deliberately kept out of the shipped
    # package (see .gitignore). Personal project names are legitimate there.
    "internal",
    # Gitignored local-only handover / worktree state (never shipped).
    "outbox",
    ".worktrees",
    ".ui-re-continuation",
    # Gitignored root-level clone output (`/impl`); the generated site keeps
    # its own name, and no shipped surface has an `impl/` directory.
    "impl",
}
# Host-integration surfaces that ARE scanned (agent definitions and manifests
# ship to users): .claude-plugin/, .codex-plugin/, .codex/, docs/.
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
    # Top-level directories the rule applies to; empty = every scanned path.
    dirs: tuple[str, ...] = ()
    # Explicit allowlist of regexes. A hit only stops counting when it lies
    # INSIDE a span one of these regexes matches on the same line, so an
    # allowed token appended as a trailing comment cannot launder an
    # unrelated hit earlier on the line. Prefer adding an entry here (with a
    # reason in the Rule comment) over weakening `pattern`.
    allow: tuple[Pattern[str], ...] = ()
    # Suffixes that are scanned only under `data_dirs` (top-level). Used for
    # data formats where a match is legitimate data everywhere except in
    # manifests that carry prose (hook status messages).
    data_suffixes: frozenset[str] = frozenset()
    data_dirs: tuple[str, ...] = ()

    def applies_to(self, suffix: str, top_dir: str) -> bool:
        if suffix not in self.suffixes or (self.dirs and top_dir not in self.dirs):
            return False
        return suffix not in self.data_suffixes or top_dir in self.data_dirs


# Codex host-config paths that any user has; only paths BELOW these are personal.
CODEX_HOST_CONFIG_PATHS = (
    "~/.codex/config.toml",
    "~/.codex/hooks.json",
    "~/.codex/plugins/",
    "~/.codex/skills",
)
# The canonical upstream URL may appear only as the value of the documented
# env default: `${UI_CLONE_REPO:-<url>}` or `UI_CLONE_REPO_DEFAULT="<url>"`.
UI_CLONE_REPO_DEFAULT_FORMS = (
    re.compile(r"\$\{UI_CLONE_REPO:-[^}]*\}"),
    re.compile(r"\bUI_CLONE_REPO_DEFAULT=\"[^\"]*\""),
)

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
        # Any bare mention of the benchmark site (realfood.gov, realfood-v2,
        # realfood-bench, tmp/ref/realfood, "realfood's card_bg", ...) is a
        # lab note; comments should say "one observed site" / "a Lenis-driven
        # site" instead. Test fixtures stay under tests/ (exempt).
        "Benchmark site names (realfood in any form, ebay-playbook)",
        re.compile(r"\brealfood\b|\bebay-playbook\b", re.IGNORECASE),
    ),
    Rule(
        # `loop-e2e-<N>` names one maintainer end-to-end clone run; the
        # evidence it cites is only readable from that run's scratch dir.
        "Maintainer end-to-end run identifiers (loop-e2e-N)",
        re.compile(r"\bloop-e2e-[0-9]+\b"),
    ),
    Rule(
        # Bare `e2e-<N>` (and `<site>-e2e-<N>` corpus names) label the same
        # runs without the `loop-` prefix. Run numbers are short (1-3
        # digits) and stand alone as prose tokens, so a path segment
        # (`tests/e2e-3/`), a longer id (`e2e-2024`), a hyphenated
        # continuation (`e2e-3-runner`), or a file name (`e2e-3.config.ts`)
        # is generic E2E vocabulary and passes. The `loop-` form is reported
        # by the previous rule, not twice here.
        "Maintainer end-to-end run labels (e2e-N)",
        re.compile(r"(?<![\w/.-])(?:(?!loop-)[a-z]+-)?e2e-[0-9]{1,3}(?![\w/-])(?!\.\w)"),
    ),
    Rule(
        "Brand / company leakage (NAVER, navercorp, dga_, kakao, coupang, nexon)",
        re.compile(
            r"\bNAVER\b|\bNaver\b|naver\.com|(?i:\bnavercorp\b)|\bdga_|\bkakao\b|\bcoupang\b|\bnexon\b"
        ),
    ),
    Rule(
        # Lab-notebook provenance: the size of a maintainer's site batch
        # ("the 26-site loop"), a review/analysis/audit pinned to a calendar
        # date, or an agent-session label (`fable-YYYYMMDD`). Comments should
        # state the finding, not which run or day produced it.
        "Dated lab notes and session labels (N-site loop, review YYYY-MM-DD, fable-YYYYMMDD)",
        re.compile(
            r"\b[0-9]+-site loop\b"
            r"|\b(?:review|analysis|audit)\s+20[0-9]{2}-[0-9]{2}-[0-9]{2}\b"
            r"|\bfable-[0-9]{8}\b",
            re.IGNORECASE,
        ),
    ),
    Rule(
        # Any calendar stamp in shipped code is a lab note, whatever word
        # precedes it: `YYYY-MM-DD SKILL.md ...`, `Review follow-up
        # YYYY-MM-DD`, `(YYYY-MM-DD)`, `since YYYY-MM`, and redacted
        # placeholders such as `YYYY-0X-XX`. Code comments and messages state
        # the rule or finding, never the day it was written. Markdown keeps
        # the narrower rule above (docs may legitimately date a design
        # record); CHANGELOG / benchmark / internal / tests are excluded trees.
        # Data files (eval fixtures, data JSON, config) may carry real dates
        # such as `"captured": "2026-09-24"`, so JSON is scanned only in the
        # prose-carrying manifests (hooks/, .claude-plugin/, .codex-plugin/:
        # status messages, descriptions, default prompts); TOML/YAML never.
        "Bare date stamps in code (YYYY-MM-DD / YYYY-MM / YYYY-0X-XX)",
        re.compile(
            r"(?<![\w.-])20[0-9]{2}-(?:[01][0-9]|[0-9X]X)(?:-(?:[0-3][0-9]|[0-9X]X))?(?![\w-])"
        ),
        frozenset({".py", ".sh", ".json"}),
        data_suffixes=frozenset({".json"}),
        data_dirs=("hooks", ".claude-plugin", ".codex-plugin"),
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
    Rule(
        "Personal home folders (~/Documents/<personal-folder>/)",
        re.compile(r"(~|\$HOME)/Documents/"),
    ),
    Rule(
        "Personal Codex state (~/.codex/<...> other than host config paths)",
        re.compile(r"~/\.codex/"),
        allow=tuple(re.compile(re.escape(path)) for path in CODEX_HOST_CONFIG_PATHS),
    ),
    Rule(
        # A workspace id is six alphanumerics mixing case and digits
        # (`ws-AbC123`); ordinary hyphenated words (`ws-client`, `ws-server`,
        # `ws-socket`) have neither an uppercase letter nor a digit.
        "Terminal multiplexer / workspace leakage (purplemux, cmux, ws-XXXXXX)",
        re.compile(
            r"\b(purplemux|cmux)\b"
            r"|\bws-(?=[A-Za-z0-9]{6}\b)(?=[A-Za-z0-9]*[0-9])(?=[A-Za-z0-9]*[A-Z])[A-Za-z0-9]{6}\b"
        ),
    ),
    Rule(
        # Personal projects live under the gitignored internal/ tree and are
        # never referenced from shipped surfaces or CI wiring.
        "Personal project names (onpixel)",
        re.compile(r"\bonpixel\b", re.IGNORECASE),
    ),
    Rule(
        "Lab batch labels (batch-N item N, tools-batch-N)",
        re.compile(r"\bbatch-[0-9]+ item\b|\btools-batch-[0-9]+\b"),
    ),
    Rule(
        # Behavior surfaces must derive the repository from UI_CLONE_REPO /
        # `git remote get-url origin`; the canonical upstream may appear only
        # on the line that declares that env default. README / manifests /
        # install docs are attribution, not behavior, and are out of scope.
        "Repository owner used as behavior (hard-coded github.com/voidmatcha URL in hooks/scripts/ui_clone)",
        re.compile(r"(raw\.githubusercontent\.com|github\.com)/voidmatcha/"),
        dirs=("hooks", "scripts", "ui_clone"),
        allow=UI_CLONE_REPO_DEFAULT_FORMS,
    ),
)


def _is_svg_path_label_false_positive(line: str, match: re.Match[str]) -> bool:
    token = match.group(0)
    if not re.fullmatch(r"L[0-9]{2,3}", token):
        return False
    return bool(re.match(r" [0-9]", line[match.end() :]))


def _allowed_spans(rule: Rule, line: str) -> list[tuple[int, int]]:
    return [m.span() for pattern in rule.allow for m in pattern.finditer(line)]


def _line_matches(rule: Rule, line: str) -> bool:
    allowed = _allowed_spans(rule, line)
    for match in rule.pattern.finditer(line):
        if _is_svg_path_label_false_positive(line, match):
            continue
        start, end = match.span()
        if any(a <= start and end <= b for a, b in allowed):
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
            top_dir = relative.parts[0] if len(relative.parts) > 1 else ""
            rules = [rule for rule in RULES if rule.applies_to(path.suffix, top_dir)]
            if not rules:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for lineno, line in enumerate(lines, 1):
                for rule in rules:
                    if _line_matches(rule, line):
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
