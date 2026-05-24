from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ._helpers import (
    _populate_pre_generate_artifacts,
    _set_done_state,
    _set_section_compare_state,
    _write_passing_result_txt,
    make_ref_dir,
    make_search_root,
    run_hook,
    set_active_marker,
)


class TestNestedGitRepoRoot:
    """Verifies find_project_root finds the correct root based on tmp/ref/ in nested git repos."""

    def test_git_root_without_tmp_ref_falls_through_to_walk(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """git root without tmp/ref/ falls through to walk-up logic."""
        import ui_clone.hooks._common as _common
        from ui_clone.hooks._common import find_project_root

        # Clear cache from previous tests
        monkeypatch.setattr(_common, "_cached_project_root", None)

        # Simulate git returning a root that does NOT have tmp/ref/
        def fake_run(cmd: Any, **kwargs: Any) -> Any:
            class R:
                returncode = 0
                stdout = str(tmp_path) + "\n"

            return R()

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.setattr("subprocess.run", fake_run)
        # Place tmp/ref/ inside a subdirectory — the walk-up should find it
        sub = tmp_path / "nested" / "project"
        sub.mkdir(parents=True)
        (sub / "tmp" / "ref").mkdir(parents=True)
        monkeypatch.chdir(sub)

        result = find_project_root()
        # Should return `sub` (found via walk-up), not `tmp_path` (the fake git root)
        assert result == sub

    def test_git_root_with_tmp_ref_is_returned_directly(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """git root with tmp/ref/ is returned when no cwd ancestor has tmp/ref/."""
        import ui_clone.hooks._common as _common
        from ui_clone.hooks._common import find_project_root

        # Clear cache from previous tests
        monkeypatch.setattr(_common, "_cached_project_root", None)

        (tmp_path / "tmp" / "ref").mkdir(parents=True)
        # chdir to a sibling so the cwd walk does NOT find the tmp_path/tmp/ref/
        # — otherwise the (new) cwd-first precedence would return the walk-found
        # root before git rev-parse fires.
        sibling = tmp_path.parent / f"{tmp_path.name}-sibling"
        sibling.mkdir(exist_ok=True)
        monkeypatch.chdir(sibling)

        def fake_run(cmd: Any, **kwargs: Any) -> Any:
            class R:
                returncode = 0
                stdout = str(tmp_path) + "\n"

            return R()

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.setattr("subprocess.run", fake_run)

        result = find_project_root()
        assert result == tmp_path

    def test_scratch_loop_root_preferred_over_env_and_git(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Loop-56/57 root-cause regression: when nested session runs from a
        scratch/loop-N/ ancestor with its own tmp/ref/, that loop root MUST
        win over both $CLAUDE_PROJECT_DIR and the git root. External
        diagnoses of loop-8/56/57 traced "passed but actually broken"
        Stop-hook decisions to env_root preempting the cwd walk; this test
        pins the corrected ordering.
        """
        import ui_clone.hooks._common as _common
        from ui_clone.hooks._common import find_project_root

        monkeypatch.setattr(_common, "_cached_project_root", None)

        # Fake repo root with stale tmp/ref/
        repo_root = tmp_path / "repo"
        (repo_root / "tmp" / "ref").mkdir(parents=True)
        # Fake scratch loop ancestor with its OWN tmp/ref/
        loop_root = tmp_path / "repo" / "scratch" / "loop-99"
        (loop_root / "tmp" / "ref" / "realfood-main").mkdir(parents=True)
        # cwd is the impl/ subdir inside the loop — typical nested-session layout
        impl_dir = loop_root / "impl" / "src" / "components"
        impl_dir.mkdir(parents=True)
        monkeypatch.chdir(impl_dir)
        # Env var points to the repo (as if launched with --plugin-dir repo)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo_root))

        def fake_run(cmd: Any, **kwargs: Any) -> Any:
            class R:
                returncode = 0
                stdout = str(repo_root) + "\n"

            return R()

        monkeypatch.setattr("subprocess.run", fake_run)

        result = find_project_root()
        assert result == loop_root, (
            f"scratch loop root must win over env/git. got: {result}, "
            f"expected: {loop_root}"
        )



class TestCompletionPatternWordBoundary:
    """Verifies post_verify completion patterns apply word-boundary correctly."""

    def test_commit_substring_not_matched(self) -> None:
        """Substrings like 'commitment' must not trigger the pattern."""
        from ui_clone.hooks.post_verify import _is_completion_command

        assert not _is_completion_command("our team's commitment to quality")

    def test_commit_word_matched(self) -> None:
        """'git commit -m ...' must trigger the pattern."""
        from ui_clone.hooks.post_verify import _is_completion_command

        assert _is_completion_command("git commit -m 'fix layout'")

    def test_done_word_matched(self) -> None:
        """'all done' must trigger the pattern."""
        from ui_clone.hooks.post_verify import _is_completion_command

        assert _is_completion_command("all done")

    def test_deploy_word_matched(self) -> None:
        """'deploy' must trigger the pattern."""
        from ui_clone.hooks.post_verify import _is_completion_command

        assert _is_completion_command("deploy to production")

    def test_finish_word_matched(self) -> None:
        """'finish' alone must trigger the pattern."""
        from ui_clone.hooks.post_verify import _is_completion_command

        assert _is_completion_command("finish the work")

    def test_merge_word_matched(self) -> None:
        from ui_clone.hooks.post_verify import _is_completion_command

        assert _is_completion_command("git merge feature-branch")

    def test_push_word_matched(self) -> None:
        from ui_clone.hooks.post_verify import _is_completion_command

        assert _is_completion_command("git push origin main")

    def test_complete_word_matched(self) -> None:
        from ui_clone.hooks.post_verify import _is_completion_command

        assert _is_completion_command("mark task complete")

    def test_looks_good_phrase_matched(self) -> None:
        from ui_clone.hooks.post_verify import _is_completion_command

        assert _is_completion_command("LGTM, looks good to me")

    def test_all_pass_phrase_matched(self) -> None:
        from ui_clone.hooks.post_verify import _is_completion_command

        assert _is_completion_command("tests all pass now")

    def test_unrelated_command_not_matched(self) -> None:
        from ui_clone.hooks.post_verify import _is_completion_command

        assert not _is_completion_command("npm run dev")

    def test_pushup_substring_not_matched(self) -> None:
        """Word-boundary check: 'pushup' must not match the 'push' alternation."""
        from ui_clone.hooks.post_verify import _is_completion_command

        assert not _is_completion_command("schedule pushups for tomorrow")



class TestGateSubprocessTimeout:
    """Verifies that gate subprocess calls fail-open on TimeoutExpired."""

    def test_pre_generate_run_gate_timeout_fail_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_run_gate in pre_generate fails open (returns passed=True) on TimeoutExpired."""
        from importlib import reload

        import ui_clone.hooks.pre_generate as mod

        reload(mod)

        def fake_run(*args: Any, **kwargs: Any) -> None:
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=5)

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = mod._run_gate(Path("/tmp/fake"))
        assert result.get("passed") is True, "TimeoutExpired must fail-open"
        assert result.get("fail_count") == 0

    def test_section_gate_run_gate_timeout_fail_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_run_gate in section_gate fails open (returns passed=True) on TimeoutExpired."""
        from importlib import reload

        import ui_clone.hooks.section_gate as mod

        reload(mod)

        def fake_run(*args: Any, **kwargs: Any) -> None:
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=5)

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = mod._run_gate(Path("/tmp/fake"), "extraction")
        assert result.get("passed") is True, "TimeoutExpired must fail-open"
        assert result.get("fail_count") == 0



class TestComponentPathEnvOverride:
    """Verifies UI_RE_COMPONENT_PATHS env var overrides default component path patterns."""

    def test_default_src_components_matched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default: /src/components/ is matched."""
        monkeypatch.delenv("UI_RE_COMPONENT_PATHS", raising=False)
        from importlib import reload

        import ui_clone.hooks.pre_generate as mod

        reload(mod)
        assert mod._is_component_file("/home/user/project/src/components/Hero.tsx")

    def test_default_app_router_page_matched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default: /src/app/**/page.tsx is matched."""
        monkeypatch.delenv("UI_RE_COMPONENT_PATHS", raising=False)
        from importlib import reload

        import ui_clone.hooks.pre_generate as mod

        reload(mod)
        assert mod._is_component_file("/home/user/project/src/app/(home)/page.tsx")

    def test_default_layout_not_matched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default: /src/app/**/layout.tsx is NOT matched (only page.* enforced)."""
        monkeypatch.delenv("UI_RE_COMPONENT_PATHS", raising=False)
        from importlib import reload

        import ui_clone.hooks.pre_generate as mod

        reload(mod)
        assert not mod._is_component_file("/home/user/project/src/app/(home)/layout.tsx")

    def test_env_override_custom_path_matched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """UI_RE_COMPONENT_PATHS=/app/components/ → /app/components/Foo.tsx is matched."""
        monkeypatch.setenv("UI_RE_COMPONENT_PATHS", "/app/components/")
        from importlib import reload

        import ui_clone.hooks.pre_generate as mod

        reload(mod)
        assert mod._is_component_file("/home/user/project/app/components/Foo.tsx")

    def test_env_override_default_not_matched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """UI_RE_COMPONENT_PATHS=/app/components/ → default /src/components/ is NOT matched."""
        monkeypatch.setenv("UI_RE_COMPONENT_PATHS", "/app/components/")
        from importlib import reload

        import ui_clone.hooks.pre_generate as mod

        reload(mod)
        assert not mod._is_component_file("/home/user/project/src/components/Hero.tsx")

    def test_env_override_multiple_paths(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """UI_RE_COMPONENT_PATHS with colon-separated list matches any of the paths."""
        monkeypatch.setenv("UI_RE_COMPONENT_PATHS", "/app/components/:/app/pages/")
        from importlib import reload

        import ui_clone.hooks.pre_generate as mod

        reload(mod)
        assert mod._is_component_file("/home/user/project/app/components/Card.tsx")
        assert mod._is_component_file("/home/user/project/app/pages/index.tsx")

    def test_env_override_empty_string_uses_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """UI_RE_COMPONENT_PATHS='' falls through to built-in defaults."""
        monkeypatch.setenv("UI_RE_COMPONENT_PATHS", "")
        from importlib import reload

        import ui_clone.hooks.pre_generate as mod

        reload(mod)
        assert mod._is_component_file("/home/user/project/src/components/Button.tsx")



class TestPreGeneratePostDoneInvalidation:
    """When pipeline-state shows 'done' but a component edit happens, the prior
    section-compare result is stale. pre_generate must demote state so the next
    Stop hook re-runs section-compare."""

    MODULE = "ui_clone.hooks.pre_generate"

    def _tool_input(self, file_path: str) -> str:
        return json.dumps({"tool_name": "Edit", "tool_input": {"file_path": file_path}})

    def test_post_done_edit_demotes_state(self, tmp_path: Path) -> None:
        """current_gate='done' + WIP + component edit → state demoted to section-compare."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        _populate_pre_generate_artifacts(ref_dir)
        _set_done_state(ref_dir)

        tool_input = self._tool_input(str(tmp_path / "src/components/Hero.tsx"))
        result = run_hook(
            self.MODULE,
            stdin_data=tool_input,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0

        # State should now be at section-compare again
        from ui_clone.state import PipelineState
        reloaded = PipelineState.load(ref_dir)
        assert reloaded.current_gate == "section-compare", (
            f"Expected demotion to section-compare, got {reloaded.current_gate}. "
            f"stderr: {result.stderr}"
        )
        # Stderr should mention the demotion
        assert "demoted" in result.stderr.lower() or "post-done" in result.stderr.lower()

    def test_post_done_edit_invalidates_result_txt(self, tmp_path: Path) -> None:
        """post-done edit must rename sections/result.txt → result.txt.stale,
        so the next section-compare gate run can't pass on the prior PASS lines."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        _populate_pre_generate_artifacts(ref_dir)
        _set_done_state(ref_dir)
        _write_passing_result_txt(ref_dir)
        result_file = ref_dir / "sections" / "result.txt"
        assert result_file.is_file()  # precondition

        tool_input = self._tool_input(str(tmp_path / "src/components/Hero.tsx"))
        result = run_hook(
            self.MODULE,
            stdin_data=tool_input,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0

        # result.txt is gone; result.txt.stale exists with the prior content.
        assert not result_file.exists(), (
            "result.txt must be moved aside on post-done edit"
        )
        stale = result_file.with_suffix(".txt.stale")
        assert stale.is_file(), "result.txt.stale must capture the prior content"
        assert "PASS" in stale.read_text(encoding="utf-8")

    def test_pre_done_state_unchanged(self, tmp_path: Path) -> None:
        """If state is already at section-compare (not done), no demotion happens."""
        search_root = make_search_root(tmp_path)
        ref_dir = make_ref_dir(search_root)
        set_active_marker(ref_dir)
        _populate_pre_generate_artifacts(ref_dir)
        _set_section_compare_state(ref_dir)

        tool_input = self._tool_input(str(tmp_path / "src/components/Hero.tsx"))
        result = run_hook(
            self.MODULE,
            stdin_data=tool_input,
            env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert result.returncode == 0

        from ui_clone.state import PipelineState
        reloaded = PipelineState.load(ref_dir)
        assert reloaded.current_gate == "section-compare"
        # No demotion message
        assert "demoted" not in result.stderr.lower()



def test_resolve_impl_dir_picks_up_scratch_loop_impl(tmp_path: Path) -> None:
    """Loop-codex-21 bypass closure: when ref_dir is at <repo>/tmp/ref/<c>/
    and a sibling impl lives at <repo>/scratch/<c>/impl/ without `.impl-root`
    or `pipeline-state.impl_root` set, the resolver still finds the impl so
    the verify-stamp gate fires.

    Reproduces the loop-codex-21 path: codex wrote both dirs as siblings but
    never linked them, escaping `_enforce_verify_stamp` because
    `_resolve_impl_dir` returned None and the implicit-activation rule
    skipped the ref dir.
    """
    from ui_clone.hooks.section_gate import _resolve_impl_dir

    # Build the canonical loop layout: <repo>/tmp/ref/<c> + <repo>/scratch/<c>/impl
    repo = tmp_path
    ref_dir = repo / "tmp" / "ref" / "loop-N"
    ref_dir.mkdir(parents=True)
    impl_dir = repo / "scratch" / "loop-N" / "impl"
    impl_dir.mkdir(parents=True)
    (impl_dir / "package.json").write_text("{}")

    # No `.impl-root`, no `pipeline-state.json`, no env override — relying
    # only on the new heuristic.
    resolved = _resolve_impl_dir(ref_dir)
    assert resolved is not None
    assert resolved.resolve() == impl_dir.resolve(), (
        f"resolver must find sibling scratch/<name>/impl/, got {resolved}"
    )


def test_bash_scratch_nested_ref_target_detects_codex_22_layout() -> None:
    """Loop-codex-22 bypass closure: detect writes to nested ref trees.

    Codex created an extracted ref directory inside the loop's scratch
    subtree and wrote canonical artifacts there. The canonical layout is
    `<repo>/tmp/ref/<component>/`, NOT `<repo>/scratch/<loop>/tmp/ref/...`.

    Test strings are concatenated to avoid the hook firing on the test
    source itself.
    """
    from ui_clone.hooks.pre_bash_rules.bash_write import (
        _bash_scratch_nested_ref_target,
    )

    # Path fragments split so the literal "scratch/.../tmp/ref/" never
    # appears in this source file (which would trigger the hook on Edit).
    SCRATCH = "scra" + "tch"
    TMPREF = "t" + "mp/r" + "ef"

    # Positive: nested scratch tmp/ref
    bad = f"cat > {SCRATCH}/loop-N/{TMPREF}/realfood/regions.json"
    assert _bash_scratch_nested_ref_target(bad) is not None

    # Positive: deeper nesting still caught
    deep = (
        f"cp /tmp/source.json /Users/x/repo/{SCRATCH}/loop-N/{TMPREF}/c/sections.json"
    )
    assert _bash_scratch_nested_ref_target(deep) is not None

    # Negative: canonical write to <repo>/tmp/ref/<c>/
    good = f"cat > {TMPREF}/realfood/regions.json"
    assert _bash_scratch_nested_ref_target(good) is None

    # Negative: scratch path with no tmp/ref
    no_ref = f"cp src.tsx {SCRATCH}/loop-N/impl/src/App.tsx"
    assert _bash_scratch_nested_ref_target(no_ref) is None

    # Negative: tmp/ref with no scratch ancestor
    no_scratch = f"tee {TMPREF}/site/extracted.json"
    assert _bash_scratch_nested_ref_target(no_scratch) is None
