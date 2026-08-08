import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from ui_clone.pipeline import Pipeline
from ui_clone.pipeline_logs import label_slug, log_tail_lines, tail_text, write_process_log
from ui_clone.pipeline_phases.verify import execute_verify


def test_log_tail_lines_env_parsing() -> None:
    assert log_tail_lines({}) == 120
    assert log_tail_lines({"UI_CLONE_LOG_TAIL_LINES": "0"}) == 0
    assert log_tail_lines({"UI_CLONE_LOG_TAIL_LINES": "3"}) == 3
    assert log_tail_lines({"UI_CLONE_LOG_TAIL_LINES": "bogus"}) == 120


def test_write_process_log_and_tail_text(tmp_path: Path) -> None:
    log_path = write_process_log(
        tmp_path,
        "run",
        "Phase 2.5 — asset metadata extraction",
        "one\ntwo\nthree\n",
        command=["python", "-m", "demo"],
        exit_code=0,
    )

    assert log_path == tmp_path / "logs" / "run" / "phase-2-5-asset-metadata-extraction.log"
    text = log_path.read_text()
    assert "$ python -m demo" in text
    assert "exit: 0" in text
    assert "one\ntwo\nthree" in text
    assert tail_text(text, 2) == "two\nthree"
    assert tail_text(text, 0) == ""
    assert label_slug("section-compare") == "section-compare"


def test_execute_verify_logs_gate_output_without_streaming_full_stdout(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UI_CLONE_LOG_TAIL_LINES", "2")

    ref_dir = tmp_path / "tmp" / "ref" / "comp"
    ref_dir.mkdir(parents=True)
    (tmp_path / "impl").mkdir()
    (ref_dir / "pipeline-state.json").write_text(
        json.dumps({"component": "comp", "current_gate": "post-implement"}),
        encoding="utf-8",
    )

    def fake_run(
        cmd: list[str],
        capture_output: bool,
        text: bool,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output is True
        assert text is True
        assert env["UI_CLONE_PHASE"] == "strict"
        gate = cmd[-1]
        if gate == "post-implement":
            return subprocess.CompletedProcess(
                cmd,
                1,
                stdout="line1\nline2\nline3\nline4\nline5\n",
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{gate} ok\n", stderr="")

    monkeypatch.setattr("ui_clone.pipeline_phases.verify.subprocess.run", fake_run)
    monkeypatch.setattr(
        "ui_clone.pipeline_phases.verify.build_verify_report",
        lambda *args, **kwargs: {"verdict": "fail"},
    )
    monkeypatch.setattr(
        "ui_clone.pipeline_phases.verify.write_verify_report",
        lambda *args, **kwargs: (ref_dir / "verify-report.json", ref_dir / "verify-report.html"),
    )

    pipeline = cast(
        Pipeline,
        SimpleNamespace(
            ref_dir=ref_dir,
            url="https://example.com",
            component="comp",
            session="sess",
        ),
    )
    assert execute_verify(pipeline) == 1

    out = capsys.readouterr().out
    assert "line1" not in out
    assert "line2" not in out
    assert "line4" in out
    assert "line5" in out
    assert "log →" in out

    failing_log = ref_dir / "logs" / "verify" / "post-implement.log"
    assert failing_log.is_file()
    assert "line1\nline2\nline3\nline4\nline5" in failing_log.read_text()
    assert (ref_dir / "logs" / "verify" / "spec.log").is_file()
