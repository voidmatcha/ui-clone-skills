from __future__ import annotations

import copy
import json as _json
import subprocess
from pathlib import Path as _Path
from typing import Any

from ui_clone.codex_hooks_install import (
    main as _main,
)
from ui_clone.codex_hooks_install import (
    merge_hooks,
    remove_ui_clone_hooks,
)


def _active() -> dict[str, Any]:
    """A realistic OMX-managed ~/.codex/hooks.json: opaque trust `state` plus
    native (non-ui-clone) hook wrappers that MUST survive a merge untouched."""
    return {
        "state": {"/Users/x/.codex/hooks.json:pre_tool_use:0:0": {}},
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [
                    {"type": "command", "command": "/omx/native/pretool-wrapper"}]},
            ],
            "SessionStart": [
                {"matcher": "startup", "hooks": [
                    {"type": "command", "command": "/omx/native/sessionstart"}]},
            ],
        },
    }


def _plugin() -> dict[str, Any]:
    """The ui-clone hooks/codex-hooks.json shape (PascalCase events, shim route)."""
    return {
        "hooks": {
            "PreToolUse": [
                {"matcher": "apply_patch|Edit|Write", "hooks": [
                    {"type": "command",
                     "command": 'bash "$R/hooks/shim.sh" ui_clone.hooks.pre_generate'}]},
            ],
            "PostToolUse": [
                {"matcher": "Bash|exec_command", "hooks": [
                    {"type": "command",
                     "command": 'bash "$R/hooks/shim.sh" ui_clone.hooks.pre_bash'}]},
            ],
        },
    }


def _ui_clone_cmds(d: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for entries in (d.get("hooks") or {}).values():
        for entry in entries:
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                if "ui_clone.hooks" in cmd:
                    out.append(cmd)
    return out


def test_merge_appends_plugin_entries_per_event() -> None:
    merged = merge_hooks(_active(), _plugin())
    pre = merged["hooks"]["PreToolUse"]
    cmds = [h["command"] for e in pre for h in e["hooks"]]
    # native entry kept AND ui-clone pre_generate appended
    assert any("/omx/native/pretool-wrapper" in c for c in cmds)
    assert any("ui_clone.hooks.pre_generate" in c for c in cmds)
    # a plugin event absent from active is created
    assert "PostToolUse" in merged["hooks"]
    assert any("ui_clone.hooks.pre_bash" in h["command"]
               for e in merged["hooks"]["PostToolUse"] for h in e["hooks"])


def test_merge_preserves_state_and_native_entries() -> None:
    active = _active()
    merged = merge_hooks(active, _plugin())
    # OMX trust `state` is opaque — preserved verbatim
    assert merged["state"] == active["state"]
    # an active-only event (no plugin counterpart) is untouched
    assert merged["hooks"]["SessionStart"] == active["hooks"]["SessionStart"]


def test_merge_does_not_mutate_inputs() -> None:
    active, plugin = _active(), _plugin()
    a_before, p_before = copy.deepcopy(active), copy.deepcopy(plugin)
    merge_hooks(active, plugin)
    assert active == a_before, "merge_hooks must not mutate the active dict"
    assert plugin == p_before, "merge_hooks must not mutate the plugin dict"


def test_merge_is_idempotent() -> None:
    plugin = _plugin()
    once = merge_hooks(_active(), plugin)
    twice = merge_hooks(once, plugin)
    assert twice == once, "re-merging must not duplicate ui-clone entries"
    # exactly the two plugin commands, each present once
    assert sorted(_ui_clone_cmds(twice)) == sorted(_ui_clone_cmds(once))
    assert len(_ui_clone_cmds(twice)) == 2


def test_remove_round_trips_to_clean_active() -> None:
    active = _active()
    merged = merge_hooks(active, _plugin())
    assert _ui_clone_cmds(merged)  # sanity: ui-clone entries present
    restored = remove_ui_clone_hooks(merged)
    assert restored == active, "removing ui-clone entries must restore the original"


def test_remove_on_clean_active_is_noop() -> None:
    active = _active()
    assert remove_ui_clone_hooks(active) == active


def test_remove_preserves_foreign_hook_in_shared_entry() -> None:
    active = {
        "state": {"opaque": {"keep": True}},
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "python -m foreign.hook"},
                        {
                            "type": "command",
                            "command": "python -m ui_clone.hooks.pre_bash",
                        },
                    ],
                }
            ]
        },
    }

    cleaned = remove_ui_clone_hooks(active)

    assert cleaned == {
        "state": active["state"],
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "python -m foreign.hook"}
                    ],
                }
            ]
        },
    }


