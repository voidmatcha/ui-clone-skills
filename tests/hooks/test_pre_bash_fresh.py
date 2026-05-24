from __future__ import annotations

import json
from pathlib import Path

from ._helpers import (
    _bash_input,
    _set_section_compare_state,
    make_ref_dir,
    make_search_root,
    run_hook,
)


class TestPreBashFreshFolderStaticMirror:
    """Fresh natural prompts must enter the ui_clone pipeline, not mirror the
    live site into impl/public and self-verify with HTTP checks."""

    MODULE = "ui_clone.hooks.pre_bash"

    def test_wget_static_mirror_blocked_before_pipeline(self, tmp_path: Path) -> None:
        """No Phase 1 evidence + wget mirror into impl/public → deny."""
        make_search_root(tmp_path)
        target = tmp_path / "scratch" / "loop-60" / "impl" / "public"
        target.mkdir(parents=True)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(
                f"wget -E -H -k -K -p -P {target} https://example.com/"
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"expected deny payload, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "fresh-folder enforcement" in reason
        assert "pipeline driver FIRST" in reason

    def test_curl_static_html_save_blocked_before_pipeline(self, tmp_path: Path) -> None:
        """No Phase 1 evidence + curl writes live HTML to impl/public → deny."""
        make_search_root(tmp_path)
        target = tmp_path / "scratch" / "loop-60" / "impl" / "public" / "index.html"
        target.parent.mkdir(parents=True)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(f"curl -L https://example.com -o {target}"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"expected deny payload, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "fresh-folder enforcement" in reason
        assert "pipeline driver FIRST" in reason

    def test_static_server_blocked_before_pipeline(self, tmp_path: Path) -> None:
        """No Phase 1 evidence + serving impl/public → deny shallow mirror completion."""
        make_search_root(tmp_path)
        server = tmp_path / "scratch" / "loop-60" / "impl" / "server.js"
        server.parent.mkdir(parents=True)
        server.write_text("require('node:http').createServer().listen(3060)")

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(f"node {server}"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"expected deny payload, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "fresh-folder enforcement" in reason
        assert "pipeline driver FIRST" in reason

    def test_static_mirror_tools_allowed_after_pipeline_evidence(self, tmp_path: Path) -> None:
        """Once pipeline-state exists, fresh-folder enforcement no longer owns the command."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        _set_section_compare_state(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input("curl -I https://example.com"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        assert result.stdout.strip() == ""

