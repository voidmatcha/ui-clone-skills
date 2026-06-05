"""Install/uninstall the ui-clone gate hooks into Codex's global hooks file.

codex-cli 0.137.0 REMOVED the `plugin_hooks` feature, so the plugin-declared
``hooks/codex-hooks.json`` gates (pre_generate / pre_bash / post_verify routing
via ``hooks/shim.sh`` → ``ui_clone.hooks.*``) no longer load from the plugin
manifest. The surviving stable path is the OMX-shared ``~/.codex/hooks.json``
(omx setup preserves user-owned hooks). This module merges the plugin's hook
entries into that file idempotently — and removes them again for uninstall —
without touching OMX's opaque trust ``state`` or its native hook wrappers.

Pure ``merge_hooks`` / ``remove_ui_clone_hooks`` operate on already-parsed dicts
so the behaviour is unit-testable; the thin CLI handles read / backup / write.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

# Every ui-clone hook command routes through `shim.sh ui_clone.hooks.<name>`, so
# this substring uniquely tags our entries inside a shared hooks file. OMX native
# wrappers never reference it.
_MARKER = "ui_clone.hooks"

Hooks = dict[str, Any]


def _entry_is_ui_clone(entry: dict[str, Any]) -> bool:
    """True when a hook list-entry routes to a ui_clone.hooks.* module."""
    for hook in entry.get("hooks", []) or []:
        if _MARKER in (hook.get("command") or ""):
            return True
    return False


def remove_ui_clone_hooks(active: Hooks) -> Hooks:
    """Return a copy of ``active`` with every ui-clone hook entry stripped.

    Native (non-ui-clone) entries and the opaque ``state`` are preserved. An
    event whose entries become empty is dropped so a merge→remove round-trip
    returns the original file exactly. Idempotent; does not mutate ``active``.
    """
    result = copy.deepcopy(active)
    events = result.get("hooks")
    if not isinstance(events, dict):
        return result
    for event in list(events):
        kept = [e for e in events[event] if not _entry_is_ui_clone(e)]
        if kept:
            events[event] = kept
        else:
            del events[event]
    return result


def merge_hooks(active: Hooks, plugin: Hooks) -> Hooks:
    """Merge the plugin's hook entries into ``active``'s ``hooks`` map.

    Idempotent: existing ui-clone entries are stripped first, then the plugin's
    current entries are appended per event (creating absent events). OMX's
    ``state`` and native entries are preserved. Inputs are not mutated.
    """
    result = remove_ui_clone_hooks(active)
    events: dict[str, Any] = result.setdefault("hooks", {})
    for event, entries in (plugin.get("hooks") or {}).items():
        events.setdefault(event, [])
        events[event].extend(copy.deepcopy(entries))
    return result


# ── on-disk CLI: fail-closed, atomic, backup-on-overwrite ────────────────────

def _atomic_write(path: Path, data: Any) -> None:
    tmp = Path(f"{path}.tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)  # atomic: the original is intact if this raises
    finally:
        tmp.unlink(missing_ok=True)  # never leak the temp file


def _backup(path: Path) -> None:
    shutil.copy2(path, Path(f"{path}.bak"))


def _write_back(path: Path, data: Any, *, backup: bool) -> int:
    """Back up (if overwriting) then atomically write. Fail closed (rc 2, original
    left intact, no temp leak) on any OS error — a half-written hooks file would
    break ALL of Codex's hook loading, not just ui-clone's."""
    try:
        if backup:
            _backup(path)
        _atomic_write(path, data)
    except OSError as exc:
        print(f"error: write failed for {path}: {exc} — original left intact", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ui_clone.codex_hooks_install",
        description="Install/uninstall ui-clone gate hooks into Codex's global hooks.json.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge", help="merge plugin hooks into the active hooks file")
    m.add_argument("--hooks-file", required=True, help="path to ~/.codex/hooks.json")
    m.add_argument("--plugin", required=True, help="path to the plugin's codex-hooks.json")
    r = sub.add_parser("remove", help="strip ui-clone hooks from the active hooks file")
    r.add_argument("--hooks-file", required=True)
    args = parser.parse_args(argv)

    hooks_path = Path(args.hooks_file)
    exists = hooks_path.exists()
    active: Any = None
    if exists:
        try:
            active = json.loads(hooks_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"error: cannot read {hooks_path}: {exc} — left untouched", file=sys.stderr)
            return 2
        if not isinstance(active, dict):
            # valid JSON but not a hooks object (list / null / scalar) — never
            # discard or wipe a user-owned file we do not recognise. Fail closed.
            print(f"error: {hooks_path} is not a JSON object — left untouched", file=sys.stderr)
            return 2
        if "hooks" in active and not isinstance(active["hooks"], dict):
            # corrupt shape (e.g. "hooks" is a list) — merging would raise; leave it.
            print(f"error: {hooks_path} has a non-object 'hooks' — left untouched", file=sys.stderr)
            return 2

    if args.cmd == "remove":
        if not exists:
            return 0  # nothing installed
        return _write_back(hooks_path, remove_ui_clone_hooks(active), backup=True)

    # merge
    try:
        plugin = json.loads(Path(args.plugin).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"error: cannot read plugin {args.plugin}: {exc}", file=sys.stderr)
        return 2
    base = active if exists else {"state": {}, "hooks": {}}
    return _write_back(hooks_path, merge_hooks(base, plugin), backup=exists)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
