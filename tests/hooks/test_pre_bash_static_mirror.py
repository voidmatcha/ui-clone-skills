from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ._helpers import (
    _bash_input,
    _populate_pre_generate_artifacts,
    _set_extraction_state,
    _set_post_implement_state,
    make_ref_dir,
    make_search_root,
    run_hook,
    set_active_marker,
    write_extracted_json,
)


class TestPreBashPipelineStateStaticMirror:
    """A partial pipeline-state file must not unlock copied HTML mirrors."""

    MODULE = "ui_clone.hooks.pre_bash"

    def test_wget_static_mirror_blocked_while_extraction_incomplete(self, tmp_path: Path) -> None:
        """Loop-61: current_gate=extraction + wget into impl/public → deny."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="realfood")
        _set_extraction_state(ref_dir)
        target = tmp_path / "scratch" / "loop-61" / "impl" / "public"
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
        assert "static mirror" in reason
        assert "pipeline" in reason

    def test_whole_document_outer_html_snapshot_blocked(self, tmp_path: Path) -> None:
        """Loop-67: documentElement.outerHTML → tmp/ref/live.html is a static mirror seed."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="loop-67")
        _set_extraction_state(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(
                "agent-browser --session loop-67 eval "
                '"(() => document.documentElement.outerHTML)()" '
                "> tmp/ref/loop-67/live.html"
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"expected deny payload, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "whole-document" in reason
        assert "static mirror" in reason

    def test_section_outer_html_probe_allowed(self, tmp_path: Path) -> None:
        """Per-section outerHTML is legitimate extraction evidence; only whole-doc is blocked."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="loop-67")
        _set_extraction_state(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(
                "agent-browser --session loop-67 eval "
                '"(() => document.querySelector(\\\"section\\\")?.outerHTML || \\\"\\\")()" '
                "> tmp/ref/loop-67/hero-section.html"
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_impl_index_html_from_live_snapshot_blocked(self, tmp_path: Path) -> None:
        """Loop-67: live-unwrapped.html → impl/index.html is copied static HTML, not impl code."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="loop-67")
        _set_post_implement_state(ref_dir)
        target = tmp_path / "scratch" / "loop-67" / "impl" / "index.html"
        target.parent.mkdir(parents=True)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(
                "node <<'NODE'\n"
                "const fs = require('fs');\n"
                "const html = fs.readFileSync('tmp/ref/loop-67/live-unwrapped.html', 'utf8');\n"
                f"fs.writeFileSync('{target}', html);\n"
                "NODE"
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"expected deny payload, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "impl/index.html" in reason
        assert "static mirror" in reason

    def test_static_server_blocked_until_post_implement_gate(self, tmp_path: Path) -> None:
        """Loop-61: serving copied public files before implementation gate → deny."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="realfood")
        _set_extraction_state(ref_dir)
        server = tmp_path / "scratch" / "loop-61" / "impl" / "server.js"
        server.parent.mkdir(parents=True)
        server.write_text("require('node:http').createServer().listen(3061)")

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
        assert "post-implement" in reason

    def test_dev_server_allowed_after_pre_generate_passes(self, tmp_path: Path) -> None:
        """After current_gate=post-implement, dev-server commands are normal verification."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="realfood")
        _set_post_implement_state(ref_dir)
        server = tmp_path / "scratch" / "loop-61" / "impl" / "server.js"
        server.parent.mkdir(parents=True)
        server.write_text("require('node:http').createServer().listen(3061)")

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(f"node {server}"),
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



class TestPreBashBenchmarkCommands:
    """Benchmark workflow commands are not production hook special cases."""

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
        subprocess.run(
            ["git", "-C", str(tmp_path), "config", "commit.gpgSign", "false"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "config", "tag.gpgSign", "false"],
            check=True,
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

    def _make_benchmark_symlink(self, tmp_path: Path, stale_sha: str) -> Path:
        """Create tmp/ref/<component> -> benchmark/work/<sha>/ref."""
        work = tmp_path / "benchmark" / "work" / stale_sha / "ref"
        work.mkdir(parents=True)
        sym_parent = tmp_path / "tmp" / "ref"
        sym_parent.mkdir(parents=True)
        sym = sym_parent / "component"
        sym.symlink_to(work)
        return sym

    def test_benchmark_work_ref_command_has_no_symlink_alignment_block(
        self, tmp_path: Path
    ) -> None:
        """Benchmark work refs do not trigger maintainer-only SHA alignment."""
        head_sha = self._init_git_repo(tmp_path)
        stale_sha = "deadbee"  # arbitrary other SHA
        assert stale_sha != head_sha
        self._make_benchmark_symlink(tmp_path, stale_sha)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(
                "bash skills/visual-debug/scripts/section-compare.sh "
                "https://x.test http://localhost:3000 sess tmp/ref/component"
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        if out:
            data = json.loads(out)
            reason = data.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
            assert "benchmark setup mismatch" not in reason, (
                "benchmark SHA alignment is no longer a production hook rule"
            )

    def test_benchmark_setup_command_is_not_special_cased(self, tmp_path: Path) -> None:
        head_sha = self._init_git_repo(tmp_path)
        stale_sha = "deadbee"
        assert stale_sha != head_sha
        self._make_benchmark_symlink(tmp_path, stale_sha)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input("bash skills/benchmark/scripts/setup.sh"),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        if out:
            data = json.loads(out)
            reason = data.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
            assert "benchmark setup mismatch" not in reason, (
                "benchmark setup is no longer a production hook escape rule"
            )

    def test_non_benchmark_command_unaffected(self, tmp_path: Path) -> None:
        """Generic commands are unaffected by benchmark symlink layouts."""
        self._init_git_repo(tmp_path)
        stale_sha = "deadbee"
        self._make_benchmark_symlink(tmp_path, stale_sha)

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
        """UI_RE_SKIP_BASH_GATE=1 short-circuits hook logic."""
        self._init_git_repo(tmp_path)
        stale_sha = "deadbee"
        self._make_benchmark_symlink(tmp_path, stale_sha)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(
                "bash skills/visual-debug/scripts/section-compare.sh "
                "https://x.test http://localhost:3000 sess tmp/ref/component"
            ),
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "UI_RE_SKIP_BASH_GATE": "1",
            },
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "", (
            "UI_RE_SKIP_BASH_GATE=1 must short-circuit hook checks"
        )
