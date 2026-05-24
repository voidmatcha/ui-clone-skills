from __future__ import annotations

import json
from pathlib import Path

from ._helpers import (
    make_ref_dir,
    make_search_root,
    run_hook,
    set_active_marker,
    write_extracted_json,
)


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

