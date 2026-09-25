"""Eval grounding lint: every artifact name an eval asserts on must exist in the repo."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "ci" / "eval_grounding.py"
SPEC = importlib.util.spec_from_file_location("eval_grounding", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
eval_grounding = importlib.util.module_from_spec(SPEC)
# Register before exec: its dataclasses resolve string annotations via sys.modules.
sys.modules[SPEC.name] = eval_grounding
SPEC.loader.exec_module(eval_grounding)


def _write_eval(root: Path, skill: str, expectations: list[str], expected_output: str = "") -> Path:
    path = root / "skills" / skill / "evals" / "evals.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "skill_name": skill,
                "evals": [
                    {
                        "id": 1,
                        "prompt": "p",
                        "expected_output": expected_output,
                        "files": [],
                        "expectations": expectations,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def mini_repo(tmp_path: Path) -> Path:
    (tmp_path / "skills" / "demo").mkdir(parents=True)
    (tmp_path / "skills" / "demo" / "SKILL.md").write_text(
        "# Demo\nRun `agent-browser --session <s>` and save `tmp/ref/<component>/hover-deltas.json`.\n"
        "Set UI_CLONE_VERIFY_TIER=standard. Step 5d-2b is mandatory. Use section-compare.sh.\n",
        encoding="utf-8",
    )
    (tmp_path / "skills" / "demo" / "scripts").mkdir()
    (tmp_path / "skills" / "demo" / "scripts" / "section-compare.sh").write_text("#!/bin/sh\n")
    (tmp_path / "ui_clone").mkdir()
    (tmp_path / "ui_clone" / "state.py").write_text(
        'GATE_ORDER: list[str] = [\n    "reference",\n    "pre-generate",\n]\n', encoding="utf-8"
    )
    return tmp_path


def test_extract_tokens_classifies_blocking_and_advisory() -> None:
    text = (
        "`hover-deltas.json` exists; ran section-compare.sh with --session; "
        "UI_CLONE_VERIFY_TIER=standard; python -m ui_clone.gate pre-generate; "
        "Step 5d-2b done; `hasTransition: true`; var(--brand-color) kept; "
        "viewport 1440x900; AE ≤ 500; re-compare then before-click"
    )
    found = {
        (kind, token): blocking for kind, token, blocking in eval_grounding.extract_tokens(text)
    }
    assert found[("file", "hover-deltas.json")] is True
    assert found[("file", "section-compare.sh")] is True
    assert found[("flag", "--session")] is True
    assert found[("env", "UI_CLONE_VERIFY_TIER")] is True
    assert found[("module", "ui_clone.gate")] is True
    assert found[("code-span", "hasTransition: true")] is False
    assert found[("step-label", "Step 5d-2b")] is False
    assert found[("hyphen-id", "pre-generate")] is False
    assert found[("threshold", "1440x900")] is False
    assert found[("threshold", "AE ≤ 500")] is False
    assert ("flag", "--brand-color") not in found, "CSS custom properties are not CLI flags"


def test_grounded_tokens_pass_and_stale_artifacts_block(mini_repo: Path) -> None:
    corpus = eval_grounding.build_corpus(mini_repo)
    assert "pre-generate" in corpus.gates
    path = _write_eval(
        mini_repo,
        "demo",
        [
            "hover-deltas.json saved under tmp/ref/<component>/",
            "section-compare.sh ran with --session",
            "gate pre-generate passed; Step 5d-2b done",
            "re-compare showed before-click state",
        ],
    )
    assert [f for f in eval_grounding.lint_evals(corpus, [path]) if f.blocking] == []

    stale = _write_eval(
        mini_repo,
        "demo",
        [
            "hover-delta-report.json saved",
            "ran tree-walk-check.sh",
            "used --no-such-flag",
            "`missing-identifier` recorded",
            "fully-open state reached",
        ],
        expected_output="demo/missing-doc.md Phase Z executed",
    )
    findings = eval_grounding.lint_evals(corpus, [stale])
    blocking = {f.token for f in findings if f.blocking}
    advisory = {f.token for f in findings if not f.blocking}
    assert blocking == {
        "hover-delta-report.json",
        "tree-walk-check.sh",
        "--no-such-flag",
        "missing-identifier",
        "demo/missing-doc.md",
    }
    assert {"fully-open", "Phase Z"} <= advisory


def test_relative_doc_path_must_resolve_not_just_basename(mini_repo: Path) -> None:
    corpus = eval_grounding.build_corpus(mini_repo)
    path = _write_eval(
        mini_repo, "demo", ["demo/demo/SKILL.md Phase A executed", "demo/SKILL.md read"]
    )
    blocking = {f.token for f in eval_grounding.lint_evals(corpus, [path]) if f.blocking}
    assert blocking == {"demo/demo/SKILL.md"}


def test_gate_like_hyphen_ids_block_only_when_unknown(mini_repo: Path) -> None:
    corpus = eval_grounding.build_corpus(mini_repo)
    path = _write_eval(mini_repo, "demo", ["hydration-check ran", "re-compare passed"])
    findings = {f.token: f.blocking for f in eval_grounding.lint_evals(corpus, [path])}
    assert findings == {"hydration-check": True, "re-compare": False}


def test_repo_evals_have_no_ungrounded_artifacts() -> None:
    started = time.perf_counter()
    corpus = eval_grounding.build_corpus(ROOT)
    findings = eval_grounding.lint_evals(corpus, eval_grounding.eval_paths(ROOT))
    elapsed = time.perf_counter() - started
    blocking = [
        f"{f.skill} eval {f.eval_id} {f.field_name} {f.kind}: {f.token}"
        for f in findings
        if f.blocking
    ]
    assert not blocking, "\n".join(blocking)
    assert elapsed < 2.0, f"eval grounding lint took {elapsed:.2f}s (budget 2s)"


def test_corpus_excludes_eval_fixtures_and_tests(mini_repo: Path) -> None:
    (mini_repo / "tests").mkdir()
    (mini_repo / "tests" / "test_x.py").write_text("only-in-tests.json\n", encoding="utf-8")
    _write_eval(mini_repo, "demo", ["only-in-evals.json saved"])
    corpus = eval_grounding.build_corpus(mini_repo)
    assert "only-in-tests.json" not in corpus.words
    assert "only-in-evals.json" not in corpus.words
