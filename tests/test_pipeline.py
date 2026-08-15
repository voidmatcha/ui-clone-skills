"""Tests for ui_clone.pipeline — pipeline status checker."""

import json
import os
import subprocess
import time
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import patch

import pytest

from ui_clone.gate import Gate
from ui_clone.gates.base import CheckResult
from ui_clone.hooks._common import load_json_safe as _load_json_safe
from ui_clone.pipeline import (
    Pipeline,
    _check_dependencies,
    _count_tsx_files,
    _find_app_dir,
    _has_files,
)
from ui_clone.state import GATE_ORDER


class TestCheckDependencies:
    def test_all_present(self) -> None:
        """No missing tools when all are available."""
        with patch("shutil.which", return_value="/usr/bin/tool"):
            missing = _check_dependencies()
        assert missing == []

    def test_missing_tool(self) -> None:
        """Missing tool returned in list."""
        original_which = __import__("shutil").which

        def fake_which(name: str) -> Any:
            if name == "agent-browser":
                return None
            return original_which(name)

        with patch("shutil.which", side_effect=fake_which):
            missing = _check_dependencies()
        assert any("agent-browser" in m for m in missing)


class TestHasFiles:
    def test_has_files_true(self, tmp_path: Path) -> None:
        d = tmp_path / "imgs"
        d.mkdir()
        for i in range(5):
            (d / f"img_{i}.png").write_text("x" * 20)
        assert _has_files(d, "*.png", 5)

    def test_has_files_false(self, tmp_path: Path) -> None:
        d = tmp_path / "imgs"
        d.mkdir()
        (d / "img_0.png").write_text("x" * 20)
        assert not _has_files(d, "*.png", 5)

    def test_missing_dir(self, tmp_path: Path) -> None:
        assert not _has_files(tmp_path / "nonexistent", "*.png", 1)


class TestLoadJsonSafe:
    def test_valid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "test.json"
        p.write_text('{"key": "value"}')
        assert _load_json_safe(p) == {"key": "value"}

    def test_invalid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "test.json"
        p.write_text("not json")
        assert _load_json_safe(p) is None

    def test_missing_file(self, tmp_path: Path) -> None:
        assert _load_json_safe(tmp_path / "nope.json") is None

    def test_array_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "test.json"
        p.write_text("[1, 2, 3]")
        assert _load_json_safe(p) is None