# ── CLI / on-disk behaviour (fail-closed, atomic) ────────────────────────────
def _write(p: _Path, obj: object) -> None:
    p.write_text(_json.dumps(obj), encoding="utf-8")


def _plugin_file(tmp: _Path) -> _Path:
    p = tmp / "codex-hooks.json"
    _write(p, _plugin())
    return p


def _canonical_route_count() -> int:
    manifest = _json.loads(
        (_Path(__file__).resolve().parents[1] / "hooks" / "codex-hooks.json").read_text(
            encoding="utf-8"
        )
    )
    return len(_ui_clone_cmds(manifest))


def test_project_status_reports_inactive_without_hook_file(
    tmp_path: _Path, capsys: Any
) -> None:
    project = tmp_path / "unrelated-project"
    project.mkdir()

    rc = _main(["status", "--project-root", str(project), "--json"])

    assert rc == 0
    payload = _json.loads(capsys.readouterr().out)
    assert payload["active"] is False
    assert payload["parity"] is False
    assert payload["routeCount"] == 0
    assert payload["canonicalRouteCount"] == 6
    assert payload["trust"] == "not-configured"


def test_project_enable_installs_exact_canonical_routes_and_is_idempotent(
    tmp_path: _Path,
) -> None:
    project = tmp_path / "clone-project"
    project.mkdir()

    assert _main(["enable", "--project-root", str(project)]) == 0
    assert _main(["enable", "--project-root", str(project)]) == 0

    hooks = project / ".codex" / "hooks.json"
    data = _json.loads(hooks.read_text(encoding="utf-8"))
    assert len(_ui_clone_cmds(data)) == _canonical_route_count() == 6


def test_project_enable_disable_preserves_foreign_hooks_and_metadata(
    tmp_path: _Path,
) -> None:
    project = tmp_path / "shared-project"
    hooks = project / ".codex" / "hooks.json"
    hooks.parent.mkdir(parents=True)
    original = _active()
    original["owner"] = {"name": "foreign"}
    _write(hooks, original)

    assert _main(["enable", "--project-root", str(project)]) == 0
    assert (project / ".codex" / "hooks.json.bak").is_file()
    assert _main(["disable", "--project-root", str(project)]) == 0

    assert _json.loads(hooks.read_text(encoding="utf-8")) == original


