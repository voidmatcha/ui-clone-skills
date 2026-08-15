from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from ui_clone import claude_continuation as cc
from ui_clone.hooks import section_gate
from ui_clone.hooks._common import mark_ref_session

from ._helpers import (
    make_ref_dir,
    make_search_root,
    run_hook,
    set_active_marker,
)


class TestSectionGate:
    MODULE = "ui_clone.hooks.section_gate"
    SESSION_ID = "session-1"
    CRON_ID = "cron-1"

    def _cron_row(
        self,
        tmp_path: Path,
        session_id: str | None = None,
        cron_id: str | None = None,
    ) -> dict[str, object]:
        session = session_id or self.SESSION_ID
        receipt = cc.load_receipt(tmp_path, session)
        assert receipt is not None
        return {
            "id": cron_id or self.CRON_ID,
            "schedule": "* * * * *",
            "recurring": False,
            "prompt": cc.continuation_prompt(receipt),
        }

    def _blocking_stop(
        self,
        tmp_path: Path,
        session_id: str | None = None,
        session_crons: object = None,
        include_session_crons: bool = True,
        ref_dir: Path | None = None,
        hook_host: str = "claude",
    ) -> tuple[dict[str, object], Path]:
        if ref_dir is None:
            search_root = make_search_root(tmp_path)
            ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        mark_ref_session(ref_dir, session_id or self.SESSION_ID, source="test")
        payload: dict[str, object] = {
            "hook_event_name": "Stop",
            "session_id": session_id or self.SESSION_ID,
        }
        if include_session_crons:
            payload["session_crons"] = session_crons
        result = run_hook(
            self.MODULE,
            stdin_data=json.dumps(payload),
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "UI_CLONE_HOOK_HOST": hook_host,
            },
        )
        assert result.returncode == 0
        data = json.loads(result.stdout.strip())
        assert data.get("decision") == "block"
        return data, ref_dir

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

    def test_markerless_probe_does_not_borrow_repo_root_impl(self, tmp_path: Path) -> None:
        """A diagnostic ref without pipeline state must not inherit root impl/."""
        search_root = make_search_root(tmp_path)
        probe = make_ref_dir(search_root, "section-map-probe")
        (probe / "section-map.json").write_text('{"sections": []}', encoding="utf-8")
        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text(
            "export default function App(){return <main />}", encoding="utf-8"
        )

        result = run_hook(
            self.MODULE,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        assert result.stdout.strip() == "", result.stdout

    def test_markerless_legacy_pipeline_state_can_borrow_repo_root_impl(
        self, tmp_path: Path
    ) -> None:
        """A real legacy run without impl_root remains fail-closed."""
        import datetime

        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, "legacy-run")
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "legacy-run",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": ["reference", "extraction"],
                    "current_gate": "post-implement",
                    "last_updated": datetime.datetime.now(datetime.UTC).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                }
            ),
            encoding="utf-8",
        )
        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text(
            "export default function App(){return <main />}", encoding="utf-8"
        )

        result = run_hook(
            self.MODULE,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, "legacy markerless refs must remain fail-closed"
        data = json.loads(out)
        assert data.get("decision") == "block"
        assert "verify-stamp.json" in data.get("reason", "")

    def test_markerless_clone_structure_can_borrow_repo_root_impl(
        self, tmp_path: Path
    ) -> None:
        """A no-state clone with captured DOM structure remains fail-closed."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, "off-pipeline-clone")
        (ref_dir / "structure.json").write_text(
            '{"tag": "body", "children": []}', encoding="utf-8"
        )
        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text(
            "export default function App(){return <main />}", encoding="utf-8"
        )

        result = run_hook(
            self.MODULE,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, "clone-shaped markerless refs must remain fail-closed"
        data = json.loads(out)
        assert data.get("decision") == "block"
        assert "verify-stamp.json" in data.get("reason", "")

    def test_markerless_clone_does_not_borrow_foreign_repo_root_impl(
        self, tmp_path: Path
    ) -> None:
        """A root impl owned by another ref must not block a fresh clone."""
        search_root = make_search_root(tmp_path)
        stale_ref = make_ref_dir(search_root, "stale-run")
        fresh_ref = make_ref_dir(search_root, "fresh-run")
        (fresh_ref / "structure.json").write_text(
            '{"tag": "body", "children": []}', encoding="utf-8"
        )
        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text(
            "export default function App(){return <main />}", encoding="utf-8"
        )
        (tmp_path / "impl" / ".ref-dir").write_text(
            str(stale_ref) + "\n", encoding="utf-8"
        )

        result = run_hook(
            self.MODULE,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        assert result.stdout.strip() == "", result.stdout

    @pytest.mark.parametrize("binding_source", ["marker", "state"])
    @pytest.mark.parametrize(
        ("target_name", "expect_block"),
        [("ref-b", False), ("ref-a", True)],
    )
    def test_explicit_impl_binding_only_blocks_owning_ref(
        self,
        tmp_path: Path,
        binding_source: str,
        target_name: str,
        expect_block: bool,
    ) -> None:
        """An explicit impl binding must follow the impl's ref ownership."""
        project_root = tmp_path / f"{binding_source}-{target_name}"
        search_root = make_search_root(project_root)
        owner_ref = make_ref_dir(search_root, "ref-a")
        target_ref = make_ref_dir(search_root, target_name)
        (target_ref / "structure.json").write_text(
            '{"tag": "body", "children": []}', encoding="utf-8"
        )

        impl_dir = project_root / "impl"
        src = impl_dir / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text(
            "export default function App(){return <main />}", encoding="utf-8"
        )
        (impl_dir / ".ref-dir").write_text(
            str(owner_ref.resolve()) + "\n", encoding="utf-8"
        )

        if binding_source == "marker":
            (target_ref / ".impl-root").write_text(
                str(impl_dir.resolve()) + "\n", encoding="utf-8"
            )
        else:
            (target_ref / "pipeline-state.json").write_text(
                json.dumps(
                    {
                        "component": target_ref.name,
                        "current_gate": "post-implement",
                        "implRoot": str(impl_dir.resolve()),
                    }
                ),
                encoding="utf-8",
            )

        result = run_hook(
            self.MODULE,
            env={"CLAUDE_PROJECT_DIR": str(project_root)},
        )

        assert result.returncode == 0
        if not expect_block:
            assert result.stdout.strip() == "", result.stdout
            return
        data = json.loads(result.stdout.strip())
        assert data.get("decision") == "block"
        assert str(owner_ref) in data.get("reason", "")
        assert "verify-stamp.json" in data.get("reason", "")

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

    def _arm_for_ref(self, tmp_path: Path) -> Path:
        ref_dir = make_ref_dir(make_search_root(tmp_path))
        cc.activate(tmp_path, self.SESSION_ID, cc.UI_RE_SKILL)
        cc.bind_ref(tmp_path, self.SESSION_ID, ref_dir)
        cc.arm(tmp_path, self.SESSION_ID)
        return ref_dir

    def _markerless_bound_incomplete_ref(self, tmp_path: Path) -> Path:
        ref_dir = make_ref_dir(make_search_root(tmp_path), "capture-only")
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "capture-only",
                    "started_at": "2026-08-15T11:10:11Z",
                    "completed_steps": ["reference"],
                    "current_gate": "extraction",
                    "last_updated": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                    ),
                    "gate_fail_counts": {},
                    "unclonable_reasons": [],
                }
            ),
            encoding="utf-8",
        )
        cc.activate(tmp_path, self.SESSION_ID, cc.UI_RE_SKILL)
        cc.bind_ref(tmp_path, self.SESSION_ID, ref_dir)
        return ref_dir

    def _write_passing_extraction_artifacts(self, ref_dir: Path) -> None:
        payloads: dict[str, object] = {
            "structure.json": {"tag": "body", "children": []},
            "head.json": {"title": "Fixture"},
            "styles.json": {"body": {"display": "block"}},
            "fonts.json": {"fonts": ["system-ui"]},
            "visible-images.json": {"images": []},
            "inline-svgs.json": [],
            "body-state.json": {"className": ""},
            "design-bundles.json": {"bundles": []},
        }
        for filename, payload in payloads.items():
            (ref_dir / filename).write_text(
                json.dumps(payload, indent=2) + "\n",
                encoding="utf-8",
            )
        css_dir = ref_dir / "css"
        css_dir.mkdir()
        (css_dir / "variables.txt").write_text(
            ":root { --fixture-color: #fff; }\n",
            encoding="utf-8",
        )

    def test_valid_empty_cron_snapshot_pauses_missing_armed_job(
        self, tmp_path: Path
    ) -> None:
        ref_dir = self._arm_for_ref(tmp_path)
        cc.mark_armed(tmp_path, self.SESSION_ID, self.CRON_ID)

        data, _ = self._blocking_stop(tmp_path, session_crons=[], ref_dir=ref_dir)

        receipt = cc.load_receipt(tmp_path, self.SESSION_ID)
        assert receipt is not None
        assert receipt["state"] == cc.STATE_PAUSED
        reason = str(data["reason"])
        assert "continuation one-shot is paused" in reason
        assert "UI-RE Gate" in reason

    @pytest.mark.parametrize("session_crons", [None, "unavailable", [{"id": 3}]])
    def test_absent_or_malformed_cron_snapshot_does_not_pause_armed_receipt(
        self, tmp_path: Path, session_crons: object
    ) -> None:
        ref_dir = self._arm_for_ref(tmp_path)
        cc.mark_armed(tmp_path, self.SESSION_ID, self.CRON_ID)

        if session_crons is None:
            self._blocking_stop(
                tmp_path,
                include_session_crons=False,
                ref_dir=ref_dir,
            )
        else:
            self._blocking_stop(
                tmp_path,
                session_crons=session_crons,
                ref_dir=ref_dir,
            )

        receipt = cc.load_receipt(tmp_path, self.SESSION_ID)
        assert receipt is not None
        assert receipt["state"] == cc.STATE_ARMED
        assert receipt["cronId"] == self.CRON_ID

    def test_exact_matching_cron_snapshot_arms_arming_receipt(
        self, tmp_path: Path
    ) -> None:
        ref_dir = self._arm_for_ref(tmp_path)
        row = self._cron_row(tmp_path)

        data, _ = self._blocking_stop(
            tmp_path,
            session_crons=[row],
            ref_dir=ref_dir,
        )

        receipt = cc.load_receipt(tmp_path, self.SESSION_ID)
        assert receipt is not None
        assert receipt["state"] == cc.STATE_ARMED
        assert receipt["cronId"] == self.CRON_ID
        assert "CronCreate" not in str(data["reason"])
        assert "end the current assistant turn" in str(data["reason"])

    def test_exact_matching_cron_snapshot_confirms_armed_receipt(
        self, tmp_path: Path
    ) -> None:
        ref_dir = self._arm_for_ref(tmp_path)
        cc.mark_armed(tmp_path, self.SESSION_ID, self.CRON_ID)
        row = self._cron_row(tmp_path)

        data, _ = self._blocking_stop(
            tmp_path,
            session_crons=[row],
            ref_dir=ref_dir,
        )

        receipt = cc.load_receipt(tmp_path, self.SESSION_ID)
        assert receipt is not None
        assert receipt["state"] == cc.STATE_ARMED
        assert receipt["cronId"] == self.CRON_ID
        assert "duplicate continuation scheduled tasks" not in str(data["reason"])
        assert "CronCreate" not in str(data["reason"])

    def test_duplicate_matching_cron_snapshot_prefixes_delete_guidance(
        self, tmp_path: Path
    ) -> None:
        ref_dir = self._arm_for_ref(tmp_path)
        row_a = self._cron_row(tmp_path, cron_id="cron-a")
        row_b = self._cron_row(tmp_path, cron_id="cron-b")

        data, _ = self._blocking_stop(
            tmp_path,
            session_crons=[row_a, row_b],
            ref_dir=ref_dir,
        )

        receipt = cc.load_receipt(tmp_path, self.SESSION_ID)
        assert receipt is not None
        assert receipt["state"] == cc.STATE_ARMING
        reason = str(data["reason"])
        assert "duplicate continuation scheduled tasks" in reason
        assert "CronDelete" in reason
        assert "UI-RE Gate" in reason

    def test_unrelated_cron_rows_do_not_arm_arming_receipt(
        self, tmp_path: Path
    ) -> None:
        ref_dir = self._arm_for_ref(tmp_path)
        unrelated = {
            "id": "other-cron",
            "schedule": "* * * * *",
            "recurring": False,
            "prompt": "wake [[UI_RE_CONTINUATION:other]]",
        }

        data, _ = self._blocking_stop(
            tmp_path,
            session_crons=[unrelated],
            ref_dir=ref_dir,
        )

        receipt = cc.load_receipt(tmp_path, self.SESSION_ID)
        assert receipt is not None
        assert receipt["state"] == cc.STATE_ARMING
        assert "CronCreate" in str(data["reason"])

    def test_paused_receipt_never_reactivates_from_matching_snapshot(
        self, tmp_path: Path
    ) -> None:
        ref_dir = self._arm_for_ref(tmp_path)
        cc.mark_armed(tmp_path, self.SESSION_ID, self.CRON_ID)
        row = self._cron_row(tmp_path)
        cc.finish_owned_delete(tmp_path, self.SESSION_ID)

        data, _ = self._blocking_stop(
            tmp_path,
            session_crons=[row],
            ref_dir=ref_dir,
        )

        receipt = cc.load_receipt(tmp_path, self.SESSION_ID)
        assert receipt is not None
        assert receipt["state"] == cc.STATE_PAUSED
        reason = str(data["reason"])
        assert "paused" in reason
        assert "CronDelete" in reason

    def test_first_incomplete_stop_creates_binds_and_arms_one_shot(
        self, tmp_path: Path
    ) -> None:
        data, ref_dir = self._blocking_stop(tmp_path, session_crons=[])

        receipt = cc.load_receipt(tmp_path, self.SESSION_ID)
        assert receipt is not None
        assert receipt["state"] == cc.STATE_ARMING
        assert receipt["refDir"] == ref_dir.relative_to(tmp_path).as_posix()
        create_input = cc.cron_create_input(receipt)
        assert create_input["recurring"] is False
        assert create_input["durable"] is False
        reason = str(data["reason"])
        assert reason.startswith("⛔ UI-RE continuation one-shot is arming")
        assert "CronCreate" in reason
        assert json.dumps(create_input, sort_keys=True) in reason
        assert "UI-RE Gate" in reason

    def test_markerless_bound_incomplete_ref_arms_claude_one_shot(
        self, tmp_path: Path
    ) -> None:
        self._markerless_bound_incomplete_ref(tmp_path)

        result = run_hook(
            self.MODULE,
            stdin_data=json.dumps(
                {
                    "hook_event_name": "Stop",
                    "session_id": self.SESSION_ID,
                    "session_crons": [],
                }
            ),
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "UI_CLONE_HOOK_HOST": "claude",
            },
        )

        assert result.returncode == 0
        assert result.stdout.strip(), "the exact bound incomplete ref must engage Stop"
        data = json.loads(result.stdout.strip())
        assert data.get("decision") == "block"
        receipt = cc.load_receipt(tmp_path, self.SESSION_ID)
        assert receipt is not None
        assert receipt["state"] == cc.STATE_ARMING
        reason = str(data["reason"])
        assert "continuation one-shot is arming" in reason
        assert '"recurring": false' in reason
        assert '"durable": false' in reason

    def test_markerless_bound_passing_gate_during_state_transition_still_arms(
        self, tmp_path: Path
    ) -> None:
        ref_dir = self._markerless_bound_incomplete_ref(tmp_path)
        self._write_passing_extraction_artifacts(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data=json.dumps(
                {
                    "hook_event_name": "Stop",
                    "session_id": self.SESSION_ID,
                    "session_crons": [],
                }
            ),
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "UI_CLONE_HOOK_HOST": "claude",
            },
        )

        assert result.returncode == 0
        assert result.stdout.strip(), (
            "a passing pre-generation gate must not release Stop before "
            "pipeline-state.json advances"
        )
        data = json.loads(result.stdout.strip())
        assert data.get("decision") == "block"
        receipt = cc.load_receipt(tmp_path, self.SESSION_ID)
        assert receipt is not None
        assert receipt["state"] == cc.STATE_ARMING
        state = section_gate.PipelineState.load(ref_dir)
        assert state.current_gate == "bundle"
        assert "extraction" in state.completed_steps
        reason = str(data["reason"])
        assert "continuation one-shot is arming" in reason
        assert "pipeline-state.json" in reason
        assert "extraction" in reason

    def test_markerless_bound_incomplete_ref_is_not_a_codex_activation_path(
        self, tmp_path: Path
    ) -> None:
        self._markerless_bound_incomplete_ref(tmp_path)

        result = run_hook(
            self.MODULE,
            stdin_data=json.dumps(
                {"hook_event_name": "Stop", "session_id": self.SESSION_ID}
            ),
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "UI_CLONE_HOOK_HOST": "codex",
            },
        )

        assert result.returncode == 0
        assert result.stdout.strip() == ""
        receipt = cc.load_receipt(tmp_path, self.SESSION_ID)
        assert receipt is not None
        assert receipt["state"] == cc.STATE_RUNNING

    def test_codex_stop_keeps_shared_gate_without_claude_continuation(
        self, tmp_path: Path
    ) -> None:
        data, _ = self._blocking_stop(
            tmp_path,
            session_crons=[],
            hook_host="codex",
        )

        assert not cc.receipt_path(tmp_path, self.SESSION_ID).exists()
        reason = str(data["reason"])
        assert reason.startswith("⛔ UI-RE Gate")
        assert "continuation one-shot" not in reason
        assert "CronCreate" not in reason

    def test_running_receipt_arms_only_after_gate_is_incomplete(
        self, tmp_path: Path
    ) -> None:
        cc.activate(tmp_path, self.SESSION_ID, cc.UI_RE_SKILL)

        data, _ = self._blocking_stop(tmp_path, session_crons=[])

        receipt = cc.load_receipt(tmp_path, self.SESSION_ID)
        assert receipt is not None
        assert receipt["state"] == cc.STATE_ARMING
        reason = str(data["reason"])
        assert '"recurring": false' in reason
        assert "CronCreate" in reason
        assert "UI-RE Gate" in reason

    def test_armed_receipt_does_not_create_a_second_job(self, tmp_path: Path) -> None:
        ref_dir = self._arm_for_ref(tmp_path)
        cc.mark_armed(tmp_path, self.SESSION_ID, self.CRON_ID)

        data, _ = self._blocking_stop(
            tmp_path,
            session_crons=[self._cron_row(tmp_path)],
            ref_dir=ref_dir,
        )

        reason = str(data["reason"])
        assert "CronCreate" not in reason
        assert "end the current assistant turn" in reason

    def test_unsupported_receipt_keeps_ordinary_one_nudge_gate(
        self, tmp_path: Path
    ) -> None:
        ref_dir = self._arm_for_ref(tmp_path)
        cc.mark_unsupported(
            tmp_path,
            self.SESSION_ID,
            "CronCreate unavailable",
        )

        data, _ = self._blocking_stop(
            tmp_path,
            include_session_crons=False,
            ref_dir=ref_dir,
        )

        receipt = cc.load_receipt(tmp_path, self.SESSION_ID)
        assert receipt is not None
        assert receipt["state"] == cc.STATE_UNSUPPORTED
        reason = str(data["reason"])
        assert reason.startswith("⛔ UI-RE Gate")
        assert "continuation state" not in reason
        assert "CronCreate" not in reason

    def test_running_receipt_adopts_exact_existing_one_shot_without_recreating(
        self, tmp_path: Path
    ) -> None:
        ref_dir = self._arm_for_ref(tmp_path)
        row = self._cron_row(tmp_path)
        cc.mark_armed(tmp_path, self.SESSION_ID, self.CRON_ID)
        cc.accept_wake(tmp_path, self.SESSION_ID, str(row["prompt"]))

        data, _ = self._blocking_stop(
            tmp_path,
            session_crons=[row],
            ref_dir=ref_dir,
        )

        receipt = cc.load_receipt(tmp_path, self.SESSION_ID)
        assert receipt is not None
        assert receipt["state"] == cc.STATE_ARMED
        assert receipt["cronId"] == self.CRON_ID
        reason = str(data["reason"])
        assert "CronCreate" not in reason
        assert "end the current assistant turn" in reason

    def test_missing_receipt_recovers_exact_existing_one_shot_without_recreating(
        self, tmp_path: Path
    ) -> None:
        ref_dir = self._arm_for_ref(tmp_path)
        row = self._cron_row(tmp_path)
        cc.receipt_path(tmp_path, self.SESSION_ID).unlink()

        data, _ = self._blocking_stop(
            tmp_path,
            session_crons=[row],
            ref_dir=ref_dir,
        )

        receipt = cc.load_receipt(tmp_path, self.SESSION_ID)
        assert receipt is not None
        assert receipt["state"] == cc.STATE_ARMED
        assert receipt["cronId"] == self.CRON_ID
        reason = str(data["reason"])
        assert "CronCreate" not in reason
        assert "end the current assistant turn" in reason

    def test_completed_stop_refreshes_receipt_from_canonical_goal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ref_dir = tmp_path / "tmp" / "ref" / "demo"
        ref_dir.mkdir(parents=True)
        cc.activate(tmp_path, self.SESSION_ID, cc.UI_RE_SKILL)
        cc.bind_ref(tmp_path, self.SESSION_ID, ref_dir)
        monkeypatch.setattr(
            cc.subprocess,
            "run",
            lambda cmd, **kwargs: subprocess.CompletedProcess(
                cmd,
                0,
                stdout="DONE verify-stamp.json",
                stderr="",
            ),
        )

        section_gate._refresh_continuation_final(tmp_path, self.SESSION_ID, ref_dir)

        receipt = cc.load_receipt(tmp_path, self.SESSION_ID)
        assert receipt is not None
        assert receipt["state"] == cc.STATE_COMPLETE

    def test_stop_hook_active_releases_without_recreating_continuation(
        self, tmp_path: Path
    ) -> None:
        cc.activate(tmp_path, self.SESSION_ID, cc.UI_RE_SKILL)
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data=json.dumps(
                {
                    "hook_event_name": "Stop",
                    "session_id": self.SESSION_ID,
                    "stop_hook_active": True,
                    "session_crons": [],
                }
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        assert result.stdout.strip() == ""
        receipt = cc.load_receipt(tmp_path, self.SESSION_ID)
        assert receipt is not None
        assert receipt["state"] == cc.STATE_RUNNING

    def test_stop_hook_active_releases_even_when_gate_would_block(
        self, tmp_path: Path
    ) -> None:
        """When the Stop payload carries stop_hook_active=true the hook must
        allow the turn to end (exit 0, no block) even though the gate would
        otherwise block — otherwise it loops until the consecutive-block cap and
        wastes hours of churn per round."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)  # would block (no result.txt)

        result = run_hook(
            self.MODULE,
            stdin_data=json.dumps({"hook_event_name": "Stop", "stop_hook_active": True}),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "", (
            f"stop_hook_active must release without a block; got: {result.stdout!r}"
        )

    def test_stop_hook_inactive_still_blocks(self, tmp_path: Path) -> None:
        """Sanity: with stop_hook_active=false the gate still blocks normally."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data=json.dumps({"hook_event_name": "Stop", "stop_hook_active": False}),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        data = json.loads(result.stdout.strip())
        assert data.get("decision") == "block"

    def test_headless_driver_demotes_gate_block_to_advisory(self, tmp_path: Path) -> None:
        """Under a headless driver the gate must advise on stderr and exit 0
        instead of emitting a block on stdout.

        The benchmark harness drives `claude --print`, where a Stop-hook block
        costs a whole iteration: the blocked turn produces no printed answer,
        and the reason only lands on the NEXT turn. The driver already re-runs
        the Python gates between iterations, so the block buys nothing it does
        not already have — but the reason still has to reach the driver log."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)  # would block (no result.txt)

        result = run_hook(
            self.MODULE,
            stdin_data=json.dumps({"hook_event_name": "Stop", "stop_hook_active": False}),
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "UI_RE_HEADLESS_DRIVER": "1",
            },
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "", (
            f"headless driver must not emit a block on stdout; got: {result.stdout!r}"
        )
        assert "UI-RE Gate" in result.stderr, (
            f"advisory reason must still reach stderr; got: {result.stderr!r}"
        )

    def test_headless_driver_unset_still_blocks(self, tmp_path: Path) -> None:
        """Sanity twin: without the headless flag the block is unchanged."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data=json.dumps({"hook_event_name": "Stop", "stop_hook_active": False}),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path), "UI_RE_HEADLESS_DRIVER": "0"},
        )
        assert json.loads(result.stdout.strip()).get("decision") == "block"

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
                        "paid-features",
                        "spec",
                        "pre-generate",
                        "state-coverage",
                        "post-implement",
                        "boundary",
                        "font-parity",
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
                        "paid-features",
                        "spec",
                        "pre-generate",
                        "state-coverage",
                        "post-implement",
                        "boundary",
                        "font-parity",
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
                        "paid-features",
                        "spec",
                        "pre-generate",
                        "state-coverage",
                        "post-implement",
                        "boundary",
                        "font-parity",
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

    def test_fresh_active_dirs_prunes_to_two_newest_markers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bound concurrent active refs so abandoned markers do not stack forever."""
        from ui_clone.hooks import section_gate

        search_root = make_search_root(tmp_path)
        old = make_ref_dir(search_root, "old")
        mid = make_ref_dir(search_root, "mid")
        new = make_ref_dir(search_root, "new")
        old_marker = set_active_marker(old, age_seconds=300)
        set_active_marker(mid, age_seconds=200)
        set_active_marker(new, age_seconds=100)
        monkeypatch.delenv("UI_RE_ACTIVE_MAX", raising=False)

        fresh = section_gate._fresh_active_dirs([old, mid, new])

        assert [p.name for p in fresh] == ["mid", "new"]
        assert not old_marker.exists(), "LRU-pruned explicit marker should be removed"

    def test_implicit_active_dir_goes_stale_by_ref_activity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Implicit activation has no marker mtime, so use ref activity TTL."""
        from ui_clone.hooks import section_gate

        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, "implicit-old")
        (ref_dir / "extracted.json").write_text("{}", encoding="utf-8")
        old_time = time.time() - (4 * 24 * 3600)
        os.utime(ref_dir / "extracted.json", (old_time, old_time))
        os.utime(ref_dir, (old_time, old_time))
        monkeypatch.delenv("UI_RE_STALE_DAYS", raising=False)

        assert section_gate._fresh_active_dirs([ref_dir]) == []

    def test_implicit_orphan_not_refreshened_by_hook_bookkeeping(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An orphan ref (no pipeline-state) must age out by its REAL artifacts,
        NOT be perpetually re-freshened by the Stop hook's own bookkeeping writes
        — the `.ui-re-sessions/` session crumb and `.ui-re-active`. Real
        recurrence: unrelated sessions kept firing on months-old orphans because
        every Stop scan wrote a fresh crumb whose mtime out-ranked the stale
        artifacts, so `_active_ref_mtime` never saw the ref as old."""
        from ui_clone.hooks import section_gate

        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, "orphan-bookkept")
        art = ref_dir / "extracted.json"
        art.write_text("{}", encoding="utf-8")
        old_time = time.time() - (5 * 24 * 3600)
        os.utime(art, (old_time, old_time))
        os.utime(ref_dir, (old_time, old_time))
        # The Stop hook's own bookkeeping, written THIS turn (fresh mtime).
        mark_ref_session(ref_dir, "unrelated-session", source="stop-scan")
        (ref_dir / ".ui-re-active").write_text("", encoding="utf-8")  # fresh
        monkeypatch.delenv("UI_RE_STALE_DAYS", raising=False)

        # NOTE: with a fresh .ui-re-active the marker path governs; remove it so
        # this exercises the implicit fallback specifically (the marker-refresh
        # case is a separate concern). The crumb alone must not re-freshen.
        (ref_dir / ".ui-re-active").unlink()
        assert section_gate._fresh_active_dirs([ref_dir]) == []

    def test_implicit_stale_by_pipeline_state_despite_fresh_fs_mtime(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An abandoned ref whose pipeline-state.last_updated is old must be
        treated as STALE even when its directory mtime was just bumped (a scan,
        a read, or the hook itself touching the dir). Filesystem mtime is noisy;
        pipeline-state.last_updated is the authoritative activity signal. Real
        bug: an orphan ref (last_updated 2 days ago) whose dir got touched today
        out-ranked the genuinely-active ref and blocked an unrelated Stop."""
        import datetime as _dt

        from ui_clone.hooks import section_gate

        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, "abandoned-orphan")
        old_iso = (
            _dt.datetime.now(_dt.UTC) - _dt.timedelta(days=4)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "abandoned-orphan",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": ["reference", "extraction"],
                    "current_gate": "post-implement",
                    "last_updated": old_iso,
                }
            ),
            encoding="utf-8",
        )
        # Directory + children carry a FRESH filesystem mtime (just written).
        monkeypatch.delenv("UI_RE_STALE_DAYS", raising=False)

        assert section_gate._fresh_active_dirs([ref_dir]) == []

    def test_lru_ranks_by_pipeline_state_not_fresh_fs_mtime(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LRU pruning must rank refs by pipeline-state activity, not fs mtime.

        Reproduces the real block: an abandoned orphan whose dir was touched
        today (freshest fs mtime) but last advanced 2 days ago must lose the
        LRU race to genuinely-active refs and be pruned — so it stops hijacking
        the 'newest active' slot and blocking an unrelated Stop."""
        import datetime as _dt

        from ui_clone.hooks import section_gate

        def _iso(days_ago: float) -> str:
            return (
                _dt.datetime.now(_dt.UTC) - _dt.timedelta(days=days_ago)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

        search_root = make_search_root(tmp_path)
        specs = {"orphan": 2.0, "active-a": 0.001, "active-b": 0.0005}
        dirs = {}
        for name, days_ago in specs.items():
            d = make_ref_dir(search_root, name)
            (d / "pipeline-state.json").write_text(
                json.dumps(
                    {
                        "component": name,
                        "started_at": "2026-01-01T00:00:00Z",
                        "completed_steps": ["reference", "extraction"],
                        "current_gate": "post-implement",
                        "last_updated": _iso(days_ago),
                    }
                ),
                encoding="utf-8",
            )
            dirs[name] = d
        # All three carry a fresh fs mtime (just written). active_max=2.
        monkeypatch.setenv("UI_RE_ACTIVE_MAX", "2")
        monkeypatch.delenv("UI_RE_STALE_DAYS", raising=False)

        kept = {p.name for p in section_gate._fresh_active_dirs(list(dirs.values()))}

        assert "orphan" not in kept, "stale-by-state orphan must be LRU-pruned"
        assert kept == {"active-a", "active-b"}

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
            "state-coverage",
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

    def test_session_scoped_stop_skips_ref_not_touched_by_current_session(
        self, tmp_path: Path
    ) -> None:
        """A Stop event from another agent tab must not force duplicate work for
        a WIP ref it never touched."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, "foreign-wip")
        set_active_marker(ref_dir)
        impl_dir = tmp_path / "scratch" / "foreign-wip" / "impl"
        impl_dir.mkdir(parents=True)
        (ref_dir / ".impl-root").write_text(str(impl_dir) + "\n")

        result = run_hook(
            self.MODULE,
            stdin_data=json.dumps({"session_id": "current-session", "hook_event_name": "Stop"}),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_session_scoped_stop_blocks_ref_touched_by_current_session(
        self, tmp_path: Path
    ) -> None:
        """The owner session still gets the strict verify-stamp block."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, "owned-wip")
        set_active_marker(ref_dir)
        impl_dir = tmp_path / "scratch" / "owned-wip" / "impl"
        impl_dir.mkdir(parents=True)
        (ref_dir / ".impl-root").write_text(str(impl_dir) + "\n")
        mark_ref_session(ref_dir, "current-session", source="test")

        result = run_hook(
            self.MODULE,
            stdin_data=json.dumps({"session_id": "current-session", "hook_event_name": "Stop"}),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        data = json.loads(result.stdout.strip())
        assert data.get("decision") == "block"
        assert "verify-stamp.json" in data.get("reason", "")

    def test_session_scoped_stop_env_can_enforce_unowned_legacy_refs(
        self, tmp_path: Path
    ) -> None:
        """Operators can opt back into legacy fail-closed enforcement for
        unowned markers while migrating old WIP state."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, "legacy-wip")
        set_active_marker(ref_dir)
        impl_dir = tmp_path / "scratch" / "legacy-wip" / "impl"
        impl_dir.mkdir(parents=True)
        (ref_dir / ".impl-root").write_text(str(impl_dir) + "\n")

        result = run_hook(
            self.MODULE,
            stdin_data=json.dumps({"session_id": "current-session", "hook_event_name": "Stop"}),
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "UI_RE_ENFORCE_UNOWNED_ACTIVE": "1",
            },
        )

        assert result.returncode == 0
        data = json.loads(result.stdout.strip())
        assert data.get("decision") == "block"
        assert "verify-stamp.json" in data.get("reason", "")

    def test_no_sid_stop_skips_ref_owned_by_another_session(self, tmp_path: Path) -> None:
        """Blank-payload (no session_id) Stop must NOT block on a ref whose
        crumbs all belong to OTHER identifiable sessions. This is the recurrence
        the earlier scoping fix missed: ownership scoping was gated behind
        `if stop_scope_session_id:`, so the no-sid branch of
        should_enforce_ref_for_session never ran and an unrelated tab was blocked
        by another session's live clone."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, "other-session-wip")
        set_active_marker(ref_dir)
        impl_dir = tmp_path / "scratch" / "other-session-wip" / "impl"
        impl_dir.mkdir(parents=True)
        (ref_dir / ".impl-root").write_text(str(impl_dir) + "\n")
        mark_ref_session(ref_dir, "some-other-live-session", source="post_verify")

        result = run_hook(
            self.MODULE,
            stdin_data=json.dumps({"hook_event_name": "Stop"}),  # NO session_id
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        assert result.stdout.strip() == "", f"expected no block, got: {result.stdout}"

    def test_no_sid_stop_still_enforces_crumbless_own_clone(self, tmp_path: Path) -> None:
        """Fail-closed preserved: a no-sid Stop on a crumb-LESS active clone (the
        omx ship-short case — a fully un-instrumented session with no session id
        anywhere, so mark_ref_session never wrote a crumb) must STILL block."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, "crumbless-own-wip")
        set_active_marker(ref_dir)
        impl_dir = tmp_path / "scratch" / "crumbless-own-wip" / "impl"
        impl_dir.mkdir(parents=True)
        (ref_dir / ".impl-root").write_text(str(impl_dir) + "\n")
        # No mark_ref_session — the ref has no session crumbs at all.

        result = run_hook(
            self.MODULE,
            stdin_data=json.dumps({"hook_event_name": "Stop"}),  # NO session_id
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        data = json.loads(result.stdout.strip())
        assert data.get("decision") == "block"
        assert "verify-stamp.json" in data.get("reason", "")



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
                        "paid-features",
                        "spec",
                        "pre-generate",
                        "state-coverage",
                        "post-implement",
                        "boundary",
                        "font-parity",
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

    def test_arbitrary_synthetic_gate_on_fresh_state_does_not_release(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Universality: any synthetic gate name (NOT in GATE_ORDER) on a
        fresh state must NOT release Stop. Codex review universalised
        the discriminator from the literal "session-cleanup" to
        "gate not in GATE_ORDER" so future renames (forensic-preserve,
        loop-archive, etc.) don't silently degrade.
        """
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / ".ui-re-active").touch()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [],
                    "current_gate": "reference",
                    "last_updated": "2026-01-01T01:00:00Z",
                    "gate_fail_counts": {},
                    "unclonable_reasons": [
                        {
                            "gate": "forensic-preserve",  # hypothetical rename
                            "reason": "preserved from prior loop",
                        }
                    ],
                }
            )
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        if exit_code == 0:
            assert "block" in output.lower() or "decision" in output.lower(), (
                f"synthetic-gate marker + completed_steps==[] must not "
                f"silently release Stop, got exit=0 output={output[:200]!r}"
            )

    def test_session_cleanup_on_fresh_state_does_not_release(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Loop-23 slip path: forensic `session-cleanup` marker on a fresh
        state (completed_steps == []) must NOT release Stop. Codex-23
        inherited such a marker from a prior loop and exited "done"
        without exercising a single gate — the universal regression this
        test locks down.
        """
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / ".ui-re-active").touch()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [],
                    "current_gate": "reference",
                    "last_updated": "2026-01-01T01:00:00Z",
                    "gate_fail_counts": {},
                    "unclonable_reasons": [
                        {
                            "gate": "session-cleanup",
                            "reason": "preserved as forensic state from prior loop",
                        }
                    ],
                }
            )
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        # Stop must NOT release silently — gate enforcement should fire.
        # Allow either a block (decision message) or any non-quiet output,
        # but the legacy "free pass via unclonable_reasons" path is closed.
        if exit_code == 0:
            # exit 0 acceptable only when output explicitly blocks
            assert "block" in output.lower() or "decision" in output.lower(), (
                f"session-cleanup + completed_steps==[] must not silently "
                f"release Stop, got exit=0 output={output[:200]!r}"
            )

    def test_legacy_unclonable_reasons_without_terminal_state_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """unclonable_reasons are evidence, not lifecycle state.

        The Stop hook must not infer completion from legacy reason entries
        alone; callers should record explicit terminalState instead.
        """
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / ".ui-re-active").touch()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": ["reference", "extraction"],
                    "current_gate": "section-compare",
                    "last_updated": "2026-01-01T01:00:00Z",
                    "gate_fail_counts": {"section-compare": 10},
                    "unclonable_reasons": [
                        {"gate": "section-compare", "reason": "hard-cap reached"}
                    ],
                }
            )
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        assert exit_code == 0
        data = json.loads(output)
        assert data.get("decision") == "block"
        assert "terminalState" in data.get("reason", "")

    def test_terminal_state_releases_stop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit failed/incomplete terminalState releases Stop without a
        fake verify-stamp.json."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / ".ui-re-active").touch()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": ["reference", "extraction"],
                    "current_gate": "section-compare",
                    "last_updated": "2026-01-01T01:00:00Z",
                    "gate_fail_counts": {"section-compare": 10},
                    "unclonable_reasons": [
                        {"gate": "section-compare", "reason": "hard-cap reached"}
                    ],
                    "terminalState": {
                        "status": "incomplete",
                        "category": "hardening-probe-incomplete",
                        "gate": "section-compare",
                        "reason": "harvested failed hardening probe",
                        "recorded_at": "2026-01-01T01:00:00Z",
                    },
                }
            )
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        assert exit_code == 0
        assert "block" not in output.lower()

    def test_terminal_state_blocks_when_impl_changed_after_recording(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / ".ui-re-active").touch()
        impl = tmp_path / "scratch" / "comp" / "impl" / "src"
        impl.mkdir(parents=True)
        changed = impl / "App.tsx"
        changed.write_text("export default function App(){return null}", encoding="utf-8")
        state_path = ref_dir / "pipeline-state.json"
        state_path.write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": ["reference"],
                    "current_gate": "post-implement",
                    "last_updated": "2026-01-01T01:00:00Z",
                    "terminalState": {
                        "status": "failed",
                        "category": "canonical-verify-failed",
                        "gate": "post-implement",
                        "reason": "verify failed",
                        "recorded_at": "2026-01-01T01:00:00Z",
                    },
                }
            )
        )
        old = time.time() - 60
        new = time.time()
        os.utime(state_path, (old, old))
        os.utime(changed, (new, new))

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        assert exit_code == 0
        data = json.loads(output)
        assert data.get("decision") == "block"
        assert "recoverable verify failure" in data.get("reason", "")

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
                        "paid-features",
                        "spec",
                        "pre-generate",
                        "state-coverage",
                        "post-implement",
                        "boundary",
                        "font-parity",
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
                        "state-coverage",
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

    def test_impl_done_state_blocks_without_verify_stamp(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """impl/ exists → clean sections alone is not enough; pipeline verify must stamp."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_done_state(ref_dir)
        sections = ref_dir / "sections"
        sections.mkdir()
        (sections / "result.txt").write_text("| hero | ✅ PASS | ... |\n", encoding="utf-8")
        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text("export default function App(){return <main />}", encoding="utf-8")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "no verify-stamp.json" in reason
        assert "Build success" in reason
        assert "spot checks" in reason

    def test_verify_stamp_blocks_when_impl_changed_after_verify(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Loop feedback: a fresh stamp must not release Stop after later JSX/CSS/asset edits."""
        import datetime

        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_done_state(ref_dir)
        sections = ref_dir / "sections"
        sections.mkdir()
        (sections / "result.txt").write_text("| hero | ✅ PASS | ... |\n", encoding="utf-8")

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        app = src / "App.tsx"
        app.write_text("export default function App(){return <main />}", encoding="utf-8")

        stamp = ref_dir / "verify-stamp.json"
        stamp.write_text(
            json.dumps(
                {
                    "verifiedAt": datetime.datetime.now(datetime.UTC).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    "gatesPassed": [
                        "spec",
                        "post-implement",
                        "boundary",
                        "font-parity",
                        "section-compare",
                    ],
                    "stampedBy": "pipeline.execute_verify",
                }
            ),
            encoding="utf-8",
        )
        now = time.time()
        os.utime(stamp, (now - 10, now - 10))
        os.utime(app, (now, now))

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "impl changed after verify" in reason
        assert "App.tsx" in reason

    def test_verify_stamp_blocks_when_not_canonical_pipeline_stamp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hand-written/fake stamp must not release Stop."""
        import datetime

        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_done_state(ref_dir)
        sections = ref_dir / "sections"
        sections.mkdir()
        (sections / "result.txt").write_text("| hero | ✅ PASS | ... |\n", encoding="utf-8")

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        app = src / "App.tsx"
        app.write_text("export default function App(){return <main />}", encoding="utf-8")

        stamp = ref_dir / "verify-stamp.json"
        stamp.write_text(
            json.dumps(
                {
                    "verifiedAt": datetime.datetime.now(datetime.UTC).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    "gatesPassed": ["post-implement"],
                    "stampedBy": "manual",
                }
            ),
            encoding="utf-8",
        )
        now = time.time()
        os.utime(app, (now - 10, now - 10))
        os.utime(stamp, (now, now))

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "canonical" in reason
        assert "pipeline.execute_verify" in reason

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
                        "paid-features",
                        "spec",
                        "pre-generate",
                        "state-coverage",
                        "post-implement",
                        "boundary",
                        "font-parity",
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


class TestDriverSessionBypass:
    """Driver-session bypass — release Stop unconditionally when the current
    Claude Code session is registered as a loop driver for this repo.

    Production users never write the marker, so the gate works as before.
    Loop sessions spawned by the driver have their own CLAUDE_CODE_SESSION_ID
    so even if they could read the marker, no match → gate fires for them.
    """

    MODULE = "ui_clone.hooks.section_gate"

    def test_marker_matches_session_id_releases_stop(self, tmp_path: Path) -> None:
        """Marker file content matches CLAUDE_CODE_SESSION_ID → exit 0 with no block."""
        # Set up an active ref dir with impl/ but no verify-stamp — would
        # normally block. With the driver bypass active, it should not.
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="loop-claude-99")
        set_active_marker(ref_dir)
        impl_dir = tmp_path / "scratch" / "loop-claude-99" / "impl"
        impl_dir.mkdir(parents=True)
        (ref_dir / ".impl-root").write_text(str(impl_dir) + "\n")

        # Write the driver-session marker
        session_id = "driver-session-id-test-12345"
        (tmp_path / ".driver-session.id").write_text(session_id + "\n")

        result = run_hook(
            self.MODULE,
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "CLAUDE_CODE_SESSION_ID": session_id,
            },
        )
        assert result.returncode == 0
        assert not result.stdout.strip(), (
            f"driver bypass must produce no block JSON; got: {result.stdout!r}"
        )

    def test_marker_mismatch_blocks_normally(self, tmp_path: Path) -> None:
        """Marker exists but content differs from session env → gate fires as usual."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="loop-claude-99")
        set_active_marker(ref_dir)
        impl_dir = tmp_path / "scratch" / "loop-claude-99" / "impl"
        impl_dir.mkdir(parents=True)
        (ref_dir / ".impl-root").write_text(str(impl_dir) + "\n")

        (tmp_path / ".driver-session.id").write_text("DIFFERENT-session-id\n")

        result = run_hook(
            self.MODULE,
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "CLAUDE_CODE_SESSION_ID": "current-session-id",
            },
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, "non-driver session must still block"
        data = json.loads(out)
        assert data.get("decision") == "block"

    def test_no_marker_file_blocks_normally(self, tmp_path: Path) -> None:
        """No .driver-session.id file at all → gate fires as usual (production)."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="loop-claude-99")
        set_active_marker(ref_dir)
        impl_dir = tmp_path / "scratch" / "loop-claude-99" / "impl"
        impl_dir.mkdir(parents=True)
        (ref_dir / ".impl-root").write_text(str(impl_dir) + "\n")

        result = run_hook(
            self.MODULE,
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "CLAUDE_CODE_SESSION_ID": "any-session-id",
            },
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, "no driver marker → must still block"
        data = json.loads(out)
        assert data.get("decision") == "block"

    def test_empty_marker_blocks_normally(self, tmp_path: Path) -> None:
        """Empty marker file (no recorded session id) → gate fires as usual."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="loop-claude-99")
        set_active_marker(ref_dir)
        impl_dir = tmp_path / "scratch" / "loop-claude-99" / "impl"
        impl_dir.mkdir(parents=True)
        (ref_dir / ".impl-root").write_text(str(impl_dir) + "\n")

        (tmp_path / ".driver-session.id").write_text("\n")

        result = run_hook(
            self.MODULE,
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "CLAUDE_CODE_SESSION_ID": "current-session-id",
            },
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, "empty marker → must still block"

    def test_marker_matches_stdin_payload_session_id_releases_stop(self, tmp_path: Path) -> None:
        """Claude Code's canonical path: session_id arrives via stdin JSON payload."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="loop-claude-99")
        set_active_marker(ref_dir)
        impl_dir = tmp_path / "scratch" / "loop-claude-99" / "impl"
        impl_dir.mkdir(parents=True)
        (ref_dir / ".impl-root").write_text(str(impl_dir) + "\n")

        session_id = "stdin-payload-session-id-abc"
        (tmp_path / ".driver-session.id").write_text(session_id + "\n")

        payload = json.dumps({"session_id": session_id, "hook_event_name": "Stop"})
        result = run_hook(
            self.MODULE,
            stdin_data=payload,
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                # Env intentionally unset / wrong — stdin payload must win.
                "CLAUDE_CODE_SESSION_ID": "",
            },
        )
        assert result.returncode == 0
        assert not result.stdout.strip(), (
            f"stdin payload session_id match must release; got: {result.stdout!r}"
        )

    def test_marker_present_but_session_id_env_unset_blocks(self, tmp_path: Path) -> None:
        """Marker file populated but CLAUDE_CODE_SESSION_ID env empty → gate fires."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="loop-claude-99")
        set_active_marker(ref_dir)
        impl_dir = tmp_path / "scratch" / "loop-claude-99" / "impl"
        impl_dir.mkdir(parents=True)
        (ref_dir / ".impl-root").write_text(str(impl_dir) + "\n")

        (tmp_path / ".driver-session.id").write_text("some-session-id\n")

        result = run_hook(
            self.MODULE,
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "CLAUDE_CODE_SESSION_ID": "",
            },
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, "missing session env → must still block"


class TestSectionGateStructuralCloseout:
    """Structural closeout policy (Task #11) — pipeline-state.closeoutPolicy=='structural'
    requires structural-convergence-stamp.json from check-converged.sh in
    addition to canonical verify-stamp.json from pipeline.execute_verify."""

    def _run_gate_hook(self, ref_dir: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int | str, str]:
        """Mirror TestSectionGateFullEnforcement._run_gate_hook so the new
        tests can stand alone if that class is later refactored."""
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

    def _write_structural_state(self, ref_dir: Path) -> None:
        """A ref dir that opted into structural closeout. completed_steps
        stops at section-compare because closeout evidence is carried by the
        structural and canonical stamps rather than this legacy list."""
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
                    ],
                    "current_gate": "section-compare",
                    "last_updated": "2026-01-01T02:00:00Z",
                    "closeoutPolicy": "structural",
                }
            ),
            encoding="utf-8",
        )

    def _write_converged_result(self, ref_dir: Path) -> Path:
        sections = ref_dir / "sections"
        sections.mkdir(exist_ok=True)
        path = sections / "result.txt"
        path.write_text(
            "| hero | 100 | 50 | ok | ✅ PASS |\n\n"
            "**Result: 3 PASS, 0 FAIL, 7 SKIP, 3 STRUCTURAL_ONLY**\n",
            encoding="utf-8",
        )
        return path

    def _write_stamp(
        self, ref_dir: Path, result_file: Path, *, stamped_by: str = "scripts/verify/check-converged.sh"
    ) -> Path:
        """Mirror check-converged.sh's stamp emission so the test exercises the
        Stop hook in isolation without invoking the bash script."""
        import datetime
        import hashlib

        sha = hashlib.sha256(result_file.read_bytes()).hexdigest()
        stamp = ref_dir / "structural-convergence-stamp.json"
        stamp.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "closeoutKind": "structural",
                    "stampedBy": stamped_by,
                    "verifiedAt": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "stage": "C",
                    "sectionResult": "**Result: 3 PASS, 0 FAIL, 7 SKIP, 3 STRUCTURAL_ONLY**",
                    "sectionsResultSha256": sha,
                }
            ),
            encoding="utf-8",
        )
        return stamp

    def _write_verify_stamp(self, ref_dir: Path) -> Path:
        import datetime

        stamp = ref_dir / "verify-stamp.json"
        stamp.write_text(
            json.dumps(
                {
                    "verifiedAt": datetime.datetime.now(datetime.UTC).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    "gatesPassed": [
                        "spec",
                        "post-implement",
                        "boundary",
                        "font-parity",
                        "section-compare",
                    ],
                    "stampedBy": "pipeline.execute_verify",
                }
            ),
            encoding="utf-8",
        )
        return stamp

    def test_structural_stamp_alone_blocks_without_canonical_verify(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Structural convergence cannot bypass the canonical verify suite."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_structural_state(ref_dir)
        result_file = self._write_converged_result(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        app = src / "App.tsx"
        app.write_text("export default function App(){return <main />}", encoding="utf-8")

        stamp = self._write_stamp(ref_dir, result_file)
        now = time.time()
        os.utime(app, (now - 10, now - 10))
        os.utime(stamp, (now, now))

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "verify-stamp.json" in reason
        assert "python -m ui_clone.pipeline" in reason

    def test_structural_and_canonical_stamps_release_stop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both fresh evidence artifacts release structural closeout."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_structural_state(ref_dir)
        result_file = self._write_converged_result(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        app = src / "App.tsx"
        app.write_text("export default function App(){return <main />}", encoding="utf-8")

        structural_stamp = self._write_stamp(ref_dir, result_file)
        verify_stamp = self._write_verify_stamp(ref_dir)
        now = time.time()
        os.utime(app, (now - 10, now - 10))
        os.utime(structural_stamp, (now, now))
        os.utime(verify_stamp, (now, now))

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        assert exit_code == 0
        assert "block" not in output.lower(), (
            f"both structural and canonical evidence must release Stop; got: {output!r}"
        )

    def test_structural_policy_blocks_when_stamp_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """structural + no stamp → block with structural-convergence-stamp.json
        reference (not verify-stamp.json — the message must match the policy)."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_structural_state(ref_dir)
        self._write_converged_result(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text("export default function App(){return null}", encoding="utf-8")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "structural-convergence-stamp.json" in reason
        assert "check-converged.sh" in reason
        # Structural evidence is checked first, so canonical guidance must not
        # obscure the immediate missing-stamp repair.
        assert "verify-stamp.json" not in reason
        assert "pipeline.execute_verify" not in reason

    def test_stale_structural_stamp_error_precedes_missing_canonical_verify(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Repair structural evidence before reporting canonical evidence."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_structural_state(ref_dir)
        result_file = self._write_converged_result(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text("export default function App(){return null}", encoding="utf-8")

        stamp = self._write_stamp(ref_dir, result_file)
        stamp_data = json.loads(stamp.read_text(encoding="utf-8"))
        stamp_data["verifiedAt"] = "2020-01-01T00:00:00Z"
        stamp.write_text(json.dumps(stamp_data), encoding="utf-8")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "Structural-stamp gate: STALE" in reason
        assert "Verify-stamp gate" not in reason

    def test_structural_stamp_blocks_when_impl_changed_after_stamp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Freshness invariant must hold for structural stamps too: an agent
        cannot stamp, then edit JSX, then exit cleanly. Mirrors the canonical
        verify-stamp freshness check (loop-codex-21 bypass class)."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_structural_state(ref_dir)
        result_file = self._write_converged_result(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        app = src / "App.tsx"
        app.write_text("export default function App(){return <main />}", encoding="utf-8")

        stamp = self._write_stamp(ref_dir, result_file)
        now = time.time()
        os.utime(stamp, (now - 10, now - 10))
        os.utime(app, (now, now))

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        assert "impl changed" in data.get("reason", "").lower()
        assert "App.tsx" in data.get("reason", "")

    def test_structural_stamp_blocks_when_not_canonical_writer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stamp with stampedBy != check-converged.sh is hand-forged and must
        not release Stop (analogous to the verify-stamp anti-cheat check)."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_structural_state(ref_dir)
        result_file = self._write_converged_result(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text("export default function App(){return null}", encoding="utf-8")

        self._write_stamp(ref_dir, result_file, stamped_by="manual")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "non-canonical" in reason.lower() or "check-converged.sh" in reason

    def test_structural_stamp_blocks_when_sections_result_tampered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """sectionsResultSha256 in the stamp must match the current
        sections/result.txt. If the agent stamps with a converged result then
        edits result.txt to claim more convergence, the hash mismatch blocks.
        This is the structural equivalent of impl-freshness — the evidence
        the stamp attests to must not have moved out from under it."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_structural_state(ref_dir)
        result_file = self._write_converged_result(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text("export default function App(){return null}", encoding="utf-8")

        self._write_stamp(ref_dir, result_file)
        # Tamper with the result file AFTER stamping. Stamp's sha is now stale.
        result_file.write_text(
            "**Result: 14 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n",
            encoding="utf-8",
        )

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "tampered" in reason.lower() or "sha256" in reason.lower() or "mismatch" in reason.lower()

    def test_canonical_policy_unchanged_when_field_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression guard: state files without closeoutPolicy keep the
        existing strict canonical path. If this test ever fails, the field
        default flipped accidentally (every legacy run would suddenly accept
        structural stamps)."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        # Same as _write_structural_state but WITHOUT closeoutPolicy.
        ref_dir.mkdir(parents=True, exist_ok=True)
        (ref_dir / ".ui-re-active").touch()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [
                        "reference", "extraction", "bundle", "paid-features",
                        "spec", "pre-generate", "state-coverage",
                        "post-implement", "boundary",
                        "font-parity", "section-compare",
                    ],
                    "current_gate": "done",
                    "last_updated": "2026-01-01T02:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        result_file = self._write_converged_result(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text("export default function App(){return null}", encoding="utf-8")

        # Write only the structural stamp (no verify-stamp.json) — should still
        # block because the policy defaults to canonical.
        self._write_stamp(ref_dir, result_file)

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        # Canonical path: must demand verify-stamp.json
        assert "verify-stamp.json" in reason



class TestSectionGateCanvasReplayCloseout:
    """Canvas-replay closeout policy (v0.7.0) — pipeline-state.closeoutPolicy=='canvas-replay'
    routes the Stop hook to accept canvas-replay-stamp.json from
    scripts/verify/check-canvas-replay.sh instead of demanding verify-stamp.json
    from pipeline.execute_verify. The canonical and structural contracts are
    untouched; this class only exercises the new policy branch.

    Codex review (2026-05-25) findings applied:
      [1] No new GATE_ORDER entry — canvas-replay is a closeout policy, not a
          pipeline phase.
      [2] Attestation file is operator's explicit license confirmation; the
          stamp records sha256(attestation) for tamper detection.
      [5] Stamp records `ref_canvas_sources` URLs from attestation (audit trail
          for the canvas JS the impl loads at runtime).
      [7] Section schema: design doc says `kind: "canvas"`; section-compare
          relief in a follow-up commit will read that field.
    """

    def _run_gate_hook(
        self, ref_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[int | str, str]:
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

    def _write_canvas_replay_state(self, ref_dir: Path) -> None:
        """A ref dir that opted into canvas-replay closeout. Distinct from
        structural — completed_steps reaches section-compare via canonical
        gates (canvas-replay does not bypass earlier gates) but the closeout
        proof is the attestation stamp, not the canonical verify-stamp."""
        ref_dir.mkdir(parents=True, exist_ok=True)
        (ref_dir / ".ui-re-active").touch()
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [
                        "reference", "extraction", "bundle", "paid-features",
                        "spec", "pre-generate", "state-coverage",
                        "post-implement", "boundary", "font-parity",
                        "section-compare",
                    ],
                    "current_gate": "done",
                    "last_updated": "2026-01-01T02:00:00Z",
                    "closeoutPolicy": "canvas-replay",
                }
            ),
            encoding="utf-8",
        )

    def _write_attestation(self, ref_dir: Path) -> Path:
        attestation = ref_dir / "canvas-replay-attestation.json"
        attestation.write_text(
            json.dumps(
                {
                    "license": "https://example.test/license — explicit owner permission granted via email 2026-05-20",
                    "disclaimer": "Not affiliated with example.test. https://example.test assets loaded for canvas-fidelity per opt-in.",
                    "attestedBy": "operator-handle",
                    "attestedAt": "2026-05-25T08:00:00Z",
                    "ref_canvas_sources": [
                        "https://example.test/assets/canvas-driver.js",
                    ],
                }
            ),
            encoding="utf-8",
        )
        return attestation

    def _write_stamp(self, ref_dir: Path, attestation_path: Path,
                      stamped_by: str = "scripts/verify/check-canvas-replay.sh") -> Path:
        """Write a canvas-replay-stamp.json with attestation sha256."""
        import datetime
        import hashlib

        attestation_sha = hashlib.sha256(attestation_path.read_bytes()).hexdigest()
        attestation_data = json.loads(attestation_path.read_text(encoding="utf-8"))
        stamp = ref_dir / "canvas-replay-stamp.json"
        stamp.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "closeoutKind": "canvas-replay",
                    "stampedBy": stamped_by,
                    "verifiedAt": datetime.datetime.now(datetime.UTC).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    "attestationSha256": attestation_sha,
                    "refCanvasSources": attestation_data.get("ref_canvas_sources", []),
                    "attestedBy": attestation_data.get("attestedBy", ""),
                }
            ),
            encoding="utf-8",
        )
        return stamp

    def test_canvas_replay_stamp_releases_stop_with_valid_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_canvas_replay_state(ref_dir)
        attestation = self._write_attestation(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        app = src / "App.tsx"
        app.write_text("export default function App(){return <canvas />}", encoding="utf-8")

        stamp = self._write_stamp(ref_dir, attestation)
        now = time.time()
        os.utime(app, (now - 10, now - 10))
        os.utime(stamp, (now, now))

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        assert exit_code == 0
        assert "block" not in output.lower(), (
            f"canvas-replay stamp must release Stop; got: {output!r}"
        )

    def test_canvas_replay_policy_blocks_when_stamp_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """canvas-replay policy + no stamp → block. Message must reference
        canvas-replay-stamp.json (not verify-stamp.json / structural-
        convergence-stamp.json)."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_canvas_replay_state(ref_dir)
        self._write_attestation(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text(
            "export default function App(){return <canvas />}", encoding="utf-8"
        )

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "canvas-replay-stamp.json" in reason
        assert "check-canvas-replay.sh" in reason
        # Must NOT reference verify-stamp.json or structural-convergence-stamp.json —
        # this policy opted out of both.
        assert "verify-stamp.json" not in reason
        assert "structural-convergence-stamp.json" not in reason

    def test_canvas_replay_stamp_non_canonical_writer_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stamp written by anything other than scripts/verify/check-canvas-replay.sh
        must be rejected. Prevents hand-written stamps."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_canvas_replay_state(ref_dir)
        attestation = self._write_attestation(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text(
            "export default function App(){return <canvas />}", encoding="utf-8"
        )

        self._write_stamp(ref_dir, attestation, stamped_by="hand-written")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "non-canonical" in reason.lower() or "stampedBy" in reason

    def test_canvas_replay_attestation_tampered_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If canvas-replay-attestation.json is edited after the stamp was
        written, the stamp's attestationSha256 won't match. Block."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_canvas_replay_state(ref_dir)
        attestation = self._write_attestation(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text(
            "export default function App(){return <canvas />}", encoding="utf-8"
        )

        self._write_stamp(ref_dir, attestation)

        # Tamper with the attestation — adds a new ref_canvas_source URL.
        att_data = json.loads(attestation.read_text())
        att_data["ref_canvas_sources"].append("https://example.test/extra.js")
        attestation.write_text(json.dumps(att_data), encoding="utf-8")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "tampered" in reason.lower() or "attestation" in reason.lower()

    def test_canvas_replay_attestation_missing_blocks_stamp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """closeoutPolicy=canvas-replay + canvas-replay-stamp.json present but
        canvas-replay-attestation.json MISSING → block. The attestation is
        the operator's license confirmation; the stamp without the attestation
        it attests to is meaningless."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        self._write_canvas_replay_state(ref_dir)
        attestation = self._write_attestation(ref_dir)

        src = tmp_path / "impl" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text(
            "export default function App(){return <canvas />}", encoding="utf-8"
        )

        self._write_stamp(ref_dir, attestation)
        attestation.unlink()  # remove the attestation after stamping

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        exit_code, output = self._run_gate_hook(ref_dir, monkeypatch)
        data = json.loads(output) if output.strip().startswith("{") else {}
        assert exit_code == 0
        assert data.get("decision") == "block"
        reason = data.get("reason", "")
        assert "attestation" in reason.lower()


def test_coerce_stop_hook_active_handles_string_false() -> None:
    """Codex LOW: bool("false") is truthy — string forms must coerce correctly."""
    from ui_clone.hooks.section_gate import _coerce_stop_hook_active
    assert _coerce_stop_hook_active(True) is True
    assert _coerce_stop_hook_active(False) is False
    assert _coerce_stop_hook_active("true") is True
    assert _coerce_stop_hook_active("false") is False
    assert _coerce_stop_hook_active("0") is False
    assert _coerce_stop_hook_active("") is False
    assert _coerce_stop_hook_active(None) is False
    assert _coerce_stop_hook_active(1) is True


# ── Item 5: self-attested terminal-state must pin sections/result.txt evidence ──


def _terminal_state(term: dict):  # type: ignore[no-untyped-def]
    from ui_clone.state import PipelineState

    s = PipelineState(component="comp", current_gate="post-implement")
    s.terminal_state = term
    return s


def _block_reason(ref_dir: Path, term: dict):  # type: ignore[no-untyped-def]
    from ui_clone.hooks.section_gate import _terminal_state_block_reason

    return _terminal_state_block_reason(ref_dir, _terminal_state(term))


def _with_result(ref_dir: Path) -> str:
    """A SUCCESS-shaped result.txt (claims PASS, no FAIL)."""
    import hashlib

    (ref_dir / "sections").mkdir(parents=True)
    (ref_dir / "sections" / "result.txt").write_text("**Result: 1 PASS**\n")
    return hashlib.sha256(
        (ref_dir / "sections" / "result.txt").read_bytes()
    ).hexdigest()


def _with_failing_result(ref_dir: Path) -> str:
    """A NON-success result.txt (has a FAIL row) — a legitimate partial-progress
    snapshot for a genuine incomplete/failed terminal end. Reaches the pin path,
    not the N1 success-fraud block."""
    import hashlib

    (ref_dir / "sections").mkdir(parents=True)
    (ref_dir / "sections" / "result.txt").write_text(
        "| hero | 4000 | 9000 | major | ❌ |\n"
        "**Result: 1 PASS, 1 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n"
    )
    return hashlib.sha256(
        (ref_dir / "sections" / "result.txt").read_bytes()
    ).hexdigest()


def test_self_attested_terminal_without_pin_blocks(tmp_path: Path) -> None:
    _with_failing_result(tmp_path)
    reason = _block_reason(
        tmp_path, {"status": "incomplete", "category": "x", "reason": "y", "writtenBy": "cli"}
    )
    assert reason is not None
    assert "self-attested" in reason and "re-record" in reason


def test_self_attested_success_result_blocks_even_with_matching_pin(tmp_path: Path) -> None:
    # N1 (inverted): a SUCCESS-shaped result.txt can no longer self-attest a
    # terminal release even with a byte-matching pin — the pin binds the bytes, not
    # the gate execution, so a forged all-PASS result.txt + a self-pin used to
    # release with zero gates run. Success must go through canonical verify.
    sha = _with_result(tmp_path)
    reason = _block_reason(
        tmp_path,
        {"status": "incomplete", "category": "x", "reason": "y",
         "writtenBy": "cli", "sectionsResultSha256": sha},
    )
    assert reason is not None
    assert "PASS" in reason and "canonical" in reason.lower()


def test_self_attested_nonsuccess_result_with_matching_pin_releases(tmp_path: Path) -> None:
    # The legitimate incomplete end: a genuine partial-progress result.txt (has a
    # FAIL) bound to its exact bytes still releases — the run is honestly not
    # claiming success, so the pin path governs and a matching pin is sufficient.
    sha = _with_failing_result(tmp_path)
    reason = _block_reason(
        tmp_path,
        {"status": "incomplete", "category": "x", "reason": "y",
         "writtenBy": "cli", "sectionsResultSha256": sha},
    )
    assert reason is None


def test_self_attested_terminal_pin_mismatch_blocks(tmp_path: Path) -> None:
    _with_failing_result(tmp_path)
    reason = _block_reason(
        tmp_path,
        {"status": "incomplete", "category": "x", "reason": "y",
         "writtenBy": "cli", "sectionsResultSha256": "0" * 64},
    )
    assert reason is not None


def test_cli_unclonable_and_failed_also_pinned(tmp_path: Path) -> None:
    """Hole-1 regression: gating on writtenBy (not status) means CLI-written
    unclonable/failed are bound too, not just incomplete/abandoned."""
    for status in ("unclonable", "failed", "abandoned"):
        ref = tmp_path / status
        _with_failing_result(ref)
        reason = _block_reason(
            ref, {"status": status, "category": "x", "reason": "y", "writtenBy": "cli"}
        )
        assert reason is not None, status


def test_pipeline_written_terminal_is_exempt(tmp_path: Path) -> None:
    # A pipeline-written NON-success terminal (the legitimate case: verify failed)
    # is exempt from the pin requirement.
    for status in ("failed", "unclonable"):
        ref = tmp_path / status
        _with_failing_result(ref)
        reason = _block_reason(
            ref, {"status": status, "category": "x", "reason": "y", "writtenBy": "pipeline"}
        )
        assert reason is None, status


def test_pipeline_written_terminal_with_success_result_still_blocks(tmp_path: Path) -> None:
    # N1 CRITICAL (Edit-route forge): the success block must NOT exempt
    # writtenBy=="pipeline" — that flag lives in agent-writable pipeline-state.json
    # and can be minted via the Edit tool. A pipeline-claimed terminal over a
    # success-shaped result.txt is the reconstituted forge and must BLOCK; a genuine
    # success never ends via terminalState.
    _with_result(tmp_path)
    reason = _block_reason(
        tmp_path, {"status": "incomplete", "category": "x", "reason": "y", "writtenBy": "pipeline"}
    )
    assert reason is not None
    assert "PASS" in reason and "canonical" in reason.lower()


def _write_failed_verify_report(ref_dir: Path, gate: str) -> None:
    (ref_dir / "verify-report.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "generatedAt": "2026-08-05T04:11:28Z",
                "verdict": "fail",
                "failures": [gate],
                "gates": [
                    {
                        "gate": gate,
                        "passed": False,
                        "pass_count": 0,
                        "warn_count": 0,
                        "fail_count": 1,
                        "checks": [
                            {
                                "label": "required: impl-scope",
                                "status": "fail",
                                "message": "impl-scope failed",
                                "fix": "rerun impl-scope-check.sh",
                                "stale": False,
                            }
                        ],
                        "exit_code": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _canonical_failure_terminal(gate: str) -> dict[str, object]:
    return {
        "status": "failed",
        "category": "canonical-verify-failed",
        "gate": gate,
        "reason": f"canonical verify failed: {gate}",
        "recorded_at": "2026-08-05T04:11:28Z",
        "writtenBy": "pipeline",
        "detail": {
            "failed_gates": [gate],
            "root_cause_gates": [gate],
            "cascade_gates": [],
            "gate_exit_codes": {gate: 1},
            "verify_report": "verify-report.json",
        },
    }


def test_legacy_canonical_nonsection_failure_blocks_as_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy canonical verify failures are active rework, not terminal release."""
    from ui_clone.gates.base import CheckResult, Gate

    _with_result(tmp_path)
    _write_failed_verify_report(tmp_path, "post-implement")
    monkeypatch.setattr(
        Gate,
        "_dispatch",
        lambda _self, gate: [
            CheckResult("required: impl-scope", "fail", f"{gate} still fails")
        ],
    )

    reason = _block_reason(
        tmp_path, _canonical_failure_terminal("post-implement")
    )

    assert reason is not None
    assert "recoverable verify failure" in reason


def test_legacy_canonical_failure_report_blocks_when_live_gate_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A legacy canonical verify failure remains Stop-blocking active work."""
    from ui_clone.gates.base import CheckResult, Gate

    _with_result(tmp_path)
    _write_failed_verify_report(tmp_path, "post-implement")
    monkeypatch.setattr(
        Gate,
        "_dispatch",
        lambda _self, gate: [CheckResult(gate, "pass", f"{gate} now passes")],
    )

    reason = _block_reason(
        tmp_path, _canonical_failure_terminal("post-implement")
    )

    assert reason is not None
    assert "recoverable verify failure" in reason


def test_legacy_canonical_section_compare_failure_blocks_as_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The section-compare variant is also recoverable rework."""
    from ui_clone.gates.base import CheckResult, Gate

    _with_result(tmp_path)
    _write_failed_verify_report(tmp_path, "section-compare")
    monkeypatch.setattr(
        Gate,
        "_dispatch",
        lambda _self, gate: [CheckResult(gate, "fail", f"{gate} still fails")],
    )

    reason = _block_reason(
        tmp_path, _canonical_failure_terminal("section-compare")
    )

    assert reason is not None
    assert "recoverable verify failure" in reason


def test_success_fraud_detector_is_failtoward_blocking(tmp_path: Path) -> None:
    # HIGH: the detector must catch success phrasings a token blacklist would miss
    # — a canonical all-PASS line, its lowercase clone, an all-✅ table, and a
    # prose/emoji success claim all BLOCK a self-attested terminal release.
    import hashlib

    from ui_clone.hooks.section_gate import _result_txt_claims_success

    success_bodies = (
        "**Result: 5 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n",  # canonical
        "**result: 5 pass, 0 fail, 0 skip, 0 structural_only**\n",  # lowercase clone
        "| hero | 100 | 200 | ok | ✅ |\n| nav | 80 | 90 | ok | ✅ |\n",  # all-✅ table
        "VERDICT: SUCCESS — clone CONVERGED, all sections green 🟢\n",  # prose/emoji
        "**Result: 1 PASS**\n",  # simplified fallback shape
    )
    for body in success_bodies:
        ref = tmp_path / hashlib.sha256(body.encode()).hexdigest()[:12]
        (ref / "sections").mkdir(parents=True)
        (ref / "sections" / "result.txt").write_text(body, encoding="utf-8")
        assert _result_txt_claims_success(ref) is True, body
        sha = hashlib.sha256(
            (ref / "sections" / "result.txt").read_bytes()
        ).hexdigest()
        reason = _block_reason(
            ref,
            {"status": "incomplete", "category": "x", "reason": "y",
             "writtenBy": "cli", "sectionsResultSha256": sha},
        )
        assert reason is not None, body


def test_success_fraud_detector_reads_last_result_line_not_decoy(tmp_path: Path) -> None:
    # MEDIUM (decoy evasion): a planted `**Result: 0 PASS, 3 FAIL**` line ABOVE a
    # real all-PASS line must NOT release — the detector reads the LAST line (what
    # the harvester/human reads), so the success claim still BLOCKS. A free-floating
    # "FAIL"/"MISSING" token in prose must not fake non-success either.
    from ui_clone.hooks.section_gate import _result_txt_claims_success

    decoy = (
        "**Result: 0 PASS, 3 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n"
        "note: an earlier pass had MISSING items, now resolved\n"
        "**Result: 9 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n"
    )
    (tmp_path / "sections").mkdir(parents=True)
    (tmp_path / "sections" / "result.txt").write_text(decoy, encoding="utf-8")
    assert _result_txt_claims_success(tmp_path) is True
    reason = _block_reason(
        tmp_path, {"status": "incomplete", "category": "x", "reason": "y", "writtenBy": "cli"}
    )
    assert reason is not None


def test_legacy_untagged_terminal_with_result_blocks(tmp_path: Path) -> None:
    """Hole-3: a legacy record with no writtenBy defaults to the enforced (cli)
    class — fail-toward-enforcement."""
    _with_failing_result(tmp_path)
    reason = _block_reason(
        tmp_path, {"status": "incomplete", "category": "x", "reason": "y"}
    )
    assert reason is not None


def test_self_attested_terminal_without_result_txt_releases(tmp_path: Path) -> None:
    """Back-compat: an early run with no sections/result.txt has no evidence to
    pin, so the sha branch is skipped and the run is not bricked."""
    reason = _block_reason(
        tmp_path, {"status": "incomplete", "category": "x", "reason": "y", "writtenBy": "cli"}
    )
    assert reason is None
