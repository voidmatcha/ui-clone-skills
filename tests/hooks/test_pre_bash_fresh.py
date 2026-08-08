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
    """Mirroring the live site into impl/public is still denied — but by the
    retained static-mirror family, not the (removed, hook-slimming B)
    fresh-folder ordering-nanny. Starting a static server before post-implement
    is now an advisory warning, not a hard block (Stop verify-stamp gate is the
    real backstop)."""

    MODULE = "ui_clone.hooks.pre_bash"

    def test_wget_static_mirror_blocked_before_pipeline(self, tmp_path: Path) -> None:
        """wget mirror into impl/public → still denied (static-mirror guard)."""
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
        assert "static mirror blocked" in reason

    def test_curl_static_html_save_blocked_before_pipeline(self, tmp_path: Path) -> None:
        """curl writes live HTML to impl/public → still denied (static-mirror guard)."""
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
        assert "static mirror blocked" in reason

    def test_static_server_before_pipeline_warns_not_blocks(self, tmp_path: Path) -> None:
        """Serving impl/public before Phase 1 → advisory WARNING, not a block
        (hook slimming B demoted the static-server guard). The command is
        allowed; the Stop verify-stamp gate is the real ship-short backstop."""
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
        # No deny payload on stdout — the command is not blocked.
        assert result.stdout.strip() == "", f"expected no block, got: {result.stdout}"

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

