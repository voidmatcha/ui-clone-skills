"""Ref-dir resolution + pipeline-state freshness detection.

Fresh-folder enforcement: when the project's `tmp/ref/` has no Phase-1
evidence yet, deny any Bash that touches the canonical extraction
surface (agent-browser CLI, visual-debug/scripts/*.sh wrappers,
scripts/extract/*.sh wrappers) except the pipeline driver itself and
read-only inspection. The deny reason names the exact command the agent
should run instead.

Allowlisted in fresh state:
  - python -m ui_clone.pipeline ...    (status / run actions)
  - which / command -v / type / ls / cat / head / tail / grep / find / pwd
  - mkdir -p (so the agent can prepare scratch dirs without hitting deny)
  - git status / git diff / git log
  - the preflight Bash literally documented in SKILL.md's First action
    section (uses `command -v` + `for c in agent-browser ...; do` shape)
  - the pipeline run's own internal invocations (we identify them by
    `PLUGIN_ROOT/scripts/extract/capture.sh` and the visual-debug script
    paths — they only appear when execute_phases() called them)

Denylisted in fresh state also includes static mirror escape routes
observed in natural-prompt validation: wget/curl copies of the live site
into impl/public and local static servers started before any pipeline
evidence exists. Those produce a browsable page but no React/Tailwind
implementation and no gateable tmp/ref artifacts.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from ui_clone.hooks._common import CMD_POSITION_PREFIX, sanitize_command_for_deny
from ui_clone.state import GATE_ORDER, PipelineState

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

# Tool invocations are denied only at COMMAND POSITION (CMD_POSITION_PREFIX) —
# a tool NAME inside a quoted pgrep pattern, a `command -v` check, or a grep
# argument is DATA, not an invocation. (Live-fire false positive 2026-06-12:
# `pgrep -fl "ci-local|section-compare|video-motion|agent-browser"` was blocked
# because the bare `\bagent-browser\b` matched inside the quoted argument.)
_FRESH_FOLDER_DENY_TOOLS = re.compile(
    CMD_POSITION_PREFIX + r"(?:"
    r"agent-browser\b"
    r"|wget\b"
    r"|curl\b[^\n\r]*https?://"
    r"|node\s+(?:\S+/)?server\.js\b"
    r"|python(?:3)?\s+-m\s+http\.server\b"
    r"|npx\s+(?:serve|vite|http-server)\b"
    r"|npm\s+run\s+dev\b"
    r")"
)
# Canonical extraction-script paths. These carry the full plugin path, so a bare
# script NAME in a diagnostic (pgrep/grep) cannot match; quoted occurrences are
# stripped by sanitize_command_for_deny before this runs.
_FRESH_FOLDER_DENY_PATHS = re.compile(
    r"/skills/visual-debug/scripts/(?:extract-dom|dom-scaffold|section-compare|"
    r"asset-transfer-check|asset-utilization-check|paid-features-detect|"
    r"bundle-impl-coverage-check|hover-state-compare|click-state-compare|"
    r"video-transition-compare|hydration-check|reveal-trigger-check|"
    r"transition-compare|font-parity-check|image-fidelity-check|"
    r"scroll-end-completion-check|lottie-runtime-check)\.sh\b"
    r"|/scripts/extract/(?:extract-assets|extract-section-html|"
    r"extract-animation-runtime|extract-dynamic-styles|section-clips|"
    r"download-chunks|gsap-to-css)\.sh\b"
)


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
    """Return likely tmp/ref roots for commands that target nested impl dirs."""
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

    Resolution order (v1.2 — cwd-aware to handle nested sub-workspaces):
      1. Walk up from `cwd` looking for the nearest `tmp/ref/`. If found,
         use that one and DO NOT also check the project root — a fresh
         sub-workspace inside a populated repo still counts as fresh.
      2. Fall back to `project_root / "tmp" / "ref"`.
      3. If neither exists, the project is fresh.

    The v1.1 version checked only project_root, which falsely reported
    "not fresh" when the repo root had stale benchmark symlinks (e.g.
    `tmp/ref/<component>-main -> benchmark/work/<sha>/ref/`) even though
    the agent was working from a clean sub-workspace.
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
    # Strip quoted strings + heredoc bodies so a tool/script name that appears
    # as DATA (quoted pgrep pattern, heredoc doc body, grep/command-v arg)
    # cannot deny. The deny matchers are now COMMAND-POSITION anchored, so a
    # tool name in argument position no longer triggers — which makes the old
    # start-anchored allow list unnecessary (and unsafe: `ls && agent-browser
    # open URL` started with an allowed `ls` yet hid a real extraction). Deny
    # wins on any real invocation, wherever it sits in the command.
    sanitized = sanitize_command_for_deny(cmd)
    if _FRESH_FOLDER_DENY_TOOLS.search(sanitized) or _FRESH_FOLDER_DENY_PATHS.search(
        sanitized
    ):
        return True
    return False


def _find_active_ref(search_root: Path) -> Path | None:
    if not search_root.is_dir():
        return None
    for d in sorted(search_root.iterdir()):
        if d.is_dir() and (d / ".ui-re-active").is_file():
            return d
    return None
