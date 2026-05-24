from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_generation_docs_do_not_allow_placeholder_fallbacks() -> None:
    """Public generation guidance must fail closed when extracted values are missing."""
    doc = ROOT / "skills" / "ui-reverse-engineering" / "css-first-generation.md"
    text = doc.read_text(encoding="utf-8").lower()

    banned = [
        "descriptive placeholder otherwise",
        "if any extracted value is missing, use placeholder",
    ]
    for phrase in banned:
        assert phrase not in text


def test_no_judgment_does_not_permit_todo_for_missing_em_base() -> None:
    """Missing typographic base values must trigger extraction, not TODO comments."""
    doc = ROOT / "skills" / "ui-reverse-engineering" / "no-judgment.md"
    text = doc.read_text(encoding="utf-8").lower()

    assert "document as `// todo: verify em base`" not in text
