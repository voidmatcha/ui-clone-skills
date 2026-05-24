"""Impl-scaffold bootstrap command detection.

Symmetric pair of pre_generate's Write/Edit gate. Blocks impl/ bootstrap
commands (npm create vite, npx create-*, etc.) when the pipeline hasn't
reached pre-generate yet.

Match shapes — keep the regex tight so unrelated `npm` invocations
(install, run build, lint) still pass:
  npm | pnpm | yarn | bun  +  create  +  <tool>
  npx                       +  create-<tool>
  npx                       +  degit
  git                       +  clone               (template repo)
"""

from __future__ import annotations

import re
from pathlib import Path

from ui_clone.state import GATE_ORDER, PipelineState

from .ref_state import _candidate_ref_roots
from .repo_identity import _canonical_repo_root, _is_scratch_nested

_IMPL_SCAFFOLD_PATTERNS = re.compile(
    r"\b(?:npm|pnpm|yarn|bun)\s+create\b"
    r"|\bnpx\s+create-\S+"
    r"|\bnpx\s+degit\b"
    r"|\bnpm\s+init\b(?!\s+-y\s+--scope)"  # npm init <tpl>; skip `-y --scope` pure metadata initializers
    r"|\b(?:npm|pnpm|yarn|bun)\s+exec\s+create-\S+"
    r"|\bgit\s+clone\b[^\n\r]*\s[^\s|;&]*/impl(?:\b|\s|$)",
    re.IGNORECASE,
)


def _missing_transition_spec_reason(ref_dir: Path) -> str | None:
    spec = ref_dir / "transition-spec.json"
    if not spec.is_file():
        return f"{spec} — MISSING"
    try:
        if spec.stat().st_size < 10:
            return f"{spec} — exists but empty"
    except OSError as e:
        return f"{spec} — unreadable ({e})"
    return None


def _impl_scaffold_violation(
    cmd: str, project_root: Path, cwd: Path | None = None
) -> str | None:
    """Block impl/ bootstrap commands (npm create vite, npx create-*, etc.)
    when the pipeline hasn't reached pre-generate yet.

    Returns a block reason string when the command would scaffold an impl/
    without the matching ref dir's pipeline-state showing
    `current_gate >= pre-generate`, else None.

    Universal resolution: walks `_candidate_ref_roots` (the same set the
    static-server guard uses) and picks the freshest ref dir. If no ref
    dir exists at all, the agent is bootstrapping impl before Phase 1 ran —
    that's the strictest form of the bypass and blocks unconditionally.

    Reject candidate ref roots nested under `<canonical_repo_root>/scratch/`
    — that subtree holds ephemeral impl + scratch artifacts, never
    canonical pipeline state. An agent that creates a nested tmp/ref/
    there (by copying a prior populated ref dir or hand-creating one) is
    spoofing the gate.
    """
    if not cmd:
        return None
    if not _IMPL_SCAFFOLD_PATTERNS.search(cmd):
        return None

    # Pick the freshest ref dir from any candidate root the command might
    # be targeting. This mirrors _ref_dir_for_static_guard but is broader:
    # the scaffold command may not name the loop dir explicitly (e.g.
    # `npm create vite my-clone` in a cwd that's not the canonical impl/), so we accept
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
            missing_spec = _missing_transition_spec_reason(freshest)
            if missing_spec:
                return (
                    "⛔ UI-RE impl-scaffold gate: BLOCKED before verified "
                    "pre-generate artifacts.\n\n"
                    f"Detected bootstrap command: `{cmd[:120]}{'…' if len(cmd) > 120 else ''}`\n"
                    f"Current gate state: {gate}\n"
                    f"Missing required artifact: {missing_spec}\n\n"
                    "pipeline-state.json alone is not enough to permit impl/ "
                    "scaffolding. transition-spec.json is the Step 5d handoff "
                    "that gate_spec and pre-generate must enforce before any "
                    "component code is written.\n\n"
                    "Run the canonical gates instead of leaf scripts:\n"
                    f"  python -m ui_clone.gate {freshest} spec\n"
                    f"  python -m ui_clone.gate {freshest} pre-generate\n\n"
                    "Emergency bypass (voids measurement signal): "
                    "UI_RE_SKIP_BASH_GATE=1 <command>"
                )
            return None  # pre-generate or later reached — allowed
        if gate == "done":
            missing_spec = _missing_transition_spec_reason(freshest)
            if missing_spec:
                return (
                    "⛔ UI-RE impl-scaffold gate: BLOCKED before verified "
                    "pre-generate artifacts.\n\n"
                    f"Detected bootstrap command: `{cmd[:120]}{'…' if len(cmd) > 120 else ''}`\n"
                    "Current gate state: done\n"
                    f"Missing required artifact: {missing_spec}\n\n"
                    "A completed pipeline state without transition-spec.json "
                    "is inconsistent; rerun the canonical spec and "
                    "pre-generate gates before bootstrapping impl/.\n\n"
                    "Emergency bypass (voids measurement signal): "
                    "UI_RE_SKIP_BASH_GATE=1 <command>"
                )
            return None  # already converged — re-scaffolding is the agent's call
        gate_label = gate or "missing"

    return (
        "⛔ UI-RE impl-scaffold gate: BLOCKED before pre-generate.\n\n"
        f"Detected bootstrap command: `{cmd[:120]}{'…' if len(cmd) > 120 else ''}`\n"
        f"Current gate state: {gate_label}\n\n"
        "pre_generate's Write/Edit hook does not see shell-spawned scaffolders "
        "(npm create vite / npx create-* / pnpm create / npm init / git clone "
        "into a sibling <subdir>/impl). Blocking here closes the bypass that lets "
        "agents skip bundle / paid-features / spec / pre-generate and ship an "
        "impl/ without canonical verification.\n\n"
        "Run the pipeline through pre-generate first:\n"
        "  python -m ui_clone.pipeline <URL> <component> <session> run --phases 0A,1,2\n"
        "  python -m ui_clone.pipeline <URL> <component> <session> status\n"
        "Then the same scaffold command runs unblocked.\n\n"
        "Emergency bypass (voids measurement signal): UI_RE_SKIP_BASH_GATE=1 <command>"
    )