def test_project_enable_fails_closed_on_malformed_hooks(tmp_path: _Path) -> None:
    project = tmp_path / "broken-project"
    hooks = project / ".codex" / "hooks.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text("{broken", encoding="utf-8")

    assert _main(["enable", "--project-root", str(project)]) == 2
    assert hooks.read_text(encoding="utf-8") == "{broken"


def test_project_enable_rejects_missing_project_root(tmp_path: _Path) -> None:
    missing = tmp_path / "missing"

    assert _main(["enable", "--project-root", str(missing)]) == 2
    assert not missing.exists()


def test_project_status_defaults_to_git_root_from_nested_directory(
    tmp_path: _Path, monkeypatch: Any, capsys: Any
) -> None:
    project = tmp_path / "repo"
    nested = project / "packages" / "web"
    nested.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    monkeypatch.chdir(nested)

    assert _main(["status", "--json"]) == 0

    payload = _json.loads(capsys.readouterr().out)
    assert payload["projectRoot"] == str(project.resolve())
    assert payload["hooksFile"] == str(project.resolve() / ".codex" / "hooks.json")


def test_cli_merge_into_absent_file_creates_struct(tmp_path: _Path) -> None:
    hooks = tmp_path / "hooks.json"  # does NOT exist
    rc = _main(["merge", "--hooks-file", str(hooks), "--plugin", str(_plugin_file(tmp_path))])
    assert rc == 0
    data = _json.loads(hooks.read_text())
    assert data["state"] == {}
    assert any("ui_clone.hooks.pre_generate" in h["command"]
               for e in data["hooks"]["PreToolUse"] for h in e["hooks"])


def test_cli_merge_preserves_native_and_backs_up(tmp_path: _Path) -> None:
    hooks = tmp_path / "hooks.json"
    _write(hooks, _active())
    rc = _main(["merge", "--hooks-file", str(hooks), "--plugin", str(_plugin_file(tmp_path))])
    assert rc == 0
    data = _json.loads(hooks.read_text())
    assert data["state"] == _active()["state"]
    cmds = [h["command"] for e in data["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert any("/omx/native/pretool-wrapper" in c for c in cmds)
    assert any("ui_clone.hooks" in c for c in cmds)
    assert (tmp_path / "hooks.json.bak").is_file(), "existing file must be backed up"


def test_cli_merge_aborts_on_malformed_active_without_writing(tmp_path: _Path) -> None:
    hooks = tmp_path / "hooks.json"
    hooks.write_text("{not valid json", encoding="utf-8")
    rc = _main(["merge", "--hooks-file", str(hooks), "--plugin", str(_plugin_file(tmp_path))])
    assert rc != 0, "malformed active hooks must fail closed"
    assert hooks.read_text() == "{not valid json", "file must be left untouched"


def test_cli_merge_is_idempotent_on_disk(tmp_path: _Path) -> None:
    hooks = tmp_path / "hooks.json"
    _write(hooks, _active())
    pf = _plugin_file(tmp_path)
    _main(["merge", "--hooks-file", str(hooks), "--plugin", str(pf)])
    _main(["merge", "--hooks-file", str(hooks), "--plugin", str(pf)])
    assert len(_ui_clone_cmds(_json.loads(hooks.read_text()))) == 2


def test_cli_remove_strips_ui_clone(tmp_path: _Path) -> None:
    hooks = tmp_path / "hooks.json"
    _write(hooks, _active())
    pf = _plugin_file(tmp_path)
    _main(["merge", "--hooks-file", str(hooks), "--plugin", str(pf)])
    rc = _main(["remove", "--hooks-file", str(hooks)])
    assert rc == 0
    assert _json.loads(hooks.read_text()) == _active(), "remove must restore the original"


def test_cli_remove_on_absent_file_is_noop(tmp_path: _Path) -> None:
    hooks = tmp_path / "nope.json"
    assert _main(["remove", "--hooks-file", str(hooks)]) == 0
    assert not hooks.exists()


def test_cli_merge_fails_closed_on_non_dict_active(tmp_path: _Path) -> None:
    """A valid-JSON-but-non-dict hooks file (e.g. a list) must NOT be discarded —
    fail closed (rc!=0) and leave it untouched, like the malformed-JSON path."""
    hooks = tmp_path / "hooks.json"
    hooks.write_text("[1, 2, 3]", encoding="utf-8")
    rc = _main(["merge", "--hooks-file", str(hooks), "--plugin", str(_plugin_file(tmp_path))])
    assert rc != 0
    assert hooks.read_text() == "[1, 2, 3]", "non-dict active must be left untouched"


def test_cli_merge_fails_closed_on_json_null_active(tmp_path: _Path) -> None:
    """A file containing literal `null` is not a hooks object — fail closed, do
    not silently replace it (and there is no backup for null → data loss)."""
    hooks = tmp_path / "hooks.json"
    hooks.write_text("null", encoding="utf-8")
    rc = _main(["merge", "--hooks-file", str(hooks), "--plugin", str(_plugin_file(tmp_path))])
    assert rc != 0
    assert hooks.read_text() == "null", "json-null active must be left untouched"


def test_cli_remove_fails_closed_on_non_dict_active(tmp_path: _Path) -> None:
    hooks = tmp_path / "hooks.json"
    hooks.write_text('[{"command": "my-own-hook"}]', encoding="utf-8")
    rc = _main(["remove", "--hooks-file", str(hooks)])
    assert rc != 0
    assert hooks.read_text() == '[{"command": "my-own-hook"}]', "must not wipe a non-dict file"


def test_cli_remove_fails_closed_when_foreign_entry_would_shift(tmp_path: _Path) -> None:
    hooks = tmp_path / "hooks.json"
    original = {
        "state": {"hooks.json:pre_tool_use:1:0": {"trusted_hash": "foreign"}},
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python -m ui_clone.hooks.pre_bash",
                        }
                    ],
                },
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "python -m foreign.hook"}
                    ],
                },
            ]
        },
    }
    _write(hooks, original)

    assert _main(["remove", "--hooks-file", str(hooks)]) == 2
    assert _json.loads(hooks.read_text(encoding="utf-8")) == original


