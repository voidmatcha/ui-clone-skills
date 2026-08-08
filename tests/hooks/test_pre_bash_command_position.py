"""Command-position hardening for the pre_bash deny cascade (82b0a4e class).

Live-fire false positive (2026-06-12): a diagnostic
`pgrep -fl "ci-local|section-compare|video-motion|agent-browser"` from the
orchestrator was blocked by fresh-folder enforcement because the deny patterns
matched the script/tool names as SUBSTRINGS inside the quoted pgrep argument.
Deny patterns must only fire when the tool is actually INVOKED — at command
position (line start or after a shell connector), never inside quoted strings,
heredoc bodies, or as an argument to grep/pgrep/command.
"""

from __future__ import annotations

from ui_clone.hooks._common import sanitize_command_for_deny, strip_heredoc_bodies
from ui_clone.hooks.pre_bash_rules.ref_state import _fresh_state_violation
from ui_clone.hooks.pre_bash_rules.static_mirror import (
    _static_mirror_download_violation,
    _static_server_violation,
)


class TestSanitizeCommandForDeny:
    def test_strips_quoted_argument(self) -> None:
        out = sanitize_command_for_deny('pgrep -fl "ci-local|section-compare|agent-browser"')
        assert "agent-browser" not in out
        assert "section-compare" not in out

    def test_strips_heredoc_body_keeps_later_command(self) -> None:
        cmd = "cat <<EOF\nrun section-compare.sh then agent-browser open x\nEOF\nls"
        out = sanitize_command_for_deny(cmd)
        assert "section-compare" not in out
        assert "agent-browser" not in out
        # a real command after the heredoc terminator survives
        assert "ls" in out

    def test_real_extraction_after_heredoc_survives(self) -> None:
        cmd = "cat <<EOF\ndoc body\nEOF\nagent-browser open https://realfood.gov"
        out = sanitize_command_for_deny(cmd)
        assert "agent-browser open" in out

    def test_strip_heredoc_keeps_quoted_path(self) -> None:
        # strip_heredoc_bodies must preserve quotes so mirror/write-target
        # extraction still sees a quoted impl/public path.
        cmd = 'cat <<EOF\nnpm run dev\nEOF\nwget -r -P "impl/public" https://x'
        out = strip_heredoc_bodies(cmd)
        assert "npm run dev" not in out
        assert "impl/public" in out


class TestStaticMirrorCommandPosition:
    def test_server_command_in_heredoc_body_passes(self) -> None:
        # The exact bug: a commit message describing the deny patterns
        # ("npm run dev", "npx serve/vite") must not trigger the server gate.
        cmd = (
            "git commit -F - <<'EOF'\n"
            "fix: detect npm run dev / npx serve/vite / http.server / node server.js\n"
            "EOF"
        )
        assert not _static_server_violation(cmd)

    def test_real_static_server_blocked(self) -> None:
        assert _static_server_violation("npm run dev")
        assert _static_server_violation("python3 -m http.server 8000")

    def test_mirror_in_heredoc_body_passes_but_keeps_real_detection(self) -> None:
        assert not _static_mirror_download_violation(
            "cat <<EOF\nwget -r -P impl/public https://x\nEOF"
        )
        assert _static_mirror_download_violation(
            "wget -r -E -P impl/public https://example.com/"
        )


class TestFreshStateCommandPosition:
    # ── false positives that must now PASS ──
    def test_quoted_pattern_pgrep_passes(self) -> None:
        assert not _fresh_state_violation(
            'pgrep -fl "ci-local|section-compare|video-motion|agent-browser"'
        )

    def test_unquoted_pgrep_arg_passes(self) -> None:
        assert not _fresh_state_violation("pgrep -fl agent-browser")

    def test_grep_pipe_arg_passes(self) -> None:
        assert not _fresh_state_violation("ps aux | grep agent-browser")

    def test_heredoc_body_with_script_names_passes(self) -> None:
        assert not _fresh_state_violation(
            "cat <<EOF\ninvoke section-compare.sh and agent-browser open https://x\nEOF"
        )

    def test_command_v_passes(self) -> None:
        assert not _fresh_state_violation("command -v agent-browser")

    def test_inspection_commands_pass(self) -> None:
        for cmd in (
            "lsof -i :5183",
            "stat tmp/ref/realfood-e2e-11",
            "du -sh /tmp/foo",
            "tail -50 /tmp/section-compare.log",
            "cat /tmp/video-motion.log",
        ):
            assert not _fresh_state_violation(cmd), cmd

    # ── real extraction calls that must STAY blocked ──
    def test_direct_agent_browser_open_blocked(self) -> None:
        assert _fresh_state_violation("agent-browser open https://realfood.gov")

    def test_agent_browser_after_connector_blocked(self) -> None:
        assert _fresh_state_violation("cd repo && agent-browser open https://x")

    def test_extraction_after_heredoc_blocked(self) -> None:
        assert _fresh_state_violation(
            "cat <<EOF\ndoc\nEOF\nagent-browser open https://realfood.gov"
        )

    def test_curl_http_blocked(self) -> None:
        assert _fresh_state_violation("curl -L https://example.com -o x.html")

    def test_wget_blocked(self) -> None:
        assert _fresh_state_violation("wget -p https://example.com/")

    def test_section_compare_script_path_blocked(self) -> None:
        assert _fresh_state_violation(
            "bash /repo/skills/visual-debug/scripts/section-compare.sh a b c"
        )


class TestEnvPrefixedInvocations:
    """batch-4 review MAJOR 2: CMD_POSITION_PREFIX missed env/assignment-prefixed
    invocations, so `FOO=1 agent-browser open`, `HTTPS_PROXY=x curl ...` and
    `PORT=5173 npm run dev` bypassed the deny. The prefix now tolerates leading
    KEY=VAL and `env` tokens before the executable, while keeping the
    quoted/heredoc/pgrep/command-v exemptions intact."""

    # ── env-prefixed real invocations that must now be BLOCKED ──
    def test_assignment_prefixed_agent_browser_blocked(self) -> None:
        assert _fresh_state_violation("FOO=1 agent-browser open https://realfood.gov")

    def test_proxy_prefixed_curl_blocked(self) -> None:
        assert _fresh_state_violation("HTTPS_PROXY=http://x curl https://example.com -o x.html")

    def test_port_prefixed_npm_dev_blocked(self) -> None:
        assert _fresh_state_violation("PORT=5173 npm run dev")

    def test_env_keyword_prefix_blocked(self) -> None:
        assert _fresh_state_violation("env agent-browser open https://x")

    def test_multiple_assignments_blocked(self) -> None:
        assert _fresh_state_violation("FOO=1 BAR=2 agent-browser open https://x")

    def test_assignment_prefix_after_connector_blocked(self) -> None:
        assert _fresh_state_violation("cd repo && PORT=3000 agent-browser open https://x")

    # ── exemptions that must STILL pass (no false positive) ──
    def test_assignment_prefix_keeps_pgrep_exemption(self) -> None:
        assert not _fresh_state_violation('FOO=1 pgrep -fl "agent-browser"')

    def test_assignment_prefix_keeps_command_v_exemption(self) -> None:
        assert not _fresh_state_violation("FOO=1 command -v agent-browser")

    def test_assignment_prefix_keeps_heredoc_exemption(self) -> None:
        assert not _fresh_state_violation(
            "cat <<EOF\nPORT=1 agent-browser open https://x\nEOF"
        )
