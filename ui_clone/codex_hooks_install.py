"""Manage project-scoped ui-clone hooks for Codex.

The globally enabled Codex plugin is skills-only. Enforcement routes are copied
from ``hooks/codex-hooks.json`` into the active project's ``.codex/hooks.json``
only when clone work opts in. Legacy ``merge`` / ``remove`` commands remain for
safe cleanup of installations made by older ui-clone-skills releases.

Pure ``merge_hooks`` / ``remove_ui_clone_hooks`` operate on already-parsed dicts
so the behaviour is unit-testable; the thin CLI handles read / backup / write.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# Every ui-clone hook command routes through `shim.sh ui_clone.hooks.<name>`, so
# this substring uniquely tags our entries inside a shared hooks file. OMX native
# wrappers never reference it.
_MARKER = "ui_clone.hooks"
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_CANONICAL_HOOKS = _PACKAGE_ROOT / "hooks" / "codex-hooks.json"

Hooks = dict[str, Any]


class UnsafeHookRemovalError(ValueError):
    """Removing a route would change a foreign hook's trust-state index."""


def _hook_is_ui_clone(hook: dict[str, Any]) -> bool:
    command = hook.get("command")
    return isinstance(command, str) and _MARKER in command


def _entry_is_ui_clone(entry: dict[str, Any]) -> bool:
    """True when a hook list-entry routes to a ui_clone.hooks.* module."""
    for hook in entry.get("hooks", []) or []:
        if _hook_is_ui_clone(hook):
            return True
    return False


def _assert_removal_preserves_foreign_positions(active: Hooks) -> None:
    """Reject removals that would shift a foreign entry or command index.

    Codex trust state keys include event, entry, and hook positions. Removing a
    ui-clone item before a foreign item would preserve the state bytes while
    changing what those bytes authorize. Trailing removals are position-safe.
    """
    for event, entries in (active.get("hooks") or {}).items():
        drop_entry: list[bool] = []
        for entry_index, entry in enumerate(entries):
            hooks = entry.get("hooks", []) or []
            owned = [_hook_is_ui_clone(hook) for hook in hooks]
            if any(owned):
                first_owned = owned.index(True)
                if any(not is_owned for is_owned in owned[first_owned + 1 :]):
                    raise UnsafeHookRemovalError(
                        f"{event} entry {entry_index} has a foreign hook after an "
                        "ui-clone hook; removing it would shift trust indexes"
                    )
            drop_entry.append(bool(owned) and all(owned))
        for entry_index, dropped in enumerate(drop_entry):
            if dropped and any(not later for later in drop_entry[entry_index + 1 :]):
                raise UnsafeHookRemovalError(
                    f"{event} entry {entry_index} precedes a foreign entry; removing "
                    "it would shift trust indexes"
                )


def remove_ui_clone_hooks(active: Hooks) -> Hooks:
    """Return a copy of ``active`` with every ui-clone hook entry stripped.

    Native (non-ui-clone) entries and the opaque ``state`` are preserved. An
    event whose entries become empty is dropped so a merge→remove round-trip
    returns the original file exactly. Idempotent; does not mutate ``active``.
    """
    _assert_removal_preserves_foreign_positions(active)
    result = copy.deepcopy(active)
    events = result.get("hooks")
    if not isinstance(events, dict):
        return result
    for event in list(events):
        kept: list[dict[str, Any]] = []
        for entry in events[event]:
            hooks = entry.get("hooks", []) or []
            filtered = [hook for hook in hooks if not _hook_is_ui_clone(hook)]
            if len(filtered) == len(hooks):
                kept.append(entry)
            elif filtered:
                entry["hooks"] = filtered
                kept.append(entry)
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


def count_ui_clone_routes(manifest: Hooks) -> int:
    """Count ui-clone command routes in a parsed hook manifest."""
    count = 0
    for entries in (manifest.get("hooks") or {}).values():
        for entry in entries:
            for hook in entry.get("hooks", []) or []:
                if _hook_is_ui_clone(hook):
                    count += 1
    return count


