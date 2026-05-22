"""
PreToolUse Bash hook — blocks declaration-of-done commands when verification incomplete.

Why this hook exists
────────────────────
The Stop hook (section_gate) catches the case where Claude finishes a turn while
`current_gate != "done"`. But agents frequently declare done by *running a bash
command* — `git commit`, `git push`, `gh pr create`, `gh pr merge`. Those commands
fire *before* the next Stop event. The PostToolUse advisory (post_verify) prints
warnings but doesn't block — and per the v0.4.5 JSONL analysis, advisories alone
don't change behavior.

This hook fires on PreToolUse Bash. When:
  - a WIP marker `tmp/ref/<c>/.ui-re-active` exists, AND
  - the bash command matches a declaration-of-done pattern, AND
  - section-compare hasn't passed (or pipeline-state isn't "done")

…it denies the tool with a permission decision pointing the agent at the gate.

Bypass:
  - UI_RE_SKIP_BASH_GATE=1 in env disables the hook (escape hatch for emergencies)

Patterns blocked (anchored at start-of-command, after optional whitespace):
  - git commit ...
  - git push ...
  - gh pr create ...
  - gh pr merge ...
  - gh pr close ... (declaring abandonment is also a 'done' state we want to verify)

Not blocked: `git status`, `git diff`, `git log`, `gh pr view`, etc. — those are
read-only inspection.

Usage:
    python -m ui_clone.hooks.pre_bash

Input:  PreToolUse JSON on stdin with tool_input.command
Output: deny payload to stdout when blocking, exit 0 (silent) otherwise
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import cast

from ui_clone.hooks._common import (
    find_project_root,
    find_ref_dir,
    is_ad_hoc_ref_artifact,
    is_component_file,
    run_gate,
)
from ui_clone.state import GATE_ORDER, PipelineState

_BLOCK_PATTERNS = re.compile(
    r"^\s*(?:"
    r"git\s+commit\b"
    r"|git\s+push\b"
    r"|gh\s+pr\s+(?:create|merge|close)\b"
    r")"
)

# Benchmark-related commands that depend on tmp/ref/realfood pointing at the
# current HEAD's work dir. When the symlink is stale (points at a prior SHA's
# dir), these commands silently iterate on the wrong artifacts and produce
# misleading metrics. Block them until `bash skills/benchmark/scripts/setup.sh`
# has re-linked. Pattern matches anywhere in the command — agents commonly
# chain a `cd` or env-setup prefix before the actual call.
_BENCHMARK_COMMAND_PATTERNS = re.compile(
    r"benchmark/work/"
    r"|skills/visual-debug/scripts/(?:section-compare|asset-transfer-check|"
    r"asset-utilization-check|visual-judge|section-spec|image-fidelity-check|"
    r"font-parity-check|lottie-runtime-check)"
    r"|scripts/extract/extract-assets"
    r"|skills/benchmark/scripts/benchmark-harvest"
    r"|tmp/ref/realfood",
    re.IGNORECASE,
)
# Within benchmark commands, setup.sh itself MUST be allowed even when the
# symlink is stale — running it is the recovery action. Same for `rm`/`ln`
# style cleanup the agent might issue when shown the block reason.
_BENCHMARK_SETUP_ESCAPES = re.compile(
    r"skills/benchmark/scripts/setup\.sh"
    r"|ln\s+-s",
    re.IGNORECASE,
)

# Bash redirects/streams that write to a file. Each pattern captures the target
# path. Designed to catch the common ways an agent could bypass the PreToolUse
# Edit/Write hook (pre_generate.py): `cat > file`, `tee file`, `sed -i ... file`,
# and Codex-flagged v0.8 additions: `python3 -c "open(...).write(...)"`,
# `cp source target`, `mv source target`. Bash redirect was the original
# bypass; v0.6 → v0.7 closed `>`/`tee`/`sed`; v0.8 closes the file-API
# bypass after a natural-prompt nested agent invented `initial-survey.json`
# / `style-survey.json` via `python3 -c` to skirt the redirect deny.
_BASH_WRITE_PATTERNS = [
    # `cmd > file` or `cmd >> file` — any redirect to a path. Excludes process
    # substitutions (>(...)), fd duplications (>&N), and /dev/* sinks.
    re.compile(r">>?\s*(?![&(])\s*([^\s|;&<>()]+)"),
    # `tee file` / `tee -a file` — also blocks `tee --append`.
    re.compile(r"\btee\b\s+(?:-a\s+|--append\s+)?([^\s|;&<>()]+)"),
    # `sed -i ... file` — in-place edit. Match the file argument that follows
    # the sed expression. Conservative: requires the target to literally end
    # in a recognised source extension to avoid false positives on inline scripts.
    re.compile(
        r"\bsed\b[^|;&]*?\s-i(?:\.\S+)?\s[^|;&]*?\s([^\s|;&<>()]+\.(?:tsx|jsx|ts|js|css|scss|svelte|vue))\b"
    ),
    # `python -c "open('path','w').write(...)"` / `python3 -c "..."` /
    # `python -c "with open('path', 'w') as f: ..."`. Matches both quoted
    # styles. Captures only paths ending in .json (the only artifact class
    # we care about under tmp/ref/<c>/) to avoid false-positives on
    # legitimate Python that writes .txt logs etc.
    re.compile(r"open\s*\(\s*['\"]([^'\"]+\.json)['\"]\s*,\s*['\"]w(?:b|t)?['\"]"),
    # `cp source target.json` / `cp -r source target.json` — final positional
    # arg is the destination. Conservative: target must end in .json.
    re.compile(r"\bcp\b\s+(?:-[a-zA-Z]+\s+)*\S+\s+([^\s|;&<>()]+\.json)\b"),
    # `mv source target.json` — same shape.
    re.compile(r"\bmv\b\s+(?:-[a-zA-Z]+\s+)*\S+\s+([^\s|;&<>()]+\.json)\b"),
]


def _is_declaration_command(cmd: str) -> bool:
    if not cmd:
        return False
    return bool(_BLOCK_PATTERNS.search(cmd))


# ── Fresh-folder first-action enforcement ──
#
# v1.0 SKILL.md added "First action — Fresh-folder fast path" telling the
# agent to run `python -m ui_clone.pipeline ... run --phases 0A,1,2` before
# any other tool. The v1.0 nested test ignored that instruction completely
# (0 invocations) and started capturing screenshots / inspecting scripts
# directly. SKILL.md is guidance, not enforcement — so the hook has to do
# the enforcement.
#
# When the project's `tmp/ref/` is empty (or absent), this hook denies any
# Bash that touches the canonical extraction surface (agent-browser CLI,
# visual-debug/scripts/*.sh wrappers, scripts/extract/*.sh wrappers) except
# the pipeline driver itself and read-only inspection. The deny reason
# names the exact command the agent should run instead.
#
# Allowlisted in fresh state:
#   - python -m ui_clone.pipeline ...    (status / run actions)
#   - which / command -v / type / ls / cat / head / tail / grep / find / pwd
#   - mkdir -p (so the agent can prepare scratch dirs without hitting deny)
#   - git status / git diff / git log
#   - the preflight Bash literally documented in SKILL.md's First action
#     section (uses `command -v` + `for c in agent-browser ...; do` shape)
#   - the pipeline run's own internal invocations (we identify them by
#     PLUGIN_ROOT/scripts/extract/capture.sh and the visual-debug script
#     paths — they only appear when execute_phases() called them)
#
# Denylisted in fresh state also includes static mirror escape routes observed
# in natural-prompt validation: wget/curl copies of the live site into
# impl/public and local static servers started before any pipeline evidence
# exists. Those produce a browsable page but no React/Tailwind implementation
# and no gateable tmp/ref artifacts.

_FRESH_FOLDER_ALLOW_PATTERNS = re.compile(
    r"^\s*("
    r"python(?:3)?\s+-m\s+ui_clone\."
    r"|which\b|command\s+-v\b|type\s+-[ap]\b|type\s+[A-Za-z_]"
    r"|ls\b|cat\b|head\b|tail\b|grep\b|find\b|pwd\b|stat\b"
    r"|mkdir\s+-p\b|mkdir\b"
    r"|git\s+(?:status|diff|log|rev-parse|show)\b"
    r"|for\s+c\s+in\s+agent-browser"  # SKILL.md preflight shape
    r"|miss=\"\""                       # SKILL.md preflight starts here too
    r"|echo\b|printf\b|true\b"
    r"|test\b|\[\s+"
    r")"
)

_FRESH_FOLDER_DENY_PATTERNS = re.compile(
    r"\bagent-browser\b"
    r"|\bwget\b"
    r"|\bcurl\b[^\n\r]*https?://"
    r"|\bnode\s+(?:\S+/)?server\.js\b"
    r"|python(?:3)?\s+-m\s+http\.server\b"
    r"|npx\s+(?:serve|vite|http-server)\b"
    r"|npm\s+run\s+dev\b"
    r"|/skills/visual-debug/scripts/(?:extract-dom|dom-scaffold|section-compare|"
    r"asset-transfer-check|asset-utilization-check|paid-features-detect|"
    r"bundle-impl-coverage-check|hover-state-compare|click-state-compare|"
    r"video-transition-compare|hydration-check|reveal-trigger-check|"
    r"transition-compare|font-parity-check|image-fidelity-check|"
    r"scroll-end-completion-check|lottie-runtime-check)\.sh\b"
    r"|/scripts/extract/(?:extract-assets|extract-section-html|"
    r"extract-animation-runtime|extract-dynamic-styles|section-clips|"
    r"download-chunks|gsap-to-css)\.sh\b"
)

#
# Match shapes — keep the regex tight so unrelated `npm` invocations
# (install, run build, lint) still pass:
#   npm | pnpm | yarn | bun  +  create  +  <tool>
#   npx       +  create-<tool>
#   npx       +  degit
#   git       +  clone               (template repo)
_IMPL_SCAFFOLD_PATTERNS = re.compile(
    r"\b(?:npm|pnpm|yarn|bun)\s+create\b"
    r"|\bnpx\s+create-\S+"
    r"|\bnpx\s+degit\b"
    r"|\bnpm\s+init\b(?!\s+-y\s+--scope)"  # npm init <tpl>; skip `-y --scope` pure metadata initializers
    r"|\b(?:npm|pnpm|yarn|bun)\s+exec\s+create-\S+"
    r"|\bgit\s+clone\b[^\n\r]*\s(?:scratch/loop-\S*|[^\s|;&]*/impl)(?:\b|\s|$)",
    re.IGNORECASE,
)


_STATIC_MIRROR_DOWNLOAD_PATTERNS = re.compile(
    r"\bwget\b"
    r"(?=[^\n\r]*https?://)"
    r"(?=[^\n\r]*(?:\s-P\s+|--directory-prefix(?:=|\s+))[^\s|;&]*impl/public)"
    r"(?=[^\n\r]*(?:\s-p\b|--page-requisites|\s-r\b|--recursive|"
    r"--mirror|\s-E\b|--adjust-extension|\s-k\b|--convert-links))"
    r"|\bcurl\b"
    r"(?=[^\n\r]*https?://)"
    r"(?=[^\n\r]*(?:\s-o\s+|--output(?:=|\s+))[^\s|;&]*impl/public/index\.html)"
    r"|\bcurl\b[^\n\r]*https?://[^\n\r]*>\s*[^\s|;&]*impl/public/index\.html",
    re.IGNORECASE,
)

_STATIC_SERVER_PATTERNS = re.compile(
    r"\bnode\s+(?:\S+/)?server\.js\b"
    r"|python(?:3)?\s+-m\s+http\.server\b"
    r"|npx\s+(?:serve|vite|http-server)\b"
    r"|npm\s+run\s+dev\b",
    re.IGNORECASE,
)

_WHOLE_DOCUMENT_HTML_PATTERNS = re.compile(
    r"document\.(?:documentElement|body)\.(?:outerHTML|innerHTML)(?!\s*\.length)",
    re.IGNORECASE,
)

_STATIC_HTML_MIRROR_SOURCE_PATTERNS = re.compile(
    r"document\.(?:documentElement|body)\.(?:outerHTML|innerHTML)"
    r"|live-unwrapped\.html|live\.html|original\.html|snapshot\.html"
    r"|<!doctype\s+html|<html[\s>]|</html>|</body>",
    re.IGNORECASE,
)

_SECTION_COMPARE_COMMAND_PATTERNS = re.compile(
    r"skills/visual-debug/scripts/section-compare\.sh\b"
    r"|python(?:3)?\s+-m\s+ui_clone\.measure\s+section-compare\b",
    re.IGNORECASE,
)

_HTML_WRITE_PATTERNS = [
    re.compile(r">>?\s*(?![&(])\s*([^\s|;&<>()]+\.html)\b", re.IGNORECASE),
    re.compile(r"\btee\b\s+(?:-a\s+|--append\s+)?([^\s|;&<>()]+\.html)\b", re.IGNORECASE),
    re.compile(r"writeFileSync\s*\(\s*['\"]([^'\"]+\.html)['\"]", re.IGNORECASE),
    re.compile(r"open\s*\(\s*['\"]([^'\"]+\.html)['\"]\s*,\s*['\"]w(?:b|t)?['\"]", re.IGNORECASE),
    re.compile(r"\bcp\b\s+(?:-[a-zA-Z]+\s+)*\S+\s+([^\s|;&<>()]+\.html)\b", re.IGNORECASE),
    re.compile(r"\bmv\b\s+(?:-[a-zA-Z]+\s+)*\S+\s+([^\s|;&<>()]+\.html)\b", re.IGNORECASE),
]


def _canonical_repo_root() -> Path | None:
    """Repo root resolved without the closest-ancestor walk that
    find_project_root() uses. Priority: CLAUDE_PROJECT_DIR env var,
    then `git rev-parse --show-toplevel`. Returns None when neither
    resolves to a directory.

    Loop-codex-7 closure (2026-05-22): a nested ref dir at
    <repo>/scratch/loop-N/tmp/ref/<c>/ can shadow the canonical
    <repo>/tmp/ref/<c>/ from inside the closest-ancestor walk. The
    impl-scaffold gate needs an authority-of-record path so it can
    reject scratch-nested candidates as spoof attempts.
    """
    env_root = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if env_root:
        try:
            p = Path(env_root).resolve()
            if p.is_dir():
                return p
        except (OSError, RuntimeError):
            pass
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).resolve()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _is_scratch_nested(root: Path, canonical_repo_root: Path | None) -> bool:
    """True when `root` lives under `<canonical_repo_root>/scratch/`.

    Loop-codex-7 spoof signature: codex created
    scratch/loop-codex-7/tmp/ref/realfood/ (copied from a prior loop's
    completed ref dir) so the closest-ancestor walk found that nested
    tmp/ref/ before the canonical one. The pipeline-state.json in the
    spoof reported `current_gate: done` so the impl-scaffold gate let
    the scaffold through.

    Distinguishing legitimate vs spoofed: when running validation loops,
    the canonical ref dir is always at `<repo-root>/tmp/ref/<loop-N>/`,
    NEVER at `<repo-root>/scratch/loop-N/tmp/ref/`. The scratch/ subtree
    is impl + per-loop scratch state only; pipeline state lives at the
    repo root. So any candidate root descending from `<repo-root>/scratch/`
    is by construction a spoof and must be rejected.

    Returns False (allow) when canonical_repo_root cannot be resolved —
    safer to fall back to the existing behavior than to false-block on
    machines without a git repo + CLAUDE_PROJECT_DIR.
    """
    if canonical_repo_root is None:
        return False
    try:
        scratch_anchor = (canonical_repo_root / "scratch").resolve()
        return root.resolve().is_relative_to(scratch_anchor)
    except (OSError, ValueError, RuntimeError):
        return False


def _impl_scaffold_violation(
    cmd: str, project_root: Path, cwd: Path | None = None
) -> str | None:
    """Block impl/ bootstrap commands (npm create vite, npx create-*, etc.)
    when the pipeline hasn't reached pre-generate yet.

    The symmetric pair of pre_generate's Write/Edit gate. Returns a block
    reason string when the command would scaffold an impl/ without the
    matching ref dir's pipeline-state showing current_gate >= pre-generate,
    else None.

    Universal resolution: walks `_candidate_ref_roots` (the same set the
    static-server guard uses) and picks the freshest ref dir. If no ref dir
    exists at all, the agent is bootstrapping impl before Phase 1 ran —
    that's the strictest form of the bypass and blocks unconditionally.

    Loop-codex-7 closure: reject any candidate ref root under
    `<canonical_repo_root>/scratch/` — that subtree only holds per-loop
    impl + scratch artifacts, never canonical pipeline state. An agent
    that creates a nested tmp/ref/ there (e.g. by copying a prior loop's
    completed ref dir) is spoofing the gate.
    """
    if not cmd:
        return None
    if not _IMPL_SCAFFOLD_PATTERNS.search(cmd):
        return None

    # Pick the freshest ref dir from any candidate root the command might
    # be targeting. This mirrors _ref_dir_for_static_guard but is broader:
    # the scaffold command may not name the loop dir explicitly (e.g.
    # `npm create vite my-clone` in a cwd of scratch/loop-N/), so we accept
    # any populated ref dir under tmp/ref/.
    canonical_root = _canonical_repo_root()
    roots = [
        r for r in _candidate_ref_roots(project_root, cwd, cmd)
        if not _is_scratch_nested(r, canonical_root)
    ]
    freshest: Path | None = None
    freshest_mtime = -1.0
    for root in roots:
        if not root.is_dir():
            continue
        for d in root.iterdir():
            if not d.is_dir():
                continue
            try:
                mt = d.stat().st_mtime
            except OSError:
                continue
            if mt > freshest_mtime:
                freshest_mtime = mt
                freshest = d

    pre_generate_idx = GATE_ORDER.index("pre-generate")
    if freshest is None:
        gate_label = "missing (no tmp/ref/<c> exists)"
    else:
        state = PipelineState.load(freshest)
        gate = state.current_gate
        if gate in GATE_ORDER and GATE_ORDER.index(gate) >= pre_generate_idx:
            return None  # pre-generate or later reached — allowed
        if gate == "done":
            return None  # already converged — re-scaffolding is the agent's call
        gate_label = gate or "missing"

    return (
        "⛔ UI-RE impl-scaffold gate: BLOCKED before pre-generate.\n\n"
        f"Detected bootstrap command: `{cmd[:120]}{'…' if len(cmd) > 120 else ''}`\n"
        f"Current gate state: {gate_label}\n\n"
        "pre_generate's Write/Edit hook does not see shell-spawned scaffolders "
        "(npm create vite / npx create-* / pnpm create / npm init / git clone "
        "into scratch/loop-*/impl). Blocking here closes the bypass that lets "
        "agents skip bundle / paid-features / spec / pre-generate and ship an "
        "impl/ without canonical verification.\n\n"
        "Run the pipeline through pre-generate first:\n"
        "  python -m ui_clone.pipeline <URL> <component> <session> run --phases 0A,1,2\n"
        "  python -m ui_clone.pipeline <URL> <component> <session> status\n"
        "Then the same scaffold command runs unblocked.\n\n"
        "Emergency bypass (voids measurement signal): UI_RE_SKIP_BASH_GATE=1 <command>"
    )


def _static_mirror_download_violation(cmd: str) -> bool:
    return bool(cmd and _STATIC_MIRROR_DOWNLOAD_PATTERNS.search(cmd))


def _static_server_violation(cmd: str) -> bool:
    return bool(cmd and _STATIC_SERVER_PATTERNS.search(cmd))


def _is_impl_index_html_path(path: str) -> bool:
    stripped = path.strip("'\"")
    parts = Path(stripped).parts
    return "impl" in parts and Path(stripped).name.lower() == "index.html"


def _whole_document_html_snapshot_violation(cmd: str) -> bool:
    if not cmd or not _WHOLE_DOCUMENT_HTML_PATTERNS.search(cmd):
        return False
    # Site-detection probes may read outerHTML.length. Full document HTML
    # snapshots are different: they seed copied static mirrors.
    if re.search(r"document\.(?:documentElement|body)\.(?:outerHTML|innerHTML)\s*\.length", cmd, re.IGNORECASE):
        return False
    if "agent-browser" in cmd or _bash_html_write_targets(cmd):
        return True
    return False


def _bash_html_write_targets(cmd: str) -> list[str]:
    targets: list[str] = []
    if not cmd:
        return targets
    for pat in _HTML_WRITE_PATTERNS:
        for m in pat.finditer(cmd):
            target = m.group(1).strip("\"'")
            if target and not target.startswith("&") and target != "/dev/null":
                targets.append(target)
    return targets


def _static_html_mirror_write_target(cmd: str) -> str | None:
    """Return impl/index.html when it is being populated from copied page HTML.

    A minimal Vite/React index.html scaffold is legitimate. The blocked path is
    specifically whole-document/live snapshot HTML becoming the implementation.
    """
    if not cmd or not _STATIC_HTML_MIRROR_SOURCE_PATTERNS.search(cmd):
        return None
    for target in _bash_html_write_targets(cmd):
        if _is_impl_index_html_path(target):
            return target
    return None


def _find_ref_dir_with_pipeline_state(search_root: Path) -> Path | None:
    """Find a ref dir for state checks, including pre-extracted pipeline dirs."""
    if not search_root.is_dir():
        return None

    for d in sorted(search_root.iterdir()):
        if d.is_dir() and (d / ".ui-re-active").is_file():
            return d

    newest_time = 0.0
    newest_dir: Path | None = None
    for d in sorted(search_root.iterdir()):
        if not d.is_dir():
            continue
        mtimes = [
            (d / name).stat().st_mtime
            for name in ("pipeline-state.json", "extracted.json", "regions.json")
            if (d / name).is_file()
        ]
        if not mtimes:
            continue
        mtime = max(mtimes)
        if mtime > newest_time:
            newest_time = mtime
            newest_dir = d

    return newest_dir


def _command_path_tokens(cmd: str) -> list[str]:
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


def _candidate_ref_roots(project_root: Path, cwd: Path | None, cmd: str) -> list[Path]:
    """Return likely tmp/ref roots for commands that target scratch/loop impl dirs."""
    roots: list[Path] = []
    seen: set[Path] = set()

    def add(root: Path) -> None:
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        if resolved in seen or not resolved.is_dir():
            return
        seen.add(resolved)
        roots.append(resolved)

    if cwd is not None:
        cur = cwd
        while cur != cur.parent:
            add(cur / "tmp" / "ref")
            cur = cur.parent

    base = cwd or project_root
    for token in _command_path_tokens(cmd):
        token = token.strip("'\"")
        if "://" in token or ("/" not in token and not token.endswith(".js")):
            continue
        p = Path(token)
        if not p.is_absolute():
            p = base / p
        cur = p if p.is_dir() else p.parent
        while cur != cur.parent:
            add(cur / "tmp" / "ref")
            cur = cur.parent

    add(project_root / "tmp" / "ref")
    return roots


def _ref_dir_for_static_guard(project_root: Path, cwd: Path | None, cmd: str) -> Path | None:
    for root in _candidate_ref_roots(project_root, cwd, cmd):
        ref_dir = _find_ref_dir_with_pipeline_state(root)
        if ref_dir is not None:
            return ref_dir
    return None


def _state_before_gate(ref_dir: Path, gate: str) -> bool:
    state = PipelineState.load(ref_dir)
    if state.current_gate == "done":
        return False
    if state.current_gate not in GATE_ORDER or gate not in GATE_ORDER:
        return True
    return GATE_ORDER.index(state.current_gate) < GATE_ORDER.index(gate)


def _has_no_populated_component(ref_root: Path) -> bool:
    """True when no immediate child dir under ref_root has Phase-1 evidence.

    Resolves symlinks (so a stale benchmark symlink doesn't pollute the
    decision) and skips entries that fail to resolve at all.
    """
    if not ref_root.is_dir():
        return True
    for d in ref_root.iterdir():
        if not d.is_dir():
            continue
        try:
            real = d.resolve(strict=True)
        except (OSError, RuntimeError):
            # Broken symlink or unresolvable — treat as not populated.
            continue
        if (real / "regions.json").is_file() or (real / "pipeline-state.json").is_file():
            return False
    return True


def _is_fresh_state(project_root: Path, cwd: Path | None = None) -> bool:
    """True when the relevant ref dir has no usable component dir yet.

    A component dir is "usable" once its resolved target contains either
    `regions.json` (Phase 1 minimal evidence) or `pipeline-state.json`.

    Resolution order (v1.2 — cwd-aware to handle scratch subdirs):
      1. Walk up from `cwd` looking for the nearest `tmp/ref/`. If found,
         use that one and DO NOT also check the project root — a fresh
         scratch subdir inside a populated repo still counts as fresh.
      2. Fall back to `project_root / "tmp" / "ref"`.
      3. If neither exists, the project is fresh.

    The v1.1 version checked only project_root, which falsely reported
    "not fresh" when the repo root had stale benchmark symlinks (e.g.
    `tmp/ref/realfood-main -> benchmark/work/<sha>/ref/`) even though
    the agent was working from a clean scratch/ subdirectory.
    """
    if cwd is not None:
        cur = cwd
        while cur != cur.parent:
            candidate = cur / "tmp" / "ref"
            if candidate.is_dir():
                # Found the nearest tmp/ref to cwd — that's the scope of
                # this session's freshness. Don't widen to project root.
                return _has_no_populated_component(candidate)
            cur = cur.parent
    return _has_no_populated_component(project_root / "tmp" / "ref")


def _fresh_state_violation(cmd: str) -> bool:
    """True when the command is on the deny list AND not on the allow list.

    Both lists are checked because some commands match both (e.g. a script
    invocation in a `command -v` check). Allow wins — better to let an
    inspection through than to falsely block.
    """
    if not cmd:
        return False
    if _FRESH_FOLDER_ALLOW_PATTERNS.match(cmd):
        return False
    return bool(_FRESH_FOLDER_DENY_PATTERNS.search(cmd))


def _bash_write_target(cmd: str) -> str | None:
    """Return the first component-file target this Bash command writes to, else None.

    Skips writes to /dev/null, /tmp, /var/tmp, .stale paths and the like —
    they're never component files anyway, but the early-out reduces regex work.
    """
    if not cmd:
        return None
    if ">/dev/null" in cmd or ">/tmp/" in cmd:
        # Common no-op redirects; quick reject before regex sweep.
        pass  # don't return — there may still be a real component-file write later in the cmd
    for pat in _BASH_WRITE_PATTERNS:
        for m in pat.finditer(cmd):
            target = m.group(1).strip("\"'")
            if not target or target.startswith("&") or target == "/dev/null":
                continue
            if is_component_file(target):
                return target
    return None


def _bash_adhoc_ref_target(cmd: str) -> tuple[str, str] | None:
    """Return (target_path, suggested_canonical) for the first Bash redirect
    that writes to an ad-hoc *.json under any `tmp/ref/<c>/`, else None.

    Closes the v0.6 bypass observed during natural-prompt fresh runs: the
    pre_generate Write/Edit hook denies invented artifact names, but nested
    agents fall back to `bash -c '... > sections-map.json'`. This catches
    `cat > file.json`, `echo > file.json`, `tee file.json`, `agent-browser
    eval ... > file.json`, etc. — the same redirect set already parsed for
    component-file enforcement.
    """
    if not cmd:
        return None
    for pat in _BASH_WRITE_PATTERNS:
        for m in pat.finditer(cmd):
            target = m.group(1).strip("\"'")
            if not target or target.startswith("&") or target == "/dev/null":
                continue
            is_adhoc, suggested = is_ad_hoc_ref_artifact(target)
            if is_adhoc:
                return target, suggested
    return None


def _emit_block(reason: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(payload, ensure_ascii=False))


def _find_active_ref(search_root: Path) -> Path | None:
    if not search_root.is_dir():
        return None
    for d in sorted(search_root.iterdir()):
        if d.is_dir() and (d / ".ui-re-active").is_file():
            return d
    return None


def _section_compare_precondition_reason(ref_dir: Path, cmd: str) -> str | None:
    """Block section-compare while earlier block-severity static gates are missing.

    section-compare is expensive and easy to misread as the "real" verdict.
    If verification-plan declares dom-mirror-check or proxy-mirror-check, a
    missing/failing artifact means the implementation already diverged
    structurally or is a mirror; running pixel crops first lets agents chase
    section ids while ignoring the root failure.
    """
    if not _SECTION_COMPARE_COMMAND_PATTERNS.search(cmd):
        return None

    plan_path = ref_dir / "verification-plan.json"
    if not plan_path.is_file():
        return None
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    rows = plan.get("requiredChecks") if isinstance(plan, dict) else None
    if not isinstance(rows, list):
        return None
    preconditions = {
        "dom-mirror-check": (
            "dom-mirror-check.json",
            f"bash $SCRIPTS_DIR/dom-mirror-check.sh {ref_dir} <impl-dir>",
            "DOM mirror",
        ),
        "proxy-mirror-check": (
            "proxy-mirror-check.json",
            f"bash $SCRIPTS_DIR/proxy-mirror-check.sh {ref_dir} <impl-dir>",
            "proxy/static mirror",
        ),
    }

    for check_id, (default_artifact, command, label) in preconditions.items():
        row = next(
            (
                row for row in rows
                if isinstance(row, dict)
                and row.get("id") == check_id
                and row.get("severity") == "block"
            ),
            None,
        )
        if not isinstance(row, dict):
            continue

        artifact_name = str(row.get("produces") or default_artifact)
        artifact = ref_dir / artifact_name
        status = "missing"
        if artifact.is_file():
            try:
                payload = json.loads(artifact.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    status = str(payload.get("status") or "unknown")
            except (json.JSONDecodeError, OSError):
                status = "malformed"

        if status == "pass":
            continue

        return (
            f"⛔ UI-RE: section-compare blocked because {check_id} is {status}. "
            f"Run/fix the block-severity {label} gate first:\n"
            f"  {command}\n"
            f"Then re-run section-compare after {artifact_name} reports status=pass."
        )

    return None


def _check_benchmark_setup_alignment(project_root: Path) -> str | None:
    """Return a block reason if `tmp/ref/realfood` points at a benchmark/work/
    dir whose SHA doesn't match `git rev-parse --short HEAD`; else None.

    The check applies only when the symlink target matches the canonical
    `benchmark/work/<sha>/ref` layout — a different layout (e.g. user
    pointing at `tmp/ref/realfood -> tmp/ref/someothercomponent` for an
    unrelated clone) is left alone.

    Rationale: rounds A / B / V3 each launched a new SHA but agents skipped
    Step 1 setup, so the symlink stayed pinned to the previous round's work
    dir. Section-compare ran on stale capture data, AE metrics were
    artifacts, and the benchmark history was misleading. This check makes
    that failure mode loud — the agent is blocked at the first benchmark
    command and pointed at the recovery script.
    """
    sym = project_root / "tmp" / "ref" / "realfood"
    if not sym.is_symlink():
        return None
    try:
        target = os.readlink(sym)
    except OSError:
        return None

    m = re.search(r"benchmark/work/([0-9a-f]{7,40})/ref/?$", target)
    if not m:
        # Symlink doesn't point at a benchmark work dir — out of scope.
        return None
    link_sha = m.group(1)

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    head = (result.stdout or "").strip()
    if not head:
        return None

    if link_sha.startswith(head) or head.startswith(link_sha):
        return None  # match (one is shorter prefix of the other)

    return (
        f"⛔ UI-RE benchmark setup mismatch: tmp/ref/realfood points at "
        f"benchmark/work/{link_sha}/ref but HEAD is {head}. The agent is "
        f"about to iterate on a stale work dir (the inheritance bug observed "
        f"in rounds A / B / V3).\n"
        f"Run setup before any other benchmark command:\n"
        f"  bash skills/benchmark/scripts/setup.sh\n"
        f"That script wipes the current-SHA work dir, re-links "
        f"tmp/ref/realfood, and exits 2 on any further mismatch.\n"
        f"Bypass (emergency only): UI_RE_SKIP_BASH_GATE=1 <command>"
    )


def main() -> None:
    if os.environ.get("UI_RE_SKIP_BASH_GATE") == "1":
        sys.exit(0)

    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    if not raw.strip():
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)

    cmd = data.get("tool_input", {}).get("command", "") or data.get("command", "")
    if not isinstance(cmd, str):
        sys.exit(0)

    # PreToolUse payload includes `cwd` — the working directory the agent's
    # Bash tool will run in. Use it as the primary scope for fresh-state
    # checks so a scratch subdir inside a populated repo still counts as
    # fresh. Falls back to project_root when absent (rare; some host
    # variants omit the field).
    payload_cwd_raw = data.get("cwd", "")
    payload_cwd: Path | None = None
    if isinstance(payload_cwd_raw, str) and payload_cwd_raw:
        candidate = Path(payload_cwd_raw)
        if candidate.is_dir():
            payload_cwd = candidate.resolve()

    project_root = find_project_root()

    # Benchmark setup-alignment check. Fires BEFORE the declaration-of-done
    # logic so the agent is blocked on the first stale-symlink command, not
    # only when it tries to commit/push. The escape lets setup.sh / `ln -s`
    # through so the recovery action isn't itself blocked.
    if _BENCHMARK_COMMAND_PATTERNS.search(cmd) and not _BENCHMARK_SETUP_ESCAPES.search(cmd):
        bench_reason = _check_benchmark_setup_alignment(project_root)
        if bench_reason is not None:
            _emit_block(bench_reason)
            sys.exit(0)

    # Static whole-document mirror guard. Per-section `outerHTML` probes are
    # valid extraction evidence, but dumping `document.documentElement.outerHTML`
    # or `document.body.innerHTML` creates a copied static page that has no
    # React/Tailwind component surface and usually drops the original motion
    # runtime. Block this before the file is written; otherwise agents can
    # self-verify HTTP 200 / title while transitions are dead.
    if not os.environ.get("UI_RE_SKIP_BASH_GATE"):
        if _whole_document_html_snapshot_violation(cmd):
            reason = (
                "⛔ UI-RE whole-document static mirror blocked: do not dump "
                "`document.documentElement.outerHTML` / `document.body.innerHTML` "
                "into tmp/ref or impl files. Section-level outerHTML probes are "
                "allowed for extraction, but the implementation must be generated "
                "from canonical artifacts and verified with motion/runtime gates."
            )
            _emit_block(reason)
            sys.exit(0)

        html_target = _static_html_mirror_write_target(cmd)
        if html_target is not None:
            reason = (
                f"⛔ UI-RE static mirror blocked: writing copied live HTML into "
                f"impl/index.html ({html_target}) is not a React/Tailwind clone "
                "and strips the original transition runtime. Use canonical "
                "extraction artifacts to generate source components, then run "
                "`python -m ui_clone.pipeline <url> <component> <session> verify`."
            )
            _emit_block(reason)
            sys.exit(0)

    # Ad-hoc ref-artifact redirect denial — closes the Bash bypass route of the
    # pre_generate Write/Edit hook. Observed during natural-prompt fresh runs:
    # nested agents dump JSON into `tmp/ref/<c>/sections-map.json` via Bash
    # redirect instead of running the canonical script. Catch the redirect
    # before the command runs and point at the canonical name + the script
    # that produces it.
    #
    # UI_RE_SKIP_BASH_GATE — emergency escape hatch. Setting this env var
    # DEFEATS the core ad-hoc-artifact enforcement on natural-path runs,
    # meaning a fresh-prompt session can revert to dumping invented JSON
    # names with no measurement signal. Reserved for cases where the agent
    # legitimately needs to write a one-off scratch file under tmp/ref/ and
    # the operator has accepted that the run will not produce a verifiable
    # ae_avg. Do NOT export this globally.
    if not os.environ.get("UI_RE_SKIP_BASH_GATE"):
        adhoc = _bash_adhoc_ref_target(cmd)
        if adhoc is not None:
            target, suggested = adhoc
            basename = Path(target).name
            if suggested:
                reason = (
                    f"⛔ UI-RE: Bash redirect to ad-hoc ref artifact "
                    f"'{basename}' blocked. Use canonical '{suggested}' "
                    f"produced by the matching pipeline script "
                    f"(e.g. `bash $PLUGIN_ROOT/skills/visual-debug/scripts/"
                    f"dom-scaffold.sh <ref-dir>` for section-map.json). "
                    f"Do NOT dump JSON into tmp/ref/<c>/ via cat/echo/tee/"
                    f"agent-browser eval redirects. See SKILL.md Pipeline section."
                )
            else:
                reason = (
                    f"⛔ UI-RE: Bash redirect to ad-hoc ref artifact "
                    f"'{basename}' blocked. Run a canonical extraction "
                    f"script (skills/visual-debug/scripts/*.sh) instead of "
                    f"hand-dumping JSON into tmp/ref/<c>/. See SKILL.md "
                    f"Pipeline section for the step → artifact mapping."
                )
            _emit_block(reason)
            sys.exit(0)

    # Fresh-folder first-action enforcement (v1.1). When the project has no
    # populated ref dir yet, deny any Bash that touches the canonical
    # extraction surface except via the pipeline driver. SKILL.md tells the
    # agent to call `pipeline run` first; this hook makes that mandatory
    # instead of just guidance — the v1.0 nested test confirmed instruction
    # alone is ignored. Same UI_RE_SKIP_BASH_GATE escape as the ad-hoc deny.
    if not os.environ.get("UI_RE_SKIP_BASH_GATE") and _is_fresh_state(
        project_root, cwd=payload_cwd
    ):
        if _fresh_state_violation(cmd):
            example_component = "site"
            example_session = "ref-capture"
            reason = (
                f"⛔ UI-RE fresh-folder enforcement: tmp/ref/ has no Phase 1 "
                f"evidence yet, so direct extraction commands are blocked.\n"
                f"Run the pipeline driver FIRST:\n"
                f"  python -m ui_clone.pipeline <URL> {example_component} "
                f"{example_session} run --phases 0A,1,2\n"
                f"It invokes capture.sh + extract-dom.sh + dom-scaffold.sh "
                f"in the right order and produces canonical artifacts.\n"
                f"Inspection commands (which / command -v / ls / cat / "
                f"`python -m ui_clone.pipeline ... status`) still pass.\n"
                f"Bypass (emergency only, voids measurement signal): "
                f"UI_RE_SKIP_BASH_GATE=1 <command>"
            )
            _emit_block(reason)
            sys.exit(0)

    if not os.environ.get("UI_RE_SKIP_BASH_GATE"):
        scaffold_reason = _impl_scaffold_violation(
            cmd, project_root, cwd=payload_cwd
        )
        if scaffold_reason is not None:
            _emit_block(scaffold_reason)
            sys.exit(0)

    # Static mirror guard. A partial pipeline-state file (e.g. after reference
    # capture) is not enough to start copying live HTML/CSS/JS into impl/public
    # or to serve a custom static server. Natural-prompt loop validation showed
    # agents can create pipeline-state.json, then immediately fall back to
    # `wget -P impl/public ...` + `node server.js` and self-verify HTTP 200s.
    if not os.environ.get("UI_RE_SKIP_BASH_GATE"):
        if _static_mirror_download_violation(cmd):
            reason = (
                "⛔ UI-RE static mirror blocked: copying live HTML/CSS/JS "
                "into impl/public is not a React/Tailwind clone and does not "
                "produce gateable implementation evidence. Continue the "
                "ui_clone pipeline, finish extraction/spec/pre-generate, then "
                "implement source code instead of mirroring the live site."
            )
            _emit_block(reason)
            sys.exit(0)

        if _static_server_violation(cmd):
            ref_dir = _ref_dir_for_static_guard(project_root, payload_cwd, cmd)
            if ref_dir is None or _state_before_gate(ref_dir, "post-implement"):
                gate = "missing" if ref_dir is None else PipelineState.load(ref_dir).current_gate
                reason = (
                    "⛔ UI-RE static server blocked before post-implement: "
                    f"current gate is {gate}. A local server is verification "
                    "surface, not an implementation shortcut. Run the pipeline "
                    "through pre-generate and write the React/Tailwind source "
                    "before starting a dev/static server."
                )
                _emit_block(reason)
                sys.exit(0)

    if _SECTION_COMPARE_COMMAND_PATTERNS.search(cmd):
        ref_dir = _find_active_ref(project_root / "tmp" / "ref")
        if ref_dir is not None:
            section_reason = _section_compare_precondition_reason(ref_dir, cmd)
            if section_reason is not None:
                _emit_block(section_reason)
                sys.exit(0)

    is_decl = _is_declaration_command(cmd)
    bash_write = _bash_write_target(cmd)
    if not is_decl and bash_write is None:
        sys.exit(0)

    # Bash-write to a component file: same gate as pre_generate (extraction must
    # be complete before component code is written). This closes the bypass where
    # an agent could use `cat > Foo.tsx`, `tee Foo.tsx`, or `sed -i ... Foo.tsx`
    # to skip the PreToolUse Edit/Write hook.
    if bash_write is not None:
        # Resolve ref_dir from the file's containing project, mirroring pre_generate.
        ref_dir = None
        try:
            fp = Path(bash_write).resolve()
            cur = fp.parent
            while cur != cur.parent:
                if (cur / "tmp" / "ref").is_dir():
                    ref_dir = find_ref_dir(cur / "tmp" / "ref")
                    break
                cur = cur.parent
        except OSError:
            pass
        if ref_dir is None:
            ref_dir = find_ref_dir(project_root / "tmp" / "ref")
        if ref_dir is None:
            sys.exit(0)
        gate_result = run_gate(ref_dir, "pre-generate")
        if not gate_result.get("passed", True):
            failures: list[dict[str, str]] = cast(list[dict[str, str]], gate_result.get("failures", []))
            fail_count = cast(int, gate_result.get("fail_count", len(failures)))
            missing = ", ".join(f.get("label", "?") for f in failures[:6])
            reason = (
                f"⛔ UI-RE: Bash write to component file '{bash_write}' blocked — "
                f"extraction incomplete ({fail_count} artifacts missing: {missing}).\n"
                f"This bypass route (cat>/tee/sed -i) goes through the same gate as Edit/Write.\n"
                f"Complete Phase 2 extraction before writing components.\n"
                f"Bypass (emergency only): UI_RE_SKIP_BASH_GATE=1 <command>"
            )
            _emit_block(reason)
            sys.exit(0)
        # Gate passed for write — fall through. If the cmd is ALSO a declaration,
        # the section-compare check below still runs. Otherwise we're done.
        if not is_decl:
            sys.exit(0)

    ref_dir = _find_active_ref(project_root / "tmp" / "ref")
    if ref_dir is None:
        sys.exit(0)

    state = PipelineState.load(ref_dir)

    # Always require section-compare result.txt to exist with 0 FAIL / 0 MISSING.
    # State alone isn't enough — we want freshness against actual artifacts.
    result_file = ref_dir / "sections" / "result.txt"
    if result_file.is_file():
        try:
            text = result_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        fail_count = text.count("❌")
        # Match section_gate.py / gate.py: explicit "⚠️ MISSING impl" marker
        missing_count = text.count("⚠️ MISSING impl")
        if fail_count == 0 and missing_count == 0 and state.current_gate == "done":
            sys.exit(0)

        # Have a result.txt but with failures
        if fail_count > 0 or missing_count > 0:
            reason = (
                f"⛔ UI-RE: cannot run '{cmd.split(chr(10))[0][:60]}' — "
                f"section-compare shows {fail_count} FAIL, {missing_count} MISSING.\n"
                f"Fix diffs in {ref_dir}/sections/diff/ and re-run:\n"
                f"  bash $SCRIPTS_DIR/section-compare.sh <orig> <impl> <session> {ref_dir}\n"
                f"Then: python -m ui_clone.gate {ref_dir} section-compare"
            )
            _emit_block(reason)
            sys.exit(0)

    # No result.txt at all OR result.txt clean but state isn't done — run the gate
    # and report what's actually missing. This avoids hardcoded message drift.
    gate_name = "section-compare" if state.current_gate in ("section-compare", "done") else state.current_gate
    gate_result = run_gate(ref_dir, gate_name)

    if gate_result.get("passed", True):
        # Gate passes (rare with no result.txt — could be 'reference' fail-open)
        # but state didn't say done. Re-load — Gate.run() may have advanced it.
        state = PipelineState.load(ref_dir)
        if state.current_gate == "done":
            sys.exit(0)
        # Gate passed but pipeline not at done — list what's left.
        # state.current_gate != "done" here (the == "done" branch returned above).
        remaining = state.current_gate
        reason = (
            f"⛔ UI-RE: cannot run '{cmd.split(chr(10))[0][:60]}' — "
            f"pipeline incomplete. Current gate: {remaining}.\n"
            f"Run: python -m ui_clone.gate {ref_dir} {remaining}"
        )
        _emit_block(reason)
        sys.exit(0)

    # Gate failed — list failures
    failures = cast(list[dict[str, str]], gate_result.get("failures", []))
    fail_count = cast(int, gate_result.get("fail_count", len(failures)))
    parts = [
        f"⛔ UI-RE: cannot run '{cmd.split(chr(10))[0][:60]}' — "
        f"{gate_name} gate FAILED ({fail_count} issue(s))."
    ]
    for f in failures[:5]:
        parts.append(f"  • {f.get('label', '?')}: {f.get('reason', '')}")
        if f.get("fix"):
            parts.append(f"    → {f['fix']}")
    parts.append(
        f"\nFix and re-run: python -m ui_clone.gate {ref_dir} {gate_name}\n"
        f"Bypass (emergency only): UI_RE_SKIP_BASH_GATE=1 <command>"
    )
    _emit_block("\n".join(parts))
    sys.exit(0)


if __name__ == "__main__":
    main()