def test_cli_remove_fails_closed_when_foreign_hook_would_shift_within_entry(
    tmp_path: _Path,
) -> None:
    hooks = tmp_path / "hooks.json"
    original = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python -m ui_clone.hooks.pre_bash",
                        },
                        {"type": "command", "command": "python -m foreign.hook"},
                    ],
                }
            ]
        }
    }
    _write(hooks, original)

    assert _main(["remove", "--hooks-file", str(hooks)]) == 2
    assert _json.loads(hooks.read_text(encoding="utf-8")) == original


def test_cli_enable_fails_closed_on_non_list_event_entries(tmp_path: _Path) -> None:
    project = tmp_path / "broken-event"
    hooks = project / ".codex" / "hooks.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text('{"hooks":{"PreToolUse":{"bad":true}}}', encoding="utf-8")

    assert _main(["enable", "--project-root", str(project)]) == 2
    assert hooks.read_text(encoding="utf-8") == '{"hooks":{"PreToolUse":{"bad":true}}}'


def test_cli_status_fails_closed_on_non_dict_entry(tmp_path: _Path) -> None:
    project = tmp_path / "broken-entry"
    hooks = project / ".codex" / "hooks.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text('{"hooks":{"PreToolUse":["bad"]}}', encoding="utf-8")

    assert _main(["status", "--project-root", str(project), "--json"]) == 2
    assert hooks.read_text(encoding="utf-8") == '{"hooks":{"PreToolUse":["bad"]}}'


def test_cli_status_fails_closed_on_non_string_command(tmp_path: _Path) -> None:
    project = tmp_path / "broken-command"
    hooks = project / ".codex" / "hooks.json"
    hooks.parent.mkdir(parents=True)
    original = '{"hooks":{"PreToolUse":[{"hooks":[{"command":5}]}]}}'
    hooks.write_text(original, encoding="utf-8")

    assert _main(["status", "--project-root", str(project), "--json"]) == 2
    assert hooks.read_text(encoding="utf-8") == original


def test_cli_write_failure_fails_closed_and_cleans_tmp(tmp_path: _Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """If the atomic write fails (e.g. os.replace raises), return the documented
    fail-closed rc=2, leave the original intact, and leak no .tmp file."""
    import ui_clone.codex_hooks_install as mod
    hooks = tmp_path / "hooks.json"
    _write(hooks, _active())
    original = hooks.read_text()

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(mod.os, "replace", _boom)
    rc = _main(["merge", "--hooks-file", str(hooks), "--plugin", str(_plugin_file(tmp_path))])
    assert rc == 2, "write failure must fail closed with rc=2"
    assert hooks.read_text() == original, "original must be intact on write failure"
    assert not list(tmp_path.glob("hooks.json.tmp.*")), "no .tmp file may leak"


def test_cli_merge_fails_closed_on_non_dict_hooks_subkey(tmp_path: _Path) -> None:
    """A top-level dict whose `hooks` key is a list (not an object) is corrupt —
    fail closed, do not raise AttributeError or wipe it."""
    hooks = tmp_path / "hooks.json"
    hooks.write_text('{"state": {}, "hooks": [1, 2, 3]}', encoding="utf-8")
    rc = _main(["merge", "--hooks-file", str(hooks), "--plugin", str(_plugin_file(tmp_path))])
    assert rc != 0
    assert _json.loads(hooks.read_text()) == {"state": {}, "hooks": [1, 2, 3]}
