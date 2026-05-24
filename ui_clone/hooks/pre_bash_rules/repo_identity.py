"""Canonical repo root resolution + scratch-nested detection.

A nested ref dir at `<repo>/scratch/<dir>/tmp/ref/<c>/` can shadow the
canonical `<repo>/tmp/ref/<c>/` from inside `find_project_root()`'s
closest-ancestor walk. The impl-scaffold gate needs an authority-of-record
path so it can reject scratch-nested candidates as spoof attempts.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _canonical_repo_root() -> Path | None:
    """Repo root resolved without the closest-ancestor walk that
    `find_project_root()` uses. Priority: `CLAUDE_PROJECT_DIR` env var,
    then `git rev-parse --show-toplevel`. Returns None when neither
    resolves to a directory.
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

    Scratch-nested spoof signature: an agent creates
    `<repo>/scratch/<dir>/tmp/ref/<component>/` (often copied from a
    prior completed ref dir) so the closest-ancestor walk finds that
    nested `tmp/ref/` before the canonical one. The `pipeline-state.json`
    in the spoof reports `current_gate: done` so the impl-scaffold gate
    lets the scaffold through.

    Distinguishing legitimate vs spoofed: the canonical ref dir always
    lives at `<repo-root>/tmp/ref/<component>/`, NEVER at
    `<repo-root>/scratch/<dir>/tmp/ref/`. The `scratch/` subtree is for
    transient impl / sub-workspace state only; pipeline state lives at
    the repo root. So any candidate root descending from
    `<repo-root>/scratch/` is by construction a spoof and must be
    rejected.

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
