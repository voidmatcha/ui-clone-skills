"""Deny running an agent-written script that touches scoped/enforcement evidence.

The Bash guard reads a command's text; a script file the agent wrote
(`bash forge.sh`, `node write-diff.js`, `./x.py`) hides the write behind a
path the text-level matchers never see. This rule opens the script BEFORE it
runs (PreToolUse) and denies it when its content names an enforcement-state
or scoped-evidence file (`bash_write._ENFORCEMENT_STATE_RE`), imports a
scoped evidence producer, or drives the recorder. Scripts shipped with this
plugin (any path under the plugin root or the `PLUGIN_ROOT` /
`CLAUDE_PLUGIN_ROOT` / `CODEX_PLUGIN_ROOT` roots, or a script inside another
plugin checkout whose whole shipped code tree is byte-identical to the
installed plugin, see `_is_shipped_copy`), dependency trees
(`node_modules`, `.venv`, `site-packages`), and a checkout of this plugin as
the project are exempt. The second line is the evidence ledger
(`ui_clone.scoped_ledger`): a script the guard does not catch still produces
files no producer command was seen to write, which scoped_check rejects.
"""

from __future__ import annotations

import filecmp
import hashlib
import json
import os
import re
import time
from pathlib import Path

from ui_clone.hooks._common import sanitize_command_for_deny
from ui_clone.hooks.pre_bash_rules.bash_write import (
    _CLONABILITY_DECIDE_RE,
    _CLONABILITY_MODULE_RE,
    _CLONABILITY_RECORD_RE,
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


# Top-level trees whose code a shipped script can load from its own checkout
# (sibling scripts, `PYTHONPATH=<checkout>` + `ui_clone`, skill libs, hooks,
# the CLI shim). A copy is trusted only when all of them match the install.
_SHIPPED_TREES = ("ui_clone", "scripts", "skills", "hooks", "bin")
# Runtime residue that is not shipped code and legitimately differs between
# checkouts. Anything else present in only one tree is a mismatch.
_RESIDUE_DIRS = frozenset(
    {
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".git",
    }
)
_RESIDUE_SUFFIXES = (".pyc", ".pyo")
_RESIDUE_NAMES = frozenset({".DS_Store"})
_CACHE_FILE = "shipped-checkouts.json"
_CACHE_MAX_ENTRIES = 32
# A cached verdict is trusted only when every fingerprinted file is older than
# the verdict by this margin: on a filesystem with 1-second mtime/ctime
# granularity a same-second rewrite after the compare keeps the fingerprint.
_CACHE_FRESH_MARGIN_NS = 2_000_000_000

Listing = list[tuple[str, int, int, int]]


def _is_residue_file(name: str) -> bool:
    return name in _RESIDUE_NAMES or name.endswith(_RESIDUE_SUFFIXES)


def _skill_names(root: Path) -> frozenset[str] | None:
    """Skill directories the install carries; None (exclude nothing) when it
    has no readable skills/ tree."""
    try:
        names = frozenset(p.name for p in (root / "skills").iterdir() if p.is_dir())
    except OSError:
        return None
    return names or None


def _is_unshipped_skill(rel: Path, shipped_skills: frozenset[str] | None) -> bool:
    """`skills/<name>/...` for a skill the install does not carry at all.

    The install projection holds only the public skills (install.sh
    CODEX_PUBLIC_SKILLS; internal ones such as skills/benchmark are listed in
    scripts/ci/review_checks.py INTERNAL_SKILLS and never installed), so the
    installed tree itself is the source of truth for what ships."""
    parts = rel.parts
    return (
        shipped_skills is not None
        and len(parts) > 2
        and parts[0] == "skills"
        and parts[1] not in shipped_skills
    )


def _tree_listing(root: Path, shipped_skills: frozenset[str] | None = None) -> Listing | None:
    """Sorted `(relative path, size, mtime_ns, ctime_ns)` of every non-residue
    file under the shipped trees of `root`, or None when a file cannot be
    stat'ed. Symlinked directories are not followed, so a tree that swaps a
    shipped directory for a link lacks those files and fails to match. With
    `shipped_skills`, whole `skills/<name>/` trees outside that set (internal
    skills the install omits) are left out."""
    out: Listing = []
    for top in _SHIPPED_TREES:
        base = root / top
        if not base.exists():
            continue
        if not base.is_dir():
            try:
                st = base.stat()
            except OSError:
                return None
            out.append((top, st.st_size, st.st_mtime_ns, st.st_ctime_ns))
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [
                d
                for d in dirnames
                if d not in _RESIDUE_DIRS
                and not _is_unshipped_skill(
                    Path(dirpath, d, "x").relative_to(root), shipped_skills
                )
            ]
            for name in filenames:
                if _is_residue_file(name):
                    continue
                file_path = Path(dirpath) / name
                try:
                    st = file_path.stat()
                except OSError:
                    return None
                rel = file_path.relative_to(root).as_posix()
                out.append((rel, st.st_size, st.st_mtime_ns, st.st_ctime_ns))
    out.sort()
    return out


def _fingerprint(candidate: Listing, installed: Listing) -> str:
    blob = json.dumps([candidate, installed], separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def _trees_identical(
    candidate_root: Path, candidate: Listing, installed_root: Path, installed: Listing
) -> bool:
    """Same file set and sizes, then byte-identical content."""
    if [(rel, size) for rel, size, _m, _c in candidate] != [
        (rel, size) for rel, size, _m, _c in installed
    ]:
        return False
    for rel, _size, _m, _c in candidate:
        try:
            if not filecmp.cmp(candidate_root / rel, installed_root / rel, shallow=False):
                return False
        except OSError:
            return False
    return True


def _cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "ui-clone-skills" / _CACHE_FILE


def _read_cache() -> dict:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(cache: dict) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if len(cache) > _CACHE_MAX_ENTRIES:
            for key in list(cache)[: len(cache) - _CACHE_MAX_ENTRIES]:
                cache.pop(key, None)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(cache, sort_keys=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _checkout_matches_install(candidate_root: Path, installed_root: Path) -> bool:
    """Whether every shipped tree of `candidate_root` is byte-identical to the
    installed plugin with no extra files (whole skill trees the install does
    not carry aside). The verdict is cached keyed by both roots and a stat
    fingerprint (paths, sizes, mtime_ns, ctime_ns) of both trees, so a warm
    call costs one stat walk; any write to either tree changes ctime and
    forces a full re-compare. A verdict is reused only when every file is at
    least `_CACHE_FRESH_MARGIN_NS` older than it (coarse-mtime filesystems)."""
    installed = _tree_listing(installed_root)
    if installed is None:
        return False
    candidate = _tree_listing(candidate_root, _skill_names(installed_root))
    if not candidate:
        return False
    key = f"{candidate_root}\0{installed_root}"
    fingerprint = _fingerprint(candidate, installed)
    newest = max(max(m, c) for _r, _s, m, c in candidate + installed)
    cache = _read_cache()
    entry = cache.get(key)
    if (
        isinstance(entry, dict)
        and entry.get("fingerprint") == fingerprint
        and isinstance(entry.get("checkedAtNs"), int)
        and newest + _CACHE_FRESH_MARGIN_NS <= entry["checkedAtNs"]
    ):
        return entry.get("identical") is True
    checked_at = time.time_ns()
    identical = _trees_identical(candidate_root, candidate, installed_root, installed)
    cache.pop(key, None)
    if newest + _CACHE_FRESH_MARGIN_NS <= checked_at:
        cache[key] = {"fingerprint": fingerprint, "identical": identical, "checkedAtNs": checked_at}
    _write_cache(cache)
    return identical


def _is_shipped_copy(path: Path) -> bool:
    """A shipped script run from another checkout of the plugin whose WHOLE
    shipped code matches the installed plugin. Skill snippets resolve scripts
    through the install marker (`~/.config/ui-clone-skills/root`, often a dev
    checkout) because the host does not export `CLAUDE_PLUGIN_ROOT` to Bash,
    while this hook runs from the host's installed copy. Shipped scripts load
    siblings and `ui_clone` from their own tree, so comparing only the entry
    script would trust an edited sibling or module: every file under
    `_SHIPPED_TREES` (runtime residue aside) must be byte-identical to the
    install, with no extra files except whole `skills/<name>/` trees the
    install does not carry (internal skills); a script inside one of those is
    never exempt. Repointing the marker at an edited tree therefore still
    denies."""
    installed = REPO_ROOT.resolve()
    for ancestor in path.parents:
        if not (ancestor / "ui_clone" / "__init__.py").is_file():
            continue
        if not (ancestor / "hooks" / "shim.sh").is_file():
            continue
        rel = path.relative_to(ancestor)
        if not rel.parts or rel.parts[0] not in _SHIPPED_TREES:
            return False
        if ancestor == installed:
            return True
        if _is_unshipped_skill(rel, _skill_names(installed)):
            return False
        return _checkout_matches_install(ancestor, installed)
    return False


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
    for pattern in (
        _ENFORCEMENT_MENTION_RE,
        _SCOPED_PRODUCER_IMPORT_RE,
        _SCOPED_RECORDER_RE,
        _CLONABILITY_DECIDE_RE,
    ):
        m = pattern.search(text)
        if m:
            return m.group(0).strip()
    # A direct import/call of the blocker-decision recorders forges a user decision.
    m = _CLONABILITY_RECORD_RE.search(text)
    if m and _CLONABILITY_MODULE_RE.search(text):
        return m.group(0)
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
        if path is None or _is_exempt(path, roots) or _is_shipped_copy(path):
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
