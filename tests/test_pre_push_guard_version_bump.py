"""Version-bump enforcement in scripts/hooks/pre-push-guard.sh (release tier).

Local-install-verify follow-up (2026-09-10): both Claude Code's and Codex's
plugin caches are version-keyed. post-push-refresh.sh re-runs install.sh after
every push, but `claude plugin update` / `codex plugin add` are no-ops when
the manifest version matches a version already recorded as installed on this
machine -- the live cache silently stays stale even though origin updated.
pre-push-guard.sh now blocks a release-branch push that reuses a version
already installed on this machine when there is real content to push.

These tests run the hook against an ISOLATED throwaway git repo (not this
repo), so scripts/ci/pre-push-security.sh and ci-local.sh (Tier 1) are absent
there and silently no-op, letting us exercise Tier 2's version-bump check in
isolation without paying the real CI cost or touching the real
~/.claude/plugins/installed_plugins.json.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


GUARD = _project_root() / "scripts" / "hooks" / "pre-push-guard.sh"

_VERSION_FILES = {
    ".claude-plugin/plugin.json": lambda v: json.dumps({"version": v}),
    ".claude-plugin/marketplace.json": lambda v: json.dumps({"plugins": [{"version": v}]}),
    ".codex-plugin/plugin.json": lambda v: json.dumps({"version": v}),
    "package.json": lambda v: json.dumps({"version": v}),
    "pyproject.toml": lambda v: f'[project]\nversion = "{v}"\n',
    "ui_clone/__init__.py": lambda v: f'__version__ = "{v}"\n',
}


def _write_versions(root: Path, version: str) -> None:
    for rel, render in _VERSION_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(version))


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, timeout=20, check=True
    )


def _make_repo_pushed_at(tmp_path: Path, version: str) -> Path:
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    origin.mkdir()
    work.mkdir()
    _git(origin, "init", "--bare", "-q")
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test")
    _git(work, "remote", "add", "origin", str(origin))
    _write_versions(work, version)
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "init")
    _git(work, "push", "-q", "-u", "origin", "main")
    return work


def _run_guard(work: Path, installed_plugins_json: Path | None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ}
    if installed_plugins_json is not None:
        env["UI_CLONE_INSTALLED_PLUGINS_JSON"] = str(installed_plugins_json)
    else:
        # Point at a definitely-absent path so the real machine's actual
        # installed_plugins.json is never consulted by these tests.
        env["UI_CLONE_INSTALLED_PLUGINS_JSON"] = str(work / "does-not-exist.json")
    # Compact (no spaces after separators) — matches the real Claude Code
    # PreToolUse hook payload shape the guard's `grep -qE '"command":"...'`
    # match requires.
    stdin = json.dumps({"tool_input": {"command": "git push origin main"}}, separators=(",", ":"))
    return subprocess.run(
        ["bash", str(GUARD)],
        cwd=work,
        input=stdin,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _installed(tmp_path: Path, version: str) -> Path:
    path = tmp_path / "installed_plugins.json"
    path.write_text(
        json.dumps(
            {
                "plugins": {
                    "ui-clone-skills@voidmatcha": [
                        {"scope": "user", "version": version}
                    ]
                }
            }
        )
    )
    return path


def test_blocks_unbumped_version_already_installed_locally(tmp_path: Path) -> None:
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    # Real content change, version left at 1.0.0.
    (work / "README.md").write_text("changed\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "change without bump")
    installed = _installed(tmp_path, "1.0.0")

    proc = _run_guard(work, installed)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "decision: block" in proc.stderr
    assert "already installed on this machine" in proc.stderr


def test_allows_push_when_version_was_bumped(tmp_path: Path) -> None:
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    _write_versions(work, "1.0.1")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "bump and change")
    installed = _installed(tmp_path, "1.0.0")

    proc = _run_guard(work, installed)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_allows_push_when_nothing_installed_locally(tmp_path: Path) -> None:
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    (work / "README.md").write_text("changed\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "change, no local install record")

    proc = _run_guard(work, None)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_allows_push_with_no_content_change(tmp_path: Path) -> None:
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    installed = _installed(tmp_path, "1.0.0")
    # Nothing committed since the initial push — base == HEAD.
    proc = _run_guard(work, installed)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_feature_branch_push_skips_version_bump_check(tmp_path: Path) -> None:
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    _git(work, "checkout", "-q", "-b", "feature")
    (work / "README.md").write_text("changed\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "feature work, no bump")
    installed = _installed(tmp_path, "1.0.0")

    env = {**os.environ, "UI_CLONE_INSTALLED_PLUGINS_JSON": str(installed)}
    stdin = json.dumps({"tool_input": {"command": "git push origin feature"}}, separators=(",", ":"))
    proc = subprocess.run(
        ["bash", str(GUARD)], cwd=work, input=stdin, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize(
    "push_command",
    ["git push origin HEAD:main", "git push origin feature:main"],
)
def test_refspec_push_to_main_from_a_feature_branch_still_enforces_bump(
    tmp_path: Path, push_command: str
) -> None:
    # fable-20260910 follow-up review (Codex dev-hook parity review, finding
    # 4): the release-push comparison base used to be @{upstream} of whatever
    # branch is checked out locally, which tracks origin/feature (or
    # nothing) here — NOT origin/main, the actual push target. That silently
    # let `git push origin HEAD:main` / `git push origin feature:main` land
    # real, unbumped content on main. The base must follow $target_branch
    # (parsed from the refspec), not the local branch's own upstream.
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    _git(work, "checkout", "-q", "-b", "feature")
    (work / "README.md").write_text("changed\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "feature work, no bump")
    installed = _installed(tmp_path, "1.0.0")

    env = {**os.environ, "UI_CLONE_INSTALLED_PLUGINS_JSON": str(installed)}
    stdin = json.dumps({"tool_input": {"command": push_command}}, separators=(",", ":"))
    proc = subprocess.run(
        ["bash", str(GUARD)], cwd=work, input=stdin, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "decision: block" in proc.stderr
