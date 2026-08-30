from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"


@pytest.fixture(autouse=True)
def _isolate_installer_python_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UI_CLONE_PYTHON_CANDIDATES", "python3")


def _extract_shell_quote() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    match = re.search(r"^shell_quote\(\) \{\n(?:.*\n)*?^\}\n", text, re.MULTILINE)
    assert match is not None, "shell_quote helper not found in install.sh"
    return match.group(0)


def test_codex_install_does_not_register_working_repo_as_marketplace() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")

    assert 'claude plugin marketplace add "$REPO_ROOT"' not in text
    assert 'codex plugin marketplace add "$REPO_ROOT"' not in text
    assert 'codex plugin marketplace add $(shell_quote "$REPO_ROOT")' not in text


def test_codex_install_cleans_legacy_global_hooks_instead_of_merging() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    match = re.search(
        r"^cleanup_legacy_codex_hooks\(\) \{\n(?:.*\n)*?^\}\n",
        text,
        re.MULTILINE,
    )

    assert match is not None, "installer must expose legacy global-hook cleanup"
    body = match.group(0)
    assert "run_codex_hooks_manager remove" in body
    assert "-m ui_clone.codex_hooks_install" in text
    assert "codex_hooks_install merge" not in text
    assert "hooks/codex-hooks.json" not in body
    assert 'hooks_file="${CODEX_HOME:-$HOME/.codex}/hooks.json"' in body


def test_claude_install_uses_real_file_source_not_the_symlink_projection() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")

    assert 'claude plugin marketplace add "$CLAUDE_PLUGIN_SRC"' in text
    assert 'claude plugin marketplace add "$CODEX_PLUGIN_DIR"' not in text, (
        "the symlink projection must never be the Claude marketplace source"
    )
    assert "Refreshing Claude marketplace" in text
    assert "current_source" in text


def test_plugin_projection_prunes_stale_local_artifacts() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")

    keep_match = re.search(
        r'^CODEX_PLUGIN_PROJECTION_KEEP="([^"]+)"',
        text,
        re.MULTILINE,
    )
    assert keep_match is not None
    assert 'for existing in "$plugin_dir"/* "$plugin_dir"/.[!.]* "$plugin_dir"/..?*' in text
    assert 'case " $CODEX_PLUGIN_PROJECTION_KEEP " in' in text
    assert 'rm -rf "$existing"' in text
    assert ".venv" not in keep_match.group(1)


def test_hook_shim_resolves_symlink_projection_to_real_project_root() -> None:
    text = (REPO_ROOT / "hooks" / "shim.sh").read_text(encoding="utf-8")

    assert "realpath \"$script_path\"" in text
    assert 'uv run --project "$project_root"' in text
    assert 'dirname "$script_path")/..' in text


def test_codex_install_uses_personal_projection_marketplace() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")

    assert 'CODEX_PERSONAL_MARKETPLACE="$HOME/.agents/plugins/marketplace.json"' in text
    assert 'CODEX_PLUGIN_DIR="$HOME/plugins/$PLUGIN_NAME"' in text
    assert 'codex plugin add "$PLUGIN_NAME@$CODEX_MARKETPLACE_NAME"' in text

    # The marketplace source must NOT be the projection. Codex copies the source
    # into its own versioned cache and skips symlinks while copying, and the
    # projection is entirely symlinks — pointing at it produced a cache holding one
    # empty skills/ dir and no plugin.json while `codex plugin list` still said
    # "installed, enabled". Derive it from the staged real-file tree instead.
    assert 'CODEX_PLUGIN_SOURCE_PATH="./plugins/$PLUGIN_NAME"' not in text
    assert 'CODEX_PLUGIN_SOURCE_PATH="${CLAUDE_PLUGIN_SRC#"$HOME"/}"' in text.replace(
        './${CLAUDE_PLUGIN_SRC#"$HOME"/}', '${CLAUDE_PLUGIN_SRC#"$HOME"/}'
    )
    # Entries written before that move still name the projection; ownership and
    # removal have to keep recognising them or an upgrade strands them.
    assert 'CODEX_PLUGIN_SOURCE_PATH_LEGACY="./plugins/$PLUGIN_NAME"' in text


def test_codex_install_projects_and_installs_native_agents() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")

    assert (
        "CODEX_NATIVE_AGENTS_DIR=\"${CODEX_HOME:-$HOME/.codex}/agents\"" in text
    )
    assert 'CODEX_PUBLIC_SKILLS="ui-reverse-engineering ui-capture visual-debug"' in text
    assert (
        'CODEX_PLUGIN_PROJECTION_ITEMS=".claude-plugin .codex-plugin .codex bin hooks scripts'
        in text
    )
    assert "install_codex_native_agents" in text
    assert "for item in $CODEX_PLUGIN_PROJECTION_ITEMS" in text
    assert "for item in .codex-plugin .codex skills hooks scripts" not in text
    assert "for skill in $CODEX_PUBLIC_SKILLS" in text
    assert 'ln -s "$src" "$dst"' in text




def test_codex_native_agent_relink_repairs_symlink_left_broken_by_a_rename() -> None:
    """Renaming the checkout leaves every projected agent symlink dangling at the
    old path. Re-running the installer must repair them, but the existence test
    guarding the relink is ``[ -e "$dst" ]``, which FOLLOWS the link and is
    therefore false for a broken one — so the guard does not fire, execution
    reaches ``ln -s`` on a path that already exists, and the link stays broken
    forever. A broken link must be replaced, not stepped over."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    marker = 'dst="$CODEX_NATIVE_AGENTS_DIR/$(basename "$src")"'
    assert marker in text, "codex native agent relink loop moved — update this test"
    body = text.split(marker, 1)[1].split("write_codex_personal_marketplace", 1)[0]
    assert (
        '[ -L "$dst" ] && [ ! -e "$dst" ]' in body
    ), "relink loop never clears a dangling symlink before ln -s"


def test_projection_prune_removes_a_dangling_symlink() -> None:
    """The projection cleanup loop skips entries failing ``[ -e ]``. A symlink
    left dangling by a source rename fails that test, so stale non-allowlisted
    links survive every reinstall — the prune silently exempts exactly the
    entries it exists to remove."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert (
        '[ -e "$existing" ] || [ -L "$existing" ] || continue' in text
    ), "projection prune still skips dangling symlinks"


def test_install_creates_local_ui_clone_bin_from_projection() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")

    assert 'LOCAL_BIN_DIR="${UI_CLONE_LOCAL_BIN_DIR:-$HOME/.local/bin}"' in text
    assert 'LOCAL_CLI_BIN="$LOCAL_BIN_DIR/ui-clone"' in text
    assert 'install_local_cli_bin()' in text
    assert 'local src="$CODEX_PLUGIN_DIR/bin/ui-clone"' in text
    assert 'ln -s "$src" "$dst"' in text
    assert 'install_local_cli_bin || return' in text

def test_shell_quote_produces_copy_paste_safe_codex_command() -> None:
    helper = _extract_shell_quote()
    cases = {
        "/path/with spaces/repo": "codex plugin marketplace add '/path/with spaces/repo'",
        "/tmp/owner's repo; $(whoami) & data": "codex plugin marketplace add '/tmp/owner'\\''s repo; $(whoami) & data'",
    }

    for path, expected in cases.items():
        script = f"""
{helper}
REPO_ROOT={shlex.quote(path)}
printf "codex plugin marketplace add %s\\n" "$(shell_quote "$REPO_ROOT")"
"""
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            check=True,
        )

        assert result.stdout == f"{expected}\n"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _write_python_wrapper(path: Path) -> None:
    real_python = shutil.which("python3")
    assert real_python is not None
    _write_executable(
        path,
        f"""#!/usr/bin/env bash
if [ "$#" -eq 2 ] && [ "$1" = "-c" ] && [[ "$2" == *"sys.version_info >= (3, 11)"* ]]; then
  printf "%s\\n" {shlex.quote(str(path))}
  exit 0
fi
if [ "$#" -ge 4 ] && [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "uninstall" ]; then
  printf "python3 %s\\n" "$*" >> "$COMMAND_LOG"
  exit 0
fi
exec {shlex.quote(real_python)} "$@"
""",
    )


