"""Deny running an agent-written script that touches scoped/enforcement evidence.

The Bash guard reads a command's text; a script file the agent wrote
(`bash forge.sh`, `node write-diff.js`, `./x.py`) hides the write behind a
path the text-level matchers never see. This rule opens the script BEFORE it
runs (PreToolUse) and denies it when its content names an enforcement-state
or scoped-evidence file (`bash_write._ENFORCEMENT_STATE_RE`), imports a
scoped evidence producer, or drives the recorder. Scripts shipped with this
plugin (any path under the plugin root or the `PLUGIN_ROOT` /
`CLAUDE_PLUGIN_ROOT` / `CODEX_PLUGIN_ROOT` roots), dependency trees
(`node_modules`, `.venv`, `site-packages`), and a checkout of this plugin as
the project are exempt. The second line is the evidence ledger
(`ui_clone.scoped_ledger`): a script the guard does not catch still produces
files no producer command was seen to write, which scoped_check rejects.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ui_clone.hooks._common import sanitize_command_for_deny
from ui_clone.hooks.pre_bash_rules.bash_write import (
    _ENFORCEMENT_MENTION_RE,
    _SCOPED_PRODUCER_IMPORT_RE,
    _SCOPED_RECORDER_RE,
    _shell_words,
)
from ui_clone.scoped_provenance import REPO_ROOT

_RUNNERS = frozenset(
    {
        "bash",
        "sh",
        "zsh",
        "dash",
        "ksh",
        "source",
        ".",
        "python",
        "python3",
        "pypy",
        "pypy3",
        "node",
        "nodejs",
        "deno",
        "bun",
        "tsx",
        "ts-node",
        "perl",
        "ruby",
        "php",
    }
)
_RUN_SUBCOMMANDS = frozenset({"run"})  # `uv run x.py`, `deno run x.ts`, `bun run x.js`
_PYTHON_RE = re.compile(r"^python[0-9.]*$")
_SCRIPT_EXTS = frozenset(
    {".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts", ".mts", ".pl", ".rb", ".php"}
)
_MAX_BYTES = 1_000_000
_EXEMPT_PARTS = frozenset({"node_modules", ".venv", "venv", "site-packages", ".git"})
_PLUGIN_ROOT_ENVS = ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "CODEX_PLUGIN_ROOT", "UI_CLONE_ROOT")
_RUNNER_MENTION_RE = re.compile(
    r"(?:^|[\s;&|(])(?:\.?/|bash\b|sh\b|zsh\b|python|node\b|deno\b|bun\b|perl\b|ruby\b|php\b|tsx\b|ts-node\b|uv\b|source\b)"
)


def _plugin_roots() -> list[Path]:
    roots = [REPO_ROOT.resolve()]
    for key in _PLUGIN_ROOT_ENVS:
        value = os.environ.get(key)
        if value:
            try:
                roots.append(Path(value).resolve())
            except OSError:
                continue
    return roots


def _is_exempt(path: Path, roots: list[Path]) -> bool:
    if any(part in _EXEMPT_PARTS for part in path.parts):
        return True
    return any(path == root or root in path.parents for root in roots)


def _script_candidates(words: list[str]) -> list[str]:
    """Tokens run as scripts: the word after a runner (flags skipped), and any
    `./x` / absolute path token at command position."""
    out: list[str] = []
    expect_script = False
    at_command_position = True
    for word in words:
        if word in {";", "&&", "||", "|", "&", "(", ")"} or word.endswith((";", "&&", "||", "|")):
            at_command_position = True
            expect_script = False
            continue
        base = word.rsplit("/", 1)[-1] if word not in {"."} else "."
        if expect_script:
            if word.startswith("-") or word in _RUN_SUBCOMMANDS:
                continue
            out.append(word)
            expect_script = False
            at_command_position = False
            continue
        if base in _RUNNERS or _PYTHON_RE.match(base) or base == "uv":
            expect_script = True
            at_command_position = False
            continue
        if at_command_position and (word.startswith("./") or word.startswith("/")):
            out.append(word)
        at_command_position = False
    return out


def _resolve(token: str, base: Path | None, project_root: Path) -> Path | None:
    candidate = Path(os.path.expanduser(token))
    roots = (
        [candidate.parent]
        if candidate.is_absolute()
        else [r for r in (base, project_root) if r is not None]
    )
    for root in roots:
        path = candidate if candidate.is_absolute() else root / candidate
        try:
            if path.is_file() and path.stat().st_size <= _MAX_BYTES:
                return path.resolve()
        except OSError:
            continue
    return None


def _offending_reference(text: str) -> str | None:
    for pattern in (_ENFORCEMENT_MENTION_RE, _SCOPED_PRODUCER_IMPORT_RE, _SCOPED_RECORDER_RE):
        m = pattern.search(text)
        if m:
            return m.group(0).strip()
    return None


def _agent_script_target(
    cmd: str, project_root: Path, cwd: Path | None = None
) -> tuple[str, str] | None:
    """`(script path, offending reference)` when a Bash command runs a script
    outside the plugin whose content names enforcement/scoped evidence or a
    producer module, else None."""
    if not cmd or not _RUNNER_MENTION_RE.search(sanitize_command_for_deny(cmd)):
        return None
    roots = _plugin_roots()
    try:
        if _is_exempt(project_root.resolve(), roots):
            return None
    except OSError:
        return None
    for token in _script_candidates(_shell_words(cmd)):
        path = _resolve(token, cwd, project_root)
        if path is None or _is_exempt(path, roots):
            continue
        if path.suffix.lower() not in _SCRIPT_EXTS and not os.access(path, os.X_OK):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hit = _offending_reference(text)
        if hit is not None:
            return token, hit
    return None
