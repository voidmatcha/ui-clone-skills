"""Tests for the offline Claude transcript context-attribution analyzer."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "analysis" / "context_token_attribution.py"


def _load_module() -> ModuleType:
    assert SCRIPT.is_file(), "context attribution analyzer has not been implemented"
    key = "context_token_attribution_test_module"
    spec = importlib.util.spec_from_file_location(key, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _assistant(
    *,
    message_id: str,
    request_id: str,
    content: dict[str, Any],
    input_tokens: int,
    cache_creation: int,
    cache_read: int,
    output_tokens: int,
    uuid: str,
) -> dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": uuid,
        "requestId": request_id,
        "timestamp": "2026-08-01T00:00:00Z",
        "message": {
            "id": message_id,
            "role": "assistant",
            "content": [content],
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
                "output_tokens": output_tokens,
                "iterations": [
                    {
                        "input_tokens": input_tokens,
                        "cache_creation_input_tokens": cache_creation,
                        "cache_read_input_tokens": cache_read,
                        "output_tokens": output_tokens,
                    }
                ],
            },
        },
    }


def _tool_result(tool_use_id: str, content: str, *, uuid: str) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": uuid,
        "timestamp": "2026-08-01T00:00:01Z",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": content,
                }
            ],
        },
    }


def test_classifies_repo_reads_and_agent_browser_commands(tmp_path: Path) -> None:
    analyzer = _load_module()
    repo = tmp_path / "repo"
    repo.mkdir()

    skill = analyzer.classify_tool_source(
        "Read",
        {"file_path": str(repo / "skills/ui-reverse-engineering/capture.md")},
        repo,
    )
    artifact = analyzer.classify_tool_source(
        "Read",
        {"file_path": "./tmp/ref/demo/structure.json"},
        repo,
    )
    browser = analyzer.classify_tool_source(
        "Bash",
        {"command": ("agent-browser --session ref eval --stdin < tmp/ref/demo/probe.js")},
        repo,
    )
    generic_bash = analyzer.classify_tool_source(
        "Bash",
        {"command": "printf '%s\\n' agent-browser.log"},
        repo,
    )
    external_skill = analyzer.classify_tool_source(
        "Bash",
        {"command": (f"sed -n '1,80p' {Path.home()}/.codex/skills/handover/SKILL.md")},
        repo,
    )
    external_read = analyzer.classify_tool_source(
        "Read",
        {"file_path": str(Path.home() / ".codex/skills/handover/SKILL.md")},
        repo,
    )

    assert skill.bucket == "skill_docs"
    assert skill.detail == "skills/ui-reverse-engineering/capture.md"
    assert artifact.bucket == "tmp_ref_artifacts"
    assert artifact.detail == "tmp/ref/demo/structure.json"
    assert browser.bucket == "agent_browser_eval"
    assert generic_bash.bucket == "bash_output"
    assert external_skill.bucket == "bash_output"
    assert external_read.bucket == "other"
    assert str(Path.home()) not in external_read.detail
    assert external_read.detail.startswith("~/")
    assert external_read.detail.endswith("skills/handover/SKILL.md")


def test_redacts_home_and_macos_temp_paths_from_report_details(tmp_path: Path) -> None:
    analyzer = _load_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    macos_temp = "/private/var/folders/61/cache/T/pytest-of-yongjae/pytest-1/test_case0/output.txt"

    normalized = analyzer.normalize_repo_path(macos_temp, repo)
    normalized_encoded_home = analyzer.normalize_repo_path(
        str(
            Path.home()
            / ".claude/projects"
            / f"-Users-{Path.home().name}-Documents-ui-clone-skills/memory/MEMORY.md"
        ),
        repo,
    )
    command_shape = analyzer._command_shape(f"tail -20 {macos_temp}", repo)
    session = analyzer.SessionReport(
        path=Path.home() / ".claude/projects/demo/session.jsonl",
        bytes_scanned=0,
    ).to_dict()
    encoded_home_session = analyzer.SessionReport(
        path=(
            Path.home()
            / ".claude/projects"
            / f"-Users-{Path.home().name}-Documents-ui-clone-skills/session.jsonl"
        ),
        bytes_scanned=0,
    ).to_dict()

    assert normalized.startswith("<temp>/")
    assert "yongjae" not in normalized
    assert Path.home().name not in normalized_encoded_home
    assert "-Users-<user>-Documents-ui-clone-skills" in normalized_encoded_home
    assert command_shape == "tail -<n> <path>"
    assert session["path"] == "~/.claude/projects/demo/session.jsonl"
    assert Path.home().name not in encoded_home_session["path"]
    assert "-Users-<user>-Documents-ui-clone-skills" in encoded_home_session["path"]


def test_deduplicates_usage_joins_results_and_reconciles(tmp_path: Path) -> None:
    analyzer = _load_module()
    transcript = tmp_path / "session.jsonl"
    skill_path = REPO_ROOT / "skills/ui-reverse-engineering/SKILL.md"
    rows = [
        _assistant(
            message_id="msg-1",
            request_id="req-1",
            input_tokens=10,
            cache_creation=20,
            cache_read=30,
            output_tokens=4,
            uuid="a1",
            content={
                "type": "tool_use",
                "id": "read-1",
                "name": "Read",
                "input": {"file_path": str(skill_path)},
            },
        ),
        _assistant(
            message_id="msg-1",
            request_id="req-1",
            input_tokens=10,
            cache_creation=20,
            cache_read=30,
            output_tokens=4,
            uuid="a2",
            content={
                "type": "tool_use",
                "id": "bash-1",
                "name": "Bash",
                "input": {"command": "git status --short"},
            },
        ),
        _tool_result("bash-1", "clean\n", uuid="u1"),
        _tool_result("read-1", "skill documentation body " * 20, uuid="u2"),
        _assistant(
            message_id="msg-2",
            request_id="req-2",
            uuid="a3",
            content={"type": "text", "text": "done"},
            input_tokens=5,
            cache_creation=6,
            cache_read=7,
            output_tokens=2,
        ),
    ]
    _write_jsonl(transcript, rows)

    result = analyzer.analyze_session(transcript, REPO_ROOT).to_dict()

    assert result["api_calls"] == 2
    assert result["usage"] == {
        "input_tokens": 15,
        "cache_creation_input_tokens": 26,
        "cache_read_input_tokens": 37,
        "output_tokens": 6,
    }
    assert result["buckets"]["skill_docs"]["introduced_estimated_tokens"] > 0
    assert result["buckets"]["bash_output"]["introduced_estimated_tokens"] > 0
    assert result["buckets"]["skill_docs"]["allocated"]["cache_read_input_tokens"] > 0
    assert result["buckets"]["bash_output"]["allocated"]["cache_read_input_tokens"] > 0
    for field in (
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        assert result["reconciliation"][field] == pytest.approx(0.0, abs=1e-9)


def test_compaction_records_precompact_sources_and_resets_context(tmp_path: Path) -> None:
    analyzer = _load_module()
    transcript = tmp_path / "compact.jsonl"
    artifact_body = '{"nodes":[' + ('{"tag":"section"},' * 100) + "]}"
    rows = [
        _assistant(
            message_id="msg-1",
            request_id="req-1",
            uuid="a1",
            content={
                "type": "tool_use",
                "id": "read-1",
                "name": "Read",
                "input": {"file_path": "tmp/ref/demo/structure.json"},
            },
            input_tokens=20,
            cache_creation=30,
            cache_read=0,
            output_tokens=3,
        ),
        _tool_result("read-1", artifact_body, uuid="u1"),
        _assistant(
            message_id="msg-2",
            request_id="req-2",
            uuid="a2",
            content={"type": "text", "text": "captured"},
            input_tokens=2,
            cache_creation=10,
            cache_read=180,
            output_tokens=2,
        ),
        {
            "type": "system",
            "subtype": "compact_boundary",
            "uuid": "compact-1",
            "timestamp": "2026-08-01T00:10:00Z",
            "compactMetadata": {"preTokens": 212, "postTokens": 18, "trigger": "manual"},
        },
        _assistant(
            message_id="msg-3",
            request_id="req-3",
            uuid="a3",
            content={"type": "text", "text": "after compact"},
            input_tokens=2,
            cache_creation=8,
            cache_read=18,
            output_tokens=2,
        ),
    ]
    _write_jsonl(transcript, rows)

    result = analyzer.analyze_session(transcript, REPO_ROOT).to_dict()

    assert result["compactions"] == 1
    assert result["max_precompact_tokens"] == 212
    assert result["compact_events"][0]["dominant_bucket"] == "tmp_ref_artifacts"
    assert result["compact_events"][0]["top_files"][0]["path"] == ("tmp/ref/demo/structure.json")
    assert result["segments"] == 2
    assert result["buckets"]["tmp_ref_artifacts"]["introduced_estimated_tokens"] > 0


def test_queue_text_does_not_create_false_source_events(tmp_path: Path) -> None:
    analyzer = _load_module()
    transcript = tmp_path / "queue.jsonl"
    rows = [
        {
            "type": "queue-operation",
            "operation": "enqueue",
            "content": (
                "cat skills/ui-reverse-engineering/SKILL.md; "
                "agent-browser eval; cat tmp/ref/demo/structure.json"
            ),
        },
        _assistant(
            message_id="msg-1",
            request_id="req-1",
            uuid="a1",
            content={"type": "text", "text": "answer"},
            input_tokens=10,
            cache_creation=0,
            cache_read=0,
            output_tokens=1,
        ),
    ]
    _write_jsonl(transcript, rows)

    result = analyzer.analyze_session(transcript, REPO_ROOT).to_dict()

    assert result["buckets"]["skill_docs"]["introduced_estimated_tokens"] == 0
    assert result["buckets"]["tmp_ref_artifacts"]["introduced_estimated_tokens"] == 0
    assert result["buckets"]["agent_browser_eval"]["introduced_estimated_tokens"] == 0
    assert result["buckets"]["other"]["allocated"]["input_tokens"] == pytest.approx(10.0)


def test_repo_skill_invocation_adds_a_labeled_synthetic_doc_estimate(
    tmp_path: Path,
) -> None:
    analyzer = _load_module()
    repo = tmp_path / "repo"
    skill_file = repo / "skills/ui-capture/SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("capture instructions " * 40, encoding="utf-8")
    transcript = tmp_path / "skill.jsonl"
    rows = [
        _assistant(
            message_id="msg-1",
            request_id="req-1",
            uuid="a1",
            content={
                "type": "tool_use",
                "id": "skill-1",
                "name": "Skill",
                "input": {"skill": "ui-capture"},
            },
            input_tokens=10,
            cache_creation=0,
            cache_read=0,
            output_tokens=2,
        ),
        _tool_result("skill-1", "Launching skill: ui-capture", uuid="u1"),
        _assistant(
            message_id="msg-2",
            request_id="req-2",
            uuid="a2",
            content={"type": "text", "text": "loaded"},
            input_tokens=2,
            cache_creation=200,
            cache_read=0,
            output_tokens=2,
        ),
    ]
    _write_jsonl(transcript, rows)

    result = analyzer.analyze_session(transcript, repo).to_dict()

    skill = result["buckets"]["skill_docs"]
    assert skill["synthetic_estimated_tokens"] > 0
    assert skill["introduced_estimated_tokens"] >= skill["synthetic_estimated_tokens"]
    assert result["top_files"][0]["path"] == "skills/ui-capture/SKILL.md"


def test_corpus_aggregation_separates_long_clone_sessions_and_renders_report(
    tmp_path: Path,
) -> None:
    analyzer = _load_module()
    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    clone_rows: list[dict[str, Any]] = []
    for index in range(5):
        clone_rows.extend(
            [
                _assistant(
                    message_id=f"clone-msg-{index}",
                    request_id=f"clone-req-{index}",
                    uuid=f"clone-a-{index}",
                    content={
                        "type": "tool_use",
                        "id": f"clone-tool-{index}",
                        "name": "Bash",
                        "input": {
                            "command": (
                                "python -m ui_clone.gate tmp/ref/demo all; "
                                "bash skills/visual-debug/scripts/section-compare.sh"
                            )
                        },
                    },
                    input_tokens=10,
                    cache_creation=20,
                    cache_read=100 + index,
                    output_tokens=3,
                ),
                _tool_result(
                    f"clone-tool-{index}",
                    "gate output " * 20,
                    uuid=f"clone-u-{index}",
                ),
            ]
        )
    _write_jsonl(transcript_dir / "clone.jsonl", clone_rows)
    _write_jsonl(
        transcript_dir / "short.jsonl",
        [
            _assistant(
                message_id="short-msg",
                request_id="short-req",
                uuid="short-a",
                content={"type": "text", "text": "hello"},
                input_tokens=2,
                cache_creation=0,
                cache_read=0,
                output_tokens=1,
            )
        ],
    )

    corpus = analyzer.analyze_corpus(
        sorted(transcript_dir.glob("*.jsonl")),
        REPO_ROOT,
        long_session_count=3,
    )
    payload = corpus.to_dict()
    markdown = analyzer.render_markdown(corpus)

    assert payload["file_count"] == 2
    assert payload["usage"]["input_tokens"] == 52
    assert payload["reconciliation"]["cache_read_input_tokens"] == pytest.approx(0.0, abs=1e-9)
    assert payload["long_clone_sessions"][0]["session"] == "clone"
    assert payload["long_clone"]["file_count"] == 1
    assert payload["session_type_cohorts"]["clone"]["file_count"] == 1
    assert payload["session_type_cohorts"]["non_clone"]["file_count"] == 1
    assert payload["session_type_cohorts"]["clone"]["usage"]["cache_read_input_tokens"] == 510
    assert payload["session_type_cohorts"]["non_clone"]["usage"]["cache_read_input_tokens"] == 0
    assert "## Session types" in markdown
    assert "## Long clone cohort" in markdown
    assert "does **not** support the claim that skill docs dominate" in markdown
    assert "## Pre-compact pressure" in markdown
    assert "skill_docs" in markdown
    assert "cache_read_input_tokens" in markdown


def test_report_supports_skill_doc_dominance_only_when_measured_share_exceeds_half(
    tmp_path: Path,
) -> None:
    analyzer = _load_module()
    report = analyzer.SessionReport(path=tmp_path / "clone.jsonl", bytes_scanned=0)
    report.clone_tool_events = 5
    report.usage["input_tokens"] = 100
    report.effective_input_tokens = 100
    report.observable_tokens_at_usage = 100
    report.buckets["skill_docs"].allocated["input_tokens"] = 80
    report.buckets["bash_output"].allocated["input_tokens"] = 10
    report.buckets["other"].allocated["input_tokens"] = 10
    corpus = analyzer.CorpusReport([report], [report], "2026-08-09T00:00:00Z")

    markdown = analyzer.render_markdown(corpus)

    assert "supports the claim that skill docs dominate" in markdown
    assert "80.00%" in markdown
    assert "refutes dominance" not in markdown


def test_report_marks_empty_long_clone_cohort_inconclusive() -> None:
    analyzer = _load_module()
    corpus = analyzer.CorpusReport([], [], "2026-08-09T00:00:00Z")

    markdown = analyzer.render_markdown(corpus)

    assert "inconclusive" in markdown


def test_cli_streams_top_level_jsonl_and_writes_bounded_artifacts(tmp_path: Path) -> None:
    _load_module()
    transcript_dir = tmp_path / "transcripts"
    nested_dir = transcript_dir / "subagents"
    nested_dir.mkdir(parents=True)
    row = _assistant(
        message_id="msg",
        request_id="req",
        uuid="a1",
        content={"type": "text", "text": "hello"},
        input_tokens=3,
        cache_creation=4,
        cache_read=5,
        output_tokens=1,
    )
    _write_jsonl(transcript_dir / "top.jsonl", [row])
    _write_jsonl(nested_dir / "nested.jsonl", [row])
    json_out = tmp_path / "analysis.json"
    markdown_out = tmp_path / "report.md"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input-dir",
            str(transcript_dir),
            "--repo-root",
            str(REPO_ROOT),
            "--json-out",
            str(json_out),
            "--markdown-out",
            str(markdown_out),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["file_count"] == 1
    assert payload["usage"]["cache_creation_input_tokens"] == 4
    assert markdown_out.read_text(encoding="utf-8").startswith(
        "# Claude context source attribution"
    )


def test_session_byte_limit_freezes_append_only_transcript_snapshot(tmp_path: Path) -> None:
    analyzer = _load_module()
    transcript = tmp_path / "growing.jsonl"
    first = _assistant(
        message_id="msg-1",
        request_id="req-1",
        uuid="a1",
        content={"type": "text", "text": "first"},
        input_tokens=3,
        cache_creation=4,
        cache_read=5,
        output_tokens=1,
    )
    second = _assistant(
        message_id="msg-2",
        request_id="req-2",
        uuid="a2",
        content={"type": "text", "text": "appended"},
        input_tokens=30,
        cache_creation=40,
        cache_read=50,
        output_tokens=10,
    )
    first_line = json.dumps(first, separators=(",", ":")) + "\n"
    transcript.write_text(
        first_line + json.dumps(second, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    result = analyzer.analyze_session(
        transcript,
        REPO_ROOT,
        byte_limit=len(first_line.encode("utf-8")),
    ).to_dict()

    assert result["api_calls"] == 1
    assert result["usage"]["input_tokens"] == 3
    assert result["bytes_scanned"] == len(first_line.encode("utf-8"))
    assert len(result["sha256"]) == 64
