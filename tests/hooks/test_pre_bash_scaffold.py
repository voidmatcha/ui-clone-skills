from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from ._helpers import (
    _bash_input,
    _set_phase2_only_state,
    _set_pre_generate_passed_state,
    make_ref_dir,
    make_search_root,
    run_hook,
)


class TestResolveImplDir:
    """`_resolve_impl_dir` must prefer per-ref-dir impl_root over the
    legacy <repo>/impl convention. Direct import + call is fine here —
    the function is pure and synchronous.
    """

    @staticmethod
    def _import() -> Callable[..., Path | None]:
        from ui_clone.hooks.section_gate import _resolve_impl_dir
        return _resolve_impl_dir

    def test_uses_pipeline_state_impl_root_when_set(self, tmp_path: Path) -> None:
        """When pipeline-state.json records impl_root, use it — not the
        convention path. Otherwise a rogue <repo>/impl symlink would
        false-positive every ref dir.
        """
        _resolve_impl_dir = self._import()
        ref_dir = tmp_path / "tmp" / "ref" / "loop-A"
        ref_dir.mkdir(parents=True)
        target_impl = tmp_path / "scratch" / "loop-A" / "impl"
        target_impl.mkdir(parents=True)
        rogue_impl = tmp_path / "impl"
        rogue_impl.mkdir()

        (ref_dir / "pipeline-state.json").write_text(json.dumps({
            "component": "loop-A",
            "started_at": "2026-01-01T00:00:00Z",
            "completed_steps": [],
            "current_gate": "reference",
            "last_updated": "2026-01-01T00:00:00Z",
            "implRoot": str(target_impl),
            "impl_root": str(target_impl),
        }))

        resolved = _resolve_impl_dir(ref_dir, fallback_root=tmp_path)
        assert resolved == target_impl, (
            f"impl_root from pipeline-state must win over rogue {rogue_impl}; "
            f"got {resolved}"
        )

    def test_uses_marker_file_when_state_missing(self, tmp_path: Path) -> None:
        """`.impl-root` marker file is the fallback for ref dirs that have
        no pipeline-state.json yet (Phase 0 or early Phase 1). Same
        precedence as the state field — wins over <repo>/impl convention.
        """
        _resolve_impl_dir = self._import()
        ref_dir = tmp_path / "tmp" / "ref" / "loop-B"
        ref_dir.mkdir(parents=True)
        target_impl = tmp_path / "scratch" / "loop-B" / "impl"
        target_impl.mkdir(parents=True)
        (tmp_path / "impl").mkdir()  # rogue convention symlink

        (ref_dir / ".impl-root").write_text(str(target_impl) + "\n", encoding="utf-8")

        resolved = _resolve_impl_dir(ref_dir, fallback_root=tmp_path)
        assert resolved == target_impl, (
            f".impl-root marker must win over convention <repo>/impl; got {resolved}"
        )

    def test_env_override_wins_over_state(self, tmp_path: Path) -> None:
        """UI_CLONE_IMPL_ROOT is the operator override — wins over both the
        state field and the marker. Lets nested-loop testing point at
        whichever impl the operator is currently iterating on.
        """
        _resolve_impl_dir = self._import()
        ref_dir = tmp_path / "tmp" / "ref" / "loop-C"
        ref_dir.mkdir(parents=True)

        env_target = tmp_path / "operator-override-impl"
        env_target.mkdir()
        state_target = tmp_path / "scratch" / "loop-C" / "impl"
        state_target.mkdir(parents=True)

        (ref_dir / "pipeline-state.json").write_text(json.dumps({
            "component": "loop-C",
            "started_at": "2026-01-01T00:00:00Z",
            "completed_steps": [],
            "current_gate": "reference",
            "last_updated": "2026-01-01T00:00:00Z",
            "implRoot": str(state_target),
            "impl_root": str(state_target),
        }))

        old_env = os.environ.get("UI_CLONE_IMPL_ROOT")
        os.environ["UI_CLONE_IMPL_ROOT"] = str(env_target)
        try:
            resolved = _resolve_impl_dir(ref_dir, fallback_root=tmp_path)
        finally:
            if old_env is None:
                os.environ.pop("UI_CLONE_IMPL_ROOT", None)
            else:
                os.environ["UI_CLONE_IMPL_ROOT"] = old_env
        assert resolved == env_target, resolved

    def test_falls_back_to_convention_when_nothing_recorded(self, tmp_path: Path) -> None:
        """No impl_root anywhere → fall back to <fallback_root>/impl.
        Preserves legacy behavior for ref dirs that predate the field.
        """
        _resolve_impl_dir = self._import()
        ref_dir = tmp_path / "tmp" / "ref" / "loop-D"
        ref_dir.mkdir(parents=True)
        convention = tmp_path / "impl"
        convention.mkdir()

        resolved = _resolve_impl_dir(ref_dir, fallback_root=tmp_path)
        assert resolved == convention, resolved

    def test_returns_none_when_no_impl_exists(self, tmp_path: Path) -> None:
        """Returns None so callers can branch on "no impl is wired up"
        — they should not silently grab a stale convention path or fire
        the verify-stamp gate against an empty ref.
        """
        _resolve_impl_dir = self._import()
        ref_dir = tmp_path / "tmp" / "ref" / "loop-E"
        ref_dir.mkdir(parents=True)

        resolved = _resolve_impl_dir(ref_dir, fallback_root=tmp_path)
        assert resolved is None



