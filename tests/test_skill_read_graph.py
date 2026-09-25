"""Tests for scripts/ci/skill_read_graph.py and the skill-reads review budget."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI_DIR = ROOT / "scripts" / "ci"
if str(CI_DIR) not in sys.path:
    sys.path.insert(0, str(CI_DIR))

GRAPH_SPEC = importlib.util.spec_from_file_location(
    "skill_read_graph", CI_DIR / "skill_read_graph.py"
)
assert GRAPH_SPEC is not None and GRAPH_SPEC.loader is not None
skill_read_graph = importlib.util.module_from_spec(GRAPH_SPEC)
sys.modules["skill_read_graph"] = skill_read_graph
GRAPH_SPEC.loader.exec_module(skill_read_graph)

CHECKS_SPEC = importlib.util.spec_from_file_location("review_checks", CI_DIR / "review_checks.py")
assert CHECKS_SPEC is not None and CHECKS_SPEC.loader is not None
review_checks = importlib.util.module_from_spec(CHECKS_SPEC)
CHECKS_SPEC.loader.exec_module(review_checks)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _link_classes(root: Path, doc: str) -> dict[str, str]:
    return {
        Path(link.target).name: link.klass for link in skill_read_graph.extract_links(root / doc)
    }


def test_classifies_links_by_sentence_and_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("setup", "cond", "step", "role", "index", "pointer", "wrapped"):
        _write(tmp_path, f"skills/demo/{name}.md", f"# {name}\n\nbody words here\n")
    _write(
        tmp_path,
        "skills/demo/SKILL.md",
        "\n".join(
            [
                "# Demo",
                "",
                "Read [setup](setup.md) once per session. If the gate fails, consult [cond](cond.md).",
                "See [pointer](pointer.md) for background.",
                "",
                "| Phase | Step | Do |",
                "|---|---|---|",
                "| 1 | A | `step.md` → `out.json` |",
                "",
                "| Role | Contract |",
                "|---|---|",
                "| worker | [role](role.md) |",
                "",
                "| File | When |",
                "|---|---|",
                "| `index.md` | whenever |",
                "",
                "Resolve the environment through",
                "[wrapped](wrapped.md) before any browser work.",
                "",
                "```bash",
                "echo 'not a link: `setup.md`'",
                "```",
            ]
        )
        + "\n",
    )
    monkeypatch.chdir(tmp_path)

    classes = _link_classes(tmp_path, "skills/demo/SKILL.md")
    assert classes == {
        "setup.md": "always",
        "cond.md": "conditional",
        "pointer.md": "pointer",
        "step.md": "step",
        "role.md": "role",
        "index.md": "index",
        "wrapped.md": "always",
    }


def test_scenario_measure_counts_mandatory_and_reachable_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        tmp_path, "skills/demo/SKILL.md", "# Demo\n\nRead [a](a.md). Only if X read [b](b.md).\n"
    )
    _write(tmp_path, "skills/demo/a.md", "one two three\n\nRead [c](c.md) next.\n")
    _write(tmp_path, "skills/demo/b.md", "four five six seven\n")
    _write(tmp_path, "skills/demo/c.md", "eight nine\n")
    monkeypatch.chdir(tmp_path)

    scenario = skill_read_graph.Scenario(
        name="demo",
        entry="skills/demo/SKILL.md",
        description="demo",
        mandatory_classes=frozenset({"always"}),
    )
    graph = skill_read_graph.build_graph([scenario.entry])
    result = skill_read_graph.measure(scenario, graph, top=5)

    assert result["mandatory_docs"] == 3  # SKILL + a (always) + c (always from a)
    assert result["mandatory_words"] == 9 + 6 + 2
    assert result["reachable_docs"] == 4
    assert result["reachable_words"] == 9 + 6 + 2 + 4
    assert [row["doc"] for row in result["top_conditional"]] == ["skills/demo/b.md"]


def test_repo_scenarios_stay_within_read_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: the measured mandatory path must not regrow past its budget."""
    monkeypatch.chdir(ROOT)
    results = {row["scenario"]: row for row in skill_read_graph.measure_all(top=3)}

    assert set(results) == set(review_checks.SKILL_READ_BUDGETS)
    for name, budget in review_checks.SKILL_READ_BUDGETS.items():
        words = results[name]["mandatory_words"]
        assert words <= budget, (
            f"{name}: mandatory path is {words} words, budget {budget}; heaviest docs: "
            f"{results[name]['top_mandatory']}"
        )


def test_skill_reads_command_reports_and_flags_budget(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(ROOT)
    assert review_checks.COMMANDS["skill-reads"] is review_checks.report_skill_reads

    assert review_checks.report_skill_reads() == 0
    out = capsys.readouterr().out
    assert "reverse-engineering-full-clone: mandatory path" in out
    assert "WARNING:" not in out

    monkeypatch.setattr(
        review_checks,
        "SKILL_READ_BUDGETS",
        {**review_checks.SKILL_READ_BUDGETS, "capture-baseline-only": 1},
    )
    assert review_checks.report_skill_reads() == 1
    assert "WARNING: capture-baseline-only mandatory path grew" in capsys.readouterr().out


def test_skill_context_report_includes_scenario_lines(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(ROOT)
    assert review_checks.report_public_skill_sizes() == 0
    out = capsys.readouterr().out
    assert "TOTAL:" in out
    assert "visual-debug-single-mismatch: mandatory path" in out


def test_transcript_sampler_counts_skill_doc_reads_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    project = tmp_path / ".claude" / "projects" / "-repo-ui-clone-skills"
    project.mkdir(parents=True)
    lines = [
        '{"message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/x/skills/ui-capture/SKILL.md"}}]}}',
        '{"message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/x/ui-clone-skills/docs/agent-cli.md"}}]}}',
        '{"message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/x/skills/ui-capture/SKILL.md"}}]}}',
        '{"message":{"content":"secret prose that must never be echoed"}}',
        "not json",
    ]
    (project / "s.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    sample = skill_read_graph.sample_transcripts("*ui-clone-skills*")

    assert sample["sessions"] == 1
    assert sample["sessions_with_skill_reads"] == 1
    assert sample["reads"] == [{"doc": "ui-capture/SKILL.md", "count": 2}]
