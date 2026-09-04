from __future__ import annotations

import json
import subprocess
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

    def _patch_input(self, patch: str) -> str:
        return json.dumps({"tool_name": "apply_patch", "tool_input": {"patch": patch}})

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

    def test_apply_patch_payload_runs_gate_and_blocks_component_write(
        self, tmp_path: Path
    ) -> None:
        """Codex apply_patch payloads may only expose a patch body, not file_path."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        write_extracted_json(ref_dir)  # incomplete pre-generate artifacts

        patch = (
            "*** Begin Patch\n"
            f"*** Update File: {tmp_path / 'src/components/Hero.tsx'}\n"
            "@@\n"
            "-export const Hero = () => null\n"
            "+export const Hero = () => <section />\n"
            "*** End Patch\n"
        )
        result = run_hook(
            self.MODULE,
            stdin_data=self._patch_input(patch),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"Expected deny JSON, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
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


class TestPreGenerateCloseoutProvenanceGuard:
    """N1 residual: Write/Edit/MultiEdit/apply_patch can forge closeout
    provenance in pipeline-state.json / verify-stamp.json. The bash write-guard
    (bash_write.py) blocks shell writes to those files, but the Write/Edit tools
    route through pre_generate, which did not guard them — so an agent could Edit
    pipeline-state.json to set terminalState.writtenBy="pipeline" (a self-attested
    provenance forge on a security boundary). These tests reproduce the hole and
    pin the fix.
    """

    MODULE = "ui_clone.hooks.pre_generate"

    def _edit_input(self, file_path: str, old_string: str, new_string: str) -> str:
        return json.dumps(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": file_path,
                    "old_string": old_string,
                    "new_string": new_string,
                },
            }
        )

    def _write_input(self, file_path: str, content: str) -> str:
        return json.dumps(
            {"tool_name": "Write", "tool_input": {"file_path": file_path, "content": content}}
        )

    def _multiedit_input(self, file_path: str, edits: list[dict[str, str]]) -> str:
        return json.dumps(
            {"tool_name": "MultiEdit", "tool_input": {"file_path": file_path, "edits": edits}}
        )

    def _patch_input(self, patch: str) -> str:
        return json.dumps({"tool_name": "apply_patch", "tool_input": {"patch": patch}})

    def _run(self, tmp_path: Path, stdin_data: str) -> subprocess.CompletedProcess[str]:
        return run_hook(
            self.MODULE,
            stdin_data=stdin_data,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

    def _assert_denied(self, result: subprocess.CompletedProcess[str]) -> None:
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"Expected deny JSON, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny", out

    def _assert_allowed(self, result: subprocess.CompletedProcess[str]) -> None:
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "permissionDecision" not in result.stdout, result.stdout

    def _ref_state_path(self, tmp_path: Path, name: str) -> str:
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        return str(ref_dir / name)

    # ── The hole: forging terminalState provenance ──

    def test_edit_pipeline_state_terminal_forge_denied(self, tmp_path: Path) -> None:
        path = self._ref_state_path(tmp_path, "pipeline-state.json")
        new_string = '"terminalState": {"status": "pass", "writtenBy": "pipeline"}'
        result = self._run(tmp_path, self._edit_input(path, '"current_gate": "done"', new_string))
        self._assert_denied(result)

    def test_write_pipeline_state_terminal_forge_denied(self, tmp_path: Path) -> None:
        path = self._ref_state_path(tmp_path, "pipeline-state.json")
        content = json.dumps(
            {"component": "x", "terminalState": {"status": "pass", "writtenBy": "pipeline"}}
        )
        result = self._run(tmp_path, self._write_input(path, content))
        self._assert_denied(result)

    def test_write_pipeline_state_snake_terminal_forge_denied(self, tmp_path: Path) -> None:
        path = self._ref_state_path(tmp_path, "pipeline-state.json")
        content = json.dumps({"terminal_state": {"status": "pass", "written_by": "pipeline"}})
        result = self._run(tmp_path, self._write_input(path, content))
        self._assert_denied(result)

    def test_multiedit_pipeline_state_terminal_forge_denied(self, tmp_path: Path) -> None:
        path = self._ref_state_path(tmp_path, "pipeline-state.json")
        edits = [
            {"old_string": '"last_updated": "a"', "new_string": '"last_updated": "b"'},
            {"old_string": "}", "new_string": ', "writtenBy": "pipeline"}'},
        ]
        result = self._run(tmp_path, self._multiedit_input(path, edits))
        self._assert_denied(result)

    def test_apply_patch_pipeline_state_terminal_forge_denied(self, tmp_path: Path) -> None:
        path = self._ref_state_path(tmp_path, "pipeline-state.json")
        patch = (
            "*** Begin Patch\n"
            f"*** Update File: {path}\n"
            "@@\n"
            '-  "current_gate": "done"\n'
            '+  "current_gate": "done",\n'
            '+  "terminalState": {"status": "pass", "writtenBy": "pipeline"}\n'
            "*** End Patch\n"
        )
        result = self._run(tmp_path, self._patch_input(patch))
        self._assert_denied(result)

    def test_edit_pipeline_state_case_variant_forge_denied(self, tmp_path: Path) -> None:
        """Case-insensitive-FS bypass (security-reviewer finding): on macOS APFS /
        Windows NTFS a write to `Pipeline-State.JSON` lands on the real
        `pipeline-state.json`. A case-sensitive basename compare would miss it;
        the case-folded compare must still deny the forge."""
        path = self._ref_state_path(tmp_path, "Pipeline-State.JSON")
        new_string = '"terminalState": {"status": "pass", "writtenBy": "pipeline"}'
        result = self._run(tmp_path, self._edit_input(path, '"current_gate": "done"', new_string))
        self._assert_denied(result)

    def test_write_verify_stamp_case_variant_denied(self, tmp_path: Path) -> None:
        path = self._ref_state_path(tmp_path, "VERIFY-STAMP.json")
        result = self._run(tmp_path, self._write_input(path, json.dumps({"gatesCovered": []})))
        self._assert_denied(result)

    # ── verify-stamp.json has no legitimate hand-edit path ──

    def test_write_verify_stamp_denied(self, tmp_path: Path) -> None:
        path = self._ref_state_path(tmp_path, "verify-stamp.json")
        result = self._run(tmp_path, self._write_input(path, json.dumps({"gatesCovered": []})))
        self._assert_denied(result)

    def test_edit_verify_stamp_denied(self, tmp_path: Path) -> None:
        path = self._ref_state_path(tmp_path, "verify-stamp.json")
        result = self._run(tmp_path, self._edit_input(path, "{}", '{"ok": true}'))
        self._assert_denied(result)

    # ── The documented legitimate path must keep working ──

    def test_edit_pipeline_state_closeout_policy_allowed(self, tmp_path: Path) -> None:
        """canvas-replay-mode.md Step 3: operator edits pipeline-state.json to set
        closeoutPolicy. This edit touches no provenance key → must be allowed."""
        path = self._ref_state_path(tmp_path, "pipeline-state.json")
        new_string = '"current_gate": "post-implement",\n  "closeoutPolicy": "canvas-replay"'
        result = self._run(
            tmp_path, self._edit_input(path, '"current_gate": "post-implement"', new_string)
        )
        self._assert_allowed(result)

    def test_write_unrelated_json_allowed(self, tmp_path: Path) -> None:
        """A write to a differently-named JSON (outside tmp/ref) mentioning
        terminalState in prose is not an enforcement-state file → not this
        guard's concern (and not an ad-hoc ref artifact either)."""
        path = str(tmp_path / "notes.json")
        result = self._run(
            tmp_path, self._write_input(path, json.dumps({"note": "terminalState writtenBy"}))
        )
        self._assert_allowed(result)

    # ── Fix B-2: sections/result.txt(+.json) closeout evidence ──
    # sections/result.txt carries the sha256-stamped section verdict the
    # post-implement gate trusts; hand-writing an all-PASS before verify mints a
    # genuine stamp over forged bytes. It is written by section-compare (Bash,
    # path built from the ref-dir arg), never hand-edited → deny tool writes.

    def _ref_sections_path(self, tmp_path: Path, name: str) -> str:
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        return str(ref_dir / "sections" / name)

    def test_write_section_result_txt_denied(self, tmp_path: Path) -> None:
        path = self._ref_sections_path(tmp_path, "result.txt")
        content = "**Result: 7 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY"
        result = self._run(tmp_path, self._write_input(path, content))
        self._assert_denied(result)

    def test_edit_section_result_txt_denied(self, tmp_path: Path) -> None:
        path = self._ref_sections_path(tmp_path, "result.txt")
        result = self._run(
            tmp_path, self._edit_input(path, "1 FAIL", "0 FAIL")
        )
        self._assert_denied(result)

    def test_write_section_result_json_denied(self, tmp_path: Path) -> None:
        path = self._ref_sections_path(tmp_path, "result.json")
        result = self._run(tmp_path, self._write_input(path, json.dumps({"summary": {"fail": 0}})))
        self._assert_denied(result)

    # ── capture-artifact-inventory.json ──
    # The reference gate's _has_inventory_capture_provenance trusts this file's
    # "status": "pass" and re-checks only path containment and byte sizes; the
    # raster decode / blank / identical-state verdicts stay in the checker. A
    # hand-written pass next to two real images therefore buys "real detection"
    # without the checker ever running.

    def test_write_capture_artifact_inventory_denied(self, tmp_path: Path) -> None:
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        path = str(ref_dir / "capture-artifact-inventory.json")
        content = json.dumps({"schemaVersion": 1, "status": "pass", "regionsChecked": 1})
        result = self._run(tmp_path, self._write_input(path, content))
        self._assert_denied(result)

    def test_edit_capture_artifact_inventory_denied(self, tmp_path: Path) -> None:
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        path = str(ref_dir / "capture-artifact-inventory.json")
        result = self._run(tmp_path, self._edit_input(path, '"fail"', '"pass"'))
        self._assert_denied(result)

    def test_write_bare_result_txt_outside_sections_allowed(self, tmp_path: Path) -> None:
        """Path-qualified: a result.txt NOT under sections/ is not closeout
        evidence and must not be over-blocked."""
        path = str(tmp_path / "build" / "result.txt")
        result = self._run(tmp_path, self._write_input(path, "ok"))
        self._assert_allowed(result)

    # ── Fix B-2: verification-plan.json gateSkipAck/deferredAck ──
    # The ack keys dissolve closeout blockers (gate_skip_blocker /
    # deferred_checks_blocker). Agent-set acks self-release un-enforced runs →
    # deny tool writes touching them. A write NOT touching the ack keys (legit
    # plan regen) stays allowed.

    def test_edit_verification_plan_gateskipack_denied(self, tmp_path: Path) -> None:
        path = self._ref_state_path(tmp_path, "verification-plan.json")
        result = self._run(
            tmp_path,
            self._edit_input(path, '"tier": "standard"', '"tier": "standard", "gateSkipAck": "me"'),
        )
        self._assert_denied(result)

    def test_write_verification_plan_deferredack_denied(self, tmp_path: Path) -> None:
        path = self._ref_state_path(tmp_path, "verification-plan.json")
        result = self._run(
            tmp_path, self._write_input(path, json.dumps({"deferredAck": "me", "tier": "standard"}))
        )
        self._assert_denied(result)

    def test_write_verification_plan_without_ack_allowed(self, tmp_path: Path) -> None:
        """deferredChecks (the tracked debt list) is not an ack key — a plan
        write that lists deferred checks without an ack must stay allowed."""
        path = self._ref_state_path(tmp_path, "verification-plan.json")
        result = self._run(
            tmp_path,
            self._write_input(path, json.dumps({"deferredChecks": [], "tier": "standard"})),
        )
        self._assert_allowed(result)

    # ── id17/id19: forgeable closeout stamps + .driver-session.id ──
    # These have no legitimate hand-edit path (produced by check-converged.sh /
    # check-canvas-replay.sh / register-driver-session.sh), so any tool write is a
    # closeout / driver-identity forge — deny it like verify-stamp.json.

    def test_write_structural_convergence_stamp_denied(self, tmp_path: Path) -> None:
        path = self._ref_state_path(tmp_path, "structural-convergence-stamp.json")
        result = self._run(tmp_path, self._write_input(path, json.dumps({"converged": True})))
        self._assert_denied(result)

    def test_edit_canvas_replay_stamp_denied(self, tmp_path: Path) -> None:
        path = self._ref_state_path(tmp_path, "canvas-replay-stamp.json")
        result = self._run(tmp_path, self._edit_input(path, "{}", '{"ok": true}'))
        self._assert_denied(result)

    def test_write_driver_session_id_denied(self, tmp_path: Path) -> None:
        path = self._ref_state_path(tmp_path, ".driver-session.id")
        result = self._run(tmp_path, self._write_input(path, "forged-session-id"))
        self._assert_denied(result)

    def test_write_structural_convergence_stamp_case_variant_denied(self, tmp_path: Path) -> None:
        path = self._ref_state_path(tmp_path, "Structural-Convergence-Stamp.JSON")
        result = self._run(tmp_path, self._write_input(path, json.dumps({"converged": True})))
        self._assert_denied(result)
