from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast

from ui_clone.hooks._common import ref_touched_by_session

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


def _bash_input_with_session(cmd: str, session_id: str, cwd: Path) -> str:
    return json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": cmd},
            "session_id": session_id,
            "cwd": str(cwd),
        }
    )


def _continuation_receipt_path(project: Path, session_id: str) -> Path:
    return project / ".ui-re-continuation" / f"{session_id}.json"


def _write_continuation_receipt(
    project: Path,
    session_id: str,
    *,
    state: str,
    ref_dir: str | None = None,
) -> Path:
    import hashlib

    path = _continuation_receipt_path(project, session_id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "host": "claude-code",
        "sessionId": session_id,
        "skill": "ui-clone-skills:ui-reverse-engineering",
        "state": state,
        "leaseTag": "UI_RE_CONTINUATION:"
        + hashlib.sha256(f"{project.resolve()}\0{session_id}".encode()).hexdigest()[:24],
        "createdAt": "2026-08-15T00:00:00Z",
        "updatedAt": "2026-08-15T00:00:00Z",
    }
    if ref_dir is not None:
        payload["refDir"] = ref_dir
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _read_continuation_receipt(project: Path, session_id: str) -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads(_continuation_receipt_path(project, session_id).read_text()),
    )


def _pipeline_run_command(component: str = "target-ref") -> str:
    return f"python -m ui_clone.pipeline https://example.com {component} sess run"


