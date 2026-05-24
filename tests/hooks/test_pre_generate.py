from __future__ import annotations

import json
from pathlib import Path

from ._helpers import (
    _populate_pre_generate_artifacts,
    make_ref_dir,
    make_search_root,
    run_hook,
    set_active_marker,
    write_extracted_json,
)


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

