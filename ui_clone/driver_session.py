"""Concurrent-safe writer for .driver-session.id (driver-session bypass marker).

The Stop hook (`ui_clone.hooks.section_gate._is_driver_session`) already reads
the marker as a newline-delimited set of session IDs. This module is the
missing writer that gives the marker append-if-missing semantics under a file
lock so two driver sessions starting roughly concurrently don't stomp each
other's IDs.

Why a Python module instead of a shell one-liner:
  Atomic rename alone has a TOCTOU race — two writers both read the same old
  set, both compute "add my id", both rename — only one rename survives, the
  other writer's ID is lost. `fcntl.flock(LOCK_EX)` on a sibling `.lock` file
  serializes the read-modify-write inside a single critical section. Portable
  across macOS and Linux (which the repo CI uses), no shell-portability
  concerns vs `flock(1)` (util-linux) which is not stock on macOS.

CLI usage (shell wrapper at scripts/register-driver-session.sh):
  python -m ui_clone.driver_session register <session-id>
  python -m ui_clone.driver_session register-from-env  # reads $CLAUDE_CODE_SESSION_ID
"""

from __future__ import annotations

import argparse
import fcntl
import os
import sys
from pathlib import Path

MARKER_FILENAME = ".driver-session.id"
_LOCK_FILENAME = ".driver-session.id.lock"


def _existing_ids(marker: Path) -> list[str]:
    """Read the marker file, return a list of non-blank IDs in file order.

    Order is preserved (not a set) so a human reading the file sees the
    history of registrations. Blank/whitespace lines are filtered — defensive
    against hand-edits via `echo >> .driver-session.id` that produced a
    trailing newline.
    """
    if not marker.is_file():
        return []
    try:
        raw = marker.read_text(encoding="utf-8")
    except OSError:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in seen:
            continue
        seen.add(stripped)
        out.append(stripped)
    return out


def register(session_id: str, project_root: Path) -> Path:
    """Add `session_id` to project_root/.driver-session.id under a file lock.

    Returns the marker path. No-op (no on-disk change beyond touching the
    lock file) when the ID is already present. Raises ValueError on an empty
    or whitespace-only session_id.
    """
    if not session_id or not session_id.strip():
        raise ValueError("driver_session.register: session_id must be non-empty")
    session_id = session_id.strip()

    project_root = Path(project_root)
    project_root.mkdir(parents=True, exist_ok=True)
    marker = project_root / MARKER_FILENAME
    lock_path = project_root / _LOCK_FILENAME

    # Open the lock file (create if needed). Keeping the fd open holds the
    # advisory exclusive lock for the duration of the with-block.
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        ids = _existing_ids(marker)
        if session_id not in ids:
            ids.append(session_id)
            tmp = marker.with_suffix(marker.suffix + ".tmp")
            tmp.write_text("\n".join(ids) + "\n", encoding="utf-8")
            os.replace(tmp, marker)
    finally:
        # Releasing via close — flock(LOCK_UN) is implicit on fd close.
        os.close(lock_fd)

    return marker


def register_from_env(project_root: Path) -> Path:
    """register() using $CLAUDE_CODE_SESSION_ID as the session id.

    Raises ValueError if the env var is unset or empty — silently writing an
    empty marker would mask a wiring bug (e.g. the operator running this
    outside a Claude Code session). Matches the Stop-hook reader's same env
    fallback in `section_gate._is_driver_session`.
    """
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    if not session_id:
        raise ValueError(
            "driver_session.register_from_env: $CLAUDE_CODE_SESSION_ID is unset; "
            "pass the session id explicitly or run inside Claude Code."
        )
    return register(session_id, project_root=project_root)


def _resolve_project_root(arg_root: str | None) -> Path:
    if arg_root:
        return Path(arg_root)
    # Walk upward looking for a `.git` or `pyproject.toml` — mirrors the same
    # convention as `ui_clone.hooks._common.find_project_root` without taking
    # a runtime dependency on it (avoids an import cycle if hooks ever start
    # using driver_session).
    cwd = Path.cwd()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").is_file():
            return candidate
    return cwd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ui_clone.driver_session",
        description=(
            "Register a driver session ID in .driver-session.id so the Stop "
            "hook bypasses on this session. Append-if-missing under a file "
            "lock — multiple concurrent driver sessions coexist without "
            "stomping each other's IDs."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_register = sub.add_parser(
        "register",
        help="register an explicit session id",
    )
    p_register.add_argument("session_id", help="the session id to register")
    p_register.add_argument(
        "--project-root",
        default=None,
        help="project root (default: walk up from cwd for .git / pyproject.toml)",
    )

    p_env = sub.add_parser(
        "register-from-env",
        help="register the id in $CLAUDE_CODE_SESSION_ID",
    )
    p_env.add_argument(
        "--project-root",
        default=None,
        help="project root (default: walk up from cwd for .git / pyproject.toml)",
    )

    args = parser.parse_args(argv)
    root = _resolve_project_root(getattr(args, "project_root", None))

    try:
        if args.command == "register":
            marker = register(args.session_id, project_root=root)
        else:  # register-from-env
            marker = register_from_env(project_root=root)
    except ValueError as exc:
        print(f"driver_session: {exc}", file=sys.stderr)
        return 2

    ids = _existing_ids(marker)
    print(f"driver_session: marker {marker} now lists {len(ids)} session(s): {', '.join(ids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
