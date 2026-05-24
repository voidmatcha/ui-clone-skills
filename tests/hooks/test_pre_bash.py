from __future__ import annotations

import json
from pathlib import Path

from ._helpers import (
    _bash_input,
    _set_done_state,
    _set_section_compare_state,
    _write_failing_result_txt,
    _write_missing_impl_result_txt,
    _write_passing_result_txt,
    make_ref_dir,
    make_search_root,
    run_hook,
    set_active_marker,
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

    def test_section_compare_blocked_until_dom_mirror_check_passes(self, tmp_path: Path) -> None:
        """section-compare is not useful if DOM mirror has already hard-failed/missing."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        _set_section_compare_state(ref_dir)
        (ref_dir / "verification-plan.json").write_text(json.dumps({
            "requiredChecks": [
                {
                    "id": "dom-mirror-check",
                    "produces": "dom-mirror-check.json",
                    "severity": "block",
                    "tier": "quick",
                }
            ]
        }))

        cmd = (
            "bash skills/visual-debug/scripts/section-compare.sh "
            f"https://example.com http://localhost:3000 sess {ref_dir}"
        )
        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(cmd),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"expected deny payload, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "dom-mirror-check" in reason
        assert "section-compare" in reason

    def test_section_compare_blocked_until_proxy_mirror_check_passes(self, tmp_path: Path) -> None:
        """Visual compare must not run before original-runtime proxy mirrors are ruled out."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        _set_section_compare_state(ref_dir)
        (ref_dir / "verification-plan.json").write_text(json.dumps({
            "requiredChecks": [
                {
                    "id": "proxy-mirror-check",
                    "produces": "proxy-mirror-check.json",
                    "severity": "block",
                    "tier": "quick",
                }
            ]
        }))

        cmd = (
            "bash skills/visual-debug/scripts/section-compare.sh "
            f"https://example.com http://localhost:3000 sess {ref_dir}"
        )
        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(cmd),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"expected deny payload, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "proxy-mirror-check" in reason
        assert "section-compare" in reason

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

