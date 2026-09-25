"""Hook-owned ledger of the scoped evidence the canonical producers wrote.

The Bash guard only sees a command's text, so an agent-written script
(`bash forge.sh`, `node x.js`) can write `pixel-perfect-diff.json` or a
`capture-manifest.json` without ever naming the file on a command line. The
PostToolUse Bash hook (`ui_clone.hooks.post_verify`, Claude and Codex) closes
that gap from the other side: after every Bash command whose text is exactly
one canonical producer invocation, it records the sha256 of the evidence file
that producer owns into `<ref-dir>/.scoped-evidence-ledger.json`.
`python -m ui_clone.scoped_check` then requires the current hash of every
evidence file to appear in the ledger under its producer; a file written by
anything the hook did not see as a producer command has no entry and fails
(`evidence-unledgered`), and a missing ledger fails (`evidence-ledger-missing`).

What counts as a producer command (`parse_producer_command`):

    [bash] <path>/element-evidence.sh <session> <url> <selector> <out.json> [receipt]
    [bash] <path>/element-state-capture.sh clip|video ... <ref-dir> <ref|impl> ...
    node <plugin-root>/bin/ui-clone scoped-diff <ref-dir> [--impl-root ...] [--json]
    [uv run [--project <plugin-root>]] python[3] -m ui_clone.scoped_diff <ref-dir> ...

Two shell expansions are resolved the way the documented commands use them
in a clone project, before the text is split into words: `$PLUGIN_ROOT`,
`$CLAUDE_PLUGIN_ROOT`, `$CODEX_PLUGIN_ROOT` (and `${...}`) become the plugin
root this hook runs from (when the hook's own environment sets that
variable, it must resolve to that same root), and `$(pwd)` / `$PWD` / `${PWD}` become
the directory the command ran in. Any other `$...` or backtick expansion
outside single quotes leaves the command unrecorded. `uv run` accepts only
value-less flags plus `--project` / `--directory`; a `--project` that is not
the plugin root, or any other option (`--with`, `--python`, `--env-file`),
leaves the command unrecorded.

The command must be that invocation alone: no `;`, `&&`, `|`, newline, redirects,
subshells, or a second command (a forge chained after the producer would be
ledgered with it), no interpreter/library path overrides in a leading
`KEY=VAL`, a script whose sha256 equals the shipped producer manifest
(`ui_clone/scoped_producers.sha256.json`, so a same-named copy is not a
producer), and no `ui_clone/` package under the working directory or
project root that would shadow the installed module (`python -m` resolves the
current directory first). Anything else is simply not recorded.

The ledger is enforcement state: the Bash and Write/Edit guards deny naming
it, like the evidence files themselves. Residual threat model: a script the
hooks never see can still edit the ledger (it is a plain file next to the
evidence), a shadowed `python`/`bash` on PATH from an earlier command, a root
variable the command's shell inherited with a value the hook's environment
does not carry (the scripts are still sha256-checked at the hook's root), or a
host without these hooks (the ledger is then missing and scoped_check fails
closed, not open).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ui_clone import scoped_producers
from ui_clone.scoped_provenance import REPO_ROOT, file_sha256

LEDGER_NAME = ".scoped-evidence-ledger.json"
LEDGER_SCHEMA_VERSION = 1
MAX_ENTRIES = 500

PRODUCER_TARGET = "element-evidence"
PRODUCER_CAPTURE = "element-state-capture"
PRODUCER_DIFF = "scoped-diff"

TARGET_SCRIPT = "element-evidence.sh"
CAPTURE_SCRIPT = "element-state-capture.sh"
DIFF_MODULE = "ui_clone.scoped_diff"

# Evidence file (ref-dir-relative) -> the producer whose ledger entry must
# carry its current hash. Manifests cover their frames (sha256 per frame,
# re-hashed by scoped_check), so frames need no entry of their own.
EVIDENCE_FILES: dict[str, str] = {
    "element-target.json": PRODUCER_TARGET,
    "frames/ref/capture-manifest.json": PRODUCER_CAPTURE,
    "frames/impl/capture-manifest.json": PRODUCER_CAPTURE,
    "pixel-perfect-diff.json": PRODUCER_DIFF,
}

# A leading `KEY=VAL` that can redirect which interpreter, module, or script
# actually runs makes the command text meaningless as provenance.
_UNSAFE_ENV_RE = re.compile(
    r"^(?:PATH|PYTHON\w*|PYPY\w*|LD_\w+|DYLD_\w+|NODE_\w+|BASH_ENV|ENV|SHELLOPTS|IFS"
    r"|UI_CLONE_CAPTURE_DRIVER|PLUGIN_ROOT|CLAUDE_PLUGIN_ROOT|CODEX_PLUGIN_ROOT|UI_CLONE_ROOT"
    r"|UV_\w+|UI_CLONE_HOOK_VENV|VIRTUAL_ENV)="
)
# Variables the documented producer commands reference; each expands to the
# plugin root the hook runs from. No other variable is expanded.
_ROOT_VARS = ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "CODEX_PLUGIN_ROOT")
_VAR_RE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*)|(\(pwd\)))")
_UNQUOTED_UNSAFE_RE = re.compile(r"[\s*?\[\]'\"\\$`]")
# `uv run` flags that take no value and cannot change what runs.
_UV_FLAGS = frozenset(
    {"--no-dev", "--frozen", "--locked", "--offline", "--no-sync", "-q", "--quiet"}
)
_UV_DIR_OPTS = frozenset({"--project", "--directory"})
_CLI_PARTS = ("bin", "ui-clone")
_CLI_DIFF = "scoped-diff"
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PYTHON_RE = re.compile(r"^python[0-9.]*$")
_PUNCT = set("();<>|&")


@dataclass(frozen=True)
class ProducerRun:
    producer: str
    ref_dir: Path
    files: tuple[str, ...]  # ref-dir-relative evidence files this run owns


def ledger_path(ref_dir: Path) -> Path:
    return ref_dir / LEDGER_NAME


def _plugin_root() -> Path:
    """The plugin root this module (and so the hook) runs from."""
    return REPO_ROOT.resolve()


def _root_var(name: str) -> Path | None:
    """What an allowlisted root variable expands to: the plugin root, unless
    the hook's own environment sets it to a different directory (the command's
    shell would then expand it to a tree this hook cannot vouch for)."""
    root = _plugin_root()
    value = os.environ.get(name)
    if value:
        try:
            if Path(value).resolve() != root:
                return None
        except OSError:
            return None
    return root


def _expand(cmd: str, base: Path) -> str | None:
    """`cmd` with the allowlisted expansions substituted outside single
    quotes; None when any other `$` / backtick expansion remains."""
    out: list[str] = []
    i, n = 0, len(cmd)
    single = double = False
    while i < n:
        ch = cmd[i]
        if single:
            single = ch != "'"
            out.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            out.append(cmd[i : i + 2])
            i += 2
            continue
        if ch == "`":
            return None
        if ch == "$":
            m = _VAR_RE.match(cmd, i)
            if m is None:
                return None
            name = m.group(1) or m.group(2)
            root = _root_var(name) if name in _ROOT_VARS else None
            if m.group(3) or name == "PWD":
                value = str(base.resolve())
            elif root is not None:
                value = str(root)
            else:
                return None
            if double:
                value = re.sub(r'(["\\$`])', r"\\\1", value)
            elif _UNQUOTED_UNSAFE_RE.search(value):
                return None  # the shell would word-split or glob this value
            out.append(value)
            i = m.end()
            continue
        if ch == "'" and not double:
            single = True
        elif ch == '"':
            double = not double
        out.append(ch)
        i += 1
    return "".join(out)


def _words(cmd: str) -> list[str] | None:
    """Shell words with operators as their own tokens; None when unparsable."""
    lexer = shlex.shlex(cmd.replace("\\\n", " "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return None


def _resolve(token: str, base: Path, project_root: Path) -> Path | None:
    candidate = Path(os.path.expanduser(token))
    for root in (base,) if candidate.is_absolute() else (base, project_root):
        path = candidate if candidate.is_absolute() else root / candidate
        if path.is_file():
            return path.resolve()
    return None


def _script_is_shipped(path: Path, rel: str, manifest: dict[str, str] | None) -> bool:
    shipped = scoped_producers.shipped_sha256(rel, manifest)
    return shipped is not None and file_sha256(path) == shipped


def _module_shadowed(base: Path, project_root: Path) -> bool:
    """A `ui_clone/` directory in cwd/project root that is not this package."""
    installed = (REPO_ROOT / "ui_clone").resolve()
    for root in {base.resolve(), project_root.resolve()}:
        candidate = root / "ui_clone"
        if candidate.is_dir() and candidate.resolve() != installed:
            return True
    return False


def parse_producer_command(
    cmd: str,
    *,
    base: Path,
    project_root: Path,
    manifest: dict[str, str] | None = None,
) -> ProducerRun | None:
    """The producer run a Bash command IS (exactly one canonical invocation),
    else None. `base` is the directory the command ran in."""
    if not cmd or not cmd.strip():
        return None
    flat = cmd.replace("\\\n", " ")
    if "\n" in flat or "\r" in flat:
        return None  # a newline separates a second command
    expanded = _expand(flat, base)
    if expanded is None:
        return None
    words = _words(expanded)
    if not words or any(w and set(w) <= _PUNCT for w in words):
        return None
    while words and _ENV_ASSIGN_RE.match(words[0]):
        if _UNSAFE_ENV_RE.match(words[0]):
            return None
        words.pop(0)
    if words and words[0] == "env":
        words.pop(0)
        if words and words[0].startswith("-"):
            return None
    if len(words) >= 2 and words[0] == "uv" and words[1] == "run":
        words = words[2:]
        while words and words[0].startswith("-"):
            opt, _, value = words.pop(0).partition("=")
            if opt in _UV_FLAGS and not value:
                continue
            if opt not in _UV_DIR_OPTS:
                return None  # --with / --python / --env-file / ... change what runs
            if not value:
                if not words:
                    return None
                value = words.pop(0)
            target = _dir(value, base)
            if opt == "--directory":
                base = target
            elif target != _plugin_root():
                return None
        if not words or not _PYTHON_RE.match(words[0]):
            return None
    if not words:
        return None
    manifest = scoped_producers.load_manifest() if manifest is None else manifest
    if manifest is None:
        return None
    head = words[0].rsplit("/", 1)[-1]
    via_shell = head in {"bash", "sh"}
    if via_shell:
        words = words[1:]
        if not words or words[0].startswith("-"):
            return None
        head = words[0].rsplit("/", 1)[-1]
    if head == CAPTURE_SCRIPT:
        path = _resolve(words[0], base, project_root)
        if path is None or not _script_is_shipped(path, scoped_producers.DRIVER_SCRIPT, manifest):
            return None
        args = words[1:]
        if not args:
            return None
        mode, rest = args[0], args[1:]
        if mode == "clip" and len(rest) in (6, 7):
            ref_dir, side = rest[3], rest[4]
        elif mode == "video" and len(rest) in (6, 7):
            ref_dir, side = rest[2], rest[3]
        else:
            return None
        if side not in ("ref", "impl"):
            return None
        return ProducerRun(
            PRODUCER_CAPTURE, _dir(ref_dir, base), (f"frames/{side}/capture-manifest.json",)
        )
    if head == TARGET_SCRIPT:
        path = _resolve(words[0], base, project_root)
        if path is None or not _script_is_shipped(
            path, "scripts/extract/element-evidence.sh", manifest
        ):
            return None
        args = words[1:]
        if len(args) not in (4, 5):
            return None
        out = Path(os.path.expanduser(args[3]))
        out = out if out.is_absolute() else base / out
        return ProducerRun(PRODUCER_TARGET, out.parent.resolve(), (out.name,))
    if via_shell:
        return None
    if words[0] == "node":
        # `node <plugin-root>/bin/ui-clone scoped-diff <ref-dir> ...`: the
        # wrapper runs `python -m ui_clone.scoped_diff` from its own root.
        if len(words) < 4 or words[2] != _CLI_DIFF:
            return None
        cli = _resolve(words[1], base, project_root)
        if cli is None or cli != _plugin_root().joinpath(*_CLI_PARTS):
            return None
        if words[3].startswith("-") or _module_shadowed(base, project_root):
            return None
        return ProducerRun(PRODUCER_DIFF, _dir(words[3], base), ("pixel-perfect-diff.json",))
    if _PYTHON_RE.match(head):
        if len(words) < 4 or words[1] != "-m" or words[2] != DIFF_MODULE:
            return None
        if words[3].startswith("-") or _module_shadowed(base, project_root):
            return None
        return ProducerRun(PRODUCER_DIFF, _dir(words[3], base), ("pixel-perfect-diff.json",))
    return None


def _dir(token: str, base: Path) -> Path:
    path = Path(os.path.expanduser(token))
    return (path if path.is_absolute() else base / path).resolve()


def load(ref_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(ledger_path(ref_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(data, dict)
        or data.get("schemaVersion") != LEDGER_SCHEMA_VERSION
        or not isinstance(data.get("entries"), list)
    ):
        return None
    return data


def record(
    ref_dir: Path,
    producer: str,
    files: tuple[str, ...] | list[str],
    command: str,
    *,
    written_after: float | None = None,
) -> Path | None:
    """Append the current sha256 of `files` (ref-dir-relative) under `producer`.
    Files that do not exist, or (with `written_after`) were not modified at or
    after that time, are skipped; nothing is written when none remain."""
    hashes: dict[str, str] = {}
    for rel in files:
        path = ref_dir / rel
        if written_after is not None:
            try:
                if path.stat().st_mtime < written_after:
                    continue
            except OSError:
                continue
        digest = file_sha256(path)
        if digest is not None:
            hashes[rel] = digest
    if not hashes:
        return None
    data = load(ref_dir) or {"schemaVersion": LEDGER_SCHEMA_VERSION, "entries": []}
    entries = data["entries"]
    entries.append({"at": time.time(), "producer": producer, "command": command, "files": hashes})
    del entries[:-MAX_ENTRIES]
    path = ledger_path(ref_dir)
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return None
    return path


PENDING_NAME = ".scoped-ledger-pending.json"
_PENDING_MAX_AGE_S = 24 * 3600


def pending_path(project_root: Path) -> Path:
    return project_root / "tmp" / "ref" / PENDING_NAME


def _load_pending(project_root: Path) -> dict[str, float]:
    try:
        data = json.loads(pending_path(project_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        k: float(v)
        for k, v in data.items()
        if isinstance(k, str)
        and isinstance(v, (int, float))  # noqa: UP038 - 3.9-safe
        and not isinstance(v, bool)
    }


def _save_pending(project_root: Path, pending: dict[str, float]) -> None:
    path = pending_path(project_root)
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(pending, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def mark_started(cmd: str, *, base: Path, project_root: Path) -> bool:
    """PreToolUse entry point: stamp the start time of a canonical producer
    command, so the PostToolUse ledger only records files that run wrote.
    Without it a producer that failed early would ledger whatever evidence
    file already sat at its output path."""
    if parse_producer_command(cmd, base=base, project_root=project_root) is None:
        return False
    now = time.time()
    pending = {k: v for k, v in _load_pending(project_root).items() if now - v < _PENDING_MAX_AGE_S}
    pending[cmd.strip()] = now
    _save_pending(project_root, pending)
    return True


def record_command(cmd: str, *, base: Path, project_root: Path) -> Path | None:
    """Hook entry point: ledger the evidence a finished Bash command produced
    when the command is exactly one canonical producer invocation that the
    PreToolUse hook stamped, keeping only files written since that stamp."""
    run = parse_producer_command(cmd, base=base, project_root=project_root)
    if run is None or not run.ref_dir.is_dir():
        return None
    pending = _load_pending(project_root)
    started = pending.pop(cmd.strip(), None)
    if started is None:
        return None
    _save_pending(project_root, pending)
    # One second of slack: some filesystems store mtime at 1s granularity.
    return record(run.ref_dir, run.producer, run.files, cmd.strip(), written_after=started - 1.0)


def ledgered_hashes(data: dict[str, Any] | None, producer: str, rel: str) -> set[str]:
    found: set[str] = set()
    if data is None:
        return found
    for entry in data["entries"]:
        if not isinstance(entry, dict) or entry.get("producer") != producer:
            continue
        files = entry.get("files")
        if isinstance(files, dict) and isinstance(files.get(rel), str):
            found.add(files[rel])
    return found


def problems(ref_dir: Path) -> list[tuple[str, str]]:
    """`(code, reason)` failures: a missing ledger, or an existing evidence
    file whose current hash no producer command recorded."""
    present = {rel: prod for rel, prod in EVIDENCE_FILES.items() if (ref_dir / rel).is_file()}
    if not present:
        return []
    data = load(ref_dir)
    if data is None:
        return [
            (
                "evidence-ledger-missing",
                f"{LEDGER_NAME} missing or invalid under {ref_dir}: the PostToolUse Bash hook "
                "records each evidence file after its producer command runs, so evidence "
                "captured without the ui-clone-skills hooks (or by a script the hooks did "
                "not see) is not accepted; re-run element-evidence.sh, "
                "element-state-capture.sh, and `python -m ui_clone.scoped_diff` as their "
                "own Bash commands with the hooks installed",
            )
        ]
    out: list[tuple[str, str]] = []
    for rel, producer in present.items():
        digest = file_sha256(ref_dir / rel)
        if digest is None or digest in ledgered_hashes(data, producer, rel):
            continue
        out.append(
            (
                "evidence-unledgered",
                f"{rel} (sha256 {digest[:12]}) was not written by a `{producer}` command the "
                "hooks saw: a producer chained with other commands, run through a script, "
                "or a hand-written file is not evidence; re-run the producer as its own Bash "
                "command",
            )
        )
    return out