def _sitecustomize_pythonpath(tmp_path: Path, source: str) -> str:
    site_dir = tmp_path / "sitecustomize-shim"
    site_dir.mkdir()
    (site_dir / "sitecustomize.py").write_text(source, encoding="utf-8")
    current = os.environ.get("PYTHONPATH")
    if current:
        return f"{site_dir}{os.pathsep}{current}"
    return str(site_dir)


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

    def test_read_only_repo_script_reference_does_not_mark_session_owner(
        self, tmp_path: Path
    ) -> None:
        """Linting/grepping script paths must not claim an unrelated active ref."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, "reference-main-omx-09")
        set_active_marker(ref_dir)
        session_id = "current-dev-session"

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input_with_session(
                "uv run ruff check scripts/extract/_resource_mirror.py",
                session_id,
                tmp_path,
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert not ref_touched_by_session(ref_dir, session_id)

    def test_executing_resource_mirror_marks_target_ref_session(
        self, tmp_path: Path
    ) -> None:
        """Actual UI-RE script execution against a ref still records ownership."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, "target-ref")
        session_id = "current-ui-re-session"

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input_with_session(
                f"bash scripts/extract/resource-mirror.sh sess {ref_dir} https://example.com",
                session_id,
                tmp_path,
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        assert ref_touched_by_session(ref_dir, session_id)

    def test_continuation_pending_blocks_ui_re_execution_without_binding(
        self, tmp_path: Path
    ) -> None:
        """A pending Claude continuation receipt must activate cron first."""
        make_search_root(tmp_path)
        session_id = "claude-session"
        _write_continuation_receipt(tmp_path, session_id, state="pending")

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input_with_session(
                _pipeline_run_command("target-ref"), session_id, tmp_path
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"expected deny payload, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "continuation" in reason.lower()
        assert "cron" in reason.lower()
        assert "create-pending" in reason
        assert "refDir" not in _read_continuation_receipt(tmp_path, session_id)
        assert not ref_touched_by_session(tmp_path / "tmp" / "ref" / "target-ref", session_id)

    def test_continuation_active_binds_first_resolved_ref_and_allows(
        self, tmp_path: Path
    ) -> None:
        make_search_root(tmp_path)
        session_id = "claude-session"
        _write_continuation_receipt(tmp_path, session_id, state="active")

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input_with_session(
                _pipeline_run_command("target-ref"), session_id, tmp_path
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert _read_continuation_receipt(tmp_path, session_id)["refDir"] == (
            "tmp/ref/target-ref"
        )
        assert ref_touched_by_session(tmp_path / "tmp" / "ref" / "target-ref", session_id)

    def test_continuation_core_import_failure_fails_closed_preserves_receipt(
        self, tmp_path: Path
    ) -> None:
        make_search_root(tmp_path)
        session_id = "claude-session"
        receipt_path = _write_continuation_receipt(tmp_path, session_id, state="active")
        before = receipt_path.read_text(encoding="utf-8")
        pythonpath = _sitecustomize_pythonpath(
            tmp_path,
            """
import importlib

_real_import_module = importlib.import_module

def _blocked_import_module(name, package=None):
    if name == "ui_clone.claude_continuation":
        raise ImportError("forced continuation import failure")
    return _real_import_module(name, package)

importlib.import_module = _blocked_import_module
""",
        )

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input_with_session(
                _pipeline_run_command("target-ref"), session_id, tmp_path
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path), "PYTHONPATH": pythonpath},
        )

        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"expected deny payload, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "continuation" in reason.lower()
        assert "unavailable" in reason.lower()
        assert "repair" in reason.lower() or "pause" in reason.lower()
        assert receipt_path.read_text(encoding="utf-8") == before
        assert not ref_touched_by_session(tmp_path / "tmp" / "ref" / "target-ref", session_id)

    def test_continuation_core_bind_import_failure_fails_closed_preserves_receipt(
        self, tmp_path: Path
    ) -> None:
        make_search_root(tmp_path)
        session_id = "claude-session"
        receipt_path = _write_continuation_receipt(tmp_path, session_id, state="active")
        before = receipt_path.read_text(encoding="utf-8")
        pythonpath = _sitecustomize_pythonpath(
            tmp_path,
            """
import importlib
import json

_real_import_module = importlib.import_module
_continuation_imports = 0

class _ContinuationCore:
    @staticmethod
    def load_receipt(project_root, session_id):
        path = project_root / ".ui-re-continuation" / f"{session_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

def _stub_import_module(name, package=None):
    global _continuation_imports
    if name == "ui_clone.claude_continuation":
        _continuation_imports += 1
        if _continuation_imports == 1:
            return _ContinuationCore
        raise ImportError("forced continuation bind import failure")
    return _real_import_module(name, package)

importlib.import_module = _stub_import_module
""",
        )

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input_with_session(
                _pipeline_run_command("target-ref"), session_id, tmp_path
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path), "PYTHONPATH": pythonpath},
        )

        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"expected deny payload, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "continuation" in reason.lower()
        assert "bind" in reason.lower()
        assert "unavailable" in reason.lower()
        assert "repair" in reason.lower() or "pause" in reason.lower()
        assert receipt_path.read_text(encoding="utf-8") == before
        assert not ref_touched_by_session(tmp_path / "tmp" / "ref" / "target-ref", session_id)

    def test_continuation_active_bound_to_different_ref_fails_closed(
        self, tmp_path: Path
    ) -> None:
        make_search_root(tmp_path)
        session_id = "claude-session"
        _write_continuation_receipt(
            tmp_path, session_id, state="active", ref_dir="tmp/ref/ref-a"
        )

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input_with_session(
                _pipeline_run_command("ref-b"), session_id, tmp_path
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"expected deny payload, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "continuation" in reason.lower()
        assert "tmp/ref/ref-a" in reason
        assert "tmp/ref/ref-b" in reason
        assert _read_continuation_receipt(tmp_path, session_id)["refDir"] == "tmp/ref/ref-a"
        assert not ref_touched_by_session(tmp_path / "tmp" / "ref" / "ref-b", session_id)

    def test_continuation_paused_and_unsupported_do_not_deny(
        self, tmp_path: Path
    ) -> None:
        make_search_root(tmp_path)
        for state in ("paused", "unsupported"):
            session_id = f"session-{state}"
            _write_continuation_receipt(tmp_path, session_id, state=state)

            result = run_hook(
                self.MODULE,
                stdin_data=_bash_input_with_session(
                    _pipeline_run_command(f"target-{state}"), session_id, tmp_path
                ),
                env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
            )

            assert result.returncode == 0
            assert result.stdout.strip() == ""

    def test_continuation_control_commands_are_always_allowed(
        self, tmp_path: Path
    ) -> None:
        make_search_root(tmp_path)
        session_id = "claude-session"
        _continuation_receipt_path(tmp_path, session_id).parent.mkdir(parents=True)
        _continuation_receipt_path(tmp_path, session_id).write_text("{not-json", encoding="utf-8")

        for subcommand in ("create-pending", "bind-ref", "mark-unsupported", "pause", "status"):
            result = run_hook(
                self.MODULE,
                stdin_data=_bash_input_with_session(
                    f"python -m ui_clone.claude_continuation {subcommand}",
                    session_id,
                    tmp_path,
                ),
                env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
            )

            assert result.returncode == 0
            assert result.stdout.strip() == ""

    def test_continuation_ignores_other_session_and_missing_session(
        self, tmp_path: Path
    ) -> None:
        make_search_root(tmp_path)
        _write_continuation_receipt(tmp_path, "other-session", state="pending")

        no_session = run_hook(
            self.MODULE,
            stdin_data=_bash_input(_pipeline_run_command("no-session-ref")),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        other_session = run_hook(
            self.MODULE,
            stdin_data=_bash_input_with_session(
                _pipeline_run_command("current-ref"), "current-session", tmp_path
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert no_session.returncode == 0
        assert no_session.stdout.strip() == ""
        assert other_session.returncode == 0
        assert other_session.stdout.strip() == ""
        assert "refDir" not in _read_continuation_receipt(tmp_path, "other-session")

    def test_continuation_invalid_current_session_receipt_fails_closed(
        self, tmp_path: Path
    ) -> None:
        make_search_root(tmp_path)
        session_id = "claude-session"
        receipt_path = _continuation_receipt_path(tmp_path, session_id)
        receipt_path.parent.mkdir(parents=True)
        receipt_path.write_text("{not-json", encoding="utf-8")

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input_with_session(
                _pipeline_run_command("target-ref"), session_id, tmp_path
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"expected deny payload, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "continuation receipt" in reason.lower()
        assert "invalid" in reason.lower() or "corrupt" in reason.lower()
        assert receipt_path.read_text(encoding="utf-8") == "{not-json"

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


class TestEnforcementStateRmGuard:
    """pre_bash blocks targeted deletion/truncation of a guard's own state file
    (.gate-skip-log, off-pipeline crumbs, .ui-re-active) — closes the bypass where
    `rm .gate-skip-log` silently releases an un-enforced run."""

    MODULE = "ui_clone.hooks.pre_bash"

    def _run(self, cmd: str, tmp_path: Path):  # type: ignore[no-untyped-def]
        return run_hook(
            self.MODULE,
            stdin_data=_bash_input(cmd),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

    def _denied(self, result) -> bool:  # type: ignore[no-untyped-def]
        out = result.stdout.strip()
        if not out:
            return False
        data = json.loads(out)
        return bool(
            data.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
        )

    def test_rm_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        r = self._run("rm tmp/ref/comp/.gate-skip-log", tmp_path)
        assert self._denied(r), r.stdout
        reason = json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        assert "gate-skip-log" in reason and "enforcement-state" in reason.lower()

    def test_rm_external_browse_crumbs_blocked(self, tmp_path: Path) -> None:
        assert self._denied(self._run("rm -rf tmp/.ui-re-external-browse", tmp_path))

    def test_truncate_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        assert self._denied(self._run(": > tmp/ref/comp/.gate-skip-log", tmp_path))

    def test_mv_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        assert self._denied(self._run("mv tmp/ref/comp/.gate-skip-log /tmp/x", tmp_path))

    def test_find_delete_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("find tmp/ref -name .gate-skip-log -delete", tmp_path)
        )

    def test_whole_ref_dir_reset_allowed(self, tmp_path: Path) -> None:
        # A real fresh-state reset names the dir, not the enforcement file →
        # must NOT be blocked by this guard (it re-runs every gate from clean).
        assert not self._denied(self._run("rm -rf tmp/ref/comp", tmp_path))

    def test_unrelated_rm_allowed(self, tmp_path: Path) -> None:
        assert not self._denied(self._run("rm foo.txt", tmp_path))

    # --- item #4: clobber/overwrite/edit vectors beyond rm/mv/`:>`/find ---

    def test_dd_overwrite_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("dd if=/dev/null of=tmp/ref/comp/.gate-skip-log", tmp_path)
        )

    def test_truncate_command_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("truncate -s 0 tmp/ref/comp/.gate-skip-log", tmp_path)
        )

    def test_install_devnull_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("install -m644 /dev/null tmp/ref/comp/.gate-skip-log", tmp_path)
        )

    def test_rsync_overwrite_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("rsync -a /tmp/empty/ tmp/ref/comp/.gate-skip-log", tmp_path)
        )

    def test_ex_inplace_edit_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        # The ex script's own '|' (in -c '%d|wq') must not cut the match short.
        assert self._denied(
            self._run("ex -s -c '%d|wq' tmp/ref/comp/.gate-skip-log", tmp_path)
        )

    def test_python_write_text_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run(
                "python3 -c \"import pathlib; "
                "pathlib.Path('tmp/ref/comp/.gate-skip-log').write_text('')\"",
                tmp_path,
            )
        )

    def test_cat_heredoc_truncate_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("cat > tmp/ref/comp/.gate-skip-log <<EOF\nforged\nEOF", tmp_path)
        )

    def test_append_redirect_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("echo forged >> tmp/ref/comp/.gate-skip-log", tmp_path)
        )

    def test_dd_after_connector_blocked(self, tmp_path: Path) -> None:
        # Command-position: a vector after && must still be caught.
        assert self._denied(
            self._run(
                "cd repo && dd if=/dev/null of=tmp/ref/comp/.gate-skip-log", tmp_path
            )
        )

    def test_clobber_reason_names_enforcement_state(self, tmp_path: Path) -> None:
        r = self._run("dd if=/dev/null of=tmp/ref/comp/.gate-skip-log", tmp_path)
        assert self._denied(r), r.stdout
        reason = json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        assert "gate-skip-log" in reason and "enforcement-state" in reason.lower()

    def test_read_gate_skip_log_allowed(self, tmp_path: Path) -> None:
        # Reading the ledger (no destructive op) must not be blocked.
        assert not self._denied(self._run("cat tmp/ref/comp/.gate-skip-log", tmp_path))
        assert not self._denied(
            self._run("grep skip tmp/ref/comp/.gate-skip-log", tmp_path)
        )

    def test_cp_over_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("cp /dev/null tmp/ref/comp/.gate-skip-log", tmp_path)
        )

    def test_redirect_pipeline_state_case_variant_blocked(self, tmp_path: Path) -> None:
        # Case-insensitive-FS bypass (security-reviewer finding): on macOS APFS /
        # Windows NTFS `> Pipeline-State.JSON` clobbers the real pipeline-state.json.
        # The case-folded filename group must still deny it.
        assert self._denied(
            self._run("echo forged > tmp/ref/comp/Pipeline-State.JSON", tmp_path)
        )

    def test_cp_verify_stamp_case_variant_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("cp /dev/null tmp/ref/comp/VERIFY-STAMP.json", tmp_path)
        )

    def test_tee_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("echo x | tee tmp/ref/comp/.gate-skip-log", tmp_path)
        )

    def test_sed_inplace_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("sed -i '/.*/d' tmp/ref/comp/.gate-skip-log", tmp_path)
        )

    def test_mv_into_gate_skip_log_destination_blocked(self, tmp_path: Path) -> None:
        # The ledger as the mv DESTINATION (overwrite), not just as the source.
        assert self._denied(
            self._run("mv /tmp/empty tmp/ref/comp/.gate-skip-log", tmp_path)
        )

    # --- N5: directory-removal + permission-lock vectors (rmdir/rmtree/chmod) ---
    # Closing the crumb-DIR hole: the off-pipeline crumb is a DIRECTORY, so a single
    # rmtree/rmdir removes it without ever naming a file; chmod 000 / chattr +i
    # silences future appends without deleting. All token-anchored.

    def test_shell_rmdir_crumb_dir_blocked(self, tmp_path: Path) -> None:
        assert self._denied(self._run("rmdir tmp/.ui-re-external-browse", tmp_path))

    def test_shell_chmod_000_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("chmod 000 tmp/ref/comp/.gate-skip-log", tmp_path)
        )

    def test_shell_chmod_recursive_crumb_dir_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("chmod -R 000 tmp/.ui-re-external-browse", tmp_path)
        )

    def test_shell_chattr_immutable_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("chattr +i tmp/ref/comp/.gate-skip-log", tmp_path)
        )

    def test_find_exec_chmod_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run(
                "find tmp/ref -name .gate-skip-log -exec chmod 000 {} +", tmp_path
            )
        )

    def test_python_shutil_rmtree_crumb_dir_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run(
                "python3 -c \"import shutil; "
                "shutil.rmtree('tmp/.ui-re-external-browse')\"",
                tmp_path,
            )
        )

    def test_python_os_rmdir_crumb_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run(
                "python3 -c \"import os; os.rmdir('tmp/.ui-re-external-browse')\"",
                tmp_path,
            )
        )

    def test_python_os_chmod_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run(
                "python3 -c \"import os; "
                "os.chmod('tmp/ref/comp/.gate-skip-log', 0)\"",
                tmp_path,
            )
        )

    # --- N1: closeout provenance (verify-stamp.json / pipeline-state.json) ---
    # The Stop hook trusts these as evidence the gates ran; a direct bash write lets
    # the agent forge a writtenBy:"pipeline" terminal or a gates-covering stamp.

    def test_redirect_over_verify_stamp_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("echo '{}' > tmp/ref/comp/verify-stamp.json", tmp_path)
        )

    def test_redirect_over_pipeline_state_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run('echo forged >> tmp/ref/comp/pipeline-state.json', tmp_path)
        )

    def test_python_write_pipeline_state_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run(
                "python3 -c \"import pathlib; "
                "pathlib.Path('tmp/ref/comp/pipeline-state.json').write_text('{}')\"",
                tmp_path,
            )
        )

    def test_cp_over_verify_stamp_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("cp /tmp/forged.json tmp/ref/comp/verify-stamp.json", tmp_path)
        )

    def test_canonical_cli_writing_state_allowed(self, tmp_path: Path) -> None:
        # The canonical writers name the MODULE, not the file, so they are not
        # matched (the hook never sees a CLI's internal file writes).
        assert not self._denied(
            self._run("python3 -m ui_clone.state tmp/ref/comp terminal --status failed", tmp_path)
        )
        assert not self._denied(
            self._run("python3 -m ui_clone.pipeline x comp s verify --json", tmp_path)
        )

    def test_unrelated_rmdir_chmod_allowed(self, tmp_path: Path) -> None:
        # Token-anchored: a chmod/rmdir/rmtree of a non-enforcement path is untouched,
        # and the whole-ref reset (names the dir, not the crumb) stays allowed.
        assert not self._denied(self._run("chmod 644 build/out.css", tmp_path))
        assert not self._denied(self._run("rmdir build/empty", tmp_path))
        assert not self._denied(
            self._run(
                "python3 -c \"import shutil; shutil.rmtree('tmp/ref/comp')\"",
                tmp_path,
            )
        )

    def test_prose_commit_message_naming_ledger_allowed(self, tmp_path: Path) -> None:
        # An honest commit message that quotes destructive verbs + the file name
        # (e.g. describing THIS guard) must not be blocked.
        for msg in (
            "stop blocking rm of .gate-skip-log",
            "guard cat > .gate-skip-log and truncate/dd clobber",
        ):
            assert not self._denied(
                self._run(f'git commit -m "{msg}"', tmp_path)
            ), msg

    def test_ex_then_pipe_read_allowed(self, tmp_path: Path) -> None:
        # `ex` on something else, then a read of the ledger after a real pipe —
        # the ex matcher must not reach across the pipe to claim the read.
        assert not self._denied(
            self._run("ex --version | cat tmp/ref/comp/.gate-skip-log", tmp_path)
        )

    def test_command_wrapper_rm_blocked(self, tmp_path: Path) -> None:
        # `command rm` is a documented Headroom-bypass idiom here; the verb must
        # still be caught despite the wrapper shifting it off bare command position.
        assert self._denied(
            self._run("command rm tmp/ref/comp/.gate-skip-log", tmp_path)
        )

    def test_perl_inplace_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("perl -i -pe 's/.*//' tmp/ref/comp/.gate-skip-log", tmp_path)
        )

    def test_ln_sink_over_gate_skip_log_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("ln -sf /dev/null tmp/ref/comp/.gate-skip-log", tmp_path)
        )

    def test_heredoc_trailing_redirect_blocked(self, tmp_path: Path) -> None:
        # `cat <<EOF >ledger` writes a forged ledger; the opener-line redirect
        # must be caught even though the sanitized view drops that line.
        assert self._denied(
            self._run(
                "cat <<EOF >tmp/ref/comp/.gate-skip-log\nforged pass\nEOF", tmp_path
            )
        )

    # --- Fix B-2: sections/result.txt(+.json) closeout evidence ---
    # sections/result.txt carries the sha256-stamped section verdict; a direct
    # bash write lets the agent forge an all-PASS before verify stamps it.
    # Path-qualified (sections/result.*) so a build/result.txt is untouched.

    def test_redirect_over_section_result_txt_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run(
                'echo "**Result: 7 PASS, 0 FAIL" > tmp/ref/comp/sections/result.txt',
                tmp_path,
            )
        )

    def test_redirect_over_section_result_json_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("echo '{}' > tmp/ref/comp/sections/result.json", tmp_path)
        )

    def test_rm_section_result_txt_blocked(self, tmp_path: Path) -> None:
        assert self._denied(self._run("rm tmp/ref/comp/sections/result.txt", tmp_path))

    def test_tee_section_result_txt_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("echo x | tee tmp/ref/comp/sections/result.txt", tmp_path)
        )

    def test_bare_result_txt_outside_sections_allowed(self, tmp_path: Path) -> None:
        # Path-qualified: a result.txt NOT under sections/ is not closeout evidence.
        assert not self._denied(self._run("echo hi > build/result.txt", tmp_path))

    # --- Fix B-2: verification-plan.json gateSkipAck/deferredAck ---
    # The ack keys dissolve closeout blockers; a bash write that sets one
    # self-releases an un-enforced/deferred run. Block writes to
    # verification-plan.json that carry an ack key; leave ack-free writes alone.

    def test_redirect_verification_plan_gateskipack_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run(
                'echo \'{"gateSkipAck": "me"}\' > tmp/ref/comp/verification-plan.json',
                tmp_path,
            )
        )

    def test_redirect_verification_plan_deferredack_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run(
                'printf \'{"deferredAck":"x"}\' > tmp/ref/comp/verification-plan.json',
                tmp_path,
            )
        )

    def test_python_write_verification_plan_ack_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run(
                "python3 -c \"import pathlib; pathlib.Path("
                "'tmp/ref/comp/verification-plan.json').write_text("
                "'{\\\"gateSkipAck\\\": \\\"me\\\"}')\"",
                tmp_path,
            )
        )

    def test_verification_plan_write_without_ack_allowed(self, tmp_path: Path) -> None:
        # Writing the plan without touching an ack key is the legit regen path.
        assert not self._denied(
            self._run(
                'echo \'{"deferredChecks": []}\' > tmp/ref/comp/verification-plan.json',
                tmp_path,
            )
        )

    # --- generation-plan.json provenance ---
    # generation-plan.json carries sourceHashes/generatedAt provenance consumed by
    # downstream gates. Direct agent rewrites/removals must be denied; the
    # canonical generation-plan.sh writer names the script, not the artifact.

    def test_heredoc_generation_plan_provenance_rewrite_blocked(
        self, tmp_path: Path
    ) -> None:
        assert self._denied(
            self._run(
                "cat <<EOF > tmp/ref/comp/generation-plan.json\n"
                '{"provenance":{"generatedAt":"forged","sourceHashes":{}}}\n'
                "EOF",
                tmp_path,
            )
        )

    def test_python_generation_plan_source_hash_rewrite_blocked(
        self, tmp_path: Path
    ) -> None:
        assert self._denied(
            self._run(
                "python3 -c \"import pathlib; pathlib.Path("
                "'tmp/ref/comp/generation-plan.json').write_text("
                "'{\\\"provenance\\\":{\\\"sourceHashes\\\":{}}}')\"",
                tmp_path,
            )
        )

    def test_rm_generation_plan_blocked(self, tmp_path: Path) -> None:
        assert self._denied(self._run("rm tmp/ref/comp/generation-plan.json", tmp_path))

    def test_canonical_generation_plan_script_allowed(self, tmp_path: Path) -> None:
        assert not self._denied(
            self._run("bash scripts/extract/generation-plan.sh tmp/ref/comp", tmp_path)
        )

    # --- id17/id19: forgeable closeout stamps + driver-session id ---
    # structural-convergence-stamp.json / canvas-replay-stamp.json release the
    # Stop gate; .driver-session.id is the registered-driver identity. All are
    # produced by scripts/modules (never named literally at command level), so a
    # direct bash write to them is an agent forging closeout / a driver identity.

    def test_redirect_over_structural_convergence_stamp_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("echo '{}' > tmp/ref/comp/structural-convergence-stamp.json", tmp_path)
        )

    def test_redirect_over_canvas_replay_stamp_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("echo '{}' >> tmp/ref/comp/canvas-replay-stamp.json", tmp_path)
        )

    def test_redirect_over_driver_session_id_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("echo forged > tmp/ref/comp/.driver-session.id", tmp_path)
        )

    def test_cp_over_structural_convergence_stamp_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run("cp /tmp/forged.json tmp/ref/comp/structural-convergence-stamp.json", tmp_path)
        )

    def test_python_write_driver_session_id_blocked(self, tmp_path: Path) -> None:
        assert self._denied(
            self._run(
                "python3 -c \"import pathlib; "
                "pathlib.Path('tmp/ref/comp/.driver-session.id').write_text('x')\"",
                tmp_path,
            )
        )


