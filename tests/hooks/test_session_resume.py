from __future__ import annotations

import json
from pathlib import Path

from ._helpers import (
    _set_done_state,
    make_ref_dir,
    make_search_root,
    run_hook,
    set_active_marker,
)


class TestSessionResume:
    """SessionStart + PostCompact reinjection — addresses the empirically-dominant
    post-compact skip pattern (73% of past verification skips).
    """

    MODULE = "ui_clone.hooks.session_resume"

    def test_no_wip_marker_exits_silently(self, tmp_path: Path) -> None:
        """No active WIP marker → no injection, exit 0 with empty stdout."""
        make_search_root(tmp_path)  # tmp/ref/ exists but no children
        result = run_hook(
            self.MODULE,
            stdin_data="{}",
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_no_tmp_ref_at_all_exits_silently(self, tmp_path: Path) -> None:
        """No tmp/ref/ directory → exit 0 with empty stdout (cold project)."""
        result = run_hook(
            self.MODULE,
            stdin_data="{}",
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_wip_marker_emits_additional_context(self, tmp_path: Path) -> None:
        """Active WIP marker → emit hookSpecificOutput.additionalContext."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="375studio")
        set_active_marker(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data="{}",
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert "hookSpecificOutput" in payload
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "375studio" in ctx
        # Must mention the gate scripts by name so the agent knows what to run.
        assert "section-compare.sh" in ctx
        assert "transition-spec-coverage.sh" in ctx
        # Must mention the post-compact skip pattern explicitly.
        assert "post-compact" in ctx.lower() or "compact" in ctx.lower()
        # Must include the host-neutral goal card for delegated workers.
        assert "Goal Card: 375studio" in ctx
        assert "python -m ui_clone.goal tmp/ref/375studio" in ctx
        assert "delegated worker" in ctx

    def test_postcompact_payload_detected(self, tmp_path: Path) -> None:
        """When stdin signals PostCompact, the emitted hookEventName matches."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="375studio")
        set_active_marker(ref_dir)

        # PostCompact payloads carry a "trigger" field ("manual" or "auto")
        result = run_hook(
            self.MODULE,
            stdin_data=json.dumps({"trigger": "auto", "summary": "..."}),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["hookEventName"] == "PostCompact"

    def test_sessionstart_default_when_payload_ambiguous(self, tmp_path: Path) -> None:
        """Empty stdin → defaults to SessionStart event name."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="375studio")
        set_active_marker(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data="",
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"

    def test_intersection_trigger_in_spec_includes_reveal_check(self, tmp_path: Path) -> None:
        """transition-spec.json with intersection entry → message must call out
        reveal-trigger-check.sh as REQUIRED (not optional)."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="375studio")
        set_active_marker(ref_dir)
        (ref_dir / "transition-spec.json").write_text(
            json.dumps(
                {
                    "transitions": [
                        {"id": "works-reveal", "trigger": "intersection", "type": "fade-up"},
                    ]
                }
            )
        )

        result = run_hook(
            self.MODULE,
            stdin_data="{}",
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "reveal-trigger-check.sh" in ctx
        assert "REQUIRED" in ctx  # the inline marker for intersection entries
        assert "transition-implementation.md" in ctx
        assert "IntersectionObserver placement" in ctx

    def test_done_state_skips_injection(self, tmp_path: Path) -> None:
        """Marker present but state==done → no injection (project finished, nothing to nag).
        Closes spam-on-completed-projects loop now that section_gate no longer
        unlinks the marker on done."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="375studio")
        set_active_marker(ref_dir)
        _set_done_state(ref_dir)

        result = run_hook(
            self.MODULE,
            stdin_data="{}",
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        # No injection — empty stdout
        assert result.stdout.strip() == "", (
            f"Expected silent skip on done state, got: {result.stdout!r}"
        )

    def test_empty_spec_omits_intersection_specific_doc_calls(self, tmp_path: Path) -> None:
        """transition-spec.json absent → omit intersection-specific guidance,
        but keep general gate list."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root, name="static-site")
        set_active_marker(ref_dir)
        # No transition-spec.json at all

        result = run_hook(
            self.MODULE,
            stdin_data="{}",
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        # Still mentions the general gates
        assert "section-compare.sh" in ctx
        # But the intersection-specific REQUIRED inline marker is absent
        # (intersection text only present in the conditional block)
        assert "intersection/fade-up entries detected" not in ctx

