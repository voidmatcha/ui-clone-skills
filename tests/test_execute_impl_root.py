from __future__ import annotations

from pathlib import Path

from ui_clone.pipeline_phases.execute import _resolve_impl_root


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


def test_single_shot_at_plugin_root_uses_existing_impl(tmp_path: Path) -> None:
    """When cwd IS the plugin root (normal single-shot use), an existing
    impl/ there is the right target."""
    root = tmp_path / "repo"
    (root / "impl").mkdir(parents=True)
    (root / "impl" / "package.json").write_text("{}", encoding="utf-8")
    got = _resolve_impl_root(str(root), root, {}, "")
    assert got == str((root / "impl").resolve())