def test_enforcement_state_target_unit_coverage() -> None:
    """Unit-level coverage of the extended guard so a regex break localizes to
    the parser rather than only failing through the dispatcher."""
    from ui_clone.hooks.pre_bash_rules.bash_write import _bash_enforcement_state_target

    g = "tmp/ref/comp/.gate-skip-log"
    a = "tmp/ref/comp/.ui-re-active"
    crumb = "tmp/.ui-re-external-browse/x.json"
    blocked = [
        f"cp /dev/null {g}",  # copy over the ledger
        f"echo x | tee {g}",  # tee write (verb after a pipe)
        f"echo x | tee -a {g}",
        f"sed -i '/.*/d' {g}",  # in-place edit (empties it)
        f"mv /tmp/empty {g}",  # ledger as DESTINATION (overwrite)
        f"mv {g} /tmp/x",  # ledger as source (moved aside)
        f"dd if=/dev/null of={g}",
        f"truncate -s 0 {g}",
        f"install -m644 /dev/null {g}",
        f"rsync -a /tmp/empty/ {g}",
        f"ex -s -c '%d|wq' {g}",
        f"python3 -c \"open('{g}','w')\"",
        f"python3 -c \"import os; os.truncate('{g}',0)\"",
        f"python3 -c \"import shutil; shutil.copy('/dev/null','{g}')\"",
        f"python3 -c \"import os; os.replace('e','{g}')\"",
        f"cat > {g} <<EOF\nx\nEOF",
        f"echo x >> {g}",
        f'dd if=/dev/null of="{g}"',  # quoted destination path
        f"ls ; truncate -s0 {g}",  # command-position after ;
        # off-pipeline crumbs are protected too, not just .gate-skip-log
        f"truncate -s0 {crumb}",
        f"echo x | tee {crumb}",
        f"rm {a}",
        # command-wrapper prefixes (`command`/`\\`/sudo/builtin/exec) must not
        # shift the verb off command position and bypass the guard.
        f"command rm {g}",
        f"\\rm {g}",
        f"sudo rm {g}",
        f"builtin rm {g}",
        f"command dd if=/dev/null of={g}",
        # perl -i / ln -sf in-place / sink-symlink clobber.
        f"perl -i -pe 's/.*//' {g}",
        f"perl -i -e 's/a/b/;s/c/d/' {g}",  # the perl script's own ';' must not cut it
        f"ln -sf /dev/null {g}",
        # heredoc whose OPENER line also redirects into the ledger (sanitized
        # view drops the opener line, so this is scanned raw).
        f"cat <<EOF >{g}\nforged pass\nEOF",
        f"cat <<EOF>{g}\nx\nEOF",
        f"cat <<'EOF' >{g}\nx\nEOF",  # quoted heredoc delimiter
        f"command \\rm {g}",  # wrapper + alias-escape together
        f"sudo \\rm {g}",
        f"echo x >| {g}",  # noclobber-override force-truncate (spaced)
        f"echo x >|{g}",  # ...and unspaced
        f"echo x 1> {g}",  # fd-prefixed redirect
        "find tmp/ref -name .gate-skip-log -exec rm {} +",  # -exec rm, not -delete
        "cat <<EOF > tmp/ref/comp/generation-plan.json\n"
        '{"provenance":{"generatedAt":"forged","sourceHashes":{}}}\n'
        "EOF",
        "python3 -c \"import pathlib; pathlib.Path('tmp/ref/comp/generation-plan.json').write_text('{}')\"",
        "rm tmp/ref/comp/generation-plan.json",
    ]
    for cmd in blocked:
        assert _bash_enforcement_state_target(cmd) is not None, cmd

    allowed = [
        f"cat {g}",  # read
        f"grep x {g}",  # read
        "rm -rf tmp/ref/comp",  # whole-dir reset names the dir, not the file
        "rm foo.txt",  # unrelated
        f"grep x {g} > /tmp/out",  # read ledger, redirect output elsewhere
        "cp -r tmp/ref/comp /backup",  # whole-dir copy names the dir, not the file
        f"ex --version | cat {g}",  # ex on something else, then a read after a pipe
        'git commit -m "stop blocking rm of .gate-skip-log"',  # prose rm
        'git commit -m "we mv .gate-skip-log aside in the reset path"',  # prose mv
        'git commit -m "cover cat > .gate-skip-log clobber"',  # prose redirect
        'git commit -m "guard truncate/dd of the .gate-skip-log"',  # prose verbs
        "git log --grep='rm .gate-skip-log'",  # searching history, not deleting
        # prose that quotes a wrapper/verb + the file name stays allowed.
        'git commit -m "use command rm on .gate-skip-log during reset"',
        'git commit -m "add perl -i guard for .gate-skip-log"',
        # prose/search that quotes the HEREDOC-redirect syntax must stay allowed
        # (the raw heredoc matcher is command-position anchored, not prose-blind).
        'git commit -m "block cat <<EOF >.gate-skip-log forging"',
        'git log --grep="cat <<EOF >.gate-skip-log"',
        "cat <<EOF >normal.txt\nbody\nEOF",  # heredoc redirecting elsewhere
        "bash scripts/extract/generation-plan.sh tmp/ref/comp",
        "echo '{}' > tmp/ref/comp/not-generation-plan.json",
        "echo '{}' > tmp/ref/comp/generation-plan.json.bak",
        "echo '{}' > tmp/ref/comp/xgeneration-plan.json",
    ]
    for cmd in allowed:
        assert _bash_enforcement_state_target(cmd) is None, cmd


