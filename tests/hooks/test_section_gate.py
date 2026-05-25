from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from ._helpers import (
    make_ref_dir,
    make_search_root,
    run_hook,
    set_active_marker,
)


class TestSectionGate:
    MODULE = "ui_clone.hooks.section_gate"

    def test_no_tmp_ref_exits_0(self, tmp_path: Path) -> None:
        """No tmp/ref/ directory → exit 0."""
        result = run_hook(
            self.MODULE,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0

    def test_no_wip_marker_exits_0(self, tmp_path: Path) -> None:
        """tmp/ref exists but no .ui-re-active marker → exit 0."""
        make_search_root(tmp_path)
        result = run_hook(
            self.MODULE,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0

    def test_wip_marker_no_result_txt_outputs_block(self, tmp_path: Path) -> None:
        """WIP marker present, no result.txt → block JSON."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        # No sections dir, no result.txt

        result = run_hook(
            self.MODULE,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"Expected block JSON, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        assert data.get("decision") == "block"
        assert "reason" in data

    def test_wip_marker_result_txt_no_failures_exits_0(self, tmp_path: Path) -> None:
        """WIP marker + pipeline-state at section-compare + result.txt with only ✅ → exit 0."""
        import json as _json

        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)

        # pipeline-state.json must be present with current_gate=section-compare
        (ref_dir / "pipeline-state.json").write_text(
            _json.dumps(
                {
                    "component": ref_dir.name,
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [
                        "reference",
                        "extraction",
                        "bundle",
                        "paid-features",
                        "spec",
                        "pre-generate",
                        "state-coverage",
                        "post-implement",
                        "boundary",
                        "font-parity",
                    ],
                    "current_gate": "section-compare",
                    "last_updated": "2026-01-01T01:00:00Z",
                }
            )
        )

        sections_dir = ref_dir / "sections"
        sections_dir.mkdir()
        (sections_dir / "result.txt").write_text(
            "| Hero | ✅ | 95% |\n| Footer | ✅ | 98% |\n",
            encoding="utf-8",
        )

        result = run_hook(
            self.MODULE,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_wip_marker_result_txt_has_failures_outputs_block(self, tmp_path: Path) -> None:
        """WIP marker + pipeline-state at section-compare + result.txt with ❌ → block JSON."""
        import json as _json

        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)

        # Must set current_gate=section-compare so section-compare branch is entered
        (ref_dir / "pipeline-state.json").write_text(
            _json.dumps(
                {
                    "component": ref_dir.name,
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [
                        "reference",
                        "extraction",
                        "bundle",
                        "paid-features",
                        "spec",
                        "pre-generate",
                        "state-coverage",
                        "post-implement",
                        "boundary",
                        "font-parity",
                    ],
                    "current_gate": "section-compare",
                    "last_updated": "2026-01-01T01:00:00Z",
                }
            )
        )

        sections_dir = ref_dir / "sections"
        sections_dir.mkdir()
        (sections_dir / "result.txt").write_text(
            "| Hero | ❌ | 60% |\n| Footer | ✅ | 98% |\n",
            encoding="utf-8",
        )

        result = run_hook(
            self.MODULE,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out
        data = json.loads(out)
        assert data.get("decision") == "block"
        assert "FAILED" in data["reason"] or "section-compare" in data["reason"].lower()

    def test_wip_marker_result_txt_has_missing_outputs_block(self, tmp_path: Path) -> None:
        """WIP marker + pipeline-state at section-compare + result.txt with ⚠️ MISSING impl → block JSON."""
        import json as _json

        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)

        # Must set current_gate=section-compare so section-compare branch is entered
        (ref_dir / "pipeline-state.json").write_text(
            _json.dumps(
                {
                    "component": ref_dir.name,
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [
                        "reference",
                        "extraction",
                        "bundle",
                        "paid-features",
                        "spec",
                        "pre-generate",
                        "state-coverage",
                        "post-implement",
                        "boundary",
                        "font-parity",
                    ],
                    "current_gate": "section-compare",
                    "last_updated": "2026-01-01T01:00:00Z",
                }
            )
        )

        sections_dir = ref_dir / "sections"
        sections_dir.mkdir()
        (sections_dir / "result.txt").write_text(
            "| Hero | ✅ | 95% |\n| Nav | ⚠️ MISSING impl |\n",
            encoding="utf-8",
        )

        result = run_hook(
            self.MODULE,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out
        data = json.loads(out)
        assert data.get("decision") == "block"

    def test_stale_marker_auto_removed_exits_0(self, tmp_path: Path) -> None:
        """Stale marker (>3 days) → auto-removed → exit 0."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        three_days_plus = 3 * 24 * 3600 + 60  # 3 days + 1 min
        marker = set_active_marker(ref_dir, age_seconds=three_days_plus)

        result = run_hook(
            self.MODULE,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        # Marker should be gone
        assert not marker.exists(), "Stale marker should have been removed"

    def test_stale_days_env_override_keeps_marker_alive(self, tmp_path: Path) -> None:
        """UI_RE_STALE_DAYS env var overrides the 3-day default."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        # Make marker 4 days old — would be stale with default 3 days
        four_days = 4 * 24 * 3600 + 60
        marker = set_active_marker(ref_dir, age_seconds=four_days)
        # With UI_RE_STALE_DAYS=5, 4-day marker is still active → should block (no result.txt)
        result = run_hook(
            self.MODULE,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path), "UI_RE_STALE_DAYS": "5"},
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, "4-day marker should still be active with 5-day threshold"
        data = json.loads(out)
        assert data.get("decision") == "block"
        assert marker.exists(), "Marker must not be removed when within custom threshold"

    def test_multiple_active_sessions_enforces_later_refs(self, tmp_path: Path) -> None:
        """Multiple WIP markers → later dirty refs still block Stop."""
        search_root = make_search_root(tmp_path)
        ref1 = make_ref_dir(search_root, "session-a")
        ref2 = make_ref_dir(search_root, "session-b")
        set_active_marker(ref1)
        set_active_marker(ref2)
        completed = [
            "reference",
            "extraction",
            "bundle",
            "paid-features",
            "spec",
            "pre-generate",
            "state-coverage",
            "post-implement",
            "boundary",
            "font-parity",
            "section-compare",
        ]
        for ref_dir in (ref1, ref2):
            (ref_dir / "pipeline-state.json").write_text(
                json.dumps(
                    {
                        "component": ref_dir.name,
                        "started_at": "2026-01-01T00:00:00Z",
                        "completed_steps": completed,
                        "current_gate": "done",
                        "last_updated": "2026-01-01T01:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
        sections = ref1 / "sections"
        sections.mkdir()
        (sections / "result.txt").write_text("| hero | ✅ PASS | ... |\n", encoding="utf-8")

        result = run_hook(
            self.MODULE,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out
        data = json.loads(out)
        assert data.get("decision") == "block"
        assert "session-b" in data.get("reason", "")



class TestSectionGateFullEnforcement:
    """Verifies that section_gate.py runs the gate matching current_gate."""

    def _run_gate_hook(self, ref_dir: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int | str, str]:
        """Invoke section_gate main() directly. Returns (exit_code, stdout)."""
        import importlib
        import io
        from unittest.mock import patch

        captured = io.StringIO()
        exit_code: int | str = 0
        try:
            with patch("sys.stdout", captured):
                from ui_clone.hooks import section_gate

                importlib.reload(section_gate)
                section_gate.main()
        except SystemExit as e:
            exit_code = e.code or 0
        return exit_code, captured.getvalue()

    def test_no_active_marker_allows(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """No WIP marker → always allow."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        assert exit_code == 0
        assert "block" not in output.lower()

    def test_extraction_gate_blocked_when_missing_artifacts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """current_gate=extraction with missing artifacts → block."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / ".ui-re-active").touch()
        # Write state with current_gate=extraction
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": ["reference"],
                    "current_gate": "extraction",
                    "last_updated": "2026-01-01T01:00:00Z",
                }
            )
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        # Should block (extraction gate fails — no artifacts)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert data.get("decision") == "block"
        assert "extraction" in data.get("reason", "").lower()

    def test_section_compare_pass_when_result_all_pass(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """current_gate=section-compare and result.txt all PASS → allow and record state as done."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / ".ui-re-active").touch()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [
                        "reference",
                        "extraction",
                        "bundle",
                        "paid-features",
                        "spec",
                        "pre-generate",
                        "state-coverage",
                        "post-implement",
                        "boundary",
                        "font-parity",
                    ],
                    "current_gate": "section-compare",
                    "last_updated": "2026-01-01T01:00:00Z",
                }
            )
        )
        sections = ref_dir / "sections"
        sections.mkdir()
        (sections / "result.txt").write_text("| hero | ✅ PASS | ... |\n| footer | ✅ PASS | ... |")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        assert exit_code == 0
        assert "block" not in output.lower()
        # state should be recorded as done
        from ui_clone.state import PipelineState

        state = PipelineState.load(ref_dir)
        assert state.current_gate == "done"
        assert "section-compare" in state.completed_steps
        # Marker must PERSIST after section-compare passes — pre_generate uses
        # marker presence + state==done to detect post-done edits and demote
        # state back to section-compare. Removing the marker here would let
        # post-completion edits ship unverified.
        assert (ref_dir / ".ui-re-active").exists(), (
            "Marker must persist after section-compare passes (closes the "
            "post-done-edit drift hole; stale-marker guard cleans up after 3 days)"
        )

    def test_arbitrary_synthetic_gate_on_fresh_state_does_not_release(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Universality: any synthetic gate name (NOT in GATE_ORDER) on a
        fresh state must NOT release Stop. Codex review universalised
        the discriminator from the literal "session-cleanup" to
        "gate not in GATE_ORDER" so future renames (forensic-preserve,
        loop-archive, etc.) don't silently degrade.
        """
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / ".ui-re-active").touch()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [],
                    "current_gate": "reference",
                    "last_updated": "2026-01-01T01:00:00Z",
                    "gate_fail_counts": {},
                    "unclonable_reasons": [
                        {
                            "gate": "forensic-preserve",  # hypothetical rename
                            "reason": "preserved from prior loop",
                        }
                    ],
                }
            )
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        if exit_code == 0:
            assert "block" in output.lower() or "decision" in output.lower(), (
                f"synthetic-gate marker + completed_steps==[] must not "
                f"silently release Stop, got exit=0 output={output[:200]!r}"
            )

    def test_session_cleanup_on_fresh_state_does_not_release(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Loop-23 slip path: forensic `session-cleanup` marker on a fresh
        state (completed_steps == []) must NOT release Stop. Codex-23
        inherited such a marker from a prior loop and exited "done"
        without exercising a single gate — the universal regression this
        test locks down.
        """
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / ".ui-re-active").touch()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [],
                    "current_gate": "reference",
                    "last_updated": "2026-01-01T01:00:00Z",
                    "gate_fail_counts": {},
                    "unclonable_reasons": [
                        {
                            "gate": "session-cleanup",
                            "reason": "preserved as forensic state from prior loop",
                        }
                    ],
                }
            )
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        # Stop must NOT release silently — gate enforcement should fire.
        # Allow either a block (decision message) or any non-quiet output,
        # but the legacy "free pass via unclonable_reasons" path is closed.
        if exit_code == 0:
            # exit 0 acceptable only when output explicitly blocks
            assert "block" in output.lower() or "decision" in output.lower(), (
                f"session-cleanup + completed_steps==[] must not silently "
                f"release Stop, got exit=0 output={output[:200]!r}"
            )

    def test_unclonable_reasons_releases_stop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Loop-41 finding: pipeline-state.json unclonable_reasons must release
        Stop, symmetric to `python -m ui_clone.goal --check-done` exit 2.
        Without this short-circuit, Stop fires every turn while the goal harness
        signals ABORT — external loops cannot terminate cleanly.
        """
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / ".ui-re-active").touch()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": ["reference", "extraction"],
                    "current_gate": "section-compare",
                    "last_updated": "2026-01-01T01:00:00Z",
                    "gate_fail_counts": {"section-compare": 10},
                    "unclonable_reasons": [
                        {"gate": "section-compare", "reason": "hard-cap reached"}
                    ],
                }
            )
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        assert exit_code == 0
        assert "block" not in output.lower()

    def test_section_compare_blocks_when_result_txt_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """current_gate=section-compare with no result.txt → block, even if diff PNGs exist."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / ".ui-re-active").touch()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [
                        "reference",
                        "extraction",
                        "bundle",
                        "paid-features",
                        "spec",
                        "pre-generate",
                        "state-coverage",
                        "post-implement",
                        "boundary",
                        "font-parity",
                    ],
                    "current_gate": "section-compare",
                    "last_updated": "2026-01-01T01:00:00Z",
                }
            )
        )
        # Create sections/diff/ with PNG files but NO result.txt
        diff_dir = ref_dir / "sections" / "diff"
        diff_dir.mkdir(parents=True)
        (diff_dir / "hero.png").write_bytes(b"\x89PNG" + b"\x00" * 20)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert data.get("decision") == "block", "diff PNGs without result.txt must still block"
        assert "result.txt" in data.get("reason", "").lower()
        assert "Goal Card: comp" in data.get("reason", "")
        assert f"python -m ui_clone.goal {ref_dir}" in data.get("reason", "")

    def test_no_pipeline_state_enforces_reference_gate(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """No pipeline-state.json → enforce reference gate (Bug #2 fix)."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / ".ui-re-active").touch()
        # No pipeline-state.json — fresh start
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        # reference gate should fire and block (no static/ref screenshots)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert data.get("decision") == "block"
        assert "reference" in data.get("reason", "").lower()
        assert "Goal Card: comp" in data.get("reason", "")
        assert f"python -m ui_clone.goal {ref_dir}" in data.get("reason", "")

    def _write_done_state(self, ref_dir: Path) -> None:
        ref_dir.mkdir(parents=True, exist_ok=True)
        (ref_dir / ".ui-re-active").touch()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [
                        "reference",
                        "extraction",
                        "bundle",
                        "paid-features",
                        "spec",
                        "pre-generate",
                        "state-coverage",
                        "post-implement",
                        "boundary",
                        "font-parity",
                        "section-compare",
                    ],
                    "current_gate": "done",
                    "last_updated": "2026-01-01T02:00:00Z",
                }
            ),
            encoding="utf-8",
        )

    def test_done_state_blocks_when_section_result_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """current_gate=done with missing sections/result.txt → block."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_done_state(ref_dir)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        assert "sections/result.txt" in data.get("reason", "")

    def test_done_state_blocks_when_section_result_dirty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """current_gate=done with dirty sections/result.txt → block."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_done_state(ref_dir)
        sections = ref_dir / "sections"
        sections.mkdir()
        (sections / "result.txt").write_text("| hero | 125 | high | ❌ |\n", encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        assert "section-compare" in data.get("reason", "")

    def test_done_state_allows_when_section_result_clean(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """current_gate=done with clean sections/result.txt → allow."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_done_state(ref_dir)
        sections = ref_dir / "sections"
        sections.mkdir()
        (sections / "result.txt").write_text("| hero | ✅ PASS | ... |\n", encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        assert exit_code == 0
        assert "block" not in output.lower()

    def test_impl_done_state_blocks_without_verify_stamp(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """impl/ exists → clean sections alone is not enough; pipeline verify must stamp."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_done_state(ref_dir)
        sections = ref_dir / "sections"
        sections.mkdir()
        (sections / "result.txt").write_text("| hero | ✅ PASS | ... |\n", encoding="utf-8")
        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text("export default function App(){return <main />}", encoding="utf-8")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "no verify-stamp.json" in reason
        assert "Build success" in reason
        assert "spot checks" in reason

    def test_verify_stamp_blocks_when_impl_changed_after_verify(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Loop feedback: a fresh stamp must not release Stop after later JSX/CSS/asset edits."""
        import datetime

        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_done_state(ref_dir)
        sections = ref_dir / "sections"
        sections.mkdir()
        (sections / "result.txt").write_text("| hero | ✅ PASS | ... |\n", encoding="utf-8")

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        app = src / "App.tsx"
        app.write_text("export default function App(){return <main />}", encoding="utf-8")

        stamp = ref_dir / "verify-stamp.json"
        stamp.write_text(
            json.dumps(
                {
                    "verifiedAt": datetime.datetime.now(datetime.UTC).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    "gatesPassed": [
                        "spec",
                        "post-implement",
                        "boundary",
                        "font-parity",
                        "section-compare",
                    ],
                    "stampedBy": "pipeline.execute_verify",
                }
            ),
            encoding="utf-8",
        )
        now = time.time()
        os.utime(stamp, (now - 10, now - 10))
        os.utime(app, (now, now))

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "impl changed after verify" in reason
        assert "App.tsx" in reason

    def test_verify_stamp_blocks_when_not_canonical_pipeline_stamp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hand-written/fake stamp must not release Stop."""
        import datetime

        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_done_state(ref_dir)
        sections = ref_dir / "sections"
        sections.mkdir()
        (sections / "result.txt").write_text("| hero | ✅ PASS | ... |\n", encoding="utf-8")

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        app = src / "App.tsx"
        app.write_text("export default function App(){return <main />}", encoding="utf-8")

        stamp = ref_dir / "verify-stamp.json"
        stamp.write_text(
            json.dumps(
                {
                    "verifiedAt": datetime.datetime.now(datetime.UTC).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    "gatesPassed": ["post-implement"],
                    "stampedBy": "manual",
                }
            ),
            encoding="utf-8",
        )
        now = time.time()
        os.utime(app, (now - 10, now - 10))
        os.utime(stamp, (now, now))

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "canonical" in reason
        assert "pipeline.execute_verify" in reason

    def test_unknown_gate_fails_closed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """current_gate with unknown value → fail-closed (block)."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / ".ui-re-active").touch()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [],
                    "current_gate": "nonexistent-gate-name",
                    "last_updated": "2026-01-01T00:00:00Z",
                }
            )
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        assert "unknown current_gate" in data.get("reason", "")
        assert "python -m ui_clone.gate" not in data.get("reason", "")



class TestSectionGateStateVerification:
    """Verify that section_gate only removes the WIP marker when state was persisted."""

    def test_marker_preserved_when_state_not_persisted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the subprocess gate fails to write state, .ui-re-active must be preserved.

        The hook reloads pipeline-state.json after _run_gate and only removes the
        marker if 'section-compare' is in completed_steps. If the gate subprocess
        failed to persist (e.g. read-only filesystem), the marker stays.
        """
        import importlib
        import io
        from unittest.mock import patch

        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        marker = ref_dir / ".ui-re-active"
        marker.touch()

        # Set up pipeline-state at section-compare gate
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [
                        "reference",
                        "extraction",
                        "bundle",
                        "paid-features",
                        "spec",
                        "pre-generate",
                        "state-coverage",
                        "post-implement",
                        "boundary",
                        "font-parity",
                    ],
                    "current_gate": "section-compare",
                    "last_updated": "2026-01-01T01:00:00Z",
                }
            )
        )

        # Passing result.txt so section-compare check itself succeeds
        sections = ref_dir / "sections"
        sections.mkdir()
        (sections / "result.txt").write_text("| hero | ✅ PASS | 99% |\n")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        # Mock run_gate at the source (_common) to return pass but NOT actually
        # write pipeline-state.json. This simulates the subprocess failing to persist.
        def fake_run_gate(rd: Path, gate_name: str) -> dict:
            return {"passed": True, "fail_count": 0, "failures": []}

        captured = io.StringIO()
        exit_code: int | str = 0
        try:
            with patch("sys.stdout", captured):
                # Reload the module first, then patch the bound name
                import ui_clone.hooks.section_gate as mod

                importlib.reload(mod)
                with patch.object(mod, "_run_gate", fake_run_gate):
                    mod.main()
        except SystemExit as e:
            exit_code = e.code or 0

        # Hook must exit 0 (not block the LLM)
        assert exit_code == 0

        # CRITICAL: marker must still exist — _run_gate returned pass but did NOT
        # write section-compare to completed_steps, so the hook's reload-and-check
        # should NOT remove the marker.
        assert marker.exists(), (
            ".ui-re-active marker must NOT be removed when state was not persisted"
        )


class TestDriverSessionBypass:
    """Driver-session bypass — release Stop unconditionally when the current
    Claude Code session is registered as a loop driver for this repo.

    Production users never write the marker, so the gate works as before.
    Loop sessions spawned by the driver have their own CLAUDE_CODE_SESSION_ID
    so even if they could read the marker, no match → gate fires for them.
    """

    MODULE = "ui_clone.hooks.section_gate"

    def test_marker_matches_session_id_releases_stop(self, tmp_path: Path) -> None:
        """Marker file content matches CLAUDE_CODE_SESSION_ID → exit 0 with no block."""
        # Set up an active ref dir with impl/ but no verify-stamp — would
        # normally block. With the driver bypass active, it should not.
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="loop-claude-99")
        set_active_marker(ref_dir)
        impl_dir = tmp_path / "scratch" / "loop-claude-99" / "impl"
        impl_dir.mkdir(parents=True)
        (ref_dir / ".impl-root").write_text(str(impl_dir) + "\n")

        # Write the driver-session marker
        session_id = "driver-session-id-test-12345"
        (tmp_path / ".driver-session.id").write_text(session_id + "\n")

        result = run_hook(
            self.MODULE,
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "CLAUDE_CODE_SESSION_ID": session_id,
            },
        )
        assert result.returncode == 0
        assert not result.stdout.strip(), (
            f"driver bypass must produce no block JSON; got: {result.stdout!r}"
        )

    def test_marker_mismatch_blocks_normally(self, tmp_path: Path) -> None:
        """Marker exists but content differs from session env → gate fires as usual."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="loop-claude-99")
        set_active_marker(ref_dir)
        impl_dir = tmp_path / "scratch" / "loop-claude-99" / "impl"
        impl_dir.mkdir(parents=True)
        (ref_dir / ".impl-root").write_text(str(impl_dir) + "\n")

        (tmp_path / ".driver-session.id").write_text("DIFFERENT-session-id\n")

        result = run_hook(
            self.MODULE,
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "CLAUDE_CODE_SESSION_ID": "current-session-id",
            },
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, "non-driver session must still block"
        data = json.loads(out)
        assert data.get("decision") == "block"

    def test_no_marker_file_blocks_normally(self, tmp_path: Path) -> None:
        """No .driver-session.id file at all → gate fires as usual (production)."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="loop-claude-99")
        set_active_marker(ref_dir)
        impl_dir = tmp_path / "scratch" / "loop-claude-99" / "impl"
        impl_dir.mkdir(parents=True)
        (ref_dir / ".impl-root").write_text(str(impl_dir) + "\n")

        result = run_hook(
            self.MODULE,
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "CLAUDE_CODE_SESSION_ID": "any-session-id",
            },
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, "no driver marker → must still block"
        data = json.loads(out)
        assert data.get("decision") == "block"

    def test_empty_marker_blocks_normally(self, tmp_path: Path) -> None:
        """Empty marker file (no recorded session id) → gate fires as usual."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="loop-claude-99")
        set_active_marker(ref_dir)
        impl_dir = tmp_path / "scratch" / "loop-claude-99" / "impl"
        impl_dir.mkdir(parents=True)
        (ref_dir / ".impl-root").write_text(str(impl_dir) + "\n")

        (tmp_path / ".driver-session.id").write_text("\n")

        result = run_hook(
            self.MODULE,
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "CLAUDE_CODE_SESSION_ID": "current-session-id",
            },
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, "empty marker → must still block"

    def test_marker_matches_stdin_payload_session_id_releases_stop(self, tmp_path: Path) -> None:
        """Claude Code's canonical path: session_id arrives via stdin JSON payload."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="loop-claude-99")
        set_active_marker(ref_dir)
        impl_dir = tmp_path / "scratch" / "loop-claude-99" / "impl"
        impl_dir.mkdir(parents=True)
        (ref_dir / ".impl-root").write_text(str(impl_dir) + "\n")

        session_id = "stdin-payload-session-id-abc"
        (tmp_path / ".driver-session.id").write_text(session_id + "\n")

        payload = json.dumps({"session_id": session_id, "hook_event_name": "Stop"})
        result = run_hook(
            self.MODULE,
            stdin_data=payload,
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                # Env intentionally unset / wrong — stdin payload must win.
                "CLAUDE_CODE_SESSION_ID": "",
            },
        )
        assert result.returncode == 0
        assert not result.stdout.strip(), (
            f"stdin payload session_id match must release; got: {result.stdout!r}"
        )

    def test_marker_present_but_session_id_env_unset_blocks(self, tmp_path: Path) -> None:
        """Marker file populated but CLAUDE_CODE_SESSION_ID env empty → gate fires."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="loop-claude-99")
        set_active_marker(ref_dir)
        impl_dir = tmp_path / "scratch" / "loop-claude-99" / "impl"
        impl_dir.mkdir(parents=True)
        (ref_dir / ".impl-root").write_text(str(impl_dir) + "\n")

        (tmp_path / ".driver-session.id").write_text("some-session-id\n")

        result = run_hook(
            self.MODULE,
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "CLAUDE_CODE_SESSION_ID": "",
            },
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, "missing session env → must still block"


class TestSectionGateStructuralCloseout:
    """Structural closeout policy (Task #11) — pipeline-state.closeoutPolicy=='structural'
    routes the Stop hook to accept structural-convergence-stamp.json from
    check-converged.sh instead of demanding verify-stamp.json from
    pipeline.execute_verify. The canonical contract is untouched; this class
    only exercises the new policy branch."""

    def _run_gate_hook(self, ref_dir: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int | str, str]:
        """Mirror TestSectionGateFullEnforcement._run_gate_hook so the new
        tests can stand alone if that class is later refactored."""
        import importlib
        import io
        from unittest.mock import patch

        captured = io.StringIO()
        exit_code: int | str = 0
        try:
            with patch("sys.stdout", captured):
                from ui_clone.hooks import section_gate

                importlib.reload(section_gate)
                section_gate.main()
        except SystemExit as e:
            exit_code = e.code or 0
        return exit_code, captured.getvalue()

    def _write_structural_state(self, ref_dir: Path) -> None:
        """A ref dir that opted into structural closeout. completed_steps
        intentionally stops at section-compare via the convergence detector
        rather than the full canonical chain — this is the whole point of
        structural mode."""
        ref_dir.mkdir(parents=True, exist_ok=True)
        (ref_dir / ".ui-re-active").touch()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [
                        "reference",
                        "extraction",
                        "bundle",
                        "paid-features",
                        "spec",
                        "pre-generate",
                    ],
                    "current_gate": "section-compare",
                    "last_updated": "2026-01-01T02:00:00Z",
                    "closeoutPolicy": "structural",
                }
            ),
            encoding="utf-8",
        )

    def _write_converged_result(self, ref_dir: Path) -> Path:
        sections = ref_dir / "sections"
        sections.mkdir(exist_ok=True)
        path = sections / "result.txt"
        path.write_text(
            "| hero | 100 | 50 | ok | ✅ PASS |\n\n"
            "**Result: 3 PASS, 0 FAIL, 7 SKIP, 3 STRUCTURAL_ONLY**\n",
            encoding="utf-8",
        )
        return path

    def _write_stamp(
        self, ref_dir: Path, result_file: Path, *, stamped_by: str = "scripts/verify/check-converged.sh"
    ) -> Path:
        """Mirror check-converged.sh's stamp emission so the test exercises the
        Stop hook in isolation without invoking the bash script."""
        import datetime
        import hashlib

        sha = hashlib.sha256(result_file.read_bytes()).hexdigest()
        stamp = ref_dir / "structural-convergence-stamp.json"
        stamp.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "closeoutKind": "structural",
                    "stampedBy": stamped_by,
                    "verifiedAt": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "stage": "C",
                    "sectionResult": "**Result: 3 PASS, 0 FAIL, 7 SKIP, 3 STRUCTURAL_ONLY**",
                    "sectionsResultSha256": sha,
                }
            ),
            encoding="utf-8",
        )
        return stamp

    def test_structural_stamp_releases_stop_with_valid_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """closeoutPolicy=structural + valid stamp + clean impl → Stop allowed.
        Verifies the new policy branch lands without breaking the canonical
        path (no verify-stamp.json present — that's the whole reason this
        branch exists)."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_structural_state(ref_dir)
        result_file = self._write_converged_result(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        app = src / "App.tsx"
        app.write_text("export default function App(){return <main />}", encoding="utf-8")

        stamp = self._write_stamp(ref_dir, result_file)
        now = time.time()
        os.utime(app, (now - 10, now - 10))
        os.utime(stamp, (now, now))

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        assert exit_code == 0
        assert "block" not in output.lower(), (
            f"structural stamp must release Stop; got: {output!r}"
        )

    def test_structural_policy_blocks_when_stamp_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """structural + no stamp → block with structural-convergence-stamp.json
        reference (not verify-stamp.json — the message must match the policy)."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_structural_state(ref_dir)
        self._write_converged_result(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text("export default function App(){return null}", encoding="utf-8")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "structural-convergence-stamp.json" in reason
        assert "check-converged.sh" in reason
        # Must NOT reference verify-stamp.json or pipeline.execute_verify — the
        # plan opted out of canonical closeout.
        assert "verify-stamp.json" not in reason
        assert "pipeline.execute_verify" not in reason

    def test_structural_stamp_blocks_when_impl_changed_after_stamp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Freshness invariant must hold for structural stamps too: an agent
        cannot stamp, then edit JSX, then exit cleanly. Mirrors the canonical
        verify-stamp freshness check (loop-codex-21 bypass class)."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_structural_state(ref_dir)
        result_file = self._write_converged_result(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        app = src / "App.tsx"
        app.write_text("export default function App(){return <main />}", encoding="utf-8")

        stamp = self._write_stamp(ref_dir, result_file)
        now = time.time()
        os.utime(stamp, (now - 10, now - 10))
        os.utime(app, (now, now))

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        assert "impl changed" in data.get("reason", "").lower()
        assert "App.tsx" in data.get("reason", "")

    def test_structural_stamp_blocks_when_not_canonical_writer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stamp with stampedBy != check-converged.sh is hand-forged and must
        not release Stop (analogous to the verify-stamp anti-cheat check)."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_structural_state(ref_dir)
        result_file = self._write_converged_result(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text("export default function App(){return null}", encoding="utf-8")

        self._write_stamp(ref_dir, result_file, stamped_by="manual")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "non-canonical" in reason.lower() or "check-converged.sh" in reason

    def test_structural_stamp_blocks_when_sections_result_tampered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """sectionsResultSha256 in the stamp must match the current
        sections/result.txt. If the agent stamps with a converged result then
        edits result.txt to claim more convergence, the hash mismatch blocks.
        This is the structural equivalent of impl-freshness — the evidence
        the stamp attests to must not have moved out from under it."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_structural_state(ref_dir)
        result_file = self._write_converged_result(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text("export default function App(){return null}", encoding="utf-8")

        self._write_stamp(ref_dir, result_file)
        # Tamper with the result file AFTER stamping. Stamp's sha is now stale.
        result_file.write_text(
            "**Result: 14 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n",
            encoding="utf-8",
        )

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "tampered" in reason.lower() or "sha256" in reason.lower() or "mismatch" in reason.lower()

    def test_canonical_policy_unchanged_when_field_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression guard: state files without closeoutPolicy keep the
        existing strict canonical path. If this test ever fails, the field
        default flipped accidentally (every legacy run would suddenly accept
        structural stamps)."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        # Same as _write_structural_state but WITHOUT closeoutPolicy.
        ref_dir.mkdir(parents=True, exist_ok=True)
        (ref_dir / ".ui-re-active").touch()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [
                        "reference", "extraction", "bundle", "paid-features",
                        "spec", "pre-generate", "state-coverage",
                        "post-implement", "boundary",
                        "font-parity", "section-compare",
                    ],
                    "current_gate": "done",
                    "last_updated": "2026-01-01T02:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        result_file = self._write_converged_result(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text("export default function App(){return null}", encoding="utf-8")

        # Write only the structural stamp (no verify-stamp.json) — should still
        # block because the policy defaults to canonical.
        self._write_stamp(ref_dir, result_file)

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        # Canonical path: must demand verify-stamp.json
        assert "verify-stamp.json" in reason



class TestSectionGateCanvasReplayCloseout:
    """Canvas-replay closeout policy (v0.7.0) — pipeline-state.closeoutPolicy=='canvas-replay'
    routes the Stop hook to accept canvas-replay-stamp.json from
    scripts/verify/check-canvas-replay.sh instead of demanding verify-stamp.json
    from pipeline.execute_verify. The canonical and structural contracts are
    untouched; this class only exercises the new policy branch.

    Codex review (2026-05-25) findings applied:
      [1] No new GATE_ORDER entry — canvas-replay is a closeout policy, not a
          pipeline phase.
      [2] Attestation file is operator's explicit license confirmation; the
          stamp records sha256(attestation) for tamper detection.
      [5] Stamp records `ref_canvas_sources` URLs from attestation (audit trail
          for the canvas JS the impl loads at runtime).
      [7] Section schema: design doc says `kind: "canvas"`; section-compare
          relief in a follow-up commit will read that field.
    """

    def _run_gate_hook(
        self, ref_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[int | str, str]:
        import importlib
        import io
        from unittest.mock import patch

        captured = io.StringIO()
        exit_code: int | str = 0
        try:
            with patch("sys.stdout", captured):
                from ui_clone.hooks import section_gate

                importlib.reload(section_gate)
                section_gate.main()
        except SystemExit as e:
            exit_code = e.code or 0
        return exit_code, captured.getvalue()

    def _write_canvas_replay_state(self, ref_dir: Path) -> None:
        """A ref dir that opted into canvas-replay closeout. Distinct from
        structural — completed_steps reaches section-compare via canonical
        gates (canvas-replay does not bypass earlier gates) but the closeout
        proof is the attestation stamp, not the canonical verify-stamp."""
        ref_dir.mkdir(parents=True, exist_ok=True)
        (ref_dir / ".ui-re-active").touch()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [
                        "reference", "extraction", "bundle", "paid-features",
                        "spec", "pre-generate", "state-coverage",
                        "post-implement", "boundary", "font-parity",
                        "section-compare",
                    ],
                    "current_gate": "done",
                    "last_updated": "2026-01-01T02:00:00Z",
                    "closeoutPolicy": "canvas-replay",
                }
            ),
            encoding="utf-8",
        )

    def _write_attestation(self, ref_dir: Path) -> Path:
        attestation = ref_dir / "canvas-replay-attestation.json"
        attestation.write_text(
            json.dumps(
                {
                    "license": "https://example.test/license — explicit owner permission granted via email 2026-05-20",
                    "disclaimer": "Not affiliated with example.test. https://example.test assets loaded for canvas-fidelity per opt-in.",
                    "attestedBy": "operator-handle",
                    "attestedAt": "2026-05-25T08:00:00Z",
                    "ref_canvas_sources": [
                        "https://example.test/assets/canvas-driver.js",
                    ],
                }
            ),
            encoding="utf-8",
        )
        return attestation

    def _write_stamp(self, ref_dir: Path, attestation_path: Path,
                      stamped_by: str = "scripts/verify/check-canvas-replay.sh") -> Path:
        """Write a canvas-replay-stamp.json with attestation sha256."""
        import datetime
        import hashlib

        attestation_sha = hashlib.sha256(attestation_path.read_bytes()).hexdigest()
        attestation_data = json.loads(attestation_path.read_text(encoding="utf-8"))
        stamp = ref_dir / "canvas-replay-stamp.json"
        stamp.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "closeoutKind": "canvas-replay",
                    "stampedBy": stamped_by,
                    "verifiedAt": datetime.datetime.now(datetime.UTC).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    "attestationSha256": attestation_sha,
                    "refCanvasSources": attestation_data.get("ref_canvas_sources", []),
                    "attestedBy": attestation_data.get("attestedBy", ""),
                }
            ),
            encoding="utf-8",
        )
        return stamp

    def test_canvas_replay_stamp_releases_stop_with_valid_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_canvas_replay_state(ref_dir)
        attestation = self._write_attestation(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        app = src / "App.tsx"
        app.write_text("export default function App(){return <canvas />}", encoding="utf-8")

        stamp = self._write_stamp(ref_dir, attestation)
        now = time.time()
        os.utime(app, (now - 10, now - 10))
        os.utime(stamp, (now, now))

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        assert exit_code == 0
        assert "block" not in output.lower(), (
            f"canvas-replay stamp must release Stop; got: {output!r}"
        )

    def test_canvas_replay_policy_blocks_when_stamp_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """canvas-replay policy + no stamp → block. Message must reference
        canvas-replay-stamp.json (not verify-stamp.json / structural-
        convergence-stamp.json)."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_canvas_replay_state(ref_dir)
        self._write_attestation(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text(
            "export default function App(){return <canvas />}", encoding="utf-8"
        )

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "canvas-replay-stamp.json" in reason
        assert "check-canvas-replay.sh" in reason
        # Must NOT reference verify-stamp.json or structural-convergence-stamp.json —
        # this policy opted out of both.
        assert "verify-stamp.json" not in reason
        assert "structural-convergence-stamp.json" not in reason

    def test_canvas_replay_stamp_non_canonical_writer_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stamp written by anything other than scripts/verify/check-canvas-replay.sh
        must be rejected. Prevents hand-written stamps."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_canvas_replay_state(ref_dir)
        attestation = self._write_attestation(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text(
            "export default function App(){return <canvas />}", encoding="utf-8"
        )

        self._write_stamp(ref_dir, attestation, stamped_by="hand-written")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "non-canonical" in reason.lower() or "stampedBy" in reason

    def test_canvas_replay_attestation_tampered_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If canvas-replay-attestation.json is edited after the stamp was
        written, the stamp's attestationSha256 won't match. Block."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_canvas_replay_state(ref_dir)
        attestation = self._write_attestation(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text(
            "export default function App(){return <canvas />}", encoding="utf-8"
        )

        self._write_stamp(ref_dir, attestation)

        # Tamper with the attestation — adds a new ref_canvas_source URL.
        att_data = json.loads(attestation.read_text())
        att_data["ref_canvas_sources"].append("https://example.test/extra.js")
        attestation.write_text(json.dumps(att_data), encoding="utf-8")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "tampered" in reason.lower() or "attestation" in reason.lower()

    def test_canvas_replay_attestation_missing_blocks_stamp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """closeoutPolicy=canvas-replay + canvas-replay-stamp.json present but
        canvas-replay-attestation.json MISSING → block. The attestation is
        the operator's license confirmation; the stamp without the attestation
        it attests to is meaningless."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_canvas_replay_state(ref_dir)
        attestation = self._write_attestation(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text(
            "export default function App(){return <canvas />}", encoding="utf-8"
        )

        self._write_stamp(ref_dir, attestation)
        attestation.unlink()  # remove the attestation after stamping

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "attestation" in reason.lower()
