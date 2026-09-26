"""Version-bump enforcement in scripts/hooks/pre-push-guard.sh (release tier).

Both Claude Code's and Codex's plugin caches are version-keyed, so a
release-branch push with real content must carry a new version. The version
is checked against upstream only: it must be exactly one release step above
upstream's (next patch, minor, or major). This machine's install record is not
consulted -- an unpushed release is reinstalled locally at that one version,
and install.sh replaces a stale same-version cache.

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


def _run_guard(
    work: Path,
    installed_plugins_json: Path | None,
    command: str = "git push origin main",
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
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
    stdin = json.dumps({"tool_input": {"command": command}}, separators=(",", ":"))
    return subprocess.run(
        ["bash", str(GUARD)],
        cwd=cwd or work,
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


def test_blocks_unbumped_version_still_live_on_origin(tmp_path: Path) -> None:
    # fable-20260911 follow-up review (MAJOR): this is now the PRIMARY,
    # machine-independent check — origin/main's own current version is the
    # deterministic ground truth for "is this push actually a new release",
    # unlike the local installed_plugins.json state (which can go stale
    # forever, see test_blocks_unbumped_version_already_installed_locally_only
    # below and the "no local record at all" case here).
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    (work / "README.md").write_text("changed\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "change without bump")

    proc = _run_guard(work, None)  # no local install record at all
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "decision: block" in proc.stderr
    assert "already the version live on origin/main" in proc.stderr


@pytest.mark.parametrize("form", ["dash_c", "no_pager"])
def test_push_with_git_global_options_is_still_guarded(tmp_path: Path, form: str) -> None:
    # `git -C <dir> push` / `git --no-pager push` used to bypass the guard: the
    # detection regex required `git` immediately followed by `push`. -C must
    # also retarget the checked repo (hook cwd here is NOT a git repo).
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    (work / "README.md").write_text("changed\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "change without bump")
    if form == "dash_c":
        outside = tmp_path / "outside"
        outside.mkdir()
        proc = _run_guard(work, None, f"git -C {work} push origin main", cwd=outside)
    else:
        proc = _run_guard(work, None, "git --no-pager push origin main")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "decision: block" in proc.stderr


def test_allows_next_version_already_installed_locally(tmp_path: Path) -> None:
    # An unpushed release is installed and reinstalled locally at origin + 1;
    # this machine's install record must not demand another bump.
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    _write_versions(work, "1.0.1")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "next patch, already installed here")
    installed = _installed(tmp_path, "1.0.1")

    proc = _run_guard(work, installed)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("version", ["1.1.0", "2.0.0"])
def test_allows_next_minor_or_major(tmp_path: Path, version: str) -> None:
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    _write_versions(work, version)
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "next minor or major")

    proc = _run_guard(work, None)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("version", ["1.0.2", "1.2.0", "1.1.1", "3.0.0"])
def test_blocks_version_more_than_one_step_above_origin(tmp_path: Path, version: str) -> None:
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    _write_versions(work, version)
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "skipped a release")

    proc = _run_guard(work, None)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "decision: block" in proc.stderr
    assert "not one release step above 1.0.0" in proc.stderr


def test_allows_push_when_version_was_bumped(tmp_path: Path) -> None:
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    _write_versions(work, "1.0.1")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "bump and change")
    installed = _installed(tmp_path, "1.0.0")

    proc = _run_guard(work, installed)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_allows_push_when_nothing_installed_locally_but_version_was_bumped(
    tmp_path: Path,
) -> None:
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    _write_versions(work, "1.0.1")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "bump, no local install record")

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


def test_all_push_enforces_bump_on_master_when_only_main_is_synced(
    tmp_path: Path,
) -> None:
    # fable-20260911 round 6 follow-up review (MINOR): --all used to check
    # only the FIRST of main/master with both a local branch and a remote-
    # tracking ref, returning immediately -- an unbumped, real change on the
    # SECOND one (master, here) went unchecked as long as main itself
    # happened to be in sync with origin/main.
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    _git(work, "branch", "master")
    _git(work, "push", "-q", "-u", "origin", "master")
    _git(work, "checkout", "-q", "master")
    (work / "README.md").write_text("changed on master\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "change on master without bump")
    _git(work, "checkout", "-q", "main")  # main stays exactly synced with origin/main
    installed = _installed(tmp_path, "1.0.0")

    env = {**os.environ, "UI_CLONE_INSTALLED_PLUGINS_JSON": str(installed)}
    stdin = json.dumps({"tool_input": {"command": "git push --all"}}, separators=(",", ":"))
    proc = subprocess.run(
        ["bash", str(GUARD)], cwd=work, input=stdin, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "decision: block" in proc.stderr


def test_non_origin_remote_name_is_used_for_base_resolution(tmp_path: Path) -> None:
    # fable-20260911 round 6 follow-up review ("also re-check the
    # non-generic parts"): the release-check base used to hardcode "origin"
    # regardless of which remote was actually named on the command line.
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    _git(work, "remote", "rename", "origin", "upstream")
    (work / "README.md").write_text("changed\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "change without bump")
    installed = _installed(tmp_path, "1.0.0")

    env = {**os.environ, "UI_CLONE_INSTALLED_PLUGINS_JSON": str(installed)}
    stdin = json.dumps({"tool_input": {"command": "git push upstream main"}}, separators=(",", ":"))
    proc = subprocess.run(
        ["bash", str(GUARD)], cwd=work, input=stdin, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "decision: block" in proc.stderr


def test_missing_version_files_at_source_skips_gracefully(tmp_path: Path) -> None:
    # fable-20260911 round 6 follow-up review (MINOR): when the pushed
    # source commit has none of the 6 version files at all (rather than a
    # genuine bump mismatch), all six reads come back empty and `unique`
    # was 0 -- printing a confusing all-blank "Version mismatch" block
    # instead of a clear "cannot read version files" skip.
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    _git(work, "checkout", "-q", "--orphan", "no-version-files")
    _git(work, "rm", "-rf", "-q", ".")
    (work / "README.md").write_text("no version files on this branch\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "orphan commit with no version files")
    installed = _installed(tmp_path, "1.0.0")

    env = {**os.environ, "UI_CLONE_INSTALLED_PLUGINS_JSON": str(installed)}
    stdin = json.dumps(
        {"tool_input": {"command": "git push origin no-version-files:main"}},
        separators=(",", ":"),
    )
    proc = subprocess.run(
        ["bash", str(GUARD)], cwd=work, input=stdin, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Cannot read any of the 6 version files" in proc.stderr


def test_url_remote_still_enforces_bump(tmp_path: Path) -> None:
    # fable-20260911 round 6 follow-up review (MAJOR): the round-5 remote-
    # name validation (added to catch `-o ci.skip origin main`) rejected any
    # remote that isn't a NAMED configured remote -- including a direct URL
    # or SCP-style destination (`git push git@host:repo.git main`,
    # `git push https://... main`), both completely legitimate and common.
    # That misparse used to fall through to the "unparseable" fallback,
    # which defaults to the CHECKED-OUT branch -- silently skipping the
    # release tier entirely when checked out on a non-release branch while
    # still pushing real content to "main" by direct URL.
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    (work / "README.md").write_text("changed\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "change without bump")
    _git(work, "checkout", "-q", "-b", "feature")  # checked out on a non-release branch
    installed = _installed(tmp_path, "1.0.0")

    env = {**os.environ, "UI_CLONE_INSTALLED_PLUGINS_JSON": str(installed)}
    stdin = json.dumps(
        {"tool_input": {"command": "git push git@example.com:owner/repo.git main"}},
        separators=(",", ":"),
    )
    proc = subprocess.run(
        ["bash", str(GUARD)], cwd=work, input=stdin, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "decision: block" in proc.stderr


def test_push_option_flag_with_separate_argument_still_enforces_bump(
    tmp_path: Path,
) -> None:
    # fable-20260911 round 5 follow-up review (MINOR): `-o ci.skip origin
    # main` -- a flag that takes its own separate argument -- was not
    # matched by the flag-skipping regex group, so "origin" got misread as
    # the refspec (one token early) and "main" was never seen as the target
    # at all, silently skipping the whole release tier.
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    (work / "README.md").write_text("changed\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "change without bump")
    installed = _installed(tmp_path, "1.0.0")

    env = {**os.environ, "UI_CLONE_INSTALLED_PLUGINS_JSON": str(installed)}
    stdin = json.dumps(
        {"tool_input": {"command": "git push -o ci.skip origin main"}},
        separators=(",", ":"),
    )
    proc = subprocess.run(
        ["bash", str(GUARD)], cwd=work, input=stdin, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "decision: block" in proc.stderr


def test_pushing_a_different_local_branch_than_checked_out_still_enforces_bump(
    tmp_path: Path,
) -> None:
    # fable-20260911 round 5 follow-up review (MAJOR): `git push origin main`
    # pushes the LOCAL branch literally named "main" -- not necessarily HEAD.
    # The guard used to diff/read everything against hardcoded HEAD, so
    # running this exact command while a DIFFERENT branch (synced with
    # origin/main, no diff) was checked out silently checked the wrong
    # branch's content and never blocked.
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    (work / "README.md").write_text("changed\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "change without bump, still on main")
    # Switch to a branch that matches origin/main exactly (no diff from
    # HEAD's perspective) -- only "main" itself carries the unbumped change.
    _git(work, "checkout", "-q", "-b", "other", "origin/main")
    installed = _installed(tmp_path, "1.0.0")

    env = {**os.environ, "UI_CLONE_INSTALLED_PLUGINS_JSON": str(installed)}
    stdin = json.dumps({"tool_input": {"command": "git push origin main"}}, separators=(",", ":"))
    proc = subprocess.run(
        ["bash", str(GUARD)], cwd=work, input=stdin, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "decision: block" in proc.stderr


def test_all_push_enforces_bump_on_local_main_even_when_checked_out_elsewhere(
    tmp_path: Path,
) -> None:
    # fable-20260911 round 5 follow-up review (MAJOR): --all always includes
    # local main/master. The ALL fallback used to resolve to the checked-out
    # branch's own upstream, so being checked out on a SEPARATE, synced
    # branch while local main carried an unbumped change bypassed the check.
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    (work / "README.md").write_text("changed\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "change without bump on main")
    _git(work, "checkout", "-q", "-b", "feat2", "origin/main")
    _git(work, "push", "-q", "-u", "origin", "feat2")  # feat2 synced with its own upstream
    installed = _installed(tmp_path, "1.0.0")

    env = {**os.environ, "UI_CLONE_INSTALLED_PLUGINS_JSON": str(installed)}
    stdin = json.dumps({"tool_input": {"command": "git push --all"}}, separators=(",", ":"))
    proc = subprocess.run(
        ["bash", str(GUARD)], cwd=work, input=stdin, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "decision: block" in proc.stderr


@pytest.mark.parametrize("push_command", ["git push --all", "git push --mirror"])
def test_all_and_mirror_push_still_enforces_bump(tmp_path: Path, push_command: str) -> None:
    # fable-20260911 follow-up review (MAJOR): target_branch resolves to the
    # literal sentinel "ALL" for --all/--mirror (not a real branch), and the
    # refspec-base fix above made `_release_push_base` return "origin/ALL"
    # for it -- a ref that never exists -- silently disabling BOTH the
    # version-bump check and the skills/CHANGELOG coupling check on every
    # --all/--mirror push. Must fall back to the checked-out branch's own
    # upstream (or origin/<branch>) for the ALL case instead.
    work = _make_repo_pushed_at(tmp_path, "1.0.0")
    (work / "README.md").write_text("changed\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "change without bump")
    installed = _installed(tmp_path, "1.0.0")

    env = {**os.environ, "UI_CLONE_INSTALLED_PLUGINS_JSON": str(installed)}
    stdin = json.dumps({"tool_input": {"command": push_command}}, separators=(",", ":"))
    proc = subprocess.run(
        ["bash", str(GUARD)], cwd=work, input=stdin, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "decision: block" in proc.stderr