def test_enforcement_state_target_blocks_python_read_conservatively() -> None:
    """Documented conservative over-block: ANY python command naming an
    enforcement file as a quoted literal is blocked, including a read. No
    legitimate flow shells out to python to touch these hook-managed dotfiles,
    so blocking python access wholesale is safer than trying to distinguish read
    from write (which would open mode-keyword / obfuscation bypasses)."""
    from ui_clone.hooks.pre_bash_rules.bash_write import _bash_enforcement_state_target

    cmd = "python3 -c \"print(open('tmp/ref/comp/.gate-skip-log').read())\""
    assert _bash_enforcement_state_target(cmd) is not None


def test_enforcement_state_deny_message_names_sanctioned_reads() -> None:
    """Plan-C friction fix: the guard over-blocks a read-only python open of an
    enforcement file (documented, intentional — distinguishing read from write
    reopens mode-keyword bypasses). Since the block is unavoidable, the deny
    MESSAGE must point the agent at the sanctioned READ tools (cat / jq / the
    status CLI) so a genuine inspection has an obvious non-blocked path instead
    of the agent thrashing against the write guard."""
    from ui_clone.hooks.pre_bash_rules.dispatcher import _guard_enforcement_state_rm

    msg = _guard_enforcement_state_rm(
        "python3 -c \"import json;json.load(open('tmp/ref/comp/pipeline-state.json'))\""
    )
    assert msg is not None
    low = msg.lower()
    # Names at least one sanctioned read tool and the status CLI.
    assert "cat" in low and "jq" in low, msg
    assert "status --json" in low or "status" in low, msg
    # Still frames it as a read-vs-write clarification.
    assert "read" in low, msg
