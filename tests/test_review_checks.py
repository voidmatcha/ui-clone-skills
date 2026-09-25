"""Focused tests for the Python-backed review checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKS_PATH = ROOT / "scripts" / "ci" / "review_checks.py"
SPEC = importlib.util.spec_from_file_location("review_checks", CHECKS_PATH)
assert SPEC is not None and SPEC.loader is not None
review_checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review_checks)


def _write_public_skills(root: Path, contents: dict[str, str]) -> None:
    for skill in review_checks.PUBLIC_SKILLS:
        skill_dir = root / "skills" / skill
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(contents.get(skill, "entry\n"), encoding="utf-8")


def test_public_skill_sizes_report_words_lines_and_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert review_checks.COMMANDS["skill-context"] is review_checks.report_public_skill_sizes
    _write_public_skills(
        tmp_path,
        {
            "ui-capture": "one two\nthree\n",
            "ui-reverse-engineering": "four\n",
            "visual-debug": "five six\n",
        },
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(review_checks, "PUBLIC_SKILL_WORD_ADVISORY", 5)

    assert review_checks.report_public_skill_sizes() == 0
    output = capsys.readouterr().out
    assert "ui-capture: 3 words, 2 lines" in output
    assert "TOTAL: 6 words, 4 lines" in output
    assert "WARNING: public skill entrypoints total 6 words (advisory 5)" in output


def test_public_skill_link_check_accepts_existing_relative_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_public_skills(
        tmp_path,
        {"ui-capture": "Read [setup](setup.md#browser) and [peer](../visual-debug/SKILL.md).\n"},
    )
    (tmp_path / "skills" / "ui-capture" / "setup.md").write_text("ok\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert review_checks.check_public_skill_links() == 0


def test_public_skill_link_check_rejects_missing_literal_markdown_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_public_skills(
        tmp_path,
        {"visual-debug": "Read [missing](guides/missing.md#details).\n"},
    )
    monkeypatch.chdir(tmp_path)

    assert review_checks.check_public_skill_links() == 1
    assert (
        "skills/visual-debug/SKILL.md: broken local Markdown link: guides/missing.md"
        in capsys.readouterr().err
    )
