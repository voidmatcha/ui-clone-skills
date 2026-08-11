from __future__ import annotations

from pathlib import Path

import pytest

from ui_clone.pipeline import Pipeline
from ui_clone.pipeline_phases.execute import _resolve_impl_root, execute_phases
from ui_clone.state import PipelineState


def test_loop_without_env_anchors_at_cwd_impl(tmp_path: Path) -> None:
    """In a loop working dir with no env and nothing scaffolded yet, impl must
    anchor at <cwd>/impl — NOT the plugin/repo root (the bug that made claude
    loops write to repo-root /impl)."""
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    loop = tmp_path / "scratch" / "loop-claude-99"
    loop.mkdir(parents=True)
    got = _resolve_impl_root(str(plugin), loop, {}, "")
    assert got == str((loop / "impl").resolve())


def test_loop_does_not_adopt_plugin_root_impl(tmp_path: Path) -> None:
    """Even when the plugin root already has a scaffolded impl/, a loop dir must
    not adopt it — that caused cross-round clobbering on the shared dir."""
    plugin = tmp_path / "plugin"
    (plugin / "impl").mkdir(parents=True)
    (plugin / "impl" / "package.json").write_text("{}", encoding="utf-8")
    loop = tmp_path / "scratch" / "loop-claude-99"
    loop.mkdir(parents=True)
    got = _resolve_impl_root(str(plugin), loop, {}, "")
    assert got == str((loop / "impl").resolve())