def _write_fake_editable_python(
    path: Path,
    *,
    resolved_path: Path | None = None,
    supported: bool = True,
    require_break_system_packages_for_install: bool = False,
    require_break_system_packages_for_uninstall: bool = False,
) -> None:
    resolved = resolved_path or path
    state = path.with_suffix(".installed")
    discovery = f"printf '%s\\n' {shlex.quote(str(resolved))}" if supported else ":"
    _write_executable(
        path,
        f"""#!/usr/bin/env bash
if [ "$#" -eq 2 ] && [ "$1" = "-c" ] && [[ "$2" == *"sys.version_info >= (3, 11)"* ]]; then
  {discovery}
  exit 0
fi
if [ "$#" -ge 3 ] && [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "install" ]; then
  printf "%s|%s\\n" {shlex.quote(str(path))} "$*" >> "$COMMAND_LOG"
  if [ {str(require_break_system_packages_for_install).lower()} = true ] &&
     [[ " $* " != *" --break-system-packages "* ]]; then
    printf "externally-managed-environment\\n" >&2
    exit 1
  fi
  touch {shlex.quote(str(state))}
  exit 0
fi
if [ "$#" -ge 3 ] && [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "uninstall" ]; then
  printf "%s|%s\\n" {shlex.quote(str(path))} "$*" >> "$COMMAND_LOG"
  if [ {str(require_break_system_packages_for_uninstall).lower()} = true ] &&
     [[ " $* " != *" --break-system-packages "* ]]; then
    exit 1
  fi
  rm -f {shlex.quote(str(state))}
  exit 0
fi
if [ "$#" -eq 1 ] && [ "$1" = "-" ]; then
  printf "owned\\n"
  exit 0
fi
if [ "$#" -eq 2 ] && [ "$1" = "-c" ] && [[ "$2" == *"installed_package"* ]]; then
  printf "%s|verify|%s\\n" {shlex.quote(str(path))} "$PWD" >> "$COMMAND_LOG"
  [ -f {shlex.quote(str(state))} ]
  exit $?
fi
exit 1
""",
    )