def _ui_clone_events(manifest: Hooks) -> dict[str, list[dict[str, Any]]]:
    """Return only ui-clone-owned entries, preserving event and entry order."""
    owned: dict[str, list[dict[str, Any]]] = {}
    for event, entries in (manifest.get("hooks") or {}).items():
        selected = [copy.deepcopy(entry) for entry in entries if _entry_is_ui_clone(entry)]
        if selected:
            owned[event] = selected
    return owned


def _manifest_shape_error(data: Hooks) -> str | None:
    events = data.get("hooks", {})
    if not isinstance(events, dict):
        return "non-object 'hooks'"
    for event, entries in events.items():
        if not isinstance(event, str):
            return "non-string hook event name"
        if not isinstance(entries, list):
            return f"non-list entries for event {event!r}"
        for entry_index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                return f"non-object entry {entry_index} for event {event!r}"
            hooks = entry.get("hooks")
            if not isinstance(hooks, list):
                return f"non-list hooks in entry {entry_index} for event {event!r}"
            for hook_index, hook in enumerate(hooks):
                if not isinstance(hook, dict):
                    return (
                        f"non-object hook {hook_index} in entry {entry_index} "
                        f"for event {event!r}"
                    )
                command = hook.get("command")
                if command is not None and not isinstance(command, str):
                    return (
                        f"non-string command in hook {hook_index}, entry {entry_index} "
                        f"for event {event!r}"
                    )
    return None


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


def _read_hooks(path: Path) -> tuple[Hooks | None, bool, int]:
    """Read and validate a hooks object, returning (data, exists, rc)."""
    exists = path.exists()
    if not exists:
        return None, False, 0
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"error: cannot read {path}: {exc} — left untouched", file=sys.stderr)
        return None, True, 2
    if not isinstance(data, dict):
        print(f"error: {path} is not a JSON object — left untouched", file=sys.stderr)
        return None, True, 2
    shape_error = _manifest_shape_error(data)
    if shape_error:
        print(f"error: {path} has {shape_error} — left untouched", file=sys.stderr)
        return None, True, 2
    return data, True, 0


def _load_canonical_hooks() -> Hooks | None:
    try:
        data: Any = json.loads(_CANONICAL_HOOKS.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"error: cannot read canonical hooks {_CANONICAL_HOOKS}: {exc}", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        print(f"error: canonical hooks {_CANONICAL_HOOKS} has an invalid shape", file=sys.stderr)
        return None
    shape_error = _manifest_shape_error(data)
    if shape_error:
        print(
            f"error: canonical hooks {_CANONICAL_HOOKS} has {shape_error}",
            file=sys.stderr,
        )
        return None
    return data


def _resolve_project_root(value: str | None) -> Path | None:
    if value:
        root = Path(value).expanduser().resolve()
    else:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=Path.cwd(),
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"error: cannot resolve current git project root: {exc}", file=sys.stderr)
            return None
        root = Path(result.stdout.strip()).resolve()
    if not root.is_dir():
        print(f"error: project root is not an existing directory: {root}", file=sys.stderr)
        return None
    return root


def _project_status(root: Path, *, as_json: bool) -> int:
    hooks_path = root / ".codex" / "hooks.json"
    active, _exists, rc = _read_hooks(hooks_path)
    if rc:
        return rc
    canonical = _load_canonical_hooks()
    if canonical is None:
        return 2
    current = active or {"hooks": {}}
    route_count = count_ui_clone_routes(current)
    canonical_count = count_ui_clone_routes(canonical)
    parity = _ui_clone_events(current) == _ui_clone_events(canonical)
    trust = "not-configured" if route_count == 0 else "review-required"
    payload = {
        "projectRoot": str(root),
        "hooksFile": str(hooks_path),
        "active": parity,
        "parity": parity,
        "routeCount": route_count,
        "canonicalRouteCount": canonical_count,
        "trust": trust,
        "nextStep": (
            "Run `ui-clone hooks enable`, then review `/hooks` and start a fresh "
            "Codex session."
            if not parity
            else "Review `/hooks` after manifest changes; a fresh Codex session may be required."
        ),
    }
    if as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        state = "active" if parity else "inactive"
        print(f"ui-clone project hooks: {state} ({route_count}/{canonical_count} routes)")
        print(payload["nextStep"])
    return 0


