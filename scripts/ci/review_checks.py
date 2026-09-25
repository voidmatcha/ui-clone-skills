#!/usr/bin/env python3
"""Python-backed checks for scripts/ci/review.sh."""

from __future__ import annotations

import ast
import json
import pathlib
import re
import sys
from collections.abc import Callable
from urllib.parse import unquote

PUBLIC_SKILLS = ("ui-capture", "ui-reverse-engineering", "visual-debug")
PUBLIC_SKILL_WORD_ADVISORY = 5_000
INTERNAL_SKILLS = {"benchmark"}
# Mandatory-path word budgets per scenario, measured by scripts/ci/skill_read_graph.py.
# Set slightly above the optimized numbers so the default read path cannot silently
# regrow; raise a budget only together with a documented reason.
SKILL_READ_BUDGETS = {
    "reverse-engineering-full-clone": 52_500,
    "capture-baseline-only": 1_400,
    "visual-debug-single-mismatch": 1_100,
}


def _report_errors(errors: list[str]) -> int:
    for error in errors:
        print(error, file=sys.stderr)
    return int(bool(errors))


def check_public_skills() -> int:
    expected = set(PUBLIC_SKILLS)
    # Internal-only skills (maintainer tooling). Allowed on the development
    # filesystem under skills/ but MUST NOT be registered in Claude's public
    # manifest, referenced from Codex defaultPrompt, or copied into the Codex
    # install projection's public skills directory.
    internal_skills = INTERNAL_SKILLS
    errors: list[str] = []

    claude = json.loads(pathlib.Path(".claude-plugin/plugin.json").read_text())
    claude_paths = claude.get("skills")
    if not isinstance(claude_paths, list):
        errors.append(".claude-plugin/plugin.json skills must be a list")
    else:
        claude_skills = {
            pathlib.PurePosixPath(path).name for path in claude_paths if isinstance(path, str)
        }
        if claude_skills != expected:
            errors.append(f"Claude plugin public skills mismatch: {sorted(claude_skills)}")

    skill_names = set()
    for path in sorted(pathlib.Path("skills").glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^---\n(.*?)\n---", text, re.S)
        if not match:
            errors.append(f"{path}: missing YAML frontmatter")
            continue
        name = re.search(r"^name:\s*['\"]?([^'\"\n]+)['\"]?\s*$", match.group(1), re.M)
        if not name:
            errors.append(f"{path}: missing frontmatter name")
            continue
        skill_names.add(name.group(1).strip())
    extras = skill_names - expected - internal_skills
    missing = expected - skill_names
    if extras:
        errors.append(
            "skills/*/SKILL.md unexpected names "
            f"(add to internal_skills if internal): {sorted(extras)}"
        )
    if missing:
        errors.append(f"skills/*/SKILL.md missing public names: {sorted(missing)}")
    internal_in_public = (
        internal_skills & claude_skills if isinstance(claude_paths, list) else set()
    )
    if internal_in_public:
        errors.append(
            ".claude-plugin/plugin.json leaks internal skills publicly: "
            f"{sorted(internal_in_public)}"
        )

    codex = json.loads(pathlib.Path(".codex-plugin/plugin.json").read_text())
    interface = codex.get("interface", {})
    prompt_value = interface.get("defaultPrompt", "")
    if not isinstance(prompt_value, list):
        errors.append(".codex-plugin/plugin.json interface.defaultPrompt must be a list")
        prompt = str(prompt_value)
    else:
        if len(prompt_value) > 3:
            errors.append("Codex defaultPrompt has more than 3 entries (Codex ignores extras)")
        long_items = [index + 1 for index, item in enumerate(prompt_value) if len(str(item)) > 128]
        if long_items:
            errors.append(f"Codex defaultPrompt entries exceed 128 chars: {long_items}")
        prompt = "\n".join(str(item) for item in prompt_value)
    codex_text = f"{interface.get('longDescription', '')}\n{prompt}"
    missing_codex = sorted(skill for skill in expected if skill not in codex_text)
    if missing_codex:
        errors.append(f"Codex prompt/description missing public skill mentions: {missing_codex}")
    codex_internal_mentions = sorted(skill for skill in internal_skills if skill in prompt)
    if codex_internal_mentions:
        errors.append(
            f".codex-plugin defaultPrompt leaks internal skills: {codex_internal_mentions}"
        )

    install_text = pathlib.Path("install.sh").read_text(encoding="utf-8")
    if re.search(r"for item in [^\n]*\bskills\b", install_text):
        errors.append("install.sh Codex projection still symlinks the whole skills/ directory")
    for skill in expected:
        if skill not in install_text:
            errors.append(f"install.sh CODEX_PUBLIC_SKILLS missing public skill: {skill}")
    for skill in internal_skills:
        if f"skills/{skill}" in install_text and "maintainer-only" not in install_text:
            errors.append(f"install.sh may copy internal skill into Codex projection: {skill}")

    return _report_errors(errors)


def report_public_skill_sizes() -> int:
    """Report entrypoint context size without turning the budget into a gate."""
    errors: list[str] = []
    total_words = 0
    total_lines = 0
    for skill in PUBLIC_SKILLS:
        path = pathlib.Path("skills", skill, "SKILL.md")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{path}: cannot read: {exc}")
            continue
        words = len(re.findall(r"\S+", text))
        lines = len(text.splitlines())
        total_words += words
        total_lines += lines
        print(f"{skill}: {words} words, {lines} lines")

    if errors:
        return _report_errors(errors)

    print(f"TOTAL: {total_words} words, {total_lines} lines")
    if total_words > PUBLIC_SKILL_WORD_ADVISORY:
        print(
            "WARNING: public skill entrypoints total "
            f"{total_words} words (advisory {PUBLIC_SKILL_WORD_ADVISORY})"
        )
    for line in skill_read_budget_lines():
        print(line)
    return 0


def skill_read_budget_lines() -> list[str]:
    """Report mandatory-path words per scenario; WARNING lines mark exceeded budgets."""
    try:
        import skill_read_graph
    except ImportError:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import skill_read_graph
    lines: list[str] = []
    for result in skill_read_graph.measure_all(top=3):
        name = str(result["scenario"])
        words = int(result["mandatory_words"])
        budget = SKILL_READ_BUDGETS.get(name)
        heavy = ", ".join(
            f"{pathlib.PurePosixPath(str(row['doc'])).name} {row['words']}"
            for row in result["top_mandatory"]
        )
        lines.append(
            f"{name}: mandatory path {words} words / {result['mandatory_docs']} docs"
            f" (budget {budget}; heaviest: {heavy})"
        )
        if budget is not None and words > budget:
            lines.append(
                f"WARNING: {name} mandatory path grew to {words} words (budget {budget})"
                " — run scripts/ci/skill_read_graph.py and move the growth behind a"
                " conditional link or trim the heaviest doc"
            )
    return lines


def report_skill_reads() -> int:
    """Print the scenario read-graph report and fail when any budget is exceeded."""
    lines = skill_read_budget_lines()
    for line in lines:
        print(line)
    return int(any(line.startswith("WARNING:") for line in lines))


def check_eval_grounding() -> int:
    """Fail when an eval asserts on a file, script, flag, or gate the repo does not have."""
    try:
        import eval_grounding
    except ImportError:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import eval_grounding
    return eval_grounding.main([])


def _local_markdown_targets(text: str) -> list[str]:
    """Extract literal relative Markdown document links from Markdown syntax."""
    targets: list[str] = []
    inline = re.compile(r"!?\[[^\]]*\]\(\s*(<[^>]+>|[^\s)]+)")
    references = re.compile(r"^\s*\[[^\]]+\]:\s*(<[^>]+>|\S+)", re.M)
    for match in (*inline.finditer(text), *references.finditer(text)):
        target = match.group(1).strip("<>")
        path_part = unquote(target.split("#", 1)[0].split("?", 1)[0])
        if not path_part.lower().endswith(".md"):
            continue
        if path_part.startswith(("/", "\\", "$")) or re.match(
            r"^[a-z][a-z0-9+.-]*:", path_part, re.I
        ):
            continue
        targets.append(path_part)
    return targets


def check_public_skill_links() -> int:
    """Validate literal local Markdown links in the three public entrypoints."""
    errors: list[str] = []
    for skill in PUBLIC_SKILLS:
        source = pathlib.Path("skills", skill, "SKILL.md")
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{source}: cannot read: {exc}")
            continue
        for target in sorted(set(_local_markdown_targets(text))):
            resolved = source.parent / target
            if not resolved.is_file():
                errors.append(f"{source}: broken local Markdown link: {target}")
    return _report_errors(errors)


def check_trigger_boundaries() -> int:
    checks = {
        "ui-reverse-engineering": {
            "live URL trigger": ("live", "url"),
            "React build target": ("react",),
            "capture route-out": ("ui-capture",),
            "mismatch route-out": ("visual-debug",),
        },
        "ui-capture": {
            "reference evidence trigger": ("reference", "capture"),
            "screenshot capture": ("screenshot",),
            "transition capture": ("transition",),
            "mismatch diagnosis route-out": ("visual-debug", "mismatch"),
        },
        "visual-debug": {
            "reference implementation comparison": ("reference", "implementation"),
            "comparison/diff trigger": ("compar",),
            "build route-out": ("ui-reverse-engineering", "build"),
            "baseline capture route-out": ("ui-capture", "capture"),
        },
    }

    errors: list[str] = []
    for skill, groups in checks.items():
        text = pathlib.Path("skills", skill, "SKILL.md").read_text(encoding="utf-8").lower()
        for label, tokens in groups.items():
            missing = [token for token in tokens if token not in text]
            if missing:
                errors.append(f"{skill}: missing {label} token(s): {', '.join(missing)}")

    return _report_errors(errors)


def check_trigger_fixtures() -> int:
    errors: list[str] = []

    reverse = json.loads(
        pathlib.Path("skills/ui-reverse-engineering/evals/trigger-eval.json").read_text()
    )
    for item in reverse:
        query = item.get("query", "").lower()
        if any(
            token in query
            for token in (
                "attached screenshot",
                "figma mockup screenshot",
                "multiple screenshots",
            )
        ):
            if item.get("should_trigger") is not False:
                errors.append(
                    f"ui-reverse screenshot-only prompt should not trigger: {item.get('query')}"
                )

    capture = json.loads(pathlib.Path("skills/ui-capture/evals/trigger-eval.json").read_text())
    for item in capture:
        query = item.get("query", "").lower()
        if any(
            token in query
            for token in (
                "visual diff",
                "verify visual match",
                "show me the differences",
                "comparison page showing original vs clone",
            )
        ):
            if item.get("should_trigger") is not False:
                errors.append(
                    "ui-capture diff/diagnosis prompt should route to visual-debug: "
                    f"{item.get('query')}"
                )

    return _report_errors(errors)


def find_hangul() -> int:
    hangul = re.compile(r"[\uAC00-\uD7AF]")
    readme_korean_link = '<a href="README.ko.md">🇰🇷 \ud55c\uad6d\uc5b4</a>'
    roots = ["skills", "CHANGELOG.md", "README.md", "AGENTS.md", "CLAUDE.md"]
    hits = []
    for root in roots:
        root_path = pathlib.Path(root)
        if not root_path.exists():
            continue
        paths = (
            [root_path]
            if root_path.is_file()
            else sorted(list(root_path.rglob("*.md")) + list(root_path.rglob("*.json")))
        )
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
                if path == pathlib.Path("README.md"):
                    text = text.replace(readme_korean_link, "")
                if hangul.search(text):
                    hits.append(str(path))
            except OSError:
                pass
    print("\n".join(hits))
    return 0


def count_subprocess_without_timeout() -> int:
    integ = pathlib.Path("tests/integration")
    count = 0
    for path in sorted(pathlib.Path("tests").rglob("*.py")):
        if integ in path.parents:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "run"
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
                and "timeout" not in {keyword.arg for keyword in node.keywords}
            ):
                count += 1
    print(count)
    return 0


COMMANDS: dict[str, Callable[[], int]] = {
    "count-subprocess-without-timeout": count_subprocess_without_timeout,
    "eval-grounding": check_eval_grounding,
    "public-skill-links": check_public_skill_links,
    "public-skills": check_public_skills,
    "skill-context": report_public_skill_sizes,
    "skill-reads": report_skill_reads,
    "trigger-boundaries": check_trigger_boundaries,
    "trigger-fixtures": check_trigger_fixtures,
    "find-hangul": find_hangul,
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        choices = ", ".join(sorted(COMMANDS))
        print(f"usage: {pathlib.Path(sys.argv[0]).name} <{choices}>", file=sys.stderr)
        return 2
    return COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main())
