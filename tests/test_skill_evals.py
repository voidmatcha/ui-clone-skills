"""Schema and consistency checks for skills/*/evals/{evals,trigger-eval}.json.

These fixtures are consumed by tests/test_repo_hygiene.py (eval ids) and
scripts/ci/review_checks.py (trigger queries), so drift in shape, duplicated
ids, empty assertions, or non-English text must fail here before push.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "skills"

EVAL_FILES = sorted(SKILLS_DIR.glob("*/evals/evals.json"))
TRIGGER_FILES = sorted(SKILLS_DIR.glob("*/evals/trigger-eval.json"))

EVAL_REQUIRED_KEYS = {"id", "prompt", "expected_output", "files", "expectations"}
TRIGGER_REQUIRED_KEYS = {"query", "should_trigger"}
TRIGGER_OPTIONAL_KEYS = {"note"}

# Eval ids that other repo runners look up by number. Keep in sync with the
# consumer named in each comment so a renumbering fails loudly here.
RUNNER_ID_REFERENCES: dict[str, set[int]] = {
    # tests/test_repo_hygiene.py: static-state capture contract (PNG only).
    "ui-capture": {3, 12, 13},
}

HANGUL = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힯]")


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _skill_name(path: Path) -> str:
    return path.parent.parent.name


def _ids(path: Path) -> str:
    return f"{_skill_name(path)}/{path.name}"


def test_public_skills_ship_both_eval_files() -> None:
    plugin = _load(ROOT / ".claude-plugin" / "plugin.json")
    assert isinstance(plugin, dict)
    public = {Path(entry).name for entry in plugin["skills"]}
    assert public, "plugin.json must list public skills"
    for skill in sorted(public):
        evals_dir = SKILLS_DIR / skill / "evals"
        assert (evals_dir / "evals.json").is_file(), f"{skill}: missing evals.json"
        assert (evals_dir / "trigger-eval.json").is_file(), f"{skill}: missing trigger-eval.json"


@pytest.mark.parametrize("path", EVAL_FILES, ids=_ids)
def test_evals_json_schema(path: Path) -> None:
    data = _load(path)
    assert isinstance(data, dict), "top level must be an object"
    assert data.get("skill_name") == _skill_name(path), "skill_name must match directory"
    evals = data.get("evals")
    assert isinstance(evals, list) and evals, "evals must be a non-empty list"

    seen_ids: set[int] = set()
    seen_prompts: set[str] = set()
    for entry in evals:
        assert isinstance(entry, dict)
        assert set(entry) == EVAL_REQUIRED_KEYS, (
            f"eval {entry.get('id')}: keys {sorted(entry)} != {sorted(EVAL_REQUIRED_KEYS)}"
        )
        eval_id = entry["id"]
        assert isinstance(eval_id, int) and eval_id > 0, (
            f"eval id must be a positive int: {eval_id!r}"
        )
        assert eval_id not in seen_ids, f"duplicate eval id {eval_id}"
        seen_ids.add(eval_id)

        prompt = entry["prompt"]
        assert isinstance(prompt, str) and prompt.strip(), f"eval {eval_id}: empty prompt"
        assert prompt not in seen_prompts, f"eval {eval_id}: duplicate prompt"
        seen_prompts.add(prompt)

        expected = entry["expected_output"]
        assert isinstance(expected, str) and expected.strip(), (
            f"eval {eval_id}: empty expected_output"
        )

        assert isinstance(entry["files"], list), f"eval {eval_id}: files must be a list"
        assert all(isinstance(item, str) for item in entry["files"]), (
            f"eval {eval_id}: files entries must be strings"
        )

        expectations = entry["expectations"]
        assert isinstance(expectations, list) and expectations, f"eval {eval_id}: no expectations"
        for expectation in expectations:
            assert isinstance(expectation, str) and expectation.strip(), (
                f"eval {eval_id}: blank expectation"
            )
        assert len(set(expectations)) == len(expectations), f"eval {eval_id}: duplicate expectation"


@pytest.mark.parametrize("path", TRIGGER_FILES, ids=_ids)
def test_trigger_eval_json_schema(path: Path) -> None:
    data = _load(path)
    assert isinstance(data, list) and data, "trigger-eval.json must be a non-empty list"

    seen_queries: set[str] = set()
    positives = 0
    negatives = 0
    for index, entry in enumerate(data):
        assert isinstance(entry, dict), f"entry {index}: not an object"
        keys = set(entry)
        missing = TRIGGER_REQUIRED_KEYS - keys
        extra = keys - TRIGGER_REQUIRED_KEYS - TRIGGER_OPTIONAL_KEYS
        assert not missing, f"entry {index}: missing {sorted(missing)}"
        assert not extra, f"entry {index}: unexpected keys {sorted(extra)}"

        query = entry["query"]
        assert isinstance(query, str) and query.strip(), f"entry {index}: empty query"
        assert query not in seen_queries, f"entry {index}: duplicate query"
        seen_queries.add(query)

        assert isinstance(entry["should_trigger"], bool), f"entry {index}: should_trigger not bool"
        if "note" in entry:
            assert isinstance(entry["note"], str) and entry["note"].strip(), (
                f"entry {index}: blank note"
            )
        if entry["should_trigger"]:
            positives += 1
        else:
            negatives += 1

    assert positives and negatives, "trigger fixture needs both positive and negative cases"


@pytest.mark.parametrize("path", EVAL_FILES + TRIGGER_FILES, ids=_ids)
def test_eval_fixtures_are_english_only(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        assert not HANGUL.search(line), (
            f"{path.name}:{lineno} contains Hangul; eval fixtures are English only"
        )


def test_runner_referenced_eval_ids_exist() -> None:
    for skill, expected_ids in RUNNER_ID_REFERENCES.items():
        data = _load(SKILLS_DIR / skill / "evals" / "evals.json")
        assert isinstance(data, dict)
        present = {entry["id"] for entry in data["evals"]}
        missing = expected_ids - present
        assert not missing, f"{skill}: runner-referenced eval ids missing: {sorted(missing)}"
