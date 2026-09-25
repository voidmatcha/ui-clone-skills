"""Which Stop-block text has already been shown in full, per session and ref.

The Stop hook re-blocks every turn end while a gate fails. Re-emitting the full
failure list + goal card each time costs hundreds of tokens per turn for text
the agent already has. The hook emits the full block once per
(session, ref, failure signature) and a one-line reminder on repeats — the
block decision itself is unchanged.

Context compaction drops that earlier full text from the agent's context, so
the SessionStart/PostCompact hook (Codex: SessionStart source `compact`) calls
`forget_session` and the next Stop
shows the full block again. This ledger is separate from the retry-budget
ledger (`.ui-re-stop-attempts.json`) on purpose: forgetting what was shown must
never reset the retry cap.
"""

from __future__ import annotations

import json
from pathlib import Path

_SHOWN_NAME = ".ui-re-stop-shown.json"


def shown_path(project_root: Path) -> Path:
    return project_root / "tmp" / "ref" / _SHOWN_NAME


def _read(project_root: Path) -> dict[str, str]:
    try:
        data = json.loads(shown_path(project_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def _write(project_root: Path, data: dict[str, str]) -> None:
    path = shown_path(project_root)
    try:
        if not data:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        # Losing this ledger only means the full block is shown again.
        pass


def entry_key(session_key: str, ref_dir: Path | None) -> str:
    return f"{session_key}|{ref_dir if ref_dir is not None else '-'}"


def was_shown(project_root: Path, key: str, signature: str) -> bool:
    return bool(signature) and _read(project_root).get(key) == signature


def mark_shown(project_root: Path, key: str, signature: str) -> None:
    data = _read(project_root)
    if data.get(key) == signature:
        return
    data[key] = signature
    _write(project_root, data)


def forget_session(project_root: Path, session_id: str) -> None:
    """Drop shown-state for a session (all sessions when the id is unknown)."""
    if not shown_path(project_root).is_file():
        return
    data = _read(project_root)
    if session_id:
        prefixes = (f"{session_id}|", "_nosid|")
        kept = {k: v for k, v in data.items() if not k.startswith(prefixes)}
    else:
        kept = {}
    if kept != data:
        _write(project_root, kept)