def _project_enable(root: Path) -> int:
    hooks_path = root / ".codex" / "hooks.json"
    active, exists, rc = _read_hooks(hooks_path)
    if rc:
        return rc
    canonical = _load_canonical_hooks()
    if canonical is None:
        return 2
    try:
        merged = merge_hooks(active or {"hooks": {}}, canonical)
    except UnsafeHookRemovalError as exc:
        print(f"error: cannot safely update {hooks_path}: {exc} — left untouched", file=sys.stderr)
        return 2
    try:
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"error: cannot create {hooks_path.parent}: {exc}", file=sys.stderr)
        return 2
    if exists and merged == active:
        print(f"ui-clone project hooks already configured: {hooks_path}")
    else:
        rc = _write_back(hooks_path, merged, backup=exists)
        if rc:
            return rc
        print(f"Configured ui-clone project hooks: {hooks_path}")
    print("Review `/hooks` if prompted, then start a fresh Codex session.")
    return 0


def _project_disable(root: Path) -> int:
    hooks_path = root / ".codex" / "hooks.json"
    active, exists, rc = _read_hooks(hooks_path)
    if rc or not exists:
        return rc
    assert active is not None
    try:
        cleaned = remove_ui_clone_hooks(active)
    except UnsafeHookRemovalError as exc:
        print(f"error: cannot safely update {hooks_path}: {exc} — left untouched", file=sys.stderr)
        return 2
    if cleaned == active:
        return 0
    rc = _write_back(hooks_path, cleaned, backup=True)
    if rc == 0:
        print(f"Removed ui-clone project hooks: {hooks_path}")
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ui_clone.codex_hooks_install",
        description="Manage project-scoped ui-clone gate hooks for Codex.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge", help="merge plugin hooks into the active hooks file")
    m.add_argument("--hooks-file", required=True, help="path to ~/.codex/hooks.json")
    m.add_argument("--plugin", required=True, help="path to the plugin's codex-hooks.json")
    r = sub.add_parser("remove", help="strip ui-clone hooks from the active hooks file")
    r.add_argument("--hooks-file", required=True)
    for command, help_text in (
        ("enable", "configure ui-clone hooks in a project"),
        ("disable", "remove ui-clone hooks from a project"),
        ("status", "report project hook configuration"),
    ):
        project_parser = sub.add_parser(command, help=help_text)
        project_parser.add_argument("--project-root", help="project root (default: current git root)")
        if command == "status":
            project_parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    if args.cmd in {"enable", "disable", "status"}:
        root = _resolve_project_root(args.project_root)
        if root is None:
            return 2
        if args.cmd == "enable":
            return _project_enable(root)
        if args.cmd == "disable":
            return _project_disable(root)
        return _project_status(root, as_json=args.json)

    hooks_path = Path(args.hooks_file)
    active, exists, rc = _read_hooks(hooks_path)
    if rc:
        return rc

    if args.cmd == "remove":
        if not exists:
            return 0  # nothing installed
        assert active is not None
        try:
            cleaned = remove_ui_clone_hooks(active)
        except UnsafeHookRemovalError as exc:
            print(
                f"error: cannot safely update {hooks_path}: {exc} — left untouched",
                file=sys.stderr,
            )
            return 2
        if cleaned == active:
            return 0
        return _write_back(hooks_path, cleaned, backup=True)

    # merge
    try:
        plugin = json.loads(Path(args.plugin).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"error: cannot read plugin {args.plugin}: {exc}", file=sys.stderr)
        return 2
    base = active if active is not None else {"state": {}, "hooks": {}}
    try:
        merged = merge_hooks(base, plugin)
    except UnsafeHookRemovalError as exc:
        print(f"error: cannot safely update {hooks_path}: {exc} — left untouched", file=sys.stderr)
        return 2
    return _write_back(hooks_path, merged, backup=exists)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
