from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK_PATH = ROOT / "scripts" / "ci" / "check-readme-i18n.py"


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_readme_i18n", CHECK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_review_checks() -> ModuleType:
    path = ROOT / "scripts" / "ci" / "review_checks.py"
    spec = importlib.util.spec_from_file_location("review_checks", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_localized_readmes_match_canonical_contract() -> None:
    checker = _load_checker()
    assert checker.check(ROOT) == []


def test_stale_canonical_revision_fails_closed(tmp_path: Path) -> None:
    checker = _load_checker()
    for name in ("README.md", *checker.TRANSLATIONS):
        (tmp_path / name).write_bytes((ROOT / name).read_bytes())

    korean = tmp_path / "README.ko.md"
    korean.write_text(
        korean.read_text(encoding="utf-8").replace(
            "sha256=", "sha256=" + "0" * 64 + "<!-- stale -->"
        ),
        encoding="utf-8",
    )

    errors = checker.check(tmp_path)
    assert "README.ko.md: canonical revision acknowledgement is missing or stale" in errors


def test_hangul_check_allows_only_korean_language_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checker = _load_review_checks()
    korean_link = '<a href="README.ko.md">🇰🇷 한국어</a>'
    (tmp_path / "README.md").write_text(korean_link, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert checker.find_hangul() == 0
    assert capsys.readouterr().out == "\n"

    (tmp_path / "README.md").write_text(f"{korean_link}\n추가 문장", encoding="utf-8")
    assert checker.find_hangul() == 0
    assert capsys.readouterr().out.strip() == "README.md"