def test_editable_install_covers_deduped_supported_interpreters(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    first = fake_bin / "python-first"
    second = fake_bin / "python-second"
    duplicate = fake_bin / "python-duplicate"
    unsupported = fake_bin / "python-unsupported"
    _write_fake_editable_python(first)
    _write_fake_editable_python(second)
    _write_fake_editable_python(duplicate, resolved_path=first)
    _write_fake_editable_python(unsupported, supported=False)
    command_log = tmp_path / "commands.log"

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "UI_CLONE_PYTHON_CANDIDATES": ":".join(
                map(str, (first, duplicate, unsupported, second))
            ),
        }
    )
    result = subprocess.run(
        [
            "bash",
            str(INSTALL_SH),
            "--no-deps",
            "--no-marketplace",
            "--claude-only",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    lines = command_log.read_text(encoding="utf-8").splitlines()
    installs = [line for line in lines if "|-m pip install " in line]
    assert len(installs) == 2
    assert {line.split("|", 1)[0] for line in installs} == {str(first), str(second)}
    assert all("--user -e" in line for line in installs)
    assert all("--break-system-packages" not in line for line in installs)
    assert {line for line in lines if "|verify|/" in line} == {
        f"{first}|verify|/",
        f"{second}|verify|/",
    }
    assert first.with_suffix(".installed").exists()
    assert second.with_suffix(".installed").exists()
    assert str(unsupported) not in result.stdout


def test_editable_install_hides_recovered_pep668_probe_error(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    python = fake_bin / "python-homebrew"
    _write_fake_editable_python(
        python,
        require_break_system_packages_for_install=True,
    )
    command_log = tmp_path / "commands.log"
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "UI_CLONE_PYTHON_CANDIDATES": str(python),
        }
    )

    result = subprocess.run(
        [
            "bash",
            str(INSTALL_SH),
            "--no-deps",
            "--no-marketplace",
            "--claude-only",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "externally-managed-environment" not in result.stdout + result.stderr
    installs = [
        line
        for line in command_log.read_text(encoding="utf-8").splitlines()
        if "|-m pip install " in line
    ]
    assert installs == [
        f"{python}|-m pip install --quiet --user -e {REPO_ROOT}",
        (
            f"{python}|-m pip install --quiet --user --break-system-packages "
            f"-e {REPO_ROOT}"
        ),
    ]
    assert python.with_suffix(".installed").exists()


def test_uninstall_checks_every_deduped_supported_interpreter(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    first = fake_bin / "python-first"
    second = fake_bin / "python-second"
    duplicate = fake_bin / "python-duplicate"
    unsupported = fake_bin / "python-unsupported"
    _write_fake_editable_python(first)
    _write_fake_editable_python(second)
    _write_fake_editable_python(duplicate, resolved_path=first)
    _write_fake_editable_python(unsupported, supported=False)
    first.with_suffix(".installed").touch()
    second.with_suffix(".installed").touch()
    command_log = tmp_path / "commands.log"

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CODEX_HOME": str(home / ".codex"),
            "AGENTS_SKILLS_DIR": str(home / ".agents" / "skills"),
            "UI_CLONE_LOCAL_BIN_DIR": str(home / ".local" / "bin"),
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "UI_CLONE_PYTHON_CANDIDATES": ":".join(
                map(str, (first, duplicate, unsupported, second))
            ),
        }
    )
    subprocess.run(
        ["bash", str(INSTALL_SH), "--uninstall"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    uninstalls = [
        line
        for line in command_log.read_text(encoding="utf-8").splitlines()
        if "|-m pip uninstall " in line
    ]
    assert uninstalls == [
        f"{first}|-m pip uninstall -y ui-clone-skills",
        f"{second}|-m pip uninstall -y ui-clone-skills",
    ]
    assert not first.with_suffix(".installed").exists()
    assert not second.with_suffix(".installed").exists()


def test_uninstall_retries_externally_managed_python_with_break_flag(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    python = fake_bin / "python-homebrew"
    _write_fake_editable_python(
        python,
        require_break_system_packages_for_uninstall=True,
    )
    python.with_suffix(".installed").touch()
    command_log = tmp_path / "commands.log"
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CODEX_HOME": str(home / ".codex"),
            "AGENTS_SKILLS_DIR": str(home / ".agents" / "skills"),
            "UI_CLONE_LOCAL_BIN_DIR": str(home / ".local" / "bin"),
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "UI_CLONE_PYTHON_CANDIDATES": str(python),
        }
    )

    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--uninstall"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    uninstalls = [
        line
        for line in command_log.read_text(encoding="utf-8").splitlines()
        if "|-m pip uninstall " in line
    ]
    assert uninstalls == [
        f"{python}|-m pip uninstall -y ui-clone-skills",
        f"{python}|-m pip uninstall -y --break-system-packages ui-clone-skills",
    ]
    assert not python.with_suffix(".installed").exists()


def _write_claude_plugin_source(path: Path) -> None:
    plugin_dir = path / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "ui-clone-skills", "version": "0.0.0"}) + "\n",
        encoding="utf-8",
    )
    (plugin_dir / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "voidmatcha",
                "plugins": [{"name": "ui-clone-skills", "source": "./"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_uninstall_removes_owned_artifacts_and_preserves_conflicts(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    codex_home = home / ".codex"
    agents_skills = home / ".agents" / "skills"
    local_bin = home / ".local" / "bin"
    plugin_dir = home / "plugins" / "ui-clone-skills"
    fake_bin = tmp_path / "bin"
    command_log = tmp_path / "commands.log"

    for path in (codex_home / "agents", agents_skills, local_bin, fake_bin):
        path.mkdir(parents=True, exist_ok=True)

    _write_executable(
        fake_bin / "codex",
        '#!/usr/bin/env bash\nprintf "codex %s\\n" "$*" >> "$COMMAND_LOG"\n',
    )
    _write_executable(
        fake_bin / "claude",
        '#!/usr/bin/env bash\nprintf "claude %s\\n" "$*" >> "$COMMAND_LOG"\n',
    )
    _write_python_wrapper(fake_bin / "python3")

    plugin_dir.mkdir(parents=True)
    for item in (".codex-plugin", "bin", "README.md"):
        source = REPO_ROOT / item
        (plugin_dir / item).symlink_to(source, target_is_directory=source.is_dir())
    (plugin_dir / "skills").mkdir()
    for skill in ("ui-reverse-engineering", "ui-capture", "visual-debug"):
        (plugin_dir / "skills" / skill).symlink_to(
            REPO_ROOT / "skills" / skill,
            target_is_directory=True,
        )
    (plugin_dir / "user-note.txt").write_text("keep me\n", encoding="utf-8")

    owned_cli = local_bin / "ui-clone"
    owned_cli.symlink_to(plugin_dir / "bin" / "ui-clone")

    shutil.copytree(
        REPO_ROOT / "skills" / "ui-reverse-engineering",
        agents_skills / "ui-reverse-engineering",
    )
    shutil.copytree(
        REPO_ROOT / "skills" / "ui-capture",
        agents_skills / "ui-capture",
    )
    conflicting_skill = agents_skills / "visual-debug"
    shutil.copytree(REPO_ROOT / "skills" / "visual-debug", conflicting_skill)

    native_agents = sorted((REPO_ROOT / ".codex" / "agents").glob("*.toml"))
    assert len(native_agents) >= 2
    owned_agent = codex_home / "agents" / native_agents[0].name
    owned_agent.symlink_to(native_agents[0])
    conflicting_agent = codex_home / "agents" / native_agents[1].name
    conflicting_agent.write_text("user owned\n", encoding="utf-8")

    marketplace = home / ".agents" / "plugins" / "marketplace.json"
    marketplace.parent.mkdir(parents=True)
    marketplace.write_text(
        json.dumps(
            {
                "name": "local",
                "interface": {"displayName": "Local Plugins"},
                "plugins": [
                    {
                        "name": "ui-clone-skills",
                        "source": {
                            "source": "local",
                            "path": "./plugins/ui-clone-skills",
                        },
                    },
                    {
                        "name": "ui-clone-skills",
                        "source": {"source": "local", "path": "./other/plugin"},
                    },
                    {"name": "unrelated", "source": {"source": "git", "url": "x"}},
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    marketplace.chmod(0o640)

    marker_dir = home / ".config" / "ui-clone-skills"
    marker_dir.mkdir(parents=True)
    (marker_dir / "root").write_text(f"{REPO_ROOT}\n", encoding="utf-8")
    legacy_claude_source = home / ".local" / "share" / "ui-clone-skills"
    _write_claude_plugin_source(legacy_claude_source)
    legacy_capture = legacy_claude_source / "skills" / "ui-capture"
    shutil.copytree(REPO_ROOT / "skills" / "ui-capture", legacy_capture)
    for path in (
        agents_skills / "ui-capture" / "detection.md",
        legacy_capture / "detection.md",
    ):
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\nlegacy installed version\n")
    known_marketplaces = home / ".claude" / "plugins" / "known_marketplaces.json"
    known_marketplaces.parent.mkdir(parents=True)
    known_marketplaces.write_text(
        json.dumps(
            {
                "voidmatcha": {
                    "source": {
                        "source": "directory",
                        "path": str(legacy_claude_source),
                    },
                    "installLocation": str(legacy_claude_source),
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CODEX_HOME": str(codex_home),
            "AGENTS_SKILLS_DIR": str(agents_skills),
            "UI_CLONE_LOCAL_BIN_DIR": str(local_bin),
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--uninstall"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert not owned_cli.is_symlink()
    assert not (agents_skills / "ui-reverse-engineering").exists()
    assert not (agents_skills / "ui-capture").exists()
    assert not conflicting_skill.exists()
    assert not owned_agent.is_symlink()
    assert conflicting_agent.read_text() == "user owned\n"
    assert (plugin_dir / "user-note.txt").read_text() == "keep me\n"
    assert not (plugin_dir / ".codex-plugin").exists()
    assert not (plugin_dir / "bin").exists()
    assert not (plugin_dir / "README.md").exists()
    assert not (plugin_dir / "skills").exists()
    assert not (marker_dir / "root").exists()

    remaining_plugins = json.loads(marketplace.read_text())["plugins"]
    assert marketplace.stat().st_mode & 0o777 == 0o640
    assert [item["source"].get("path") for item in remaining_plugins] == [
        "./other/plugin",
        None,
    ]
    assert "codex plugin remove ui-clone-skills@local" in command_log.read_text()
    assert "python3 -m pip uninstall -y ui-clone-skills" in command_log.read_text()
    assert (
        "claude plugin uninstall ui-clone-skills@voidmatcha"
        in command_log.read_text()
    )
    assert "preserving user-owned path" in result.stdout


def test_uninstall_preserves_conflicting_cli_and_install_marker(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    local_bin = home / ".local" / "bin"
    marker_dir = home / ".config" / "ui-clone-skills"
    local_bin.mkdir(parents=True)
    marker_dir.mkdir(parents=True)
    cli = local_bin / "ui-clone"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    marker = marker_dir / "root"
    marker.write_text("/another/checkout\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "commands.log"
    _write_python_wrapper(fake_bin / "python3")

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CODEX_HOME": str(home / ".codex"),
            "AGENTS_SKILLS_DIR": str(home / ".agents" / "skills"),
            "UI_CLONE_LOCAL_BIN_DIR": str(local_bin),
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
        }
    )
    subprocess.run(
        ["bash", str(INSTALL_SH), "--uninstall"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert cli.read_text() == "#!/bin/sh\n"
    assert marker.read_text() == "/another/checkout\n"


def test_uninstall_preserves_non_owned_python_distributions(tmp_path: Path) -> None:
    cases = [
        (
            "non-editable",
            {"url": REPO_ROOT.as_uri(), "dir_info": {"editable": False}},
            "distribution is not editable",
        ),
        (
            "different-source",
            {
                "url": (tmp_path / "another-checkout").as_uri(),
                "dir_info": {"editable": True},
            },
            "editable source is",
        ),
    ]

    for name, direct_url, expected_warning in cases:
        case_dir = tmp_path / name
        home = case_dir / "home"
        fake_bin = case_dir / "bin"
        fake_site = case_dir / "site-packages"
        dist_info = fake_site / "ui_clone_skills-9.9.9.dist-info"
        fake_bin.mkdir(parents=True)
        dist_info.mkdir(parents=True)
        (dist_info / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: ui-clone-skills\nVersion: 9.9.9\n",
            encoding="utf-8",
        )
        (dist_info / "direct_url.json").write_text(
            json.dumps(direct_url) + "\n",
            encoding="utf-8",
        )
        command_log = case_dir / "commands.log"
        _write_python_wrapper(fake_bin / "python3")

        env = os.environ.copy()
        env.update(
            {
                "HOME": str(home),
                "CODEX_HOME": str(home / ".codex"),
                "AGENTS_SKILLS_DIR": str(home / ".agents" / "skills"),
                "UI_CLONE_LOCAL_BIN_DIR": str(home / ".local" / "bin"),
                "COMMAND_LOG": str(command_log),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "PYTHONPATH": str(fake_site),
            }
        )
        result = subprocess.run(
            ["bash", str(INSTALL_SH), "--uninstall"],
            cwd=case_dir,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )

        assert expected_warning in result.stdout
        assert not command_log.exists()


def test_public_skill_receipt_handles_source_upgrade_and_user_edits(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copy2(INSTALL_SH, checkout / "install.sh")
    shutil.copy2(REPO_ROOT / "pyproject.toml", checkout / "pyproject.toml")
    shutil.copytree(REPO_ROOT / ".claude-plugin", checkout / ".claude-plugin")
    shutil.copytree(REPO_ROOT / "skills", checkout / "skills")
    shutil.copytree(REPO_ROOT / "bin", checkout / "bin")
    # hooks/hooks.json is the file that decides whether the installed plugin
    # has any hooks at all; the installer refuses a source without it.
    shutil.copytree(REPO_ROOT / "hooks", checkout / "hooks")

    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "commands.log"
    _write_python_wrapper(fake_bin / "python3")
    _write_executable(
        fake_bin / "claude",
        '#!/usr/bin/env bash\nprintf "claude %s\\n" "$*" >> "$COMMAND_LOG"\n',
    )
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "AGENTS_SKILLS_DIR": str(home / ".agents" / "skills"),
            "UI_CLONE_LOCAL_BIN_DIR": str(home / ".local" / "bin"),
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )

    subprocess.run(
        ["bash", str(checkout / "install.sh"), "--no-deps", "--claude-only"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    ownership = home / ".config" / "ui-clone-skills" / "public-skills.json"
    receipt = json.loads(ownership.read_text(encoding="utf-8"))
    assert receipt["skills"]["ui-capture"]["source"] == str(checkout)
    assert receipt["skills"]["ui-capture"]["sha256"]
    assert "version" in receipt["skills"]["ui-capture"]

    with (checkout / "skills" / "ui-capture" / "detection.md").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write("\nsource version N+1\n")
    subprocess.run(
        ["bash", str(checkout / "install.sh"), "--uninstall"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert not (home / ".agents" / "skills" / "ui-capture").exists()

    subprocess.run(
        ["bash", str(checkout / "install.sh"), "--no-deps", "--claude-only"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    customized = home / ".agents" / "skills" / "visual-debug"
    (customized / "user-notes.md").write_text("keep me\n", encoding="utf-8")
    result = subprocess.run(
        ["bash", str(checkout / "install.sh"), "--uninstall"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "post-install edits" in result.stdout
    assert "Uninstall complete." not in result.stdout
    assert (customized / "user-notes.md").read_text(encoding="utf-8") == "keep me\n"
    assert ownership.is_file()


def test_legacy_skill_history_removes_untouched_and_preserves_unknown(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copy2(INSTALL_SH, checkout / "install.sh")
    shutil.copy2(REPO_ROOT / "pyproject.toml", checkout / "pyproject.toml")
    shutil.copytree(REPO_ROOT / ".claude-plugin", checkout / ".claude-plugin")
    shutil.copytree(REPO_ROOT / "skills", checkout / "skills")
    shutil.copytree(REPO_ROOT / "bin", checkout / "bin")
    # hooks/hooks.json is the file that decides whether the installed plugin
    # has any hooks at all; the installer refuses a source without it.
    shutil.copytree(REPO_ROOT / "hooks", checkout / "hooks")
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.name", "Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "commit", "-qm", "version N"],
        check=True,
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "commands.log"
    _write_python_wrapper(fake_bin / "python3")
    _write_executable(
        fake_bin / "claude",
        '#!/usr/bin/env bash\nprintf "claude %s\\n" "$*" >> "$COMMAND_LOG"\n',
    )

    def environment(home: Path) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(home),
                "AGENTS_SKILLS_DIR": str(home / ".agents" / "skills"),
                "UI_CLONE_LOCAL_BIN_DIR": str(home / ".local" / "bin"),
                "COMMAND_LOG": str(command_log),
                "PATH": f"{fake_bin}:{env['PATH']}",
            }
        )
        return env

    legacy_home = tmp_path / "legacy-home"
    legacy_env = environment(legacy_home)
    subprocess.run(
        ["bash", str(checkout / "install.sh"), "--no-deps", "--claude-only"],
        cwd=tmp_path,
        env=legacy_env,
        capture_output=True,
        text=True,
        check=True,
    )
    (legacy_home / ".config" / "ui-clone-skills" / "public-skills.json").unlink()
    with (checkout / "skills" / "ui-capture" / "detection.md").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write("\nsource version N+1\n")
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "commit", "-qm", "version N+1"],
        check=True,
    )
    subprocess.run(
        ["bash", str(checkout / "install.sh"), "--uninstall"],
        cwd=tmp_path,
        env=legacy_env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert not (legacy_home / ".agents" / "skills" / "ui-capture").exists()

    unknown_home = tmp_path / "unknown-home"
    unknown_env = environment(unknown_home)
    subprocess.run(
        ["bash", str(checkout / "install.sh"), "--no-deps", "--claude-only"],
        cwd=tmp_path,
        env=unknown_env,
        capture_output=True,
        text=True,
        check=True,
    )
    (unknown_home / ".config" / "ui-clone-skills" / "public-skills.json").unlink()
    customized = unknown_home / ".agents" / "skills" / "ui-capture"
    (customized / "user-notes.md").write_text("keep me\n", encoding="utf-8")
    result = subprocess.run(
        ["bash", str(checkout / "install.sh"), "--uninstall"],
        cwd=tmp_path,
        env=unknown_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "cannot prove ownership" in result.stdout
    assert "Uninstall complete." not in result.stdout
    assert (customized / "user-notes.md").read_text(encoding="utf-8") == "keep me\n"


def test_marketplace_rewrite_is_atomic_and_preserves_permissions(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copy2(INSTALL_SH, checkout / "install.sh")
    shutil.copy2(REPO_ROOT / "pyproject.toml", checkout / "pyproject.toml")
    shutil.copytree(REPO_ROOT / ".claude-plugin", checkout / ".claude-plugin")
    shutil.copytree(REPO_ROOT / ".codex", checkout / ".codex")
    shutil.copytree(REPO_ROOT / "skills", checkout / "skills")
    shutil.copytree(REPO_ROOT / "bin", checkout / "bin")
    # hooks/hooks.json is the file that decides whether the installed plugin
    # has any hooks at all; the installer refuses a source without it.
    shutil.copytree(REPO_ROOT / "hooks", checkout / "hooks")

    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "commands.log"
    _write_python_wrapper(fake_bin / "python3")
    _write_executable(
        fake_bin / "codex",
        '#!/usr/bin/env bash\nprintf "codex %s\\n" "$*" >> "$COMMAND_LOG"\n',
    )
    marketplace = home / ".agents" / "plugins" / "marketplace.json"
    marketplace.parent.mkdir(parents=True)
    original = {
        "name": "local",
        "plugins": [{"name": "unrelated", "source": {"source": "git", "url": "x"}}],
    }
    marketplace.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
    marketplace.chmod(0o640)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CODEX_HOME": str(home / ".codex"),
            "AGENTS_SKILLS_DIR": str(home / ".agents" / "skills"),
            "UI_CLONE_LOCAL_BIN_DIR": str(home / ".local" / "bin"),
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )

    subprocess.run(
        ["bash", str(checkout / "install.sh"), "--no-deps", "--codex-only"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    installed = json.loads(marketplace.read_text(encoding="utf-8"))
    assert [item["name"] for item in installed["plugins"]] == [
        "unrelated",
        "ui-clone-skills",
    ]
    assert marketplace.stat().st_mode & 0o777 == 0o640

    marketplace.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
    marketplace.chmod(0o640)
    marketplace.parent.chmod(0o500)
    try:
        result = subprocess.run(
            ["bash", str(checkout / "install.sh"), "--no-deps", "--codex-only"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )
    finally:
        marketplace.parent.chmod(0o700)

    assert result.returncode != 0
    assert json.loads(marketplace.read_text(encoding="utf-8")) == original
    assert marketplace.stat().st_mode & 0o777 == 0o640


def test_marketplace_symlink_survives_install_and_uninstall(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copy2(INSTALL_SH, checkout / "install.sh")
    shutil.copy2(REPO_ROOT / "pyproject.toml", checkout / "pyproject.toml")
    shutil.copytree(REPO_ROOT / ".claude-plugin", checkout / ".claude-plugin")
    shutil.copytree(REPO_ROOT / ".codex", checkout / ".codex")
    shutil.copytree(REPO_ROOT / "skills", checkout / "skills")
    shutil.copytree(REPO_ROOT / "bin", checkout / "bin")
    # hooks/hooks.json is the file that decides whether the installed plugin
    # has any hooks at all; the installer refuses a source without it.
    shutil.copytree(REPO_ROOT / "hooks", checkout / "hooks")

    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "commands.log"
    _write_python_wrapper(fake_bin / "python3")
    _write_executable(
        fake_bin / "codex",
        '#!/usr/bin/env bash\nprintf "codex %s\\n" "$*" >> "$COMMAND_LOG"\n',
    )

    marketplace = home / ".agents" / "plugins" / "marketplace.json"
    marketplace.parent.mkdir(parents=True)
    target = tmp_path / "shared" / "marketplace.json"
    target.parent.mkdir()
    unrelated = {
        "name": "unrelated",
        "source": {"source": "git", "url": "https://example.test/plugin.git"},
    }
    target.write_text(
        json.dumps({"name": "local", "plugins": [unrelated]}, indent=2) + "\n",
        encoding="utf-8",
    )
    target.chmod(0o640)
    marketplace.symlink_to(target)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CODEX_HOME": str(home / ".codex"),
            "AGENTS_SKILLS_DIR": str(home / ".agents" / "skills"),
            "UI_CLONE_LOCAL_BIN_DIR": str(home / ".local" / "bin"),
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )

    subprocess.run(
        ["bash", str(checkout / "install.sh"), "--no-deps", "--codex-only"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert marketplace.is_symlink()
    assert marketplace.resolve() == target.resolve()
    installed = json.loads(target.read_text(encoding="utf-8"))
    assert [item["name"] for item in installed["plugins"]] == [
        "unrelated",
        "ui-clone-skills",
    ]
    assert target.stat().st_mode & 0o777 == 0o640

    subprocess.run(
        ["bash", str(checkout / "install.sh"), "--uninstall"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert marketplace.is_symlink()
    assert marketplace.resolve() == target.resolve()
    uninstalled = json.loads(target.read_text(encoding="utf-8"))
    assert uninstalled["plugins"] == [unrelated]
    assert target.stat().st_mode & 0o777 == 0o640


def test_marketplace_broken_symlink_install_fails_closed(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copy2(INSTALL_SH, checkout / "install.sh")
    shutil.copy2(REPO_ROOT / "pyproject.toml", checkout / "pyproject.toml")
    shutil.copytree(REPO_ROOT / ".claude-plugin", checkout / ".claude-plugin")
    shutil.copytree(REPO_ROOT / ".codex", checkout / ".codex")
    shutil.copytree(REPO_ROOT / "skills", checkout / "skills")
    shutil.copytree(REPO_ROOT / "bin", checkout / "bin")
    # hooks/hooks.json is the file that decides whether the installed plugin
    # has any hooks at all; the installer refuses a source without it.
    shutil.copytree(REPO_ROOT / "hooks", checkout / "hooks")

    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "commands.log"
    _write_python_wrapper(fake_bin / "python3")
    _write_executable(
        fake_bin / "codex",
        '#!/usr/bin/env bash\nprintf "codex %s\\n" "$*" >> "$COMMAND_LOG"\n',
    )
    marketplace = home / ".agents" / "plugins" / "marketplace.json"
    marketplace.parent.mkdir(parents=True)
    missing_target = tmp_path / "missing" / "marketplace.json"
    marketplace.symlink_to(missing_target)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CODEX_HOME": str(home / ".codex"),
            "AGENTS_SKILLS_DIR": str(home / ".agents" / "skills"),
            "UI_CLONE_LOCAL_BIN_DIR": str(home / ".local" / "bin"),
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )

    result = subprocess.run(
        ["bash", str(checkout / "install.sh"), "--no-deps", "--codex-only"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert marketplace.is_symlink()
    assert os.readlink(marketplace) == str(missing_target)
    assert not missing_target.exists()


def test_claude_install_refreshes_recognized_known_marketplace_source(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    old_source = home / ".local" / "share" / "ui-clone-skills"
    _write_claude_plugin_source(old_source)
    known_marketplaces = home / ".claude" / "plugins" / "known_marketplaces.json"
    known_marketplaces.parent.mkdir(parents=True)
    known_marketplaces.write_text(
        json.dumps(
            {
                "voidmatcha": {
                    "source": {"source": "directory", "path": str(old_source)},
                    "installLocation": str(old_source),
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "commands.log"
    _write_executable(
        fake_bin / "claude",
        '#!/usr/bin/env bash\nprintf "claude %s\\n" "$*" >> "$COMMAND_LOG"\n',
    )
    _write_python_wrapper(fake_bin / "python3")

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "AGENTS_SKILLS_DIR": str(home / ".agents" / "skills"),
            "UI_CLONE_LOCAL_BIN_DIR": str(home / ".local" / "bin"),
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )
    subprocess.run(
        ["bash", str(INSTALL_SH), "--no-deps", "--claude-only"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    commands = command_log.read_text(encoding="utf-8")
    remove = "claude plugin marketplace remove voidmatcha"
    add = (
        "claude plugin marketplace add "
        f"{home / '.local' / 'share' / 'ui-clone-skills-claude-src'}"
    )
    assert remove in commands
    assert add in commands
    assert commands.index(remove) < commands.index(add)


def test_uninstall_removes_recognized_legacy_projection_symlink(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    old_source = home / ".local" / "share" / "ui-clone-skills"
    _write_claude_plugin_source(old_source)
    plugin_dir = home / "plugins" / "ui-clone-skills"
    plugin_dir.parent.mkdir(parents=True)
    plugin_dir.symlink_to(old_source, target_is_directory=True)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "commands.log"
    _write_python_wrapper(fake_bin / "python3")

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CODEX_HOME": str(home / ".codex"),
            "AGENTS_SKILLS_DIR": str(home / ".agents" / "skills"),
            "UI_CLONE_LOCAL_BIN_DIR": str(home / ".local" / "bin"),
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
        }
    )
    subprocess.run(
        ["bash", str(INSTALL_SH), "--uninstall"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert not plugin_dir.is_symlink()
    assert (old_source / ".claude-plugin" / "plugin.json").is_file()


def test_claude_legacy_settings_fallback_and_conflict_preservation(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "claude",
        '#!/usr/bin/env bash\nprintf "claude %s\\n" "$*" >> "$COMMAND_LOG"\n',
    )
    _write_python_wrapper(fake_bin / "python3")

    legacy_home = tmp_path / "legacy-home"
    legacy_source = legacy_home / ".local" / "share" / "ui-clone-skills"
    _write_claude_plugin_source(legacy_source)
    legacy_settings = legacy_home / ".claude" / "settings.json"
    legacy_settings.parent.mkdir(parents=True)
    legacy_settings.write_text(
        json.dumps(
            {
                "extraKnownMarketplaces": {
                    "voidmatcha": {
                        "source": {
                            "source": "directory",
                            "path": str(legacy_source),
                        }
                    }
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    legacy_log = tmp_path / "legacy.log"
    legacy_env = os.environ.copy()
    legacy_env.update(
        {
            "HOME": str(legacy_home),
            "CODEX_HOME": str(legacy_home / ".codex"),
            "AGENTS_SKILLS_DIR": str(legacy_home / ".agents" / "skills"),
            "UI_CLONE_LOCAL_BIN_DIR": str(legacy_home / ".local" / "bin"),
            "COMMAND_LOG": str(legacy_log),
            "PATH": f"{fake_bin}:{legacy_env['PATH']}",
        }
    )
    subprocess.run(
        ["bash", str(INSTALL_SH), "--uninstall"],
        cwd=tmp_path,
        env=legacy_env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "claude plugin uninstall ui-clone-skills@voidmatcha" in legacy_log.read_text()
    assert "claude plugin marketplace remove voidmatcha" in legacy_log.read_text()

    conflict_home = tmp_path / "conflict-home"
    conflict_source = conflict_home / "marketplace"
    conflict_plugin = conflict_source / ".claude-plugin"
    conflict_plugin.mkdir(parents=True)
    (conflict_plugin / "plugin.json").write_text(
        json.dumps({"name": "another-plugin"}) + "\n",
        encoding="utf-8",
    )
    (conflict_plugin / "marketplace.json").write_text(
        json.dumps({"name": "voidmatcha", "plugins": [{"name": "another-plugin"}]})
        + "\n",
        encoding="utf-8",
    )
    known = conflict_home / ".claude" / "plugins" / "known_marketplaces.json"
    known.parent.mkdir(parents=True)
    known.write_text(
        json.dumps(
            {
                "voidmatcha": {
                    "source": {
                        "source": "directory",
                        "path": str(conflict_source),
                    }
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    conflict_log = tmp_path / "conflict.log"
    conflict_env = os.environ.copy()
    conflict_env.update(
        {
            "HOME": str(conflict_home),
            "AGENTS_SKILLS_DIR": str(conflict_home / ".agents" / "skills"),
            "UI_CLONE_LOCAL_BIN_DIR": str(conflict_home / ".local" / "bin"),
            "COMMAND_LOG": str(conflict_log),
            "PATH": f"{fake_bin}:{conflict_env['PATH']}",
        }
    )
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--no-deps", "--claude-only"],
        cwd=tmp_path,
        env=conflict_env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "not a validated ui-clone-skills source" in result.stderr
    assert not conflict_log.exists()


def test_uninstall_cleans_hooks_without_uv_and_is_fail_closed_on_codex_failure(
    tmp_path: Path,
) -> None:
    for name, with_hooks, codex_exit, expected_rc in (
        ("missing-uv", True, 0, 0),
        ("codex-failure", False, 1, 1),
    ):
        case_dir = tmp_path / name
        home = case_dir / "home"
        fake_bin = case_dir / "bin"
        fake_bin.mkdir(parents=True)
        command_log = case_dir / "commands.log"
        _write_python_wrapper(fake_bin / "python3")
        _write_executable(
            fake_bin / "codex",
            f"""#!/usr/bin/env bash
printf "codex %s\\n" "$*" >> "$COMMAND_LOG"
if [ "$1 $2" = "plugin remove" ]; then exit {codex_exit}; fi
exit 0
""",
        )

        marketplace = home / ".agents" / "plugins" / "marketplace.json"
        marketplace.parent.mkdir(parents=True)
        marketplace.write_text(
            json.dumps(
                {
                    "name": "local",
                    "plugins": [
                        {
                            "name": "ui-clone-skills",
                            "source": {
                                "source": "local",
                                "path": "./plugins/ui-clone-skills",
                            },
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        marker = home / ".config" / "ui-clone-skills" / "root"
        marker.parent.mkdir(parents=True)
        marker.write_text(f"{REPO_ROOT}\n", encoding="utf-8")
        codex_config = home / ".codex" / "config.toml"
        codex_config.parent.mkdir(parents=True, exist_ok=True)
        codex_config.write_text(
            '[plugins."ui-clone-skills@local"]\nenabled = true\n',
            encoding="utf-8",
        )
        if with_hooks:
            hooks = home / ".codex" / "hooks.json"
            hooks.parent.mkdir(parents=True, exist_ok=True)
            hooks.write_text(
                '{"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": '
                '[{"type": "command", "command": '
                '"python -m ui_clone.hooks.pretool"}]}]}}\n',
                encoding="utf-8",
            )

        env = os.environ.copy()
        env.update(
            {
                "HOME": str(home),
                "CODEX_HOME": str(home / ".codex"),
                "AGENTS_SKILLS_DIR": str(home / ".agents" / "skills"),
                "UI_CLONE_LOCAL_BIN_DIR": str(home / ".local" / "bin"),
                "COMMAND_LOG": str(command_log),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            }
        )
        result = subprocess.run(
            ["bash", str(INSTALL_SH), "--uninstall"],
            cwd=case_dir,
            env=env,
            capture_output=True,
            text=True,
        )

        assert result.returncode == expected_rc
        if expected_rc == 0:
            assert "Uninstall complete." in result.stdout
            cleaned = json.loads((home / ".codex" / "hooks.json").read_text(encoding="utf-8"))
            assert cleaned == {"hooks": {}}
        else:
            assert "Uninstall incomplete" in result.stdout
            assert "Uninstall complete." not in result.stdout
            assert marker.is_file()
            plugins = json.loads(marketplace.read_text(encoding="utf-8"))["plugins"]
            assert [item["name"] for item in plugins] == ["ui-clone-skills"]
            assert codex_config.read_text(encoding="utf-8") == (
                '[plugins."ui-clone-skills@local"]\nenabled = true\n'
            )


def test_uninstall_removes_exact_stale_claude_marketplace(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    missing_source = home / ".local" / "share" / "ui-clone-skills"
    known = home / ".claude" / "plugins" / "known_marketplaces.json"
    known.parent.mkdir(parents=True)
    known.write_text(
        json.dumps(
            {
                "unrelated": {
                    "source": {
                        "source": "directory",
                        "path": str(home / "unrelated"),
                    }
                },
                "voidmatcha": {
                    "source": {
                        "source": "directory",
                        "path": str(missing_source),
                    },
                    "installLocation": str(missing_source),
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "commands.log"
    _write_python_wrapper(fake_bin / "python3")
    _write_executable(
        fake_bin / "claude",
        '#!/usr/bin/env bash\nprintf "claude %s\\n" "$*" >> "$COMMAND_LOG"\n',
    )

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CODEX_HOME": str(home / ".codex"),
            "AGENTS_SKILLS_DIR": str(home / ".agents" / "skills"),
            "UI_CLONE_LOCAL_BIN_DIR": str(home / ".local" / "bin"),
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
        }
    )
    subprocess.run(
        ["bash", str(INSTALL_SH), "--uninstall"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    commands = command_log.read_text(encoding="utf-8")
    assert "claude plugin uninstall ui-clone-skills@voidmatcha" in commands
    assert "claude plugin marketplace remove voidmatcha" in commands
    assert "unrelated" in json.loads(known.read_text(encoding="utf-8"))


def test_claude_marketplace_source_is_self_contained_real_files(tmp_path: Path) -> None:
    """The directory registered as the Claude marketplace must contain real
    files, not symlinks into the checkout.

    `claude plugin install` copies the marketplace source into
    ~/.claude/plugins/cache/<owner>/<plugin>/<version> WITHOUT following
    symlinks. A symlinked source therefore caches as an empty shell: no
    hooks.json, no skills, so every session whose cwd is outside the checkout
    loads nothing. Measured on the live machine before this test existed: the
    0.7.24 cache directory held 0 files and one empty skills/ dir, while
    `claude plugin list` reported the plugin installed and enabled.

    Do NOT try to verify this by asking a `claude -p` session to name its
    available skills. That reports none even when the plugin loads correctly,
    so it cannot distinguish the two states — it was tried, and it agreed with
    itself both before and after the fix. The observables that do work are the
    cached file count and a hook side effect on disk (a `pre_bash` browse crumb
    from an out-of-repo session).
    """
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copy2(INSTALL_SH, checkout / "install.sh")
    shutil.copy2(REPO_ROOT / "pyproject.toml", checkout / "pyproject.toml")
    for item in (".claude-plugin", "skills", "bin", "hooks"):
        shutil.copytree(REPO_ROOT / item, checkout / item, symlinks=True)

    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "commands.log"
    _write_python_wrapper(fake_bin / "python3")
    _write_executable(
        fake_bin / "claude",
        '#!/usr/bin/env bash\nprintf "claude %s\\n" "$*" >> "$COMMAND_LOG"\n',
    )
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "AGENTS_SKILLS_DIR": str(home / ".agents" / "skills"),
            "UI_CLONE_LOCAL_BIN_DIR": str(home / ".local" / "bin"),
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )

    subprocess.run(
        ["bash", str(checkout / "install.sh"), "--no-deps", "--claude-only"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    logged = command_log.read_text(encoding="utf-8")
    match = re.search(r"^claude plugin marketplace add (\S+)$", logged, re.MULTILINE)
    assert match is not None, f"no marketplace add logged; got:\n{logged}"
    source = Path(match.group(1))
    assert source.is_dir(), f"registered marketplace source does not exist: {source}"

    links = [p for p in source.rglob("*") if p.is_symlink()]
    assert links == [], (
        "the Claude marketplace source must contain zero symlinks — the host "
        f"caches it without following them; found: {[str(p) for p in links[:5]]}"
    )

    hooks_json = source / "hooks" / "hooks.json"
    assert hooks_json.is_file() and not hooks_json.is_symlink(), (
        f"hooks.json must be a real file in the marketplace source: {hooks_json}"
    )
    assert json.loads(hooks_json.read_text(encoding="utf-8"))

    skills = sorted(p.name for p in (source / "skills").iterdir())
    assert skills == ["ui-capture", "ui-reverse-engineering", "visual-debug"], (
        f"marketplace source must ship exactly the public skills; got {skills}"
    )
    for name in skills:
        assert (source / "skills" / name / "SKILL.md").is_file(), (
            f"public skill {name} must carry a real SKILL.md in the source"
        )


def test_reinstall_refreshes_an_already_installed_claude_plugin(tmp_path: Path) -> None:
    """An install over an existing install must refresh the host's copy.

    The host caches the plugin per version under
    ~/.claude/plugins/cache/<owner>/<plugin>/<version>, so 'already in
    `plugin list`' does not mean 'up to date' — it means the cache exists. When
    the installer treats that as nothing-to-do, every content change after the
    first install ships to a stale cache. The refresh is `plugin update`, not
    uninstall+install: uninstall rewrites enabledPlugins in settings.json, and
    a failure between the two steps leaves no plugin at all where a stale one
    stood.
    """
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copy2(INSTALL_SH, checkout / "install.sh")
    shutil.copy2(REPO_ROOT / "pyproject.toml", checkout / "pyproject.toml")
    for item in (".claude-plugin", "skills", "bin", "hooks"):
        shutil.copytree(REPO_ROOT / item, checkout / item, symlinks=True)

    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "commands.log"
    _write_python_wrapper(fake_bin / "python3")
    # Reports the plugin as already installed, the way a second run sees it.
    _write_executable(
        fake_bin / "claude",
        '#!/usr/bin/env bash\n'
        'printf "claude %s\\n" "$*" >> "$COMMAND_LOG"\n'
        'if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then\n'
        '  printf "  ui-clone-skills@voidmatcha\\n"\n'
        'fi\n',
    )
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "AGENTS_SKILLS_DIR": str(home / ".agents" / "skills"),
            "UI_CLONE_LOCAL_BIN_DIR": str(home / ".local" / "bin"),
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )

    subprocess.run(
        ["bash", str(checkout / "install.sh"), "--no-deps", "--claude-only"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    logged = command_log.read_text(encoding="utf-8")
    assert "plugin update ui-clone-skills@voidmatcha" in logged, (
        f"an already-installed plugin must be refreshed via `plugin update`; got:\n{logged}"
    )
    assert "plugin uninstall" not in logged, (
        "refresh must not go through uninstall — a mid-failure leaves the user "
        f"with no plugin where a stale one stood; got:\n{logged}"
    )


def _write_cache_faking_claude(path: Path, *, populate: bool) -> None:
    """A `claude` stub that mimics the host's install step.

    The real CLI copies the marketplace source into
    ~/.claude/plugins/cache/<marketplace>/<plugin>/<version>. `populate=False`
    reproduces the observed failure: the version directory is created but the
    copy lands nothing, because the source was symlinks.
    """
    copy = (
        'cp -R "$src"/. "$dst"/\n'
        if populate
        else '# deliberately leaves the cache dir empty\n'
    )
    _write_executable(
        path,
        '#!/usr/bin/env bash\n'
        'printf "claude %s\\n" "$*" >> "$COMMAND_LOG"\n'
        'if [ "$1" = "plugin" ] && [ "$2" = "marketplace" ] && [ "$3" = "add" ]; then\n'
        '  printf "%s" "$4" > "$HOME/.fake-marketplace-src"\n'
        'fi\n'
        'if [ "$1" = "plugin" ] && [ "$2" = "install" ]; then\n'
        '  src="$(cat "$HOME/.fake-marketplace-src")"\n'
        '  ver="$(sed -n \'s/.*"version": "\\([^"]*\\)".*/\\1/p\' "$src/.claude-plugin/plugin.json" | head -1)"\n'
        '  dst="$HOME/.claude/plugins/cache/voidmatcha/ui-clone-skills/$ver"\n'
        '  mkdir -p "$dst"\n'
        f'  {copy}'
        'fi\n',
    )


def _claude_probe_env(tmp_path: Path, home: Path, fake_bin: Path, log: Path) -> dict:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "AGENTS_SKILLS_DIR": str(home / ".agents" / "skills"),
            "UI_CLONE_LOCAL_BIN_DIR": str(home / ".local" / "bin"),
            "COMMAND_LOG": str(log),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )
    return env


def _probe_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copy2(INSTALL_SH, checkout / "install.sh")
    shutil.copy2(REPO_ROOT / "pyproject.toml", checkout / "pyproject.toml")
    for item in (".claude-plugin", "skills", "bin", "hooks", "ui_clone"):
        shutil.copytree(REPO_ROOT / item, checkout / item, symlinks=True)
    return checkout


def test_install_fails_when_the_host_caches_the_plugin_without_its_hooks(
    tmp_path: Path,
) -> None:
    """The exact incident, as an install-time failure.

    Version 0.7.24 installed 'successfully' while its cache directory held zero
    files, so every session outside the checkout ran with no hooks and no
    skills for weeks. Nothing in the install output said so. An install that
    delivers an empty plugin must fail loudly.
    """
    checkout = _probe_checkout(tmp_path)
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    _write_python_wrapper(fake_bin / "python3")
    _write_cache_faking_claude(fake_bin / "claude", populate=False)
    _write_executable(fake_bin / "uv", '#!/usr/bin/env bash\nexit 0\n')

    result = subprocess.run(
        ["bash", str(checkout / "install.sh"), "--no-deps", "--claude-only"],
        cwd=tmp_path,
        env=_claude_probe_env(tmp_path, home, fake_bin, log),
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0, (
        "an install that cached an empty plugin must not report success\n"
        f"stdout:\n{result.stdout[-2000:]}"
    )
    combined = result.stdout + result.stderr
    assert "hooks" in combined and "cache" in combined.lower(), (
        f"the failure must name the empty cache; got:\n{combined[-2000:]}"
    )


def test_install_runs_an_installed_hook_from_the_host_cache(tmp_path: Path) -> None:
    """Counting cached files is not enough — hooks load by directory
    convention, with no manifest field to verify. The installer must actually
    run one the way the host does and require it to succeed."""
    checkout = _probe_checkout(tmp_path)
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    uv_log = tmp_path / "uv.log"
    _write_python_wrapper(fake_bin / "python3")
    _write_cache_faking_claude(fake_bin / "claude", populate=True)
    _write_executable(
        fake_bin / "uv",
        '#!/usr/bin/env bash\nprintf "uv %s\\n" "$*" >> "$UV_LOG"\nexit 0\n',
    )
    env = _claude_probe_env(tmp_path, home, fake_bin, log)
    env["UV_LOG"] = str(uv_log)

    result = subprocess.run(
        ["bash", str(checkout / "install.sh"), "--no-deps", "--claude-only"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout[-2000:]}\nstderr:\n{result.stderr[-2000:]}"

    assert uv_log.is_file(), "the installer never executed a hook from the cache"
    invocations = uv_log.read_text(encoding="utf-8")
    cache = home / ".claude" / "plugins" / "cache" / "voidmatcha" / "ui-clone-skills"
    assert "ui_clone.hooks.section_gate" in invocations, (
        f"probe must run a real hook module; got:\n{invocations}"
    )
    assert str(cache) in invocations, (
        f"probe must run the hook from the host cache, not the checkout; got:\n{invocations}"
    )


def test_install_refuses_a_claude_source_that_resolves_inside_the_checkout(
    tmp_path: Path,
) -> None:
    """Staging must never land inside the repo, even via a symlinked parent.

    Observed live: `~/.local/share/ui-clone-skills` was a symlink to the
    checkout, left by an earlier curl-pipe install whose INSTALL_DIR defaults
    there. The staging path resolved to <repo>/claude-src, so the installer
    copied 5860 files into the working tree — untracked and not gitignored —
    and aimed its `rm -rf` at a path inside the repo. Refusing only the repo
    ROOT is not enough; any descendant is equally wrong.
    """
    checkout = _probe_checkout(tmp_path)
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    _write_python_wrapper(fake_bin / "python3")
    _write_executable(
        fake_bin / "claude",
        '#!/usr/bin/env bash\nprintf "claude %s\\n" "$*" >> "$COMMAND_LOG"\n',
    )
    env = _claude_probe_env(tmp_path, home, fake_bin, log)
    # A symlinked parent is how this happens in the wild; point straight at a
    # descendant of the checkout to assert the property directly.
    env["UI_CLONE_CLAUDE_SRC_DIR"] = str(checkout / "claude-src")

    result = subprocess.run(
        ["bash", str(checkout / "install.sh"), "--no-deps", "--claude-only"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0, (
        "staging inside the checkout must fail the install\n"
        f"stdout:\n{result.stdout[-1500:]}"
    )
    assert not (checkout / "claude-src").exists(), (
        "the installer must not leave a staged copy inside the working tree"
    )


def test_install_prunes_superseded_cache_versions(tmp_path: Path) -> None:
    """A verified install reclaims cache directories nothing points at.

    The host caches the plugin per version under
    ~/.claude/plugins/cache/<marketplace>/<plugin>/<version>, and the hook
    probe materialises a ~220MB uv venv inside the live one. Nothing reclaims
    superseded versions, so a few releases reach multiple GB. Only versions
    absent from installed_plugins.json are removed — the live installPath is
    read from the host's own record rather than guessed.
    """
    checkout = _probe_checkout(tmp_path)
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    _write_python_wrapper(fake_bin / "python3")
    _write_cache_faking_claude(fake_bin / "claude", populate=True)
    _write_executable(fake_bin / "uv", '#!/usr/bin/env bash\nexit 0\n')

    cache = home / ".claude" / "plugins" / "cache" / "voidmatcha" / "ui-clone-skills"
    stale = cache / "0.7.1"
    stale.mkdir(parents=True)
    (stale / "filler.txt").write_text("superseded\n", encoding="utf-8")
    # An unrelated plugin's cache must never be touched.
    other = home / ".claude" / "plugins" / "cache" / "someone-else" / "other-plugin" / "1.0.0"
    other.mkdir(parents=True)
    (other / "keep.txt").write_text("not ours\n", encoding="utf-8")

    version = json.loads(
        (checkout / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )["version"]
    installed = home / ".claude" / "plugins" / "installed_plugins.json"
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_text(
        json.dumps(
            {
                "plugins": {
                    "ui-clone-skills@voidmatcha": [
                        {
                            "scope": "user",
                            "version": version,
                            "installPath": str(cache / version),
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(checkout / "install.sh"), "--no-deps", "--claude-only"],
        cwd=tmp_path,
        env=_claude_probe_env(tmp_path, home, fake_bin, log),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    assert not stale.exists(), "superseded cache version must be reclaimed"
    assert (cache / version).is_dir(), "the installed version must survive"
    assert other.is_dir(), "another plugin's cache must never be touched"


def test_install_never_reclaims_the_version_it_just_verified(tmp_path: Path) -> None:
    """The prune reads the live set from installed_plugins.json alone, so a host
    record that still names only the PREVIOUS version authorises deleting the
    directory the delivery probe just ran a hook out of. `claude plugin update`
    is warn-and-continue, so a half-applied update — new version copied into
    cache, record not yet rewritten — reaches the prune with exactly that
    shape, and the installer then reports both "delivery probe passed" and
    "reclaimed 1 superseded version" about the same directory. The manifest
    version is verified evidence and must survive regardless of the record."""
    checkout = _probe_checkout(tmp_path)
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    _write_python_wrapper(fake_bin / "python3")
    _write_cache_faking_claude(fake_bin / "claude", populate=True)
    _write_executable(fake_bin / "uv", '#!/usr/bin/env bash\nexit 0\n')

    cache = home / ".claude" / "plugins" / "cache" / "voidmatcha" / "ui-clone-skills"
    version = json.loads(
        (checkout / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )["version"]
    # The host record lags: it still names only the previous version.
    previous = cache / "0.7.1"
    previous.mkdir(parents=True)
    (previous / "filler.txt").write_text("previous\n", encoding="utf-8")
    installed = home / ".claude" / "plugins" / "installed_plugins.json"
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_text(
        json.dumps(
            {
                "plugins": {
                    "ui-clone-skills@voidmatcha": [
                        {
                            "scope": "user",
                            "version": "0.7.1",
                            "installPath": str(previous),
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(checkout / "install.sh"), "--no-deps", "--claude-only"],
        cwd=tmp_path,
        env=_claude_probe_env(tmp_path, home, fake_bin, log),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    assert (cache / version).is_dir(), (
        "the manifest version the probe just verified was reclaimed as superseded"
    )


def test_install_keeps_cache_versions_when_the_host_record_is_unreadable(
    tmp_path: Path,
) -> None:
    """Without the host's own record of what is installed, removing anything
    is a guess. Fail safe: keep every version."""
    checkout = _probe_checkout(tmp_path)
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    _write_python_wrapper(fake_bin / "python3")
    _write_cache_faking_claude(fake_bin / "claude", populate=True)
    _write_executable(fake_bin / "uv", '#!/usr/bin/env bash\nexit 0\n')

    cache = home / ".claude" / "plugins" / "cache" / "voidmatcha" / "ui-clone-skills"
    stale = cache / "0.7.1"
    stale.mkdir(parents=True)
    installed = home / ".claude" / "plugins" / "installed_plugins.json"
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_text("{ this is not json", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(checkout / "install.sh"), "--no-deps", "--claude-only"],
        cwd=tmp_path,
        env=_claude_probe_env(tmp_path, home, fake_bin, log),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert stale.exists(), "an unreadable host record must not authorise deletion"


def test_install_warns_when_the_plugin_is_installed_but_not_enabled(
    tmp_path: Path,
) -> None:
    """Installed and enabled are different states, and only one of them runs.

    Observed live: after a `plugin marketplace remove` + reinstall recovery,
    installed_plugins.json listed the plugin while settings.json enabledPlugins
    no longer did. Hooks silently stopped firing in every session, and the
    delivery probe still passed — it runs the hook straight from the cache, so
    it proves DELIVERY, never ACTIVATION. The installer must say so; it must
    not silently enable, because a deliberate user disable has to be respected.
    """
    checkout = _probe_checkout(tmp_path)
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    _write_python_wrapper(fake_bin / "python3")
    _write_cache_faking_claude(fake_bin / "claude", populate=True)
    _write_executable(fake_bin / "uv", '#!/usr/bin/env bash\nexit 0\n')

    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"enabledPlugins": {}}), encoding="utf-8")

    result = subprocess.run(
        ["bash", str(checkout / "install.sh"), "--no-deps", "--claude-only"],
        cwd=tmp_path,
        env=_claude_probe_env(tmp_path, home, fake_bin, log),
        capture_output=True,
        text=True,
    )

    combined = result.stdout + result.stderr
    assert "not enabled" in combined.lower(), combined[-1500:]
    assert "plugin enable" in combined, "must name the exact recovery command"
