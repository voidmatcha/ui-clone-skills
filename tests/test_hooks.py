"""
Tests for ui_clone.hooks.{pre_generate, post_verify, section_gate}
TDD: all tests written before implementation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def run_hook(
    module: str, stdin_data: str = "", env: dict | None = None
) -> subprocess.CompletedProcess:
    """Run a hook module as a subprocess, returning CompletedProcess."""
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", module],
        input=stdin_data,
        capture_output=True,
        text=True,
        env=merged_env,
    )


def make_search_root(tmp_path: Path) -> Path:
    """Create a tmp/ref directory and return it."""
    sr = tmp_path / "tmp" / "ref"
    sr.mkdir(parents=True)
    return sr


def make_ref_dir(search_root: Path, name: str = "test-session") -> Path:
    """Create a ref dir inside search_root."""
    ref = search_root / name
    ref.mkdir(parents=True, exist_ok=True)
    return ref


def set_active_marker(ref_dir: Path, age_seconds: float = 0.0) -> Path:
    """Touch a .ui-re-active marker inside ref_dir, optionally with a past mtime."""
    marker = ref_dir / ".ui-re-active"
    marker.touch()
    if age_seconds > 0:
        t = time.time() - age_seconds
        os.utime(marker, (t, t))
    return marker


def write_extracted_json(ref_dir: Path) -> None:
    """Write a minimal extracted.json so mtime fallback picks this ref."""
    (ref_dir / "extracted.json").write_text(
        json.dumps({"sections": [], "url": "https://example.com"}),
        encoding="utf-8",
    )


def _populate_pre_generate_artifacts(ref_dir: Path) -> None:
    """Write the minimal artifact set that makes gate_pre_generate pass.

    Sets parent artifacts to a fixed past mtime and extracted.json to a newer
    mtime so the DAG staleness check doesn't flag anything.
    """
    base_time = time.time() - 2.0
    extracted_time = base_time + 1.0

    # Core extraction artifacts
    (ref_dir / "structure.json").write_text(json.dumps({"sections": [], "totalCount": 0}))
    (ref_dir / "styles.json").write_text(json.dumps({"selectors": {}}))
    (ref_dir / "section-map.json").write_text(
        json.dumps({"sections": [], "totalCount": 0, "hasFooter": False})
    )
    (ref_dir / "component-map.json").write_text(json.dumps({"sections": [], "sectionCount": 0}))
    (ref_dir / "interactions-detected.json").write_text(
        json.dumps({"interactions": [], "hasPreloader": False})
    )
    (ref_dir / "hover-css-rules.json").write_text(json.dumps({"rules": []}))
    (ref_dir / "transition-spec.json").write_text(json.dumps({"transitions": []}))
    (ref_dir / "bundle-map.json").write_text(json.dumps({"chunks": []}))
    (ref_dir / "animation-init-styles.json").write_text(json.dumps({"elements": []}))
    (ref_dir / "svg-text-elements.json").write_text(json.dumps({"elements": []}))
    (ref_dir / "transition-coverage.json").write_text(
        json.dumps({"animatedElements": [], "staticElements": []})
    )
    (ref_dir / "element-roles.json").write_text(json.dumps({"roles": []}))
    (ref_dir / "element-groups.json").write_text(json.dumps({"groups": []}))
    (ref_dir / "layout-decisions.json").write_text(json.dumps({"decisions": []}))
    responsive = ref_dir / "responsive"
    responsive.mkdir(exist_ok=True)
    (responsive / "sizing-expressions.json").write_text(json.dumps({"expressions": []}))

    # Set all parents to base_time
    for name in [
        "structure.json", "styles.json", "section-map.json", "component-map.json",
        "interactions-detected.json", "hover-css-rules.json", "transition-spec.json",
        "bundle-map.json", "animation-init-styles.json", "svg-text-elements.json",
        "transition-coverage.json",
    ]:
        p = ref_dir / name
        if p.exists():
            os.utime(p, (base_time, base_time))

    # extracted.json must be strictly newer
    (ref_dir / "extracted.json").write_text(
        json.dumps({"sections": [], "url": "https://example.com"})
    )
    os.utime(ref_dir / "extracted.json", (extracted_time, extracted_time))

    provenance_artifacts = [
        "extracted.json",
        "transition-spec.json",
        "animation-init-styles.json",
        "section-map.json",
        "svg-text-elements.json",
        "responsive/sizing-expressions.json",
        "interactions-detected.json",
        "transition-coverage.json",
        "component-map.json",
    ]
    (ref_dir / "artifact-provenance.json").write_text(json.dumps({
        "artifacts": [
            {
                "path": artifact,
                "source": "agent-browser-eval" if artifact != "transition-spec.json" else "bundle-grep",
                "evidence": [artifact],
                "generatedAt": "2026-05-14T00:00:00Z",
            }
            for artifact in provenance_artifacts
        ],
    }))


# ─────────────────────────────────────────────────────────────────────────────
# pre_generate tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPreGenerate:
    MODULE = "ui_clone.hooks.pre_generate"

    def _tool_input(self, file_path: str) -> str:
        return json.dumps({"tool_name": "Write", "tool_input": {"file_path": file_path}})

    def test_no_wip_marker_runs_gate_and_blocks_on_missing_artifacts(self, tmp_path: Path) -> None:
        """No WIP marker + incomplete artifacts → gate runs, blocks. Marker is the
        side-effect of a passing gate, not a precondition for enforcement."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        write_extracted_json(ref_dir)  # only extracted.json — gate must fail

        tool_input = self._tool_input(str(tmp_path / "src/components/Button.tsx"))
        result = run_hook(
            self.MODULE,
            stdin_data=tool_input,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"Expected deny JSON, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        # Marker not created when gate failed — activation only happens on pass.
        assert not (ref_dir / ".ui-re-active").is_file()

    def test_no_wip_marker_gate_passes_creates_marker_and_prints_activation(self, tmp_path: Path) -> None:
        """No WIP marker + full artifacts → gate passes → marker is created on first
        activation and the stop-gate activation message is printed to stderr."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        _populate_pre_generate_artifacts(ref_dir)
        marker = ref_dir / ".ui-re-active"
        assert not marker.is_file()

        tool_input = self._tool_input(str(tmp_path / "src/components/Hero.tsx"))
        result = run_hook(
            self.MODULE,
            stdin_data=tool_input,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert "permissionDecision" not in result.stdout
        # First activation: marker created, message printed
        assert marker.is_file()
        assert "stop gate" in result.stderr.lower()

    def test_wip_marker_gate_passes_exits_0(self, tmp_path: Path) -> None:
        """WIP marker exists but gate.py returns pass → exit 0."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        # Mark as active
        set_active_marker(ref_dir)
        # Write enough artifacts that gate passes (or mock by not having
        # a component path match — but we do want path match here).
        # Easiest: use a path that is NOT a component file → hook exits early.
        tool_input = self._tool_input(str(tmp_path / "README.md"))
        result = run_hook(
            self.MODULE,
            stdin_data=tool_input,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_component_path_no_wip_exits_0(self, tmp_path: Path) -> None:
        """Component path + no WIP marker → exits 0 (no ref dir found via marker)."""
        make_search_root(tmp_path)
        # No active marker, no extracted.json → no ref dir found
        tool_input = self._tool_input(str(tmp_path / "src/components/Hero.tsx"))
        result = run_hook(
            self.MODULE,
            stdin_data=tool_input,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_wip_marker_gate_fails_outputs_block_json(self, tmp_path: Path) -> None:
        """WIP marker present + gate fails (missing artifacts) → block JSON."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        # Do NOT write extracted.json → gate_pre_generate will fail

        tool_input = self._tool_input(str(tmp_path / "src/components/Hero.tsx"))
        result = run_hook(
            self.MODULE,
            stdin_data=tool_input,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        # Should output block JSON (exit 0 per Claude hook protocol)
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"Expected JSON output, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        assert "hookSpecificOutput" in data
        hook_out = data["hookSpecificOutput"]
        assert hook_out.get("permissionDecision") == "deny"
        assert "permissionDecisionReason" in hook_out

    def test_wip_marker_gate_passes_refreshes_marker_silently(self, tmp_path: Path) -> None:
        """Existing marker + gate passes → marker mtime refreshed, activation
        message NOT re-printed (only first activation prints)."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        marker = set_active_marker(ref_dir, age_seconds=60.0)  # 1 min old
        old_mtime = marker.stat().st_mtime

        _populate_pre_generate_artifacts(ref_dir)

        tool_input = self._tool_input(str(tmp_path / "src/components/Hero.tsx"))
        result = run_hook(
            self.MODULE,
            stdin_data=tool_input,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert "permissionDecision" not in result.stdout
        # Marker mtime refreshed
        assert marker.exists()
        assert marker.stat().st_mtime > old_mtime
        # Activation message NOT re-printed on subsequent edits (avoids spam)
        assert "stop gate" not in result.stderr.lower()

    def test_non_component_path_skips(self, tmp_path: Path) -> None:
        """Non-component path → exits 0 regardless of WIP state."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)

        tool_input = self._tool_input(str(tmp_path / "package.json"))
        result = run_hook(
            self.MODULE,
            stdin_data=tool_input,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""


# ─────────────────────────────────────────────────────────────────────────────
# section_gate tests
# ─────────────────────────────────────────────────────────────────────────────


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
                        "spec",
                        "pre-generate",
                        "post-implement",
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
                        "spec",
                        "pre-generate",
                        "post-implement",
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
                        "spec",
                        "pre-generate",
                        "post-implement",
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


# ─────────────────────────────────────────────────────────────────────────────
# post_verify tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPostVerify:
    MODULE = "ui_clone.hooks.post_verify"

    def _bash_tool_input(self, command: str) -> str:
        return json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "tool_response": "ok",
            }
        )

    def test_no_ref_dir_exits_0(self, tmp_path: Path) -> None:
        """No tmp/ref dir → skips everything → exit 0."""
        result = run_hook(
            self.MODULE,
            stdin_data=self._bash_tool_input("git commit -m done"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0

    def test_non_completion_command_exits_0(self, tmp_path: Path) -> None:
        """Non-completion Bash command → exit 0 (advisory-only hook)."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data=self._bash_tool_input("npm install"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0

    def test_always_exits_0(self, tmp_path: Path) -> None:
        """post_verify is advisory — always exits 0 even on completion signal."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        write_extracted_json(ref_dir)
        # No diff PNGs, no layout-health.json → verification hasn't been run

        result = run_hook(
            self.MODULE,
            stdin_data=self._bash_tool_input("git commit -m done"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        # Always advisory → exit 0
        assert result.returncode == 0

    def test_multi_state_warning_on_click_interactions(self, tmp_path: Path) -> None:
        """Click interactions present + no alt-state captures → warns but exits 0."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        # Provide diff images + layout-health.json so Check 1 passes
        diff_dir = ref_dir / "static" / "diff"
        diff_dir.mkdir(parents=True)
        for i in range(3):
            (diff_dir / f"diff_{i}.png").write_bytes(b"\x89PNG" + b"\x00" * 20)
        (ref_dir / "layout-health.json").write_text('{"healthy": true}')
        # Click interactions but no alternate-state captures
        (ref_dir / "interactions-detected.json").write_text(
            json.dumps(
                {
                    "interactions": [
                        {"trigger": "click", "selector": ".search-btn"},
                        {"trigger": "hover", "selector": ".nav-item"},
                    ],
                    "hasPreloader": False,
                }
            )
        )

        result = run_hook(
            self.MODULE,
            stdin_data=self._bash_tool_input("git commit -m done"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert (
            "click interaction" in result.stdout.lower()
            or "alternate-state" in result.stdout.lower()
        )


# ── section_gate — full gate enforcement ──


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
                        "spec",
                        "pre-generate",
                        "post-implement",
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
                        "spec",
                        "pre-generate",
                        "post-implement",
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


class TestNestedGitRepoRoot:
    """Verifies find_project_root finds the correct root based on tmp/ref/ in nested git repos."""

    def test_git_root_without_tmp_ref_falls_through_to_walk(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """git root without tmp/ref/ falls through to walk-up logic."""
        import ui_clone.hooks._common as _common
        from ui_clone.hooks._common import find_project_root

        # Clear cache from previous tests
        monkeypatch.setattr(_common, "_cached_project_root", None)

        # Simulate git returning a root that does NOT have tmp/ref/
        def fake_run(cmd: Any, **kwargs: Any) -> Any:
            class R:
                returncode = 0
                stdout = str(tmp_path) + "\n"

            return R()

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.setattr("subprocess.run", fake_run)
        # Place tmp/ref/ inside a subdirectory — the walk-up should find it
        sub = tmp_path / "nested" / "project"
        sub.mkdir(parents=True)
        (sub / "tmp" / "ref").mkdir(parents=True)
        monkeypatch.chdir(sub)

        result = find_project_root()
        # Should return `sub` (found via walk-up), not `tmp_path` (the fake git root)
        assert result == sub

    def test_git_root_with_tmp_ref_is_returned_directly(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """git root with tmp/ref/ is returned directly."""
        import ui_clone.hooks._common as _common
        from ui_clone.hooks._common import find_project_root

        # Clear cache from previous tests
        monkeypatch.setattr(_common, "_cached_project_root", None)

        (tmp_path / "tmp" / "ref").mkdir(parents=True)

        def fake_run(cmd: Any, **kwargs: Any) -> Any:
            class R:
                returncode = 0
                stdout = str(tmp_path) + "\n"

            return R()

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.setattr("subprocess.run", fake_run)

        result = find_project_root()
        assert result == tmp_path


class TestPostVerifyVerificationNotRun:
    """Tests for post_verify Check 1: verification has NOT been run."""

    MODULE = "ui_clone.hooks.post_verify"

    def _bash_tool_input(self, command: str) -> str:
        return json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": command}, "tool_response": "ok"}
        )

    def test_no_diff_no_health_warns(self, tmp_path: Path) -> None:
        """WIP marker + completion cmd + no diffs/health → warns about verification."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        write_extracted_json(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data=self._bash_tool_input("git commit -m done"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert "verification" in result.stdout.lower() and "not" in result.stdout.lower()

    def test_enough_diffs_no_warning(self, tmp_path: Path) -> None:
        """WIP marker + completion cmd + >=3 diffs + health file → no Check 1 warning."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        write_extracted_json(ref_dir)
        diff_dir = ref_dir / "static" / "diff"
        diff_dir.mkdir(parents=True)
        for i in range(3):
            (diff_dir / f"diff_{i}.png").write_bytes(b"\x89PNG" + b"\x00" * 20)

        result = run_hook(
            self.MODULE,
            stdin_data=self._bash_tool_input("git commit -m done"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        # Should NOT warn about verification not being run
        assert "verification has not" not in result.stdout.lower()


class TestPostVerifyBatchCompareFailures:
    """Tests for post_verify Check 2: batch-compare result has failures."""

    MODULE = "ui_clone.hooks.post_verify"

    def _bash_tool_input(self, command: str) -> str:
        return json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": command}, "tool_response": "ok"}
        )

    def test_batch_compare_failures_warns(self, tmp_path: Path) -> None:
        """batch-compare-result.txt with ❌ lines → warns about failures."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        write_extracted_json(ref_dir)
        # Make Check 1 pass (enough diffs)
        diff_dir = ref_dir / "static" / "diff"
        diff_dir.mkdir(parents=True)
        for i in range(3):
            (diff_dir / f"diff_{i}.png").write_bytes(b"\x89PNG" + b"\x00" * 20)
        # batch-compare-result.txt with failures
        (ref_dir / "batch-compare-result.txt").write_text(
            "scroll_00: ✅ AE=800\nscroll_50: ❌ AE=5000\nscroll_100: ❌ AE=4200\n",
            encoding="utf-8",
        )

        result = run_hook(
            self.MODULE,
            stdin_data=self._bash_tool_input("git commit -m done"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert "FAILED" in result.stdout or "failed" in result.stdout.lower()
        assert "2" in result.stdout  # 2 failures

    def test_batch_compare_all_pass_no_warning(self, tmp_path: Path) -> None:
        """batch-compare-result.txt with only ✅ → no Check 2 warning."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        write_extracted_json(ref_dir)
        diff_dir = ref_dir / "static" / "diff"
        diff_dir.mkdir(parents=True)
        for i in range(3):
            (diff_dir / f"diff_{i}.png").write_bytes(b"\x89PNG" + b"\x00" * 20)
        (ref_dir / "batch-compare-result.txt").write_text(
            "scroll_00: ✅ AE=300\nscroll_50: ✅ AE=200\n",
            encoding="utf-8",
        )

        result = run_hook(
            self.MODULE,
            stdin_data=self._bash_tool_input("git commit -m done"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert "FAILED" not in result.stdout


class TestCompletionPatternWordBoundary:
    """Verifies post_verify completion patterns apply word-boundary correctly."""

    def test_commit_substring_not_matched(self) -> None:
        """Substrings like 'commitment' must not trigger the pattern."""
        from ui_clone.hooks.post_verify import _is_completion_command

        assert not _is_completion_command("our team's commitment to quality")

    def test_commit_word_matched(self) -> None:
        """'git commit -m ...' must trigger the pattern."""
        from ui_clone.hooks.post_verify import _is_completion_command

        assert _is_completion_command("git commit -m 'fix layout'")

    def test_done_word_matched(self) -> None:
        """'all done' must trigger the pattern."""
        from ui_clone.hooks.post_verify import _is_completion_command

        assert _is_completion_command("all done")

    def test_deploy_word_matched(self) -> None:
        """'deploy' must trigger the pattern."""
        from ui_clone.hooks.post_verify import _is_completion_command

        assert _is_completion_command("deploy to production")

    def test_finish_word_matched(self) -> None:
        """'finish' alone must trigger the pattern."""
        from ui_clone.hooks.post_verify import _is_completion_command

        assert _is_completion_command("finish the work")

    def test_merge_word_matched(self) -> None:
        from ui_clone.hooks.post_verify import _is_completion_command

        assert _is_completion_command("git merge feature-branch")

    def test_push_word_matched(self) -> None:
        from ui_clone.hooks.post_verify import _is_completion_command

        assert _is_completion_command("git push origin main")

    def test_complete_word_matched(self) -> None:
        from ui_clone.hooks.post_verify import _is_completion_command

        assert _is_completion_command("mark task complete")

    def test_looks_good_phrase_matched(self) -> None:
        from ui_clone.hooks.post_verify import _is_completion_command

        assert _is_completion_command("LGTM, looks good to me")

    def test_all_pass_phrase_matched(self) -> None:
        from ui_clone.hooks.post_verify import _is_completion_command

        assert _is_completion_command("tests all pass now")

    def test_unrelated_command_not_matched(self) -> None:
        from ui_clone.hooks.post_verify import _is_completion_command

        assert not _is_completion_command("npm run dev")

    def test_pushup_substring_not_matched(self) -> None:
        """Word-boundary check: 'pushup' must not match the 'push' alternation."""
        from ui_clone.hooks.post_verify import _is_completion_command

        assert not _is_completion_command("schedule pushups for tomorrow")


class TestGateSubprocessTimeout:
    """Verifies that gate subprocess calls fail-open on TimeoutExpired."""

    def test_pre_generate_run_gate_timeout_fail_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_run_gate in pre_generate fails open (returns passed=True) on TimeoutExpired."""
        import subprocess
        from importlib import reload

        import ui_clone.hooks.pre_generate as mod

        reload(mod)

        def fake_run(*args: Any, **kwargs: Any) -> None:
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=5)

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = mod._run_gate(Path("/tmp/fake"))
        assert result.get("passed") is True, "TimeoutExpired must fail-open"
        assert result.get("fail_count") == 0

    def test_section_gate_run_gate_timeout_fail_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_run_gate in section_gate fails open (returns passed=True) on TimeoutExpired."""
        import subprocess
        from importlib import reload

        import ui_clone.hooks.section_gate as mod

        reload(mod)

        def fake_run(*args: Any, **kwargs: Any) -> None:
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=5)

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = mod._run_gate(Path("/tmp/fake"), "extraction")
        assert result.get("passed") is True, "TimeoutExpired must fail-open"
        assert result.get("fail_count") == 0


class TestComponentPathEnvOverride:
    """Verifies UI_RE_COMPONENT_PATHS env var overrides default component path patterns."""

    def test_default_src_components_matched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default: /src/components/ is matched."""
        monkeypatch.delenv("UI_RE_COMPONENT_PATHS", raising=False)
        from importlib import reload

        import ui_clone.hooks.pre_generate as mod

        reload(mod)
        assert mod._is_component_file("/home/user/project/src/components/Hero.tsx")

    def test_default_app_router_page_matched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default: /src/app/**/page.tsx is matched."""
        monkeypatch.delenv("UI_RE_COMPONENT_PATHS", raising=False)
        from importlib import reload

        import ui_clone.hooks.pre_generate as mod

        reload(mod)
        assert mod._is_component_file("/home/user/project/src/app/(home)/page.tsx")

    def test_default_layout_not_matched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default: /src/app/**/layout.tsx is NOT matched (only page.* enforced)."""
        monkeypatch.delenv("UI_RE_COMPONENT_PATHS", raising=False)
        from importlib import reload

        import ui_clone.hooks.pre_generate as mod

        reload(mod)
        assert not mod._is_component_file("/home/user/project/src/app/(home)/layout.tsx")

    def test_env_override_custom_path_matched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """UI_RE_COMPONENT_PATHS=/app/components/ → /app/components/Foo.tsx is matched."""
        monkeypatch.setenv("UI_RE_COMPONENT_PATHS", "/app/components/")
        from importlib import reload

        import ui_clone.hooks.pre_generate as mod

        reload(mod)
        assert mod._is_component_file("/home/user/project/app/components/Foo.tsx")

    def test_env_override_default_not_matched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """UI_RE_COMPONENT_PATHS=/app/components/ → default /src/components/ is NOT matched."""
        monkeypatch.setenv("UI_RE_COMPONENT_PATHS", "/app/components/")
        from importlib import reload

        import ui_clone.hooks.pre_generate as mod

        reload(mod)
        assert not mod._is_component_file("/home/user/project/src/components/Hero.tsx")

    def test_env_override_multiple_paths(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """UI_RE_COMPONENT_PATHS with colon-separated list matches any of the paths."""
        monkeypatch.setenv("UI_RE_COMPONENT_PATHS", "/app/components/:/app/pages/")
        from importlib import reload

        import ui_clone.hooks.pre_generate as mod

        reload(mod)
        assert mod._is_component_file("/home/user/project/app/components/Card.tsx")
        assert mod._is_component_file("/home/user/project/app/pages/index.tsx")

    def test_env_override_empty_string_uses_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """UI_RE_COMPONENT_PATHS='' falls through to built-in defaults."""
        monkeypatch.setenv("UI_RE_COMPONENT_PATHS", "")
        from importlib import reload

        import ui_clone.hooks.pre_generate as mod

        reload(mod)
        assert mod._is_component_file("/home/user/project/src/components/Button.tsx")


# ── section_gate — mark_passed OSError safety ──


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
                        "spec",
                        "pre-generate",
                        "post-implement",
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


# ─────────────────────────────────────────────────────────────────────────────
# session_resume tests
# ─────────────────────────────────────────────────────────────────────────────


class TestSessionResume:
    """SessionStart + PostCompact reinjection — addresses the empirically-dominant
    post-compact skip pattern (73% of past verification skips).
    """

    MODULE = "ui_clone.hooks.session_resume"

    def test_no_wip_marker_exits_silently(self, tmp_path: Path) -> None:
        """No active WIP marker → no injection, exit 0 with empty stdout."""
        make_search_root(tmp_path)  # tmp/ref/ exists but no children
        result = run_hook(
            self.MODULE,
            stdin_data="{}",
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_no_tmp_ref_at_all_exits_silently(self, tmp_path: Path) -> None:
        """No tmp/ref/ directory → exit 0 with empty stdout (cold project)."""
        result = run_hook(
            self.MODULE,
            stdin_data="{}",
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_wip_marker_emits_additional_context(self, tmp_path: Path) -> None:
        """Active WIP marker → emit hookSpecificOutput.additionalContext."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="375studio")
        set_active_marker(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data="{}",
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert "hookSpecificOutput" in payload
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "375studio" in ctx
        # Must mention the gate scripts by name so the agent knows what to run.
        assert "section-compare.sh" in ctx
        assert "transition-spec-coverage.sh" in ctx
        # Must mention the post-compact skip pattern explicitly.
        assert "post-compact" in ctx.lower() or "compact" in ctx.lower()
        # Must include the host-neutral goal card for delegated workers.
        assert "Goal Card: 375studio" in ctx
        assert "python -m ui_clone.goal tmp/ref/375studio" in ctx
        assert "delegated worker" in ctx

    def test_postcompact_payload_detected(self, tmp_path: Path) -> None:
        """When stdin signals PostCompact, the emitted hookEventName matches."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="375studio")
        set_active_marker(ref_dir)

        # PostCompact payloads carry a "trigger" field ("manual" or "auto")
        result = run_hook(
            self.MODULE,
            stdin_data=json.dumps({"trigger": "auto", "summary": "..."}),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["hookEventName"] == "PostCompact"

    def test_sessionstart_default_when_payload_ambiguous(self, tmp_path: Path) -> None:
        """Empty stdin → defaults to SessionStart event name."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="375studio")
        set_active_marker(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data="",
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"

    def test_intersection_trigger_in_spec_includes_reveal_check(self, tmp_path: Path) -> None:
        """transition-spec.json with intersection entry → message must call out
        reveal-trigger-check.sh as REQUIRED (not optional)."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="375studio")
        set_active_marker(ref_dir)
        (ref_dir / "transition-spec.json").write_text(
            json.dumps(
                {
                    "transitions": [
                        {"id": "works-reveal", "trigger": "intersection", "type": "fade-up"},
                    ]
                }
            )
        )

        result = run_hook(
            self.MODULE,
            stdin_data="{}",
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "reveal-trigger-check.sh" in ctx
        assert "REQUIRED" in ctx  # the inline marker for intersection entries
        assert "transition-implementation.md" in ctx
        assert "IntersectionObserver placement" in ctx

    def test_done_state_skips_injection(self, tmp_path: Path) -> None:
        """Marker present but state==done → no injection (project finished, nothing to nag).
        Closes spam-on-completed-projects loop now that section_gate no longer
        unlinks the marker on done."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="375studio")
        set_active_marker(ref_dir)
        _set_done_state(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data="{}",
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        # No injection — empty stdout
        assert result.stdout.strip() == "", (
            f"Expected silent skip on done state, got: {result.stdout!r}"
        )

    def test_empty_spec_omits_intersection_specific_doc_calls(self, tmp_path: Path) -> None:
        """transition-spec.json absent → omit intersection-specific guidance,
        but keep general gate list."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="static-site")
        set_active_marker(ref_dir)
        # No transition-spec.json at all

        result = run_hook(
            self.MODULE,
            stdin_data="{}",
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        # Still mentions the general gates
        assert "section-compare.sh" in ctx
        # But the intersection-specific REQUIRED inline marker is absent
        # (intersection text only present in the conditional block)
        assert "intersection/fade-up entries detected" not in ctx


# ─────────────────────────────────────────────────────────────────────────────
# pre_bash tests — blocks declaration-of-done bash commands
# ─────────────────────────────────────────────────────────────────────────────


def _bash_input(cmd: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})


def _set_done_state(ref_dir: Path) -> None:
    """Write pipeline-state.json with current_gate='done'."""
    from ui_clone.state import GATE_ORDER as _GO
    (ref_dir / "pipeline-state.json").write_text(
        json.dumps(
            {
                "component": ref_dir.name,
                "started_at": "2026-01-01T00:00:00Z",
                "completed_steps": list(_GO),
                "current_gate": "done",
                "last_updated": "2026-01-01T02:00:00Z",
            }
        )
    )


def _set_section_compare_state(ref_dir: Path) -> None:
    """Write pipeline-state.json with current_gate='section-compare'."""
    from ui_clone.state import GATE_ORDER as _GO
    (ref_dir / "pipeline-state.json").write_text(
        json.dumps(
            {
                "component": ref_dir.name,
                "started_at": "2026-01-01T00:00:00Z",
                "completed_steps": list(_GO[:-1]),
                "current_gate": "section-compare",
                "last_updated": "2026-01-01T02:00:00Z",
            }
        )
    )


def _write_passing_result_txt(ref_dir: Path) -> None:
    sections_dir = ref_dir / "sections"
    sections_dir.mkdir(exist_ok=True)
    (sections_dir / "result.txt").write_text(
        "Section 01 hero: ✅ PASS\nSection 02 cta: ✅ PASS\n"
    )


def _write_failing_result_txt(ref_dir: Path) -> None:
    sections_dir = ref_dir / "sections"
    sections_dir.mkdir(exist_ok=True)
    (sections_dir / "result.txt").write_text(
        "Section 01 hero: ✅ PASS\nSection 02 cta: ❌ FAIL diff=12.4%\n"
    )


def _write_missing_impl_result_txt(ref_dir: Path) -> None:
    sections_dir = ref_dir / "sections"
    sections_dir.mkdir(exist_ok=True)
    (sections_dir / "result.txt").write_text(
        "Section 01 hero: ✅ PASS\nSection 02 cta: ⚠️ MISSING impl\n"
    )


class TestPreBash:
    """PreToolUse Bash hook — blocks declaration-of-done commands when verification
    is incomplete. Closes the gap left by Stop hook + advisory-only PostToolUse."""

    MODULE = "ui_clone.hooks.pre_bash"

    def test_no_wip_marker_allows_anything(self, tmp_path: Path) -> None:
        """No active WIP → hook must not interfere with any bash command."""
        make_search_root(tmp_path)
        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input("git commit -m 'wip'"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_non_declaration_command_allowed(self, tmp_path: Path) -> None:
        """WIP active but command is read-only (git status) → allow."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input("git status"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_git_commit_blocked_when_state_not_done(self, tmp_path: Path) -> None:
        """WIP + git commit + state != done → deny."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        _set_section_compare_state(ref_dir)
        # No result.txt — gate will fail on missing artifact

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input("git commit -m 'done'"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"expected deny payload, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "section-compare" in reason or "post-implement" in reason

    def test_git_commit_allowed_when_done_and_result_clean(self, tmp_path: Path) -> None:
        """WIP + git commit + state == done + result.txt clean → allow."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        _set_done_state(ref_dir)
        _write_passing_result_txt(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input("git commit -m 'ship'"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_git_commit_blocked_when_result_has_fail(self, tmp_path: Path) -> None:
        """Even with state==done, if result.txt has ❌ FAIL → deny.
        (Catches the case where state.json says done but artifacts say otherwise.)"""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        _set_done_state(ref_dir)
        _write_failing_result_txt(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input("git commit -m 'ship'"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        out = result.stdout.strip()
        assert out, "expected deny payload"
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "FAIL" in data["hookSpecificOutput"]["permissionDecisionReason"]

    def test_git_commit_blocked_when_result_has_missing(self, tmp_path: Path) -> None:
        """⚠️ MISSING impl line → deny."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        _set_done_state(ref_dir)
        _write_missing_impl_result_txt(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input("git commit -m 'ship'"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        out = result.stdout.strip()
        assert out, "expected deny payload"
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "MISSING" in data["hookSpecificOutput"]["permissionDecisionReason"]

    def test_git_push_blocked_when_state_not_done(self, tmp_path: Path) -> None:
        """git push also triggers the gate."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        _set_section_compare_state(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input("git push origin main"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        out = result.stdout.strip()
        assert out, "expected deny payload"
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_gh_pr_create_blocked(self, tmp_path: Path) -> None:
        """gh pr create is also a declaration-of-done."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        _set_section_compare_state(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input("gh pr create --title 'feat: clone'"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        out = result.stdout.strip()
        assert out, "expected deny payload"

    def test_skip_env_var_disables_hook(self, tmp_path: Path) -> None:
        """UI_RE_SKIP_BASH_GATE=1 → hook silent, allows anything."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        _set_section_compare_state(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input("git commit -m 'emergency'"),
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "UI_RE_SKIP_BASH_GATE": "1",
            },
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_invalid_json_stdin_exits_silently(self, tmp_path: Path) -> None:
        """Garbled stdin → no crash, no block (fail-open on parse errors)."""
        result = run_hook(
            self.MODULE,
            stdin_data="not json{{{",
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""


class TestPreBashFileWriteBypass:
    """PreToolUse Bash hook also blocks Bash redirects/streams that write to
    component files (cat>, tee, sed -i ...). Closes the bypass where an agent
    could route around the PreToolUse Edit/Write gate via shell redirection.

    Reuses the pre-generate gate (extraction-complete) — same enforcement as
    pre_generate.py for symmetric coverage.
    """

    MODULE = "ui_clone.hooks.pre_bash"

    def _component_path(self, tmp_path: Path) -> Path:
        d = tmp_path / "src" / "components"
        d.mkdir(parents=True, exist_ok=True)
        return d / "Hero.tsx"

    def test_cat_redirect_to_component_blocked_when_extraction_incomplete(self, tmp_path: Path) -> None:
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        # Marker not required — pre_bash file-write check uses pre-generate gate
        # path (mirrors pre_generate's behaviour: gate runs even without marker).
        write_extracted_json(ref_dir)  # only extracted.json — pre-generate fails
        target = self._component_path(tmp_path)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(f"cat > {target} << 'EOF'\n<div/>\nEOF"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"expected deny payload, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "Bash write" in reason or "extraction incomplete" in reason

    def test_append_redirect_to_component_blocked(self, tmp_path: Path) -> None:
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        write_extracted_json(ref_dir)
        target = self._component_path(tmp_path)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(f"cat >> {target} << 'EOF'\n.x{{}}\nEOF"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout.strip(), "expected block on >> redirect"

    def test_tee_to_component_blocked(self, tmp_path: Path) -> None:
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        write_extracted_json(ref_dir)
        target = self._component_path(tmp_path)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(f"echo '<div/>' | tee {target}"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout.strip(), "expected block on tee redirect"

    def test_sed_inplace_to_component_blocked(self, tmp_path: Path) -> None:
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        write_extracted_json(ref_dir)
        target = self._component_path(tmp_path)
        target.write_text("placeholder")

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(f"sed -i 's/foo/bar/g' {target}"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout.strip(), "expected block on sed -i"

    def test_redirect_to_non_component_allowed(self, tmp_path: Path) -> None:
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        write_extracted_json(ref_dir)
        # /tmp/whatever.tsx is NOT inside /src/components or /src/projects → allowed
        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input("cat > /tmp/scratch.tsx << 'EOF'\nx\nEOF"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_dev_null_redirect_ignored(self, tmp_path: Path) -> None:
        """Common process-output redirects must not trip the file-write gate."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input("npm test 2>&1 > /dev/null"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_skip_env_bypass_works(self, tmp_path: Path) -> None:
        """UI_RE_SKIP_BASH_GATE=1 short-circuits the entire hook."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        write_extracted_json(ref_dir)
        target = self._component_path(tmp_path)
        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(f"cat > {target} << 'EOF'\nx\nEOF"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path), "UI_RE_SKIP_BASH_GATE": "1"},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_passing_gate_allows_write(self, tmp_path: Path) -> None:
        """Full extraction artifacts → gate passes → bash redirect to component allowed."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        _populate_pre_generate_artifacts(ref_dir)
        target = self._component_path(tmp_path)
        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(f"cat > {target} << 'EOF'\nx\nEOF"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "", f"expected allow, got: {result.stdout}"


# ─────────────────────────────────────────────────────────────────────────────
# pre_generate post-done invalidation
# ─────────────────────────────────────────────────────────────────────────────


class TestPreGeneratePostDoneInvalidation:
    """When pipeline-state shows 'done' but a component edit happens, the prior
    section-compare result is stale. pre_generate must demote state so the next
    Stop hook re-runs section-compare."""

    MODULE = "ui_clone.hooks.pre_generate"

    def _tool_input(self, file_path: str) -> str:
        return json.dumps({"tool_name": "Edit", "tool_input": {"file_path": file_path}})

    def test_post_done_edit_demotes_state(self, tmp_path: Path) -> None:
        """current_gate='done' + WIP + component edit → state demoted to section-compare."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        _populate_pre_generate_artifacts(ref_dir)
        _set_done_state(ref_dir)

        tool_input = self._tool_input(str(tmp_path / "src/components/Hero.tsx"))
        result = run_hook(
            self.MODULE,
            stdin_data=tool_input,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0

        # State should now be at section-compare again
        from ui_clone.state import PipelineState
        reloaded = PipelineState.load(ref_dir)
        assert reloaded.current_gate == "section-compare", (
            f"Expected demotion to section-compare, got {reloaded.current_gate}. "
            f"stderr: {result.stderr}"
        )
        # Stderr should mention the demotion
        assert "demoted" in result.stderr.lower() or "post-done" in result.stderr.lower()

    def test_post_done_edit_invalidates_result_txt(self, tmp_path: Path) -> None:
        """post-done edit must rename sections/result.txt → result.txt.stale,
        so the next section-compare gate run can't pass on the prior PASS lines."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        _populate_pre_generate_artifacts(ref_dir)
        _set_done_state(ref_dir)
        _write_passing_result_txt(ref_dir)
        result_file = ref_dir / "sections" / "result.txt"
        assert result_file.is_file()  # precondition

        tool_input = self._tool_input(str(tmp_path / "src/components/Hero.tsx"))
        result = run_hook(
            self.MODULE,
            stdin_data=tool_input,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0

        # result.txt is gone; result.txt.stale exists with the prior content.
        assert not result_file.exists(), (
            "result.txt must be moved aside on post-done edit"
        )
        stale = result_file.with_suffix(".txt.stale")
        assert stale.is_file(), "result.txt.stale must capture the prior content"
        assert "PASS" in stale.read_text(encoding="utf-8")

    def test_pre_done_state_unchanged(self, tmp_path: Path) -> None:
        """If state is already at section-compare (not done), no demotion happens."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        _populate_pre_generate_artifacts(ref_dir)
        _set_section_compare_state(ref_dir)

        tool_input = self._tool_input(str(tmp_path / "src/components/Hero.tsx"))
        result = run_hook(
            self.MODULE,
            stdin_data=tool_input,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0

        from ui_clone.state import PipelineState
        reloaded = PipelineState.load(ref_dir)
        assert reloaded.current_gate == "section-compare"
        # No demotion message
        assert "demoted" not in result.stderr.lower()


class TestPreBashBenchmarkSetupAlignment:
    """PreToolUse Bash hook blocks benchmark-related commands when the
    `tmp/ref/realfood` symlink points at a different SHA's work dir than
    the current HEAD. This closes the 'agent skips Step 1 setup, inherits
    prior round's symlink' bug observed 3× across rounds A / B / V3.

    Recovery action: `bash skills/benchmark/scripts/setup.sh` (idempotent
    wipe + re-link). The escape regex lets that command through even when
    the symlink is stale.
    """

    MODULE = "ui_clone.hooks.pre_bash"

    def _init_git_repo(self, tmp_path: Path, commit_message: str = "init") -> str:
        """Init a tmp git repo and return its short HEAD SHA."""
        subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
        subprocess.run(
            ["git", "-C", str(tmp_path), "config", "user.email", "t@t.invalid"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True
        )
        (tmp_path / "README").write_text("test", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "README"], check=True
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-q", "-m", commit_message],
            check=True,
        )
        sha = subprocess.run(
            ["git", "-C", str(tmp_path), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return sha

    def _make_stale_symlink(self, tmp_path: Path, stale_sha: str) -> Path:
        """Create tmp/ref/realfood → benchmark/work/<stale_sha>/ref."""
        work = tmp_path / "benchmark" / "work" / stale_sha / "ref"
        work.mkdir(parents=True)
        sym_parent = tmp_path / "tmp" / "ref"
        sym_parent.mkdir(parents=True)
        sym = sym_parent / "realfood"
        sym.symlink_to(work)
        return sym

    def test_stale_symlink_blocks_section_compare(self, tmp_path: Path) -> None:
        """Bench command with symlink pointing at a different SHA → block."""
        head_sha = self._init_git_repo(tmp_path)
        stale_sha = "deadbee"  # arbitrary other SHA
        assert stale_sha != head_sha
        self._make_stale_symlink(tmp_path, stale_sha)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(
                "bash skills/visual-debug/scripts/section-compare.sh "
                "https://x.test http://localhost:3000 sess tmp/ref/realfood"
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"expected deny payload, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "benchmark setup mismatch" in reason
        assert stale_sha in reason
        assert head_sha in reason
        assert "skills/benchmark/scripts/setup.sh" in reason

    def test_aligned_symlink_allows_section_compare(self, tmp_path: Path) -> None:
        """Symlink SHA matches HEAD → no block (other checks may still fire)."""
        head_sha = self._init_git_repo(tmp_path)
        self._make_stale_symlink(tmp_path, head_sha)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(
                f"bash skills/visual-debug/scripts/section-compare.sh "
                f"https://x.test http://localhost:3000 sess "
                f"benchmark/work/{head_sha}/ref"
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        # No active WIP marker, no declaration command → no block expected.
        # (Downstream existing logic may emit if other conditions hold; for
        # this test we care that the alignment check doesn't fire.)
        out = result.stdout.strip()
        if out:
            data = json.loads(out)
            reason = data.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
            assert "benchmark setup mismatch" not in reason, (
                "alignment check should not fire when SHAs match"
            )

    def test_setup_sh_escape_allowed_even_when_stale(self, tmp_path: Path) -> None:
        """`bash skills/benchmark/scripts/setup.sh` is the recovery action.
        It must be allowed through even when the symlink is stale — otherwise
        the agent cannot recover."""
        head_sha = self._init_git_repo(tmp_path)
        stale_sha = "deadbee"
        assert stale_sha != head_sha
        self._make_stale_symlink(tmp_path, stale_sha)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input("bash skills/benchmark/scripts/setup.sh"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        # setup.sh escape — alignment check must not fire on this command.
        if out:
            data = json.loads(out)
            reason = data.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
            assert "benchmark setup mismatch" not in reason, (
                "setup.sh is the recovery path; alignment check must allow it"
            )

    def test_non_benchmark_command_unaffected(self, tmp_path: Path) -> None:
        """Generic commands (ls, git status, npm test) must not trigger the
        alignment check at all, even with a stale symlink present."""
        self._init_git_repo(tmp_path)
        stale_sha = "deadbee"
        self._make_stale_symlink(tmp_path, stale_sha)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input("ls -la"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        if out:
            data = json.loads(out)
            reason = data.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
            assert "benchmark setup mismatch" not in reason

    def test_bypass_env_var_disables_check(self, tmp_path: Path) -> None:
        """UI_RE_SKIP_BASH_GATE=1 short-circuits ALL hook logic, including
        the alignment check — the existing escape hatch must keep working."""
        self._init_git_repo(tmp_path)
        stale_sha = "deadbee"
        self._make_stale_symlink(tmp_path, stale_sha)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(
                "bash skills/visual-debug/scripts/section-compare.sh "
                "https://x.test http://localhost:3000 sess tmp/ref/realfood"
            ),
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "UI_RE_SKIP_BASH_GATE": "1",
            },
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "", (
            "UI_RE_SKIP_BASH_GATE=1 must short-circuit even the alignment check"
        )