class TestAgentReadableCliSurfaces:
    def test_future_run_dir_resolves_and_status_json_is_json_only(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)
        run_dir = tmp_path / ".ui-clone" / "runs" / "run-1"
        run_dir.mkdir(parents=True)
        (run_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "run-1",
                    "completed_steps": ["reference"],
                    "current_gate": "extraction",
                    "terminalState": {
                        "status": "incomplete",
                        "category": "hardening-probe-incomplete",
                        "gate": "extraction",
                        "reason": "probe harvested incomplete",
                        "recorded_at": "2026-01-01T00:00:00Z",
                    },
                }
            )
        )

        pipeline = Pipeline("https://example.com", "run-1", "sess")
        assert pipeline.ref_dir == run_dir.resolve()

        result = pipeline.print_status_json()
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert result == 0
        assert payload["status"] == "incomplete"
        assert payload["layout"] == "agent-run"
        assert payload["verify_stamp"]["success_only"] is True
        assert payload["read_for_llm"][0].endswith("pipeline-state.json")
        assert "Pipeline State" not in out

    def test_status_payload_reports_invalid_stamp_when_impl_changed_after_verify(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from ui_clone.pipeline_phases.verify import build_verify_stamp
        from ui_clone.state import POST_IMPL_VERIFY_GATES

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        (ref_dir / "sections").mkdir(parents=True)
        (ref_dir / "sections" / "result.txt").write_text(
            "| Section | AE | AE/Mpx | Severity | Status |\n"
            "| hero | 0 | 0 | ok | ✅ |\n"
            "\n**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n",
            encoding="utf-8",
        )
        impl = tmp_path / "impl"
        (impl / "src").mkdir(parents=True)
        app = impl / "src" / "App.tsx"
        app.write_text("export default function App() { return null }\n", encoding="utf-8")
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "current_gate": "done",
                    "completed_steps": list(GATE_ORDER),
                    "implRoot": str(impl),
                }
            ),
            encoding="utf-8",
        )
        stamp = build_verify_stamp(ref_dir, impl, list(POST_IMPL_VERIFY_GATES))
        stamp_path = ref_dir / "verify-stamp.json"
        stamp_path.write_text(json.dumps(stamp), encoding="utf-8")
        old = time.time() - 5
        os.utime(stamp_path, (old, old))
        app.write_text("export default function App() { return <main /> }\n", encoding="utf-8")

        payload = Pipeline("https://example.com", "comp", "sess").status_payload()

        assert payload["status"] == "needs_verify_stamp"
        assert payload["next_action"]
        verify_stamp = payload["verify_stamp"]
        assert isinstance(verify_stamp, dict)
        assert "impl changed after verify" in str(verify_stamp.get("problem"))

    def test_next_json_reports_terminal_next_action(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "current_gate": "post-implement",
                    "terminalState": {
                        "status": "failed",
                        "category": "explicit-terminal",
                        "gate": "post-implement",
                        "reason": "verify failed",
                        "recorded_at": "2026-01-01T00:00:00Z",
                        "next_action": "read verify-report.json",
                    },
                }
            )
        )
        pipeline = Pipeline("https://example.com", "comp", "sess")
        assert pipeline.next(json_output=True) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "failed"
        assert payload["next_action"] == "read verify-report.json"

    def test_status_payload_treats_legacy_canonical_verify_failure_as_active(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "verify-report.json").write_text("{}", encoding="utf-8")
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "current_gate": "post-implement",
                    "terminalState": {
                        "status": "failed",
                        "category": "canonical-verify-failed",
                        "gate": "post-implement",
                        "reason": "verify failed",
                        "recorded_at": "2026-01-01T00:00:00Z",
                        "next_action": "read verify-report.json",
                    },
                }
            ),
            encoding="utf-8",
        )

        payload = Pipeline("https://example.com", "comp", "sess").status_payload()

        assert payload["status"] == "active"
        assert payload["terminalState"] == {}
        next_action = payload["next_action"]
        assert isinstance(next_action, str)
        assert next_action.endswith("comp sess verify")
        read_for_llm = payload["read_for_llm"]
        assert isinstance(read_for_llm, list)
        assert str(ref_dir / "verify-report.json") in read_for_llm

    def test_verify_json_failure_is_recoverable_without_success_stamp(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from ui_clone.pipeline_phases.verify import execute_verify
        from ui_clone.state import PipelineState

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        impl_dir = tmp_path / "impl"
        impl_dir.mkdir()
        PipelineState(component="comp", current_gate="post-implement").save(ref_dir)

        def fake_run(
            cmd: list[str],
            capture_output: bool,
            text: bool,
            env: dict[str, str] | None = None,
        ) -> subprocess.CompletedProcess[str]:
            assert env is not None and env.get("UI_CLONE_PHASE") == "strict"
            gate = cmd[-1]
            code = 1 if gate == "post-implement" else 0
            return subprocess.CompletedProcess(cmd, code, stdout=f"{gate} stdout", stderr="")

        monkeypatch.setattr("ui_clone.pipeline_phases.verify.subprocess.run", fake_run)
        pipeline = Pipeline("https://example.com", "comp", "sess")

        assert execute_verify(pipeline, json_output=True) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "failed"
        assert payload["verify_stamp"]["created"] is False
        assert payload["reports"]["json"].endswith("verify-report.json")
        assert "post-implement" in payload["logs"]
        assert "terminalState" not in payload
        assert not (ref_dir / "verify-stamp.json").exists()
        state = PipelineState.load(ref_dir)
        assert state.terminal_state == {}
        assert (ref_dir / "logs" / "verify" / "post-implement.log").is_file()
        assert (ref_dir / "verify-report.json").is_file()

    def test_verify_text_failure_does_not_label_state_as_terminal_state(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from ui_clone.pipeline_phases.verify import execute_verify
        from ui_clone.state import PipelineState

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        impl_dir = tmp_path / "impl"
        impl_dir.mkdir()
        PipelineState(component="comp", current_gate="post-implement").save(ref_dir)

        def fake_run(
            cmd: list[str],
            capture_output: bool,
            text: bool,
            env: dict[str, str] | None = None,
        ) -> subprocess.CompletedProcess[str]:
            gate = cmd[-1]
            code = 1 if gate == "post-implement" else 0
            return subprocess.CompletedProcess(cmd, code, stdout=f"{gate} stdout", stderr="")

        monkeypatch.setattr("ui_clone.pipeline_phases.verify.subprocess.run", fake_run)

        assert execute_verify(Pipeline("https://example.com", "comp", "sess")) == 1
        out = capsys.readouterr().out
        assert "terminalState:" not in out
        assert "pipeline-state:" in out
        assert PipelineState.load(ref_dir).terminal_state == {}

    def test_verify_json_failure_preserves_preexisting_hard_cap_terminal(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from ui_clone.pipeline_phases.verify import execute_verify
        from ui_clone.state import PipelineState

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        impl_dir = tmp_path / "impl"
        impl_dir.mkdir()
        state = PipelineState(component="comp", current_gate="post-implement")
        state.mark_terminal(
            ref_dir,
            status="unclonable",
            category="hard-cap-fail",
            gate="post-implement",
            reason="hard cap reached",
            written_by="pipeline",
        )

        def fake_run(
            cmd: list[str],
            capture_output: bool,
            text: bool,
            env: dict[str, str] | None = None,
        ) -> subprocess.CompletedProcess[str]:
            gate = cmd[-1]
            code = 1 if gate == "post-implement" else 0
            return subprocess.CompletedProcess(cmd, code, stdout=f"{gate} stdout", stderr="")

        monkeypatch.setattr("ui_clone.pipeline_phases.verify.subprocess.run", fake_run)
        pipeline = Pipeline("https://example.com", "comp", "sess")

        assert execute_verify(pipeline, json_output=True) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "unclonable"
        assert payload["terminalState"]["status"] == "unclonable"
        assert payload["terminalState"]["category"] == "hard-cap-fail"
        assert payload["next_action"] == (
            "Resolve the terminal hard-cap-fail state before rerunning verify."
        )
        reloaded = PipelineState.load(ref_dir)
        assert reloaded.terminal_state["status"] == "unclonable"
        assert reloaded.terminal_state["category"] == "hard-cap-fail"


class TestFindAppDir:
    def test_flat_layout(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "components").mkdir(parents=True)
        result = _find_app_dir(tmp_path, "hero")
        assert result == tmp_path

    def test_monorepo_specific(self, tmp_path: Path) -> None:
        (tmp_path / "apps" / "hero" / "src" / "components").mkdir(parents=True)
        result = _find_app_dir(tmp_path, "hero")
        assert result == tmp_path / "apps" / "hero"

    def test_monorepo_fallback(self, tmp_path: Path) -> None:
        (tmp_path / "apps" / "other" / "src" / "components").mkdir(parents=True)
        result = _find_app_dir(tmp_path, "hero")
        assert result == tmp_path / "apps" / "other"

    def test_no_app_dir(self, tmp_path: Path) -> None:
        assert _find_app_dir(tmp_path, "hero") is None


class TestCountTsxFiles:
    def test_counts_tsx(self, tmp_path: Path) -> None:
        comp_dir = tmp_path / "src" / "components"
        comp_dir.mkdir(parents=True)
        (comp_dir / "Hero.tsx").write_text("export default function Hero() {}")
        (comp_dir / "Footer.tsx").write_text("export default function Footer() {}")
        assert _count_tsx_files(tmp_path) == 2

    def test_no_tsx(self, tmp_path: Path) -> None:
        assert _count_tsx_files(tmp_path) == 0


class TestPipeline:
    def test_done_state_short_circuits(self, tmp_path: Path) -> None:
        """Pipeline with current_gate=done exits immediately."""
        ref_dir = tmp_path / "tmp" / "ref" / "test-comp"
        ref_dir.mkdir(parents=True)
        state = {
            "component": "test-comp",
            "started_at": "2025-01-01T00:00:00Z",
            "completed_steps": [
                "reference",
                "extraction",
                "bundle",
                "spec",
                "pre-generate",
                "post-implement",
                "section-compare",
            ],
            "current_gate": "done",
            "last_updated": "2025-01-01T01:00:00Z",
        }
        (ref_dir / "pipeline-state.json").write_text(json.dumps(state))

        with patch("ui_clone.pipeline.find_project_root", return_value=tmp_path):
            with patch("ui_clone.pipeline._check_dependencies", return_value=[]):
                pipeline = Pipeline("https://example.com", "test-comp", "sess")
                pipeline.project_root = tmp_path
                pipeline.ref_dir = ref_dir
                result = pipeline.run()
        assert result == 0

    def test_missing_deps_returns_1(self, tmp_path: Path) -> None:
        """Pipeline returns 1 when dependencies are missing."""
        with patch("ui_clone.pipeline.find_project_root", return_value=tmp_path):
            with patch("ui_clone.pipeline._check_dependencies", return_value=["agent-browser"]):
                pipeline = Pipeline("https://example.com", "test-comp", "sess")
                pipeline.project_root = tmp_path
                pipeline.ref_dir = tmp_path / "tmp" / "ref" / "test-comp"
                result = pipeline.run()
        assert result == 1

    def test_remaining_json_reports_next_verify_command(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """remaining --json reports pending gates and the canonical resume command."""
        ref_dir = tmp_path / "tmp" / "ref" / "test-comp"
        ref_dir.mkdir(parents=True)
        completed = GATE_ORDER[: GATE_ORDER.index("post-implement")]
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "test-comp",
                    "completed_steps": completed,
                    "current_gate": "post-implement",
                }
            )
        )

        with patch("ui_clone.pipeline.find_project_root", return_value=tmp_path):
            pipeline = Pipeline("https://example.com", "test-comp", "sess")
            pipeline.project_root = tmp_path
            pipeline.ref_dir = ref_dir
            result = pipeline.remaining(json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["current_gate"] == "post-implement"
        assert payload["remaining"][0] == "post-implement"
        assert payload["next_action"].endswith("test-comp sess verify")

    def test_resume_done_reports_no_remaining_gate(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """resume --json is explicit when no gate remains."""
        ref_dir = tmp_path / "tmp" / "ref" / "test-comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "test-comp",
                    "completed_steps": GATE_ORDER,
                    "current_gate": "done",
                }
            )
        )

        with patch("ui_clone.pipeline.find_project_root", return_value=tmp_path):
            pipeline = Pipeline("https://example.com", "test-comp", "sess")
            pipeline.project_root = tmp_path
            pipeline.ref_dir = ref_dir
            result = pipeline.resume(json_output=True)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["current_gate"] == "done"
        assert payload["next_action"] == "No remaining gate."

    def test_reconcile_updates_state_without_failure_counters(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """reconcile recomputes the first failing gate without Gate.run side effects."""
        ref_dir = tmp_path / "tmp" / "ref" / "test-comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "test-comp",
                    "completed_steps": [],
                    "current_gate": "reference",
                    "gate_fail_counts": {"bundle": 7},
                }
            )
        )

        def method_name(gate_name: str) -> str:
            return f"gate_{gate_name.replace('-', '_')}"

        with patch("ui_clone.pipeline.find_project_root", return_value=tmp_path):
            pipeline = Pipeline("https://example.com", "test-comp", "sess")
            pipeline.project_root = tmp_path
            pipeline.ref_dir = ref_dir
            with ExitStack() as stack:
                for gate_name in GATE_ORDER:
                    status: Literal["pass", "fail", "warn"] = (
                        "fail" if gate_name == "bundle" else "pass"
                    )
                    stack.enter_context(
                        patch.object(
                            Gate,
                            method_name(gate_name),
                            return_value=[
                                CheckResult(
                                    f"{gate_name} check",
                                    status,
                                    f"{gate_name} {status}",
                                )
                            ],
                        )
                    )
                result = pipeline.reconcile(json_output=True)

        assert result == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["current_gate"] == "bundle"
        assert payload["completed_steps"] == ["reference", "extraction"]
        state = _load_json_safe(ref_dir / "pipeline-state.json")
        assert state is not None
        assert state["current_gate"] == "bundle"
        assert state["completed_steps"] == ["reference", "extraction"]
        assert state["gate_fail_counts"] == {"bundle": 7}

    def test_verify_stamps_pipeline_state_impl_root(self, tmp_path: Path) -> None:
        """verify must stamp the run-owned implRoot, not a drifting cwd/impl symlink."""
        from ui_clone.pipeline_phases.verify import execute_verify

        ref_dir = tmp_path / "tmp" / "ref" / "test-comp"
        ref_dir.mkdir(parents=True)
        canonical_impl = tmp_path / "scratch" / "test-comp" / "impl"
        canonical_impl.mkdir(parents=True)
        cwd_impl = tmp_path / "impl"
        cwd_impl.mkdir()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "test-comp",
                    "current_gate": "post-implement",
                    "implRoot": str(canonical_impl),
                }
            ),
            encoding="utf-8",
        )

        with patch("pathlib.Path.cwd", return_value=tmp_path):
            with patch(
                "ui_clone.pipeline_phases.verify.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ):
                with patch(
                    "ui_clone.pipeline_phases.verify.build_verify_report",
                    return_value={"verdict": "pass", "gates": []},
                ) as build_report:
                    with patch(
                        "ui_clone.pipeline_phases.verify.write_verify_report",
                        return_value=(ref_dir / "verify-report.json", ref_dir / "verify-report.html"),
                    ):
                        result = execute_verify(
                            cast(Pipeline, SimpleNamespace(ref_dir=ref_dir))
                        )

        assert result == 0
        stamp = json.loads((ref_dir / "verify-stamp.json").read_text(encoding="utf-8"))
        assert stamp["implDir"] == str(canonical_impl.resolve())
        assert build_report.call_args.kwargs["impl_dir"] == canonical_impl.resolve()

    def test_verify_forces_strict_phase_for_gate_subprocesses(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Canonical verify must not inherit rapid-mode warn downgrades."""
        from ui_clone.pipeline_phases.verify import execute_verify

        ref_dir = tmp_path / "tmp" / "ref" / "strict-comp"
        ref_dir.mkdir(parents=True)
        impl = tmp_path / "impl"
        impl.mkdir()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {"component": "strict-comp", "current_gate": "post-implement", "implRoot": str(impl)}
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("UI_CLONE_PHASE", "rapid")
        seen_envs: list[dict[str, str] | None] = []

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            env = kwargs.get("env")
            seen_envs.append(cast(dict[str, str] | None, env))
            return subprocess.CompletedProcess(args=[], returncode=0)

        with patch("pathlib.Path.cwd", return_value=tmp_path):
            with patch("ui_clone.pipeline_phases.verify.subprocess.run", side_effect=fake_run):
                with patch(
                    "ui_clone.pipeline_phases.verify.build_verify_report",
                    return_value={"verdict": "pass", "gates": []},
                ):
                    with patch(
                        "ui_clone.pipeline_phases.verify.write_verify_report",
                        return_value=(ref_dir / "verify-report.json", ref_dir / "verify-report.html"),
                    ):
                        result = execute_verify(
                            cast(Pipeline, SimpleNamespace(ref_dir=ref_dir))
                        )

        assert result == 0
        assert seen_envs
        assert all(env is not None and env.get("UI_CLONE_PHASE") == "strict" for env in seen_envs)

    def test_verify_does_not_leave_stamp_when_success_report_write_fails(
        self,
        tmp_path: Path,
    ) -> None:
        """A partial successful verify report write must not leave a release stamp."""
        from ui_clone.pipeline_phases.verify import execute_verify

        ref_dir = tmp_path / "tmp" / "ref" / "atomic-comp"
        ref_dir.mkdir(parents=True)
        impl = tmp_path / "impl"
        impl.mkdir()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {"component": "atomic-comp", "current_gate": "post-implement", "implRoot": str(impl)}
            ),
            encoding="utf-8",
        )

        with patch("pathlib.Path.cwd", return_value=tmp_path):
            with patch(
                "ui_clone.pipeline_phases.verify.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ):
                with patch(
                    "ui_clone.pipeline_phases.verify.build_verify_report",
                    return_value={"verdict": "pass", "gates": []},
                ):
                    with patch(
                        "ui_clone.pipeline_phases.verify.write_verify_report",
                        side_effect=OSError("disk full"),
                    ):
                        with pytest.raises(OSError):
                            execute_verify(cast(Pipeline, SimpleNamespace(ref_dir=ref_dir)))

        assert not (ref_dir / "verify-stamp.json").exists()

    @pytest.mark.parametrize(
        ("transitions", "fires", "expected_result"),
        [
            (
                [
                    {"id": "measurable", "target": ".measurable"},
                    {"id": "blocked", "target": ".blocked"},
                ],
                {
                    "unmeasurableIds": ["blocked"],
                    "entries": [
                        {"id": "measurable", "status": "pass"},
                        {"id": "blocked", "status": "pass"},
                    ],
                },
                1,
            ),
            (
                [
                    {"id": "measurable", "target": ".measurable"},
                    {"id": "blocked", "target": ".blocked"},
                ],
                {
                    "unmeasurableIds": [],
                    "entries": [
                        {"id": "measurable", "status": "pass"},
                        {"id": "blocked", "status": "unmeasurable"},
                    ],
                },
                1,
            ),
            (
                [],
                {
                    "unmeasurableIds": ["stale"],
                    "entries": [{"id": "stale", "status": "unmeasurable"}],
                },
                0,
            ),
        ],
    )
    def test_verify_blocks_unmeasurable_motion_only_for_nonempty_spec(
        self,
        tmp_path: Path,
        transitions: list[dict[str, str]],
        fires: dict[str, object],
        expected_result: int,
    ) -> None:
        from ui_clone.pipeline_phases.verify import execute_verify

        ref_dir = tmp_path / "tmp" / "ref" / "motion"
        ref_dir.mkdir(parents=True)
        impl = tmp_path / "impl"
        impl.mkdir()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {"component": "motion", "current_gate": "post-implement", "implRoot": str(impl)}
            ),
            encoding="utf-8",
        )
        (ref_dir / "transition-spec.json").write_text(
            json.dumps({"transitions": transitions}),
            encoding="utf-8",
        )
        (ref_dir / "transition-fires.json").write_text(
            json.dumps(fires),
            encoding="utf-8",
        )

        with patch("pathlib.Path.cwd", return_value=tmp_path):
            with patch(
                "ui_clone.pipeline_phases.verify.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ):
                with patch(
                    "ui_clone.pipeline_phases.verify.build_verify_report",
                    return_value={"verdict": "pass", "gates": []},
                ):
                    with patch(
                        "ui_clone.pipeline_phases.verify.write_verify_report",
                        return_value=(ref_dir / "r.json", ref_dir / "r.html"),
                    ):
                        result = execute_verify(
                            cast(Pipeline, SimpleNamespace(ref_dir=ref_dir))
                        )

        assert result == expected_result
        assert (ref_dir / "verify-stamp.json").is_file() is (expected_result == 0)

    def test_verify_invalidates_prior_stamp_before_unmeasurable_motion_failure(
        self,
        tmp_path: Path,
    ) -> None:
        """A failed re-verify cannot leave an earlier green stamp reusable."""
        from ui_clone.pipeline_phases.verify import build_verify_stamp, execute_verify
        from ui_clone.state import POST_IMPL_VERIFY_GATES

        ref_dir = tmp_path / "tmp" / "ref" / "motion"
        ref_dir.mkdir(parents=True)
        impl = tmp_path / "impl"
        impl.mkdir()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {"component": "motion", "current_gate": "post-implement", "implRoot": str(impl)}
            ),
            encoding="utf-8",
        )
        (ref_dir / "transition-spec.json").write_text(
            json.dumps({"transitions": [{"id": "blocked", "target": ".blocked"}]}),
            encoding="utf-8",
        )
        fires_path = ref_dir / "transition-fires.json"
        fires_path.write_text(
            json.dumps(
                {
                    "unmeasurableIds": [],
                    "entries": [{"id": "blocked", "status": "pass"}],
                }
            ),
            encoding="utf-8",
        )
        prior_stamp = build_verify_stamp(
            ref_dir,
            impl,
            list(POST_IMPL_VERIFY_GATES),
        )
        (ref_dir / "verify-stamp.json").write_text(
            json.dumps(prior_stamp),
            encoding="utf-8",
        )
        fires_path.write_text(
            json.dumps(
                {
                    "unmeasurableIds": ["blocked"],
                    "entries": [{"id": "blocked", "status": "unmeasurable"}],
                }
            ),
            encoding="utf-8",
        )

        with patch("pathlib.Path.cwd", return_value=tmp_path):
            with patch(
                "ui_clone.pipeline_phases.verify.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ):
                with patch(
                    "ui_clone.pipeline_phases.verify.build_verify_report",
                    return_value={"verdict": "pass", "gates": []},
                ):
                    with patch(
                        "ui_clone.pipeline_phases.verify.write_verify_report",
                        return_value=(ref_dir / "r.json", ref_dir / "r.html"),
                    ):
                        result = execute_verify(
                            cast(Pipeline, SimpleNamespace(ref_dir=ref_dir))
                        )

        assert result == 1
        assert not (ref_dir / "verify-stamp.json").exists()

    def _run_verify_with_skip_log(self, tmp_path: Path, skip_gate: str) -> tuple[int, Path]:
        from ui_clone.pipeline_phases.verify import execute_verify

        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        impl = tmp_path / "impl"
        impl.mkdir()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {"component": "comp", "current_gate": "post-implement", "implRoot": str(impl)}
            ),
            encoding="utf-8",
        )
        (ref_dir / ".gate-skip-log").write_text(
            f"2026-01-01T00:00:00Z gate={skip_gate} reason=FileNotFoundError\n",
            encoding="utf-8",
        )
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            with patch(
                "ui_clone.pipeline_phases.verify.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ):
                with patch(
                    "ui_clone.pipeline_phases.verify.build_verify_report",
                    return_value={"verdict": "pass", "gates": []},
                ):
                    with patch(
                        "ui_clone.pipeline_phases.verify.write_verify_report",
                        return_value=(ref_dir / "r.json", ref_dir / "r.html"),
                    ):
                        result = execute_verify(
                            cast(Pipeline, SimpleNamespace(ref_dir=ref_dir))
                        )
        return result, ref_dir

    def test_verify_not_blocked_by_skip_it_reruns(self, tmp_path: Path) -> None:
        """A capable host re-running verify must NOT be false-blocked by its own
        prior fail-open skip of a post-impl gate: the verify loop reruns the gate
        and clears its skip entry, so closeout proceeds (the MAJOR review fix)."""
        result, ref_dir = self._run_verify_with_skip_log(tmp_path, "spec")
        assert result == 0, "verify must not block on a skip it just re-enforced"
        assert not (ref_dir / ".gate-skip-log").exists(), "the rerun must clear the skip"
        assert (ref_dir / "verify-stamp.json").is_file()

    def test_verify_blocks_on_unrecovered_skip(self, tmp_path: Path) -> None:
        """A gate skipped fail-open and NOT re-run by the verify loop (an
        earlier-phase gate like `bundle`) must still block the success stamp."""
        result, ref_dir = self._run_verify_with_skip_log(tmp_path, "bundle")
        assert result == 1, "an un-recovered fail-open skip must block closeout"
        assert not (ref_dir / "verify-stamp.json").is_file()


class TestPipelineFullRun:
    """Integration test for Pipeline.run() with a fully populated ref_dir."""

    def test_full_run_all_phases_present(self, tmp_path: Path, ref_dir_with_artifacts: Path) -> None:
        """Pipeline.run() completes without error when all artifacts exist."""
        # Create app dir so Phase 3 passes
        comp_dir = tmp_path / "src" / "components"
        comp_dir.mkdir(parents=True)
        (comp_dir / "Hero.tsx").write_text("export default function Hero() {}")

        # Set pipeline state to done
        (ref_dir_with_artifacts / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": ref_dir_with_artifacts.name,
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [
                        "reference",
                        "extraction",
                        "bundle",
                        "spec",
                        "pre-generate",
                        "post-implement",
                        "section-compare",
                    ],
                    "current_gate": "done",
                    "last_updated": "2026-01-01T01:00:00Z",
                }
            )
        )

        with patch("ui_clone.pipeline.find_project_root", return_value=tmp_path):
            with patch("ui_clone.pipeline._check_dependencies", return_value=[]):
                pipeline = Pipeline("https://example.com", ref_dir_with_artifacts.name, "sess")
                pipeline.project_root = tmp_path
                pipeline.ref_dir = ref_dir_with_artifacts
                result = pipeline.run()
        assert result == 0

    def test_full_run_incomplete_reports_next_phase(self, tmp_path: Path) -> None:
        """Pipeline.run() reports next phase when artifacts are missing."""
        ref_dir = tmp_path / "tmp" / "ref" / "test-comp"
        ref_dir.mkdir(parents=True)
        # Only canvas detection exists — Phase 1 should be next
        (ref_dir / "canvas-webgl-detection.json").write_text(
            json.dumps({"primaryRenderType": "DOM", "hasCanvas": False, "hasWebGL": False})
        )

        with patch("ui_clone.pipeline.find_project_root", return_value=tmp_path):
            with patch("ui_clone.pipeline._check_dependencies", return_value=[]):
                pipeline = Pipeline("https://example.com", "test-comp", "sess")
                pipeline.project_root = tmp_path
                pipeline.ref_dir = ref_dir
                result = pipeline.run()
        assert result == 0
        assert pipeline.next_phase == "1"


class TestPipelinePhases:
    """Unit tests for individual Pipeline.check_phase_* methods."""

    def _make_pipeline(self, tmp_path: Path, ref_dir: Path) -> Any:
        with patch("ui_clone.pipeline.find_project_root", return_value=tmp_path):
            p = Pipeline("https://example.com", ref_dir.name, "sess")
            p.project_root = tmp_path
            p.ref_dir = ref_dir
        return p

    def test_check_phase_0a_missing(self, tmp_path: Path) -> None:
        """Phase 0A: no canvas-webgl-detection.json → check fails."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_0a()
        assert result.name == "0A"
        assert any(not c.passed for c in result.checks)

    def test_check_phase_0a_present(self, tmp_path: Path) -> None:
        """Phase 0A: canvas-webgl-detection.json present → check passes."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "canvas-webgl-detection.json").write_text(
            json.dumps({"primaryRenderType": "DOM", "hasCanvas": False, "hasWebGL": False})
        )
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_0a()
        assert any(c.passed for c in result.checks)

    def test_check_phase_0a_canvas_detected(self, tmp_path: Path) -> None:
        """Phase 0A: canvas detected → still passes but sets next_phase if ref_dir is new."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "canvas-webgl-detection.json").write_text(
            json.dumps({"primaryRenderType": "canvas", "hasCanvas": True, "hasWebGL": False})
        )
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_0a()
        assert any(c.passed for c in result.checks)

    def test_check_phase_0_no_prior_data(self, tmp_path: Path) -> None:
        """Phase 0: no prior data → both checks fail."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_0()
        assert result.name == "0"
        assert not any(c.passed for c in result.checks)

    def test_check_phase_0_with_prior_data(self, tmp_path: Path) -> None:
        """Phase 0: both transition-spec.json and extracted.json present."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "transition-spec.json").write_text(json.dumps({"transitions": []}))
        (ref_dir / "extracted.json").write_text(json.dumps({"sections": []}))
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_0()
        assert all(c.passed for c in result.checks)

    def test_check_phase_1_no_screenshots(self, tmp_path: Path) -> None:
        """Phase 1: no reference screenshots → sets next_phase."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_1()
        assert result.name == "1"
        assert not any(c.passed for c in result.checks)
        assert p.next_phase == "1"

    def test_check_phase_1_with_screenshots(self, tmp_path: Path) -> None:
        """Phase 1: all reference artifacts present."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        screenshots = ref_dir / "static" / "ref"
        screenshots.mkdir(parents=True)
        for i in range(5):
            (screenshots / f"scroll_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
        scroll_dir = ref_dir / "scroll-video" / "ref"
        scroll_dir.mkdir(parents=True)
        (scroll_dir / "scroll.webm").write_bytes(b"\x1a" + b"\x00" * 100)
        trans_dir = ref_dir / "transitions" / "ref"
        trans_dir.mkdir(parents=True)
        (trans_dir / "hover.webm").write_bytes(b"\x1a" + b"\x00" * 100)
        (ref_dir / "regions.json").write_text(json.dumps({"regions": []}))
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_1()
        assert all(c.passed for c in result.checks)

    def test_check_phase_2_skipped_without_ref(self, tmp_path: Path) -> None:
        """Phase 2: skipped when has_ref=False."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_2(has_ref=False)
        assert result.skipped

    def test_check_phase_1_regions_only_does_not_set_has_ref(self, tmp_path: Path) -> None:
        """Regression: regions.json existing alone must not satisfy has_ref.

        The supplementary phase-1 checks (scroll-video, transitions, regions.json)
        can pass independently. Only static/ref/ screenshots is the canonical
        "reference exists" signal — the run_status() codepath at pipeline.py uses
        phase_1.checks[0].passed to decide whether Phase 2 may proceed.
        """
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "regions.json").write_text(json.dumps({"regions": []}))
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_1()
        assert result.checks[0].passed is False, "static/ref/ screenshots must fail"
        assert result.checks[3].passed is True, "regions.json must pass"
        # The fix: has_ref derives from the canonical first check, not any().
        assert result.checks[0].passed is False

    def test_check_phase_3_no_app_dir(self, tmp_path: Path) -> None:
        """Phase 3: no app directory → sets next_phase."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        p = self._make_pipeline(tmp_path, ref_dir)
        p.next_phase = ""  # Reset
        result = p.check_phase_3()
        assert result.name == "3"
        assert p.next_phase == "3"

    def test_check_phase_3_with_tsx_files(self, tmp_path: Path) -> None:
        """Phase 3: tsx files present → passes."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        comp_dir = tmp_path / "src" / "components"
        comp_dir.mkdir(parents=True)
        (comp_dir / "Hero.tsx").write_text("export default function Hero() {}")
        p = self._make_pipeline(tmp_path, ref_dir)
        p.next_phase = ""
        result = p.check_phase_3()
        assert result.name == "3"
        # next_phase should NOT be set to "3" since component exists
        assert p.next_phase != "3"

    def test_check_phase_4_no_impl(self, tmp_path: Path) -> None:
        """Phase 4: no impl screenshots → sets next_phase."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        p = self._make_pipeline(tmp_path, ref_dir)
        p.next_phase = ""
        result = p.check_phase_4()
        assert result.name == "4"
        assert p.next_phase == "4"

    def test_check_phase_4_with_impl(self, tmp_path: Path) -> None:
        """Phase 4: impl screenshots and diffs present → next_phase set to 4 (verify step)."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        impl_dir = ref_dir / "static" / "impl"
        impl_dir.mkdir(parents=True)
        for i in range(5):
            (impl_dir / f"scroll_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
        diff_dir = ref_dir / "static" / "diff"
        diff_dir.mkdir(parents=True)
        (diff_dir / "diff_0.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
        p = self._make_pipeline(tmp_path, ref_dir)
        p.next_phase = ""
        result = p.check_phase_4()
        assert result.name == "4"
        # Phase 4 always sets next_phase to "4" when no prior phase was incomplete
        assert p.next_phase == "4"

    def test_set_next_first_incomplete_wins(self, tmp_path: Path) -> None:
        """_set_next only records the first incomplete phase."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        p = self._make_pipeline(tmp_path, ref_dir)
        p._set_next("1", "Do phase 1")
        p._set_next("2", "Do phase 2")
        assert p.next_phase == "1"
        assert p.next_step == "Do phase 1"

    def test_json_output(self, tmp_path: Path) -> None:
        """Pipeline.run(json_output=True) prints JSON summary."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [
                        "reference",
                        "extraction",
                        "bundle",
                        "spec",
                        "pre-generate",
                        "post-implement",
                        "section-compare",
                    ],
                    "current_gate": "done",
                    "last_updated": "2026-01-01T01:00:00Z",
                }
            )
        )
        with patch("ui_clone.pipeline.find_project_root", return_value=tmp_path):
            with patch("ui_clone.pipeline._check_dependencies", return_value=[]):
                p = Pipeline("https://example.com", "comp", "sess")
                p.project_root = tmp_path
                p.ref_dir = ref_dir
                result = p.run(json_output=True)
        assert result == 0


class TestPipelineRunDriver:
    """Regression tests for `pipeline ... run --phases ...` driver behavior."""

    def _make_pipeline(self, tmp_path: Path, ref_dir: Path) -> Any:
        with patch("ui_clone.pipeline.find_project_root", return_value=tmp_path):
            p = Pipeline("https://example.com", ref_dir.name, "sess")
            p.project_root = tmp_path
            p.ref_dir = ref_dir
        return p

    def _fake_plugin_root(self, tmp_path: Path) -> Path:
        root = tmp_path / "plugin"
        (root / "scripts" / "extract").mkdir(parents=True)
        (root / "skills" / "visual-debug" / "scripts").mkdir(parents=True)
        asset_metadata = root / "scripts" / "extract" / "extract-asset-metadata.sh"
        asset_metadata.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "ref=\"$2\"\n"
            "mkdir -p \"$ref/css\"\n"
            "printf called > \"$ref/asset-metadata-called\"\n"
            "printf '{\"title\":\"Example\",\"url\":\"%s\"}\\n' \"$3\" > \"$ref/head.json\"\n"
            "printf '{\"faces\":[]}\\n' > \"$ref/fonts.json\"\n"
            "printf '{\"images\":[]}\\n' > \"$ref/visible-images.json\"\n"
            "printf '/* ui-clone: no CSS custom properties observed in downloaded CSS */\\n' > \"$ref/css/variables.txt\"\n",
            encoding="utf-8",
        )
        return root

    def test_execute_phase_0a_writes_canvas_detection_artifact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Phase 0A run must produce canvas-webgl-detection.json, not only check it."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        root = self._fake_plugin_root(tmp_path)
        detect = root / "skills" / "visual-debug" / "scripts" / "canvas-webgl-detect.sh"
        detect.write_text(
            "#!/usr/bin/env bash\n"
            "mkdir -p \"$3\"\n"
            "printf '{\"schemaVersion\":1,\"primaryRenderType\":\"DOM\",\"hasCanvas\":false,\"hasWebGL\":false}\\n' > \"$3/canvas-webgl-detection.json\"\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("PLUGIN_ROOT", str(root))

        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.execute_phases(("0A",))

        assert result == 0
        artifact = json.loads((ref_dir / "canvas-webgl-detection.json").read_text())
        assert artifact["primaryRenderType"] == "DOM"

    def test_execute_phase_1_marks_reference_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Phase 1 must stamp the reference gate after producing artifacts."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        root = self._fake_plugin_root(tmp_path)
        capture = root / "scripts" / "extract" / "capture.sh"
        capture.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "ref=\"$3\"\n"
            "mkdir -p \"$ref/static/ref\" \"$ref/transitions/ref\" \"$ref/scroll-video/ref\"\n"
            "for i in 0 1 2 3 4; do printf png > \"$ref/static/ref/scroll_$i.png\"; done\n"
            "printf webm > \"$ref/transitions/ref/hover.webm\"\n"
            "printf webm > \"$ref/scroll-video/ref/scroll.webm\"\n"
            "printf '{\"placeholder\":true,\"detectionRan\":false,"
            "\"regions\":[{\"name\":\"full-page-placeholder\","
            "\"selector\":\"body\"}]}\\n' > \"$ref/regions.json\"\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("PLUGIN_ROOT", str(root))

        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.execute_phases(("1",))

        assert result == 0
        state = _load_json_safe(ref_dir / "pipeline-state.json")
        assert state is not None
        assert "reference" in state["completed_steps"]
        assert state["current_gate"] == "extraction"

    def test_execute_phase_2_marks_extraction_and_bundle_gates(
        self,
        tmp_path: Path,
        ref_dir_with_artifacts: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Phase 2 must not leave a valid artifact tree with an unstamped state cursor."""
        ref_dir = ref_dir_with_artifacts
        (ref_dir / "detected-breakpoints.json").write_text(
            json.dumps({"breakpoints": [768, 1024, 1440]})
        )
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": ref_dir.name,
                    "completed_steps": ["reference"],
                    "current_gate": "extraction",
                }
            )
        )
        root = self._fake_plugin_root(tmp_path)
        monkeypatch.setenv("PLUGIN_ROOT", str(root))

        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.execute_phases(("2",))

        assert result == 0
        state = _load_json_safe(ref_dir / "pipeline-state.json")
        assert state is not None
        assert state["completed_steps"][:3] == ["reference", "extraction", "bundle"]
        assert state["current_gate"] == "paid-features"

    def test_execute_phase_2_runs_bundle_extraction_producer(
        self,
        tmp_path: Path,
        ref_dir_with_artifacts: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A1: Phase 2 must run the deterministic bundle-parameter extractor so
        bundle-extraction.json is produced WITHOUT dispatching a subagent.

        Previously bundle-extraction.json only appeared as a side effect of the
        agent running download-chunks.sh. The Phase-2 driver now invokes
        bundle-extraction.sh directly (producer-only, best-effort).
        """
        ref_dir = ref_dir_with_artifacts
        (ref_dir / "detected-breakpoints.json").write_text(
            json.dumps({"breakpoints": [768, 1024, 1440]})
        )
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": ref_dir.name,
                    "completed_steps": ["reference"],
                    "current_gate": "extraction",
                }
            )
        )
        root = self._fake_plugin_root(tmp_path)
        bundle_extraction = root / "scripts" / "extract" / "bundle-extraction.sh"
        bundle_extraction.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'ref="$1"\n'
            "[ -d \"$ref/bundles\" ] || exit 0\n"
            'printf \'{"schemaVersion":1,"bundlesScanned":3,"extractions":{}}\\n\' '
            '> "$ref/bundle-extraction.json"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("PLUGIN_ROOT", str(root))

        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.execute_phases(("2",))

        assert result == 0
        artifact = _load_json_safe(ref_dir / "bundle-extraction.json")
        assert artifact is not None
        assert artifact["schemaVersion"] == 1

    def _write_phase3_stub_scripts(self, root: Path) -> None:
        """Stub the deterministic Phase-3 codegen chain so the driver branch can
        be exercised without a real browser/transpiler run. Each stub drops a
        marker so the test can assert it ran with the right args."""
        gen_plan = root / "scripts" / "extract" / "generation-plan.sh"
        gen_plan.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\nref=\"$1\"\n"
            'printf ran > "$ref/gen-plan-ran"\n',
            encoding="utf-8",
        )
        for name in ("scaffold-to-jsx.sh", "emit-scroll-helpers.sh"):
            extra = (
                'mkdir -p "$impl/src/components"\n'
                'printf "export default () => null;" > "$impl/src/components/Stub.tsx"\n'
                if name == "scaffold-to-jsx.sh" else ""
            )
            (root / "skills" / "visual-debug" / "scripts" / name).write_text(
                "#!/usr/bin/env bash\nset -euo pipefail\nref=\"$1\"\nimpl=\"$2\"\n"
                f'printf "$impl" > "$ref/{name}-ran"\n{extra}',
                encoding="utf-8",
            )

    def test_execute_phase_3_deterministic_generate_runs_codegen_chain(
        self,
        tmp_path: Path,
        ref_dir_with_artifacts: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With UI_CLONE_DETERMINISTIC_GENERATE set, Phase 3 runs the codegen
        chain (generation-plan -> scaffold-to-jsx -> emit-scroll-helpers) that was
        previously reachable only from the LLM/SKILL.md path."""
        ref_dir = ref_dir_with_artifacts
        root = self._fake_plugin_root(tmp_path)
        self._write_phase3_stub_scripts(root)
        monkeypatch.setenv("PLUGIN_ROOT", str(root))
        monkeypatch.setenv("UI_CLONE_DETERMINISTIC_GENERATE", "1")
        monkeypatch.setenv("UI_CLONE_IMPL_ROOT", str(tmp_path / "impl"))

        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.execute_phases(("3",))

        assert result == 0
        assert (ref_dir / "gen-plan-ran").is_file()
        # scaffold + scroll-helpers receive the resolved impl root as 2nd arg.
        assert (ref_dir / "scaffold-to-jsx.sh-ran").read_text().strip() != ""
        assert (ref_dir / "emit-scroll-helpers.sh-ran").is_file()

    def test_execute_phase_3_without_flag_is_unchanged(
        self,
        tmp_path: Path,
        ref_dir_with_artifacts: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Default behavior must be identical to before: no flag -> Phase 3 is
        'not yet supported' (return 1) and the codegen chain never runs."""
        ref_dir = ref_dir_with_artifacts
        root = self._fake_plugin_root(tmp_path)
        self._write_phase3_stub_scripts(root)
        monkeypatch.setenv("PLUGIN_ROOT", str(root))
        monkeypatch.delenv("UI_CLONE_DETERMINISTIC_GENERATE", raising=False)

        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.execute_phases(("3",))

        assert result == 1
        assert not (ref_dir / "gen-plan-ran").is_file()
        assert not (ref_dir / "scaffold-to-jsx.sh-ran").is_file()

    def test_execute_phase_3_missing_emitter_is_fatal(
        self,
        tmp_path: Path,
        ref_dir_with_artifacts: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Review follow-up: opting into deterministic generation with a missing
        emitter script must FAIL loudly, not silently skip + report success."""
        ref_dir = ref_dir_with_artifacts
        root = self._fake_plugin_root(tmp_path)
        self._write_phase3_stub_scripts(root)
        # Remove one required emitter.
        (root / "skills" / "visual-debug" / "scripts" / "emit-scroll-helpers.sh").unlink()
        monkeypatch.setenv("PLUGIN_ROOT", str(root))
        monkeypatch.setenv("UI_CLONE_DETERMINISTIC_GENERATE", "1")
        monkeypatch.setenv("UI_CLONE_IMPL_ROOT", str(tmp_path / "impl"))

        p = self._make_pipeline(tmp_path, ref_dir)
        assert p.execute_phases(("3",)) == 1

    def test_execute_phase_3_no_components_generated_fails(
        self,
        tmp_path: Path,
        ref_dir_with_artifacts: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Review follow-up: phase-3 success requires actual .tsx output — a chain
        that runs but emits no components must not return 0."""
        ref_dir = ref_dir_with_artifacts
        root = self._fake_plugin_root(tmp_path)
        self._write_phase3_stub_scripts(root)
        # Neuter the scaffold so it produces NO .tsx.
        (root / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh").write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\nref=\"$1\"\n"
            'printf ran > "$ref/scaffold-to-jsx.sh-ran"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("PLUGIN_ROOT", str(root))
        monkeypatch.setenv("UI_CLONE_DETERMINISTIC_GENERATE", "1")
        monkeypatch.setenv("UI_CLONE_IMPL_ROOT", str(tmp_path / "impl"))

        p = self._make_pipeline(tmp_path, ref_dir)
        assert p.execute_phases(("3",)) == 1

    def test_execute_phase_2_bundle_crash_with_bundles_is_fatal(
        self,
        tmp_path: Path,
        ref_dir_with_artifacts: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bundle fail-closed reverses the earlier N3 fail-open: when bundles/ ARE
        present and the deterministic bundle parser crashes (non-zero exit),
        execute.py records bundle-extraction-status.json {"completed": false}, and
        gate_bundle now FAILS on that — so Phase 2 is fatal (the run can't silently
        proceed past a crashed producer). _bundle_extraction.py exits non-zero only
        on a true crash, never on a benign "couldn't fully resolve a minified
        bundle" (that returns exit 0 with unresolved[]), so this surfaces an honest
        mistake rather than false-blocking minified sites. Green paths are
        unaffected: a clean parse writes no status artifact; a no-bundles SKIP exits
        0; downstream consumers (hover_probe / state_reveal) stay fail-open.
        """
        ref_dir = ref_dir_with_artifacts  # fixture provides bundles/ with chunks
        (ref_dir / "detected-breakpoints.json").write_text(
            json.dumps({"breakpoints": [768, 1024, 1440]})
        )
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": ref_dir.name,
                    "completed_steps": ["reference"],
                    "current_gate": "extraction",
                }
            )
        )
        root = self._fake_plugin_root(tmp_path)
        bundle_extraction = root / "scripts" / "extract" / "bundle-extraction.sh"
        bundle_extraction.write_text(
            "#!/usr/bin/env bash\nexit 7\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("PLUGIN_ROOT", str(root))

        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.execute_phases(("2",))

        assert result != 0  # bundle gate fails on completed=false → Phase 2 fatal
        state = _load_json_safe(ref_dir / "pipeline-state.json")
        assert state is not None
        # The bundle gate did not pass, so "bundle" is not recorded complete.
        assert "bundle" not in state["completed_steps"]
        status = _load_json_safe(ref_dir / "bundle-extraction-status.json")
        assert status is not None and status["completed"] is False

    def test_execute_phase_2_bundle_failure_with_bundles_writes_advisory(
        self,
        tmp_path: Path,
        ref_dir_with_artifacts: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """N3 (advisory) + bundle fail-closed: a bundle-extraction crash WITH
        bundles/ present still writes the durable advisory status artifact (so
        Phase 5d has an explicit obligation), AND gate_bundle now consumes that
        completed=false status and FAILS — so the run is blocked rather than
        silently proceeding past a crashed producer."""
        ref_dir = ref_dir_with_artifacts  # fixture provides bundles/ with chunks
        (ref_dir / "detected-breakpoints.json").write_text(
            json.dumps({"breakpoints": [768, 1024, 1440]})
        )
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": ref_dir.name,
                    "completed_steps": ["reference"],
                    "current_gate": "extraction",
                }
            )
        )
        root = self._fake_plugin_root(tmp_path)
        bundle_extraction = root / "scripts" / "extract" / "bundle-extraction.sh"
        bundle_extraction.write_text("#!/usr/bin/env bash\nexit 7\n", encoding="utf-8")
        monkeypatch.setenv("PLUGIN_ROOT", str(root))

        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.execute_phases(("2",))

        assert result != 0  # gate_bundle now fails on completed=false (was non-fatal under N3)
        status = _load_json_safe(ref_dir / "bundle-extraction-status.json")
        assert status is not None
        assert status["completed"] is False
        assert "Phase-5d" in status["advisory"]

    def test_execute_phase_2_bundle_unresolved_exit0_stays_green(
        self,
        tmp_path: Path,
        ref_dir_with_artifacts: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Net-positive proof for the bundle fail-closed change: a deterministic
        parser that SUCCEEDS but leaves some params unresolved (the common
        minified-bundle case) exits 0, so execute.py writes NO status artifact,
        gate_bundle passes, and Phase 2 stays non-fatal. Only a true producer
        crash (non-zero exit) is fail-closed — minified sites do not regress onto
        the Phase-5d LLM path."""
        ref_dir = ref_dir_with_artifacts  # fixture provides bundles/ with chunks
        (ref_dir / "detected-breakpoints.json").write_text(
            json.dumps({"breakpoints": [768, 1024, 1440]})
        )
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": ref_dir.name,
                    "completed_steps": ["reference"],
                    "current_gate": "extraction",
                }
            )
        )
        root = self._fake_plugin_root(tmp_path)
        bundle_extraction = root / "scripts" / "extract" / "bundle-extraction.sh"
        # Simulate a successful parse that left libraries unresolved (exit 0).
        bundle_extraction.write_text(
            "#!/usr/bin/env bash\n"
            'printf \'{"schemaVersion":1,"extractions":{},"unresolved":[{"library":"Swiper"}]}\' '
            '> "$1/bundle-extraction.json"\nexit 0\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("PLUGIN_ROOT", str(root))

        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.execute_phases(("2",))

        assert result == 0  # unresolved-but-clean parse is non-fatal
        assert not (ref_dir / "bundle-extraction-status.json").exists()
        state = _load_json_safe(ref_dir / "pipeline-state.json")
        assert state is not None
        assert "bundle" in state["completed_steps"]

    def test_execute_phase_2_does_not_scaffold_before_required_inputs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dom-scaffold.sh must not run until structure, styles, and section-map exist."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "regions.json").write_text(json.dumps({"regions": []}))
        root = self._fake_plugin_root(tmp_path)
        scripts = root / "skills" / "visual-debug" / "scripts"
        (scripts / "extract-dom.sh").write_text(
            "#!/usr/bin/env bash\n"
            "printf '{\"tag\":\"body\",\"children\":[]}\\n' > \"$1/structure.json\"\n",
            encoding="utf-8",
        )
        (scripts / "dom-scaffold.sh").write_text(
            "#!/usr/bin/env bash\n"
            "printf called > \"$1/dom-scaffold-called\"\n"
            "printf '{\"tree\":{\"tag\":\"body\"},\"sections\":[]}\\n' > \"$1/dom-scaffold.json\"\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("PLUGIN_ROOT", str(root))

        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.execute_phases(("2",))

        assert result == 1
        assert not (ref_dir / "dom-scaffold-called").exists()

    def test_execute_phase_2_does_not_claim_complete_when_validator_still_missing_artifacts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Phase 2 run must fail if check_phase_2 still reports missing required artifacts."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "regions.json").write_text(json.dumps({"regions": []}))
        root = self._fake_plugin_root(tmp_path)
        scripts = root / "skills" / "visual-debug" / "scripts"
        (scripts / "extract-dom.sh").write_text(
            "#!/usr/bin/env bash\n"
            "printf '{\"tag\":\"body\",\"children\":[]}\\n' > \"$1/structure.json\"\n"
            "printf '{\"sections\":[]}\\n' > \"$1/section-map.json\"\n"
            "printf '{}\\n' > \"$1/styles.json\"\n",
            encoding="utf-8",
        )
        (scripts / "dom-scaffold.sh").write_text(
            "#!/usr/bin/env bash\n"
            "printf '{\"tree\":{\"tag\":\"body\"},\"sections\":[]}\\n' > \"$1/dom-scaffold.json\"\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("PLUGIN_ROOT", str(root))

        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.execute_phases(("2",))

        assert result == 1
        assert p.next_phase == "2"

    def test_execute_phase_2_runs_asset_metadata_extraction_before_validator(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Phase 2 run must execute Step 2.5 so canonical asset artifacts exist.

        Loop 04 captured DOM/style/scaffold artifacts but then reached the
        extraction gate with missing head.json, fonts.json, visible-images.json,
        and css/variables.txt. The run driver must invoke the deterministic
        asset metadata primitive before validating Phase 2 instead of leaving
        those files as manual follow-up work.
        """
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "regions.json").write_text(json.dumps({"regions": []}))
        root = self._fake_plugin_root(tmp_path)
        scripts = root / "skills" / "visual-debug" / "scripts"
        (scripts / "extract-dom.sh").write_text(
            "#!/usr/bin/env bash\n"
            "printf '{\"tag\":\"body\",\"children\":[]}\\n' > \"$1/structure.json\"\n",
            encoding="utf-8",
        )
        (scripts / "extract-section-map.sh").write_text(
            "#!/usr/bin/env bash\n"
            "printf '{\"sections\":[]}\\n' > \"$1/section-map.json\"\n",
            encoding="utf-8",
        )
        (scripts / "extract-styles.sh").write_text(
            "#!/usr/bin/env bash\n"
            "printf '{}\\n' > \"$1/styles.json\"\n",
            encoding="utf-8",
        )
        (scripts / "dom-scaffold.sh").write_text(
            "#!/usr/bin/env bash\n"
            "printf '{\"tree\":{\"tag\":\"body\"},\"sections\":[]}\\n' > \"$1/dom-scaffold.json\"\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("PLUGIN_ROOT", str(root))

        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.execute_phases(("2",))

        assert result == 1
        assert (ref_dir / "asset-metadata-called").is_file()
        assert (ref_dir / "head.json").is_file()
        assert (ref_dir / "fonts.json").is_file()
        assert (ref_dir / "visible-images.json").is_file()
        assert (ref_dir / "css" / "variables.txt").is_file()
        assert p.next_phase == "2"

    def test_execute_phase_2_runs_resource_mirror_when_available(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Phase 2 run should capture browser-observed resources when the helper exists."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "regions.json").write_text(json.dumps({"regions": []}))
        root = self._fake_plugin_root(tmp_path)
        resource_mirror = root / "scripts" / "extract" / "resource-mirror.sh"
        resource_mirror.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "ref=\"$2\"\n"
            "printf called > \"$ref/resource-mirror-called\"\n"
            "printf '{\"schemaVersion\":1,\"resources\":[]}\\n' > \"$ref/resource-manifest.json\"\n",
            encoding="utf-8",
        )
        scripts = root / "skills" / "visual-debug" / "scripts"
        (scripts / "extract-dom.sh").write_text(
            "#!/usr/bin/env bash\n"
            "printf '{\"tag\":\"body\",\"children\":[]}\\n' > \"$1/structure.json\"\n",
            encoding="utf-8",
        )
        (scripts / "extract-section-map.sh").write_text(
            "#!/usr/bin/env bash\n"
            "printf '{\"sections\":[]}\\n' > \"$1/section-map.json\"\n",
            encoding="utf-8",
        )
        (scripts / "extract-styles.sh").write_text(
            "#!/usr/bin/env bash\n"
            "printf '{}\\n' > \"$1/styles.json\"\n",
            encoding="utf-8",
        )
        (scripts / "dom-scaffold.sh").write_text(
            "#!/usr/bin/env bash\n"
            "printf '{\"tree\":{\"tag\":\"body\"},\"sections\":[]}\\n' > \"$1/dom-scaffold.json\"\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("PLUGIN_ROOT", str(root))

        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.execute_phases(("2",))

        assert result == 1
        assert (ref_dir / "resource-mirror-called").is_file()
        assert (ref_dir / "resource-manifest.json").is_file()
        assert p.next_phase == "2"

    def test_execute_phase_2_resource_mirror_failure_is_advisory_by_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "regions.json").write_text(json.dumps({"regions": []}))
        root = self._fake_plugin_root(tmp_path)
        resource_mirror = root / "scripts" / "extract" / "resource-mirror.sh"
        resource_mirror.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
        scripts = root / "skills" / "visual-debug" / "scripts"
        (scripts / "extract-dom.sh").write_text(
            "#!/usr/bin/env bash\nprintf '{\"tag\":\"body\",\"children\":[]}\\n' > \"$1/structure.json\"\n",
            encoding="utf-8",
        )
        (scripts / "extract-section-map.sh").write_text(
            "#!/usr/bin/env bash\nprintf '{\"sections\":[]}\\n' > \"$1/section-map.json\"\n",
            encoding="utf-8",
        )
        (scripts / "extract-styles.sh").write_text(
            "#!/usr/bin/env bash\nprintf '{}\\n' > \"$1/styles.json\"\n",
            encoding="utf-8",
        )
        (scripts / "dom-scaffold.sh").write_text(
            "#!/usr/bin/env bash\n"
            "printf called > \"$1/dom-scaffold-called\"\n"
            "printf '{\"tree\":{\"tag\":\"body\"},\"sections\":[]}\\n' > \"$1/dom-scaffold.json\"\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("PLUGIN_ROOT", str(root))

        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.execute_phases(("2",))

        assert result == 1
        assert (ref_dir / "dom-scaffold-called").is_file()

    def test_execute_phase_2_resource_mirror_required_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "regions.json").write_text(json.dumps({"regions": []}))
        root = self._fake_plugin_root(tmp_path)
        resource_mirror = root / "scripts" / "extract" / "resource-mirror.sh"
        resource_mirror.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
        scripts = root / "skills" / "visual-debug" / "scripts"
        (scripts / "extract-dom.sh").write_text(
            "#!/usr/bin/env bash\nprintf '{\"tag\":\"body\",\"children\":[]}\\n' > \"$1/structure.json\"\n",
            encoding="utf-8",
        )
        (scripts / "extract-section-map.sh").write_text(
            "#!/usr/bin/env bash\nprintf '{\"sections\":[]}\\n' > \"$1/section-map.json\"\n",
            encoding="utf-8",
        )
        (scripts / "extract-styles.sh").write_text(
            "#!/usr/bin/env bash\nprintf '{}\\n' > \"$1/styles.json\"\n",
            encoding="utf-8",
        )
        (scripts / "dom-scaffold.sh").write_text(
            "#!/usr/bin/env bash\nprintf called > \"$1/dom-scaffold-called\"\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("PLUGIN_ROOT", str(root))
        monkeypatch.setenv("UI_CLONE_RESOURCE_MIRROR_REQUIRED", "1")

        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.execute_phases(("2",))

        assert result == 1
        assert not (ref_dir / "dom-scaffold-called").exists()


class TestCheckPhase2:
    """Unit tests for Pipeline.check_phase_2 — the extraction phase."""

    def _make_pipeline(self, tmp_path: Path, ref_dir: Path) -> Any:
        with patch("ui_clone.pipeline.find_project_root", return_value=tmp_path):
            p = Pipeline("https://example.com", ref_dir.name, "sess")
            p.project_root = tmp_path
            p.ref_dir = ref_dir
        return p

    def test_skipped_when_no_ref(self, tmp_path: Path) -> None:
        """check_phase_2(has_ref=False) → skipped."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_2(has_ref=False)
        assert result.skipped
        assert result.skip_reason

    def test_empty_ref_dir_sets_next_phase_2(self, tmp_path: Path) -> None:
        """has_ref=True but no artifacts → next_phase=2."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        p = self._make_pipeline(tmp_path, ref_dir)
        p.check_phase_2(has_ref=True)
        assert p.next_phase == "2"

    def _add_breakpoints(self, ref_dir: Path) -> None:
        """Add detected-breakpoints.json — missing from ref_dir_with_artifacts fixture."""
        (ref_dir / "detected-breakpoints.json").write_text(
            json.dumps({"breakpoints": [768, 1024, 1440]})
        )

    def test_all_extraction_artifacts_present(self, tmp_path: Path, ref_dir_with_artifacts: Path) -> None:
        """All extraction artifacts present → no next_phase set to 2."""
        self._add_breakpoints(ref_dir_with_artifacts)
        p = self._make_pipeline(tmp_path, ref_dir_with_artifacts)
        p.check_phase_2(has_ref=True)
        assert p.next_phase != "2"

    def test_check_phase_2_finalizes_missing_design_bundles(
        self, tmp_path: Path, ref_dir_with_artifacts: Path
    ) -> None:
        """Phase 2 status should share the extraction gate's sentinel finalizer."""
        self._add_breakpoints(ref_dir_with_artifacts)
        (ref_dir_with_artifacts / "design-bundles.json").unlink()
        p = self._make_pipeline(tmp_path, ref_dir_with_artifacts)

        p.check_phase_2(has_ref=True)

        assert (ref_dir_with_artifacts / "design-bundles.json").is_file()
        assert p.next_phase != "2"

    def test_missing_structure_json_sets_next(self, tmp_path: Path, ref_dir_with_artifacts: Path) -> None:
        """Missing structure.json → next_phase=2 with dom-extraction hint."""
        self._add_breakpoints(ref_dir_with_artifacts)
        (ref_dir_with_artifacts / "structure.json").unlink()
        p = self._make_pipeline(tmp_path, ref_dir_with_artifacts)
        p.check_phase_2(has_ref=True)
        assert p.next_phase == "2"
        assert "dom-extraction" in p.next_step.lower() or "structure" in p.next_step.lower()

    def test_missing_bundles_sets_next(self, tmp_path: Path, ref_dir_with_artifacts: Path) -> None:
        """Missing bundles/ directory → next_phase=2 with bundle hint (first-incomplete wins)."""
        import shutil

        self._add_breakpoints(ref_dir_with_artifacts)
        shutil.rmtree(ref_dir_with_artifacts / "bundles")
        p = self._make_pipeline(tmp_path, ref_dir_with_artifacts)
        p.check_phase_2(has_ref=True)
        assert p.next_phase == "2"
        assert "bundle" in p.next_step.lower()

    def test_missing_transition_spec_sets_next(self, tmp_path: Path, ref_dir_with_artifacts: Path) -> None:
        """Missing transition-spec.json → next_phase=2."""
        self._add_breakpoints(ref_dir_with_artifacts)
        (ref_dir_with_artifacts / "transition-spec.json").unlink()
        p = self._make_pipeline(tmp_path, ref_dir_with_artifacts)
        p.check_phase_2(has_ref=True)
        assert p.next_phase == "2"
        assert "transition-spec" in p.next_step.lower() or "bundle" in p.next_step.lower()

    def test_missing_extracted_json_sets_next(self, tmp_path: Path, ref_dir_with_artifacts: Path) -> None:
        """Missing extracted.json → next_phase=2 with assemble hint."""
        self._add_breakpoints(ref_dir_with_artifacts)
        (ref_dir_with_artifacts / "extracted.json").unlink()
        p = self._make_pipeline(tmp_path, ref_dir_with_artifacts)
        p.check_phase_2(has_ref=True)
        assert p.next_phase == "2"
        assert "assemble" in p.next_step.lower() or "extracted" in p.next_step.lower()

    def test_missing_component_map_sets_next(self, tmp_path: Path, ref_dir_with_artifacts: Path) -> None:
        """Missing component-map.json → next_phase=2 with section-audit hint."""
        self._add_breakpoints(ref_dir_with_artifacts)
        (ref_dir_with_artifacts / "component-map.json").unlink()
        p = self._make_pipeline(tmp_path, ref_dir_with_artifacts)
        p.check_phase_2(has_ref=True)
        assert p.next_phase == "2"
        assert "audit" in p.next_step.lower() or "component-map" in p.next_step.lower()

    def test_stale_extracted_json_warns(self, tmp_path: Path, ref_dir_with_artifacts: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """extracted.json older than its parent → staleness warning printed."""
        import os

        self._add_breakpoints(ref_dir_with_artifacts)
        now = time.time()
        os.utime(ref_dir_with_artifacts / "structure.json", (now, now))
        os.utime(ref_dir_with_artifacts / "extracted.json", (now - 5, now - 5))

        p = self._make_pipeline(tmp_path, ref_dir_with_artifacts)
        p.check_phase_2(has_ref=True)
        captured = capsys.readouterr()
        assert "STALE" in captured.out

    def test_few_js_chunks_warns(self, tmp_path: Path, ref_dir_with_artifacts: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Only 1-2 JS chunks → advisory warning printed."""
        import shutil

        self._add_breakpoints(ref_dir_with_artifacts)
        shutil.rmtree(ref_dir_with_artifacts / "bundles")
        bundles = ref_dir_with_artifacts / "bundles"
        bundles.mkdir()
        (bundles / "chunk-0.js").write_text("// single chunk")

        p = self._make_pipeline(tmp_path, ref_dir_with_artifacts)
        p.check_phase_2(has_ref=True)
        captured = capsys.readouterr()
        assert "1 JS chunk" in captured.out or "Only 1" in captured.out

    def test_missing_responsive_sizing_sets_next(self, tmp_path: Path, ref_dir_with_artifacts: Path) -> None:
        """Missing responsive/sizing-expressions.json → next_phase=2."""
        self._add_breakpoints(ref_dir_with_artifacts)
        (ref_dir_with_artifacts / "responsive" / "sizing-expressions.json").unlink()
        p = self._make_pipeline(tmp_path, ref_dir_with_artifacts)
        p.check_phase_2(has_ref=True)
        assert p.next_phase == "2"
        assert "sizing" in p.next_step.lower() or "responsive" in p.next_step.lower()


class TestDagDepsCoverage:
    """Verify that all artifacts in DEPS are checked by at least one gate."""

    def test_deps_artifacts_referenced_in_gates(self) -> None:
        """Every DEPS artifact must be directly or transitively gate-owned."""
        from ui_clone import dag
        from ui_clone.gate import Gate

        # Collect all artifact names from DEPS
        all_artifacts = set(dag.DEPS.keys())
        for targets in dag.DEPS.values():
            all_artifacts.update(targets)

        # Walk Gate's bound methods (post-Item-5 refactor: methods live in
        # ui_clone/gates/*.py and are rebound onto Gate via __init__.py, so
        # `inspect.getsource(Gate)` returns only the base class body —
        # walking methods explicitly captures the per-module sources too).
        import inspect

        sources: list[str] = [inspect.getsource(Gate)]
        for name in dir(Gate):
            if not (name.startswith("gate_") or name.startswith("_check_")):
                continue
            attr = getattr(Gate, name, None)
            if attr is None or not callable(attr):
                continue
            try:
                sources.append(inspect.getsource(attr))
            except (OSError, TypeError):
                continue
        source = "\n".join(sources)

        # Generation-plan inputs are intentionally consumed through one
        # provenance boundary instead of being repeated in Gate source. Keep
        # that indirect ownership explicit: the source must be registered,
        # must invalidate generation-plan.json, and the gate must validate the
        # generation-plan provenance receipt.
        assert "generation_plan_provenance_issues" in source
        generation_plan_sources = {
            artifact
            for artifact in dag.GENERATION_PLAN_SOURCES
            if "*" not in artifact
            and "generation-plan.json" in dag.DEPS.get(artifact, ())
        }
        missing = sorted(
            artifact
            for artifact in all_artifacts
            if artifact not in source and artifact not in generation_plan_sources
        )

        assert not missing, f"DEPS artifacts not referenced in Gate: {missing}"