def test_env_takes_priority(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    loop = tmp_path / "loop"
    loop.mkdir()
    target = tmp_path / "custom-impl"
    got = _resolve_impl_root(str(plugin), loop, {"UI_CLONE_IMPL_ROOT": str(target)}, "")
    assert got == str(target.resolve())


def test_existing_state_root_used_when_no_env(tmp_path: Path) -> None:
    plugin = tmp_path / "p"
    plugin.mkdir()
    loop = tmp_path / "l"
    loop.mkdir()
    got = _resolve_impl_root(str(plugin), loop, {}, "/some/existing/impl")
    assert got == "/some/existing/impl"


def test_existing_state_root_for_other_scratch_run_is_ignored(tmp_path: Path) -> None:
    """When the ref name is known, stale scratch/<other-run> state must not win."""
    repo = tmp_path / "repo"
    ref = repo / "tmp" / "ref" / "project-a-main"
    ref.mkdir(parents=True)
    stale = repo / "scratch" / "project-a-sustainability-04"
    stale.mkdir(parents=True)
    got = _resolve_impl_root(str(repo), repo, {}, str(stale), ref)
    assert got == str((repo / "impl").resolve())


def test_existing_state_root_with_foreign_backlink_is_ignored(tmp_path: Path) -> None:
    """Stale state must not overwrite another ref's explicit ownership."""
    repo = tmp_path / "repo"
    ref = repo / "tmp" / "ref" / "current"
    other_ref = repo / "tmp" / "ref" / "other"
    ref.mkdir(parents=True)
    other_ref.mkdir(parents=True)
    impl = repo / "impl"
    impl.mkdir()
    (impl / ".ref-dir").write_text(str(other_ref) + "\n", encoding="utf-8")

    got = _resolve_impl_root(str(repo), repo, {}, str(impl), ref)

    assert got == str((repo / "scratch" / ref.name).resolve())


def test_root_impl_symlink_to_other_scratch_run_is_ignored(tmp_path: Path) -> None:
    """A repo-root impl symlink must not bind a different scratch run."""
    repo = tmp_path / "repo"
    ref = repo / "tmp" / "ref" / "project-a-main"
    ref.mkdir(parents=True)
    stale = repo / "scratch" / "project-a-sustainability-04"
    stale.mkdir(parents=True)
    (stale / "package.json").write_text("{}", encoding="utf-8")
    (repo / "impl").symlink_to(stale, target_is_directory=True)
    got = _resolve_impl_root(str(repo), repo, {}, "", ref)
    assert got == str((repo / "scratch" / "project-a-main").resolve())


def test_env_root_can_still_override_other_scratch_run(tmp_path: Path) -> None:
    """The cross-scratch guard only rejects stale state, not operator env."""
    repo = tmp_path / "repo"
    ref = repo / "tmp" / "ref" / "project-a-main"
    ref.mkdir(parents=True)
    target = repo / "scratch" / "custom-manual-target"
    stale = repo / "scratch" / "project-a-sustainability-04"
    got = _resolve_impl_root(
        str(repo),
        repo,
        {"UI_CLONE_IMPL_ROOT": str(target)},
        str(stale),
        ref,
    )
    assert got == str(target.resolve())


def test_single_shot_at_plugin_root_uses_existing_impl(tmp_path: Path) -> None:
    """When cwd IS the plugin root (normal single-shot use), an existing
    impl/ there is the right target."""
    root = tmp_path / "repo"
    (root / "impl").mkdir(parents=True)
    (root / "impl" / "package.json").write_text("{}", encoding="utf-8")
    got = _resolve_impl_root(str(root), root, {}, "")
    assert got == str((root / "impl").resolve())


def test_root_impl_with_foreign_backlink_rejected(tmp_path: Path) -> None:
    """D4 (loop-nvti-0): repo-root impl/ carrying ANOTHER ref's `.ref-dir`
    backlink is a different site's tree — adopting it made state-coverage PASS
    against stale eBay leftovers (false positive) and then let the new run
    clobber that tree. A foreign backlink must reject the candidate and
    redirect the default off the shared dir."""
    repo = tmp_path / "repo"
    ref = repo / "tmp" / "ref" / "nvti"
    ref.mkdir(parents=True)
    other_ref = repo / "tmp" / "ref" / "ebay"
    other_ref.mkdir(parents=True)
    impl = repo / "impl"
    impl.mkdir()
    (impl / "package.json").write_text("{}", encoding="utf-8")
    (impl / ".ref-dir").write_text(str(other_ref) + "\n", encoding="utf-8")
    got = _resolve_impl_root(str(repo), repo, {}, "", ref)
    assert got != str(impl.resolve())
    assert got == str((repo / "scratch" / "nvti").resolve())


def test_root_impl_with_matching_backlink_adopted(tmp_path: Path) -> None:
    """The handshake in the other direction: a backlink resolving to THIS ref
    is the same run's impl — adopt it (resume/continue flows)."""
    repo = tmp_path / "repo"
    ref = repo / "tmp" / "ref" / "nvti"
    ref.mkdir(parents=True)
    impl = repo / "impl"
    impl.mkdir()
    (impl / "package.json").write_text("{}", encoding="utf-8")
    (impl / ".ref-dir").write_text(str(ref) + "\n", encoding="utf-8")
    got = _resolve_impl_root(str(repo), repo, {}, "", ref)
    assert got == str(impl.resolve())


def test_root_impl_without_backlink_keeps_legacy_adoption(tmp_path: Path) -> None:
    """Back-compat: impls scaffolded before the backlink existed have no
    `.ref-dir`; keep adopting them at the plugin root (single-shot flows)."""
    repo = tmp_path / "repo"
    ref = repo / "tmp" / "ref" / "nvti"
    ref.mkdir(parents=True)
    impl = repo / "impl"
    impl.mkdir()
    (impl / "package.json").write_text("{}", encoding="utf-8")
    got = _resolve_impl_root(str(repo), repo, {}, "", ref)
    assert got == str(impl.resolve())


def test_execute_phases_backfills_impl_backlink_for_stable_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later run must bind an impl created after the state was first saved."""
    repo = tmp_path / "repo"
    ref = repo / "tmp" / "ref" / "current"
    impl = repo / "impl"
    ref.mkdir(parents=True)
    impl.mkdir(parents=True)

    state = PipelineState.load(ref)
    state.impl_root = str(impl.resolve())
    state.save(ref)
    (ref / ".impl-root").write_text(str(impl.resolve()) + "\n", encoding="utf-8")

    monkeypatch.chdir(repo)
    for key in (
        "PLUGIN_ROOT",
        "CLAUDE_PLUGIN_ROOT",
        "CODEX_PLUGIN_ROOT",
        "UI_CLONE_IMPL_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)

    pipeline = Pipeline(
        "https://example.com", ref.name, "session", run_dir=str(ref)
    )
    result = execute_phases(pipeline, ())

    assert result == 0
    assert (impl / ".ref-dir").read_text(encoding="utf-8") == str(ref.resolve()) + "\n"


def test_execute_phases_fails_when_impl_backlink_cannot_be_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Phase execution must not succeed with an unowned implementation."""
    repo = tmp_path / "repo"
    ref = repo / "tmp" / "ref" / "current"
    impl = repo / "impl"
    ref.mkdir(parents=True)
    impl.mkdir(parents=True)

    state = PipelineState.load(ref)
    state.impl_root = str(impl.resolve())
    state.save(ref)
    (ref / ".impl-root").write_text(str(impl.resolve()) + "\n", encoding="utf-8")

    monkeypatch.chdir(repo)
    for key in (
        "PLUGIN_ROOT",
        "CLAUDE_PLUGIN_ROOT",
        "CODEX_PLUGIN_ROOT",
        "UI_CLONE_IMPL_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)
    pipeline = Pipeline(
        "https://example.com", ref.name, "session", run_dir=str(ref)
    )

    original_write_text = Path.write_text

    def fail_backlink_write(
        path: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        if path.name == ".ref-dir":
            raise OSError("read-only impl")
        return original_write_text(
            path, data, encoding=encoding, errors=errors, newline=newline
        )

    monkeypatch.setattr(Path, "write_text", fail_backlink_write)

    result = execute_phases(pipeline, ())

    assert result == 1
    assert "cannot persist impl ownership" in capsys.readouterr().out