class TestImplScaffoldGate:
    """Loop-codex-5 surfaced the gap: pre_generate fires on Write/Edit only;
    `npm create vite`/`npx create-*` etc. route through Bash and produce
    impl/ files without triggering the gate. pre_bash now blocks scaffold
    commands until current_gate >= pre-generate. These fixtures pin the
    behavior so a future refactor cannot silently reopen the bypass.
    """
    MODULE = "ui_clone.hooks.pre_bash"

    def test_blocks_npm_create_vite_when_pre_generate_not_reached(
        self, tmp_path: Path
    ) -> None:
        search_root = make_search_root(tmp_path)
        ref = make_ref_dir(search_root, name="loop-codex-N")
        # State mirrors loop-codex-5: Phase 2 ran but bundle gate still pending.
        _set_phase2_only_state(ref)
        # Phase 2 evidence file present — makes _is_fresh_state() return False
        # so the existing fresh-folder guard would NOT catch this.
        (ref / "regions.json").write_text(json.dumps({"sections": []}))
        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(
                "npm create vite@latest scratch/loop-codex-N/impl -- --template react"
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        # Hook emits permission denial as JSON on stdout, exits 0.
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, f"expected block JSON, got empty. stderr: {result.stderr}"
        data = json.loads(out)
        hook_out = data.get("hookSpecificOutput", {})
        assert hook_out.get("permissionDecision") == "deny"
        reason = hook_out.get("permissionDecisionReason", "")
        assert "impl-scaffold gate" in reason, reason
        assert "bundle" in reason, reason  # current gate label in message

    def test_blocks_npx_create_react_app_too(self, tmp_path: Path) -> None:
        search_root = make_search_root(tmp_path)
        ref = make_ref_dir(search_root, name="loop-codex-N")
        _set_phase2_only_state(ref)
        (ref / "regions.json").write_text(json.dumps({"sections": []}))
        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(
                "npx create-vite scratch/loop-codex-N/impl --template react"
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout.strip(), "expected block JSON for npx create-vite"

    def test_blocks_when_no_ref_dir_exists_at_all(self, tmp_path: Path) -> None:
        """Strictest form of bypass: bootstrap impl/ before any Phase 1
        evidence. Make tmp/ref exist but empty so _candidate_ref_roots
        returns at least one root."""
        make_search_root(tmp_path)
        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(
                "npm create vite@latest scratch/loop-bare/impl -- --template react"
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, "expected block JSON when no ref dir exists"
        data = json.loads(out)
        reason = data.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        assert "impl-scaffold gate" in reason, reason

    def test_allows_scaffold_after_pre_generate_passed(self, tmp_path: Path) -> None:
        search_root = make_search_root(tmp_path)
        ref = make_ref_dir(search_root, name="loop-codex-N")
        _set_pre_generate_passed_state(ref)
        (ref / "regions.json").write_text(json.dumps({"sections": []}))
        (ref / "transition-spec.json").write_text(json.dumps({"transitions": []}))
        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(
                "npm create vite@latest scratch/loop-codex-N/impl -- --template react"
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0
        # No block emitted — once pre-generate has passed, scaffolders are
        # fine. The agent can iterate on the impl freely.
        out = result.stdout.strip()
        if out:
            data = json.loads(out)
            hook_out = data.get("hookSpecificOutput", {})
            assert hook_out.get("permissionDecision") != "deny", (
                f"scaffold should be allowed post-pre-generate, got: {hook_out}"
            )

    def test_blocks_scaffold_after_pre_generate_state_when_transition_spec_missing(
        self, tmp_path: Path
    ) -> None:
        search_root = make_search_root(tmp_path)
        ref = make_ref_dir(search_root, name="loop-codex-N")
        _set_pre_generate_passed_state(ref)
        (ref / "regions.json").write_text(json.dumps({"sections": []}))

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(
                "npm create vite@latest scratch/loop-codex-N/impl -- --template react"
            ),
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )

        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, "expected block JSON when transition-spec.json is missing"
        data = json.loads(out)
        hook_out = data.get("hookSpecificOutput", {})
        assert hook_out.get("permissionDecision") == "deny"
        reason = hook_out.get("permissionDecisionReason", "")
        assert "transition-spec.json" in reason, reason

    def test_allows_unrelated_npm_commands_when_phase2_only(
        self, tmp_path: Path
    ) -> None:
        """npm install / npm run build / npm run lint are NOT scaffolders —
        they manipulate an already-existing impl. The guard must not be
        overbroad and flag every npm invocation.
        """
        search_root = make_search_root(tmp_path)
        ref = make_ref_dir(search_root, name="loop-codex-N")
        _set_phase2_only_state(ref)
        (ref / "regions.json").write_text(json.dumps({"sections": []}))
        for cmd in (
            "npm install",
            "npm run build",
            "npm run lint",
            "npm ls",
        ):
            result = run_hook(
                self.MODULE,
                stdin_data=_bash_input(cmd),
                env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
            )
            assert result.returncode == 0
            out = result.stdout.strip()
            if out:
                data = json.loads(out)
                reason = (
                    data.get("hookSpecificOutput", {})
                    .get("permissionDecisionReason", "")
                )
                assert "impl-scaffold gate" not in reason, (
                    f"impl-scaffold gate must not fire on `{cmd}`; got: {reason}"
                )

    def test_bypass_env_var_disables_scaffold_check(self, tmp_path: Path) -> None:
        """UI_RE_SKIP_BASH_GATE=1 is the documented emergency escape; it
        must short-circuit even the scaffold guard."""
        search_root = make_search_root(tmp_path)
        ref = make_ref_dir(search_root, name="loop-codex-N")
        _set_phase2_only_state(ref)
        (ref / "regions.json").write_text(json.dumps({"sections": []}))
        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(
                "npm create vite@latest scratch/loop-codex-N/impl -- --template react"
            ),
            env={
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "UI_RE_SKIP_BASH_GATE": "1",
            },
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        if out:
            data = json.loads(out)
            hook_out = data.get("hookSpecificOutput", {})
            assert hook_out.get("permissionDecision") != "deny", (
                f"UI_RE_SKIP_BASH_GATE=1 must short-circuit the scaffold "
                f"guard; got: {hook_out}"
            )


    def test_blocks_scaffold_when_only_scratch_nested_ref_dir_exists(
        self, tmp_path: Path
    ) -> None:
        """Loop-codex-7 closure: agent creates scratch/loop-codex-N/tmp/ref/<c>/
        with a fake pipeline-state.json reporting done (e.g. by copying a prior
        loop's completed ref dir into the scratch subtree). The closest-ancestor
        walk in find_project_root finds the scratch-nested tmp/ref first; the
        impl-scaffold gate must NOT accept that as evidence of pipeline progress.
        """
        repo = tmp_path
        scratch_ref = repo / "scratch" / "loop-codex-N" / "tmp" / "ref" / "realfood"
        scratch_ref.mkdir(parents=True)
        # Fake completed state — exactly what codex-7 produced by copy.
        (scratch_ref / "pipeline-state.json").write_text(json.dumps({
            "component": "realfood",
            "started_at": "2026-05-19T15:09:58Z",
            "completed_steps": [
                "reference", "extraction", "bundle", "paid-features",
                "spec", "pre-generate", "post-implement", "boundary",
                "font-parity", "section-compare",
            ],
            "current_gate": "done",
            "last_updated": "2026-05-19T16:00:00Z",
        }))
        # No canonical <repo>/tmp/ref/ — the canonical surface is empty.
        # The gate should block the scaffold because there's no canonical
        # evidence the pipeline ran, even though the scratch-nested dir
        # reports done.
        (repo / "tmp" / "ref").mkdir(parents=True)

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(
                "npm create vite@latest scratch/loop-codex-N/impl -- --template react"
            ),
            env={"CLAUDE_PROJECT_DIR": str(repo)},
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        assert out, (
            "expected block JSON; scratch-nested ref must not satisfy "
            f"impl-scaffold gate. stderr: {result.stderr}"
        )
        data = json.loads(out)
        hook_out = data.get("hookSpecificOutput", {})
        assert hook_out.get("permissionDecision") == "deny", (
            f"scratch-nested ref dir spoof must be rejected; got: {hook_out}"
        )
        reason = hook_out.get("permissionDecisionReason", "")
        assert "impl-scaffold gate" in reason, reason

    def test_allows_scaffold_when_canonical_repo_root_ref_at_pre_generate(
        self, tmp_path: Path
    ) -> None:
        """Canonical legitimate flow: <repo>/tmp/ref/<c>/pipeline-state.json
        shows pre-generate. Even if a scratch-nested ref also exists with
        a higher gate, the canonical one is what counts (and the scaffold
        runs because canonical reached pre-generate)."""
        repo = tmp_path
        canonical_ref = repo / "tmp" / "ref" / "loop-codex-N"
        canonical_ref.mkdir(parents=True)
        _set_pre_generate_passed_state(canonical_ref)
        (canonical_ref / "regions.json").write_text(json.dumps({"sections": []}))
        (canonical_ref / "transition-spec.json").write_text(json.dumps({"transitions": []}))
        # Scratch-nested copy that the previous test rejected — here it
        # exists, but the canonical ref is the source of truth.
        scratch_ref = repo / "scratch" / "loop-codex-N" / "tmp" / "ref" / "realfood"
        scratch_ref.mkdir(parents=True)
        from ui_clone.state import GATE_ORDER as _GO_INLINE
        (scratch_ref / "pipeline-state.json").write_text(json.dumps({
            "component": "realfood",
            "started_at": "2026-05-19T15:09:58Z",
            "completed_steps": list(_GO_INLINE),
            "current_gate": "done",
            "last_updated": "2026-05-19T16:00:00Z",
        }))

        result = run_hook(
            self.MODULE,
            stdin_data=_bash_input(
                "npm create vite@latest scratch/loop-codex-N/impl -- --template react"
            ),
            env={"CLAUDE_PROJECT_DIR": str(repo)},
        )
        # canonical = pre-generate → scaffold allowed.
        assert result.returncode == 0
        out = result.stdout.strip()
        if out:
            data = json.loads(out)
            hook_out = data.get("hookSpecificOutput", {})
            assert hook_out.get("permissionDecision") != "deny", (
                f"canonical ref at pre-generate must allow scaffold; got: {hook_out}"
            )
